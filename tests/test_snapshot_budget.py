"""CAD-13 bounded export regression; Python doubles are NOT Krita evidence."""
from array import array
import ast
import gc
import hashlib
import inspect
import random
import struct
import sys
import tempfile
import threading
import tracemalloc
import types
import unittest
import weakref
from pathlib import Path
from unittest.mock import Mock, patch

import test_snapshot as base
import test_layer_groups as groups

snapshot = base.snapshot
placement = base.load_module("_cad13_budget_placement", base.ROOT / "core/krita_cowork/placement.py")


def chunks(png):
    pos = 8
    while pos < len(png):
        size, kind = struct.unpack(">I4s", png[pos:pos + 8])
        yield kind, size
        pos += size + 12


class ExportAdmission(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cad13-budget-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = base.App()
        self.service = snapshot.SnapshotService(self.app)
        self.paths = {"projection_path": str(self.root / "p.png"),
                      "selection_path": str(self.root / "m.png")}

    def export(self, **kwargs):
        return self.service.export(**self.paths, **kwargs)

    def assert_rejected_before_native(self, **kwargs):
        doc = self.app.doc
        with patch.object(doc, "pixelData", side_effect=AssertionError("native read")), \
                patch.object(doc, "selection", side_effect=AssertionError("native selection")), \
                patch.object(snapshot, "encode_png", side_effect=AssertionError("encode")), \
                patch.object(snapshot, "publish_pngs", side_effect=AssertionError("publish")), \
                self.assertRaises(snapshot.SnapshotError):
            self.export(**kwargs)
        self.assertEqual(doc.events, [])  # Even wait/refresh were not started.
        self.assertEqual(list(self.root.iterdir()), [])

    def test_actual_oversized_pair_projection_mask_and_full_canvas_preflight(self):
        for width, height in ((32768, 32768), (4096, 4097), (8192, 2049)):
            for kinds in (("projection_path",), ("selection_path",), tuple(self.paths)):
                for mode in ("selection", "full_canvas"):
                    with self.subTest(size=(width, height), kinds=kinds, mode=mode):
                        self.app.doc.w, self.app.doc.h = width, height
                        all_paths = self.paths
                        self.paths = {kind: all_paths[kind] for kind in kinds}
                        try:
                            self.assert_rejected_before_native(selection_mode=mode)
                        finally:
                            self.paths = all_paths

    def test_exact_real_pixel_boundary_admits_without_allocating_large_fixture(self):
        class ReachedRead(Exception):
            pass
        for width, height in ((4096, 4096), (8192, 2048), (512, 32768)):
            with self.subTest(size=(width, height)):
                self.app.doc.w, self.app.doc.h = width, height
                self.app.doc.events.clear()
                with patch.object(self.app.doc, "pixelData", side_effect=ReachedRead), \
                        self.assertRaises(ReachedRead):
                    self.export()
                self.assertEqual(self.app.doc.events, ["wait", "refresh", "wait"])
                self.assertEqual(list(self.root.iterdir()), [])
                budget = snapshot._export_budget(width, height, ("projection", "selection"))
                self.assertEqual(budget["canvas_pixels"], snapshot.MAX_EXPORT_PIXELS)
                self.assertLessEqual(budget["estimated_owned_buffer_bytes"], snapshot.MAX_EXPORT_OWNED_BYTES)

    def test_small_exact_budget_accepts_and_each_limit_rejects_before_read(self):
        expected = snapshot._export_budget(3, 2, ("projection", "selection"))
        with patch.object(snapshot, "MAX_EXPORT_PIXELS", 6), \
                patch.object(snapshot, "MAX_EXPORT_PIXEL_BYTES", 24), \
                patch.object(snapshot, "MAX_EXPORT_OWNED_BYTES", expected["estimated_owned_buffer_bytes"]):
            result = self.export()
        self.assertEqual(result["resource_budget"]["estimated_owned_buffer_bytes"],
                         expected["estimated_owned_buffer_bytes"])
        for path in self.root.iterdir():
            path.unlink()  # Only this test's generated files.
        self.app.doc.events.clear()
        for name, limit in (("MAX_EXPORT_PIXELS", 5), ("MAX_EXPORT_PIXEL_BYTES", 23),
                            ("MAX_EXPORT_OWNED_BYTES", expected["estimated_owned_buffer_bytes"] - 1)):
            with self.subTest(limit=name), patch.object(snapshot, name, limit):
                self.assert_rejected_before_native()

    def test_pair_estimate_counts_held_mask_and_png_not_only_single_buffer(self):
        projection = snapshot._export_budget(4096, 4096, ("projection",))
        pair = snapshot._export_budget(4096, 4096, ("projection", "selection"))
        self.assertEqual(pair["captured_pixel_bytes"], 80 * 1024 * 1024)
        self.assertGreater(pair["estimated_owned_buffer_bytes"], projection["estimated_owned_buffer_bytes"])
        self.assertEqual(set(pair["png_byte_limits"]), {"projection", "selection"})
        self.assertIn("excludes", pair["scope"])
        self.assertEqual(pair["max_concurrent_document_exports"], 1)

    def test_oversized_encoded_second_result_never_stages_or_publishes(self):
        original = snapshot.encode_png
        def encode(data, width, height, channels):
            if channels == 1:
                return b"x" * (snapshot._png_byte_limit(width, height, channels) + 1)
            return original(data, width, height, channels)
        with patch.object(snapshot, "encode_png", side_effect=encode), \
                patch.object(snapshot, "publish_pngs") as publish, \
                self.assertRaisesRegex(snapshot.SnapshotError, "未发布"):
            self.export()
        publish.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_actual_native_wrong_length_is_rejected_before_python_copy(self):
        class CopyTrap(bytearray):
            def __bytes__(self):
                raise AssertionError("copy before length validation")
        for data in (CopyTrap(b"x" * 23), CopyTrap(b"x" * 25)):
            with self.subTest(size=len(data)), \
                    patch.object(self.app.doc, "pixelData", return_value=data), \
                    patch.object(self.app.doc, "selection", side_effect=AssertionError("second read")), \
                    self.assertRaisesRegex(snapshot.SnapshotError, "长度异常"):
                self.export()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_encoder_budget_failure_leaves_existing_files_untouched(self):
        for path in self.paths.values():
            Path(path).write_bytes(b"original")
        with patch.object(snapshot, "_png_byte_limit", return_value=45), \
                self.assertRaisesRegex(snapshot.SnapshotError, "PNG 编码输出超预算"):
            self.export(overwrite=True)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()},
                         {"p.png": b"original", "m.png": b"original"})

    def test_guard_releases_after_admission_read_and_publish_failures(self):
        for target, message in (("pixelData", "read failure"), ("refreshProjection", "refresh failure")):
            with patch.object(self.app.doc, target, side_effect=RuntimeError(message)), \
                    self.assertRaisesRegex(RuntimeError, message):
                self.export()
        with patch.object(snapshot, "publish_pngs", side_effect=OSError("publish failure")), \
                self.assertRaises(OSError):
            self.export()
        self.assertTrue(self.export()["paired"])

    def test_concurrent_services_reject_before_read_and_release_after_completion(self):
        entered, release = threading.Event(), threading.Event()
        outcome = []
        def block():
            entered.set()
            if not release.wait(5):
                raise AssertionError("test did not release fake read")
        self.app.doc.on_pixels = block
        def first_export():
            try:
                outcome.append(self.export())
            except BaseException as exc:
                outcome.append(exc)
        worker = threading.Thread(target=first_export)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            other_app = base.App()
            other_service = snapshot.SnapshotService(other_app)
            with self.assertRaisesRegex(snapshot.SnapshotError, "并发"):
                other_service.export(projection_path=str(self.root / "other.png"))
            self.assertEqual(other_app.doc.events, [])
            self.assertFalse((self.root / "other.png").exists())
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], dict, outcome)
        other_service.export(projection_path=str(self.root / "other.png"))
        self.assertTrue((self.root / "other.png").exists())

    def test_reentrant_export_is_rejected_without_deadlock(self):
        def nested():
            with self.assertRaisesRegex(snapshot.SnapshotError, "并发"):
                self.service.export(projection_path=str(self.root / "nested.png"))
        self.app.doc.on_pixels = nested
        self.assertTrue(self.export()["paired"])
        self.assertFalse((self.root / "nested.png").exists())


class BoundedCodec(unittest.TestCase):
    def test_arbitrary_bytes_and_iterable_are_not_executed(self):
        class Trap:
            def __bytes__(self):
                raise AssertionError("unbounded bytes conversion")
            def __iter__(self):
                raise AssertionError("unbounded iterable")
        with self.assertRaises(snapshot.SnapshotError):
            snapshot._pixels(Trap(), 1, 1, 4)

    def test_actual_buffer_length_checked_before_copy_and_nbytes_not_len(self):
        class CopyTrap(bytearray):
            def __bytes__(self):
                raise AssertionError("unbounded bytes override")
        for data in (CopyTrap(b"a"), CopyTrap(b"abcde")):
            with self.assertRaisesRegex(snapshot.SnapshotError, "长度异常"):
                snapshot._pixels(data, 1, 1, 4)
        self.assertEqual(snapshot._pixels(CopyTrap(b"abcd"), 1, 1, 4), b"abcd")
        words = array("I", [0x12345678])
        self.assertEqual(snapshot._pixels(memoryview(words), 1, 1, 4), words.tobytes())
        self.assertEqual(snapshot._pixels(memoryview(b"a0b0c0d0")[::2], 1, 1, 4), b"abcd")
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.encode_png(b"a", 1, 1, True)

    def test_streamed_input_and_idat_chunks_are_bounded_with_exact_pixels(self):
        rng = random.Random(130055)
        real_compressobj = snapshot.zlib.compressobj
        input_sizes = []
        class Recorder:
            def __init__(self, *args):
                self.compressor = real_compressobj(*args)
            def compress(self, data):
                input_sizes.append(len(data))
                if len(data) > snapshot.ENCODE_BLOCK_BYTES:
                    raise AssertionError("oversized compressor input")
                return self.compressor.compress(data)
            def flush(self):
                return self.compressor.flush()
        with patch.object(snapshot.zlib, "compressobj", Recorder), \
                patch.object(snapshot.zlib, "compress", side_effect=AssertionError("whole-image compression")):
            for channels in (1, 4):
                for width in (1, 3, 16383, 16384, 16385, 32768):
                    height = 2
                    raw = rng.randbytes(width * height * channels)
                    with self.subTest(width=width, channels=channels):
                        png = snapshot.encode_png(raw, width, height, channels)
                        self.assertEqual(base.decode_png(png)[:4], (width, height, channels, raw))
                        self.assertLessEqual(len(png), snapshot._png_byte_limit(width, height, channels))
                        self.assertTrue(all(size <= snapshot.ENCODE_BLOCK_BYTES
                                            for kind, size in chunks(png) if kind == b"IDAT"))
        self.assertEqual(max(input_sizes), snapshot.ENCODE_BLOCK_BYTES)

    def test_alpha_all_values_hidden_rgb_and_pixel_digest_preserved(self):
        raw = b"".join(bytes((17, 203, 51, alpha)) for alpha in range(256))
        png = snapshot.encode_png(raw, 256, 1, 4)
        pixels = base.decode_png(png)[3]
        self.assertEqual(pixels, raw)
        self.assertEqual(hashlib.sha256(pixels).digest(), hashlib.sha256(raw).digest())

    def test_shared_codecs_do_not_inherit_document_export_limits(self):
        with patch.object(snapshot, "MAX_EXPORT_PIXELS", 0), \
                patch.object(snapshot, "MAX_EXPORT_PIXEL_BYTES", 0), \
                patch.object(snapshot, "MAX_EXPORT_OWNED_BYTES", 0):
            raw = bytes((1, 2, 3, 0, 4, 5, 6, 128, 7, 8, 9, 255))
            self.assertEqual(snapshot.swap_rb(snapshot.swap_rb(raw, 3, 1), 3, 1), raw)
            self.assertEqual(base.decode_png(snapshot.encode_png(raw, 3, 1, 4))[3], raw)
            self.assertEqual(snapshot.alpha_summary(raw, 3, 1)["coverage_pixels"], 2)

    def test_multiple_idat_png_passes_existing_import_admission_without_pixel_changes(self):
        width, height = 257, 257
        raw = random.Random(13).randbytes(width * height * 4)
        png = snapshot.encode_png(raw, width, height, 4)
        self.assertGreater(sum(kind == b"IDAT" for kind, _ in chunks(png)), 1)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "多块.png"
            path.write_bytes(png)
            admitted, mapping = placement.prepare_png(str(path), width, height)
        self.assertEqual(admitted, png)
        self.assertEqual(base.decode_png(admitted)[3], raw)
        self.assertEqual(mapping["output_size"], [width, height])

    def test_actual_layer_png_call_preserves_original_underlay_modes(self):
        package = types.ModuleType("_cad11_package")
        package.__path__ = [str(base.ROOT / "core/krita_cowork")]
        raw = bytes((11, 29, 233, 0, 17, 83, 197, 1, 39, 71, 193, 128, 47, 97, 181, 255))
        bgra = snapshot.swap_rb(raw, 4, 1)
        bounds = types.SimpleNamespace(x=lambda: 5, y=lambda: 7, width=lambda: 4, height=lambda: 1)
        node = types.SimpleNamespace(bounds=lambda: bounds, pixelData=Mock(return_value=bytearray(bgra)))
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(sys.modules, {"_cad11_package": package, "_cad11_package.snapshot": snapshot}), \
                patch.object(groups.ops, "_active_doc", return_value=groups.Doc()), \
                patch.object(groups.ops, "_find_node", return_value=node), \
                patch.object(snapshot, "MAX_EXPORT_PIXELS", 0):
            for mode in ("none", "magenta"):
                path = Path(temporary) / (mode + ".png")
                result = groups.ops.layer_png("fixture", str(path), underlay=mode)
                expected = raw if mode == "none" else snapshot.flatten_over_magenta(raw, 4, 1)[0]
                self.assertEqual(base.decode_png(path.read_bytes())[3], expected)
                self.assertEqual(result["alpha"], {"coverage_pixels": 3,
                                                  "bbox": {"x": 6, "y": 7, "width": 3, "height": 1}})
        node.pixelData.assert_called_with(5, 7, 4, 1)


class FailedExportLifetime(unittest.TestCase):
    """Actual bridge method, without QObject/Qt; no forced GC as a remedy."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cad13-failure-life-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = base.App()
        self.app.doc.w = self.app.doc.h = 256
        self.app.doc.bgra = random.Random(55).randbytes(256 * 256 * 4)
        self.app.doc.mask = base.Selection(b"\xff" * (256 * 256), (0, 0, 256, 256))
        self.service = snapshot.SnapshotService(self.app)
        # Execute the unmodified production method body. The QObject host is
        # unused by _on_run; this does NOT test Qt signal delivery or threads.
        path = base.ROOT / "core/krita_cowork/bridge_qt.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        runner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_Runner")
        method = next(node for node in runner.body if isinstance(node, ast.FunctionDef) and node.name == "_on_run")
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
        self.on_run = namespace["_on_run"]

    def run_failure(self, index):
        box = {"event": threading.Event()}
        self.on_run(None, lambda: self.service.export(
            projection_path=str(self.root / ("p%d.png" % index)),
            selection_path=str(self.root / ("m%d.png" % index))), box)
        self.assertTrue(box["event"].is_set())
        self.assertIn("error", box)
        try:
            raise box["error"]
        except BaseException as error:
            return type(error), str(error)  # Never retain exception/box here.

    def assert_failure_series_bounded(self, fault, *, partial=False, expected=snapshot.SnapshotError):
        enabled_before = gc.isenabled()
        try:
            # Default GC first, then explicitly disabled: releasing image-sized
            # objects must not depend on an automatic collection happening.
            for enabled in (True, False):
                gc.enable() if enabled else gc.disable()
                tracemalloc.start()
                try:
                    with fault():
                        for attempt in range(3):
                            index = attempt + (0 if enabled else 3)
                            kind, message = self.run_failure(index)
                            self.assertIs(kind, expected)
                            self.assertFalse(snapshot._EXPORT_LOCK.locked())
                            self.assertLess(tracemalloc.get_traced_memory()[0], 256 * 1024,
                                            "failed exports retained image-sized Python objects")
                            self.assertFalse((self.root / ("m%d.png" % index)).exists())
                            self.assertEqual((self.root / ("p%d.png" % index)).exists(), partial)
                            if partial:
                                self.assertIn(str(self.root / ("p%d.png" % index)), message)
                    self.assertEqual(list(self.root.glob(".cwb-snapshot-*")), [])
                finally:
                    tracemalloc.stop()
        finally:
            gc.enable() if enabled_before else gc.disable()

    def test_repeated_native_buffer_failure_drops_captured_projection(self):
        self.assert_failure_series_bounded(
            lambda: patch.object(self.app.doc.mask, "data", b"short"))

    def test_repeated_second_encoder_budget_failure_drops_raw_and_encoded(self):
        original = snapshot._png_byte_limit
        def limit(width, height, channels):
            return 45 if channels == 1 else original(width, height, channels)
        self.assert_failure_series_bounded(lambda: patch.object(snapshot, "_png_byte_limit", limit))

    def test_repeated_second_zlib_failure_clears_deep_encoder_frames(self):
        original = snapshot.zlib.compressobj
        count = [0]
        class Compressor:
            def __init__(self, *args):
                count[0] += 1
                self.fail = count[0] % 2 == 0
                self.original = original(*args)
            def compress(self, data):
                if self.fail and len(data) > 1:
                    # Model a C zlib failure, not an external Python owner
                    # deliberately retaining its input or compressor object.
                    del data, self
                    raise snapshot.zlib.error("injected second zlib failure")
                return self.original.compress(data)
            def flush(self):
                return self.original.flush()
        self.assert_failure_series_bounded(
            lambda: patch.object(snapshot.zlib, "compressobj", Compressor), expected=snapshot.zlib.error)

    def test_repeated_fsync_failure_clears_actual_publish_and_chained_error(self):
        def fail(fd):
            raise OSError("injected fsync failure")
        self.assert_failure_series_bounded(lambda: patch.object(snapshot.os, "fsync", fail))

    def test_repeated_first_publish_failure_drops_both_pngs(self):
        def fail(*args):
            raise OSError("injected first link failure")
        self.assert_failure_series_bounded(lambda: patch.object(snapshot.os, "link", fail))

    def test_repeated_second_publish_failure_preserves_partial_paths(self):
        original = snapshot.os.link
        def fail_second(source, target):
            if Path(target.decode("utf-8")).name.startswith("m"):
                raise OSError("injected second link failure")
            return original(source, target)
        self.assert_failure_series_bounded(lambda: patch.object(snapshot.os, "link", fail_second), partial=True)

    def test_external_frame_payloads_and_diagnostics_are_not_ours_to_clear(self):
        references = []
        class Payload:
            def __init__(self):
                self.data = bytearray(1024 * 1024)
        def inner():
            payload = Payload()
            references.append(weakref.ref(payload))
            raise ValueError("inner cause")
        def failed_export(**kwargs):
            payload = Payload()
            references.append(weakref.ref(payload))
            try:
                inner()
            except ValueError as cause:
                raise ExceptionGroup("group error", [cause, TypeError("sibling")]) from cause
        box = {"event": threading.Event()}
        with patch.object(self.service, "_export", failed_export):
            self.on_run(None, lambda: self.service.export(projection_path="unused.png"), box)
        error = box["error"]  # Keeping the exception itself must be safe, too.
        self.assertIsInstance(error, ExceptionGroup)
        self.assertEqual(error.message, "group error")
        self.assertIsInstance(error.__cause__, ValueError)
        self.assertEqual(str(error.__cause__), "inner cause")
        self.assertIs(error.__context__, error.__cause__)
        self.assertIs(error.exceptions[0], error.__cause__)
        self.assertIsInstance(error.exceptions[1], TypeError)
        self.assertTrue(all(reference() is not None for reference in references))
        names = []
        current = error.__traceback__
        while current is not None:
            names.append(current.tb_frame.f_code.co_name)
            current = current.tb_next
        self.assertIn("failed_export", names)  # Locations survive frame cleanup.
        self.assertFalse(snapshot._EXPORT_LOCK.locked())

    def captured_leaf(self):
        # A real ended _export frame with module-created RGBA, not a payload
        # invented in an external test frame. Skip the wrapper only to leave
        # this leaf uncleared until the enclosing error/group is exported.
        def fail():
            raise ValueError("retained leaf")
        with patch.object(self.app.doc, "selection", fail):
            try:
                self.service._export(projection_path=str(self.root / "leaf-p.png"),
                    selection_path=str(self.root / "leaf-m.png"), overwrite=False,
                    selection_mode="selection", expected_document_token=None)
            except ValueError as error:
                tb = error.__traceback__
                while tb.tb_frame.f_code.co_name != "_export":
                    tb = tb.tb_next
                frame = tb.tb_frame
                self.assertEqual(len(frame.f_locals["captured"]["projection"]), 256 * 256 * 4)
                return error, frame
        self.fail("expected capture failure")

    def native_failure(self, original):
        def fail():
            raise original
        box = {"event": threading.Event()}
        with patch.object(self.app.doc, "selection", fail):
            self.on_run(None, lambda: self.service.export(
                projection_path=str(self.root / "p.png"),
                selection_path=str(self.root / "m.png")), box)
        self.assertTrue(box["event"].is_set())
        self.assertFalse(snapshot._EXPORT_LOCK.locked())
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIs(box["error"], original)
        # The actual export captured RGBA before native Selection failed.
        # Inspect the real traceback descriptor, not the subclass's getter.
        tb = BaseException.__traceback__.__get__(original)
        found_export = False
        while tb is not None:
            if tb.tb_frame.f_code.co_name == "_export":
                found_export = True
                self.assertEqual(tb.tb_frame.f_locals, {})
            tb = tb.tb_next
        self.assertTrue(found_export)

    def test_native_exception_metadata_properties_cannot_hijack_cleanup(self):
        accesses = []
        def trap(error):
            accesses.append(True)
            raise LookupError("metadata property must not execute")
        for attribute in ("__traceback__", "__cause__", "__context__", "__class__"):
            with self.subTest(attribute=attribute):
                failure = type("NativeFailure", (RuntimeError,), {attribute: property(trap)})
                original = failure("original native failure")
                leaf, captured_frame = self.captured_leaf()
                BaseException.__cause__.__set__(original, leaf)
                BaseException.__context__.__set__(leaf, original)  # Real cycle.
                original.operation_id = "preserved"
                self.native_failure(original)
                self.assertEqual(captured_frame.f_locals, {})
                self.assertIs(BaseException.__cause__.__get__(original), leaf)
                self.assertIs(BaseException.__context__.__get__(leaf), original)
                self.assertEqual(original.operation_id, "preserved")
        self.assertEqual(accesses, [])

    def test_native_getattribute_override_cannot_intercept_base_descriptors(self):
        accesses = []
        class NativeFailure(RuntimeError):
            def __getattribute__(self, name):
                if name in ("__traceback__", "__cause__", "__context__", "__class__"):
                    accesses.append(name)
                    raise LookupError("metadata interception")
                return super().__getattribute__(name)
        original = NativeFailure("original")
        leaf, captured_frame = self.captured_leaf()
        BaseException.__context__.__set__(original, leaf)
        self.native_failure(original)
        self.assertEqual(captured_frame.f_locals, {})
        self.assertEqual(accesses, [])

    def test_group_subclass_properties_cannot_hide_nested_payloads(self):
        accesses = []
        def trap(error):
            accesses.append(True)
            raise LookupError("group metadata property must not execute")
        for getter in (trap, lambda error: ()):
            group = type("NativeGroup", (ExceptionGroup,), {
                "exceptions": property(getter), "__traceback__": property(trap),
                "__cause__": property(trap), "__context__": property(trap),
                "__class__": property(trap)})
            leaf, captured_frame = self.captured_leaf()
            nested = group("nested", [leaf])
            original = group("original", [nested])
            BaseException.__cause__.__set__(original, nested)
            BaseException.__context__.__set__(leaf, original)
            self.native_failure(original)
            self.assertEqual(captured_frame.f_locals, {})
            self.assertIs(BaseExceptionGroup.exceptions.__get__(original)[0], nested)
            self.assertIs(BaseExceptionGroup.exceptions.__get__(nested)[0], leaf)
        self.assertEqual(accesses, [])

    def test_active_owned_frame_does_not_skip_group_siblings(self):
        first, first_frame = self.captured_leaf()
        second, second_frame = self.captured_leaf()
        original = ExceptionGroup("original", [first, second])
        # Function with this module's actual globals makes frame.clear() hit
        # the executing-frame RuntimeError; cleanup must continue to siblings.
        def active(original):
            sentinel = bytearray(b"still active")
            try:
                raise original
            except BaseException:
                _release_failure_frames(original)
                return sentinel
        owned_active = types.FunctionType(active.__code__, snapshot.__dict__)
        self.assertEqual(owned_active(original), b"still active")
        self.assertEqual(first_frame.f_locals, {})
        self.assertEqual(second_frame.f_locals, {})
        self.assertEqual(original.message, "original")

    def test_last_resort_cleanup_failure_preserves_original_admission_error(self):
        # Simulate an unexpected graph-walk failure before any large allocation.
        # This protects diagnostics; normal per-frame cleanup continues past
        # active frames, independently exercised above with real captures.
        self.app.doc.w = 0
        def unavailable(error):
            raise MemoryError("graph-walk allocation unavailable")
        with patch.object(snapshot, "_release_failure_frames", unavailable):
            kind, message = self.run_failure(0)
        self.assertIs(kind, snapshot.SnapshotError)
        self.assertIn("无效画布尺寸", message)
        self.assertFalse(snapshot._EXPORT_LOCK.locked())

    def test_current_frame_is_not_cleared(self):
        sentinel = bytearray(b"still in use")
        try:
            raise ValueError("active frame")
        except ValueError as error:
            snapshot._release_failure_frames(error)
            self.assertEqual(sentinel, b"still in use")

    def test_suspended_generator_survives_actual_native_failure(self):
        for relation in ("direct", "cause", "context", "group"):
            with self.subTest(relation=relation):
                events = []
                def owner():
                    sentinel = bytearray(b"external live state")
                    try:
                        try:
                            raise ValueError("handled operation")
                        except ValueError as error:
                            yield error
                        yield ("continuation", sentinel)
                    finally:
                        events.append("closed")
                generator = owner()
                leaf = next(generator)
                original = (leaf if relation == "direct" else
                    ExceptionGroup("native group", [leaf]) if relation == "group" else
                    RuntimeError("native failure"))
                if relation in ("cause", "context"):
                    getattr(BaseException, "__" + relation + "__").__set__(original, leaf)
                try:
                    self.native_failure(original)
                    self.assertEqual(inspect.getgeneratorstate(generator), "GEN_SUSPENDED")
                    self.assertEqual(events, [])
                    self.assertEqual(next(generator), ("continuation", b"external live state"))
                finally:
                    generator.close()  # Only the owner closes after verification.
                self.assertEqual(events, ["closed"])

    def test_suspended_coroutine_survives_actual_native_failure(self):
        events = []
        class Pause:
            def __init__(self, error):
                self.error = error
            def __await__(self):
                yield self.error
        async def owner():
            sentinel = bytearray(b"coroutine live state")
            try:
                try:
                    raise ValueError("handled operation")
                except ValueError as error:
                    await Pause(error)
                return sentinel
            finally:
                events.append("closed")
        coroutine = owner()
        original = coroutine.send(None)
        try:
            self.native_failure(original)
            self.assertEqual(inspect.getcoroutinestate(coroutine), "CORO_SUSPENDED")
            self.assertEqual(events, [])
            with self.assertRaises(StopIteration) as stopped:
                coroutine.send(None)
            self.assertEqual(stopped.exception.value, b"coroutine live state")
            self.assertEqual(events, ["closed"])
        finally:
            coroutine.close()

    def test_suspended_async_generator_survives_actual_native_failure(self):
        async def owner(events):
            sentinel = bytearray(b"async generator live state")
            try:
                try:
                    raise ValueError("handled operation")
                except ValueError as error:
                    yield error
                yield sentinel
            finally:
                events.append("closed")
        def advance(operation):
            with self.assertRaises(StopIteration) as stopped:
                operation.send(None)
            return stopped.exception.value
        for function in (owner, types.FunctionType(owner.__code__, snapshot.__dict__)):
            events = []
            generator = function(events)
            original = advance(generator.__anext__())
            try:
                self.native_failure(original)
                self.assertIsNotNone(generator.ag_frame)
                self.assertFalse(generator.ag_running)
                self.assertEqual(events, [])
                self.assertEqual(advance(generator.__anext__()), b"async generator live state")
            finally:
                advance(generator.aclose())
            self.assertEqual(events, ["closed"])

    def test_module_generator_flags_are_never_cleared(self):
        # Future-proof the module ownership filter: even matching globals do
        # not make suspended generator/coroutine state ours to destroy.
        def owner(events):
            try:
                try:
                    raise ValueError("handled operation")
                except ValueError as error:
                    yield error
                yield "continuation"
            finally:
                events.append("closed")
        owned_owner = types.FunctionType(owner.__code__, snapshot.__dict__)
        events = []
        generator = owned_owner(events)
        original = next(generator)
        try:
            self.native_failure(original)
            self.assertEqual(inspect.getgeneratorstate(generator), "GEN_SUSPENDED")
            self.assertEqual(events, [])
            self.assertEqual(next(generator), "continuation")
        finally:
            generator.close()



if __name__ == "__main__":
    unittest.main()
