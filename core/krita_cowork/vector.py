"""CAD-28 restricted annotation SVG; stdlib only, no Qt/Krita imports.

This is deliberately NOT a general SVG importer. Successful readback is only a
structural check; font rendering, points alignment and GUI editing need Krita QA.
"""
from collections import Counter
import math
import os
import re
import xml.etree.ElementTree as ET

NS = "http://www.w3.org/2000/svg"
MAX_SVG_BYTES = 65536
MAX_ELEMENTS = 160
MAX_TEXT = 1600
MAX_NUMBER = 32768
NUMBER = r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
ATTRS = {
    "svg": {"width", "height", "viewBox"},
    "text": {"x", "y", "font-size", "font-family", "fill"},
    "line": {"x1", "y1", "x2", "y2", "stroke", "stroke-width"},
    "rect": {"x", "y", "width", "height", "fill", "stroke", "stroke-width"},
}


class VectorError(ValueError):
    pass


class VectorWriteError(Exception):
    def __init__(self, payload):
        self.payload = payload
        super().__init__(payload["error"])


def number(value, label, minimum=0, maximum=MAX_NUMBER):
    if type(value) not in (int, float) or not minimum <= value <= maximum or not math.isfinite(value):
        raise VectorError(label + " 必须是范围内有限数（不接受 bool）")
    return value


def _numeric(text, label):
    if not isinstance(text, str) or re.fullmatch(NUMBER, text) is None:
        raise VectorError("非法 SVG 数值: " + label)
    return number(float(text), label)


def _tag(element):
    if not element.tag.startswith("{" + NS + "}"):
        raise VectorError("仅允许 SVG 命名空间")
    return element.tag[len(NS) + 2:]


def validate_svg(svg, canvas, dpi):
    """Flat text/line/rect only; physical pt size + px viewBox, square DPI only."""
    if not isinstance(svg, str) or len(svg.encode("utf-8")) > MAX_SVG_BYTES:
        raise VectorError("SVG 字符串超过 64KiB 或类型非法")
    if re.search(r"<!|<\?|url\s*\(|(?:href|style)\s*=", svg, re.I):
        raise VectorError("拒绝 DTD/实体/处理指令/资源/CSS")
    if not isinstance(canvas, list) or len(canvas) != 2:
        raise VectorError("canvas 必须为 [width,height]")
    if any(type(v) is not int or not 1 <= v <= MAX_NUMBER for v in canvas):
        raise VectorError("canvas 必须是 1..32768 整数")
    if not isinstance(dpi, list) or len(dpi) != 2:
        raise VectorError("dpi 必须为 [x,y]")
    for v in dpi:
        number(v, "dpi", 1, 2400)
    if dpi[0] != dpi[1]:
        raise VectorError("首版仅支持明确方形 DPI；不猜测非方形映射")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise VectorError("SVG XML 无效") from exc
    elements = list(root.iter())
    if not 2 <= len(elements) <= MAX_ELEMENTS or _tag(root) != "svg":
        raise VectorError("SVG 元素数或根无效")
    if list(root) != elements[1:]:
        raise VectorError("仅允许扁平标注，不接受嵌套")
    chars = 0
    for el in elements:
        tag = _tag(el)
        if tag not in ATTRS or set(el.attrib) - ATTRS[tag]:
            raise VectorError("SVG 元素/属性不在白名单: " + tag)
        if el.tail and el.tail.strip():
            raise VectorError("仅 text 元素可含文字")
        if tag != "text" and el.text and el.text.strip():
            raise VectorError("仅 text 元素可含文字")
        for key, value in el.attrib.items():
            if key in ("fill", "stroke"):
                if re.fullmatch(r"#[0-9a-fA-F]{6}|none", value) is None:
                    raise VectorError("仅允许十六进制颜色/none")
            elif key == "font-family":
                if not 1 <= len(value) <= 80 or re.fullmatch(r"[\w -]+", value) is None:
                    raise VectorError("字体名称无效")
            elif tag != "svg":
                _numeric(value, key)
        required = {"text": {"x", "y", "font-size", "font-family", "fill"},
                    "line": ATTRS["line"], "rect": ATTRS["rect"]}.get(tag, ATTRS["svg"])
        if set(el.attrib) != required:
            raise VectorError("SVG 缺必要属性: " + tag)
        if tag == "text":
            text = el.text or ""
            if not 1 <= len(text) <= 120 or any(ord(c) < 32 for c in text):
                raise VectorError("仅接受单行 1..120 字短文字")
            chars += len(text)
            if not 1 <= float(el.get("font-size")) <= 96:
                raise VectorError("font-size 超出 1..96")
        if tag in ("line", "rect") and not 0 < float(el.get("stroke-width")) <= 16:
            raise VectorError("stroke-width 超出范围")
        if tag == "rect" and (float(el.get("width")) <= 0 or float(el.get("height")) <= 0):
            raise VectorError("rect 宽高必须为正")
        if tag == "line":
            coords = [(float(el.get("x1")), float(el.get("y1"))),
                      (float(el.get("x2")), float(el.get("y2")))]
        elif tag == "rect":
            coords = [(float(el.get("x")), float(el.get("y"))),
                      (float(el.get("x")) + float(el.get("width")),
                       float(el.get("y")) + float(el.get("height")))]
        elif tag == "text":
            coords = [(float(el.get("x")), float(el.get("y")))]
        else:
            coords = []
        if any(not (0 <= x <= canvas[0] and 0 <= y <= canvas[1]) for x, y in coords):
            raise VectorError("标注几何越出画布")
    if chars > MAX_TEXT:
        raise VectorError("文字总预算超限")
    expected_view = "0 0 {0} {1}".format(*canvas)
    if root.get("viewBox") != expected_view:
        raise VectorError("viewBox 必须明确对应画布 px")
    for attr, pixels, resolution in zip(("width", "height"), canvas, dpi):
        raw = root.get(attr, "")
        if not raw.endswith("pt"):
            raise VectorError("SVG width/height 必须显式 pt")
        if not math.isclose(_numeric(raw[:-2], attr), pixels * 72 / resolution, abs_tol=1e-7):
            raise VectorError("SVG pt 与 canvas/DPI 映射不符")
    return root


def _signature(root):
    # Strict by design: if Krita rewrites line to path, styles or transforms,
    # do not pretend we proved preservation. Caller reports unverified partial.
    return Counter((_tag(el), tuple(sorted(el.attrib.items())), el.text or "") for el in list(root))


def import_annotations(ops, *, svg, name, target_document, canvas, dpi,
                       dedicated_document, parent_id=None, above_layer_id=None):
    """Called only by ops under MainThreadBridge; never selects another document."""
    expected = validate_svg(svg, canvas, dpi)
    if dedicated_document is not True:
        raise VectorError("必须明确 dedicated_document=true 并人工确认专用文档")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise VectorError("name 必须为短非空文字")
    if (not isinstance(target_document, str) or not os.path.isabs(target_document)
            or not target_document.endswith(".kra") or os.path.normpath(target_document) != target_document):
        raise VectorError("target_document 必须为明确已保存专用 .kra 绝对路径")
    for value in (parent_id, above_layer_id):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise VectorError("parent/above 必须为非空会话 ID")
    app = ops._krita()
    doc = ops._active_doc()
    ops._check_rgba8(doc)

    def stable():
        if app.activeDocument() != doc or doc.fileName() != target_document:
            raise VectorError("活动文档/目标已改变；未开始下一写步骤")
        if [doc.width(), doc.height()] != canvas:
            raise VectorError("目标画布不符")
        # Official libkis Document.xRes/yRes return pixels per inch. The scalar
        # resolution() rounds x only; it cannot establish actual y resolution.
        for method, expected_dpi in zip(("xRes", "yRes"), dpi):
            getter = getattr(doc, method, None)
            if not callable(getter):
                raise VectorError("缺 Document." + method + " 能力；拒绝猜测双轴 DPI")
            actual_dpi = getter()
            number(actual_dpi, "实际 " + method, 1, 2400)
            if not math.isclose(actual_dpi, expected_dpi, rel_tol=1e-10, abs_tol=1e-8):
                raise VectorError("实际 " + method + " DPI 不符")

    stable()
    parent, above = ops._insertion_target(doc, parent_id, above_layer_id)
    creator = getattr(doc, "createVectorLayer", None)
    if not callable(creator):
        raise VectorError("缺专用 createVectorLayer；不以 createNode fallback")
    layer = None
    phase = "createVectorLayer"
    try:
        stable()
        layer = creator(name)
        if layer is None or layer.type() != "vectorlayer":
            raise VectorError("createVectorLayer 返回无效")
        for method in ("addShapesFromSvg", "shapes"):
            if not callable(getattr(layer, method, None)):
                raise VectorError("专用 VectorLayer 缺能力: " + method)
        phase = "attach"
        stable()
        if not parent.addChildNode(layer, above):
            raise VectorError("addChildNode 返回 false")
        ops._verify_attached(doc, parent, layer, above)
        phase = "import"
        stable()
        added = layer.addShapesFromSvg(svg)  # string, not a filename
        if not added:
            raise VectorError("addShapesFromSvg 返回空 list；已挂载层可能为空")
        phase = "readback"
        stable()
        actual = ops._verify_attached(doc, parent, layer, above)
        if actual.type() != "vectorlayer":
            raise VectorError("树中类型不符")
        shapes = layer.shapes()
        if not shapes:
            raise VectorError("形状回读为空")
        combined = ET.Element("{" + NS + "}svg")
        total = 0
        for shape in shapes:
            # stripTextMode=False: never strip native text for our check.
            exported = shape.toSvg(False, False)
            if not isinstance(exported, str):
                raise VectorError("形状 SVG 回读类型不符")
            total += len(exported.encode("utf-8"))
            if total > MAX_SVG_BYTES or re.search(r"<!|<\?", exported):
                raise VectorError("形状回读超预算或含声明")
            fragment = ET.fromstring(exported)
            if _tag(fragment) == "svg":
                if fragment.attrib != expected.attrib:
                    raise VectorError("回读 SVG 根的物理尺寸/viewBox 不能证明映射保留")
                combined.extend(list(fragment))
            else:
                combined.append(fragment)
        if _signature(combined) != _signature(expected):
            raise VectorError("元素/文本/几何回读不能证明完整保留（可能格式重写）；禁止当完整成功")
        phase = "refresh"
        stable()
        doc.refreshProjection()
        stable()
        result = {"id": ops._node_id(layer), "type": "vectorlayer", "parent_id": ops._node_id(parent),
                  "status": "structural_readback_only", "shapes": len(shapes),
                  "coordinate_mapping": {"canvas_px": canvas, "dpi": dpi,
                                         "size_pt": [v * 72 / d for v, d in zip(canvas, dpi)]},
                  "krita_editing_verified": False}
    except Exception as exc:
        layer_id = None
        if layer is not None:
            try:
                layer_id = ops._node_id(layer)
            except Exception:
                pass  # Only diagnostic ID; never masks the write failure.
        raise VectorWriteError({
            "error": "矢量写入失败或未能验证；可能已部分写入，未回滚，禁止盲目重试: " + str(exc),
            "id": layer_id, "failed": phase, "status": "partial_or_unverified",
            "rolled_back": False, "read_layers_required": True,
        }) from exc
    return result
