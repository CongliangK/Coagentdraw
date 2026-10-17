"""CAD-13 offline tests. Doubles DO NOT prove Krita pixel/lifecycle semantics.

The PNG encoder/decoder, filesystem failures and HTTP/CLI plumbing run locally;
Krita Document/Selection/Notifier are explicit Python doubles, not live evidence.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
import cwb


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


snapshot = load_module("_cad13_snapshot", ROOT / "core/krita_cowork/snapshot.py")


def decode_png(png):
    """Independent standard-library reader: validate CRC, type, filter and bytes."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    offset, packed, kinds = 8, b"", []
    while offset < len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        kind = png[offset + 4:offset + 8]
        payload = png[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", png[offset + 8 + length:offset + 12 + length])[0]
        assert crc == zlib.crc32(kind + payload) & 0xffffffff
        kinds.append(kind)
        if kind == b"IHDR":
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            assert (depth, compression, filtering, interlace) == (8, 0, 0, 0)
            channels = {0: 1, 6: 4}[color]
        elif kind == b"IDAT":
            packed += payload
        offset += length + 12
    assert kinds[-1] == b"IEND"
    raw, stride = zlib.decompress(packed), width * channels
    assert len(raw) == (stride + 1) * height
    assert all(raw[y * (stride + 1)] == 0 for y in range(height))
    pixels = b"".join(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)] for y in range(height))
    return width, height, channels, pixels, kinds


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, fn):
        self.callbacks.append(fn)

    def emit(self, *args):
        for fn in self.callbacks:
            fn(*args)


class Notifier:
    def __init__(self):
        self.imageCreated, self.imageClosed = Signal(), Signal()
        self.enabled = False

    def setActive(self, active):
        self.enabled = active

    def active(self):
        return self.enabled


class Selection:
    def __init__(self, data, native=(0, 0, 3, 2)):
        self.data, self.native = data, native
        self.reads = []

    def pixelData(self, *rect):
        self.reads.append(rect)
        return self.data

    def x(self): return self.native[0]
    def y(self): return self.native[1]
    def width(self): return self.native[2]
    def height(self): return self.native[3]


class Doc:
    def __init__(self, width=3, height=2):
        self.w, self.h = width, height
        self.uid = str(uuid.uuid4())
        self.model, self.depth, self.profile = "RGBA", "U8", snapshot.SUPPORTED_PROFILE
        # RGBA order (live-verified 2026-10, Krita 6.0.4): opaque blue,
        # half-alpha green, transparent red, repeating.
        self.pixels = bytes([0, 0, 255, 255, 0, 255, 0, 128, 255, 0, 0, 0]) * (width * height // 3)
        self.mask = Selection(bytes([0, 128, 255, 1, 254, 0]))
        self.events = []
        self.on_pixels = None

    def rootNode(self):
        # New wrapper EACH time; there is deliberately no bounds()/pixelData().
        return types.SimpleNamespace(colorModel=lambda: self.model,
                                     colorDepth=lambda: self.depth,
                                     uniqueId=lambda: types.SimpleNamespace(toString=lambda: "{" + self.uid + "}"))

    def width(self): return self.w
    def height(self): return self.h
    def colorProfile(self): return self.profile

    def waitForDone(self): self.events.append("wait")
    def refreshProjection(self): self.events.append("refresh")

    def pixelData(self, *rect):
        self.events.append(("projection", rect))
        if self.on_pixels:
            self.on_pixels()
        return self.pixels

    def selection(self):
        self.events.append("selection")
        return self.mask


class App:
    def __init__(self, doc=None):
        self.doc = doc or Doc()
        self.open_docs = [self.doc]
        self.signals = Notifier()

    def activeDocument(self): return self.doc
    def documents(self): return list(self.open_docs)
    def notifier(self): return self.signals


class Codec(unittest.TestCase):
    def test_pixeldata_bytes_pass_through_unchanged(self):
        # Live-verified 2026-10: pixelData is native RGBA order; no B/R swap.
        rgba = bytes([3, 2, 1, 255, 30, 20, 10, 128, 9, 8, 7, 0])
        decoded = decode_png(snapshot.encode_png(rgba, 3, 1, 4))
        self.assertEqual(decoded[:4], (3, 1, 4, rgba))
        # Untagged RGBA: agent vision decoders misrender the sRGB chunk.
        self.assertNotIn(b"sRGB", decoded[4])
        self.assertNotIn(b"iCCP", decoded[4])
        self.assertNotIn(b"gAMA", decoded[4])

    def test_magenta_flatten_renders_transparency_explicitly(self):
        # a=0 → pure magenta regardless of stale RGB; a=255 → unchanged; all output opaque.
        rgba = bytes([9, 9, 9, 0, 10, 20, 30, 255, 20, 40, 60, 128])
        flat, near = snapshot.flatten_over_magenta(rgba, 3, 1)
        mr, mg, mb = snapshot.MAGENTA
        self.assertEqual(flat[:4], bytes((mr, mg, mb, 255)))
        self.assertEqual(flat[4:8], bytes((10, 20, 30, 255)))
        blended = bytes((v * 128 + c * (255 - 128) + 127) // 255
                        for v, c in ((20, mr), (40, mg), (60, mb)))
        self.assertEqual(flat[8:12], blended + b"\xff")
        self.assertEqual(near, 0)

    def test_magenta_flatten_counts_content_confusable_with_field(self):
        rgba = bytes([255, 0, 255, 255, 191, 0, 255, 255,
                      250, 70, 250, 255, 0, 0, 0, 255])
        flat, near = snapshot.flatten_over_magenta(rgba, 4, 1)
        self.assertEqual(near, 1)  # only the exact-magenta content; edge/bright/grey stay uncounted
        self.assertTrue(all(flat[i + 3] == 255 for i in range(0, len(flat), 4)))

    def test_alpha_summary_coverage_and_bbox(self):
        rgba = bytearray(12 * 4)
        for x in range(2, 5):
            rgba[x * 4 + 3] = 255
        summary = snapshot.alpha_summary(bytes(rgba), 12, 1)
        self.assertEqual(summary, {"coverage_pixels": 3,
                                   "bbox": {"x": 2, "y": 0, "width": 3, "height": 1}})
        empty = snapshot.alpha_summary(bytes(4), 1, 1)
        self.assertEqual(empty, {"coverage_pixels": 0, "bbox": None})

    def test_magenta_flatten_rejects_bad_buffer(self):
        for data in (4, True, "abcd", None, object(), b"short"):
            with self.subTest(data=data), self.assertRaises(snapshot.SnapshotError):
                snapshot.flatten_over_magenta(data, 1, 1)

    def test_gray_is_coverage_not_alpha_and_preserves_all_256_values(self):
        gray = bytes(range(256))
        decoded = decode_png(snapshot.encode_png(gray, 256, 1, 1))
        self.assertEqual(decoded[:4], (256, 1, 1, gray))
        self.assertNotIn(b"tRNS", decoded[4])
        self.assertNotIn(b"sRGB", decoded[4])

    def test_dimensions_formats_and_lengths_rejected(self):
        for width, height, data, channels in [(0, 1, b"", 4), (True, 1, b"abcd", 4),
                                             (1, 1, b"abc", 4), (1, 1, b"abcde", 4),
                                             (1, 1, b"", 1), (1, 1, b"a", 3),
                                             (32769, 1, b"", 1)]:
            with self.subTest(width=width, channels=channels), self.assertRaises(snapshot.SnapshotError):
                snapshot.encode_png(data, width, height, channels)

    def test_empty_edge_and_feather_bounds(self):
        self.assertIsNone(snapshot.mask_bounds(bytes(6), 3, 2))
        self.assertEqual(snapshot.mask_bounds(bytes([0, 0, 1, 0, 0, 0]), 3, 2),
                         {"x": 2, "y": 0, "width": 1, "height": 1})
        self.assertEqual(snapshot.mask_bounds(bytes([128, 0, 0, 0, 0, 255]), 3, 2),
                         {"x": 0, "y": 0, "width": 3, "height": 2})


class Exports(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cad13-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = App()
        self.service = snapshot.SnapshotService(self.app)
        self.p, self.m = self.root / "projection.png", self.root / "mask.png"

    def pair(self, **kwargs):
        return self.service.export(projection_path=str(self.p), selection_path=str(self.m), **kwargs)

    def test_pair_full_canvas_document_pixels_and_shared_metadata(self):
        result = self.pair()
        self.assertTrue(result["paired"])
        self.assertFalse(result["token_lifecycle_runtime_verified"])
        self.assertEqual(result["token_contract"], "session/notifier-epoch/root-uuid-v1")
        self.assertEqual(result["capture_scope"], "one-main-thread-callback")
        self.assertEqual((result["width"], result["height"], result["origin"]), (3, 2, {"x": 0, "y": 0}))
        self.assertEqual(self.app.doc.events, ["wait", "refresh", "wait", ("projection", (0, 0, 3, 2)), "selection"])
        self.assertEqual(self.app.doc.mask.reads, [(0, 0, 3, 2)])
        self.assertLessEqual(result["sample_started_monotonic_ns"], result["sample_finished_monotonic_ns"])
        self.assertTrue(result["sampled_at"].endswith("+00:00"))
        for kind, path, channels in (("projection", self.p, 4), ("selection", self.m, 1)):
            info = result[kind]
            png = path.read_bytes()
            width, height, count, raw, _ = decode_png(png)
            self.assertEqual((width, height, count), (3, 2, channels))
            self.assertEqual(info["file_sha256"], hashlib.sha256(png).hexdigest())
            self.assertEqual(info["pixel_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(info["file_bytes"], len(png))
            self.assertEqual(info["pixel_bytes"], 3 * 2 * channels)
            self.assertEqual(info["path"], str(path))
        self.assertEqual(decode_png(self.m.read_bytes())[3], bytes([0, 128, 255, 1, 254, 0]))
        self.assertEqual(list(self.root.glob(".cwb-snapshot-*")), [])

    def test_separate_exports_do_not_claim_same_capture(self):
        a = self.service.export(projection_path=str(self.p))
        b = self.service.export(selection_path=str(self.m))
        self.assertEqual(a["document_token"], b["document_token"])
        self.assertNotEqual(a["capture_id"], b["capture_id"])
        self.assertFalse(a["paired"] or b["paired"])
        self.assertEqual(a["capture_scope"], "single-export-only")

    def test_1328_rectangle_feather_edges_and_half_alpha(self):
        doc = self.app.doc
        doc.w = doc.h = 1328
        doc.pixels = bytes([19, 41, 227, 128]) * (1328 * 1328)
        mask = bytearray(1328 * 1328)
        for y in range(1326, 1328):
            mask[y * 1328 + 1325:(y + 1) * 1328] = bytes([1, 128, 255])
        doc.mask = Selection(mask, native=(1325, 1326, 3, 2))
        result = self.pair()
        self.assertEqual(result["selection"]["bounds"], {"x": 1325, "y": 1326, "width": 3, "height": 2})
        self.assertEqual(result["selection"]["native_bounds"], result["selection"]["bounds"])
        self.assertEqual(decode_png(self.p.read_bytes())[:4], (1328, 1328, 4, bytes([19, 41, 227, 128]) * (1328 * 1328)))
        self.assertEqual(decode_png(self.m.read_bytes())[:4], (1328, 1328, 1, bytes(mask)))

    def test_native_outside_bounds_not_used_as_crop(self):
        self.app.doc.mask.native = (-10, -5, 20, 30)
        result = self.pair()
        self.assertEqual(result["selection"]["native_bounds"]["x"], -10)
        self.assertEqual(result["selection"]["bounds"], {"x": 0, "y": 0, "width": 3, "height": 2})

    def test_no_selection_empty_selection_and_explicit_full_canvas(self):
        for value in (None, Selection(bytes(6))):
            self.app.doc.mask = value
            with self.assertRaises(snapshot.SnapshotError):
                self.pair()
            self.assertEqual(list(self.root.iterdir()), [])
        result = self.pair(selection_mode="full_canvas")
        self.assertEqual(decode_png(self.m.read_bytes())[3], b"\xff" * 6)
        self.assertEqual(result["selection"]["source"], "explicit_full_canvas")
        self.assertIsNone(result["selection"]["native_bounds"])

    def test_format_profile_lengths_and_no_document_fail_before_publish(self):
        doc = self.app.doc
        for attr, bad in (("model", "CMYKA"), ("depth", "U16"), ("profile", "linear-sRGB.icc"),
                          ("pixels", b"short")):
            old = getattr(doc, attr)
            with self.subTest(attr=attr), patch.object(doc, attr, bad), self.assertRaises(snapshot.SnapshotError):
                self.pair()
            self.assertEqual(getattr(doc, attr), old)
        doc.mask.data = b"short"
        with self.assertRaises(snapshot.SnapshotError):
            self.pair()
        self.app.doc = None
        with self.assertRaises(snapshot.SnapshotError):
            self.pair()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_native_api_is_explicit_failure(self):
        with patch.object(self.app.doc, "pixelData", None), self.assertRaisesRegex(snapshot.SnapshotError, "Document API"):
            self.pair()
        with patch.object(self.app.doc.mask, "pixelData", None), self.assertRaisesRegex(snapshot.SnapshotError, "Selection"):
            self.pair()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_input_paths_modes_and_bool_validation(self):
        for kwargs in ({}, {"projection_path": ""}, {"projection_path": "x.jpg"},
                       {"projection_path": str(self.root / "missing/x.png")},
                       {"projection_path": str(self.p), "overwrite": "true"},
                       {"projection_path": str(self.p), "selection_mode": "auto"},
                       {"projection_path": str(self.p), "selection_path": str(self.p)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(snapshot.SnapshotError):
                self.service.export(**kwargs)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.app.doc.events, [])

    def test_existing_and_alias_outputs_never_silently_overwritten(self):
        self.p.write_bytes(b"previous")
        with self.assertRaises(snapshot.SnapshotError):
            self.pair()
        self.assertEqual(self.p.read_bytes(), b"previous")
        self.m.symlink_to(self.p)
        with self.assertRaises(snapshot.SnapshotError):
            self.pair(overwrite=True)
        self.m.unlink()
        os.link(self.p, self.m)
        with self.assertRaises(snapshot.SnapshotError):
            self.pair(overwrite=True)

    def test_explicit_overwrite_succeeds(self):
        self.p.write_bytes(b"old projection")
        self.m.write_bytes(b"old mask")
        self.assertTrue(self.pair(overwrite=True)["paired"])
        decode_png(self.p.read_bytes())
        decode_png(self.m.read_bytes())

    def test_encoder_failure_never_publishes_first_file(self):
        original = snapshot.encode_png
        def fail_mask(data, width, height, channels):
            if channels == 1:
                raise snapshot.SnapshotError("PNG encode failure")
            return original(data, width, height, channels)
        with patch.object(snapshot, "encode_png", side_effect=fail_mask), self.assertRaises(snapshot.SnapshotError):
            self.pair()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_second_stage_failure_never_publishes(self):
        original = snapshot.tempfile.mkstemp
        calls = 0
        def stage(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk full")
            return original(*args, **kwargs)
        with patch.object(snapshot.tempfile, "mkstemp", side_effect=stage), self.assertRaisesRegex(snapshot.SnapshotError, "published_paths=\\[\\]"):
            self.pair()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_fsync_failure_leaves_no_published_files(self):
        with patch.object(snapshot.os, "fsync", side_effect=OSError("sync failed")), self.assertRaisesRegex(snapshot.SnapshotError, "published_paths=\\[\\]"):
            self.pair()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_second_publish_failure_reports_partial_without_success(self):
        original = snapshot.os.link
        def publish(src, dst):
            if Path(dst) == self.m:
                raise OSError("second publish failed")
            return original(src, dst)
        with patch.object(snapshot.os, "link", side_effect=publish), self.assertRaises(snapshot.SnapshotError) as exc:
            self.pair()
        self.assertIn(str(self.p), str(exc.exception))
        decode_png(self.p.read_bytes())
        self.assertFalse(self.m.exists())
        self.assertEqual(list(self.root.glob(".cwb-snapshot-*")), [])

    def test_no_clobber_publication_race_retains_competing_file(self):
        original = snapshot.os.link
        def race(src, dst):
            Path(dst).write_bytes(b"competing file")
            return original(src, dst)
        with patch.object(snapshot.os, "link", side_effect=race), self.assertRaises(snapshot.SnapshotError):
            self.pair()
        self.assertEqual(self.p.read_bytes(), b"competing file")
        self.assertFalse(self.m.exists())

    def test_overwrite_partial_failure_leaves_explicitly_reported_first_file(self):
        self.p.write_bytes(b"old p")
        self.m.write_bytes(b"old m")
        original = snapshot.os.replace
        def publish(src, dst):
            if Path(dst) == self.m:
                raise OSError("second overwrite failed")
            return original(src, dst)
        with patch.object(snapshot.os, "replace", side_effect=publish), self.assertRaisesRegex(snapshot.SnapshotError, "published_paths"):
            self.pair(overwrite=True)
        decode_png(self.p.read_bytes())
        self.assertEqual(self.m.read_bytes(), b"old m")

    def test_lifecycle_activity_during_sampling_discards_pair(self):
        self.app.doc.on_pixels = lambda: self.app.signals.imageCreated.emit(self.app.doc)
        with self.assertRaises(snapshot.SnapshotError):
            self.pair()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_canvas_change_during_sampling_discards_pair(self):
        self.app.doc.on_pixels = lambda: setattr(self.app.doc, "w", 4)
        with self.assertRaises(snapshot.SnapshotError):
            self.pair()
        self.assertEqual(list(self.root.iterdir()), [])


class Identity(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.tokens = snapshot.DocumentTokens(self.app)

    def test_stable_root_uuid_not_wrapper_identity_or_filename(self):
        token = self.tokens.token(self.app.doc)
        other_wrapper = Doc()
        other_wrapper.uid = self.app.doc.uid
        self.app.open_docs = [other_wrapper]
        self.assertEqual(token, self.tokens.token(other_wrapper, token))
        other_wrapper.w = 1328
        self.assertEqual(token, self.tokens.token(other_wrapper))

    def test_close_and_reopen_same_root_uuid_invalidates_old_token(self):
        old = self.tokens.token(self.app.doc)
        self.app.signals.imageClosed.emit("same.kra")
        reopened = Doc()
        reopened.uid = self.app.doc.uid
        self.app.doc = reopened
        self.app.open_docs = [reopened]
        with self.assertRaises(snapshot.SnapshotError):
            self.tokens.token(reopened, old)
        self.assertNotEqual(self.tokens.token(reopened), old)

    def test_creation_or_close_of_unrelated_doc_conservatively_invalidates(self):
        for name in ("imageCreated", "imageClosed"):
            old = self.tokens.token(self.app.doc)
            getattr(self.app.signals, name).emit("unrelated")
            with self.assertRaises(snapshot.SnapshotError):
                self.tokens.token(self.app.doc, old)

    def test_document_change_duplicate_uuid_missing_uuid_and_disabled_notifier(self):
        old = self.tokens.token(self.app.doc)
        other = Doc()
        self.app.open_docs.append(other)
        with self.assertRaises(snapshot.SnapshotError):
            self.tokens.token(other, old)
        other.uid = self.app.doc.uid
        with self.assertRaises(snapshot.SnapshotError):
            self.tokens.token(self.app.doc)
        self.app.open_docs = [self.app.doc]
        self.app.doc.uid = "not-uuid"
        with self.assertRaises(snapshot.SnapshotError):
            self.tokens.token(self.app.doc)
        self.app.doc.uid = str(uuid.UUID(int=0))
        with self.assertRaises(snapshot.SnapshotError):
            self.tokens.token(self.app.doc)
        self.app.signals.setActive(False)
        with self.assertRaises(snapshot.SnapshotError):
            self.tokens.token(self.app.doc)
        self.assertEqual(self.tokens.tokens, {})

    def test_missing_notifier_lifecycle_api_fails_closed(self):
        self.app.signals = object()
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.DocumentTokens(self.app)

    def test_new_service_session_cannot_reuse_token(self):
        old = self.tokens.token(self.app.doc)
        replacement = snapshot.DocumentTokens(self.app)
        with self.assertRaises(snapshot.SnapshotError):
            replacement.token(self.app.doc, old)


class Plumbing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qtgui = types.ModuleType("PyQt6.QtGui")
        qtgui.QImage = Mock()
        krita = types.ModuleType("krita")
        krita.Krita = Mock()
        cls.package = types.ModuleType("_cad13_package")
        cls.package.__path__ = []
        bridge = types.ModuleType("_cad13_package.bridge_qt")
        bridge.MainThreadBridge = Mock
        with patch.dict(sys.modules, {"PyQt6": types.ModuleType("PyQt6"), "PyQt6.QtGui": qtgui,
                                      "krita": krita, "_cad13_package": cls.package,
                                      "_cad13_package.bridge_qt": bridge}):
            cls.ops = load_module("_cad13_package.ops", ROOT / "core/krita_cowork/ops.py")
            with patch.dict(sys.modules, {"_cad13_package.ops": cls.ops}):
                cls.server = load_module("_cad13_package.server", ROOT / "core/krita_cowork/server.py")

    def test_ops_thin_wrappers_and_error_mapping(self):
        fake = types.ModuleType("_cad13_package.snapshot")
        fake.SnapshotError = snapshot.SnapshotError
        fake.export_snapshot = Mock(return_value={"paired": True})
        with patch.dict(sys.modules, {"_cad13_package": self.package, "_cad13_package.snapshot": fake}):
            for fn, args, expected in [
                (self.ops.doc_export, ("p.png",), {"projection_path": "p.png", "overwrite": False, "expected_document_token": None}),
                (self.ops.doc_selection_png, ("m.png",), {"selection_path": "m.png", "selection_mode": "selection", "overwrite": False, "expected_document_token": None}),
                (self.ops.doc_snapshot, ("p.png", "m.png"), {"projection_path": "p.png", "selection_path": "m.png", "selection_mode": "selection", "overwrite": False, "expected_document_token": None})]:
                self.assertEqual(fn(*args), {"paired": True})
                self.assertEqual(fake.export_snapshot.call_args.kwargs, expected)
            with self.assertRaises(self.ops.OpError):
                self.ops.doc_snapshot(None, "m.png")
            fake.export_snapshot.side_effect = snapshot.SnapshotError("bad selection")
            with self.assertRaises(self.ops.OpError):
                self.ops.doc_export("p.png")

    def handler(self):
        control = self.server.ControlServer(0)
        control.bridge.call.side_effect = lambda fn: fn()
        class FakeHTTP:
            def __init__(self, address, handler):
                self.address, self.handler = address, handler
            def serve_forever(self, **kwargs): pass
        with patch.object(self.server, "ThreadingHTTPServer", FakeHTTP), patch("threading.Thread"):
            control.start()
        self.assertEqual(control._httpd.address, ("127.0.0.1", 0))
        return control, control._httpd.handler

    def request(self, cls, route, body):
        handler = cls.__new__(cls)
        handler.path = route
        raw = json.dumps(body).encode()
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.close_connection = False
        handler._reply = Mock()
        handler._dispatch("POST")
        return handler._reply.call_args.args

    def test_routes_one_bridge_callback_and_required_fields(self):
        for route, body in [
            ("/doc/export", {"path": "p.png", "overwrite": True}),
            ("/doc/selection_png", {"path": "m.png", "selection_mode": "full_canvas"}),
            ("/doc/snapshot", {"projection_path": "p.png", "selection_path": "m.png", "expected_document_token": "doc-x"})]:
            control, cls = self.handler()
            old = self.server.POST_ROUTES[route]
            fn = Mock(return_value={"paired": route == "/doc/snapshot"})
            with patch.dict(self.server.POST_ROUTES, {route: (fn, old[1], old[2])}):
                self.assertEqual(self.request(cls, route, body)[0], 200)
                fn.assert_called_once_with(**body)
                control.bridge.call.assert_called_once()
        control, cls = self.handler()
        self.assertEqual(self.request(cls, "/doc/snapshot", {"projection_path": "p.png"})[0], 400)
        control.bridge.call.assert_not_called()

    def test_error_and_timeout_never_report_pair_success(self):
        for error, expected in ((self.ops.OpError("partial PNG failure"), 400), (TimeoutError("busy"), 503)):
            control, cls = self.handler()
            control.bridge.call.side_effect = error
            status, payload = self.request(cls, "/doc/snapshot", {"projection_path": "p", "selection_path": "m"})
            self.assertEqual(status, expected)
            self.assertNotIn("paired", payload)

    def test_cli_payloads(self):
        for args, route, expected in [
            (["export", "p.png"], "/doc/export", {"path": str(Path("p.png").absolute()), "overwrite": False}),
            (["selection-png", "m.png", "--full-canvas"], "/doc/selection_png",
             {"path": str(Path("m.png").absolute()), "overwrite": False, "selection_mode": "full_canvas"}),
            (["snapshot", "p.png", "m.png", "--overwrite", "--expect-document-token", "doc-x"], "/doc/snapshot",
             {"projection_path": str(Path("p.png").absolute()), "selection_path": str(Path("m.png").absolute()),
              "overwrite": True, "selection_mode": "selection", "expected_document_token": "doc-x"})]:
            with patch.object(cwb, "http", return_value={}) as http, contextlib.redirect_stdout(io.StringIO()):
                cwb.main(args)
                http.assert_called_once_with(cwb.Krita + route, expected, timeout=120)

    def test_help_offline_no_cache(self):
        with tempfile.TemporaryDirectory(prefix="cad13-help-") as tmp:
            target = Path(tmp) / "absent"
            for args in (["--help"], ["export", "--help"], ["selection-png", "--help"], ["snapshot", "--help"]):
                with patch.object(cwb, "http", side_effect=AssertionError("network")), \
                        patch.object(os, "makedirs", side_effect=AssertionError("mkdir")), \
                        contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    cwb.main(args)
                self.assertEqual(exc.exception.code, 0)
                proc = subprocess.run([sys.executable, "-B", str(ROOT / "core/cwb.py"), *args], cwd=tmp,
                                      env={**os.environ, "CWB_WF_DIR": str(target)}, capture_output=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertFalse(target.exists())
                self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
