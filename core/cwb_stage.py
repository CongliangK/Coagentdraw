"""确定性阶段组；独立 sidecar，不修改项目状态或 Krita 插件。

组名中的随机 [cwb:UUID] 是可见的持久身份标记，不是快照文档 token。
仅在同一 pid/root 的已知会话使用节点 ID；否则必须重新解析随机标记。
HTTP 创建和 sidecar 写入不是原子事务；未确认请求始终阻止自动重试。
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

from cwb_workflow import WORKFLOW, DOCUMENT_STAGES, document_specs


MARKER = re.compile(r"\[cwb:([0-9a-f-]{36})\]")


class StageError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def specs(number, workflow=WORKFLOW, role=None):
    if type(number) is not int or not 1 <= number <= 8:
        raise StageError("阶段必须为 1–8")
    if workflow != WORKFLOW:
        raise StageError("未知 workflow")
    if role not in DOCUMENT_STAGES or number not in DOCUMENT_STAGES[role]:
        raise StageError("必须选择兼容阶段的 --role 文档角色")
    return document_specs(role)


def marker(token):
    return "[cwb:" + token + "]"


def validate(data):
    try:
        if type(data["schema_version"]) is not int or data["schema_version"] != 2:
            raise ValueError("未知版本；需要 schema_version=2")
        if data["workflow_id"] != WORKFLOW or data["document_role"] not in DOCUMENT_STAGES:
            raise ValueError("未知 workflow/文档角色")
        definitions = {key: (label, parent) for key, label, parent in document_specs(data["document_role"])}
        uuid.UUID(data["sidecar_id"])
        if not Path(data["document"]).is_absolute():
            raise ValueError("文档必须为绝对路径")
        if not isinstance(data["groups"], dict) or not isinstance(data["events"], list):
            raise ValueError("无效记录")
        tokens = set()
        for key, group in data["groups"].items():
            if key not in definitions:
                raise ValueError("未知预设组")
            token = group["token"]
            if str(uuid.UUID(token)) != token or token in tokens:
                raise ValueError("重复或无效身份标记")
            tokens.add(token)
            if group["status"] not in ("pending", "verified"):
                raise ValueError("无效创建状态")
            if not isinstance(group.get("id"), (str, type(None))):
                raise ValueError("无效节点 ID")
            if group["status"] == "verified" and not group.get("id"):
                raise ValueError("缺少已核验节点 ID")
            if group["name"] != definitions[key][0] + " " + marker(token):
                raise ValueError("创建名称与身份不一致")
        if data["session"] is not None:
            if type(data["session"]["pid"]) is not int or not isinstance(data["session"]["root"], str):
                raise ValueError("无效会话记录")
        pending = data["pending"]
        if pending is not None:
            if pending["kind"] != "group" or not isinstance(pending["request"], dict):
                raise ValueError("无效待确认操作")
            uuid.UUID(pending["operation_id"])
            if pending.get("key") not in data["groups"]:
                raise ValueError("待确认操作缺少组记录")
        for key, group in data["groups"].items():
            if group["status"] == "pending" and (pending is None or pending.get("key") != key):
                raise ValueError("未确认组缺少对应操作记录")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise StageError("阶段 sidecar 损坏；保留原文件并人工检查: " + str(exc)) from exc
    return data


def read_bytes(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if path.is_symlink():
        raise StageError("拒绝符号链接 sidecar")
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise StageError("sidecar 必须是普通文件")
        return stream.read()


class Store:
    def __init__(self, path):
        self.path = Path(path).absolute()
        if not self.path.name.endswith(".cwb-stage.json"):
            raise StageError("独立 sidecar 文件名必须以 .cwb-stage.json 结尾；不能使用 project_state.json")
        if not self.path.parent.is_dir():
            raise StageError("sidecar 父目录必须已存在")
        self.previous = None

    def load(self):
        try:
            self.previous = read_bytes(self.path)
        except FileNotFoundError:
            return None
        try:
            return validate(json.loads(self.previous))
        except (ValueError, UnicodeError) as exc:
            raise StageError("sidecar 损坏，未覆盖: " + str(exc)) from exc

    @contextmanager
    def locked(self):
        if os.name != "posix":
            raise StageError("阶段 sidecar 写入需要 POSIX 文件锁")
        import fcntl
        lock = self.path.with_name(self.path.name + ".lock")
        if lock.is_symlink():
            raise StageError("拒绝符号链接锁文件")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) |
                     getattr(os, "O_NONBLOCK", 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise StageError("锁文件必须是普通文件")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StageError("阶段 sidecar 忙；请勿并行 setup/import") from None
            yield
        finally:
            os.close(fd)

    def save(self, data):
        validate(data)
        payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        fd, name = tempfile.mkstemp(prefix=".cwb-stage-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            current = read_bytes(self.path) if self.path.exists() else None
            if current != self.previous or self.path.is_symlink():
                raise StageError("sidecar 已被其他写入者修改，拒绝覆盖")
            if self.previous is None:
                os.link(name, self.path)
            else:
                os.replace(name, self.path)
            self.previous = payload
            directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            except OSError as exc:
                raise StageError("sidecar 已替换，但目录持久化失败；先检查实际记录: " + str(exc)) from exc
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def snapshot(request, document):
    """仅消费现有只读契约；前后查询不构成跨 HTTP 请求的文档锁。"""
    def active():
        docs = request("/docs")["documents"]
        selected = [d for d in docs if d.get("active")]
        if len(selected) != 1 or not selected[0].get("filename"):
            raise StageError("需要唯一、已保存的活动文档")
        paths = [str(Path(d["filename"]).resolve()) for d in docs if d.get("filename")]
        if paths.count(document) != 1 or str(Path(selected[0]["filename"]).resolve()) != document:
            raise StageError("活动文档错误或同一路径打开多份；未授权该文档")
        return selected[0]

    info = active()
    pid = request("/ping")["pid"]
    tree = request("/layers")
    if active() != info or tree.get("document") != info["name"]:
        raise StageError("读取期间活动文档发生变化；停止")
    roots = tree.get("layers", [])
    if len(roots) != 1 or roots[0].get("root") is not True or type(pid) is not int:
        raise StageError("无效图层树或会话信息")
    nodes, parents = {}, {}

    def walk(node, parent):
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id or node_id in nodes:
            raise StageError("图层树含缺失或重复 ID")
        nodes[node_id], parents[node_id] = node, parent
        for child in node.get("children", []):
            walk(child, node_id)

    walk(roots[0], None)
    return {"session": {"pid": pid, "root": roots[0]["id"]},
            "nodes": nodes, "parents": parents, "tree": tree}


def resolve(data, snap):
    """全量预检；先发现失效/歧义再创建。名称、父级、顺序都不是身份。"""
    result = {}
    same_session = data["session"] == snap["session"]
    for key, group in data["groups"].items():
        token = group["token"]
        candidates = [node for node in snap["nodes"].values()
                      if marker(token) in node.get("name", "")]
        if len(candidates) > 1:
            raise StageError("组身份标记歧义，停止: " + key)
        node = snap["nodes"].get(group.get("id")) if same_session else None
        if node is not None and candidates and candidates[0]["id"] != node["id"]:
            raise StageError("节点 ID 与身份标记冲突，停止: " + key)
        if node is None:
            node = candidates[0] if candidates else None
        if node is None or node.get("root") or node.get("type") != "grouplayer":
            raise StageError("组身份失效；无法凭同名恢复，停止: " + key)
        result[key] = node["id"]
    if len(set(result.values())) != len(result):
        raise StageError("多个组身份指向同一节点，停止")
    return result


def ensure_no_conflicts(data, snap, wanted):
    known = {g["token"] for g in data["groups"].values()}
    for node in snap["nodes"].values():
        if node.get("root"):
            continue
        name = node.get("name", "")
        if any(token not in known for token in MARKER.findall(name)):
            raise StageError("发现不属于此 sidecar 的身份标记；不要丢弃/更换 sidecar")
        for key, label, _ in wanted:
            if key not in data["groups"] and (name == label or name.startswith(label + " [cwb:")):
                raise StageError("未登记同名节点冲突，不自动接管: " + label)


def event(data, kind, **fields):
    data["events"].append({"at": now(), "kind": kind, **fields})


def canonical_document(value):
    path = Path(value).expanduser().resolve()
    if path.suffix.lower() != ".kra" or not path.is_file():
        raise StageError("--document 必须是已保存且存在的 .kra 文件；本命令不会保存作品")
    return str(path)


def execute(number, document, state_path, request, *, workflow=None, role=None):
    """request(path, payload=None) 由 CLI 注入；mock 无网络/Qt 依赖。"""
    if workflow is not None and workflow != WORKFLOW:
        raise StageError("未知 workflow")
    if role is not None:
        specs(number, WORKFLOW, role)
    document = canonical_document(document)
    store = Store(state_path)
    with store.locked():
        data = store.load()
        if data is not None and data["document"] != document:
            raise StageError("sidecar 绑定了另一文档，拒绝写入")
        selected_role = role or (data["document_role"] if data else None)
        if data and selected_role != data["document_role"]:
            raise StageError("sidecar 文档角色冲突")
        wanted = specs(number, WORKFLOW, selected_role)
        snap = snapshot(request, document)
        if data is None:
            data = {"schema_version": 2, "workflow_id": WORKFLOW, "document_role": selected_role,
                    "sidecar_id": str(uuid.uuid4()), "document": document, "session": snap["session"],
                    "groups": {}, "pending": None, "events": []}
        if data["pending"] is not None:
            raise StageError("存在未确认操作；先人工核对 sidecar pending 与最新 layers，禁止自动重试")
        ids = resolve(data, snap)
        ensure_no_conflicts(data, snap, wanted)
        for key, node_id in ids.items():
            data["groups"][key]["id"] = node_id
        data["session"] = snap["session"]
        store.save(data)
        created = []
        for key, label, parent in wanted:
            if key in ids:
                continue
            token = str(uuid.uuid4())
            group_name = label + " " + marker(token)
            data["groups"][key] = {"token": token, "id": None, "status": "pending", "name": group_name}
            payload = {"name": group_name, "parent_id": ids[parent] if parent else None}
            operation = {"operation_id": str(uuid.uuid4()), "kind": "group", "key": key, "request": payload}
            result, snap = perform(store, data, request, document, snap, operation)
            ids[key] = result["id"]
            created.append(key)
        return {"stage": number, "workflow_id": WORKFLOW, "document_role": selected_role,
                "created": created, "groups": ids, "state": str(store.path)}


def perform(store, data, request, document, before, operation):
    # 再检查活动文档/会话；现有接口没有原子的 expected-document 条件。
    current = snapshot(request, document)
    if current["session"] != before["session"]:
        raise StageError("写入前会话改变；停止")
    # 组 identity 必须仍然有效，但当前准备创建的 pending 组尚不存在。
    existing = {**data, "groups": {k: g for k, g in data["groups"].items() if g["status"] == "verified"}}
    if resolve(existing, current) != {k: g["id"] for k, g in existing["groups"].items()}:
        raise StageError("写入前组 ID 改变；停止并重新解析")
    data["pending"] = operation
    event(data, "request_prepared", operation_id=operation["operation_id"],
          operation_kind=operation["kind"], request=operation["request"],
          key=operation["key"])
    store.save(data)  # 必须在首次写请求之前可追溯。
    try:
        response = request("/layer/group", operation["request"])
        operation["response"] = response
        event(data, "response_received", operation_id=operation["operation_id"], response=response)
        store.save(data)
        after = snapshot(request, document)
        if after["session"] != current["session"]:
            raise StageError("写后会话改变；结果未确认")
        node_id = response.get("id")
        node = after["nodes"].get(node_id)
        parent_id = operation["request"].get("parent_id") or current["session"]["root"]
        if (node is None or node_id in current["nodes"] or node.get("type") != "grouplayer" or
                node.get("name") != operation["request"]["name"] or after["parents"][node_id] != parent_id or
                response.get("parent_id") != parent_id):
            raise StageError("创建后回读不符；可能已经留下新节点")
        group = data["groups"][operation["key"]]
        group.update(id=node_id, status="verified")
        event(data, "verified", operation_id=operation["operation_id"], id=node_id)
        data["pending"] = None
        store.save(data)
        return response, after
    except (Exception, SystemExit, KeyboardInterrupt) as exc:
        # 不回滚、不删除；保留发送前 payload、响应(若有)、故障和最后可读树。
        data["pending"] = operation
        event(data, "unconfirmed", operation_id=operation["operation_id"], error=str(exc))
        try:
            observed = snapshot(request, document)
            event(data, "failure_readback", tree=observed["tree"], session=observed["session"])
        except (Exception, SystemExit) as read_exc:
            event(data, "failure_readback_failed", error=str(read_exc))
        try:
            store.save(data)
        except Exception as save_exc:
            raise StageError("请求可能已生效且失败记录写入失败；保留发送前 pending，人工核对: " + str(save_exc)) from exc
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise StageError("创建未确认；不自动回滚或重试，查看 sidecar pending: " + str(exc)) from exc


def run(args, http, base):
    if args.stage_cmd == "status":
        store = Store(args.state)
        data = store.load()
        if data is None:
            raise StageError("未找到阶段 sidecar")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    def request(path, payload=None):
        try:
            return http(base + path, payload, timeout=120)
        except SystemExit as exc:
            raise StageError(str(exc)) from exc

    result = execute(args.number, args.document, args.state, request,
                     workflow=args.workflow, role=args.role)
    print(json.dumps(result, ensure_ascii=False, indent=2))
