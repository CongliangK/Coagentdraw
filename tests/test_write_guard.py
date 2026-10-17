"""CAD-49 CPU protocol/production-ops tests; NOT real Qt/Krita acceptance.

Fresh wrappers below model libkis native QPointer equality. They are deliberately
not Python-object-identity stable and do not emit lifecycle notifier signals.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
import uuid
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
import test_layer_groups as groups
import test_stage as stage_tests
import cwb_stage as stage

# Use an isolated package so no real Qt/Krita module is ever imported.
PKG = "_cad49_package"
package = types.ModuleType(PKG)
package.__path__ = [str(ROOT / "core/krita_cowork")]
with patch.dict(sys.modules, {PKG: package}):
    guard = __import__(PKG + ".write_guard", fromlist=["*"])


class Node(groups.Node):
    def __init__(self, name, kind):
        super().__init__(name, kind)
        self.uid = "{" + str(uuid.uuid4()) + "}"


class Native:
    def __init__(self, filename="/tmp/专用测试.kra"):
        self.doc = groups.Doc()
        self.doc.root = Node("", "grouplayer")
        self.doc.factory = Node
        self.filename, self.closed = filename, False
        self.width, self.height = 16, 16
        self.title = "fixture"


class Wrapper:
    def __init__(self, native):
        self.native = native

    def __eq__(self, other):
        if not isinstance(other, Wrapper):
            return NotImplemented
        # The referenced native pointer becomes null after destruction.
        left = None if self.native.closed else self.native
        right = None if other.native.closed else other.native
        return left is right

    def rootNode(self):
        return None if self.native.closed else self.native.doc.root

    def fileName(self):
        return "" if self.native.closed else self.native.filename

    def name(self):
        return "" if self.native.closed else self.native.title

    def width(self):
        return 0 if self.native.closed else self.native.width

    def height(self):
        return 0 if self.native.closed else self.native.height

    def createNode(self, *args):
        return self.native.doc.createNode(*args)

    def refreshProjection(self):
        return self.native.doc.refreshProjection()


class App:
    def __init__(self):
        self.opened = [Native()]
        self.current = self.opened[0]
        self.wrapper_type = Wrapper

    def activeDocument(self):
        return self.wrapper_type(self.current) if self.current else None

    def documents(self):
        return [self.wrapper_type(item) for item in self.opened]


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.native = self.app.current
        self.service = guard.WriteService(self.app)
        self.modules = patch.dict(sys.modules, {PKG: package, PKG + ".write_guard": guard})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        qtgui = types.ModuleType("PyQt6.QtGui")
        qtgui.QImage = groups.Image
        krita = types.ModuleType("krita")
        krita.Krita = Mock()
        with patch.dict(sys.modules, {"PyQt6": types.ModuleType("PyQt6"),
                                    "PyQt6.QtGui": qtgui, "krita": krita}):
            self.ops = groups.load_module(PKG + ".ops", ROOT / "core/krita_cowork/ops.py")
        self.ops._write_service = self.service
        self.ops._krita = lambda: self.app
        self.target = self.service.target()["expected_target"]
        self.temp = tempfile.TemporaryDirectory(prefix="cad49-")
        self.addCleanup(self.temp.cleanup)
        # Valid 1x2 PNG; fake Qt reads 1x2 padded bytes, not actual pixels.
        snapshot = __import__(PKG + ".snapshot", fromlist=["*"])
        self.png = Path(self.temp.name) / "来源.png"
        self.png.write_bytes(snapshot.encode_png(b"abcdefgh", 1, 2, 4))
        self.digest = hashlib.sha256(self.png.read_bytes()).hexdigest()

    def request(self, kind="group", **changes):
        request = {"operation_id": str(uuid.uuid4()), "expected_target": copy.deepcopy(self.target),
                   "expected_bindings": [], "name": "new"}
        if kind == "add":
            request.update(png_path=str(self.png), source_sha256=self.digest)
        request.update(changes)
        return request

    def call(self, request, kind="group"):
        return self.service.execute(kind, self.ops.layer_group if kind == "group" else self.ops.layer_add,
                                    **copy.deepcopy(request))

    def assert_no_nodes(self):
        self.assertEqual(self.native.doc.created, [])
        self.assertEqual(self.native.doc.root.children, [])

    def test_target_uses_native_equality_with_fresh_wrappers(self):
        self.assertIsNot(self.app.activeDocument(), self.app.activeDocument())
        self.assertEqual(self.service.target()["expected_target"], self.target)
        result = self.call(self.request())
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["created_objects"], [{"id": result["result"]["id"], "attached": "verified"}])
        self.assertEqual(result["phase"], "completed")

    def test_identical_retry_is_historical_even_after_created_node_deleted(self):
        request = self.request()
        first = self.call(request)
        self.native.doc.root.children.clear()
        second = self.call(request)
        self.assertEqual(first, second)
        self.assertEqual(len(self.native.doc.created), 1)
        self.assertEqual(self.native.doc.root.children, [])

    def test_same_id_different_content_or_kind_refused(self):
        request = self.request()
        self.call(request)
        for changed in ({**request, "name": "other"},
                        {**request, "expected_bindings": [{"id": "x", "token": str(uuid.uuid4())}]}):
            with self.assertRaisesRegex(guard.GuardError, "内容不同"):
                self.call(changed)
        with self.assertRaisesRegex(guard.GuardError, "内容不同"):
            self.call({**request, "png_path": str(self.png), "source_sha256": self.digest}, "add")
        self.assertEqual(len(self.native.doc.created), 1)

    def test_switch_and_resize_and_rename_reject_before_mutation(self):
        cases = (lambda: setattr(self.app, "current", Native("/tmp/other.kra")),
                 lambda: setattr(self.native, "width", 32),
                 lambda: setattr(self.native, "filename", "/tmp/renamed.kra"))
        for change in cases:
            with self.subTest(change=change):
                self.app.current, self.native.width, self.native.filename = self.native, 16, "/tmp/专用测试.kra"
                change()
                result = self.call(self.request())
                self.assertEqual(result["status"], "rejected")
                self.assertFalse(result["mutation_started"])
                self.assert_no_nodes()

    def test_reopen_same_root_without_any_notifier_signal_is_rejected(self):
        old_root = self.native.doc.root.uid
        self.native.closed = True
        replacement = Native(self.native.filename)
        replacement.doc.root.uid = old_root
        self.app.opened, self.app.current = [replacement], replacement
        result = self.call(self.request())
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(replacement.doc.created, [])
        new_target = self.service.target()["expected_target"]
        self.assertNotEqual(new_target["document_handle"], self.target["document_handle"])
        self.assertEqual(new_target["root"], self.target["root"])

    def test_no_active_closed_or_ambiguous_documents_refused(self):
        duplicate = Native(self.native.filename)
        self.app.opened.append(duplicate)
        with self.assertRaises(guard.GuardError):
            self.service.target()
        duplicate.filename = "/tmp/other.kra"
        duplicate.doc.root.uid = self.native.doc.root.uid
        with self.assertRaises(guard.GuardError):
            self.service.target()
        self.app.opened = [self.native, self.native]
        with self.assertRaises(guard.GuardError):
            self.service.target()
        self.app.current = None
        with self.assertRaises(guard.GuardError):
            self.service.target()
        self.app.current = self.native
        self.native.closed = True
        with self.assertRaises(guard.GuardError):
            self.service.target()
        self.assert_no_nodes()

    def test_native_equality_must_be_strict_bool_not_notimplemented(self):
        class InvalidEquality(Wrapper):
            def __eq__(self, other):
                return NotImplemented
        self.app.wrapper_type = InvalidEquality
        with self.assertRaisesRegex(guard.GuardError, "bool"):
            self.service.target()
        self.assert_no_nodes()

    def test_group_binding_marker_loss_conflict_and_user_move(self):
        token = str(uuid.uuid4())
        first = self.call(self.request(name="group [cwb:" + token + "]"))
        node = self.native.doc.root.children[0]
        binding = [{"id": node.uid, "token": token}]
        request = self.request(parent_id=node.uid, expected_bindings=binding)
        duplicate = Node(node.label, "grouplayer")
        self.native.doc.root.children.append(duplicate)
        result = self.call(request)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(len(self.native.doc.created), 1)
        duplicate.label = "user group"
        node.label = "renamed without marker"
        self.native.doc.root.children.remove(node)
        duplicate.children.append(node)
        result = self.call(self.request(parent_id=node.uid, expected_bindings=binding))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["parent_id"], node.uid)
        self.assertEqual(node.label, "renamed without marker")
        node.uid = "{" + str(uuid.uuid4()) + "}"
        result = self.call(self.request(expected_bindings=binding))
        self.assertEqual(result["status"], "rejected")

    def test_attach_then_verify_failure_keeps_created_id_and_never_retries(self):
        self.native.doc.root.attach_mode = "detached"
        request = self.request()
        result = self.call(request)
        self.assertEqual(result["status"], "partial_or_unknown")
        self.assertEqual(result["created_objects"][0]["id"], self.native.doc.created[0].uid)
        self.assertEqual(result["created_objects"][0]["attached"], "unknown")
        self.assertEqual(self.call(request), result)
        self.assertEqual(len(self.native.doc.created), 1)

    def test_attach_side_effect_followed_by_exception_is_partial(self):
        def broken(node, above):
            self.native.doc.root.children.append(node)
            raise RuntimeError("API raised after attaching")
        self.native.doc.root.addChildNode = broken
        request = self.request()
        result = self.call(request)
        self.assertEqual(result["status"], "partial_or_unknown")
        self.assertEqual(len(self.native.doc.root.children), 1)
        self.assertEqual(self.call(request), result)
        self.assertEqual(len(self.native.doc.created), 1)

    def test_refresh_failure_keeps_verified_attached_object(self):
        self.native.doc.refreshProjection = Mock(side_effect=RuntimeError("refresh failed"))
        request = self.request()
        result = self.call(request)
        self.assertEqual(result["status"], "partial_or_unknown")
        self.assertEqual(result["created_objects"][0]["attached"], "verified")
        self.assertEqual(self.call(request), result)
        self.assertEqual(len(self.native.doc.created), 1)

    def test_switch_inside_create_never_attaches_to_either_document(self):
        other = Native("/tmp/other.kra")
        self.app.opened.append(other)
        def factory(name, kind):
            self.app.current = other
            return Node(name, kind)
        self.native.doc.factory = factory
        result = self.call(self.request())
        self.assertEqual(result["status"], "partial_or_unknown")
        self.assertEqual(self.native.doc.root.children, [])
        self.assertEqual(other.doc.root.children, [])
        self.assertEqual(len(result["created_objects"]), 1)

    def test_png_same_snapshot_digest_and_successful_retry(self):
        request = self.request("add")
        result = self.call(request, "add")
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["result"]["placement"]["source_sha256"], self.digest)
        self.assertEqual(self.native.doc.root.children[0].pixels[0], b"cbadgfeh")
        self.png.write_bytes(b"changed since success")
        self.assertEqual(self.call(request, "add"), result)
        self.assertEqual(len(self.native.doc.created), 1)

    def test_png_source_change_before_snapshot_rejected_before_decode(self):
        request = self.request("add", source_sha256="0" * 64)
        with patch.object(self.ops.QImage, "fromData", side_effect=AssertionError("must not decode")):
            result = self.call(request, "add")
        self.assertEqual(result["status"], "rejected")
        self.assertIn("摘要", result["error"])
        self.assert_no_nodes()

    def test_png_path_change_after_snapshot_does_not_change_decoded_bytes(self):
        original = self.ops.QImage.fromData
        def replace_after_read(encoded, fmt):
            self.png.write_bytes(b"new path content")
            return original(encoded, fmt)
        with patch.object(self.ops.QImage, "fromData", side_effect=replace_after_read):
            result = self.call(self.request("add"), "add")
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["source_sha256"], self.digest)
        self.assertNotEqual(hashlib.sha256(self.png.read_bytes()).hexdigest(), self.digest)

    def test_pixel_failure_has_object_receipt_and_retry_does_not_create(self):
        def factory(name, kind):
            node = Node(name, kind)
            node.pixel_ok = False
            return node
        self.native.doc.factory = factory
        request = self.request("add")
        result = self.call(request, "add")
        self.assertEqual(result["status"], "partial_or_unknown")
        self.assertEqual(result["phase"], "set_pixels")
        self.assertEqual(result["created_objects"][0]["attached"], "verified")
        self.assertEqual(self.call(request, "add"), result)
        self.assertEqual(len(self.native.doc.created), 1)

    def test_target_switch_after_attach_blocks_pixel_write(self):
        other = Native("/tmp/other.kra")
        self.app.opened.append(other)
        attach = self.native.doc.root.addChildNode
        def switched(node, above):
            result = attach(node, above)
            self.app.current = other
            return result
        self.native.doc.root.addChildNode = switched
        result = self.call(self.request("add"), "add")
        self.assertEqual(result["status"], "partial_or_unknown")
        self.assertIsNone(self.native.doc.created[0].pixels)
        self.assertEqual(other.doc.created, [])

    def test_guard_checks_do_not_bypass_import_time_budget(self):
        placement = __import__(PKG + ".placement", fromlist=["*"])
        for expire_at, expected_nodes in ((2, 0), (4, 1)):
            with self.subTest(expire_at=expire_at):
                self.native.doc.created.clear()
                self.native.doc.root.children.clear()
                clock, checks = [0.0], [0]
                budget = placement.PreprocessBudget(clock=lambda: clock[0])
                original = self.service._check
                def guarded(*args):
                    result = original(*args)
                    checks[0] += 1
                    if checks[0] == expire_at:
                        clock[0] = 6.0
                    return result
                with patch.object(placement, "PreprocessBudget", return_value=budget), \
                        patch.object(self.service, "_check", side_effect=guarded):
                    result = self.call(self.request("add"), "add")
                self.assertEqual(result["status"], "partial_or_unknown")
                self.assertIn("预算", result["error"])
                self.assertEqual(len(self.native.doc.created), expected_nodes)
                if expected_nodes:
                    self.assertIsNone(self.native.doc.created[0].pixels)

    def test_receipt_absence_and_restart_mean_unknown_not_safe_to_retry(self):
        request = self.request()
        self.assertEqual(self.service.receipt(request["operation_id"], self.service.service_id)["status"], "unknown")
        self.call(request)
        other = guard.WriteService(self.app)
        unknown = other.receipt(request["operation_id"], self.service.service_id)
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(unknown["reason"], "service_changed")
        with self.assertRaisesRegex(guard.GuardError, "未知"):
            other.execute("group", self.ops.layer_group, **request)
        self.assertEqual(other.receipts, {})
        self.assertEqual(len(self.native.doc.created), 1)

    def test_receipt_and_target_capacity_fail_closed_without_eviction(self):
        first = self.request()
        self.call(first)
        with patch.object(guard, "MAX_RECEIPTS", 1):
            with self.assertRaisesRegex(guard.GuardError, "预算耗尽"):
                self.call(self.request())
            self.assertEqual(self.call(first)["status"], "succeeded")
        self.app.current = Native("/tmp/other.kra")
        self.app.opened.append(self.app.current)
        with patch.object(guard, "MAX_TARGETS", 1):
            with self.assertRaisesRegex(guard.GuardError, "预算"):
                self.service.target()
        self.assertEqual(len(self.service.targets), 1)
        self.assertEqual(len(self.service.receipts), 1)

    def test_request_and_binding_and_tree_budgets_fail_before_write(self):
        with self.assertRaises(guard.GuardError):
            self.call(self.request(name="x" * 65536))
        with self.assertRaisesRegex(guard.GuardError, "字节预算"):
            self.call(self.request(expected_bindings=[{"id": "x" * 1000, "token": "y" * 1000}] * 64))
        self.assertEqual(self.service.receipts, {})
        result = self.call(self.request(expected_bindings=[{}] * 129))
        self.assertEqual(result["status"], "rejected")
        with patch.object(guard, "MAX_NODES", 1):
            self.native.doc.root.children.append(Node("other", "paintlayer"))
            with self.assertRaisesRegex(guard.GuardError, "预算"):
                self.service.target()
        self.assertEqual(self.native.doc.created, [])
        for _, encoded in self.service.receipts.values():
            self.assertLessEqual(len(encoded), guard.MAX_RECEIPT_BYTES)
            self.assertNotIn("expected_bindings", json.loads(encoded))
            self.assertNotIn("png_path", json.loads(encoded))

    def test_reentrant_same_operation_gets_running_receipt_not_second_write(self):
        request = self.request()
        observed = []
        def factory(name, kind):
            observed.append(self.call(request))
            return Node(name, kind)
        self.native.doc.factory = factory
        result = self.call(request)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(observed[0]["status"], "running")
        self.assertTrue(observed[0]["mutation_started"])
        self.assertEqual(len(self.native.doc.created), 1)

    def test_queued_timeout_skips_callback_and_receipt_remains_unknown(self):
        bridge, queued = self.fake_bridge()
        request = self.request()
        with self.assertRaises(TimeoutError):
            bridge.call(lambda: self.call(request), timeout=0)
        fn, box = queued.pop()
        bridge._runner._on_run(fn, box)
        self.assert_no_nodes()
        self.assertEqual(self.service.receipt(request["operation_id"], self.service.service_id)["status"], "unknown")

    def test_timeout_after_callback_started_can_write_receipt_prevents_double_create(self):
        bridge, queued = self.fake_bridge()
        request = self.request()
        entered, release = threading.Event(), threading.Event()
        def factory(name, kind):
            entered.set()
            release.wait(2)
            return Node(name, kind)
        self.native.doc.factory = factory
        errors = []
        def caller():
            try:
                bridge.call(lambda: self.call(request), timeout=0.03)
            except TimeoutError as exc:
                errors.append(exc)
        thread = threading.Thread(target=caller)
        thread.start()
        for _ in range(1000):
            if queued:
                break
            threading.Event().wait(0.001)
        fn, box = queued.pop()
        runner = threading.Thread(target=bridge._runner._on_run, args=(fn, box))
        runner.start()
        self.assertTrue(entered.wait(1))
        thread.join(1)
        self.assertEqual(len(errors), 1)
        self.assertTrue(box["cancelled"])
        release.set()
        runner.join(2)
        self.assertFalse(runner.is_alive())
        result = self.service.receipt(request["operation_id"], self.service.service_id)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.call(request), result)
        self.assertEqual(len(self.native.doc.created), 1)

    def fake_bridge(self):
        queued = []
        class Signal:
            def connect(self, fn):
                pass
            def emit(self, *args):
                queued.append(args)
        qtcore = types.ModuleType("PyQt6.QtCore")
        qtcore.QObject = object
        qtcore.pyqtSignal = lambda *args: Signal()
        with patch.dict(sys.modules, {"PyQt6.QtCore": qtcore}):
            module = groups.load_module(PKG + ".bridge_fixture", ROOT / "core/krita_cowork/bridge_qt.py")
        return module.MainThreadBridge(), queued


class Routes(unittest.TestCase):
    request = GuardTests.request
    call = GuardTests.call
    assert_no_nodes = GuardTests.assert_no_nodes

    def setUp(self):
        GuardTests.setUp(self)
        bridge_module = types.ModuleType(PKG + ".bridge_qt")
        bridge_module.MainThreadBridge = Mock
        with patch.dict(sys.modules, {PKG + ".ops": self.ops, PKG + ".bridge_qt": bridge_module}):
            self.server = groups.load_module(PKG + ".server", ROOT / "core/krita_cowork/server.py")
        self.control = self.server.ControlServer(0)
        self.control.bridge.call.side_effect = lambda fn: fn()
        class FakeHTTP:
            def __init__(self, address, handler):
                self.handler = handler
            def serve_forever(self, **kwargs):
                pass
        with patch.object(self.server, "ThreadingHTTPServer", FakeHTTP), patch("threading.Thread"):
            self.control.start()

    def http(self, path, payload=None, length=None):
        handler = self.control._httpd.handler.__new__(self.control._httpd.handler)
        handler.path = path
        raw = json.dumps(payload).encode() if payload is not None else b""
        handler.headers = {"Content-Length": str(len(raw) if length is None else length)}
        handler.rfile = io.BytesIO(raw)
        handler.close_connection = False
        handler._reply = Mock()
        handler._dispatch("POST" if payload is not None else "GET")
        return handler._reply.call_args.args

    def test_versioned_routes_aliases_and_real_ops_under_bridge(self):
        code, snap = self.http("/write/v1/target")
        self.assertEqual(code, 200)
        req = self.request(expected_target=snap["expected_target"])
        code, result = self.http("/write/v1/group", req)
        self.assertEqual((code, result["status"]), (200, "succeeded"))
        code, receipt = self.http("/write/v1/receipt", {"operation_id": req["operation_id"],
                                                       "service_id": self.service.service_id})
        self.assertEqual(code, 200)
        self.assertEqual(receipt, result)
        self.assertGreaterEqual(self.control.bridge.call.call_count, 3)
        req = self.request("add", parent_id=result["result"]["id"])
        code, added = self.http("/write/v1/add", req)
        self.assertEqual((code, added["status"]), (200, "succeeded"))
        self.assertEqual(added["result"]["parent_id"], result["result"]["id"])

    def test_invalid_unicode_request_fields_are_400_without_receipts_or_creation(self):
        for invalid in ("\ud800", "\udfff"):
            for kind, field in (("group", "name"), ("group", "parent_id"),
                                ("add", "name"), ("add", "png_path")):
                with self.subTest(kind=kind, field=field, invalid=repr(invalid)):
                    code, response = self.http("/write/v1/" + kind,
                                               self.request(kind, **{field: invalid}))
                    self.assertEqual(code, 400)
                    self.assertIn("error", response)
                    self.assertNotIn("where", response)
                    self.assert_no_nodes()
                    self.assertEqual(self.service.receipts, {})

    def test_invalid_unicode_native_labels_are_400_without_receipts_or_creation(self):
        for owner, field in ((self.native, "title"), (self.native.doc.root, "label")):
            original = getattr(owner, field)
            for invalid in ("\ud800", "\udfff"):
                with self.subTest(field=field, invalid=repr(invalid)):
                    setattr(owner, field, invalid)
                    try:
                        code, response = self.http("/write/v1/target")
                        self.assertEqual(code, 400)
                        self.assertIn("error", response)
                        self.assertNotIn("where", response)
                        self.assert_no_nodes()
                        self.assertEqual(self.service.receipts, {})
                    finally:
                        setattr(owner, field, original)

    def test_unknown_fields_missing_guards_body_limit_before_bridge(self):
        for req in ({"name": "bare"}, self.request(extra="x"), self.request(_write_context={})):
            before = self.control.bridge.call.call_count
            code, _ = self.http("/write/v1/group", req)
            self.assertEqual(code, 400)
            self.assertEqual(self.control.bridge.call.call_count, before)
        for length in (-1, 0, 65537):
            before = self.control.bridge.call.call_count
            code, _ = self.http("/write/v1/group", {}, length)
            self.assertEqual(code, 400)
            self.assertEqual(self.control.bridge.call.call_count, before)
        self.assert_no_nodes()


class StageGuardTests(unittest.TestCase):
    setUp = stage_tests.StageTests.setUp
    setup_stage = stage_tests.StageTests.setup_stage
    data = stage_tests.StageTests.data

    def test_legacy_service_cannot_silently_discard_guard_fields(self):
        original = self.fake.request
        def old_server(path, payload=None):
            if path.startswith("/write/v1/"):
                raise stage.StageError("未知路径：旧服务")
            return original(path, payload)
        with self.assertRaisesRegex(stage.StageError, "旧服务"):
            stage.execute(4, self.document, self.state, old_server)
        self.assertEqual(self.fake.posts, 0)
        self.assertFalse(self.state.exists())
        self.assertFalse(any(path in ("/layer/add", "/layer/group") for path, _ in self.fake.calls))

    def test_index_failure_retains_exact_guarded_request_and_queries_receipt(self):
        original = stage.Store.save
        def failing(store, data):
            if data["pending"] and "response" in data["pending"]:
                raise OSError("index full")
            return original(store, data)
        with patch.object(stage.Store, "save", failing), self.assertRaisesRegex(stage.StageError, "记录写入失败"):
            self.setup_stage()
        pending = self.data()["pending"]
        self.assertIn("expected_target", pending["request"])
        self.assertEqual(pending["operation_id"], pending["request"]["operation_id"])
        self.assertTrue(any(path == "/write/v1/receipt" for path, _ in self.fake.calls))
        receipt = self.fake.receipts[pending["operation_id"]]
        self.assertEqual(receipt["status"], "succeeded")
        with self.assertRaisesRegex(stage.StageError, "未确认"):
            self.setup_stage()
        self.assertEqual(self.fake.posts, 1)

    def test_second_object_failure_preserves_first_and_no_blind_resubmit(self):
        self.fake.fail_at, self.fake.mode = 2, "after"
        with self.assertRaises(stage.StageError):
            self.setup_stage()
        first = copy.deepcopy(self.fake.root["children"][0])
        data = self.data()
        self.assertEqual(data["groups"]["4"]["status"], "verified")
        self.assertEqual(data["pending"]["key"], "4/剪影")
        self.assertEqual(data["pending"]["last_receipt"]["status"], "unknown")
        with self.assertRaises(stage.StageError):
            self.setup_stage()
        self.assertEqual(self.fake.posts, 2)
        self.assertEqual(self.fake.root["children"][0], first)

    def test_source_digest_is_bounded_and_regular_before_any_http(self):
        large = self.root / "large.png"
        with large.open("wb") as stream:
            stream.truncate(stage.MAX_SOURCE_BYTES + 1)
        with self.assertRaisesRegex(stage.StageError, "32MiB"):
            self.setup_stage(6, diagnostic=large)
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
