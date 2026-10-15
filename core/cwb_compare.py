"""CAD-23: offline PNG comparison and append-only decisions; never calls Krita.

Pillow is optional and imported only by create(). All disk access is anchored
with dir_fd/O_NOFOLLOW (Linux); no symlink traversal or overwriting outputs.
"""
import contextlib
import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import uuid
import warnings

SCHEMA = "cwb-compare-offline-v1"
MAX_BYTES = 128 * 1024 * 1024
MAX_PIXELS = 16_000_000
STATES = {"accept": "accepted", "reject": "rejected", "hide": "hidden", "withdraw": "pending"}
NOTICE = "仅离线记录；未改变 Krita 图层/投影，未接入 #25，未完成 #17 或真实恢复验收。"


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _json(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _absolute(path):
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("路径必须是非空文本")
    parts = raw.split("/")
    if any(p in (".", "..") for p in parts) or "//" in raw or raw.endswith("/"):
        raise ValueError("拒绝路径别名（.、..、重复或末尾分隔符）")
    return os.path.abspath(raw)


@contextlib.contextmanager
def _parent(path):
    path = _absolute(path)
    parts = Path(path).parts
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, parts[-1], path
    finally:
        os.close(fd)


@contextlib.contextmanager
def _directory(path):
    with _parent(path) as (parent, name, absolute):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            yield fd, absolute
        finally:
            os.close(fd)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_at(fd, name, limit=None):
    if limit is None:
        limit = MAX_BYTES
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        first = os.fstat(handle)
        if not stat.S_ISREG(first.st_mode) or first.st_nlink != 1:
            raise ValueError("只接受无硬链接别名的普通文件: " + name)
        if first.st_size > limit:
            raise ValueError("文件超过大小限制: " + name)
        with os.fdopen(os.dup(handle), "rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit or _signature(first) != _signature(os.fstat(handle)):
            raise ValueError("读取期间源改变: " + name)
        return data, _signature(first)
    finally:
        os.close(handle)


def _capture(path):
    with _parent(path) as (fd, name, absolute):
        data, signature = _read_at(fd, name)
    return {"path": absolute, "sha256": _digest(data), "bytes": len(data)}, data, signature


def _unchanged(captures):
    for record, _, signature in captures:
        current, _, current_signature = _capture(record["path"])
        if current_signature != signature or current["sha256"] != record["sha256"]:
            raise ValueError("源文件改变；未记录成功: " + record["path"])


def _write_at(fd, name, data):
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(os.dup(handle), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        os.unlink(name, dir_fd=fd)
        raise
    finally:
        os.close(handle)


def _pillow():
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise ValueError("compare create 需要外部 CLI Python 的可选 Pillow (PIL)；不会自动安装，不是 Krita 插件依赖") from exc
    return Image, ImageDraw


def _render(before, after, crop):
    Image, ImageDraw = _pillow()
    images = []
    try:
        for data in (before, after):
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                        raise ValueError("仅支持单帧 PNG；不接受其他格式/动画")
                    if image.width * image.height > MAX_PIXELS:
                        raise ValueError("PNG 像素数超过限制")
                    if data[24] > 8 or image.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
                        raise ValueError("仅接受可无损显示为 RGBA8 的 PNG 模式，不接受 16 位整数图")
                    image.load()
                    images.append(image.convert("RGBA"))
        if images[0].size != images[1].size:
            raise ValueError("before/after 尺寸不匹配；不会静默拉伸或映射")
        width, height = images[0].size
        if crop is None:
            crop = (0, 0, width, height)
        if (len(crop) != 4 or any(type(v) is not int for v in crop)
                or crop[0] < 0 or crop[1] < 0 or crop[2] <= 0 or crop[3] <= 0
                or crop[0] + crop[2] > width or crop[1] + crop[3] > height):
            raise ValueError("共同 crop 必须为界内 x y width height（正宽高）")
        x, y, w, h = crop
        # Separate opaque label band; paste without mask preserves input alpha.
        result = Image.new("RGBA", (w * 2, h + 20), (0, 0, 0, 0))
        draw = ImageDraw.Draw(result)
        draw.rectangle((0, 0, w * 2 - 1, 19), fill=(32, 32, 32, 255))
        draw.text((1, 2), "Before", fill="white")
        draw.text((w + 1, 2), "After", fill="white")
        for index, image in enumerate(images):
            result.paste(image.crop((x, y, x + w, y + h)), (index * w, 20))
        output = io.BytesIO()
        result.save(output, format="PNG")
        result.close()
        if output.tell() > MAX_BYTES:
            raise ValueError("比较图超过文件大小限制")
        return output.getvalue(), [width, height], list(crop)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("PNG 超过安全解码限制") from exc
    finally:
        for image in images:
            image.close()


def create(before, after, output, *, crop=None, source_version=None, candidate_version=None,
           prompt=None, workflow=None, seed=None, document_ref=None, candidate_layer_ref=None):
    """Create a new standalone directory. Caller-supplied provenance is unverified."""
    metadata = dict(source_version=source_version, candidate_version=candidate_version,
                    prompt=prompt, workflow=workflow, document_ref=document_ref,
                    candidate_layer_ref=candidate_layer_ref)
    if any(value is not None and (not isinstance(value, str) or not value.strip()) for value in metadata.values()):
        raise ValueError("来源字段必须为非空文本或未知 null")
    if any(value is not None and len(value.encode("utf-8")) > 64 * 1024 for value in metadata.values()):
        raise ValueError("每个来源字段最多 64 KiB")
    if seed is not None and type(seed) is not int:
        raise ValueError("seed 必须为整数或未知 null")
    captures = [_capture(before), _capture(after)]
    if captures[0][2][:2] == captures[1][2][:2]:
        raise ValueError("before/after 不得是同一文件或路径别名")
    png, dimensions, crop = _render(captures[0][1], captures[1][1], crop)
    sources = {}
    for key, captured, version in zip(("before", "after"), captures, (source_version, candidate_version)):
        sources[key] = dict(captured[0], dimensions=dimensions, source_version=version,
                            asset=key + ".png")
    manifest = dict(schema=SCHEMA, comparison_id=str(uuid.uuid4()), created_at=_now(),
                    mode="offline_record_only", krita_applied=False, decision_store_integrated=False,
                    notice=NOTICE, sources=sources,
                    provenance=dict(prompt=prompt, workflow=workflow, seed=seed,
                                    document_ref=document_ref, candidate_layer_ref=candidate_layer_ref,
                                    evidence="caller_supplied_unverified; null means unknown; no generation performed"),
                    display=dict(file="comparison.png", sha256=_digest(png), crop_xywh=crop,
                                 scale=1, coordinates="native PNG pixels; no EXIF/color transformation",
                                 panel_boxes_xywh=[[0, 20, crop[2], crop[3]], [crop[2], 20, crop[2], crop[3]]]))
    manifest_bytes = _json(manifest)
    if len(manifest_bytes) > 1024 * 1024:
        raise ValueError("sidecar 超过 1 MiB 限制")
    _unchanged(captures)
    with _parent(output) as (parent, name, absolute):
        # An existing file, directory or symlink always fails; never reuse results.
        os.mkdir(name, 0o700, dir_fd=parent)
        fd = None
        owned = []
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            fcntl.flock(fd, fcntl.LOCK_EX)
            for filename, data in (("before.png", captures[0][1]), ("after.png", captures[1][1]),
                                   ("comparison.png", png), ("compare.json", manifest_bytes)):
                _write_at(fd, filename, data)
                owned.append(filename)
            _unchanged(captures)
            os.fsync(fd)
        except BaseException:
            if fd is not None:
                for filename in owned:
                    try:
                        os.unlink(filename, dir_fd=fd)
                    except FileNotFoundError:
                        pass
                # Only remove the exact directory we created, not a replaced path.
                if _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))[:2] == _signature(os.fstat(fd))[:2]:
                    os.rmdir(name, dir_fd=parent)
            raise
        finally:
            if fd is not None:
                os.close(fd)
    return dict(output=absolute, comparison_id=manifest["comparison_id"], mode="offline_record_only", notice=NOTICE)


def _load_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON 重复字段")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("JSON 非有限值: " + value)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def _load(fd):
    manifest = _load_json(_read_at(fd, "compare.json", 1024 * 1024)[0])
    if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or manifest.get("mode") != "offline_record_only"
            or manifest.get("krita_applied") is not False or manifest.get("decision_store_integrated") is not False):
        raise ValueError("不是离线比较 sidecar")
    if not isinstance(manifest["comparison_id"], str):
        raise ValueError("比较 UUID 必须为文本")
    uuid.UUID(manifest["comparison_id"])
    for key in ("before", "after"):
        source = manifest["sources"][key]
        if source["asset"] != key + ".png" or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
            raise ValueError("非法资产记录")
        if _digest(_read_at(fd, key + ".png")[0]) != source["sha256"]:
            raise ValueError("保存的资产摘要不匹配: " + key)
    if manifest["display"]["file"] != "comparison.png" or _digest(_read_at(fd, "comparison.png")[0]) != manifest["display"]["sha256"]:
        raise ValueError("比较图摘要不匹配")
    names = sorted(os.listdir(fd))
    event_names = [name for name in names if name.startswith("event-")]
    events = []
    state, previous_hash = "pending", None
    for sequence, name in enumerate(event_names, 1):
        if name != f"event-{sequence:06d}.json":
            raise ValueError("事件序号缺失或文件名非法")
        raw = _read_at(fd, name, 1024 * 1024)[0]
        event = _load_json(raw)
        action = event["action"]
        if (type(event["sequence"]) is not int or event["sequence"] != sequence
                or event["comparison_id"] != manifest["comparison_id"]
                or action not in STATES or event["state"] != STATES[action]
                or event["previous_state"] != state or event["state"] == state
                or event["previous_event_sha256"] != previous_hash
                or event["mode"] != "offline_record_only" or event["krita_applied"] is not False
                or not isinstance(event["reason"], str) or not event["reason"].strip()
                or not isinstance(event["recorded_at"], str)):
            raise ValueError("事件链无效")
        events.append(event)
        state, previous_hash = event["state"], _digest(raw)
    return manifest, events, state, previous_hash


def show(output):
    """Reopen saved assets/events even when original input paths no longer exist."""
    with _directory(output) as (fd, _):
        fcntl.flock(fd, fcntl.LOCK_SH)
        manifest, events, state, _ = _load(fd)
    return dict(manifest=manifest, events=events, state=state, sequence=len(events), notice=NOTICE)


def decide(output, action, reason, expect_sequence):
    """Record only; hide/withdraw never changes Krita visibility or projection."""
    if action not in STATES or not isinstance(reason, str) or not reason.strip():
        raise ValueError("决定必须为 accept/reject/hide/withdraw，且须提供非空理由")
    if type(expect_sequence) is not int or expect_sequence < 0:
        raise ValueError("必须提供非负 expect-sequence")
    if len(reason.encode("utf-8")) > 64 * 1024:
        raise ValueError("理由过长")
    with _directory(output) as (fd, _):
        fcntl.flock(fd, fcntl.LOCK_EX)
        manifest, events, state, previous_hash = _load(fd)
        if len(events) != expect_sequence:
            raise ValueError("事件序号冲突；请先 compare show 重开查询")
        if state == STATES[action]:
            raise ValueError("该决定不改变离线状态；未追加事件")
        captures = [_capture(manifest["sources"][key]["path"]) for key in ("before", "after")]
        for key, capture in zip(("before", "after"), captures):
            if capture[0]["sha256"] != manifest["sources"][key]["sha256"]:
                raise ValueError("原始源文件已改变；不能给旧比较追加决定")
        event = dict(comparison_id=manifest["comparison_id"], sequence=len(events) + 1,
                     action=action, previous_state=state, state=STATES[action], reason=reason,
                     recorded_at=_now(), previous_event_sha256=previous_hash,
                     mode="offline_record_only", krita_applied=False)
        name = f"event-{event['sequence']:06d}.json"
        created = False
        try:
            # Reserve our own file before writing so cleanup cannot delete an older result.
            handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            created = True
            with os.fdopen(handle, "wb") as stream:
                stream.write(_json(event))
                stream.flush()
                os.fsync(stream.fileno())
            _unchanged(captures)
            os.fsync(fd)
        except BaseException:
            if created:
                os.unlink(name, dir_fd=fd)
            raise
    return dict(event=event, notice=NOTICE)


def run(args):
    if args.compare_cmd == "create":
        result = create(args.before, args.after, args.output, crop=args.crop,
                        source_version=args.source_version, candidate_version=args.candidate_version,
                        prompt=args.prompt, workflow=args.workflow, seed=args.seed,
                        document_ref=args.document_ref, candidate_layer_ref=args.candidate_layer_ref)
    elif args.compare_cmd == "decide":
        result = decide(args.output, args.action, args.reason, args.expect_sequence)
    else:
        result = show(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
