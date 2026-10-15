"""CAD-28: restricted SVG and in-memory Krita/handler doubles, NOT real editing QA."""
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import test_layer_groups as groups
import test_layer_props as props

ROOT = Path(__file__).resolve().parents[1]
vector = groups.load_module('_cad28_vector', ROOT / 'core/krita_cowork/vector.py')
ops = groups.ops


def svg(dpi=300):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="24pt" height="48pt" viewBox="0 0 100 200">'
            '<text x="10" y="20" font-size="12" font-family="sans-serif" fill="#202830">肩 &amp; waist</text>'
            '<line x1="10" y1="30" x2="90" y2="30" stroke="#657580" stroke-width="1"/>'
            '<rect x="10" y="40" width="20" height="30" fill="none" stroke="#657580" stroke-width="1"/>'
            '</svg>')


class Shape:
    def __init__(self, source):
        self.source = source

    def toSvg(self, prepend, strip):
        assert prepend is False and strip is False
        return self.source


class VectorNode(props.Node):
    def __init__(self, name='annotations', kind='vectorlayer'):
        super().__init__(name, kind)
        self.contents = []
        self.import_mode = 'normal'

    def addShapesFromSvg(self, source):
        assert isinstance(source, str)
        root = ET.fromstring(source)
        self.contents = [Shape(ET.tostring(el, encoding='unicode')) for el in root]
        if self.import_mode == 'empty':
            self.contents = []
            return []
        if self.import_mode == 'partial':
            self.contents = self.contents[:1]
        if self.import_mode == 'throw':
            raise RuntimeError('after partial mutation')
        return self.contents

    def shapes(self):
        return self.contents


class Document(groups.Doc):
    def __init__(self):
        super().__init__()
        self.factory = VectorNode

    def fileName(self):
        return '/dedicated/cad28.kra'

    def width(self):
        return 100

    def height(self):
        return 200

    def resolution(self):
        raise AssertionError('scalar resolution is not proof of two-axis DPI')

    def xRes(self):
        return 300.0

    def yRes(self):
        return 300.0

    def createVectorLayer(self, name):
        node = self.factory(name, 'vectorlayer')
        self.created.append(node)
        return node

    def createNode(self, *args):
        raise AssertionError('never generic createNode fallback')


class Validation(unittest.TestCase):
    def test_points_mapping_300_and_144_dpi(self):
        vector.validate_svg(svg(), [100, 200], [300, 300])
        other = svg().replace('24pt', '50pt').replace('48pt', '100pt')
        vector.validate_svg(other, [100, 200], [144, 144])
        for source, dpi in [(svg(), [72, 72]), (svg(), [300, 144]),
                            (svg().replace('24pt', '100px'), [300, 300])]:
            with self.assertRaises(ValueError):
                vector.validate_svg(source, [100, 200], dpi)

    def test_attack_allowlist(self):
        attacks = ['<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>',
                   '<?xml-stylesheet href="https://example.org/style"?>',
                   '<script>alert(1)</script>', '<foreignObject/>', '<use href="#x"/>',
                   '<image href="https://remote/image.png"/>', '<path d="M0 0L1 1"/>',
                   '<style>text{fill:url(file:///x)}</style>', '<g><line/></g>']
        for attack in attacks:
            with self.subTest(attack=attack), self.assertRaises(ValueError):
                vector.validate_svg(svg().replace('</svg>', attack + '</svg>'), [100, 200], [300, 300])
        for attr in ['onclick="x"', 'style="fill:red"', 'href="file:///x"',
                     'fill="url(https://x)"', 'transform="scale(2)"', 'xlink:href="x"']:
            with self.subTest(attr=attr), self.assertRaises(ValueError):
                vector.validate_svg(svg().replace('<text ', '<text ' + attr + ' '), [100, 200], [300, 300])

    def test_numeric_xml_text_and_resource_limits(self):
        sources = [svg().replace('x="10"', 'x="NaN"'), svg().replace('x="10"', 'x="-1"'),
                   svg().replace('x2="90"', 'x2="101"'), svg().replace('width="20"', 'width="200"'),
                   svg().replace('font-size="12"', 'font-size="0"'),
                   svg().replace('肩 &amp; waist', 'x' * 121), svg().replace('肩 &amp; waist', ''),
                   svg()[:-4], '', svg() + ' ' * 65536,
                   svg().replace('</svg>', '<line/>' * 160 + '</svg>')]
        for source in sources:
            with self.subTest(source=source[:60]), self.assertRaises(ValueError):
                vector.validate_svg(source, [100, 200], [300, 300])
        for value in [True, float('nan'), float('inf'), 10**1000, '300']:
            with self.assertRaises(ValueError):
                vector.validate_svg(svg(), [100, 200], [value, value])


class Operations(unittest.TestCase):
    def setUp(self):
        self.doc = Document()
        self.old = props.Node('user original', 'paintlayer')
        self.doc.root.children.append(self.old)
        self.app = types.SimpleNamespace(activeDocument=lambda: self.doc)
        self.patches = [patch.object(ops, '_active_doc', return_value=self.doc),
                        patch.object(ops, '_krita', return_value=self.app)]
        package = types.ModuleType('_cad11_package')
        package.__path__ = [str(ROOT / 'core/krita_cowork')]
        self.patches.append(patch.dict(sys.modules, {'_cad11_package': package}))
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def call(self, **changes):
        values = dict(svg=svg(), name='annotations', target_document='/dedicated/cad28.kra',
                      canvas=[100, 200], dpi=[300, 300], dedicated_document=True)
        values.update(changes)
        return ops.layer_vector(**values)

    def test_independent_vector_layer_root_group_and_above(self):
        result = self.call(above_layer_id=self.old.uid)
        self.assertEqual(result['type'], 'vectorlayer')
        self.assertEqual(result['status'], 'structural_readback_only')
        self.assertFalse(result['krita_editing_verified'])
        self.assertEqual(self.doc.root.children[0], self.old)
        self.assertIsNone(self.old.pixels)
        group = groups.Node('nested', 'grouplayer')
        self.doc.root.children.append(group)
        child = self.call(parent_id=group.uid)
        second = self.call(parent_id=group.uid, above_layer_id=child['id'])
        self.assertEqual([n.uid for n in group.children], [child['id'], second['id']])
        self.assertEqual(result['coordinate_mapping']['size_pt'], [24, 48])

    def test_preflight_refuses_wrong_target_capability_and_guards(self):
        for changes in [dict(target_document='/other.kra'), dict(target_document='relative.kra'),
                        dict(target_document='/dedicated/../cad28.kra'), dict(dedicated_document=False),
                        dict(dedicated_document=1), dict(canvas=[101, 200]), dict(dpi=[72, 72]),
                        dict(parent_id=self.old.uid), dict(parent_id=self.doc.root.uid),
                        dict(above_layer_id=self.doc.root.uid), dict(svg='bad')]:
            with self.subTest(changes=changes), self.assertRaises(ops.OpError):
                self.call(**changes)
        for method in ('createVectorLayer', 'xRes', 'yRes'):
            with patch.object(self.doc, method, None), self.assertRaises(ops.OpError):
                self.call()
        with patch.object(self.doc.root, 'colorDepth', return_value='U16'), self.assertRaises(ops.OpError):
            self.call()
        with patch.object(self.doc, 'yRes', return_value=144), self.assertRaises(ops.OpError):
            self.call()
        self.assertEqual(self.doc.created, [])
        with self.assertRaises(ops.NotFoundError):
            self.call(parent_id='stale')

    def test_partial_empty_throw_refresh_failures_keep_old_tree(self):
        for mode in ('empty', 'partial', 'throw', 'refresh'):
            self.doc.root.children = [self.old]
            def factory(name, kind):
                node = VectorNode(name, kind)
                node.import_mode = mode
                return node
            self.doc.factory = factory
            with patch.object(self.doc, 'refreshProjection', side_effect=RuntimeError('refresh') if mode == 'refresh' else None):
                with self.subTest(mode=mode), self.assertRaises(ops.PropsWriteError) as error:
                    self.call()
            payload = error.exception.payload
            self.assertEqual(payload['status'], 'partial_or_unverified')
            self.assertFalse(payload['rolled_back'])
            self.assertEqual(payload['id'], self.doc.created[-1].uid)
            self.assertEqual(self.doc.root.children[0], self.old)
            self.assertEqual(len(self.doc.root.children), 2)

    def test_attach_false_detached_wrong_order_and_bad_readback(self):
        for mode in ('false', 'detached', 'wrong_order'):
            self.doc.root.children = [self.old]
            self.doc.root.attach_ok = mode != 'false'
            self.doc.root.attach_mode = mode
            with self.subTest(mode=mode), self.assertRaises(ops.PropsWriteError) as error:
                self.call(above_layer_id=self.old.uid)
            self.assertEqual(error.exception.payload['failed'], 'attach')
            self.assertEqual(self.doc.created[-1].contents, [])
        self.doc.root.attach_ok = True
        self.doc.root.attach_mode = 'normal'
        with patch.object(VectorNode, 'shapes', side_effect=RuntimeError('readback')):
            with self.assertRaises(ops.PropsWriteError) as error:
                self.call()
            self.assertEqual(error.exception.payload['failed'], 'readback')

    def test_readback_svg_wrapper_cannot_hide_changed_physical_mapping(self):
        changed = svg().replace('width="24pt"', 'width="48pt"')
        with patch.object(VectorNode, 'shapes', return_value=[Shape(changed)]):
            with self.assertRaises(ops.PropsWriteError) as error:
                self.call()
        self.assertEqual(error.exception.payload['failed'], 'readback')
        self.assertFalse(error.exception.payload['rolled_back'])

    def test_active_document_changes_stop_next_write(self):
        real = self.doc.createVectorLayer
        def changed(name):
            node = real(name)
            self.app.activeDocument = lambda: None
            return node
        with patch.object(self.doc, 'createVectorLayer', side_effect=changed):
            with self.assertRaises(ops.PropsWriteError) as error:
                self.call()
        self.assertEqual(error.exception.payload['failed'], 'attach')
        self.assertEqual(self.doc.root.children, [self.old])
        self.assertEqual(self.doc.created[-1].contents, [])

    def test_minimal_above_and_props_extensions_not_alpha_or_root(self):
        node = VectorNode()
        self.doc.root.children.append(node)
        self.assertEqual(ops._insertion_target(self.doc, None, node.uid)[1], node)
        self.assertEqual(ops.layer_props(node.uid, opacity=.5)['opacity'], .502)
        self.assertEqual(ops.layer_props(node.uid, blending_mode='multiply')['blending_mode'], 'multiply')
        with self.assertRaises(ops.OpError):
            ops.layer_props(node.uid, alpha_locked=True)
        with self.assertRaises(ops.OpError):
            ops.layer_props(self.doc.root.uid, opacity=.5)
        node.kind = 'filterlayer'
        with self.assertRaises(ops.OpError):
            ops.layer_props(node.uid, opacity=.5)
        with self.assertRaises(ops.OpError):
            ops._insertion_target(self.doc, None, node.uid)


class Routing(unittest.TestCase):
    setUp = groups.Routing.setUp
    request = groups.Routing.request

    def payload(self):
        return dict(svg=svg(), name='annotation', target_document='/dedicated/cad28.kra',
                    canvas=[100, 200], dpi=[300, 300], dedicated_document=True, above='a')

    def test_vector_help_schema_alias_bridge_and_partial_error(self):
        route = self.server.POST_ROUTES['/layer/vector']
        fn = Mock(return_value={'type': 'vectorlayer'})
        with patch.dict(self.server.POST_ROUTES, {'/layer/vector': (fn, route[1], route[2])}):
            self.assertEqual(self.request('/layer/vector', self.payload()), (200, {'type': 'vectorlayer'}))
        expected = self.payload()
        expected['above_layer_id'] = expected.pop('above')
        fn.assert_called_once_with(**expected)
        self.control.bridge.call.assert_called_once()
        self.control.bridge.call.reset_mock()
        for body in [dict(self.payload(), token='unsafe'), dict(self.payload(), svg='x' * 131073), {}]:
            self.assertEqual(self.request('/layer/vector', body)[0], 400)
        self.control.bridge.call.assert_not_called()
        error = {'error': 'partial', 'id': 'v', 'rolled_back': False}
        self.control.bridge.call.side_effect = ops.PropsWriteError(error)
        self.assertEqual(self.request('/layer/vector', self.payload()), (400, error))


if __name__ == '__main__':
    unittest.main()
