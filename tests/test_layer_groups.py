"""离线替身测试: 不证明真实 Krita/Qt 图层树或 .kra 行为。"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
import cwb


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Image:
    Format = types.SimpleNamespace(Format_RGBA8888=1)

    def __init__(self, path):
        self.path = path

    @classmethod
    def fromData(cls, encoded, fmt):
        assert fmt == 'PNG'
        return cls('input.png')

    def isNull(self):
        return self.path.endswith('bad.png')

    def convertToFormat(self, fmt):
        return self

    def width(self):
        return 1

    def height(self):
        return 2

    def bytesPerLine(self):
        return 8

    def constBits(self):
        # 每行四字节像素加四字节 padding。
        return types.SimpleNamespace(asarray=lambda size: b'abcdPAD!efghPAD!')


qtgui = types.ModuleType('PyQt6.QtGui')
qtgui.QImage = Image
krita = types.ModuleType('krita')
krita.Krita = Mock()
with patch.dict(sys.modules, {'PyQt6': types.ModuleType('PyQt6'),
                              'PyQt6.QtGui': qtgui, 'krita': krita}):
    ops = load_module('_layer_groups_package.ops', ROOT / 'core/krita_cowork/ops.py')


class Node:
    serial = 0

    def __init__(self, name, kind):
        Node.serial += 1
        self.uid = '{node-%s}' % Node.serial
        self.label = name
        self.kind = kind
        self.children = []
        self.attach_ok = True
        self.attach_mode = 'normal'
        self.pixel_ok = True
        self.pixels = None

    def uniqueId(self):
        return types.SimpleNamespace(toString=lambda: self.uid)

    def type(self):
        return self.kind

    def name(self):
        return self.label

    def childNodes(self):
        return list(self.children)

    def colorModel(self):
        return 'RGBA'

    def colorDepth(self):
        return 'U8'

    def addChildNode(self, node, above):
        if not self.attach_ok:
            return False
        if self.attach_mode == 'detached':
            return True
        if self.attach_mode == 'wrong_order':
            self.children.insert(0, node)
        else:
            index = self.children.index(above) + 1 if above else len(self.children)
            self.children.insert(index, node)
        return True

    def setPixelData(self, *args):
        self.pixels = args
        return self.pixel_ok


class Doc:
    def __init__(self):
        self.root = Node('root', 'grouplayer')
        self.created = []
        self.factory = Node
        self.refreshes = 0

    def rootNode(self):
        return self.root

    def createNode(self, name, kind):
        node = self.factory(name, kind.lower())
        self.created.append(node)
        return node

    def width(self):
        return 16

    def height(self):
        return 16

    def refreshProjection(self):
        self.refreshes += 1


class Operations(unittest.TestCase):
    def setUp(self):
        package = types.ModuleType('_layer_groups_package')
        package.__path__ = [str(ROOT / 'core/krita_cowork')]
        modules = patch.dict(sys.modules, {'_layer_groups_package': package})
        modules.start()
        self.addCleanup(modules.stop)
        self.doc = Doc()
        self.active = patch.object(ops, '_active_doc', return_value=self.doc)
        self.active.start()
        self.addCleanup(self.active.stop)
        # layer_groups tests use a nonexistent input.png and a Qt image double. Keep
        # that one virtual path local to these tests; all real paths still run
        # production preflight (placement separately tests malformed/resource PNGs).
        placement = importlib.import_module('_layer_groups_package.placement')
        real_prepare = placement.prepare_png
        def prepare(path, *args, **kwargs):
            if path == os.path.abspath('input.png'):
                kwargs.pop('budget', None)
                return b'virtual PNG fixture', placement.plan_placement(1, 2, *args, **kwargs)
            return real_prepare(path, *args, **kwargs)
        self.png_fixture = patch.object(placement, 'prepare_png', side_effect=prepare)
        self.png_fixture.start()
        self.addCleanup(self.png_fixture.stop)

    def group(self, name='组', **kwargs):
        return ops.layer_group(name, **kwargs)

    def test_two_nested_groups_png_and_sibling_order(self):
        outer = self.group('剪影')
        inner = self.group('结构', parent_id=outer['id'])
        paint = ops.layer_add('input.png', '明暗', parent_id=inner['id'])
        second = self.group('配色', parent_id=inner['id'], above_layer_id=paint['id'])
        outer_node = self.doc.root.children[0]
        inner_node = outer_node.children[0]
        self.assertEqual(outer_node.name(), '剪影')
        self.assertEqual(inner_node.name(), '结构')
        self.assertEqual([n.uid for n in inner_node.children], [paint['id'], second['id']])
        # setPixelData receives BGRA: swap_rb exchanges bytes 0<->2 ('abcd'->'cbad').
        self.assertEqual(inner_node.children[0].pixels, (b'cbadgfeh', 0, 0, 1, 2))
        self.assertEqual(paint['parent_id'], inner['id'])
        self.assertEqual(self.doc.refreshes, 4)

    def test_add_positional_arguments_and_default_root(self):
        result = ops.layer_add('input.png', 'paint', None, 2, 3)
        self.assertEqual(result['parent_id'], self.doc.root.uid)
        self.assertEqual(self.doc.root.children[0].pixels[1:], (2, 3, 1, 2))

    def test_parent_uses_returned_layer_id(self):
        group = self.group()
        self.group(parent_id=group['id'])
        self.assertEqual(len(self.doc.root.children[0].children), 1)

    def test_invalid_parent_and_above_for_both_operations(self):
        group = self.group()
        paint = ops.layer_add('input.png', 'paint')
        child = self.group(parent_id=group['id'])
        other = Node('mask', 'transparencymask')
        self.doc.root.children.append(other)
        cases = [({'parent_id': self.doc.root.uid}, ops.OpError),
                 ({'parent_id': paint['id']}, ops.OpError),
                 ({'parent_id': other.uid}, ops.OpError),
                 ({'parent_id': ''}, ops.NotFoundError),
                 ({'parent_id': 'stale'}, ops.NotFoundError),
                 ({'above_layer_id': 'stale'}, ops.NotFoundError),
                 ({'above_layer_id': self.doc.root.uid}, ops.OpError),
                 ({'above_layer_id': other.uid}, ops.OpError),
                 ({'above_layer_id': child['id']}, ops.OpError),
                 ({'parent_id': group['id'], 'above_layer_id': paint['id']}, ops.OpError),
                 ({'parent_id': group['id'], 'above_layer_id': group['id']}, ops.OpError)]
        for fn in (self.group, lambda **kw: ops.layer_add('input.png', 'p', **kw)):
            for kwargs, error in cases:
                with self.subTest(fn=fn, kwargs=kwargs):
                    count = len(self.doc.created)
                    with self.assertRaises(error):
                        fn(**kwargs)
                    self.assertEqual(len(self.doc.created), count)

    def test_rgba8_guard_before_creation(self):
        for method, bad in (('colorModel', 'CMYKA'), ('colorDepth', 'U16')):
            with patch.object(self.doc.root, method, return_value=bad):
                for fn in (self.group, lambda: ops.layer_add('input.png', 'p')):
                    with self.assertRaises(ops.OpError):
                        fn()
        self.assertEqual(self.doc.created, [])

    def test_png_and_offset_guards(self):
        for path, kw in [('bad.png', {}), ('ok.png', {'offset_x': -1}),
                         ('ok.png', {'offset_y': 15}), ('ok.png', {'offset_x': 'bad'})]:
            with self.subTest(path=path, kw=kw), self.assertRaises(ops.OpError):
                ops.layer_add(path, 'p', **kw)
        self.assertEqual(self.doc.created, [])

    def test_create_and_attach_failures_never_write_pixels(self):
        for factory in (lambda *a: None, lambda *a: Node('wrong', 'vectorlayer')):
            self.doc.factory = factory
            with self.assertRaises(ops.OpError):
                self.group()
        self.doc.factory = Node
        for mode in ('false', 'detached', 'wrong_order'):
            for paint in (False, True):
                self.doc.root.children = [Node('anchor', 'paintlayer')]
                self.doc.root.attach_ok = mode != 'false'
                self.doc.root.attach_mode = mode
                with self.subTest(mode=mode, paint=paint), self.assertRaises(ops.OpError):
                    kwargs = {'above_layer_id': self.doc.root.children[0].uid}
                    if paint:
                        ops.layer_add('input.png', 'p', **kwargs)
                    else:
                        self.group(**kwargs)
                self.assertIsNone(self.doc.created[-1].pixels)

    def test_wrong_parent_readback(self):
        outer = self.group()
        parent = self.doc.root.children[0]
        def misplaced(node, above):
            self.doc.root.children.append(node)
            return True
        with patch.object(parent, 'addChildNode', side_effect=misplaced):
            with self.assertRaisesRegex(ops.OpError, '指定父级'):
                self.group(parent_id=outer['id'])

    def test_tree_is_read_again_after_pixel_write(self):
        def factory(name, kind):
            node = Node(name, kind)
            def detach_after_write(*args):
                self.doc.root.children.remove(node)
                return True
            node.setPixelData = detach_after_write
            return node
        self.doc.factory = factory
        with self.assertRaisesRegex(ops.OpError, '创建后树结构异常'):
            ops.layer_add('input.png', 'p')
        self.assertEqual(self.doc.refreshes, 0)

    def test_pixel_failure_is_reported_without_deleting_existing_layers(self):
        def factory(name, kind):
            node = Node(name, kind)
            node.pixel_ok = False
            return node
        self.doc.factory = factory
        with self.assertRaisesRegex(ops.OpError, 'setPixelData'):
            ops.layer_add('input.png', 'failed')
        self.assertEqual(len(self.doc.root.children), 1)
        self.assertEqual(self.doc.refreshes, 0)


class Client(unittest.TestCase):
    def test_group_and_add_payloads(self):
        for args, route, expected in [
            (['group', '剪影', '--parent', 'g', '--above', 'a'], '/layer/group',
             {'name': '剪影', 'parent_id': 'g', 'above': 'a'}),
            (['group', '结构'], '/layer/group', {'name': '结构', 'parent_id': None, 'above': None}),
            (['add', 'in.png', '明暗', '--parent', 'g', '--above', 'a', '--x', '2'], '/layer/add',
             {'png_path': os.path.abspath('in.png'), 'name': '明暗', 'parent_id': 'g',
              'above': 'a', 'offset_x': 2, 'offset_y': 0})]:
            with patch.object(cwb, 'http', return_value={}) as http, contextlib.redirect_stdout(io.StringIO()):
                cwb.main(args)
                self.assertEqual(http.call_args.args, (cwb.Krita + route, expected))

    def test_help_has_no_http_or_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory(prefix='layer-groups-help-') as tmp:
            target = Path(tmp) / 'absent'
            for args in (['--help'], ['group', '--help'], ['add', '--help']):
                with patch.object(cwb, 'http', side_effect=AssertionError('network')):
                    with patch.object(os, 'makedirs', side_effect=AssertionError('mkdir')):
                        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exc:
                            cwb.main(args)
                        self.assertEqual(exc.exception.code, 0)
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'core/cwb.py'), *args],
                                        cwd=tmp, env={**os.environ, 'CWB_WF_DIR': str(target)},
                                        capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(target.exists())
                self.assertEqual(list(Path(tmp).iterdir()), [])


class Routing(unittest.TestCase):
    def setUp(self):
        package = types.ModuleType('_layer_groups_package')
        package.__path__ = []
        bridge_module = types.ModuleType('_layer_groups_package.bridge_qt')
        bridge_module.MainThreadBridge = Mock
        with patch.dict(sys.modules, {'_layer_groups_package': package, '_layer_groups_package.ops': ops,
                                      '_layer_groups_package.bridge_qt': bridge_module}):
            self.server = load_module('_layer_groups_package.server', ROOT / 'core/krita_cowork/server.py')
        self.control = self.server.ControlServer(0)
        self.control.bridge.call.side_effect = lambda fn: fn()
        # 捕获真实 Handler,不打开监听 socket,也不运行后台线程。
        class FakeHTTP:
            def __init__(self, address, handler):
                self.handler = handler

            def serve_forever(self, **kwargs):
                pass
        with patch.object(self.server, 'ThreadingHTTPServer', FakeHTTP), patch('threading.Thread'):
            self.control.start()
        self.handler_class = self.control._httpd.handler

    def request(self, path, body):
        handler = self.handler_class.__new__(self.handler_class)
        handler.path = path
        raw = json.dumps(body).encode()
        handler.headers = {'Content-Length': str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.close_connection = False
        handler._reply = Mock()
        handler._dispatch('POST')
        return handler._reply.call_args.args

    def test_routes_aliases_and_bridge(self):
        for path, body, name, expected in [
            ('/layer/group', {'name': '组', 'parent_id': 'g', 'above': 'a'}, 'layer_group',
             {'name': '组', 'parent_id': 'g', 'above_layer_id': 'a'}),
            ('/layer/add', {'name': 'p', 'png_path': 'x', 'parent_id': 'g'}, 'layer_add',
             {'name': 'p', 'png_path': 'x', 'parent_id': 'g'})]:
            fn = Mock(return_value={'id': 'new'})
            old = self.server.POST_ROUTES[path]
            with patch.dict(self.server.POST_ROUTES, {path: (fn, old[1], old[2])}):
                self.assertEqual(self.request(path, body), (200, {'id': 'new'}))
            fn.assert_called_once_with(**expected)
        self.assertEqual(self.control.bridge.call.call_count, 2)

    def test_missing_parameter_does_not_reach_bridge(self):
        status, body = self.request('/layer/group', {'parent_id': 'g'})
        self.assertEqual(status, 400)
        self.assertIn('name', body['error'])
        self.control.bridge.call.assert_not_called()

    def test_operation_errors_map_to_status(self):
        for error, status in ((ops.OpError('invalid parent'), 400),
                              (ops.NotFoundError('stale id'), 404), (TimeoutError('timeout'), 503)):
            self.control.bridge.call.side_effect = error
            self.assertEqual(self.request('/layer/group', {'name': 'g'})[0], status)


if __name__ == '__main__':
    unittest.main()
