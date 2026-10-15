"""CAD-28 offline six-image layout. No network; Pillow is render/prepare CLI-only.

Pure placement/vector files are loaded without importing the Krita plugin package.
All outputs are exclusive new assets; an interrupted bundle remains incomplete.
"""
import contextlib
from fractions import Fraction
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import xml.etree.ElementTree as ET

ROLES = ("main", "front", "side", "back", "equipment1", "equipment2")
REFERENCES = ("head", "shoulder", "waist", "knee")
SCHEMA = "cwb-layout-v1"
MAX_TOTAL_BYTES = 64 * 1024 * 1024
NOTICE = "仅已有图排版/人工校对；不生成三视图、不证明一致性；Krita 编辑/字体/落点/保存重开未验。"


def _pure(name):
    path = Path(__file__).parent / "krita_cowork" / (name + ".py")
    spec = importlib.util.spec_from_file_location("cwb_layout_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _absolute(path):
    raw = os.fspath(path)
    if (not isinstance(raw, str) or not raw or "\x00" in raw or
            any(p in (".", "..") for p in raw.split("/")) or "//" in raw or raw.endswith("/")):
        raise ValueError("拒绝空路径/路径别名")
    return os.path.abspath(raw)


@contextlib.contextmanager
def _parent(path):
    path = _absolute(path)
    parts = Path(path).parts
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd, parts[-1], path
    finally:
        os.close(fd)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _capture(path, limit):
    with _parent(path) as (fd, name, absolute):
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(handle)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise ValueError("只接受预算内无链接别名的普通文件")
            with os.fdopen(os.dup(handle), "rb") as stream:
                data = stream.read(limit + 1)
            if len(data) != before.st_size or _signature(before) != _signature(os.fstat(handle)):
                raise ValueError("读取期间源改变")
            record = {"path": absolute, "sha256": _sha(data), "bytes": len(data)}
            return record, data, _signature(before)
        finally:
            os.close(handle)


def _unchanged(captures):
    for record, _, signature in captures:
        current, data, actual = _capture(record["path"], MAX_TOTAL_BYTES)
        del data
        if current != record or actual != signature:
            raise ValueError("来源已改变；拒绝继续: " + record["path"])


def _write(fd, name, data):
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("JSON 重复字段: " + key)
        result[key] = value
    return result


def _load(data):
    return json.loads(data, object_pairs_hook=_pairs,
                      parse_constant=lambda s: (_ for _ in ()).throw(ValueError("非有限 JSON: " + s)))


def _keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(label + " 字段必须恰为 " + ",".join(keys))


def _point(value, width, height, label):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(label + " 必须为 [x,y]")
    for v, bound in zip(value, (width, height)):
        if type(v) is not int or not 0 <= v <= bound:
            raise ValueError(label + " 必须为界内整数")
    return value


def _annotation(spec, manifest, vector):
    w, h = manifest["canvas"]
    dpi = manifest["dpi"]
    root = ET.Element("svg", {"xmlns": vector.NS, "width": format(w * 72 / dpi[0], ".10f") + "pt",
                              "height": format(h * 72 / dpi[1], ".10f") + "pt",
                              "viewBox": "0 0 {0} {1}".format(w, h)})

    def line(x1, y1, x2, y2):
        ET.SubElement(root, "line", {"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2),
                                     "stroke": "#657580", "stroke-width": "1"})

    def text(x, y, content):
        ET.SubElement(root, "text", {"x": str(x), "y": str(y), "fill": "#202830",
                                     "font-size": "12", "font-family": "sans-serif"}).text = content

    views = [item for item in manifest["images"] if item["role"] in ("front", "side", "back")]
    left = min(v["cell"][0] for v in views)
    right = max(v["cell"][0] + v["cell"][2] for v in views)
    for name in REFERENCES:
        y = spec["references"][name]
        line(left, y, right, y)
        text(left, y, name)
    for item in manifest["images"]:
        x, y, cw, ch = item["cell"]
        ET.SubElement(root, "rect", {"x": str(x), "y": str(y), "width": str(cw), "height": str(ch),
                                     "fill": "none", "stroke": "#657580", "stroke-width": "1"})
        text(x, y + 12, item["role"])
    for callout in spec["callouts"]:
        _keys(callout, ("text", "label", "target"), "callout")
        label = _point(callout["label"], w, h, "callout label")
        target = _point(callout["target"], w, h, "callout target")
        if not isinstance(callout["text"], str):
            raise ValueError("callout text 必须为字符串")
        text(*label, callout["text"])
        line(*label, *target)  # Simple independent line, no marker/resource URL.
    svg = ET.tostring(root, encoding="unicode")
    vector.validate_svg(svg, manifest["canvas"], dpi)
    return svg


def _prepare_png(placement, capture, width, height, budget):
    # Pin the no-follow regular source; the decoder never reopens a mutable path.
    with _parent(capture[0]["path"]) as (fd, basename, _):
        handle = os.open(basename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            if _signature(os.fstat(handle)) != capture[2]:
                raise ValueError("PNG 来源已替换")
            encoded, mapping = placement.prepare_png("/proc/self/fd/" + str(handle), width, height,
                                                     "fit_canvas", budget=budget)
            if _signature(os.fstat(handle)) != capture[2]:
                raise ValueError("PNG 预检期间源改变")
            return encoded, mapping
        finally:
            os.close(handle)


def _collect(spec_path):
    placement, vector = _pure("placement"), _pure("vector")
    source = _capture(spec_path, 65536)
    spec = _load(source[1])
    _keys(spec, ("schema", "unit", "canvas", "dpi", "fixture", "images", "references", "callouts"), "spec")
    if spec["schema"] != SCHEMA or spec["unit"] != "px" or type(spec["fixture"]) is not bool:
        raise ValueError("未知 schema/unit 或 fixture 非 bool")
    canvas, dpi = spec["canvas"], spec["dpi"]
    # Validate canvas and physical mapping before touching image sources.
    if not isinstance(canvas, list) or len(canvas) != 2:
        raise ValueError("canvas 必须为两轴整数")
    for dim in canvas:
        placement.integer(dim, "canvas", 1)
    if not isinstance(dpi, list) or len(dpi) != 2:
        raise ValueError("dpi 必须显式两轴")
    for value in dpi:
        vector.number(value, "dpi", 1, 2400)
    if dpi[0] != dpi[1]:
        raise ValueError("仅支持方形 DPI")
    _keys(spec["images"], ROLES, "images")
    _keys(spec["references"], REFERENCES, "references")
    refs = [spec["references"][r] for r in REFERENCES]
    if any(type(y) is not int or not 0 <= y <= canvas[1] for y in refs) or refs != sorted(set(refs)):
        raise ValueError("head/shoulder/waist/knee 必须依次向下且不重合")
    if not isinstance(spec["callouts"], list) or not 2 <= len(spec["callouts"]) <= 8:
        raise ValueError("需要 2..8 个短 callout")
    manifest = {"schema": SCHEMA, "canvas": canvas, "dpi": dpi, "unit": "px",
                "fixture": spec["fixture"], "notice": NOTICE,
                "spec": source[0], "references": spec["references"], "callouts": spec["callouts"], "images": [],
                "anchor_rounding": "floor_source_edge_then_integer_translation",
                "krita_verified": False}
    captures = [source]
    view_scales, view_cells, view_anchors = [], [], []
    total = len(source[1])
    for role in ROLES:
        item = spec["images"][role]
        _keys(item, ("path", "cell", "anchor_source", "anchor_target"), role)
        cell = item["cell"]
        if not isinstance(cell, list) or len(cell) != 4:
            raise ValueError("cell 必须为 x/y/w/h")
        x, y, cw, ch = cell
        placement.integer(x, "cell x")
        placement.integer(y, "cell y")
        placement.integer(cw, "cell width", 1)
        placement.integer(ch, "cell height", 1)
        if x + cw > canvas[0] or y + ch > canvas[1]:
            raise ValueError("cell 越出画布")
        for previous in manifest["images"]:
            px, py, pw, ph = previous["cell"]
            if x < px + pw and px < x + cw and y < py + ph and py < y + ch:
                raise ValueError("六个视图 cell 不得重叠")
        source_path = item["path"]
        if not isinstance(source_path, str):
            raise ValueError("image path 必须为文字")
        # Reject spelling aliases BEFORE Path discards ./ or repeated slashes.
        _absolute(source_path)
        if not os.path.isabs(source_path):
            source_path = str(Path(source[0]["path"]).parent / source_path)
        budget = placement.PreprocessBudget()
        capture = _capture(source_path, placement.MAX_PNG_BYTES)
        total += len(capture[1])
        if total > MAX_TOTAL_BYTES:
            raise ValueError("六图来源总字节预算 64MiB 超限")
        # Reuse CAD-14 public admission/PNG normalization and contain geometry.
        encoded, mapping = _prepare_png(placement, capture, cw, ch, budget)
        sw, sh = mapping["source_size"]
        anchor = _point(item["anchor_source"], sw, sh, "source anchor")
        target = _point(item["anchor_target"], *canvas, "target anchor")
        w, h = mapping["output_size"]
        ox, oy = target[0] - anchor[0] * w // sw, target[1] - anchor[1] * h // sh
        if ox < x or oy < y or ox + w > x + cw or oy + h > y + ch:
            raise ValueError("人工锚点平移后超出 cell")
        # Imported PNG is pre-scaled; final placement is CAD-14 insert_at_offset.
        final = placement.plan_placement(w, h, *canvas, "insert_at_offset", ox, oy)
        if role in ("front", "side", "back"):
            view_scales.append(Fraction(*mapping["nominal_scale"]))
            view_cells.append([cw, ch])
            view_anchors.append([target[0] - x, target[1] - y])
            if target[1] != spec["references"]["head"]:
                raise ValueError("三视图人工头顶锚点必须落在 head 参考线")
            if any(not y <= ref <= y + ch for ref in refs):
                raise ValueError("头顶/肩/腰/膝参考线必须在统一视图 cell 高度内")
        error = [Fraction(a * size, source_size) % 1
                 for a, size, source_size in zip(anchor, (w, h), (sw, sh))]
        manifest["images"].append({"role": role, "cell": cell, "source": capture[0],
                                    "anchor_source": anchor, "anchor_target": target,
                                    "anchor_error_px": [[v.numerator, v.denominator] for v in error],
                                    "resize": mapping, "placement": final,
                                    "original": role + ".original.png", "rendered": role + ".png"})
        # Only identities survive between images; never retain six decoded or
        # compressed snapshots together. render recaptures one source at a time.
        captures.append((capture[0], b"", capture[2]))
        del capture, encoded
    if len(set(view_scales)) != 1 or any(c != view_cells[0] for c in view_cells):
        raise ValueError("前/侧/背须同 cell 尺寸、同名义缩放；请人工预备共同像素尺度，不自动猜人体比例")
    if any(a != view_anchors[0] for a in view_anchors):
        raise ValueError("三视图 cell 内人工锚点不统一")
    manifest["view_scale"] = [view_scales[0].numerator, view_scales[0].denominator]
    svg = _annotation(spec, manifest, vector)
    manifest["svg_sha256"] = _sha(svg.encode())
    _unchanged(captures)
    return manifest, svg, captures


def plan(spec_path):
    return _collect(spec_path)[0]


def render(spec_path, output):
    manifest, svg, captures = _collect(spec_path)
    # Optional dependency never enters the plugin, plan, fixture or help paths.
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("layout render 需要 CLI-only 可选 Pillow；不会自动安装") from exc
    placement = _pure("placement")
    _unchanged(captures)
    with _parent(output) as (parent, name, absolute):
        os.mkdir(name, 0o700, dir_fd=parent)  # exclusive; never overwrites old output
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            for item, expected in zip(manifest["images"], captures[1:]):
                budget = placement.PreprocessBudget()
                capture = _capture(item["source"]["path"], placement.MAX_PNG_BYTES)
                if capture[0] != expected[0] or capture[2] != expected[2]:
                    raise ValueError("渲染前来源改变")
                encoded, mapping = _prepare_png(placement, capture, *item["cell"][2:], budget)
                if mapping != item["resize"]:
                    raise ValueError("渲染前布局改变")
                stream = io.BytesIO(encoded)
                del encoded
                with Image.open(stream) as image:
                    image.load()
                    budget.check("Pillow 解码后")
                    rgba = image.convert("RGBA")
                    budget.check("RGBA 转换后")
                    if list(rgba.size) != mapping["source_size"]:
                        raise ValueError("解码尺寸与预检不符")
                image.close()
                stream.close()
                raw = placement.resize_rgba(rgba.tobytes(), mapping, budget=budget)
                rgba.close()
                result = Image.frombytes("RGBA", tuple(mapping["output_size"]), raw)
                del raw
                stream = io.BytesIO()
                result.save(stream, format="PNG")
                result.close()
                data = stream.getvalue()
                stream.close()
                item["rendered_sha256"] = _sha(data)
                budget.check("编码后/写资产前")
                _write(fd, item["original"], capture[1])
                _write(fd, item["rendered"], data)
                del capture, data
            _write(fd, "source.json", captures[0][1])
            _write(fd, "annotations.svg", svg.encode())
            _unchanged(captures)
            _write(fd, "manifest.json", _json(manifest))  # completeness marker, last
        finally:
            os.close(fd)
    return {"output": absolute, "fixture": manifest["fixture"], "notice": NOTICE}


def _rgba(encoded, size, budget):
    """Decode only the CAD-14 checked snapshot; CLI-only optional dependency."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("layout prepare 需要 CLI-only 可选 Pillow 核验像素；不会自动安装") from exc
    try:
        with io.BytesIO(encoded) as stream, Image.open(stream) as image:
            if image.format != "PNG" or list(image.size) != size:
                raise ValueError("PNG 解码尺寸/格式与已核布局不符")
            image.load()
            budget.check("prepare PNG 解码后")
            with image.convert("RGBA") as rgba:
                budget.check("prepare RGBA 转换后")
                # As in render, release decoder storage before packing bytes;
                # never hold decoder + RGBA + packed whole-source copies.
                image.close()
                data = rgba.tobytes()
        budget.check("prepare RGBA 打包后")
        return data
    except (OSError, SyntaxError) as exc:
        raise ValueError("PNG 像素解码失败") from exc


def _verify_rendered(placement, source, rendered, item):
    # Local consistency, NOT authentication against a same-UID writer. Derive
    # expected bytes from the rechecked original source and production CAD-14
    # sampler; a rewritten manifest SHA cannot authorize different pixels.
    budget = placement.PreprocessBudget()
    encoded, mapping = _prepare_png(placement, source, *item["cell"][2:], budget)
    if mapping != item["resize"]:
        raise ValueError("prepare 重新采样布局改变")
    raw = _rgba(encoded, mapping["source_size"], budget)
    del encoded
    expected = placement.resize_rgba(raw, mapping, budget=budget)
    del raw
    size = mapping["output_size"]
    encoded, actual_mapping = _prepare_png(placement, rendered, *size, budget)
    if actual_mapping["source_size"] != size:
        raise ValueError("渲染 PNG 尺寸与布局不符")
    actual = _rgba(encoded, size, budget)
    del encoded
    if actual != expected:  # Includes RGB hidden under zero alpha.
        raise ValueError("渲染 PNG 像素与原源及 CAD-14 采样不符")
    budget.check("prepare 像素核验后")


def prepare(bundle, target_document):
    """Return manual request sequence only; absolutely no HTTP/apply implementation."""
    bundle = _absolute(bundle)
    target_document = _absolute(target_document)
    if not target_document.endswith(".kra"):
        raise ValueError("专用目标必须为已保存 .kra（离线不能证明它当前活动）")
    capture = _capture(bundle + "/manifest.json", 65536)
    manifest = _load(capture[1])
    if manifest["schema"] != SCHEMA or manifest["krita_verified"] is not False:
        raise ValueError("未知或伪造验收状态")
    # Re-plan from ORIGINAL sources; reject source changes, not just stale copies.
    current, svg, sources = _collect(manifest["spec"]["path"])
    placement = _pure("placement")
    if ({k: v for k, v in current.items() if k != "images"} !=
            {k: v for k, v in manifest.items() if k != "images"}):
        raise ValueError("来源/布局改变，须创建新 bundle")
    if len(manifest["images"]) != len(ROLES):
        raise ValueError("bundle 图数错误")
    source_copy = _capture(bundle + "/source.json", 65536)
    if source_copy[0]["sha256"] != manifest["spec"]["sha256"]:
        raise ValueError("保留的布局源改变")
    checks = [capture, source_copy, *sources]
    requests = []
    for expected, item, source in zip(current["images"], manifest["images"], sources[1:]):
        if {k: v for k, v in item.items() if k != "rendered_sha256"} != expected:
            raise ValueError("布局 manifest 改变")
        for field, sha in (("original", item["source"]["sha256"]),
                           ("rendered", item["rendered_sha256"])):
            captured = _capture(bundle + "/" + item[field], 32 * 1024 * 1024)
            if captured[0]["sha256"] != sha:
                raise ValueError("bundle 资产改变")
            checks.append((captured[0], b"", captured[2]))
            if field == "rendered":
                _verify_rendered(placement, source, captured, expected)
            del captured
        x, y = item["placement"]["origin"]
        requests.append({"route": "/layer/add", "payload": {"png_path": bundle + "/" + item["rendered"],
                         "name": "CAD28 " + item["role"], "mode": "insert_at_offset", "offset_x": x, "offset_y": y}})
    svg_capture = _capture(bundle + "/annotations.svg", 65536)
    if svg_capture[1] != svg.encode():
        raise ValueError("SVG 源改变")
    checks.append(svg_capture)
    requests.append({"route": "/layer/vector", "payload": {
        "svg": svg, "name": "CAD28 editable annotations", "canvas": manifest["canvas"], "dpi": manifest["dpi"],
        "target_document": target_document, "dedicated_document": True}})
    _unchanged(checks)
    return {"manual_only": True, "target_document": target_document,
            "asset_verification": "local_rgba_consistency_only",
            "warning": "未执行。先人工确认专用已保存活动文档尺寸/DPI；每次PNG请求无文档锁，逐步回读，失败停止不重试。" + NOTICE,
            "requests": requests}


def fixture(output):
    """Teaching placeholders, emphatically not a real character case; stdlib PNG."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 100, 160, 8, 6, 0, 0, 0))
    rows = b"".join(b"\x00" + bytes((50 + y, 100, 180, 0 if y < 8 else 150)) * 100 for y in range(160))
    png += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    cells = {"main": [10, 30, 100, 160], "front": [130, 30, 100, 160],
             "side": [250, 30, 100, 160], "back": [370, 30, 100, 160],
             "equipment1": [10, 230, 100, 160], "equipment2": [130, 230, 100, 160]}
    spec = {"schema": SCHEMA, "unit": "px", "canvas": [500, 420], "dpi": [300, 300], "fixture": True,
            "references": dict(zip(REFERENCES, (38, 65, 110, 155))),
            "images": {role: {"path": role + ".png", "cell": cell, "anchor_source": [50, 8],
                              "anchor_target": [cell[0] + 50, cell[1] + 8]} for role, cell in cells.items()},
            "callouts": [{"text": "TEACHING PLACEHOLDER - NOT A CHARACTER", "label": [10, 220], "target": [60, 260]},
                         {"text": "Manual equipment review", "label": [250, 250], "target": [180, 280]}]}
    with _parent(output) as (parent, name, absolute):
        os.mkdir(name, 0o700, dir_fd=parent)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            for role in ROLES:
                _write(fd, role + ".png", png)
            _write(fd, "fixture.json", _json(spec))
        finally:
            os.close(fd)
    return {"output": absolute, "fixture": True, "notice": "教学占位PNG，非真实角色fixture；" + NOTICE}


def run(args):
    if args.layout_cmd == "fixture":
        result = fixture(args.output)
    elif args.layout_cmd == "plan":
        result = plan(args.spec)
    elif args.layout_cmd == "render":
        result = render(args.spec, args.output)
    else:
        result = prepare(args.bundle, args.document)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
