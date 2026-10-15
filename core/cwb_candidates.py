"""CAD-22: bounded offline plans and boards of explicitly registered PNGs.

No generation, segmentation, network, Krita, or compare internals. Pillow is
optional, imported only for board rendering. Linux dir_fd/O_NOFOLLOW required.
"""
import contextlib
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import warnings

PLAN_SCHEMA = "cwb-candidates-plan-v1"
REGISTRY_SCHEMA = "cwb-candidates-registration-v1"
BOARD_SCHEMA = "cwb-candidates-board-v1"
MAX_COUNT = 64
MAX_JSON = 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_IMAGE_PIXELS = 4_000_000
MAX_TOTAL_PIXELS = 32_000_000
MAX_BOARD_PIXELS = 16_000_000
MAX_SEED = 2**64 - 1
NOTICE = "仅离线计划/人工来源登记；未生成、未分割、未验证概念方向或 mask，未写入 Krita。"


def _text(value, field, nullable=False):
    if nullable and value is None:
        return value
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 8192:
        raise ValueError(field + " 必须为非空文本（最多 8 KiB）")
    return value


def _integer(value, low, high, field):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{field} 必须为 {low}..{high} 整数")
    return value


def _seed(value):
    return None if value is None else _integer(value, 0, MAX_SEED, "seed")


def _absolute(path):
    raw = os.fspath(path)
    if (not isinstance(raw, str) or not raw or "\x00" in raw or raw.endswith("/")
            or "//" in raw or any(p in (".", "..") for p in raw.split("/"))):
        raise ValueError("拒绝空路径、NUL、.、..、重复/末尾分隔符别名")
    return os.path.abspath(raw)


@contextlib.contextmanager
def _parent(path):
    absolute = _absolute(path)
    parts = Path(absolute).parts
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1], absolute
    finally:
        os.close(fd)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _capture(path, limit):
    with _parent(path) as (parent, name, absolute):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError("仅接受无硬链接别名的普通文件: " + absolute)
            if before.st_size > limit:
                raise ValueError("输入超过大小上限: " + absolute)
            with os.fdopen(os.dup(fd), "rb") as stream:
                data = stream.read(limit + 1)
            if len(data) > limit or _signature(before) != _signature(os.fstat(fd)):
                raise ValueError("读取期间来源改变: " + absolute)
            if _signature(before) != _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)):
                raise ValueError("读取期间来源路径被替换: " + absolute)
            return dict(path=absolute, sha256=_sha(data), bytes=len(data)), data, _signature(before)
        finally:
            os.close(fd)


def _unchanged(captures):
    for record, data, signature in captures:
        current, _, sig = _capture(record["path"], max(len(data), 1))
        if sig != signature or current != record:
            raise ValueError("来源改变；拒绝完成: " + record["path"])


def _json(data):
    encoded = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_JSON:
        raise ValueError("JSON 输出超过 1 MiB")
    return encoded


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 重复字段: " + key)
        result[key] = value
    return result


def _load(capture):
    try:
        return json.loads(capture[1], object_pairs_hook=_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("非有限 JSON 数值")))
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("无效或过深 JSON") from exc


def _write_file(fd, name, data):
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(os.dup(handle), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(handle)


def _publish(output, files, manifest, captures):
    # No cleanup/overwrite, even on failure. manifest.json is the completion marker.
    manifest_bytes = _json(manifest)
    _unchanged(captures)
    with _parent(output) as (parent, name, absolute):
        os.mkdir(name, 0o700, dir_fd=parent)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            identity = _signature(os.fstat(fd))[:2]
            for filename, data in files:
                _write_file(fd, filename, data)
            _unchanged(captures)
            with _parent(absolute) as (check_parent, check_name, _):
                if _signature(os.stat(check_name, dir_fd=check_parent, follow_symlinks=False))[:2] != identity:
                    raise ValueError("输出目录在写入期间被替换")
            _write_file(fd, "manifest.json", manifest_bytes)
            os.fsync(fd)
            os.fsync(parent)
        finally:
            os.close(fd)
    return absolute


def _make_plan(prompt, count, seed_start, seed_end, directions, workflow_source):
    _text(prompt, "prompt")
    _integer(count, 1, MAX_COUNT, "count")
    _seed(seed_start)
    _seed(seed_end)
    if seed_start is None or seed_end is None or seed_end < seed_start or seed_end - seed_start + 1 < count:
        raise ValueError("seed 范围必须递增且容纳 count 个不同 seed")
    if not isinstance(directions, list) or len(directions) > MAX_COUNT:
        raise ValueError("direction 最多 64 项")
    for direction in directions:
        _text(direction, "direction")
    _text(workflow_source, "workflow_source", nullable=True)
    return dict(schema=PLAN_SCHEMA, status="prepared_not_executed", notice=NOTICE,
                base_prompt=prompt, count=count, seed_range=[seed_start, seed_end],
                directions=directions, workflow_source=workflow_source,
                provenance="caller_supplied_unverified; null means unknown; no workflow file read",
                candidates=[dict(id=f"C{i + 1:03d}", planned_seed=seed_start + i,
                                 concept_direction=directions[i % len(directions)] if directions else None,
                                 workflow_source=workflow_source, status="prepared_not_executed")
                            for i in range(count)])


def plan(prompt, output, *, count, seed_start, seed_end, directions=None, workflow_source=None):
    """Deterministic consecutive seeds, stable IDs; never executes the plan."""
    manifest = _make_plan(prompt, count, seed_start, seed_end, directions or [], workflow_source)
    absolute = _publish(output, [], manifest, [])
    return dict(output=absolute, status=manifest["status"], count=count, notice=NOTICE)


def _validate_plan(value):
    if not isinstance(value, dict):
        raise ValueError("计划必须为对象")
    try:
        expected = _make_plan(value["base_prompt"], value["count"], *value["seed_range"],
                              value["directions"], value["workflow_source"])
    except (KeyError, TypeError) as exc:
        raise ValueError("计划字段不完整") from exc
    if _json(value) != _json(expected):
        raise ValueError("计划不匹配受支持 schema/稳定编号/seed 契约")
    return value


def _registrations(value, ids):
    if not isinstance(value, dict) or set(value) != {"schema", "candidates"} or value["schema"] != REGISTRY_SCHEMA:
        raise ValueError("登记文件 schema 不匹配")
    entries = value["candidates"]
    if not isinstance(entries, list) or len(entries) != len(ids):
        raise ValueError("必须显式登记每个计划候选，包括失败")
    records = {}
    for entry in entries:
        fields = {"id", "status", "png", "sha256", "seed", "description", "workflow_source", "concept_direction", "failure"}
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError("登记项字段不完整或有未知字段")
        candidate_id = entry["id"]
        if not isinstance(candidate_id, str) or candidate_id not in ids or candidate_id in records:
            raise ValueError("未知/重复候选 ID")
        _seed(entry["seed"])
        _text(entry["description"], "description")
        for field in ("workflow_source", "concept_direction"):
            _text(entry[field], field, nullable=True)
        if entry["status"] == "ready":
            _text(entry["png"], "png")
            digest = entry["sha256"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("ready 项必须显式提供小写 SHA256")
            if entry["failure"] is not None:
                raise ValueError("ready 项 failure 必须为 null")
        elif entry["status"] == "failed":
            _text(entry["failure"], "failure")
            if entry["png"] is not None or entry["sha256"] is not None:
                raise ValueError("failed 项 png/sha256 必须为 null；不会读取失败素材")
        else:
            raise ValueError("status 必须为 ready/failed")
        records[candidate_id] = dict(entry)
    return [records[candidate_id] for candidate_id in ids]


def _pillow():
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise ValueError("candidates board 需要外部 CLI Python 的可选 Pillow；不会安装，不是 Krita 依赖") from exc
    return Image, ImageDraw


class _ResourceLimit(ValueError):
    pass


def _decode(data, Image, budget):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("仅接受单帧 PNG")
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("单图超过 4 百万像素")
                budget[0] -= image.width * image.height
                if budget[0] < 0:
                    raise _ResourceLimit("解码总量超过 32 百万像素（包括无效剪影）")
                if data[24] > 8 or image.mode not in ("RGBA", "LA", "P"):
                    raise ValueError("必须为已有透明 RGBA8/LA8/调色板 PNG；不会生成 mask")
                image.load()
                rgba = image.convert("RGBA")
                try:
                    if rgba.getchannel("A").getextrema()[0] != 0 or rgba.getchannel("A").getextrema()[1] == 0:
                        raise ValueError("必须存在全透明背景和非透明主体")
                    pixels = memoryview(rgba.tobytes())
                    if any(a and (r or g or b) for r, g, b, a in
                           zip(pixels[0::4], pixels[1::4], pixels[2::4], pixels[3::4])):
                        raise ValueError("非透明像素必须已经为纯黑；不会执行剪影/分割算法")
                except BaseException:
                    rgba.close()
                    raise
                return rgba
    except (Image.DecompressionBombWarning, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise ValueError("PNG 解码失败或超出安全限制") from exc


def board(plan_path, registration_path, output, *, columns=4, cell_width=256, cell_height=256,
          selected=None, parent_ref=None):
    """Render explicit existing assets; failed slots remain. No Krita calls."""
    _integer(columns, 1, MAX_COUNT, "columns")
    _integer(cell_width, 32, 2048, "cell_width")
    _integer(cell_height, 32, 2048, "cell_height")
    _text(parent_ref, "parent_ref", nullable=True)
    captures = [_capture(plan_path, MAX_JSON), _capture(registration_path, MAX_JSON)]
    prepared = _validate_plan(_load(captures[0]))
    ids = [entry["id"] for entry in prepared["candidates"]]
    entries = _registrations(_load(captures[1]), ids)
    selected = [] if selected is None else selected
    if (not isinstance(selected, list) or any(not isinstance(item, str) or item not in ids for item in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError("选择含未知/重复 ID")
    rows = math.ceil(len(ids) / columns)
    width, height = columns * cell_width, rows * (cell_height + 24)
    if width * height > MAX_BOARD_PIXELS:
        raise ValueError("拼板超过 16 百万像素")
    # Capture every source before Pillow; path/SHA/alias failures are fatal, not hidden placeholders.
    source_data = {}
    identities = {capture[2][:2] for capture in captures}
    if len(identities) != len(captures):
        raise ValueError("计划与登记不得为同一来源")
    total_bytes = sum(len(capture[1]) for capture in captures)
    for entry in entries:
        if entry["status"] == "failed":
            continue
        capture = _capture(entry["png"], MAX_FILE)
        if capture[0]["sha256"] != entry["sha256"]:
            raise ValueError("来源 SHA256 不匹配: " + entry["id"])
        if capture[2][:2] in identities:
            raise ValueError("拒绝重复来源/文件别名: " + entry["id"])
        identities.add(capture[2][:2])
        total_bytes += len(capture[1])
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("输入总量超过 128 MiB")
        captures.append(capture)
        source_data[entry["id"]] = capture
    Image, ImageDraw = _pillow()
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    files, results = [], []
    pixel_budget = [MAX_TOTAL_PIXELS]
    try:
        for index, entry in enumerate(entries):
            candidate_id = entry["id"]
            x, y = index % columns * cell_width, index // columns * (cell_height + 24)
            result = dict(entry, evidence="caller_supplied_unverified; actual seed/direction/workflow not inferred from plan",
                          cell_xywh=[x, y + 24, cell_width, cell_height], fit="contain", rendered_xywh=None,
                          source=None, asset=None)
            if entry["status"] == "ready":
                capture = source_data[candidate_id]
                result["source"] = capture[0]
                try:
                    image = _decode(capture[1], Image, pixel_budget)
                except _ResourceLimit:
                    raise
                except ValueError as exc:
                    result.update(status="failed", failure=str(exc))
                else:
                    try:
                        scale = min(cell_width / image.width, cell_height / image.height)
                        target = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
                        px, py = x + (cell_width - target[0]) // 2, y + 24 + (cell_height - target[1]) // 2
                        # NEAREST preserves supplied black RGB/alpha values; no mask or shape extraction.
                        resized = image.resize(target, Image.Resampling.NEAREST)
                        try:
                            canvas.alpha_composite(resized, (px, py))
                        finally:
                            resized.close()
                        result.update(asset=candidate_id + ".png", dimensions=list(image.size),
                                      rendered_xywh=[px, py, *target], scale=scale)
                        files.append((result["asset"], capture[1]))
                    finally:
                        image.close()
            failed = result["status"] == "failed"
            draw.rectangle((x, y, x + cell_width - 1, y + 23), fill=(48, 48, 48, 255))
            draw.text((x + 4, y + 4), candidate_id + (" FAILED" if failed else ""), fill="white")
            if failed:
                draw.rectangle((x, y + 24, x + cell_width - 1, y + 23 + cell_height), fill=(235, 220, 220, 255))
                draw.line((x, y + 24, x + cell_width - 1, y + 23 + cell_height), fill=(160, 0, 0, 255), width=2)
                draw.line((x + cell_width - 1, y + 24, x, y + 23 + cell_height), fill=(160, 0, 0, 255), width=2)
            results.append(result)
        by_id = {result["id"]: result for result in results}
        if any(by_id[candidate_id]["status"] != "ready" for candidate_id in selected):
            raise ValueError("不能选择失败候选")
        absolute = _absolute(output)
        selection = dict(schema="cwb-candidates-selection-v1", status="not_applied", krita_applied=False,
                         parent_ref=parent_ref, parent_ref_verified=False, selected=selected,
                         new_layer_payloads=[dict(candidate_id=candidate_id, operation="new_paint_layer_only",
                                                  png_path=absolute + "/" + by_id[candidate_id]["asset"],
                                                  sha256=by_id[candidate_id]["source"]["sha256"],
                                                  name=candidate_id, parent_id=parent_ref,
                                                  offset_x=0, offset_y=0)
                                             for candidate_id in selected], notice=NOTICE)
        stream = io.BytesIO()
        canvas.save(stream, format="PNG")
        png = stream.getvalue()
        if len(png) > MAX_FILE:
            raise ValueError("拼板 PNG 超过 16 MiB")
        failures = sum(result["status"] == "failed" for result in results)
        manifest = dict(schema=BOARD_SCHEMA, status="created_with_failures" if failures else "created",
                        notice=NOTICE, krita_applied=False, failures=failures,
                        plan=captures[0][0], registration=captures[1][0],
                        planned_candidates=prepared["candidates"], candidates=results,
                        display=dict(file="board.png", sha256=_sha(png), dimensions=[width, height],
                                     columns=columns, cell_dimensions=[cell_width, cell_height],
                                     label_height=24, fit="contain", resampling="nearest", background="opaque_white",
                                      compositing="source_over_for_display; original PNG alpha preserved in assets"),
                        selection_file="selection.json")
        files.extend([("board.png", png), ("selection.json", _json(selection))])
        published = _publish(output, files, manifest, captures)
        return dict(output=published, status=manifest["status"], failures=failures, selected=selected, notice=NOTICE)
    finally:
        canvas.close()


def run(args):
    if args.candidates_cmd == "plan":
        result = plan(args.prompt, args.output, count=args.count, seed_start=args.seed_start,
                      seed_end=args.seed_end, directions=args.direction, workflow_source=args.workflow_source)
    else:
        result = board(args.plan, args.registration, args.output, columns=args.columns,
                       cell_width=args.cell_width, cell_height=args.cell_height,
                       selected=args.select, parent_ref=args.parent_ref)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("failures", 0):
        raise SystemExit(2)
