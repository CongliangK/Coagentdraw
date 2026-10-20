"""Offline layout fixtures; teaching PNGs, not a real character/Krita case."""
import builtins
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
import cwb
import cwb_layout as layout

PIL_AVAILABLE = importlib.util.find_spec('PIL') is not None


class Layout(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='layout-layout-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.inputs = self.root / 'teaching'
        layout.fixture(str(self.inputs))
        self.spec_path = self.inputs / 'fixture.json'
        self.spec = json.loads(self.spec_path.read_bytes())
        self.output = self.root / 'bundle'

    def save(self):
        self.spec_path.write_text(json.dumps(self.spec, ensure_ascii=False), encoding='utf-8')

    def test_deterministic_common_cells_scale_anchors_points_and_no_plugin(self):
        before = set(self.root.iterdir())
        first = layout.plan(str(self.spec_path))
        self.assertEqual(layout.plan(str(self.spec_path)), first)
        self.assertTrue(first['fixture'])
        self.assertFalse(first['krita_verified'])
        self.assertEqual(first['view_scale'], [1, 1])
        for item in first['images']:
            x, y = item['placement']['origin']
            self.assertEqual([x + item['anchor_source'][0], y + item['anchor_source'][1]], item['anchor_target'])
            self.assertEqual(item['placement']['mode'], 'insert_at_offset')
            self.assertEqual(item['resize']['sampling'], 'nearest_pixel_center_floor')
        self.assertEqual(set(self.root.iterdir()), before)
        _, svg, _ = layout._collect(str(self.spec_path))
        root = ET.fromstring(svg)
        self.assertEqual(root.get('viewBox'), '0 0 500 420')
        self.assertAlmostEqual(float(root.get('width')[:-2]), 120)
        self.assertEqual(len(root.findall('{http://www.w3.org/2000/svg}line')), 6)
        self.assertEqual(len(root.findall('{http://www.w3.org/2000/svg}rect')), 6)
        self.assertNotIn('<image', svg)
        self.assertNotIn('<path', svg)

    def test_xml_escape_short_text_and_downscale_anchor(self):
        self.spec['callouts'][0]['text'] = '肩 < A & B > 腰'
        for role in ('front', 'side', 'back'):
            item = self.spec['images'][role]
            item['cell'][2:] = [50, 80]
            item['anchor_target'] = [item['cell'][0] + 25, 34]
        self.spec['references'] = dict(zip(layout.REFERENCES, (34, 45, 65, 90)))
        self.save()
        plan, svg, _ = layout._collect(str(self.spec_path))
        self.assertIn('&lt; A &amp; B &gt;', svg)
        self.assertEqual(plan['view_scale'], [1, 2])
        for item in plan['images'][1:4]:
            self.assertEqual(item['resize']['output_size'], [50, 80])
            self.assertEqual(item['placement']['origin'][1], 30)

    def test_fractional_anchor_error_is_explicit_and_snapshots_not_retained(self):
        item = self.spec['images']['main']
        item.update(cell=[10, 30, 50, 80], anchor_source=[51, 9], anchor_target=[35, 34])
        self.save()
        manifest, _, captures = layout._collect(str(self.spec_path))
        self.assertEqual(manifest['images'][0]['anchor_error_px'], [[1, 2], [1, 2]])
        self.assertEqual(manifest['images'][0]['placement']['origin'], [10, 30])
        self.assertTrue(all(capture[1] == b'' for capture in captures[1:]))

    def test_reject_unknown_units_invalid_geometry_and_manual_anchors(self):
        mutations = [lambda s: s.update(unit='inch'), lambda s: s.update(dpi=[300, 144]),
                     lambda s: s.update(dpi=[float('nan'), 300]), lambda s: s.update(canvas=[True, 420]),
                     lambda s: s['references'].update(waist=20),
                     lambda s: s['images']['front'].update(cell=[130, 30, 101, 160]),
                     lambda s: s['images']['front'].update(anchor_target=[181, 38]),
                     lambda s: s['images']['front'].update(anchor_target=[180, 39]),
                     lambda s: s['images']['front'].update(anchor_source=[101, 8]),
                     lambda s: s['images']['main'].update(cell=[490, 30, 100, 160]),
                     lambda s: s['images']['main'].update(cell=[130, 30, 100, 160]),
                     lambda s: s['references'].update(knee=210),
                     lambda s: s['images']['main'].update(cell=[10, 30, -1, 160]),
                     lambda s: s['images']['main'].update(cell=[10, 30, 100.0, 160]),
                     lambda s: s['callouts'][0].update(text='x' * 121),
                     lambda s: s['callouts'][0].update(target=[501, 2]),
                     lambda s: s['images'].pop('back'), lambda s: s.update(script='no')]
        original = json.dumps(self.spec)
        for mutate in mutations:
            self.spec = json.loads(original)
            mutate(self.spec)
            self.save()
            with self.subTest(spec=self.spec), self.assertRaises((ValueError, OSError)):
                layout.plan(str(self.spec_path))
            self.assertFalse(self.output.exists())

    def test_png_admission_bad_crc_missing_animation_and_resource(self):
        path = self.inputs / 'main.png'
        good = path.read_bytes()
        for bad in [b'not a PNG', good[:-1], good[:50] + b'X' + good[51:]]:
            path.write_bytes(bad)
            with self.assertRaises(ValueError):
                layout.plan(str(self.spec_path))
        path.write_bytes(good)
        # Admission uses placement before allocating/decoding large buffers.
        placement = layout._pure('placement')
        with self.assertRaises(ValueError):
            placement.plan_placement(8192, 8192, 100, 160, 'fit_canvas')
        with self.assertRaises(ValueError):
            placement.plan_placement(4096, 4096, 4095, 4095, 'fit_canvas')
        path.unlink()
        with self.assertRaises(OSError):
            layout.plan(str(self.spec_path))

    def test_links_aliases_fifo_and_output_protection(self):
        path = self.inputs / 'main.png'
        original = self.inputs / 'original.png'
        path.rename(original)
        path.symlink_to(original.name)
        with self.assertRaises(OSError):
            layout.plan(str(self.spec_path))
        path.unlink()
        os.link(original, path)
        with self.assertRaises(ValueError):
            layout.plan(str(self.spec_path))
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(ValueError):
            layout.plan(str(self.spec_path))
        path.unlink()
        original.rename(path)
        for value in [str(self.inputs) + '/./fixture.json', str(self.inputs) + '//fixture.json']:
            with self.assertRaises(ValueError):
                layout.plan(value)
        linked_parent = self.root / 'alias'
        linked_parent.symlink_to(self.inputs, target_is_directory=True)
        with self.assertRaises(OSError):
            layout.plan(str(linked_parent / 'fixture.json'))
        with self.assertRaises(OSError):
            layout.fixture(str(self.inputs))
        self.assertEqual(path.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')

    def test_plan_no_pillow_no_network_no_output_and_source_change(self):
        original_import = builtins.__import__
        def no_optional(name, *args, **kwargs):
            if name == 'PIL' or name.startswith(('PIL.', 'krita', 'PyQt')):
                raise AssertionError('unexpected optional/plugin import: ' + name)
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=no_optional), patch.object(cwb, 'http', side_effect=AssertionError('network')):
            layout.plan(str(self.spec_path))
        capture = layout._capture(str(self.inputs / 'main.png'), 100000)
        with (self.inputs / 'main.png').open('ab') as stream:
            stream.write(b'change')
        with self.assertRaises(ValueError):
            layout._unchanged([capture])
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(PIL_AVAILABLE, 'optional CLI Pillow absent; no install')
    def test_render_originals_transparency_and_separate_manual_payloads(self):
        from PIL import Image
        result = layout.render(str(self.spec_path), str(self.output))
        self.assertTrue(result['fixture'])
        manifest = json.loads((self.output / 'manifest.json').read_bytes())
        for item in manifest['images']:
            self.assertEqual((self.output / item['original']).read_bytes(), (self.inputs / (item['role'] + '.png')).read_bytes())
            with Image.open(self.output / item['rendered']) as image:
                self.assertEqual(image.mode, 'RGBA')
                self.assertEqual(image.getpixel((0, 0)), (50, 100, 180, 0))
                self.assertEqual(image.getpixel((0, 8)), (58, 100, 180, 150))
        with patch.object(cwb, 'http', side_effect=AssertionError('network')):
            prepared = layout.prepare(str(self.output), '/dedicated/test.kra')
        self.assertTrue(prepared['manual_only'])
        self.assertEqual(prepared['asset_verification'], 'local_rgba_consistency_only')
        self.assertEqual([r['route'] for r in prepared['requests']], ['/layer/add'] * 6 + ['/layer/vector'])
        self.assertTrue(prepared['requests'][-1]['payload']['dedicated_document'])
        for item, request in zip(manifest['images'], prepared['requests']):
            self.assertEqual(request['payload']['mode'], 'insert_at_offset')
            self.assertEqual([request['payload']['offset_x'], request['payload']['offset_y']], item['placement']['origin'])
        with self.assertRaises(FileExistsError):
            layout.render(str(self.spec_path), str(self.output))
        with (self.inputs / 'main.png').open('ab') as stream:
            stream.write(b'changed source')
        with self.assertRaises(ValueError):
            layout.prepare(str(self.output), '/dedicated/test.kra')

    @unittest.skipUnless(PIL_AVAILABLE, 'optional CLI Pillow absent; no install')
    def test_bundle_tamper_and_incomplete_outputs_not_success(self):
        layout.render(str(self.spec_path), str(self.output))
        for filename in ('annotations.svg', 'main.png', 'main.original.png', 'source.json'):
            path = self.output / filename
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                layout.prepare(str(self.output), '/dedicated/test.kra')
            path.write_bytes(original)
        linked = self.root / 'linked-bundle'
        linked.symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(OSError):
            layout.prepare(str(linked), '/dedicated/test.kra')
        with self.assertRaises(ValueError):
            layout.prepare(str(self.output), '/dedicated/not-png.png')
        second = self.root / 'incomplete'
        real_write = layout._write
        def fail(fd, name, data):
            if name == 'annotations.svg':
                raise OSError('simulated disk failure')
            return real_write(fd, name, data)
        with patch.object(layout, '_write', side_effect=fail), self.assertRaises(OSError):
            layout.render(str(self.spec_path), str(second))
        self.assertFalse((second / 'manifest.json').exists())
        with self.assertRaises(OSError):
            layout.prepare(str(second), '/dedicated/test.kra')

    def test_source_lexical_aliases_rejected_before_path_normalization(self):
        for alias in ('./main.png', 'main.png//', 'sub//main.png', 'sub/../main.png'):
            self.spec['images']['main']['path'] = alias
            self.save()
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                layout.plan(str(self.spec_path))

    @unittest.skipUnless(PIL_AVAILABLE, 'optional CLI Pillow absent; no install')
    def test_prepare_recomputes_pixels_not_self_reported_sha(self):
        from PIL import Image
        layout.render(str(self.spec_path), str(self.output))
        manifest_path = self.output / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        path = self.output / 'main.png'
        good = path.read_bytes()
        import struct
        import zlib
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        huge_header = chunk(b'IHDR', struct.pack('>IIBBBBB', 8192, 8192, 8, 6, 0, 0, 0))
        bad_stream = good[:33] + chunk(b'IDAT', b'invalid zlib') + chunk(b'IEND', b'')
        replacements = [b'not a PNG', good[:-1], good[:50] + b'X' + good[51:],
                        b'\x89PNG\r\n\x1a\n' + huge_header, bad_stream]
        for size in ((1, 1), (100, 160)):
            stream = io.BytesIO()
            Image.new('RGBA', size, (255, 0, 0, 255)).save(stream, format='PNG')
            replacements.append(stream.getvalue())
        # Changing hidden RGB alone must also fail, even though visible output
        # is identical; this is a byte-channel contract, not visual similarity.
        with Image.open(io.BytesIO(good)) as image:
            image.putpixel((0, 0), (99, 100, 180, 0))
            stream = io.BytesIO()
            image.save(stream, format='PNG')
            replacements.append(stream.getvalue())
        for data in replacements:
            path.write_bytes(data)
            manifest['images'][0]['rendered_sha256'] = layout._sha(data)
            manifest_path.write_bytes(layout._json(manifest))
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                layout.prepare(str(self.output), '/dedicated/test.kra')
        # Lossless re-encoding is acceptable: compare decoded RGBA, not encoder
        # spelling. The expected content is derived from original sources.
        with Image.open(io.BytesIO(good)) as image:
            stream = io.BytesIO()
            image.save(stream, format='PNG', compress_level=0)
        data = stream.getvalue()
        path.write_bytes(data)
        manifest['images'][0]['rendered_sha256'] = layout._sha(data)
        manifest_path.write_bytes(layout._json(manifest))
        self.assertEqual(len(layout.prepare(str(self.output), '/dedicated/test.kra')['requests']), 7)

    @unittest.skipUnless(PIL_AVAILABLE, 'optional CLI Pillow absent; no install')
    def test_prepare_downscale_is_production_sampling_and_missing_decoder_fails_closed(self):
        from PIL import Image
        raw = bytes(v for y in range(5) for x in range(7)
                    for v in (x, y, 100, 0 if x == 0 else 200))
        Image.frombytes('RGBA', (7, 5), raw).save(self.inputs / 'main.png')
        self.spec['images']['main'].update(cell=[10, 30, 4, 3], anchor_source=[0, 0], anchor_target=[10, 30])
        self.save()
        layout.render(str(self.spec_path), str(self.output))
        self.assertEqual(len(layout.prepare(str(self.output), '/dedicated/test.kra')['requests']), 7)
        with Image.open(self.output / 'main.png') as image:
            expected = bytes(v for y in (1, 3) for x in (0, 2, 4, 6)
                             for v in (x, y, 100, 0 if x == 0 else 200))
            self.assertEqual(image.tobytes(), expected)
        real_import = builtins.__import__
        def no_pillow(name, *args, **kwargs):
            if name == 'PIL' or name.startswith('PIL.'):
                raise ImportError('optional decoder unavailable')
            return real_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=no_pillow), self.assertRaisesRegex(ValueError, 'Pillow'):
            layout.prepare(str(self.output), '/dedicated/test.kra')

    def test_empty_cell_area_is_not_an_image_sampling_allocation(self):
        self.spec['canvas'] = [19000, 4000]
        self.spec['references']['head'] = 8
        for index, role in enumerate(layout.ROLES):
            self.spec['images'][role].update(cell=[index * 3000, 0, 3000, 3000],
                                              anchor_target=[index * 3000 + 50, 8])
        self.save()
        manifest = layout.plan(str(self.spec_path))
        self.assertEqual(manifest['view_scale'], [1, 1])
        self.assertTrue(all(item['resize']['output_size'] == [100, 160] for item in manifest['images']))

    def test_expired_shared_preprocess_budget_does_not_start_output(self):
        placement = layout._pure('placement')
        real_pure = layout._pure
        class Expired:
            def check(self, stage):
                raise placement.PlacementError('expired ' + stage)
        with patch.object(layout, '_pure', side_effect=lambda name: placement if name == 'placement' else real_pure(name)):
            with patch.object(placement, 'PreprocessBudget', Expired):
                with self.assertRaises(ValueError):
                    layout.render(str(self.spec_path), str(self.output))
        self.assertFalse(self.output.exists())

    def test_duplicate_json_and_aggregate_budget(self):
        self.spec_path.write_text('{"schema":1,"schema":2}', encoding='utf-8')
        with self.assertRaises(ValueError):
            layout.plan(str(self.spec_path))
        self.save()
        with patch.object(layout, 'MAX_TOTAL_BYTES', 128), self.assertRaises(ValueError):
            layout.plan(str(self.spec_path))


class Client(unittest.TestCase):
    def test_all_help_offline_without_executing_layout_or_optional_modules(self):
        real_import = builtins.__import__
        def no_layout(name, *args, **kwargs):
            if name == 'cwb_layout' or name.startswith(('PIL', 'krita', 'PyQt')):
                raise AssertionError('help executed module: ' + name)
            return real_import(name, *args, **kwargs)
        args_list = [['--help'], ['layout', '--help']]
        args_list += [['layout', cmd, '--help'] for cmd in ('fixture', 'plan', 'render', 'prepare')]
        with tempfile.TemporaryDirectory(prefix='layout-help-') as temp:
            for args in args_list:
                with patch('builtins.__import__', side_effect=no_layout), patch.object(cwb, 'http', side_effect=AssertionError('HTTP')):
                    with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
                        cwb.main(args)
                    self.assertEqual(error.exception.code, 0)
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'core/cwb.py'), *args],
                                        cwd=temp, env={**os.environ, 'CWB_WF_DIR': str(Path(temp) / 'absent')},
                                        capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
