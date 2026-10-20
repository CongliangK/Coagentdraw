"""Reference dataset integrity, local-only HTTP scope and read-only browsing."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
import cwb_references as refs

try:
    from PIL import Image
except ImportError:
    Image = None


def example():
    return json.loads((ROOT / 'examples/reference_gallery/references.json').read_text())


@unittest.skipIf(Image is None, 'optional CLI Pillow is not installed')
class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / '图库 空格'
        refs.initialize(self.root, '测试图库')
        (self.root / 'image.png').write_bytes((ROOT / 'examples/reference_gallery/images/silhouette.png').read_bytes())
        self.source = dict(url='https://example.org/art', creator='测试作者', rights='unknown', retrieved_at='2026-10-20')
        self.record = refs.add(self.root, 'image.png', id='one', title='披衣', meaning='观察外披轮廓', source=self.source, tags=['外披'], material='透明示意图')

    def state(self):
        return (self.root / refs.CATALOG).read_bytes()

    def server(self):
        server = refs.make_server(self.root)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        return server, f'http://127.0.0.1:{server.server_port}'

    def read(self, url):
        # Explicitly avoid machine-specific proxy environment for loopback tests.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=5) as response:
            return response.status, response.headers, response.read()

    def test_dimensions_alpha_and_ratio_are_measured(self):
        image = self.record['image']
        self.assertEqual((image['width'], image['height'], image['aspect_ratio']), (240, 320, .75))
        self.assertTrue(image['has_alpha'])
        self.assertEqual(image['sha256'], hashlib.sha256((self.root / 'image.png').read_bytes()).hexdigest())
        self.assertEqual(refs.load(self.root, verify=True)[0]['records'][0]['meaning'], '观察外披轮廓')
        palette = Image.new('P', (3, 2))
        palette.info['transparency'] = 0
        buf = io.BytesIO(); palette.save(buf, format='PNG')
        self.assertTrue(refs.image_info(buf.getvalue())['has_alpha'])

    def test_tiff_original_and_browser_preview_remain_separate(self):
        output = io.BytesIO(); Image.new('RGB', (40, 70), 'green').save(output, 'TIFF')
        raw = output.getvalue(); (self.root / 'photo.img').write_bytes(raw)
        refs.add(self.root, 'photo.img', id='tiff', title='实物照片', meaning='观察环架', source=self.source)
        _, url = self.server()
        _, headers, original = self.read(url + '/media/tiff')
        self.assertEqual(headers['Content-Type'], 'image/tiff')
        self.assertEqual(original, raw)
        _, headers, observation = self.read(url + '/preview/tiff')
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(Image.open(io.BytesIO(observation)).size, (40, 70))

    def test_orientation_is_measured_as_displayed(self):
        image = Image.new('RGB', (7, 3), 'red')
        exif = image.getexif(); exif[274] = 6
        buf = io.BytesIO(); image.save(buf, 'JPEG', exif=exif)
        info = refs.image_info(buf.getvalue())
        self.assertEqual((info['width'], info['height']), (3, 7))

    def test_failed_mutation_preserves_data_and_existing_init(self):
        before = self.state()
        with self.assertRaises(FileExistsError):
            refs.initialize(self.root, '覆盖')
        with self.assertRaises(ValueError):
            refs.add(self.root, 'image.png', id='one', title='重复', meaning='意义', source=self.source)
        with self.assertRaises(ValueError):
            refs.annotate(self.root, 'one', {'meaning': ''})
        self.assertEqual(before, self.state())

    def test_annotation_is_external_and_rejects_stale_revision(self):
        _, digest = refs.load(self.root)
        refs.annotate(self.root, 'one', {'meaning': '新的设计意义', 'tags': ['外披', '轮廓']}, expected=digest)
        after = self.state()
        with self.assertRaisesRegex(ValueError, '已变更'):
            refs.annotate(self.root, 'one', {'title': '过期写入'}, expected=digest)
        self.assertEqual(after, self.state())
        self.assertEqual(refs.load(self.root)[0]['records'][0]['meaning'], '新的设计意义')

    def test_files_cannot_escape_root_or_follow_symlinks(self):
        outside = Path(self.temp.name) / 'secret.png'
        outside.write_bytes((self.root / 'image.png').read_bytes())
        (self.root / 'linked.png').symlink_to(outside)
        (self.root / 'linked-dir').symlink_to(outside.parent, target_is_directory=True)
        for path in ('../secret.png', str(outside), './image.png', 'image.png/../image.png', 'file:///secret.png', 'linked.png', 'linked-dir/secret.png', 'a\\image.png', 'a//image.png'):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                refs.add(self.root, path, id='bad', title='资料', meaning='意义', source=self.source)
        self.assertEqual(len(refs.load(self.root)[0]['records']), 1)

    def test_schema_rejects_false_geometry_sources_and_duplicates(self):
        data = refs.load(self.root)[0]
        cases = []
        bad = copy.deepcopy(data); bad['records'][0]['image']['aspect_ratio'] = 5; cases.append(bad)
        bad = copy.deepcopy(data); bad['records'][0]['image']['width'] = True; cases.append(bad)
        bad = copy.deepcopy(data); bad['records'][0]['image']['aspect_ratio'] = float('nan'); cases.append(bad)
        bad = copy.deepcopy(data); bad['records'][0]['source']['url'] = 'javascript:alert(1)'; cases.append(bad)
        bad = copy.deepcopy(data); bad['records'][0]['source']['url'] = 'https://name:password@example.org'; cases.append(bad)
        bad = copy.deepcopy(data); bad['records'][0]['tags'] = ['x', 'x']; cases.append(bad)
        bad = copy.deepcopy(data); bad['records'].append(copy.deepcopy(bad['records'][0])); cases.append(bad)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                refs.validate(value)
        data['records'][0]['source']['url'] = 'unknown'
        self.assertEqual(refs.validate(data), data)

    def test_changed_pixels_require_explicit_refresh(self):
        path = self.root / 'image.png'
        Image.new('RGB', (40, 20), 'blue').save(path)
        with self.assertRaisesRegex(ValueError, '已改变'):
            refs.load(self.root, verify=True)
        refs.annotate(self.root, 'one', {}, refresh=True)
        record = refs.load(self.root, verify=True)[0]['records'][0]
        self.assertEqual(record['image']['aspect_ratio'], 2)
        self.assertFalse(record['image']['has_alpha'])

    def test_shortlist_preserves_identity_and_is_not_approval(self):
        before = self.state()
        result = refs.shortlist(self.root, ['one'])
        self.assertEqual(result['catalog_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(result['records'][0]['image']['sha256'], self.record['image']['sha256'])
        self.assertIn('不代表', result['notice'])
        with self.assertRaises(ValueError):
            refs.shortlist(self.root, ['missing'])
        with self.assertRaises(ValueError):
            refs.shortlist(self.root, ['one', 'one'])
        self.assertEqual(before, self.state())

    def test_server_loads_changed_external_json_and_never_embeds_records(self):
        server, url = self.server()
        self.assertEqual(server.server_address[0], '127.0.0.1')
        _, headers, body = self.read(url + '/api/catalog')
        self.assertEqual(json.loads(body)['catalog']['records'][0]['meaning'], '观察外披轮廓')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        refs.annotate(self.root, 'one', {'meaning': '外部数据刚更新'})
        body = self.read(url + '/api/catalog')[2]
        self.assertEqual(json.loads(body)['catalog']['records'][0]['meaning'], '外部数据刚更新')
        status, headers, body = self.read(url + '/')
        self.assertEqual(status, 200)
        self.assertNotIn('观察外披轮廓'.encode(), body)
        self.assertNotIn('外部数据刚更新'.encode(), body)
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])

    def test_server_serves_only_catalogued_images_and_handles_changed_files(self):
        _, url = self.server()
        before = self.state()
        status, headers, body = self.read(url + '/media/one')
        self.assertEqual(status, 200)
        self.assertEqual(body, (self.root / 'image.png').read_bytes())
        self.assertEqual(headers['Content-Type'], 'image/png')
        body = self.read(url + '/thumbnail/one')[2]
        image = Image.open(io.BytesIO(body)); self.assertEqual(image.format, 'JPEG')
        for route in ('/references.json', '/image.png', '/.references.lock', '/../secret.png', '/media/../../secret.png', '/media/missing', '/%2e%2e/secret.png'):
            with self.subTest(route=route), self.assertRaises(urllib.error.HTTPError) as raised:
                self.read(url + route)
            self.assertEqual(raised.exception.code, 404)
        self.assertEqual(before, self.state())
        Image.new('RGB', (2, 2)).save(self.root / 'image.png')
        for route in ('/api/catalog', '/media/one', '/thumbnail/one'):
            with self.subTest(route=route), self.assertRaises(urllib.error.HTTPError) as raised:
                self.read(url + route)
            self.assertEqual(raised.exception.code, 409)

    def test_server_rejects_foreign_host_and_supports_head(self):
        _, url = self.server()
        request = urllib.request.Request(url, headers={'Host': 'untrusted.example'})
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.read(request)
        self.assertEqual(raised.exception.code, 403)
        request = urllib.request.Request(url + '/api/catalog', method='HEAD')
        status, headers, body = self.read(request)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')
        self.assertGreater(int(headers['Content-Length']), 0)

    def test_serving_does_not_modify_dataset_or_create_cache(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        _, url = self.server()
        self.read(url + '/api/catalog'); self.read(url + '/thumbnail/one')
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)


@unittest.skipIf(Image is None, 'optional CLI Pillow is not installed')
class GalleryWorkflowTests(unittest.TestCase):
    def setUp(self):
        import cwb_project as project
        self.project = project
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        project.initialize(self.root, '教学Web资料项目')
        self.snapshot = self.root / 'checkpoints/gallery/v1'
        import shutil
        shutil.copytree(ROOT / 'examples/reference_gallery', self.snapshot)
        self.path = 'checkpoints/gallery/v1/references.json'
        self.preview = 'checkpoints/gallery/v1/preview.png'
        (self.root / self.preview).write_bytes((self.snapshot / 'images/silhouette.png').read_bytes())

    def register(self, preview=True):
        p = self.project
        p.update(self.root, lambda r, d: p.record(r, d, self.path, 2, 'final', '教学快照',
                  object_id='gallery', version='v1', role='research_gallery', kind='checkpoint',
                  preview=self.preview if preview else None))
        return p.read_state(self.root)[0]['assets'][-1]

    def approve(self, asset):
        p = self.project
        p.update(self.root, lambda r, d: p.add_decision(r, d, 'stage', 'research_imagination', 'approve',
                 '仅测试Web资料快照', '人工转录：测试批准，不是真实作品批准', outcomes=['asset:' + asset['id']]))
        decision = p.read_state(self.root)[0]['decisions'][-1]
        p.update(self.root, lambda r, d: p.set_stage(d, 2, '测试', 'completed', decision['id']))

    def test_web_research_can_complete_without_creating_kra(self):
        p = self.project
        asset = self.register(); self.approve(asset)
        data = p.inspect(self.root)
        self.assertEqual(data['state']['stages'][1]['status'], 'completed')
        self.assertEqual(data['state']['documents'], [])
        self.assertEqual(list(self.root.rglob('*.kra')), [])
        self.assertEqual(data['asset_checks'][0]['status'], 'ok')

    def test_gallery_image_dependency_change_invalidates_approval(self):
        p = self.project
        asset = self.register(); self.approve(asset)
        raw = (self.root / p.STATE).read_bytes()
        (self.snapshot / 'images/silhouette.png').write_bytes(b'changed')
        data = p.inspect(self.root)
        self.assertEqual(data['asset_checks'][0]['status'], 'invalid')
        self.assertEqual(data['state']['stages'][1]['status'], 'awaiting_review')
        self.assertEqual(data['state']['decisions'][0]['validity'], 'needs_review')
        self.assertEqual((self.root / p.STATE).read_bytes(), raw)

    def test_empty_gallery_and_absent_visual_preview_cannot_approve(self):
        p = self.project
        asset = self.register(preview=False)
        with self.assertRaisesRegex(p.ProjectError, '可视预览'):
            self.approve(asset)
        data = refs.load(self.snapshot)[0]; data['records'] = []
        (self.snapshot / refs.CATALOG).write_text(json.dumps(data))
        with self.assertRaisesRegex(p.ProjectError, '空图库'):
            self.register()
        with self.assertRaisesRegex(p.ProjectError, '阶段02'):
            p.update(self.root, lambda r, d: p.record(r, d, self.path, 4, 'final', 'wrong stage', role='research_gallery'))


class ReferenceCliTests(unittest.TestCase):
    def test_help_has_no_project_side_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, CWB_WF_DIR=str(Path(folder) / 'no-cache'))
            for args in (['--help'], ['references', '--help'], ['references', 'serve', '--help']):
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'core/cwb.py'), *args], cwd=folder, env=env, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_public_example_uses_only_original_synthetic_assets(self):
        data = refs.validate(example())
        self.assertEqual(len(data['records']), 3)
        for record in data['records']:
            self.assertEqual(record['source']['url'], 'unknown')
            self.assertIn('CC0', record['source']['rights'])
        if Image:
            refs.load(ROOT / 'examples/reference_gallery', verify=True)


if __name__ == '__main__':
    unittest.main()
