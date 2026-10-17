"""CAD-49 v1 guarded append operations, called only by the existing GUI bridge.

This is an in-process receipt journal, not a transaction or a persisted ledger.
Retained native Document handles use libkis equality (QPointer<KisDocument>),
never Python object identity, file names or lifecycle notifications alone.
"""
import copy
import hashlib
import json
import os
import re
import uuid

PROTOCOL = "cwb-guarded-write-v1"
MAX_REQUEST_BYTES = 65536
MAX_RECEIPTS = 4096
MAX_TARGETS = 128
MAX_BINDINGS = 128
MAX_NODES = 4096
MAX_TREE_BYTES = 1024 * 1024
MAX_RECEIPT_BYTES = 16384


class GuardError(ValueError):
    pass


def _json(value, maximum=MAX_REQUEST_BYTES):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise GuardError("请求必须是有限 JSON 值") from exc
    if len(encoded) > maximum:
        raise GuardError("守卫协议 JSON 字节预算超限")
    return encoded


def _uuid(value, label):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise GuardError(label + " 必须是规范 UUID") from None
    return value


def _text(value, label, maximum=1024):
    try:
        if (not isinstance(value, str) or not value or "\0" in value or
                len(value.encode("utf-8")) > maximum):
            raise GuardError(label + " 必须是有界非空字符串")
    except UnicodeError as exc:
        raise GuardError(label + " 必须是有效 UTF-8 字符串") from exc
    return value


def _label(value):
    try:
        if not isinstance(value, str) or "\0" in value or len(value.encode("utf-8")) > 1024:
            raise GuardError("节点/文档名称超过协议字节预算")
    except UnicodeError as exc:
        raise GuardError("节点/文档名称必须是有效 UTF-8 字符串") from exc
    return value


def _node_id(node):
    return _text(node.uniqueId().toString(), "节点 ID", 128)


def _same(left, right):
    if left is None or right is None:
        return False
    try:
        result = left.__eq__(right)
    except Exception as exc:
        raise GuardError("无法比较原生文档句柄") from exc
    if type(result) is not bool:
        raise GuardError("原生文档相等比较必须返回 bool")
    return result


def _path(value):
    _text(value, "已保存文档路径", 4096)
    # The embedded Python filesystem codec can differ from UTF-8.
    raw = os.path.realpath(os.path.abspath(value).encode("utf-8"))
    return raw.decode("utf-8")


def _tree(doc):
    """Bounded structural readback, with explicit root rather than wrapper is."""
    seen, nodes, count = set(), {}, 0

    def walk(node, root=False, depth=0):
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > 64:
            raise GuardError("图层树数量或深度预算超限")
        identity = _node_id(node)
        if identity in seen:
            raise GuardError("图层 ID 缺失/重复；不能建立唯一绑定")
        seen.add(identity)
        row = {"id": identity, "name": _label(node.name()),
               "type": _text(node.type(), "图层类型", 64), "root": root,
               "children": [walk(child, depth=depth + 1) for child in node.childNodes()]}
        nodes[identity] = row
        return row

    tree = {"document": _label(doc.name()), "layers": [walk(doc.rootNode(), True)]}
    _json(tree, MAX_TREE_BYTES)
    return tree, nodes


class WriteContext:
    def __init__(self, service, document, target, bindings, receipt):
        self.service, self.document = service, document
        self.target, self.bindings, self.receipt = target, bindings, receipt

    def check(self, phase):
        self.service._check(self.target, self.bindings)
        self.receipt["phase"] = phase
        self.service._save_receipt(self.receipt)

    def before_mutation(self, phase):
        self.check(phase)
        # Set before calling the API: even an exception may follow a side effect.
        self.receipt["mutation_started"] = True
        self.service._save_receipt(self.receipt)

    def created(self, node):
        if node is not None:
            self.receipt["created_objects"].append({"id": _node_id(node), "attached": "unknown"})
            self.service._save_receipt(self.receipt)

    def attached(self, node):
        identity = _node_id(node)
        for item in self.receipt["created_objects"]:
            if item["id"] == identity:
                item["attached"] = "verified"
        self.service._save_receipt(self.receipt)


class WriteService:
    """One plugin lifetime; all methods execute in the GUI bridge callback.

    Native wrappers retain a non-owning QPointer, not a pixel buffer. No eviction
    makes an old operation executable again. Capacity exhaustion fails closed.
    """
    def __init__(self, app):
        self.app = app
        self.service_id = str(uuid.uuid4())
        self.targets = {}  # token -> native Document wrapper
        self.receipts = {}  # operation UUID -> (request hash, bounded JSON bytes)

    def _describe(self, doc):
        if doc is None:
            raise GuardError("没有活动文档")
        root = doc.rootNode()
        if root is None:
            raise GuardError("授权文档已经关闭或失效")
        root_id = _node_id(root)
        # A real root UUID is mandatory, but is not itself the identity proof.
        _uuid(root_id.strip("{}"), "root UUID")
        if root.colorModel() != "RGBA" or root.colorDepth() != "U8":
            raise GuardError("受保护写入仅支持 RGBA/U8")
        width, height = doc.width(), doc.height()
        if any(type(n) is not int or not 1 <= n <= 32768 for n in (width, height)):
            raise GuardError("文档尺寸无效")
        path = _path(doc.fileName())
        if not path.lower().endswith(".kra"):
            raise GuardError("需要明确的已保存 .kra 活动文档")
        return {"document": path, "root": root_id,
                "width": width, "height": height}

    def _current(self):
        doc = self.app.activeDocument()
        description = self._describe(doc)
        opened = self.app.documents()
        if len(opened) > MAX_TARGETS:
            raise GuardError("同时打开文档数量超过受保护写入预算")
        # Native libkis equality is independent of newly allocated SIP wrappers.
        matches = [other for other in opened if _same(other, doc)]
        paths = [_path(other.fileName()) for other in opened if other.fileName()]
        roots = [_node_id(other.rootNode()) for other in opened if other.rootNode() is not None]
        if (len(matches) != 1 or paths.count(description["document"]) != 1 or
                roots.count(description["root"]) != 1):
            raise GuardError("活动文档/路径/root UUID 缺失或多义")
        return doc, description

    def target(self):
        doc, description = self._current()
        tokens = [key for key, retained in self.targets.items() if _same(retained, doc)]
        if len(tokens) > 1:
            raise GuardError("服务端文档句柄多义")
        if tokens:
            token = tokens[0]
        else:
            if len(self.targets) >= MAX_TARGETS:
                raise GuardError("文档授权句柄预算耗尽；先人工核对未决操作，不自动重启/重试")
            token = str(uuid.uuid4())
            self.targets[token] = doc
        expected = {"protocol": PROTOCOL, "service_id": self.service_id,
                    "document_handle": token, **description}
        tree, _ = _tree(doc)
        self._check(expected, [])
        return {"protocol": PROTOCOL, "expected_target": expected, "tree": tree,
                "pid": os.getpid(), "receipt_limit": MAX_RECEIPTS,
                "identity_contract": "retained-native-document-equality-v1",
                "runtime_verified": False}

    def _check(self, expected, bindings):
        required = {"protocol", "service_id", "document_handle", "document", "root", "width", "height"}
        if not isinstance(expected, dict) or set(expected) != required:
            raise GuardError("expected_target 合同不完整")
        _uuid(expected["service_id"], "service_id")
        _uuid(expected["document_handle"], "document_handle")
        _text(expected["document"], "document", 4096)
        _text(expected["root"], "root", 128)
        if any(type(expected[k]) is not int for k in ("width", "height")):
            raise GuardError("expected_target 尺寸必须为整数")
        if expected["protocol"] != PROTOCOL or expected["service_id"] != self.service_id:
            raise GuardError("受保护服务会话已改变；旧结果未知，不得重试")
        retained = self.targets.get(expected["document_handle"])
        if retained is None:
            raise GuardError("授权文档句柄未知/失效")
        doc, description = self._current()
        if not _same(retained, doc) or self._describe(retained) != description:
            raise GuardError("授权目标已切换、关闭重开或失效")
        if any(expected[key] != value for key, value in description.items()):
            raise GuardError("授权目标路径、root 或尺寸已改变")
        if not isinstance(bindings, list) or len(bindings) > MAX_BINDINGS:
            raise GuardError("expected_bindings 必须为有界列表")
        _, nodes = _tree(doc)
        ids, tokens = set(), set()
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {"id", "token"}:
                raise GuardError("组身份绑定字段无效")
            identity = _text(binding["id"], "组 ID", 128)
            token = _uuid(binding["token"], "组标记")
            if identity in ids or token in tokens:
                raise GuardError("重复组身份绑定")
            ids.add(identity)
            tokens.add(token)
            candidates = [row for row in nodes.values() if "[cwb:" + token + "]" in row["name"]]
            node = nodes.get(identity)
            if (node is None or node["root"] or node["type"] != "grouplayer" or
                    len(candidates) > 1 or (candidates and candidates[0]["id"] != identity)):
                raise GuardError("组身份丢失、冲突或多义")
        return doc

    def _save_receipt(self, receipt):
        self.receipts[receipt["operation_id"]] = (receipt["request_sha256"],
                                                 _json(receipt, MAX_RECEIPT_BYTES))

    def receipt(self, operation_id, service_id):
        _uuid(operation_id, "operation_id")
        _uuid(service_id, "service_id")
        if service_id != self.service_id:
            return {"protocol": PROTOCOL, "operation_id": operation_id,
                    "service_id": self.service_id, "status": "unknown", "reason": "service_changed"}
        stored = self.receipts.get(operation_id)
        if stored is None:
            return {"protocol": PROTOCOL, "operation_id": operation_id,
                    "service_id": self.service_id, "status": "unknown", "reason": "not_recorded"}
        return json.loads(stored[1])

    def execute(self, kind, callback, *, operation_id, expected_target,
                expected_bindings, source_sha256=None, **payload):
        _uuid(operation_id, "operation_id")
        if kind not in ("group", "add"):
            raise GuardError("未知受保护写入类型")
        if kind == "add" and (not isinstance(source_sha256, str) or
                              re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None):
            raise GuardError("add 必须绑定 source_sha256")
        if kind == "group" and source_sha256 is not None:
            raise GuardError("group 不接受 source_sha256")
        _text(payload.get("name"), "name")
        for field in ("parent_id", "above_layer_id"):
            if payload.get(field) is not None:
                _text(payload[field], field, 128)
        if kind == "add":
            _text(payload.get("png_path"), "png_path", 4096)
        request = {"kind": kind, "expected_target": expected_target,
                   "expected_bindings": expected_bindings, "source_sha256": source_sha256,
                   "payload": payload}
        fingerprint = hashlib.sha256(_json(request)).hexdigest()
        old = self.receipts.get(operation_id)
        if old is not None:
            if old[0] != fingerprint:
                raise GuardError("同一 operation_id 的请求内容不同；拒绝重复使用")
            return json.loads(old[1])  # Historical receipt; never recreate even after deletion.
        if not isinstance(expected_target, dict) or expected_target.get("service_id") != self.service_id:
            raise GuardError("受保护服务会话已改变；旧结果未知，不得重试")
        if len(self.receipts) >= MAX_RECEIPTS:
            raise GuardError("操作收据预算耗尽；保留已有收据，不自动逐出或重启")
        receipt = {"protocol": PROTOCOL, "service_id": self.service_id,
                   "operation_id": operation_id, "request_sha256": fingerprint,
                   "status": "running", "phase": "reserved", "mutation_started": False,
                   "created_objects": [], "source_sha256": source_sha256}
        self.receipts[operation_id] = fingerprint, _json(receipt, MAX_RECEIPT_BYTES)
        try:
            doc = self._check(expected_target, expected_bindings)
            context = WriteContext(self, doc, expected_target, expected_bindings, receipt)
            if kind == "add":
                payload["_expected_source_sha256"] = source_sha256
            result = callback(**payload, _write_context=context)
            context.check("completed")
            _json(result, 8192)
            receipt.update(status="succeeded", result=result)
        except BaseException as exc:
            receipt.update(status="partial_or_unknown" if receipt["mutation_started"] else "rejected",
                           error=(type(exc).__name__ + ": " + str(exc))[:2048])
        finally:
            self.receipts[operation_id] = fingerprint, _json(receipt, MAX_RECEIPT_BYTES)
        return copy.deepcopy(receipt)
