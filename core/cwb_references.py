"""Local reference datasets and a read-only web gallery; no Krita or remote I/O."""
import contextlib
import datetime
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import uuid
from urllib.parse import unquote, urlsplit

SCHEMA = "cwb-reference-gallery-v1"
CATALOG = "references.json"
WEB = Path(__file__).with_name("reference_gallery")
MAX_CATALOG = 16 * 1024 * 1024
MAX_IMAGE = 64 * 1024 * 1024
MAX_PIXELS = 50_000_000
MAX_RECORDS = 10_000
MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif", "TIFF": "image/tiff"}
NOTICE = "本地资料预览与试拼清单；推荐不代表作者采纳，清单不代表已导入 Krita 或艺术批准。"


def text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 8192 or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError(f"{field} 须为非空文本（最多8192字符）；未知信息明确写 unknown")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("id 须为1..64位字母、数字、下划线或连字符")
    return value


def source_url(value):
    if value == "unknown":
        return value
    text(value, "来源URL")
    parsed = urlsplit(value)
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password
            or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError("来源URL须为无凭据的http(s)地址，未知写unknown")
    return value


def local_file(root, relative):
    text(relative, "本地文件路径")
    parts = relative.split('/')
    if ('\\' in relative or ':' in relative or any(p in ('', '.', '..') for p in parts)
            or PurePosixPath(relative).is_absolute()):
        raise ValueError("文件路径须相对资料目录，使用/分隔，不含空段、..、反斜杠或驱动器")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("资料路径不可经符号链接；请引用目录内原图")
    if not stat.S_ISREG(current.stat().st_mode):
        raise ValueError("资料须为普通文件")
    return current


def read_file(path, limit):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"文件超过大小上限：{path.name}")
    return data


def pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ValueError("此命令需要CLI环境的可选Pillow；不安装或修改Krita插件环境") from exc
    return Image, ImageOps


def image_info(data):
    Image, ImageOps = pillow()
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in MIMES or source.width * source.height > MAX_PIXELS:
                raise ValueError("只支持PNG/JPEG/WebP/GIF/TIFF，且图片不可超过5000万像素")
            mime = MIMES[source.format]
            image = ImageOps.exif_transpose(source)
            image.load()
            width, height = image.size
            alpha = image.convert('RGBA').getchannel('A').getextrema()[0] < 255
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("图片无法解码或尺寸过大") from exc
    return {"width": width, "height": height, "aspect_ratio": width / height,
            "has_alpha": alpha, "mime": mime, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def thumbnail(data):
    Image, ImageOps = pillow()
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert('RGBA')
        image.thumbnail((640, 640))
        background = Image.new('RGBA', image.size, '#f4f2ec')
        background.alpha_composite(image)
        output = io.BytesIO()
        background.convert('RGB').save(output, format='JPEG', quality=85)
        return output.getvalue()


def preview(data):
    """Browser-readable observation copy for TIFF; never rewrite the original."""
    Image, ImageOps = pillow()
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert('RGBA')
        image.thumbnail((2048, 2048))
        output = io.BytesIO()
        image.save(output, format='PNG')
        return output.getvalue()


def validate(data):
    if not isinstance(data, dict) or set(data) != {"schema", "id", "title", "description", "records"} or data['schema'] != SCHEMA:
        raise ValueError("须为cwb-reference-gallery-v1数据集，包含schema/id/title/description/records")
    identifier(data['id'])
    text(data['title'], '图库名称')
    if not isinstance(data['description'], str) or len(data['description']) > 8192:
        raise ValueError('description须为最多8192字符的文本')
    if not isinstance(data['records'], list) or len(data['records']) > MAX_RECORDS:
        raise ValueError('records须为最多10000项的列表')
    seen = set()
    required = {'id', 'title', 'file', 'meaning', 'source', 'tags', 'group', 'material', 'priority', 'image'}
    for record in data['records']:
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError('资料项字段须为：' + ', '.join(sorted(required)))
        rid = identifier(record['id'])
        if rid in seen:
            raise ValueError(f'重复资料id：{rid}')
        seen.add(rid)
        for field in ('title', 'file', 'meaning', 'group', 'material'):
            text(record[field], field)
        if record['priority'] not in ('normal', 'recommended', 'parked'):
            raise ValueError('priority须为normal/recommended/parked（不代表批准）')
        tags = record['tags']
        if not isinstance(tags, list) or len(tags) > 64 or any(not isinstance(t, str) for t in tags) or len(set(tags)) != len(tags):
            raise ValueError('tags须为最多64项的不重复文本列表')
        for tag in tags:
            text(tag, 'tag')
        source = record['source']
        if (not isinstance(source, dict) or not {'url', 'creator', 'rights', 'retrieved_at'} <= set(source)
                or set(source) - {'url', 'creator', 'rights', 'retrieved_at', 'image_url'}):
            raise ValueError('source须包含url/creator/rights/retrieved_at；可选image_url')
        for field in ('creator', 'rights', 'retrieved_at'):
            text(source[field], field)
        source_url(source['url'])
        if 'image_url' in source:
            source_url(source['image_url'])
        info = record['image']
        if not isinstance(info, dict) or set(info) != {'width', 'height', 'aspect_ratio', 'has_alpha', 'mime', 'bytes', 'sha256'}:
            raise ValueError('image须为工具测量的宽高、长宽比、透明度、类型、字节数与SHA256')
        for field in ('width', 'height', 'bytes'):
            if type(info[field]) is not int or info[field] < 1:
                raise ValueError(f'image.{field}须为正整数')
        if info['width'] * info['height'] > MAX_PIXELS or info['bytes'] > MAX_IMAGE:
            raise ValueError('图片超过尺寸或文件大小上限')
        ratio = info['aspect_ratio']
        if (type(ratio) not in (int, float) or not math.isfinite(ratio)
                or not math.isclose(ratio, info['width'] / info['height'], rel_tol=1e-12)):
            raise ValueError('长宽比须与宽高一致')
        if type(info['has_alpha']) is not bool or info['mime'] not in MIMES.values():
            raise ValueError('无效透明度或图片类型')
        if not isinstance(info['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', info['sha256']):
            raise ValueError('无效图片SHA256')
    return data


def load(root, *, verify=False):
    root = Path(root).resolve(strict=True)
    raw = read_file(local_file(root, CATALOG), MAX_CATALOG)
    data = validate(json.loads(raw))
    for record in data['records']:
        path = local_file(root, record['file'])
        if verify:
            info = image_info(read_file(path, MAX_IMAGE))
            if info != record['image']:
                raise ValueError(f"{record['id']} 原图已改变，须用refresh重新测量；不沿用旧尺寸或摘要")
    return data, hashlib.sha256(raw).hexdigest()


def _write(root, data):
    validate(data)
    target = local_file(root, CATALOG)
    raw = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if len(raw) > MAX_CATALOG:
        raise ValueError('数据集超过16MiB')
    descriptor, name = tempfile.mkstemp(prefix='.references-', suffix='.tmp', dir=root)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def editing(root, expected=None):
    root = Path(root).resolve(strict=True)
    lock = root / '.references.lock'
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        data, digest = load(root)
        if expected is not None and digest != expected:
            raise ValueError('数据集已变更，请重新读取后编辑')
        yield root, data
        # Check again for non-cooperating edits before replacing the file.
        if load(root)[1] != digest:
            raise ValueError('写入前发现数据集改变，保留现有文件')
        _write(root, data)


def initialize(root, title, description=''):
    root = Path(root)
    text(title, '图库名称')
    if not root.exists():
        root.mkdir(parents=True)
    root = root.resolve(strict=True)
    data = {'schema': SCHEMA, 'id': str(uuid.uuid4()), 'title': title, 'description': description, 'records': []}
    validate(data)
    with (root / CATALOG).open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return data


def add(root, file, *, id, title, meaning, source, tags=None, group='未分组', material='未分类', priority='normal', expected=None):
    with editing(root, expected) as (root, data):
        if any(record['id'] == id for record in data['records']):
            raise ValueError('资料id已存在；标注使用annotate，原图变化使用refresh')
        record = dict(id=id, title=title, file=file, meaning=meaning, source=source,
                      tags=tags or [], group=group, material=material, priority=priority,
                      image=image_info(read_file(local_file(root, file), MAX_IMAGE)))
        data['records'].append(record)
    return record


def annotate(root, rid, changes, source_changes=None, *, refresh=False, expected=None):
    allowed = {'title', 'meaning', 'tags', 'group', 'material', 'priority'}
    if changes.keys() - allowed:
        raise ValueError('标注不能改变id、路径或测量值')
    with editing(root, expected) as (root, data):
        record = next((x for x in data['records'] if x['id'] == rid), None)
        if record is None:
            raise ValueError('找不到资料id：' + rid)
        record.update(changes)
        if source_changes:
            record['source'].update(source_changes)
        if refresh:
            record['image'] = image_info(read_file(local_file(root, record['file']), MAX_IMAGE))
    return record


def shortlist(root, ids):
    data, digest = load(root, verify=True)
    lookup = {record['id']: record for record in data['records']}
    if len(set(ids)) != len(ids) or any(rid not in lookup for rid in ids):
        raise ValueError('清单id不可重复，且须存在于当前数据集')
    return {'schema': 'cwb-reference-shortlist-v1', 'catalog_id': data['id'], 'catalog_sha256': digest,
            'notice': NOTICE, 'records': [lookup[rid] for rid in ids]}


def make_server(root, port=0):
    root = Path(root).resolve(strict=True)
    load(root, verify=True)
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('端口须为0..65535，0表示系统分配')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_HEAD(self):
            self.do_GET(head=True)

        def do_GET(self, head=False):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                self.reply(403, 'text/plain; charset=utf-8', b'Local Host required', head)
                return
            try:
                parts = urlsplit(self.path)
                path = unquote(parts.path)
                if path in ('/', '/index.html', '/app.js', '/style.css'):
                    name = 'index.html' if path in ('/', '/index.html') else path[1:]
                    mime = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'style.css': 'text/css; charset=utf-8'}[name]
                    self.reply(200, mime, (WEB / name).read_bytes(), head)
                elif path == '/api/catalog':
                    data, digest = load(root, verify=True)
                    self.reply(200, 'application/json; charset=utf-8', json.dumps({
                        'catalog': data, 'sha256': digest, 'notice': NOTICE}, ensure_ascii=False).encode(), head)
                elif re.fullmatch(r'/(media|thumbnail|preview)/[A-Za-z0-9][A-Za-z0-9_-]{0,63}', path):
                    _, kind, rid = path.split('/')
                    data, _ = load(root)
                    record = next((x for x in data['records'] if x['id'] == rid), None)
                    if record is None:
                        raise FileNotFoundError('找不到图片')
                    raw = read_file(local_file(root, record['file']), MAX_IMAGE)
                    info = record['image']
                    if hashlib.sha256(raw).hexdigest() != info['sha256']:
                        raise ValueError('原图已改变，请刷新测量后重开图库')
                    if kind == 'thumbnail':
                        content, mime = thumbnail(raw), 'image/jpeg'
                    elif kind == 'preview':
                        content, mime = preview(raw), 'image/png'
                    else:
                        content, mime = raw, info['mime']
                    self.reply(200, mime, content, head)
                else:
                    self.reply(404, 'text/plain; charset=utf-8', b'Not found', head)
            except FileNotFoundError:
                self.reply(404, 'text/plain; charset=utf-8', '资料或原图不存在'.encode(), head)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.reply(409, 'text/plain; charset=utf-8', str(exc).encode(), head)

        def reply(self, code, mime, body, head):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            if not head:
                self.wfile.write(body)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def run(args):
    try:
        command = args.references_cmd
        if command == 'init':
            result = initialize(args.root, args.title, args.description)
        elif command in ('add', 'annotate'):
            if args.tag and args.clear_tags:
                raise ValueError('--tag与--clear-tags不可同时使用')
            source = {key: getattr(args, flag) for key, flag in (
                ('url', 'source_url'), ('creator', 'creator'), ('rights', 'rights'),
                ('image_url', 'image_url'), ('retrieved_at', 'retrieved_at')) if getattr(args, flag) is not None}
            if command == 'add':
                source['retrieved_at'] = args.retrieved_at or datetime.date.today().isoformat()
            values = {key: getattr(args, key) for key in ('title', 'meaning', 'group', 'material', 'priority') if getattr(args, key) is not None}
            if args.tag is not None or args.clear_tags or command == 'add':
                values['tags'] = args.tag or []
            if command == 'add':
                result = add(args.root, args.file, id=args.id, source=source, expected=args.expect_sha256, **values)
            else:
                result = annotate(args.root, args.id, values, source, expected=args.expect_sha256)
        elif command == 'refresh':
            result = annotate(args.root, args.id, {}, refresh=True, expected=args.expect_sha256)
        elif command == 'validate':
            data, digest = load(args.root, verify=True)
            result = {'schema': SCHEMA, 'records': len(data['records']), 'sha256': digest, 'notice': NOTICE}
        elif command == 'shortlist':
            result = shortlist(args.root, args.id)
            with Path(args.output).open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        elif command == 'serve':
            server = make_server(args.root, args.port)
            print(f'参考图库：http://127.0.0.1:{server.server_port}/', flush=True)
            print('Ctrl+C停止；仅读取本地图片和外部JSON，不访问Krita或上传资料。', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit('references: ' + str(exc)) from exc
