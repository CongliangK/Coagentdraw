"""CAD-12 offline contract tests. Fakes are NOT Krita/Qt visual evidence."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import test_layer_groups as groups

ops = groups.ops
cwb = groups.cwb


class Node(groups.Node):
    def __init__(self, name='paint', kind='paintlayer'):
        super().__init__(name, kind)
        self.alpha = False
        self.amount = 255
        self.mode = 'normal'
        self.writes = []

    def alphaLocked(self):
        return self.alpha

    def setAlphaLocked(self, value):
        self.writes.append(('alpha', value))
        self.alpha = value

    def opacity(self):
        return self.amount

    def setOpacity(self, value):
        self.writes.append(('opacity', value))
        self.amount = value

    def blendingMode(self):
        return self.mode

    def setBlendingMode(self, value):
        self.writes.append(('blend', value))
        self.mode = value

    def bounds(self):
        return types.SimpleNamespace(x=lambda: 0, y=lambda: 0, width=lambda: 1, height=lambda: 1)

    def visible(self):
        return True


class Operations(unittest.TestCase):
    def setUp(self):
        self.doc = groups.Doc()
        self.doc.root = Node('root', 'grouplayer')
        self.doc.name = lambda: 'test'
        self.node = Node()
        self.doc.root.children.append(self.node)
        active = patch.object(ops, '_active_doc', return_value=self.doc)
        active.start()
        self.addCleanup(active.stop)

    def props(self, **kwargs):
        return ops.layer_props(self.node.uid, **kwargs)

    def test_opacity_endpoints_and_half_up_rounding(self):
        for value, expected in [(0, 0), (0.5, 128), (1, 255), (0.3, 77)]:
            with self.subTest(value=value):
                result = self.props(opacity=value)
                self.assertEqual(self.node.amount, expected)
                self.assertEqual(result['opacity'], round(expected / 255, 3))

    def test_reject_invalid_opacity_and_validate_all_before_writes(self):
        for value in [True, False, float('nan'), float('inf'), -float('inf'), -0.01,
                      1.01, '0.5', None, [], {}, 10**1000]:
            with self.subTest(value=value), self.assertRaises(ops.OpError):
                self.props(alpha_locked=True, opacity=value)
            self.assertEqual(self.node.writes, [])
        for kwargs in [{}, {'alpha_locked': 1}, {'alpha_locked': None},
                       {'alpha_locked': 'true'}, {'blending_mode': None},
                       {'opacity': .5, 'blending_mode': '普通'}, {'blending_mode': []}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ops.OpError):
                self.props(**kwargs)
            self.assertEqual(self.node.writes, [])

    def test_alpha_and_all_stable_blending_ids(self):
        for mode in ('normal', 'multiply', 'overlay', 'screen'):
            result = self.props(alpha_locked=True, opacity=.5, blending_mode=mode)
            self.assertEqual(result, {'id': self.node.uid, 'alpha_locked': True,
                                      'opacity': .502, 'blending_mode': mode})
        self.assertFalse(self.props(alpha_locked=False)['alpha_locked'])

    def test_node_and_document_guards(self):
        with self.assertRaises(ops.OpError):
            ops.layer_props(self.doc.root.uid, opacity=.5)
        with self.assertRaises(ops.NotFoundError):
            ops.layer_props('stale', opacity=.5)
        for kind in ['grouplayer', 'vectorlayer', 'filterlayer', 'transparencymask']:
            self.node.kind = kind
            with self.assertRaises(ops.OpError):
                self.props(alpha_locked=True, opacity=.5)
            self.assertEqual(self.node.writes, [])
        self.node.kind = 'grouplayer'
        self.assertEqual(self.props(opacity=.5, blending_mode='multiply')['opacity'], .502)
        self.node.kind = 'paintlayer'
        for method, value in [('colorModel', 'CMYKA'), ('colorDepth', 'U16')]:
            with patch.object(self.doc.root, method, return_value=value), self.assertRaises(ops.OpError):
                self.props(opacity=.5)

    def test_preflight_missing_methods_or_failed_reads_never_write(self):
        with patch.object(self.node, 'setBlendingMode', None), self.assertRaises(ops.OpError):
            self.props(opacity=.5, blending_mode='multiply')
        with patch.object(self.node, 'blendingMode', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(ops.OpError):
                self.props(alpha_locked=True, blending_mode='multiply')
        with patch.object(self.node, 'setAlphaLocked', None), self.assertRaises(ops.OpError):
            self.props(alpha_locked=True)
        self.assertEqual(self.node.writes, [])

    def test_silent_failure_false_return_and_partial_write_reporting(self):
        for failure in [lambda value: None, lambda value: False]:
            with patch.object(self.node, 'setBlendingMode', side_effect=failure):
                with self.assertRaises(ops.PropsWriteError) as exc:
                    self.props(alpha_locked=True, opacity=.5, blending_mode='multiply')
            payload = exc.exception.payload
            self.assertEqual(payload['verified'], ['alpha_locked', 'opacity'])
            self.assertEqual(payload['attempted'], ['alpha_locked', 'opacity', 'blending_mode'])
            self.assertEqual(payload['failed'], 'blending_mode')
            self.assertFalse(payload['rolled_back'])
            self.assertTrue(self.node.alpha)
            self.assertEqual(self.node.amount, 128)

    def test_setter_can_mutate_then_throw_and_refresh_can_fail(self):
        def fail(value):
            self.node.amount = value
            raise RuntimeError('after mutation')
        with patch.object(self.node, 'setOpacity', side_effect=fail):
            with self.assertRaises(ops.PropsWriteError) as exc:
                self.props(opacity=.5, blending_mode='multiply')
        self.assertEqual(exc.exception.payload['verified'], [])
        self.assertEqual(exc.exception.payload['attempted'], ['opacity'])
        self.assertEqual(self.node.amount, 128)
        self.assertEqual(self.node.mode, 'normal')
        with patch.object(self.doc, 'refreshProjection', side_effect=RuntimeError('refresh')):
            with self.assertRaises(ops.PropsWriteError) as exc:
                self.props(alpha_locked=True)
        self.assertEqual(exc.exception.payload['failed'], 'refreshProjection')

    def test_layers_optional_getter_failures_preserve_entire_tree(self):
        peer = Node('healthy-peer')
        child = Node('healthy-child')
        self.doc.root.children.append(peer)
        self.node.children.append(child)
        cases = [('alphaLocked',), ('blendingMode',), ('alphaLocked', 'blendingMode')]
        for getters in cases:
            with self.subTest(getters=getters), contextlib.ExitStack() as stack:
                for getter in getters:
                    stack.enter_context(patch.object(self.node, getter,
                                                     side_effect=RuntimeError('read unavailable')))
                tree = ops.layers()['layers'][0]
                paint, healthy = tree['children']
                self.assertEqual([paint['id'], healthy['id']], [self.node.uid, peer.uid])
                self.assertEqual(paint['children'][0]['id'], child.uid)
                self.assertEqual(tree['name'], 'root')
                for key, value in {'id': self.node.uid, 'name': 'paint', 'type': 'paintlayer',
                                   'root': False, 'visible': True, 'opacity': 1.0, 'x': 0,
                                   'y': 0, 'width': 1, 'height': 1, 'depth': 1}.items():
                    self.assertEqual(paint[key], value)
                self.assertTrue(paint['alpha_lock_supported'])
                for getter, key, expected in [('alphaLocked', 'alpha_locked', False),
                                              ('blendingMode', 'blending_mode', 'normal')]:
                    status = paint['property_status'][key]
                    if getter in getters:
                        self.assertIsNone(paint[key])
                        self.assertEqual(status, {'status': 'error',
                                                  'error': 'RuntimeError: read unavailable'})
                    else:
                        self.assertEqual(paint[key], expected)
                        self.assertEqual(status, {'status': 'ok'})
                    for good in [tree, healthy, paint['children'][0]]:
                        self.assertNotEqual(good['property_status'][key]['status'], 'error')
                self.assertEqual(self.node.writes, [])

    def test_layers_missing_apis_and_unsupported_nodes_are_distinct_from_read_errors(self):
        for method in ['alphaLocked', 'setAlphaLocked', 'blendingMode']:
            with self.subTest(method=method), patch.object(self.node, method, None):
                paint = ops.layers()['layers'][0]['children'][0]
                key = 'blending_mode' if method == 'blendingMode' else 'alpha_locked'
                self.assertIsNone(paint[key])
                self.assertEqual(paint['property_status'][key]['status'], 'unavailable')
                self.assertIn(method, paint['property_status'][key]['error'])
                if key == 'alpha_locked':
                    self.assertFalse(paint['alpha_lock_supported'])
        for kind in ['grouplayer', 'vectorlayer', 'filterlayer', 'transparencymask']:
            self.node.kind = kind
            with self.subTest(kind=kind), patch.object(self.node, 'alphaLocked',
                                                       side_effect=AssertionError('must not read')):
                paint = ops.layers()['layers'][0]['children'][0]
                self.assertIsNone(paint['alpha_locked'])
                self.assertFalse(paint['alpha_lock_supported'])
                self.assertEqual(paint['property_status']['alpha_locked']['status'], 'unsupported')
                self.assertEqual(paint['blending_mode'], 'normal')

    def test_layers_getter_attribute_error_is_not_missing_api(self):
        for getter, key in [('alphaLocked', 'alpha_locked'), ('blendingMode', 'blending_mode')]:
            with self.subTest(getter=getter), patch.object(self.node, getter,
                                                          side_effect=AttributeError('inside getter')):
                paint = ops.layers()['layers'][0]['children'][0]
                self.assertIsNone(paint[key])
                self.assertTrue(paint['alpha_lock_supported'])
                self.assertEqual(paint['property_status'][key],
                                 {'status': 'error', 'error': 'AttributeError: inside getter'})

    def test_layers_alpha_api_probe_error_reports_unknown_capability(self):
        def unavailable(node):
            raise RuntimeError('API lookup failed')
        with patch.object(Node, 'alphaLocked', property(unavailable)):
            paint = ops.layers()['layers'][0]['children'][0]
            self.assertIsNone(paint['alpha_locked'])
            self.assertIsNone(paint['alpha_lock_supported'])
            self.assertEqual(paint['property_status']['alpha_locked'],
                             {'status': 'error', 'error': 'RuntimeError: API lookup failed'})
            self.assertEqual(paint['blending_mode'], 'normal')

    def test_layers_never_swallows_legacy_field_errors(self):
        for method in ['bounds', 'uniqueId', 'name', 'type', 'visible', 'opacity', 'childNodes']:
            with self.subTest(method=method), patch.object(self.node, method,
                                                          side_effect=RuntimeError('legacy failure')):
                with self.assertRaisesRegex(RuntimeError, 'legacy failure'):
                    ops.layers()

    def test_optional_getter_faults_still_strictly_reject_props_preflight(self):
        for getters in [('alphaLocked',), ('blendingMode',), ('alphaLocked', 'blendingMode')]:
            with self.subTest(getters=getters), contextlib.ExitStack() as stack:
                for getter in getters:
                    stack.enter_context(patch.object(self.node, getter,
                                                     side_effect=RuntimeError('read unavailable')))
                with self.assertRaisesRegex(ops.OpError, '属性预读失败'):
                    self.props(alpha_locked=True, opacity=.5, blending_mode='multiply')
                self.assertEqual(self.node.writes, [])

    def test_layers_preserves_shape_and_adds_actual_properties(self):
        self.props(alpha_locked=True, opacity=.5, blending_mode='screen')
        tree = ops.layers()['layers'][0]
        self.assertIsNone(tree['alpha_locked'])
        self.assertFalse(tree['alpha_lock_supported'])
        paint = tree['children'][0]
        self.assertEqual(paint['opacity'], .502)
        self.assertEqual(paint['blending_mode'], 'screen')
        self.assertTrue(paint['alpha_locked'])
        self.assertTrue(paint['alpha_lock_supported'])
        for key in ['id', 'name', 'type', 'root', 'visible', 'x', 'y', 'width', 'height', 'depth', 'children']:
            self.assertIn(key, paint)
        self.node.kind = 'filterlayer'
        self.node.mode = 'unknown-existing-mode'
        paint = ops.layers()['layers'][0]['children'][0]
        self.assertIsNone(paint['alpha_locked'])
        self.assertEqual(paint['blending_mode'], 'unknown-existing-mode')


class Routing(unittest.TestCase):
    setUp = groups.Routing.setUp
    request = groups.Routing.request

    def get_layers(self):
        handler = self.handler_class.__new__(self.handler_class)
        handler.path = '/layers'
        handler._reply = groups.Mock()
        handler._dispatch('GET')
        return handler._reply.call_args.args

    def test_get_layers_survives_optional_fault_on_unrelated_node(self):
        doc = groups.Doc()
        doc.root = Node('root', 'grouplayer')
        doc.name = lambda: 'test'
        target, broken = Node('target'), Node('unrelated')
        doc.root.children = [target, broken]
        with patch.object(ops, '_active_doc', return_value=doc):
            with patch.object(broken, 'blendingMode', side_effect=RuntimeError('unrelated read')):
                self.assertEqual(self.request('/layer/props', {'id': target.uid, 'opacity': .5})[0], 200)
                status, body = self.get_layers()
                self.assertEqual(status, 200)
                healthy, failed = body['layers'][0]['children']
                self.assertEqual(healthy['opacity'], .502)
                self.assertIsNone(failed['blending_mode'])
                self.assertEqual(failed['property_status']['blending_mode']['status'], 'error')
            with patch.object(broken, 'opacity', side_effect=RuntimeError('legacy failure')):
                status, body = self.get_layers()
                self.assertEqual(status, 500)
                self.assertIn('legacy failure', body['error'])

    def test_partial_write_failed_readback_then_layers_recovery(self):
        for getter, key, stored, requested in [('alphaLocked', 'alpha_locked', 'alpha', True),
                                               ('blendingMode', 'blending_mode', 'mode', 'multiply')]:
            with self.subTest(getter=getter):
                doc = groups.Doc()
                doc.root = Node('root', 'grouplayer')
                doc.name = lambda: 'test'
                node, peer = Node(), Node('healthy')
                doc.root.children = [node, peer]
                original_getter = getattr(node, getter)
                def read():
                    if node.writes:
                        raise RuntimeError('persistent readback failure')
                    return original_getter()
                with patch.object(ops, '_active_doc', return_value=doc), patch.object(node, getter, side_effect=read):
                    payload = {'id': node.uid, key: requested}
                    if key == 'alpha_locked':
                        payload['opacity'] = .5  # Must stop before the next setter.
                    status, error = self.request('/layer/props', payload)
                    self.assertEqual(status, 400)
                    self.assertEqual(error['failed'], key)
                    self.assertEqual(error['attempted'], [key])
                    self.assertEqual(error['verified'], [])
                    self.assertFalse(error['rolled_back'])
                    self.assertIn('/layers', error['error'])
                    self.assertEqual(getattr(node, stored), requested)
                    self.assertEqual(node.amount, 255)
                    status, body = self.get_layers()
                    self.assertEqual(status, 200)
                    failed, healthy = body['layers'][0]['children']
                    self.assertEqual(failed['id'], node.uid)
                    self.assertIsNone(failed[key])
                    self.assertEqual(failed['property_status'][key]['status'], 'error')
                    self.assertTrue(failed['alpha_lock_supported'])
                    self.assertEqual(healthy['id'], peer.uid)
                    self.assertEqual(healthy['property_status'][key], {'status': 'ok'})
                    # Recovery is not verification: retry must still fail preflight, without another write.
                    writes = list(node.writes)
                    status, error = self.request('/layer/props', payload)
                    self.assertEqual(status, 400)
                    self.assertIn('属性预读失败', error['error'])
                    self.assertEqual(node.writes, writes)

    def test_props_route_alias_and_bridge(self):
        fn = groups.Mock(return_value={'opacity': .502})
        route = self.server.POST_ROUTES['/layer/props']
        with patch.dict(self.server.POST_ROUTES, {'/layer/props': (fn, route[1], route[2])}):
            self.assertEqual(self.request('/layer/props', {'id': 'p', 'opacity': .5}),
                             (200, {'opacity': .502}))
        fn.assert_called_once_with(layer_id='p', opacity=.5)
        self.control.bridge.call.assert_called_once()

    def test_empty_missing_unknown_rejected_before_bridge(self):
        for body in [{}, {'id': 'p'}, {'opacity': .5}, {'id': 'p', 'opactiy': .5},
                     {'id': 'p', 'opacity': .5, 'typo': True}]:
            self.assertEqual(self.request('/layer/props', body)[0], 400)
        self.control.bridge.call.assert_not_called()

    def test_partial_write_error_payload_and_status_mapping(self):
        payload = {'error': 'partial; no rollback', 'rolled_back': False, 'attempted': ['opacity']}
        for error, status in [(ops.PropsWriteError(payload), 400), (ops.OpError('bad'), 400),
                              (ops.NotFoundError('stale'), 404), (TimeoutError('timeout'), 503)]:
            self.control.bridge.call.side_effect = error
            result = self.request('/layer/props', {'id': 'p', 'opacity': .5})
            self.assertEqual(result[0], status)
            if isinstance(error, ops.PropsWriteError):
                self.assertEqual(result[1], payload)


class Client(unittest.TestCase):
    def test_payloads_include_false_and_zero_and_omit_unspecified(self):
        for args, payload in [(['--alpha-locked', 'false', '--opacity', '0'],
                               {'alpha_locked': False, 'opacity': 0.0}),
                              (['--blending-mode', 'multiply'], {'blending_mode': 'multiply'})]:
            with patch.object(cwb, 'http', return_value={}) as http, contextlib.redirect_stdout(io.StringIO()):
                cwb.main(['props', 'p', *args])
            http.assert_called_once_with(cwb.Krita + '/layer/props', {'id': 'p', **payload})

    def test_invalid_args_never_call_http(self):
        for args in [[], ['--opacity', 'nan'], ['--opacity', 'inf'], ['--opacity', '-1'],
                     ['--opacity', '1.1'], ['--opacity', 'true'], ['--alpha-locked', '1'],
                     ['--blending-mode', '普通']]:
            with patch.object(cwb, 'http') as http, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    cwb.main(['props', 'p', *args])
                self.assertEqual(exc.exception.code, 2)
            http.assert_not_called()

    def test_help_is_offline_without_filesystem_side_effects(self):
        for args in [['--help'], ['props', '--help']]:
            with patch.object(cwb, 'http', side_effect=AssertionError('network')):
                with patch.object(os, 'makedirs', side_effect=AssertionError('mkdir')):
                    with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exc:
                        cwb.main(args)
                    self.assertEqual(exc.exception.code, 0)
            with tempfile.TemporaryDirectory(prefix='cad12-help-') as tmp:
                result = subprocess.run([sys.executable, '-B', str(groups.ROOT / 'core/cwb.py'), *args],
                                        cwd=tmp, env={**os.environ, 'CWB_WF_DIR': tmp + '/absent'},
                                        capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
