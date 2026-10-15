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
        if _normalize(_node_id(n)) == _normalize(want):
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


def _snapshot_export(**kwargs):
    # Lazy import keeps the codec isolated and all Krita access inside this
    # existing MainThreadBridge operation (including paired projection + mask).
    from .snapshot import SnapshotError, export_snapshot
    for key in ("projection_path", "selection_path"):
        if key in kwargs and not isinstance(kwargs[key], str):
            raise OpError("{0} 必须是非空 PNG 路径".format(key))
    try:
        return export_snapshot(_krita(), **kwargs)
    except SnapshotError as exc:
        raise OpError(str(exc)) from exc


def doc_export(path, overwrite=False, expected_document_token=None):
    return _snapshot_export(projection_path=path, overwrite=overwrite,
                            expected_document_token=expected_document_token)


def doc_selection_png(path, selection_mode="selection", overwrite=False,
                      expected_document_token=None):
    return _snapshot_export(selection_path=path, selection_mode=selection_mode,
                            overwrite=overwrite, expected_document_token=expected_document_token)


def doc_snapshot(projection_path, selection_path, selection_mode="selection",
                 overwrite=False, expected_document_token=None):
    return _snapshot_export(projection_path=projection_path, selection_path=selection_path,
                            selection_mode=selection_mode, overwrite=overwrite,
                            expected_document_token=expected_document_token)


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


def _insertion_target(doc, parent_id, above_layer_id):
    """root 仅能作为默认容器;显式父级只能是组,above 只能是同级。"""
    parent = doc.rootNode() if parent_id is None else _find_node(doc, parent_id)
    if parent_id is not None and parent.type() != "grouplayer":
        raise OpError("parent_id 必须指向 groupLayer")
    above = None if above_layer_id is None else _find_node(doc, above_layer_id)
    if above is not None:
        if above.type() not in ("grouplayer", "paintlayer"):
            raise OpError("above 仅支持 groupLayer/paintLayer")
        if _node_id(above) not in [_node_id(n) for n in parent.childNodes()]:
            raise OpError("above 必须是指定父级的直接子节点")
    return parent, above


def _verify_attached(doc, parent, node, above):
    """从文档实际树回读,不信任 createNode 返回的游离节点。"""
    try:
        actual = _find_node(doc, _node_id(node))
        actual_parent = _find_node(doc, _node_id(parent), allow_root=True)
    except NotFoundError as exc:
        raise OpError("创建后树结构异常: {0}".format(exc)) from exc
    children = [_node_id(n) for n in actual_parent.childNodes()]
    if _node_id(actual) not in children:
        raise OpError("创建后树结构异常: 新节点未挂到指定父级")
    if above is not None:
        if (_node_id(above) not in children or
                children.index(_node_id(actual)) != children.index(_node_id(above)) + 1):
            raise OpError("创建后树结构异常: above 顺序不符")
    if actual.type() != node.type():
        raise OpError("创建后树结构异常: 节点类型不符")
    return actual


def _create_attached(doc, name, node_type, parent, above):
    node = doc.createNode(str(name), node_type)
    if node is None or node.type() != node_type.lower():
        raise OpError("createNode 失败或节点类型不符: {0}".format(node_type))
    if not parent.addChildNode(node, above):
        raise OpError("addChildNode 失败: {0}".format(_node_id(node)))
    return _verify_attached(doc, parent, node, above)


def layer_group(name, parent_id=None, above_layer_id=None):
    """新建空 groupLayer,可嵌套;不移动/合并已有图层。"""
    doc = _active_doc()
    _check_rgba8(doc)
    parent, above = _insertion_target(doc, parent_id, above_layer_id)
    node = _create_attached(doc, name, "groupLayer", parent, above)
    doc.refreshProjection()
    return {"id": _node_id(node), "name": node.name(), "type": node.type(),
            "parent_id": _node_id(parent)}


def layer_add(png_path, name, above_layer_id=None, offset_x=0, offset_y=0,
              parent_id=None):
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

    parent, above_node = _insertion_target(doc, parent_id, above_layer_id)
    layer = _create_attached(doc, name, "paintLayer", parent, above_node)
    if not layer.setPixelData(bytes(buf), off_x, off_y, w, h):
        raise OpError("setPixelData 失败;新图层可能已挂树: {0}".format(_node_id(layer)))
    layer = _verify_attached(doc, parent, layer, above_node)
    doc.refreshProjection()
    return {"id": _node_id(layer), "name": layer.name(), "width": w, "height": h,
            "parent_id": _node_id(parent)}


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
