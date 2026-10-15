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
import math

# Krita composite operation IDs, not translated GUI labels. Local visual QA pending.
BLENDING_MODES = ("normal", "multiply", "overlay", "screen")
_UNSET = object()


class PropsWriteError(Exception):
    """写阶段失败,不承诺回滚;payload 可供客户端判断后回读。"""

    def __init__(self, payload):
        self.payload = payload
        super().__init__(payload["error"])

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


def _optional_layer_property(node, getter, required_methods=()):
    """仅供列表的新增可选属性使用;写入预读/回读不得降级为未知。"""
    supported = None  # 能力探测自身异常时,不能谎称不支持。
    try:
        methods = {name: getattr(node, name, None) for name in (getter, *required_methods)}
        missing = [name for name, method in methods.items() if not callable(method)]
        if missing:
            return None, False, {"status": "unavailable", "error": "API missing: " + ", ".join(missing)}
        supported = True
        return methods[getter](), supported, {"status": "ok"}
    except Exception as exc:
        return None, supported, {"status": "error", "error": "{0}: {1}".format(type(exc).__name__, exc)}


def layers():
    doc = _active_doc()
    _check_rgba8(doc)

    def ser(n, depth):
        b = n.bounds()  # root 层无 x()/y(),统一走 bounds
        # 旧字段/树结构保持严格读取,不把其异常伪装成完整列表。
        result = {
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
        if result["type"] == "paintlayer":
            alpha, supported, alpha_status = _optional_layer_property(n, "alphaLocked", ("setAlphaLocked",))
        else:
            alpha, supported, alpha_status = None, False, {"status": "unsupported"}
        mode, _, mode_status = _optional_layer_property(n, "blendingMode")
        result.update({
            "alpha_locked": alpha,
            "alpha_lock_supported": supported,
            "blending_mode": mode,  # 保留原始稳定 ID,不改写未知模式。
            "property_status": {"alpha_locked": alpha_status, "blending_mode": mode_status},
        })
        return result

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
              parent_id=None, mode=None, fit_canvas=None):
    """PNG → 新透明 paint layer；坐标映射见 placement，不动既有图层。"""
    from .placement import (PlacementError, packed_rgba, plan_placement,
                            resize_rgba, resolve_mode)
    doc = _active_doc()
    _check_rgba8(doc)
    for label, value in (("png_path", png_path), ("name", name),
                         ("parent_id", parent_id), ("above", above_layer_id)):
        if value is None and label in ("parent_id", "above"):
            continue
        if not isinstance(value, str):
            raise OpError("{0} 必须是字符串".format(label))
        if label in ("png_path", "name") and not value.strip():
            raise OpError("{0} 必须是非空字符串".format(label))
    try:
        # Reject ambiguous modes/types even before decoding, and all geometry
        # before createNode/addChildNode/setPixelData.
        resolve_mode(mode, fit_canvas, offset_x, offset_y)
        png_path = os.path.abspath(os.path.expanduser(png_path))
        img = QImage(png_path)
        if img.isNull():
            raise OpError("PNG 读取失败: {0}".format(png_path))
        img = img.convertToFormat(QImage.Format.Format_RGBA8888)
        if img.isNull():
            raise OpError("QImage RGBA8888 转换失败")
        sw, sh = img.width(), img.height()
        mapping = plan_placement(sw, sh, doc.width(), doc.height(),
                                 mode, offset_x, offset_y, fit_canvas)
        parent, above_node = _insertion_target(doc, parent_id, above_layer_id)
        bpl = img.bytesPerLine()
        raw = img.constBits()
        try:
            raw = raw.asarray(bpl * sh)  # PyQt6: voidptr 需显式取尺寸
        except AttributeError:
            pass  # 保留既有可切片缓冲区路径
        buf = resize_rgba(packed_rgba(raw, sw, sh, bpl), mapping)
    except PlacementError as exc:
        raise OpError(str(exc)) from exc
    w, h = mapping["output_size"]
    off_x, off_y = mapping["origin"]
    layer = _create_attached(doc, name, "paintLayer", parent, above_node)
    if not layer.setPixelData(buf, off_x, off_y, w, h):
        raise OpError("setPixelData 失败;新图层可能已挂树: {0}".format(_node_id(layer)))
    layer = _verify_attached(doc, parent, layer, above_node)
    doc.refreshProjection()
    return {"id": _node_id(layer), "name": layer.name(), "width": w, "height": h,
            "parent_id": _node_id(parent), "placement": mapping}


def _alpha_supported(node):
    return (node.type() == "paintlayer" and
            callable(getattr(node, "alphaLocked", None)) and
            callable(getattr(node, "setAlphaLocked", None)))


def layer_props(layer_id, alpha_locked=_UNSET, opacity=_UNSET, blending_mode=_UNSET):
    """先验证所有参数/能力,再按 alpha/opacity/blend 顺序写入并逐项回读。

    不是事务: setter 抛异常/返回 False/回读不符时停止,保留已写状态并明确报错。
    """
    changes = []
    if alpha_locked is not _UNSET:
        if type(alpha_locked) is not bool:
            raise OpError("alpha_locked 必须是布尔值")
        changes.append(("alpha_locked", "setAlphaLocked", "alphaLocked", alpha_locked))
    if opacity is not _UNSET:
        if (type(opacity) not in (int, float) or not 0 <= opacity <= 1 or
                not math.isfinite(opacity)):
            raise OpError("opacity 必须是 0..1 的有限数值(不接受 bool)")
        changes.append(("opacity", "setOpacity", "opacity", math.floor(opacity * 255 + 0.5)))
    if blending_mode is not _UNSET:
        if type(blending_mode) is not str or blending_mode not in BLENDING_MODES:
            raise OpError("blending_mode 必须是: " + ", ".join(BLENDING_MODES))
        changes.append(("blending_mode", "setBlendingMode", "blendingMode", blending_mode))
    if not changes:
        raise OpError("至少指定一个图层属性")
    doc = _active_doc()
    _check_rgba8(doc)
    node = _find_node(doc, layer_id)
    if node.type() not in ("paintlayer", "grouplayer"):
        raise OpError("属性写入仅支持 paintLayer/groupLayer")
    if alpha_locked is not _UNSET and not _alpha_supported(node):
        raise OpError("此节点不支持 Alpha Lock(仅 paintLayer)")
    # 在首次写入前探测全部方法和读取能力;不把 API 缺失当作成功。
    for key, setter, getter, value in changes:
        if not callable(getattr(node, setter, None)) or not callable(getattr(node, getter, None)):
            raise OpError("此节点不支持属性: " + key)
        try:
            getattr(node, getter)()
        except Exception as exc:
            raise OpError("属性预读失败: " + key) from exc
    attempted, verified, values = [], [], {}
    current = "refreshProjection"
    try:
        for key, setter, getter, value in changes:
            current = key
            attempted.append(key)  # setter 即使抛异常也可能已经修改节点
            result = getattr(node, setter)(value)
            actual = getattr(node, getter)()
            if result is False or actual != value:
                raise RuntimeError("setter 失败或回读不符: {0!r} != {1!r}".format(actual, value))
            verified.append(key)
            values[key] = round(actual / 255.0, 3) if key == "opacity" else actual
        current = "refreshProjection"
        doc.refreshProjection()
    except Exception as exc:
        raise PropsWriteError({
            "error": "属性写入失败;可能已部分写入,未回滚,请重新读取 /layers: " + str(exc),
            "id": _node_id(node), "failed": current,
            "attempted": attempted, "verified": verified, "rolled_back": False,
        }) from exc
    # 与 /layers 一致返回实际量化后的 opacity,不回显未经回读的输入。
    return {"id": _node_id(node), **values}


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
