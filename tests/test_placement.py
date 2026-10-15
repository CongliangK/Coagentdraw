"""CAD-14 pure mapping and Qt/Krita doubles; NOT a live pixel/channel test."""
import contextlib
import io
import json
import math
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import test_layer_groups as groups

placement = groups.load_module('_cad14_placement', groups.ROOT / 'core/krita_cowork/placement.py')


def fixture(width=7, height=5):
    """Odd-width coloured checkerboard; R/G encode x/y, alpha marks edge.

    The blue checker and asymmetric R/G coordinate markers detect channel swaps;
    transparent border retains nonzero hidden RGB (must not be composited away).
    """
    return b''.join(bytes((17 + 29 * x, 11 + 43 * y,
                           231 if (x + y) % 2 else 37,
                           0 if x in (0, width - 1) or y in (0, height - 1) else 255))
                    for y in range(height) for x in range(width))


def pixel(data, width, x, y):
    start = (y * width + x) * 4
    return data[start:start + 4]


def canvas(data, width, height, cw, ch, x, y):
    result = bytearray(cw * ch * 4)
    for row in range(height):
        start = ((y + row) * cw + x) * 4
        result[start:start + width * 4] = data[row * width * 4:(row + 1) * width * 4]
    return bytes(result)


class Geometry(unittest.TestCase):
    def test_named_modes_dimensions_origins_and_margins(self):
        cases = [
            ((7, 5, 11, 9), {'mode': 'keep_size'}, [7, 5], [0, 0], [0, 0, 4, 4]),
            ((7, 5, 11, 9), {'mode': 'insert_at_offset', 'offset_x': 4, 'offset_y': 4},
             [7, 5], [4, 4], [4, 4, 0, 0]),
            ((13, 9, 8, 8), {'mode': 'fit_canvas'}, [8, 5], [0, 1], [0, 1, 0, 2]),
            ((3, 2, 8, 7), {'mode': 'fit_canvas'}, [3, 2], [2, 2], [2, 2, 3, 3]),
            ((7, 5, 4, 3), {'mode': 'fit_canvas'}, [4, 2], [0, 0], [0, 0, 0, 1]),
            ((1, 32768, 1, 1), {'mode': 'fit_canvas'}, [1, 1], [0, 0], [0, 0, 0, 0]),
        ]
        for dims, kwargs, size, origin, margins in cases:
            with self.subTest(dims=dims, kwargs=kwargs):
                plan = placement.plan_placement(*dims, **kwargs)
                self.assertEqual(plan['source_size'], list(dims[:2]))
                self.assertEqual(plan['canvas_size'], list(dims[2:]))
                self.assertEqual(plan['output_size'], size)
                self.assertEqual(plan['origin'], origin)
                self.assertEqual(plan['transparent_margins'], margins)
                self.assertEqual(plan['destination_rect'], origin + size)
                self.assertFalse(plan['crop'])
                self.assertFalse(plan['upscale'])
                self.assertEqual(json.loads(json.dumps(plan)), plan)

    def test_exact_rational_scale_and_raster_transform(self):
        p = placement.plan_placement(13, 9, 8, 8, mode='fit_canvas')
        self.assertEqual(p['nominal_scale'], [8, 13])
        self.assertEqual(p['edge_transform'], {'scale_x': [8, 13], 'scale_y': [5, 9],
                                             'translate': [0, 1]})

    def test_legacy_offsets_and_boolean_alias_have_one_interpretation(self):
        base = (3, 2, 8, 7)
        self.assertEqual(placement.plan_placement(*base)['mode'], 'keep_size')
        self.assertEqual(placement.plan_placement(*base, offset_x=1)['mode'], 'insert_at_offset')
        self.assertEqual(placement.plan_placement(*base, fit_canvas=True),
                         placement.plan_placement(*base, mode='fit_canvas'))
        self.assertEqual(placement.plan_placement(*base, fit_canvas=False, offset_y=1),
                         placement.plan_placement(*base, mode='insert_at_offset', offset_y=1))
        self.assertEqual(placement.plan_placement(*base, mode='fit_canvas', fit_canvas=True),
                         placement.plan_placement(*base, mode='fit_canvas'))
        for kw in ({'mode': 'keep_size', 'offset_x': 1},
                   {'mode': 'fit_canvas', 'offset_y': 1},
                   {'fit_canvas': True, 'offset_x': 1},
                   {'mode': 'keep_size', 'fit_canvas': True},
                   {'mode': 'fit_canvas', 'fit_canvas': False},
                   {'mode': 'crop_at_bbox'}, {'mode': 'other'}, {'mode': []},
                   {'fit_canvas': 1}, {'fit_canvas': 'false'}):
            with self.subTest(kw=kw), self.assertRaises(placement.PlacementError):
                placement.plan_placement(*base, **kw)

    def test_strict_coordinates_dimensions_bounds_and_huge_integers(self):
        bad = (-1, -0.5, 1.9, 1.0, True, False, math.inf, -math.inf, math.nan,
               '1', None, [], {}, 32769, 10 ** 200)
        for value in bad:
            for key in ('offset_x', 'offset_y'):
                with self.subTest(value=value, key=key), self.assertRaises(placement.PlacementError):
                    placement.plan_placement(1, 1, 16, 16, **{key: value})
        for index in range(4):
            for value in bad + (0,):
                dims = [1, 1, 16, 16]
                dims[index] = value
                with self.subTest(dims=dims), self.assertRaises(placement.PlacementError):
                    placement.plan_placement(*dims)
        for dims, kw in [((17, 1, 16, 16), {}), ((1, 17, 16, 16), {}),
                         ((7, 5, 16, 16), {'offset_x': 10}),
                         ((7, 5, 16, 16), {'offset_y': 12})]:
            with self.assertRaisesRegex(placement.PlacementError, '越界'):
                placement.plan_placement(*dims, **kw)

    def test_padded_odd_width_checker_coordinate_and_transparent_edge_mapping(self):
        source = fixture()
        padded = b''.join(source[y * 28:(y + 1) * 28] + b'PAD!' for y in range(5))
        packed = placement.packed_rgba(padded, 7, 5, 32)
        self.assertEqual(packed, source)
        for mode in ('keep_size', 'insert_at_offset'):
            p = placement.plan_placement(7, 5, 11, 9, mode=mode,
                                         offset_x=3 if mode == 'insert_at_offset' else 0,
                                         offset_y=2 if mode == 'insert_at_offset' else 0)
            mapped = placement.resize_rgba(packed, p)
            self.assertEqual(mapped, source)
            x, y = p['origin']
            result = canvas(mapped, 7, 5, 11, 9, x, y)
            for sy in range(5):
                for sx in range(7):
                    self.assertEqual(pixel(result, 11, x + sx, y + sy), pixel(source, 7, sx, sy))
        p = placement.plan_placement(7, 5, 4, 3, mode='fit_canvas')
        mapped = placement.resize_rgba(packed, p)
        # Independent, hand-computed source coordinate oracle for 7x5 -> 4x2.
        expected = b''.join(pixel(source, 7, x, y) for y in (1, 3) for x in (0, 2, 4, 6))
        self.assertEqual(mapped, expected)
        self.assertEqual(pixel(mapped, 4, 0, 0), bytes((17, 54, 231, 0)))
        self.assertEqual(pixel(mapped, 4, 1, 0), bytes((75, 54, 231, 255)))
        self.assertEqual(canvas(mapped, 4, 2, 4, 3, 0, 0)[-16:], bytes(16))

    def test_bad_pixel_layout_is_rejected(self):
        for args in [(b'x' * 32, 7, 1, 27), (b'x' * 31, 7, 1, 32)]:
            with self.assertRaises(placement.PlacementError):
                placement.packed_rgba(*args)
        with self.assertRaises(placement.PlacementError):
            placement.resize_rgba(b'wrong', placement.plan_placement(7, 5, 7, 5))


class FixtureImage:
    Format = groups.Image.Format
    data = fixture()

    def __init__(self, path):
        self.path = path

    def isNull(self):
        return False

    def convertToFormat(self, fmt):
        if fmt != self.Format.Format_RGBA8888:
            raise AssertionError('changed channel-format guard')
        return self

    def width(self):
        return 7

    def height(self):
        return 5

    def bytesPerLine(self):
        return 32

    def constBits(self):
        raw = b''.join(self.data[y * 28:(y + 1) * 28] + b'PAD!' for y in range(5))
        return types.SimpleNamespace(asarray=lambda size: raw if size == 160 else b'')


class ImportOperations(unittest.TestCase):
    def setUp(self):
        groups.Operations.setUp(self)
        self.image = patch.object(groups.ops, 'QImage', FixtureImage)
        self.image.start()
        self.addCleanup(self.image.stop)

    def test_actual_ops_payload_with_parent_above_and_no_old_layer_changes(self):
        group = groups.ops.layer_group('parent')
        parent = self.doc.root.children[0]
        anchor = groups.Node('old', 'paintlayer')
        anchor.pixels = b'original bytes'
        parent.children.append(anchor)
        result = groups.ops.layer_add('fixture.png', 'local', parent_id=group['id'],
                                      above_layer_id=anchor.uid, mode='insert_at_offset',
                                      offset_x=9, offset_y=11)
        self.assertEqual(parent.children[0], anchor)
        self.assertEqual(anchor.pixels, b'original bytes')
        self.assertEqual(parent.children[1].pixels, (fixture(), 9, 11, 7, 5))
        self.assertEqual(result['placement']['origin'], [9, 11])
        self.assertEqual(result['parent_id'], group['id'])
        self.assertEqual(result['width'], 7)

    def test_fit_uses_rounded_plan_and_returns_mapping(self):
        with patch.object(self.doc, 'width', return_value=4), patch.object(self.doc, 'height', return_value=3):
            result = groups.ops.layer_add('fixture.png', 'fit', fit_canvas=True)
        expected = b''.join(pixel(fixture(), 7, x, y) for y in (1, 3) for x in (0, 2, 4, 6))
        self.assertEqual(self.doc.created[-1].pixels, (expected, 0, 0, 4, 2))
        self.assertEqual(result['placement'], placement.plan_placement(7, 5, 4, 3, mode='fit_canvas'))

    def test_invalid_parameters_never_create_attach_or_write(self):
        for value in (-0.5, 1.9, True, math.inf, -math.inf, math.nan, 10**200, '1', 1.0):
            for key in ('offset_x', 'offset_y'):
                with self.subTest(value=value, key=key), self.assertRaises(groups.ops.OpError):
                    groups.ops.layer_add('fixture.png', 'bad', **{key: value})
        for kw in ({'offset_x': 10}, {'offset_y': 12}, {'mode': 'crop_at_bbox'},
                   {'mode': 'keep_size', 'offset_x': 1}, {'fit_canvas': 'true'},
                   {'mode': 'fit_canvas', 'fit_canvas': False}, {'parent_id': True},
                   {'above_layer_id': []}, {'name': ''}, {'png_path': None}):
            args = {'png_path': 'fixture.png', 'name': 'bad', **kw}
            with self.subTest(kw=kw), self.assertRaises(groups.ops.OpError):
                groups.ops.layer_add(**args)
        self.assertEqual(self.doc.created, [])
        self.assertEqual(self.doc.root.children, [])
        self.assertEqual(self.doc.refreshes, 0)

    def test_bad_buffers_and_conversion_fail_before_creation(self):
        with patch.object(FixtureImage, 'constBits', return_value=b'short'):
            with self.assertRaises(groups.ops.OpError):
                groups.ops.layer_add('fixture.png', 'bad')
        null = Mock()
        null.isNull.return_value = True
        with patch.object(FixtureImage, 'convertToFormat', return_value=null):
            with self.assertRaises(groups.ops.OpError):
                groups.ops.layer_add('fixture.png', 'bad')
        self.assertEqual(self.doc.created, [])


class Contract(unittest.TestCase):
    def test_cli_mapping_and_legacy_payload(self):
        for args, extra in [(['--mode', 'fit_canvas'], {'mode': 'fit_canvas'}),
                            (['--mode', 'insert_at_offset', '--x', '3', '--y', '4'],
                             {'mode': 'insert_at_offset', 'offset_x': 3, 'offset_y': 4}),
                            (['--fit-canvas'], {'fit_canvas': True}), ([], {})]:
            with patch.object(groups.cwb, 'http', return_value={}) as http, contextlib.redirect_stdout(io.StringIO()):
                groups.cwb.main(['add', 'fixture.png', 'paint', '--parent', 'g', '--above', 'a', *args])
            expected = {'png_path': os.path.abspath('fixture.png'), 'name': 'paint',
                        'parent_id': 'g', 'above': 'a', 'offset_x': 0, 'offset_y': 0, **extra}
            http.assert_called_once_with(groups.cwb.Krita + '/layer/add', expected, timeout=120)
        for args in (['--x', '1.9'], ['--mode', 'crop_at_bbox'],
                     ['--mode', 'keep_size', '--fit-canvas']):
            with patch.object(groups.cwb, 'http') as http, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    groups.cwb.main(['add', 'fixture.png', 'p', *args])
                http.assert_not_called()

    def test_http_to_ops_contract_and_bad_coordinate_400(self):
        routing = groups.Routing()
        routing.setUp()
        package = types.ModuleType('_cad11_package')
        package.__path__ = [str(groups.ROOT / 'core/krita_cowork')]
        doc = groups.Doc()
        with patch.dict(sys.modules, {'_cad11_package': package}), \
                patch.object(groups.ops, '_active_doc', return_value=doc), \
                patch.object(groups.ops, 'QImage', FixtureImage):
            body = {'png_path': 'fixture.png', 'name': 'paint', 'mode': 'insert_at_offset',
                    'offset_x': 2, 'offset_y': 3, 'fit_canvas': False}
            status, result = routing.request('/layer/add', body)
            self.assertEqual(status, 200)
            self.assertEqual(result['placement']['origin'], [2, 3])
            self.assertEqual(doc.created[-1].pixels, (fixture(), 2, 3, 7, 5))
            for value in (-0.5, 1.9, True, math.inf, math.nan, 10**200):
                count = len(doc.created)
                self.assertEqual(routing.request('/layer/add', {**body, 'offset_x': value})[0], 400)
                self.assertEqual(len(doc.created), count)
            self.assertEqual(routing.request('/layer/add', {**body, 'mode': 'crop_at_bbox'})[0], 400)


if __name__ == '__main__':
    unittest.main()
