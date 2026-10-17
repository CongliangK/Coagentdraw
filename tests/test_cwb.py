"""纯内存 transport；不启动 Krita、ComfyUI、socket 或 GPU。"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import cwb
import cwb_comfy as cc

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / 'comfyui'
PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082')
BASE = 'http://127.0.0.1:8188'


class Fake:
    def __init__(self, history=None):
        self.calls = []
        self.pid = '任务 /?&'
        self.history = history
        self.fail = None
        self.post = None

    def request(self, base, path, timeout, **kw):
        self.calls.append((path, kw, timeout))
        if self.fail:
            self.fail(path)
        if path.startswith('/upload/'):
            return {'name': '服务端 &?#.png', 'subfolder': '目录 / &', 'type': 'input'}
        if path == '/prompt':
            return self.post if self.post is not None else {'prompt_id': self.pid}
        if path.startswith('/history/'):
            if self.history is not None:
                return self.history
            return {self.pid: {'status': {'completed': True, 'status_str': 'success'}, 'outputs': {'save': {'images': [
                {'filename': '图 &?#.png', 'subfolder': '子目录 &/', 'type': 'temp'}]}}}}
        if path.startswith('/view?'):
            return PNG
        raise AssertionError(path)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = Fake()
        self.graph, self.contract = cc.prepare(WF, 'wf_qwen21_t2i_api.json', 't2i', {'prompt': 'test'})

    def tearDown(self):
        self.temp.cleanup()

    def run_graph(self, **kwargs):
        return cc.execute(self.graph, self.contract, self.root / 'runs', BASE, transport=self.fake, **kwargs)

    def records(self):
        return [cc.read_json(p) for p in (self.root / 'runs').glob('*/run.json')]

    def test_all_contracts_preserve_defaults(self):
        for path in WF.glob('*_api.json'):
            original = cc.read_json(path)
            mode = 'edit' if 'load1' in original else 't2i'
            graph, contract = cc.prepare(WF, path.name, mode, {})
            self.assertEqual(graph, original)
            self.assertEqual(contract['outputs'], ['save'])
        self.assertEqual(len(list(WF.glob('*_api.json'))), 5)

    def test_help_invalid_args_no_files(self):
        script = self.root / 'core' / 'cwb.py'
        script.parent.mkdir()
        shutil.copyfile(ROOT / 'core/cwb.py', script)
        shutil.copyfile(ROOT / 'core/cwb_comfy.py', script.parent / 'cwb_comfy.py')
        for args in (['--help'], ['comfy-edit', '--help'], ['comfy-t2i', '--help'], ['comfy-replay', '--help'], ['bad-command'], ['comfy-edit', 'base', 'ref', 'prompt']):
            target = self.root / 'absent'
            proc = subprocess.run([sys.executable, str(script), *args], env={**os.environ, 'CWB_WF_DIR': str(target)}, capture_output=True)
            self.assertEqual(proc.returncode, 0 if '--help' in args else 2)
            self.assertFalse(target.exists())
            self.assertFalse((script.parent / '__pycache__').exists())

    def test_two_parameter_sets_exact_snapshots(self):
        requests = []
        for seed, steps in ((42, 25), (123, 10)):
            self.graph, self.contract = cc.prepare(WF, 'wf_qwen21_t2i_api.json', 't2i', {'prompt': 'test', 'seed': seed, 'steps': steps, 'cfg': 2, 'denoise': .5, 'w': 512, 'h': 768})
            record, path = self.run_graph()
            posted = [kw['payload']['prompt'] for p, kw, _ in self.fake.calls if p == '/prompt'][-1]
            self.assertEqual(cc.read_json(path.parent / 'workflow.json'), posted)
            self.assertEqual(record['parameters']['seed'], seed)
            self.assertEqual(record['parameters']['steps'], steps)
            self.assertEqual(record['status'], 'completed')
            requests.append(posted)
        self.assertNotEqual(requests[0], requests[1])
        self.assertEqual(len(self.records()), 2)

    def test_invalid_values_no_side_effects(self):
        for name, value in [('seed', -1), ('seed', 2**64), ('seed', True), ('steps', 0), ('cfg', float('nan')), ('cfg', float('inf')), ('denoise', 1.1), ('w', 0), ('prompt', ' ')]:
            with self.subTest(name=name, value=value), self.assertRaises(cc.ComfyError):
                cc.prepare(WF, 'wf_qwen21_t2i_api.json', 't2i', {name: value})
        for timeout in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(cc.ComfyError):
                self.run_graph(timeout=timeout)
        self.assertFalse((self.root / 'runs').exists())
        self.assertFalse(self.fake.calls)

    def test_bad_contracts_and_unsupported_parameter(self):
        mutations = [lambda c: c['parameters']['steps'].update(node='missing'), lambda c: c['parameters']['steps'].update(input='missing'), lambda c: c['parameters']['steps'].update(class_type='unknown'), lambda c: c.update(outputs=['missing']), lambda c: c['parameters'].update(cfg=c['parameters']['steps'])]
        for mutation in mutations:
            contract = copy.deepcopy(self.contract)
            mutation(contract)
            with self.assertRaises(cc.ComfyError):
                cc.execute(self.graph, contract, self.root / 'runs', BASE, transport=self.fake)
        graph = copy.deepcopy(self.graph)
        contract = copy.deepcopy(self.contract)
        del contract['parameters']['cfg']
        cc.write_json(self.root / 'custom.json', graph)
        cc.write_json(self.root / 'custom.contract.json', contract)
        with self.assertRaisesRegex(cc.ComfyError, '不支持参数'):
            cc.prepare(self.root, 'custom.json', 't2i', {'cfg': 2})
        self.assertFalse(self.fake.calls)
        self.assertFalse((self.root / 'runs').exists())

    def test_workflow_path_and_mode(self):
        for path in ('../escape.json', '/tmp/escape.json'):
            with self.assertRaises(cc.ComfyError):
                cc.prepare(WF, path, 't2i', {})
        with self.assertRaises(cc.ComfyError):
            cc.prepare(WF, 'wf_qwen21_edit_api.json', 't2i', {})

    def test_upload_response_single_and_double(self):
        for double in (False, True):
            self.graph, self.contract = cc.prepare(WF, 'wf_qwen21_edit_api.json', 'edit', {'prompt': 'edit'})
            image = self.root / 'input.png'
            image.write_bytes(PNG)
            inputs = {'image1': image}
            if double:
                inputs['image2'] = image
            record, path = self.run_graph(inputs=inputs)
            graph = cc.read_json(path.parent / 'workflow.json')
            self.assertEqual(graph['load1']['inputs']['image'], '目录 / &/服务端 &?#.png [input]')
            self.assertEqual(len(graph['encode']['inputs']['images']), 2 if double else 1)
            self.assertEqual(len(record['uploads']), len(inputs))
            self.assertEqual(Path(record['uploads'][0]['local_path']).read_bytes(), PNG)

    def test_missing_second_input_prevents_first_upload(self):
        self.graph, self.contract = cc.prepare(WF, 'wf_qwen21_edit_api.json', 'edit', {})
        image = self.root / 'input.png'
        image.write_bytes(PNG)
        with self.assertRaises(OSError):
            self.run_graph(inputs={'image1': image, 'image2': self.root / 'missing'})
        self.assertFalse(self.fake.calls)
        self.assertFalse((self.root / 'runs').exists())

    def test_encoded_locators_and_node_preserved(self):
        record, _ = self.run_graph()
        output = record['outputs'][0]
        self.assertEqual(output['node_id'], 'save')
        self.assertEqual(output['locator']['type'], 'temp')
        history = [p for p, _, _ in self.fake.calls if p.startswith('/history/')][0]
        self.assertEqual(history, '/history/' + urllib.parse.quote(self.fake.pid, safe=''))
        view = [p for p, _, _ in self.fake.calls if p.startswith('/view?')][0]
        self.assertEqual(urllib.parse.parse_qs(view.split('?', 1)[1]), {k: [v] for k, v in output['locator'].items()})

    def test_multi_output_only_explicit_nodes(self):
        loc = {'filename': 'same.png', 'subfolder': '', 'type': 'output'}
        self.fake.history = {self.fake.pid: {'status': {'completed': True}, 'outputs': {'save': {'images': [loc, loc]}, 'ignored': {'images': [loc]}}}}
        record, _ = self.run_graph()
        self.assertEqual(len(record['outputs']), 2)
        self.assertNotEqual(record['outputs'][0]['path'], record['outputs'][1]['path'])
        self.assertEqual([o['index'] for o in record['outputs']], [0, 1])

    def test_no_premature_output_return(self):
        self.fake.history = {self.fake.pid: {'status': {'completed': False}, 'outputs': {'save': {'images': [{'filename': 'partial.png', 'subfolder': '', 'type': 'output'}]}}}}
        now = [0]
        def sleep(seconds): now[0] += seconds
        with self.assertRaisesRegex(cc.ComfyError, '超时'):
            self.run_graph(timeout=2, clock=lambda: now[0], sleep=sleep)
        self.assertFalse(any(p.startswith('/view') for p, _, _ in self.fake.calls))
        self.assertEqual(self.records()[0]['prompt_id'], self.fake.pid)

    def test_error_empty_output_and_node_errors(self):
        for history in ({'status': {'status_str': 'error'}}, {'status': {'completed': True}, 'outputs': {}}, {'status': {'messages': [['execution_interrupted', {}]]}}):
            self.fake.history = {self.fake.pid: history}
            with self.assertRaises(cc.ComfyError):
                self.run_graph()
        self.fake.post = {'node_errors': {'sampler': 'bad'}, 'prompt_id': 'rejected-id'}
        with self.assertRaisesRegex(cc.ComfyError, '拒绝'):
            self.run_graph()
        self.assertEqual(self.records()[-1]['status'], 'failed')

    def test_timeout_includes_submission(self):
        now = [0]
        def delay(path): now[0] += 5
        self.fake.fail = delay
        with self.assertRaisesRegex(cc.ComfyError, '总超时'):
            self.run_graph(timeout=1, clock=lambda: now[0])
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.records()[0]['failed_stage'], 'submitting')
        self.assertEqual(self.records()[0]['prompt_id'], self.fake.pid)

    def test_failure_and_interrupt_never_resubmit(self):
        for error in (cc.ComfyError('lost response'), KeyboardInterrupt()):
            self.fake = Fake()
            def fail(path):
                if path == '/prompt': raise error
            self.fake.fail = fail
            with self.assertRaises(type(error)):
                self.run_graph()
            self.assertEqual(sum(p == '/prompt' for p, _, _ in self.fake.calls), 1)
        records = self.records()
        self.assertEqual({r['status'] for r in records}, {'failed', 'interrupted'})
        self.assertTrue(all(r['failed_stage'] == 'submitting' for r in records))

    def test_failed_download_keeps_all_locators(self):
        def fail(path):
            if path.startswith('/view'): raise cc.ComfyError('download lost')
        self.fake.fail = fail
        with self.assertRaises(cc.ComfyError): self.run_graph()
        record = self.records()[0]
        self.assertEqual(record['failed_stage'], 'downloading')
        self.assertEqual(record['prompt_id'], self.fake.pid)
        self.assertEqual(len(record['outputs']), 1)

    def test_replay_exact_and_repeat_unique(self):
        first, path = self.run_graph()
        graph, contract, old = cc.load_replay(path, BASE)
        for _ in range(2):
            record, new = cc.execute(graph, contract, self.root / 'runs', BASE, transport=self.fake, replay=old)
            self.assertNotEqual(new, path)
            self.assertEqual(record['replay_of'], first['run_id'])
            self.assertEqual(cc.read_json(new.parent / 'workflow.json'), graph)
        self.assertEqual(len(self.records()), 3)

    def test_edit_replay_validates_inputs_and_replay_of_replay(self):
        self.graph, self.contract = cc.prepare(WF, 'wf_qwen21_edit_api.json', 'edit', {})
        image = self.root / 'input.png'; image.write_bytes(PNG)
        first, path = self.run_graph(inputs={'image1': image})
        graph, contract, old = cc.load_replay(path, BASE)
        record, path2 = cc.execute(graph, contract, self.root / 'runs', BASE, transport=self.fake, replay=old)
        cc.load_replay(path2, BASE)
        old['uploads'][0]['sha256'] = '0' * 64
        before = len([p for p, _, _ in self.fake.calls if p == '/prompt'])
        with self.assertRaisesRegex(cc.ComfyError, '输入已改变'):
            cc.execute(graph, contract, self.root / 'runs', BASE, transport=self.fake, replay=old)
        self.assertEqual(before, len([p for p, _, _ in self.fake.calls if p == '/prompt']))

    def test_replay_tamper_or_other_server_rejected(self):
        record, path = self.run_graph()
        with self.assertRaises(cc.ComfyError): cc.load_replay(path, 'http://other')
        (path.parent / 'workflow.json').write_text('{}')
        with self.assertRaises(cc.ComfyError): cc.load_replay(path, BASE)

    def test_parameter_image_overlap_rejected_before_any_side_effect(self):
        for image_key in ('image1', 'image2'):
            graph, contract = cc.prepare(WF, 'wf_qwen21_edit_api.json', 'edit', {})
            contract['parameters']['prompt'] = copy.deepcopy(contract['images'][image_key])
            graph[contract['images'][image_key]['node']]['inputs']['image'] = 'MY PROMPT'
            with self.assertRaisesRegex(cc.ComfyError, '重叠'):
                cc.execute(graph, contract, self.root / 'runs', BASE, inputs={'image1': self.root / 'missing'}, transport=self.fake)
        self.assertFalse(self.fake.calls)
        self.assertFalse((self.root / 'runs').exists())

    def make_edit_record(self, double=True):
        self.graph, self.contract = cc.prepare(WF, 'wf_qwen21_edit_api.json', 'edit', {})
        image = self.root / 'input.png'
        image.write_bytes(PNG)
        return self.run_graph(inputs={'image1': image, **({'image2': image} if double else {})})

    def test_replay_manifest_schema_and_exact_coverage(self):
        record, path = self.make_edit_record()
        mutations = [
            lambda r: r.pop('uploads'),
            lambda r: r.update(uploads=None),
            lambda r: r.update(uploads=[]),
            lambda r: r['uploads'].pop(),
            lambda r: r['uploads'].append(copy.deepcopy(r['uploads'][0])),
            lambda r: r['uploads'][0].update(key='extra'),
            lambda r: r['uploads'][0].update(key=[]),
            lambda r: r['uploads'][0].update(sha256='bad'),
            lambda r: r['uploads'][0].pop('locator'),
            lambda r: r['uploads'][0]['locator'].update(type='unknown'),
            lambda r: r['uploads'][0]['locator'].update(extra='unexpected'),
        ]
        for mutate in mutations:
            modified = copy.deepcopy(record)
            mutate(modified)
            cc.write_json(path, modified)
            before_calls = len(self.fake.calls)
            before_dirs = sorted((self.root / 'runs').iterdir())
            with self.assertRaises(cc.ComfyError):
                graph, contract, old = cc.load_replay(path, BASE)
                cc.execute(graph, contract, self.root / 'runs', BASE, transport=self.fake, replay=old)
            self.assertEqual(len(self.fake.calls), before_calls)
            self.assertEqual(sorted((self.root / 'runs').iterdir()), before_dirs)

    def test_replay_locator_graph_binding_and_direct_execute_guard(self):
        for double in (False, True):
            record, path = self.make_edit_record(double)
            graph = cc.read_json(path.parent / 'workflow.json')
            modified = copy.deepcopy(record)
            modified['uploads'][0]['locator']['filename'] = 'unrelated.png'
            before = len(self.fake.calls)
            before_dirs = sorted((self.root / 'runs').iterdir())
            with self.assertRaisesRegex(cc.ComfyError, '绑定不一致'):
                cc.execute(graph, self.contract, self.root / 'runs', BASE, transport=self.fake, replay=modified)
            cc.write_json(path, modified)
            with self.assertRaisesRegex(cc.ComfyError, '绑定不一致'):
                cc.load_replay(path, BASE)
            self.assertEqual(len(self.fake.calls), before)
            self.assertEqual(sorted((self.root / 'runs').iterdir()), before_dirs)

    def test_replay_snapshot_must_reconstruct_from_template_and_uploads(self):
        record, path = self.make_edit_record()
        graph = cc.read_json(path.parent / 'workflow.json')
        graph['encode']['inputs']['negative_prompt'] = 'unexpected change'
        before = len(self.fake.calls)
        with self.assertRaisesRegex(cc.ComfyError, '模板/上传绑定不一致'):
            cc.execute(graph, self.contract, self.root / 'runs', BASE, transport=self.fake, replay=record)
        self.assertEqual(len(self.fake.calls), before)

    def test_unmapped_load_image_is_rejected(self):
        self.graph['hidden'] = {'class_type': 'LoadImage', 'inputs': {'image': 'untracked.png'}}
        with self.assertRaisesRegex(cc.ComfyError, '明确映射'):
            self.run_graph()
        self.assertFalse(self.fake.calls)
        self.assertFalse((self.root / 'runs').exists())

    def test_replay_all_workflows_with_complete_input_checks(self):
        for name in ('wf_qwen21_t2i_api.json', 'wf_t2i_2k_api.json', 'wf_qwen21_edit_api.json', 'wf_edit_w4a8_api.json', 'wf_qwen21_localedit_api.json'):
            mode = 'edit' if 'edit' in name else 't2i'
            # localedit 的掩码经 tomask 分支引用 load2，单图模式按设计拒绝，只能双图。
            dual_only = 'localedit' in name
            for double in ((False, True) if mode == 'edit' and not dual_only else ((True,) if mode == 'edit' else (False,))):
                self.graph, self.contract = cc.prepare(WF, name, mode, {'prompt': 'test'})
                image = self.root / 'input.png'
                image.write_bytes(PNG)
                inputs = {'image1': image, **({'image2': image} if double else {})} if mode == 'edit' else {}
                record, path = self.run_graph(inputs=inputs)
                graph, contract, old = cc.load_replay(path, BASE)
                before = len(self.fake.calls)
                replayed, replay_path = cc.execute(graph, contract, self.root / 'runs', BASE, transport=self.fake, replay=old)
                calls = self.fake.calls[before:]
                prepost = calls[:next(i for i, (p, _, _) in enumerate(calls) if p == '/prompt')]
                self.assertEqual(len(prepost), len(inputs))
                self.assertTrue(all(p.startswith('/view?') for p, _, _ in prepost))
                self.assertEqual(cc.read_json(replay_path.parent / 'workflow.json'), graph)
                cc.load_replay(replay_path, BASE)

    def test_single_image_rejects_other_second_input_branches_before_io(self):
        for declared in (False, True):
            for nested in (False, True):
                graph, contract = cc.prepare(WF, 'wf_qwen21_edit_api.json', 'edit', {})
                edge = ['load2', 0]
                graph['extra'] = {'class_type': 'SaveImage', 'inputs': {'images': {'nested': [edge]} if nested else edge}}
                if declared:
                    contract['outputs'].append('extra')
                with self.assertRaisesRegex(cc.ComfyError, '其他分支引用 image2'):
                    cc.execute(graph, contract, self.root / 'runs', BASE, inputs={'image1': self.root / 'missing'}, transport=self.fake)
        self.assertFalse(self.fake.calls)
        self.assertFalse((self.root / 'runs').exists())

    def test_replay_rejects_second_input_branch_even_without_manifest_changes(self):
        record, path = self.make_edit_record(False)
        graph = cc.read_json(path.parent / 'workflow.json')
        for source in (graph, record['template']):
            source['extra'] = {'class_type': 'SaveImage', 'inputs': {'images': ['load2', 0]}}
        record['contract']['outputs'].append('extra')
        before = len(self.fake.calls)
        before_dirs = sorted((self.root / 'runs').iterdir())
        with self.assertRaisesRegex(cc.ComfyError, '其他分支引用 image2'):
            cc.execute(graph, record['contract'], self.root / 'runs', BASE, transport=self.fake, replay=record)
        self.assertEqual(len(self.fake.calls), before)
        self.assertEqual(sorted((self.root / 'runs').iterdir()), before_dirs)

    def test_multi_output_image_branches_remain_supported_with_supplied_inputs(self):
        image = self.root / 'input.png'; image.write_bytes(PNG)
        for second in (False, True):
            self.graph, self.contract = cc.prepare(WF, 'wf_qwen21_edit_api.json', 'edit', {})
            self.graph['extra'] = {'class_type': 'SaveImage', 'inputs': {'images': ['load2' if second else 'load1', 0]}}
            self.contract['outputs'].append('extra')
            loc = {'filename': 'out.png', 'subfolder': '', 'type': 'output'}
            self.fake.history = {self.fake.pid: {'status': {'completed': True}, 'outputs': {node: {'images': [loc]} for node in self.contract['outputs']}}}
            record, path = self.run_graph(inputs={'image1': image, **({'image2': image} if second else {})})
            graph, contract, old = cc.load_replay(path, BASE)
            replayed, _ = cc.execute(graph, contract, self.root / 'runs', BASE, transport=self.fake, replay=old)
            self.assertEqual(len(replayed['outputs']), 2)
            self.assertEqual(len(replayed['uploads']), 2 if second else 1)

    def test_transport_http_json_and_network_errors(self):
        for error in (urllib.error.HTTPError('url', 400, 'bad', {}, io.BytesIO(b'{"node_errors": "bad"}')), urllib.error.URLError('offline'), TimeoutError('slow')):
            with patch('urllib.request.urlopen', side_effect=error) as opened, self.assertRaises(cc.ComfyError):
                cc.Transport().request(BASE, '/prompt', 1, payload={})
            self.assertEqual(opened.call_count, 1)
        response = contextlib.nullcontext(io.BytesIO(b'not-json'))
        with patch('urllib.request.urlopen', return_value=response), self.assertRaises(cc.ComfyError):
            cc.Transport().request(BASE, '/history/id', 1)

    def test_cli_multi_output_does_not_add(self):
        result = {'outputs': [{'path': 'a'}, {'path': 'b'}]}
        with patch.object(cc, 'execute', return_value=(result, 'run.json')), patch.object(cwb, 'http') as http, contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(cc.ComfyError, '多输出'):
            cwb.main(['comfy-t2i', 'test', '--add', 'new layer'])
        http.assert_not_called()


if __name__ == '__main__':
    unittest.main()
