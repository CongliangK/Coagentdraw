"""CAD-24 offline review contracts. No backend, network, Krita or project writes.

Reuse the existing no-follow bounded file primitives; do not invoke compare's
commands/rendering. Pillow is imported only when PNG validation is needed.
"""
import io
import json
import os
from pathlib import Path
import re
import struct
import uuid
import warnings
import zlib

from cwb_compare import (
    _digest, _directory, _json, _load_json, _parent, _read_at,
    _signature, _unchanged, _write_at,
)

SCHEMA = "cwb-review-request-v1"
RESPONSE_SCHEMA = "cwb-review-feedback-v1"
REPORT_SCHEMA = "cwb-review-report-v1"
MAX_JSON = 1024 * 1024
MAX_PNG = 32 * 1024 * 1024
MAX_PNG_CHUNKS = 4096
MAX_PIXELS = 16_000_000
ROLES = ("original", "silhouette", "grayscale")
COORDINATES = "original PNG native pixels; top-left origin; bbox_xywh; no scaling/rotation"
CONTRACT = {
    "schema": RESPONSE_SCHEMA,
    "binding": "request_id + request_sha256 + source_sha256 (all three image roles)",
    "origin": "kind: human|model; name: nonempty; reference: nonempty; user-supplied, not verified",
    "opinions": "0..100; never invent a quota or missing intent/pillars",
    "opinion_fields": ["id", "bbox_xywh", "pillar_ids", "evidence", "uncertainty", "impact",
                       "action", "priority", "classification", "claim_type", "reason"],
    "evidence": "observations: text; source_roles: nonempty role list; inference: text (separate from observation)",
    "uncertainty": "level: low|medium|high; reason: nonempty text",
    "classification": "must_change|suggestion|personal_preference (with reason)",
    "claim_type": "intent_conflict|hypothesis|subjective_preference; preference iff personal_preference",
    "priority": "P0|P1|P2; priority is a supplied opinion, not an objective finding",
    "decisions": "0..1 per opinion: opinion_id, action(confirm|amend|reject), actor, reason; amend adds full amended_opinion with same id",
    "epistemic_limit": "All evidence/claims unverified; subjective taste is not an empirically proven defect",
}
NOTICE = ("仅本地接口准备/手工反馈导入；未调用或验证审评后端，未验证艺术效果，"
          "未修改作品/生成画改提示，未接入 #18/#19/#25，不能关闭 #24。")


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError("缺少字段或包含未知字段: " + ", ".join(required))


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 16 * 1024:
        raise ValueError("需要非空文本（最多 16 KiB）")
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("ID 必须为 1..64 位 ASCII 字母/数字/_/- 且以字母开头")
    return value


def _capture_small(path, limit):
    with _parent(path) as (fd, name, absolute):
        data, signature = _read_at(fd, name, limit)
    return {"path": absolute, "sha256": _digest(data), "bytes": len(data)}, data, signature


def _json_capture(path):
    capture = _capture_small(path, MAX_JSON)
    try:
        value = _load_json(capture[1])
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("JSON 编码或嵌套深度无效") from exc
    return value, capture


def _context(value):
    _keys(value, ("intent", "pillars"))
    if value["intent"] is not None:
        _text(value["intent"])
    if not isinstance(value["pillars"], list) or len(value["pillars"]) > 50:
        raise ValueError("pillars 必须是最多 50 项的列表；未知用 []")
    ids = set()
    for pillar in value["pillars"]:
        _keys(pillar, ("id", "text"))
        _identifier(pillar["id"])
        _text(pillar["text"])
        if pillar["id"] in ids:
            raise ValueError("重复支柱 ID")
        ids.add(pillar["id"])
    return value


def _png_container(data):
    """Bounded static-PNG framing checks, not a replacement pixel decoder.

    Inspect only the already captured bytes; Pillow tolerates some missing/bad
    IEND CRCs and unknown critical chunks. Do not normalize or reopen sources.
    """
    if len(data) > MAX_PNG or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("无效/超限 PNG 签名或字节数")
    offset, count, palette_entries = 8, 0, 0
    seen, idat_closed = set(), False
    before_palette = {b"cHRM", b"gAMA", b"iCCP", b"sBIT", b"sRGB"}
    before_idat = before_palette | {b"bKGD", b"hIST", b"tRNS", b"pHYs"}
    view = memoryview(data)
    while offset < len(data):
        count += 1
        if count > MAX_PNG_CHUNKS or len(data) - offset < 12:
            raise ValueError("PNG chunk 过多或边界/CRC 截断")
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + length + 12
        if length > 0x7fffffff or end > len(data):
            raise ValueError("PNG chunk 长度越界/截断")
        if (any(not (65 <= c <= 90 or 97 <= c <= 122) for c in kind)
                or kind[2] & 32):
            raise ValueError("PNG chunk 类型/保留位无效")
        crc = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(view[offset + 4:end - 4]) & 0xffffffff != crc:
            raise ValueError("PNG chunk CRC 错误")
        if count == 1 and kind != b"IHDR":
            raise ValueError("PNG 必须以唯一 IHDR 开始")
        if kind in (b"acTL", b"fcTL", b"fdAT"):
            raise ValueError("仅接受静态 PNG；拒绝全部 APNG（包括单帧）")
        if not kind[0] & 32 and kind not in (b"IHDR", b"PLTE", b"IDAT", b"IEND"):
            raise ValueError("PNG 未知 critical chunk")
        if kind in before_idat:
            if kind in seen or b"IDAT" in seen or (kind in before_palette and b"PLTE" in seen):
                raise ValueError("PNG 元数据 chunk 重复/顺序无效")
            if kind == b"hIST" and not palette_entries:
                raise ValueError("PNG hIST 必须在 PLTE 之后")
            if kind == b"bKGD" and color == 3 and not palette_entries:
                raise ValueError("索引 PNG bKGD 必须在 PLTE 之后")
        if kind == b"IHDR":
            if count != 1 or length != 13:
                raise ValueError("PNG IHDR 必须唯一且长度为 13")
            width, height, depth, color, compression, filtering, interlace = struct.unpack_from(">IIBBBBB", data, offset + 8)
            depths = {0: (1, 2, 4, 8), 2: (8,), 3: (1, 2, 4, 8), 4: (8,), 6: (8,)}
            if (not width or not height or max(width, height) > 16384 or width * height > MAX_PIXELS
                    or depth not in depths.get(color, ()) or compression or filtering or interlace not in (0, 1)):
                raise ValueError("PNG IHDR 无效/超限；仅接受 <=8 位静态 PNG")
        elif kind == b"PLTE":
            if (palette_entries or b"IDAT" in seen or seen & {b"tRNS", b"bKGD", b"hIST"}
                    or color in (0, 4) or not 0 < length <= 768 or length % 3
                    or (color == 3 and length // 3 > 2 ** depth)):
                raise ValueError("PNG PLTE 长度/色型/顺序无效")
            palette_entries = length // 3
        elif kind == b"tRNS":
            if not ((color == 0 and length == 2) or (color == 2 and length == 6)
                    or (color == 3 and 0 < length <= palette_entries)):
                raise ValueError("PNG tRNS 长度/色型/顺序无效")
        elif kind == b"IDAT":
            if idat_closed or (color == 3 and not palette_entries):
                raise ValueError("PNG IDAT 必须连续；索引图必须先有 PLTE")
        elif kind == b"IEND":
            if length or b"IDAT" not in seen or end != len(data):
                raise ValueError("PNG IEND 必须空数据、CRC 完整且唯一末尾；必须有 IDAT")
            return [width, height]
        if kind != b"IDAT" and b"IDAT" in seen:
            idat_closed = True
        seen.add(kind)
        offset = end
    raise ValueError("PNG 缺失完整 IEND")


def _png_dimensions(data):
    dimensions = _png_container(data)
    # CLI-only optional dependency. Never import for --help or read credentials.
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("review prepare/import 的 PNG 校验需要可选 CLI Pillow；不会安装或联网") from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if (image.format != "PNG" or getattr(image, "n_frames", 1) != 1
                        or list(image.size) != dimensions
                        or image.mode not in ("1", "L", "LA", "P", "RGB", "RGBA")):
                    raise ValueError("只接受 <=16M 像素、边长<=16384 的静态 <=8 位 PNG")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()  # detect truncated pixel streams as well as bad chunks
        return dimensions
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError) as exc:
        raise ValueError("无效/超限 PNG") from exc


def _publish(output, files, captures):
    """Exclusive directory + final hash receipt; never reuse partial/old outputs."""
    _unchanged(captures)
    with _parent(output) as (parent, name, absolute):
        os.mkdir(name, 0o700, dir_fd=parent)
        fd, owned = None, []
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            identity = _signature(os.fstat(fd))[:2]
            for filename, data in files.items():
                _write_at(fd, filename, data)
                owned.append(filename)
            _unchanged(captures)
            receipt = {"schema": "cwb-review-complete-v1", "files": {k: _digest(v) for k, v in files.items()}}
            _write_at(fd, "complete.json", _json(receipt))
            owned.append("complete.json")
            _unchanged(captures)
            os.fsync(fd)
            if _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))[:2] != identity:
                raise ValueError("输出目录路径已改变；未记录成功")
            os.fsync(parent)
            with _directory(absolute) as (check, _):
                if _signature(os.fstat(check))[:2] != identity:
                    raise ValueError("输出父路径已改变；未记录成功")
        except BaseException:
            if fd is not None:
                # All names are fixed, exclusively created, and anchored to our fd.
                for filename in owned:
                    os.unlink(filename, dir_fd=fd)
                if _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))[:2] == _signature(os.fstat(fd))[:2]:
                    os.rmdir(name, dir_fd=parent)
            raise
        finally:
            if fd is not None:
                os.close(fd)
    return absolute


def prepare(original, silhouette, grayscale, context, output):
    intent, context_capture = _json_capture(context)
    _context(intent)
    captures = [_capture_small(path, MAX_PNG) for path in (original, silhouette, grayscale)]
    if len({c[2][:2] for c in captures}) != 3:
        raise ValueError("三个角色图片必须是独立文件，拒绝路径别名")
    dimensions = [_png_dimensions(c[1]) for c in captures]
    if any(d != dimensions[0] for d in dimensions):
        raise ValueError("原图/剪影/灰阶尺寸必须完全相同；不会生成诊断图或映射坐标")
    template_path = str(Path(__file__).resolve().parent.parent / "docs/development/cad24_review_prompt.md")
    template_capture = _capture_small(template_path, MAX_JSON)
    request = dict(schema=SCHEMA, status="prepared_not_executed", request_id=str(uuid.uuid4()),
                   coordinate_system=COORDINATES, context=intent, feedback_contract=CONTRACT,
                   prompt=template_capture[1].decode("utf-8"), images={})
    files = {}
    for role, captured, size in zip(ROLES, captures, dimensions):
        request["images"][role] = dict(captured[0], dimensions=size, signature=list(captured[2]), asset=role + ".png")
        files[role + ".png"] = captured[1]
    raw = _json(request)
    if len(raw) > MAX_JSON:
        raise ValueError("请求超过 1 MiB")
    files["request.json"] = raw
    absolute = _publish(output, files, captures + [context_capture, template_capture])
    return dict(output=absolute, status=request["status"], request_id=request["request_id"],
                request_sha256=_digest(raw), notice=NOTICE)


def _load_prepared(prepared):
    captures = []
    with _directory(prepared) as (fd, absolute):
        expected = {"request.json", *(r + ".png" for r in ROLES)}
        if set(os.listdir(fd)) != expected | {"complete.json"}:
            raise ValueError("准备目录缺失/包含未知工件")
        receipt_raw, sig = _read_at(fd, "complete.json", MAX_JSON)
        captures.append(({"path": absolute + "/complete.json", "sha256": _digest(receipt_raw)}, receipt_raw, sig))
        receipt = _load_json(receipt_raw)
        _keys(receipt, ("schema", "files"))
        if receipt["schema"] != "cwb-review-complete-v1" or not isinstance(receipt["files"], dict) or set(receipt["files"]) != expected:
            raise ValueError("不完整的准备输出")
        assets = {}
        for name in sorted(expected):
            data, sig = _read_at(fd, name, MAX_JSON if name.endswith(".json") else MAX_PNG)
            if _digest(data) != receipt["files"][name]:
                raise ValueError("准备工件摘要改变: " + name)
            assets[name] = data
            captures.append(({"path": absolute + "/" + name, "sha256": _digest(data)}, data, sig))
    request = _load_json(assets["request.json"])
    _keys(request, ("schema", "status", "request_id", "coordinate_system", "context", "feedback_contract", "prompt", "images"))
    if (request["schema"] != SCHEMA or request["status"] != "prepared_not_executed"
            or request["coordinate_system"] != COORDINATES or request["feedback_contract"] != CONTRACT):
        raise ValueError("不支持的请求契约/状态")
    if not isinstance(request["request_id"], str):
        raise ValueError("请求 ID 必须为 UUID 文本")
    uuid.UUID(request["request_id"])
    _text(request["prompt"])
    _context(request["context"])
    _keys(request["images"], ROLES)
    sizes = []
    for role in ROLES:
        record = request["images"][role]
        _keys(record, ("path", "sha256", "bytes", "dimensions", "signature", "asset"))
        if (record["asset"] != role + ".png" or not isinstance(record["path"], str)
                or not os.path.isabs(record["path"])
                or not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
                or not isinstance(record["dimensions"], list) or len(record["dimensions"]) != 2
                or any(type(v) is not int or v <= 0 for v in record["dimensions"])
                or not isinstance(record["signature"], list) or len(record["signature"]) != 5
                or any(type(v) is not int or v < 0 for v in record["signature"])):
            raise ValueError("非法图片来源记录/工件名")
        original = _capture_small(record["path"], MAX_PNG)
        data = assets[role + ".png"]
        size = _png_dimensions(data)
        if (original[0]["sha256"] != record["sha256"] or list(original[2]) != record["signature"]
                or original[1] != data or type(record["bytes"]) is not int or record["bytes"] != len(data)
                or record["dimensions"] != size):
            raise ValueError("原图来源/尺寸已改变；拒绝旧反馈")
        sizes.append(size)
        captures.append(original)
    if any(size != sizes[0] for size in sizes):
        raise ValueError("图片尺寸不匹配")
    if len({tuple(request["images"][r]["signature"][:2]) for r in ROLES}) != 3:
        raise ValueError("图片来源路径别名")
    return request, assets["request.json"], captures


def _opinion(opinion, request):
    _keys(opinion, CONTRACT["opinion_fields"])
    _identifier(opinion["id"])
    box = opinion["bbox_xywh"]
    width, height = request["images"]["original"]["dimensions"]
    if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
            or min(box[:2]) < 0 or min(box[2:]) <= 0
            or box[0] + box[2] > width or box[1] + box[3] > height):
        raise ValueError("bbox 必须为原图界内整数 x/y/width/height")
    pillars = opinion["pillar_ids"]
    valid = {p["id"] for p in request["context"]["pillars"]}
    if (not isinstance(pillars, list) or not pillars or any(not isinstance(p, str) for p in pillars)
            or len(set(pillars)) != len(pillars) or not set(pillars) <= valid):
        raise ValueError("意见必须引用已提供的设计支柱 ID")
    if request["context"]["intent"] is None:
        raise ValueError("未提供意图；不得编造意图支持意见，可导入空意见")
    evidence = opinion["evidence"]
    _keys(evidence, ("observations", "source_roles", "inference"))
    _text(evidence["observations"])
    _text(evidence["inference"])
    roles = evidence["source_roles"]
    if (not isinstance(roles, list) or not roles or any(not isinstance(r, str) for r in roles)
            or len(set(roles)) != len(roles) or not set(roles) <= set(ROLES)):
        raise ValueError("证据必须引用明确图片角色")
    uncertainty = opinion["uncertainty"]
    _keys(uncertainty, ("level", "reason"))
    if uncertainty["level"] not in ("low", "medium", "high"):
        raise ValueError("无效不确定性等级")
    _text(uncertainty["reason"])
    for field in ("impact", "action", "reason"):
        _text(opinion[field])
    if (opinion["priority"] not in ("P0", "P1", "P2")
            or opinion["classification"] not in ("must_change", "suggestion", "personal_preference")
            or opinion["claim_type"] not in ("intent_conflict", "hypothesis", "subjective_preference")):
        raise ValueError("无效优先级/三分类/主张类型")
    if ((opinion["classification"] == "personal_preference") != (opinion["claim_type"] == "subjective_preference")
            or (opinion["classification"] == "must_change" and opinion["claim_type"] != "intent_conflict")):
        raise ValueError("主观偏好不得标为必须改/实证缺陷；必须改需明确意图冲突")
    return opinion


def _feedback(value, request, request_raw):
    _keys(value, ("schema", "request_id", "request_sha256", "source_sha256", "origin", "opinions", "decisions"))
    if (value["schema"] != RESPONSE_SCHEMA or value["request_id"] != request["request_id"]
            or value["request_sha256"] != _digest(request_raw)
            or value["source_sha256"] != {r: request["images"][r]["sha256"] for r in ROLES}):
        raise ValueError("反馈请求/图片来源绑定不匹配")
    _keys(value["origin"], ("kind", "name", "reference"))
    if value["origin"]["kind"] not in ("human", "model"):
        raise ValueError("来源必须显式为 human 或 model（均未验证）")
    _text(value["origin"]["name"])
    _text(value["origin"]["reference"])
    if not isinstance(value["opinions"], list) or len(value["opinions"]) > 100:
        raise ValueError("意见必须为 0..100 项列表；没有三条配额")
    opinions = {}
    for supplied in value["opinions"]:
        item = _opinion(supplied, request)
        if item["id"] in opinions:
            raise ValueError("重复意见 ID")
        opinions[item["id"]] = dict(original_opinion=item, effective_opinion=item,
                                    state="pending", decision_history=[])
    if not isinstance(value["decisions"], list) or len(value["decisions"]) > len(opinions):
        raise ValueError("人工决定必须为列表；每条意见至多一个决定")
    for decision in value["decisions"]:
        _keys(decision, ("opinion_id", "action", "actor", "reason"), ("amended_opinion",))
        _text(decision["actor"])
        _text(decision["reason"])
        key = decision["opinion_id"]
        if not isinstance(key, str) or key not in opinions or opinions[key]["decision_history"]:
            raise ValueError("人工决定引用未知/重复意见")
        action = decision["action"]
        if action not in ("confirm", "amend", "reject") or ((action == "amend") != ("amended_opinion" in decision)):
            raise ValueError("人工决定必须为 confirm/amend/reject；仅 amend 需要完整修改意见")
        item = opinions[key]
        if action == "amend":
            amendment = _opinion(decision["amended_opinion"], request)
            if amendment["id"] != key:
                raise ValueError("人工修改必须保留稳定意见 ID")
            item["effective_opinion"] = amendment
        item["state"] = {"confirm": "confirmed", "amend": "amended", "reject": "rejected"}[action]
        item["decision_history"].append(decision)
    return list(opinions.values())


def _markdown(report):
    # All external strings are JSON-quoted inside indented blocks, never active Markdown.
    def block(value):
        return ["    " + line for line in json.dumps(value, ensure_ascii=False, indent=2).splitlines()]
    lines = ["# 离线审评导入", "", "status: response_user_supplied", "", NOTICE,
             "", "来源声明（未验证，非实际模型调用证据）：", "", *block(report["origin"]),
             "", "坐标：" + COORDINATES, "", "设计意图与支柱（用户提供，null/[] 表示未知）：", "",
             *block(report["context"])]
    labels = {"bbox_xywh": "位置（原图像素）", "pillar_ids": "设计支柱引用", "classification": "三分类",
              "claim_type": "主张类型（未验证）", "reason": "分类理由", "evidence": "可观察证据与推断（未验证）",
              "uncertainty": "不确定性", "impact": "影响", "action": "可执行修改（仅记录）", "priority": "优先级"}
    for item in report["opinions"]:
        lines += ["", "## " + item["original_opinion"]["id"] + " — " + item["state"],
                  "", "当前意见（rejected 仅记录，不删除原意见）："]
        for field, label in labels.items():
            lines += ["", "### " + label, "", *block(item["effective_opinion"][field])]
        lines += ["", "### 原意见（保留）", "", *block(item["original_opinion"]),
                  "", "### 人工决定历史（用户手工声明，未认证）", "", *block(item["decision_history"])]
    return ("\n".join(lines) + "\n").encode("utf-8")


def import_feedback(prepared, feedback, output):
    request, request_raw, captures = _load_prepared(prepared)
    value, feedback_capture = _json_capture(feedback)
    opinions = _feedback(value, request, request_raw)
    report = dict(schema=REPORT_SCHEMA, status="response_user_supplied", request_id=request["request_id"],
                  request_sha256=_digest(request_raw), origin=value["origin"],
                  origin_verification="user_supplied_unverified", context=request["context"],
                  coordinate_system=COORDINATES, source_sha256=value["source_sha256"],
                  feedback_sha256=feedback_capture[0]["sha256"], opinions=opinions,
                  backend_executed=False, artwork_modified=False, generated_prompt=False, notice=NOTICE)
    files = {"review.json": _json(report), "review.md": _markdown(report),
             "supplied-feedback.json": feedback_capture[1], "request.json": request_raw}
    absolute = _publish(output, files, captures + [feedback_capture])
    return dict(output=absolute, status="response_user_supplied", opinions=len(opinions), notice=NOTICE)


def run(args):
    try:
        if args.review_cmd == "prepare":
            result = prepare(args.original, args.silhouette, args.grayscale, args.context, args.output)
        else:
            result = import_feedback(args.prepared, args.feedback, args.output)
    except (ValueError, OSError, KeyError, TypeError, RecursionError) as exc:
        raise SystemExit("review: " + str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
