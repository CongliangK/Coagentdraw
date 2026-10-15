"""ComfyUI 离线可测契约与运行记录；只依赖标准库。"""
import copy
import hashlib
import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class ComfyError(ValueError):
    pass


def read_json(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def validate_value(name, value):
    if name == 'prompt':
        valid = isinstance(value, str) and bool(value.strip())
    elif name in ('seed', 'steps', 'w', 'h'):
        valid = type(value) is int and (0 <= value <= 2**64 - 1 if name == 'seed' else value > 0)
    else:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
        if name == 'denoise':
            valid = valid and value <= 1
        if name == 'timeout':
            valid = valid and value > 0
    if not valid:
        raise ComfyError('无效参数 %s: %r' % (name, value))


def validate_contract(graph, contract, mode=None, single_image=False):
    if not isinstance(graph, dict) or not graph or not isinstance(contract, dict):
        raise ComfyError('工作流/契约必须为非空 JSON 对象')
    if contract.get('version') != 1 or contract.get('mode') not in ('t2i', 'edit'):
        raise ComfyError('不支持的契约版本/模式')
    if mode and contract['mode'] != mode:
        raise ComfyError('工作流模式与命令不匹配')
    for node in graph.values():
        if not isinstance(node, dict) or not isinstance(node.get('inputs'), dict) or not isinstance(node.get('class_type'), str):
            raise ComfyError('不是 ComfyUI API 格式工作流')
    parameters = contract.get('parameters')
    if not isinstance(parameters, dict) or 'prompt' not in parameters:
        raise ComfyError('契约缺少 prompt 映射')
    seen = set()
    for name, target in parameters.items():
        if name not in ('prompt', 'seed', 'steps', 'cfg', 'denoise', 'w', 'h'):
            raise ComfyError('未知参数映射: ' + name)
        node, field = validate_target(graph, target)
        if (node, field) in seen:
            raise ComfyError('重复参数映射')
        seen.add((node, field))
        validate_value(name, graph[node]['inputs'][field])
    outputs = contract.get('outputs')
    if not isinstance(outputs, list) or not outputs or not all(isinstance(n, str) for n in outputs) or len(set(outputs)) != len(outputs):
        raise ComfyError('契约缺少明确输出节点')
    for node in outputs:
        if node not in graph or graph[node]['class_type'] not in ('SaveImage', 'PreviewImage'):
            raise ComfyError('输出节点不存在或不是图像输出: ' + str(node))
    if contract['mode'] == 'edit':
        images = contract.get('images', {})
        if not isinstance(images, dict) or set(images) != {'image1', 'image2', 'collection'}:
            raise ComfyError('编辑契约缺少图像映射')
        for name in ('image1', 'image2', 'collection'):
            node, field = validate_target(graph, images[name])
            if (node, field) in seen:
                raise ComfyError('参数/图像映射目标重叠')
            seen.add((node, field))
            if name == 'collection':
                continue
            if graph[node]['class_type'] != 'LoadImage' or field != 'image':
                raise ComfyError('图像映射必须指向 LoadImage.image')
        if images['image1']['node'] == images['image2']['node']:
            raise ComfyError('两个图像输入不能映射到同一节点')
        node, field = validate_target(graph, images['collection'])
        expected = {'image_1': [images['image1']['node'], 0], 'image_2': [images['image2']['node'], 0]}
        if single_image is True or (single_image is None and graph[node]['inputs'][field] == {'image_1': expected['image_1']}):
            expected.pop('image_2')
        if graph[node]['inputs'][field] != expected:
            raise ComfyError('图像集合必须明确引用两个输入节点')
        if 'image_2' not in expected:
            validate_single_image_dependencies(graph, contract)
    mapped_loaders = {contract['images'][key]['node'] for key in ('image1', 'image2')} if contract['mode'] == 'edit' else set()
    if {key for key, node in graph.items() if node['class_type'] == 'LoadImage'} != mapped_loaders:
        raise ComfyError('所有 LoadImage 节点必须由编辑契约明确映射')
    return contract


def validate_single_image_dependencies(graph, contract):
    """单图只允许删除 collection 内的 image_2；其他 load2 引用一律明确拒绝。

    保守检查整个 API 图（也包括未声明为下载目标的分支），不用猜测自定义
    节点执行语义或 ComfyUI 的 OUTPUT_NODE 注册状态。
    """
    second = contract['images']['image2']['node']
    collection = contract['images']['collection']

    def references(value):
        if isinstance(value, list):
            if len(value) == 2 and value[0] == second and isinstance(value[1], int):
                return True
            return any(references(child) for child in value)
        if isinstance(value, dict):
            return any(references(child) for child in value.values())
        return False

    for node, definition in graph.items():
        for field, value in definition['inputs'].items():
            if node == collection['node'] and field == collection['input']:
                value = {key: child for key, child in value.items() if key != 'image_2'}
            if references(value):
                raise ComfyError('单图模式仍有其他分支引用 image2；请提供 --image2 或修改工作流')


def validate_target(graph, target):
    if not isinstance(target, dict) or set(target) != {'node', 'input', 'class_type'}:
        raise ComfyError('映射需要 node/input/class_type')
    node, field = target['node'], target['input']
    if not isinstance(node, str) or not isinstance(field, str) or node not in graph or graph[node]['class_type'] != target['class_type'] or field not in graph[node]['inputs']:
        raise ComfyError('映射节点/类型/字段不匹配: ' + str(target))
    return node, field


def prepare(root, name, mode, overrides):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path.suffix != '.json':
        raise ComfyError('--wf 必须是 CWB_WF_DIR 内的 JSON')
    graph = read_json(path)
    contract = read_json(path.with_suffix('.contract.json'))
    validate_contract(graph, contract, mode)
    for name, value in overrides.items():
        if value is None:
            continue
        if name not in contract['parameters']:
            raise ComfyError('工作流不支持参数: ' + name)
        validate_value(name, value)
        target = contract['parameters'][name]
        graph[target['node']]['inputs'][target['input']] = value
    return graph, contract


class Transport:
    def request(self, base, path, timeout, payload=None, data=None, headers=None, binary=False):
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers = {'Content-Type': 'application/json'}
        req = urllib.request.Request(base + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise ComfyError('HTTP %s: %s' % (exc.code, exc.read(8192).decode(errors='replace'))) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ComfyError('网络失败（未自动重试）: ' + str(exc)) from exc
        if binary:
            return body
        try:
            return json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise ComfyError('服务端返回无效 JSON') from exc


def locator(value):
    if not isinstance(value, dict) or not isinstance(value.get('filename', value.get('name')), str):
        raise ComfyError('无效图像定位')
    result = {'filename': value.get('filename', value.get('name')), 'subfolder': value.get('subfolder'), 'type': value.get('type')}
    if not result['filename'] or not isinstance(result['subfolder'], str) or result['type'] not in ('input', 'output', 'temp'):
        raise ComfyError('图像定位缺少 name/subfolder/type')
    return result


def view_path(loc):
    return '/view?' + urllib.parse.urlencode(loc)


def effective(graph, contract):
    return {name: graph[t['node']]['inputs'][t['input']] for name, t in contract['parameters'].items()}


def image_binding(loc):
    """与 LoadImage 实际使用的带类型路径一致；同时用于写入与重放核对。"""
    return (loc['subfolder'].rstrip('/') + '/' if loc['subfolder'] else '') + loc['filename'] + ' [%s]' % loc['type']


def validate_replay(graph, contract, record, base):
    """不访问网络/创建目录；图像键覆盖从实际图推导，不信任清单长度。"""
    if not isinstance(record, dict) or record.get('version') != 1 or record.get('base_url') != base:
        raise ComfyError('重放版本或服务地址不匹配')
    if record.get('contract') != contract:
        raise ComfyError('重放契约不匹配')
    validate_contract(graph, contract, single_image=None)
    template = record.get('template')
    validate_contract(template, contract)
    uploads = record.get('uploads')
    if not isinstance(uploads, list):
        raise ComfyError('重放 uploads 必须为数组')
    expected = set()
    reconstructed = copy.deepcopy(template)
    if contract['mode'] == 'edit':
        target = contract['images']['collection']
        collection = graph[target['node']]['inputs'][target['input']]
        expected = {'image1', 'image2'} if 'image_2' in collection else {'image1'}
        reconstructed[target['node']]['inputs'][target['input']] = copy.deepcopy(collection)
    seen = set()
    for upload in uploads:
        if not isinstance(upload, dict) or set(upload) != {'key', 'locator', 'sha256', 'local_path'}:
            raise ComfyError('重放上传记录结构无效')
        key = upload['key']
        if not isinstance(key, str) or key not in expected or key in seen:
            raise ComfyError('重放上传键重复或不在实际工作流中')
        seen.add(key)
        loc = locator(upload['locator'])
        if loc != upload['locator']:
            raise ComfyError('重放 locator 字段必须为 filename/subfolder/type')
        digest = upload['sha256']
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ComfyError('重放输入 SHA-256 无效')
        if not isinstance(upload['local_path'], str) or not upload['local_path']:
            raise ComfyError('重放输入副本路径无效')
        target = contract['images'][key]
        binding = image_binding(loc)
        if graph[target['node']]['inputs'][target['input']] != binding:
            raise ComfyError('重放输入 locator 与工作流实际绑定不一致')
        reconstructed[target['node']]['inputs'][target['input']] = binding
    if seen != expected:
        raise ComfyError('重放 uploads 未准确覆盖工作流输入')
    if reconstructed != graph:
        raise ComfyError('重放快照与模板/上传绑定不一致')
    if effective(graph, contract) != record.get('parameters'):
        raise ComfyError('重放参数不匹配')


def execute(graph, contract, workdir, base, *, inputs=None, timeout=1800, transport=None,
            clock=time.monotonic, sleep=time.sleep, replay=None):
    """一次显式执行；任何失败不重新 POST。不取消已提交的服务端任务。"""
    validate_value('timeout', timeout)
    graph, contract = copy.deepcopy(graph), copy.deepcopy(contract)
    if replay is not None:
        if inputs:
            raise ComfyError('重放不能同时上传新输入')
        validate_replay(graph, contract, replay, base)
    else:
        validate_contract(graph, contract)
        if contract['mode'] == 'edit' and 'image2' not in (inputs or {}):
            validate_single_image_dependencies(graph, contract)
    if contract['mode'] == 't2i' and inputs:
        raise ComfyError('文生图不接受图像上传')
    transport = transport or Transport()
    blobs = {}
    for key, path in (inputs or {}).items():
        blobs[key] = Path(path).read_bytes()  # 全部读取成功后才能创建记录或上传。
    if replay is None and contract['mode'] == 'edit' and ('image1' not in blobs or set(blobs) - {'image1', 'image2'}):
        raise ComfyError('编辑需要 image1，且只支持 image1/image2')
    run = Path(workdir) / str(uuid.uuid4())
    run.mkdir(parents=True, exist_ok=False)
    record = {'version': 1, 'run_id': run.name, 'base_url': base, 'status': 'preparing',
              'prompt_id': None, 'parameters': effective(graph, contract), 'outputs': [], 'uploads': [],
              'contract': contract, 'template': copy.deepcopy(replay['template'] if replay else graph), 'replay_of': replay.get('run_id') if replay else None}
    record_path = run / 'run.json'
    def save():
        write_json(record_path, record)
    deadline = clock() + timeout
    def request(path, check_after=True, **kwargs):
        remaining = deadline - clock()
        if remaining <= 0:
            raise ComfyError('总超时；已提交任务可能仍在运行，禁止盲目重试')
        result = transport.request(base, path, timeout=remaining, **kwargs)
        if check_after and clock() > deadline:
            raise ComfyError('总超时；已提交任务可能仍在运行，禁止盲目重试')
        return result
    save()
    try:
        if replay:
            for upload in replay['uploads']:
                data = request(view_path(upload['locator']), binary=True)
                if hashlib.sha256(data).hexdigest() != upload['sha256']:
                    raise ComfyError('重放输入已改变；未提交工作流')
            record['uploads'] = copy.deepcopy(replay['uploads'])
        for key, data in blobs.items():
            name = 'cwb_' + uuid.uuid4().hex + '.png'
            boundary = uuid.uuid4().hex
            body = ('--%s\r\nContent-Disposition: form-data; name="image"; filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n' % (boundary, name)).encode() + data + ('\r\n--%s--\r\n' % boundary).encode()
            record['status'] = 'uploading'
            save()
            loc = locator(request('/upload/image?type=input&overwrite=false', data=body,
                                  headers={'Content-Type': 'multipart/form-data; boundary=' + boundary}))
            target = contract['images'][key]
            graph[target['node']]['inputs'][target['input']] = image_binding(loc)
            local = run / (key + '.png')
            local.write_bytes(data)
            record['uploads'].append({'key': key, 'locator': loc, 'sha256': hashlib.sha256(data).hexdigest(), 'local_path': str(local.resolve())})
            save()
        if blobs and 'image2' not in blobs:
            target = contract['images']['collection']
            graph[target['node']]['inputs'][target['input']] = {'image_1': [contract['images']['image1']['node'], 0]}
        write_json(run / 'workflow.json', graph)
        record['workflow_sha256'] = hashlib.sha256((run / 'workflow.json').read_bytes()).hexdigest()
        record['status'] = 'submitting'
        save()
        response = request('/prompt', check_after=False, payload={'prompt': graph, 'client_id': record['run_id']})
        if not isinstance(response, dict):
            raise ComfyError('无效 /prompt 响应')
        # 即使 node_errors 同时返回 ID，也保留 ID 以便查证。
        record['prompt_id'] = response.get('prompt_id')
        save()
        if clock() > deadline:
            raise ComfyError('提交后总超时；已保存 prompt_id，任务可能仍在运行')
        if response.get('node_errors') or response.get('error'):
            raise ComfyError('ComfyUI 拒绝工作流: ' + json.dumps(response, ensure_ascii=False))
        pid = record['prompt_id']
        if not isinstance(pid, str) or not pid:
            raise ComfyError('/prompt 缺少 prompt_id；提交状态不确定')
        record['status'] = 'polling'
        save()
        while True:
            history = request('/history/' + urllib.parse.quote(pid, safe=''))
            if not isinstance(history, dict):
                raise ComfyError('无效 history 响应')
            item = history.get(pid)
            if item is not None:
                if not isinstance(item, dict) or not isinstance(item.get('status'), dict):
                    raise ComfyError('无效 history 状态')
                status = item['status']
                if status.get('status_str') == 'error' or any(isinstance(m, list) and m and m[0] in ('execution_error', 'execution_interrupted') for m in status.get('messages', [])):
                    raise ComfyError('ComfyUI 执行错误: ' + json.dumps(status, ensure_ascii=False))
                if status.get('completed') is True or status.get('status_str') == 'success':
                    outputs = item.get('outputs', {})
                    if not isinstance(outputs, dict):
                        raise ComfyError('无效 outputs')
                    for node in contract['outputs']:
                        for index, image in enumerate(outputs.get(node, {}).get('images', [])):
                            loc = locator(image)
                            record['outputs'].append({'node_id': node, 'index': index, 'locator': loc, 'path': None})
                    if not record['outputs']:
                        raise ComfyError('工作流完成但映射输出节点没有图像')
                    break
            remaining = deadline - clock()
            if remaining <= 0:
                raise ComfyError('ComfyUI 轮询总超时')
            sleep(min(3, remaining))
        record['status'] = 'downloading'
        save()
        for index, output in enumerate(record['outputs']):
            data = request(view_path(output['locator']), binary=True)
            if not data:
                raise ComfyError('图像下载为空')
            path = run / ('output_%03d.png' % index)
            path.write_bytes(data)
            output.update(path=str(path.resolve()), sha256=hashlib.sha256(data).hexdigest())
            save()
        record['status'] = 'completed'
        save()
        return record, record_path
    except BaseException as exc:
        record['failed_stage'] = record['status']
        record['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
        record['error'] = str(exc) or type(exc).__name__
        save()
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise ComfyError('%s；记录: %s' % (exc, record_path)) from exc


def load_replay(path, base):
    path = Path(path)
    record = read_json(path)
    if not isinstance(record, dict) or record.get('version') != 1 or record.get('base_url') != base:
        raise ComfyError('重放版本或服务地址不匹配')
    graph_path = path.parent / 'workflow.json'
    if hashlib.sha256(graph_path.read_bytes()).hexdigest() != record.get('workflow_sha256'):
        raise ComfyError('工作流快照校验失败')
    graph = read_json(graph_path)
    validate_replay(graph, record.get('contract'), record, base)
    return graph, record['contract'], record
