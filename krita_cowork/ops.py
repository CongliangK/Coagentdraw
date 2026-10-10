"""ops:在 Krita 主线程执行的图层原语。HTTP 层通过 MainThreadBridge 调用。

硬约束(来自对抗式审查实证):
- 仅支持 RGBA8 文档(其他色彩空间明确报错,不静默损坏)。
- QImage 一律 Format_RGBA8888,逐行按 bytesPerLine 拷贝(规避行填充)。
- 新图层先挂树再写像素;root 图层只读(不可被 agent 操作整文档)。
- 图层 id 用 uniqueId().toString()(裸 UUID,不依赖 PyQt repr 格式),
  仅会话内有效,失效抛 NotFoundError → HTTP 404。
- offset 越界/类型非法在写像素前拒绝(Krita 会静默裁剪,必须自检)。
"""
import os

try:
    from PyQt6.QtGui import QImage
except ImportError:  # pragma: no cover
    from PyQt5.QtGui import QImage

from krita import Krita


class OpError(Exception):
    """请求参数/状态不合法 → HTTP 400。"""


class NotFoundError(LookupError):
    """图层 id 失效 → HTTP 404(独立类型,避免内部 KeyError 被误判)。"""


def _krita():
    return Krita.instance()


def _active_doc():
    doc = _krita().activeDocument()
    if doc is None:
        raise OpError("没有打开的文档")
    return doc


def _check_rgba8(doc):
    root = doc.rootNode()
    if root.colorModel() != "RGBA" or root.colorDepth() != "U8":
        raise OpError(
            "仅支持 RGBA/U8 文档,当前 {0}/{1}".format(
                root.colorModel(), root.colorDepth()
            )
        )


def _node_id(n):
    return n.uniqueId().toString()


def _find_node(doc, layer_id, allow_root=False):
    want = str(layer_id).strip()
    root = doc.rootNode()
    stack = [root]
    while stack:
        n = stack.pop()
        if _node_id(n) == want or _node_id(n) == _normalize(want):
            if n is root and not allow_root:
                raise OpError("root 图层不可作为操作目标")
            return n
        stack.extend(n.childNodes())
    raise NotFoundError("图层 id 失效(文档可能已重开): {0}".format(layer_id))


def _normalize(id_str):
    """兼容早期协议键 'PyQt6.QtCore.QUuid("{uuid}")' 与 '{uuid}'。"""
    if "QUuid(" in id_str:
        id_str = id_str.split("QUuid(", 1)[1].rstrip(")")
    return id_str.strip("'{\"}")


# ── 只读操作 ──────────────────────────────────────────────────────

def ping():
    return {
        "ok": True,
        "krita_version": _krita().version(),
        "pid": os.getpid(),
    }


def doc_info():
    docs = _krita().documents()
    active = _krita().activeDocument()
    return {
        "documents": [
            {
                "name": d.name(),
                "width": d.width(),
                "height": d.height(),
                "active": d == active,
                "filename": d.fileName(),
            }
            for d in docs
        ]
    }


def layers():
    doc = _active_doc()
    _check_rgba8(doc)

    def ser(n, depth):
        b = n.bounds()  # root 层无 x()/y(),统一走 bounds
        return {
            "id": _node_id(n),
            "name": n.name(),
            "type": n.type(),
            "root": n is doc.rootNode(),
            "visible": n.visible(),
            "x": b.x(),
            "y": b.y(),
            "width": b.width(),
            "height": b.height(),
            "opacity": round(n.opacity() / 255.0, 3),  # 归一化 0-1
            "depth": depth,
            "children": [ser(c, depth + 1) for c in n.childNodes()],
        }

    return {"document": doc.name(), "layers": [ser(doc.rootNode(), 0)]}


def layer_png(layer_id, path):
    """把图层像素导出为 PNG。"""
    doc = _active_doc()
    _check_rgba8(doc)
    node = _find_node(doc, layer_id)
    b = node.bounds()
    if b.width() <= 0 or b.height() <= 0:
        raise OpError("图层无像素区域")
    data = node.pixelData(b.x(), b.y(), b.width(), b.height())
    if len(data) != b.width() * b.height() * 4:
        raise OpError(
            "pixelData 长度异常: {0} != {1}".format(
                len(data), b.width() * b.height() * 4
            )
        )
    img = QImage(
        bytes(data), b.width(), b.height(), b.width() * 4,
        QImage.Format.Format_RGBA8888,
    )
    out_path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    if not img.save(out_path, "PNG"):
        raise OpError("PNG 写入失败: {0}".format(out_path))
    return {"path": out_path, "width": b.width(), "height": b.height()}


# ── 写操作 ────────────────────────────────────────────────────────

def doc_new(name, width, height):
    """新建 RGBA8 文档并激活。"""
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        raise OpError("width/height 必须是整数")
    if not (1 <= width <= 32768 and 1 <= height <= 32768):
        raise OpError("width/height 超出合理范围")
    doc = _krita().createDocument(width, height, str(name),
                                  "RGBA", "U8", "", 300.0)
    if doc is None:
        raise OpError("createDocument 失败")
    _krita().activeWindow().addView(doc)
    doc.refreshProjection()
    return {"name": doc.name(), "width": width, "height": height}


def layer_add(png_path, name, above_layer_id=None, offset_x=0, offset_y=0):
    """PNG → 新 paint layer(非破坏式,不动既有图层)。返回新图层 id。"""
    doc = _active_doc()
    _check_rgba8(doc)
    try:
        off_x, off_y = int(offset_x), int(offset_y)
    except (TypeError, ValueError):
        raise OpError("offset_x/offset_y 必须是整数")
    png_path = os.path.abspath(os.path.expanduser(png_path))
    img = QImage(png_path)
    if img.isNull():
        raise OpError("PNG 读取失败: {0}".format(png_path))
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    if off_x < 0 or off_y < 0 or off_x + w > doc.width() or off_y + h > doc.height():
        raise OpError(  # Krita 会静默裁剪,必须先拒绝
            "图层越界: {0}x{1}@({2},{3}) 超出画布 {4}x{5}".format(
                w, h, off_x, off_y, doc.width(), doc.height())
        )

    bpl = img.bytesPerLine()
    raw = img.constBits()
    try:
        raw = raw.asarray(bpl * h)  # PyQt6: voidptr 需显式取尺寸
    except AttributeError:
        pass  # PyQt5: 可直接切片
    buf = bytearray(w * h * 4)
    for r in range(h):  # 逐行拷贝,规避行尾对齐填充
        buf[r * w * 4:(r + 1) * w * 4] = raw[r * bpl:r * bpl + w * 4]

    parent = doc.rootNode()
    above_node = _find_node(doc, above_layer_id) if above_layer_id else None
    layer = doc.createNode(str(name), "paintLayer")
    parent.addChildNode(layer, above_node)  # 先挂树
    layer.setPixelData(bytes(buf), off_x, off_y, w, h)
    doc.refreshProjection()
    return {"id": _node_id(layer), "name": str(name), "width": w, "height": h}


def layer_set_visible(layer_id, visible):
    doc = _active_doc()
    node = _find_node(doc, layer_id)
    node.setVisible(bool(visible))
    doc.refreshProjection()
    return {"id": _node_id(node), "visible": bool(visible)}


def layer_rename(layer_id, name):
    doc = _active_doc()
    node = _find_node(doc, layer_id)
    node.setName(str(name))
    return {"id": _node_id(node), "name": node.name()}


def doc_save_as(path):
    doc = _active_doc()
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not doc.saveAs(path):
        raise OpError("saveAs 失败: {0}".format(path))
    return {"path": path}
