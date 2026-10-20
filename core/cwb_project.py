"""离线项目元数据；资产只读，协作写入必须使用本模块的锁与修订检查。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import uuid

from cwb_workflow import (WORKFLOW, STAGES as WORKFLOW_STAGES,
                          DOCUMENT_STAGES, REQUIRED_ROLES)


STATE = "project_state.json"
LOCK = ".project_state.lock"


class ProjectError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def integer(value, low=0, high=None):
    return type(value) is int and value >= low and (high is None or value <= high)


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ProjectError("资产路径必须是项目内的 POSIX 相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")) or ":" in value:
        raise ProjectError("资产路径不能是绝对路径、驱动器路径或含 . / ..")
    if path.parts[0] in (STATE, LOCK) or path.name.startswith(".project-state-"):
        raise ProjectError("项目状态与锁文件不能作为作品成果")
    return path


def asset_path(root, value):
    relative = relative_path(value)
    target = root.joinpath(*relative.parts)
    # 不通过符号链接登记资产，避免链接重定向及移植后的含义改变。
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ProjectError("资产路径含符号链接: " + value)
    if not target.resolve().is_relative_to(root):
        raise ProjectError("资产路径越出项目: " + value)
    return target


def fingerprint(root, value):
    target = asset_path(root, value)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(target, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ProjectError("资产必须是普通文件: " + value)
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        current = target.stat()
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if signature(before) != signature(after) or signature(after) != signature(current):
            raise ProjectError("读取时资产发生变化，请重试: " + value)
    return digest.hexdigest(), after.st_size


def state_bytes(root):
    path = root / STATE
    if path.is_symlink():
        raise ProjectError("拒绝符号链接状态文件")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ProjectError("状态路径必须是普通文件")
        return stream.read()


def read_state(root):
    try:
        raw = state_bytes(root)
    except FileNotFoundError:
        raise ProjectError("未找到 project_state.json；请先初始化") from None
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ProjectError("project_state.json 损坏；保留原文件，请人工恢复备份") from exc
    return validate(data), raw


@contextmanager
def locked(root):
    # 锁文件永久保留，不能 unlink，否则等待者可能锁住不同 inode。
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if (root / LOCK).is_symlink():
        raise ProjectError("拒绝符号链接锁文件")
    fd = os.open(root / LOCK, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ProjectError("锁路径必须是普通文件")
        if os.name != "posix":
            raise ProjectError("首版项目写入需要 POSIX 文件锁；当前平台不支持")
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProjectError("项目正被另一进程修改，请稍后重试") from None
        yield
    finally:
        os.close(fd)


def atomic_write(root, data, previous):
    validate(data)
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, name = tempfile.mkstemp(prefix=".project-state-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        path = root / STATE
        # 检出未遵循锁约定的手动编辑；不把旧快照静默覆盖回去。
        if path.is_symlink() or (state_bytes(root) if path.exists() else None) != previous:
            raise ProjectError("项目状态已改变；拒绝覆盖，请重新查看状态")
        if previous is None:
            # 创建时 hard-link 提供原子的“不存在才写入”，不覆盖并发新建文件。
            os.link(name, path)
        else:
            os.replace(name, path)
        try:
            directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise ProjectError("状态已替换，但目录持久化失败；请查看状态，勿假定写入未发生: " + str(exc)) from exc
    finally:
        if os.path.exists(name):
            os.unlink(name)


def initialize(root, name):
    if not name.strip():
        raise ProjectError("项目名不能为空")
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with locked(root):
        if (root / STATE).exists() or (root / STATE).is_symlink():
            data, _ = read_state(root)
            if data["name"] != name:
                raise ProjectError("项目已经存在且名称不同，未修改")
            return data  # 幂等初始化，不重置状态或作品。
        data = new_state(name)
        atomic_write(root, data, None)
        return data


def update(root, operation, expected=None):
    root = Path(root).resolve(strict=True)
    with locked(root):
        data, raw = read_state(root)
        if expected is not None and data["revision"] != expected:
            raise ProjectError("修订冲突：预期 {}，当前 {}；未写入".format(expected, data["revision"]))
        before = json.dumps(data, sort_keys=True)
        refresh_reviews(root, data)
        operation(root, data)
        if json.dumps(data, sort_keys=True) != before:
            data["revision"] += 1
            data["updated_at"] = now()
            atomic_write(root, data, raw)
        return data


def set_stage(data, stage, note, status="in_progress", approval=None):
    if not integer(stage, 1, 8) or status not in ("pending", "in_progress", "awaiting_review", "completed"):
        raise ProjectError("无效阶段或状态")
    target = data["stages"][stage - 1]
    if status == "completed":
        decision = lookup(data["decisions"], approval)
        if (decision["target_kind"] != "stage" or decision["target_id"] != target["stage_id"] or
                decision["action"] != "approve" or decision["validity"] != "valid"):
            raise ProjectError("completed 需要本阶段有效的人类批准与必要成果")
        check_outcomes(data, stage, decision["outcomes"])
        if stage == 3 and not any(r["kind"] == "idea" and r["id"] == data["selected_concept_id"] for r in decision["outcomes"]):
            raise ProjectError("当前主线已改变；需要本轮创意重新批准")
        target.update(approval_id=approval, outcomes=decision["outcomes"], needs_review=False)
    old, old_status = data["current_stage"], target["status"]
    if old != stage or old_status != status:
        data["current_stage"], target["status"] = stage, status
        data["stage_history"].append({"from": old, "to": stage, "at": now(), "note": note,
                                      "workflow_id": WORKFLOW,
                                      "previous_target_status": old_status, "status": status})


def record(root, data, path, stage, category, note, *, object_id=None, version=None,
           role="unspecified", document_id=None, kind="working", group_marker=None,
           region=None, preview=None):
    digest, size = fingerprint(root, path)
    stage = stage or data["current_stage"]
    if not integer(stage, 1, 8) or category not in ("temporary", "final"):
        raise ProjectError("无效成果阶段或类别")
    if document_id is not None:
        doc = lookup(data["documents"], document_id)
        if stage not in DOCUMENT_STAGES[doc["role"]]:
            raise ProjectError("成果阶段与文档角色不兼容")
    preview_record = None
    if preview is not None:
        preview_digest, preview_size = fingerprint(root, preview)
        preview_record = {"path": preview, "sha256": preview_digest, "size": preview_size}
    # Changed bytes append a new registration; captured hashes are immutable.
    fields = dict(path=path, sha256=digest, size=size, stage=stage, category=category,
                  note=note, role=role, document_id=document_id, kind=kind,
                  group_marker=group_marker, region=region, preview=preview_record)
    for asset in data["assets"]:
        if all(asset.get(k) == v for k, v in fields.items()) and (object_id is None or asset["object_id"] == object_id) and (version is None or asset["version"] == version):
            return asset
    object_id, version = object_id or str(uuid.uuid4()), version or str(uuid.uuid4())
    identifier(object_id)
    identifier(version)
    if kind not in ("working", "checkpoint"):
        raise ProjectError("kind 必须为 working/checkpoint")
    if kind == "checkpoint":
        if not path.startswith("checkpoints/" + object_id + "/" + version + "/"):
            raise ProjectError("确认快照必须位于 checkpoints/<object-id>/<version>/")
        if preview is not None and not preview.startswith("checkpoints/" + object_id + "/" + version + "/"):
            raise ProjectError("确认预览必须位于同一不覆盖快照目录")
    for asset in data["assets"]:
        if (asset["object_id"], asset["version"]) == (object_id, version):
            raise ProjectError("对象版本已登记；不得替换旧版本")
        if (asset.get("kind") == "checkpoint" or kind == "checkpoint") and path == asset["path"]:
            raise ProjectError("确认快照路径已使用；不得覆盖或复用")
    asset = {"id": str(uuid.uuid4()), "object_id": object_id, "version": version,
             "stage_id": WORKFLOW_STAGES[stage - 1][0], "workflow_id": WORKFLOW,
             "recorded_at": now(), **fields}
    for old in data["assets"]:
        if old["object_id"] == object_id and asset_content(old) != asset_content(asset):
            invalidate(data, old["id"], old["stage"])
    data["assets"].append(asset)
    return asset


def asset_content(asset):
    preview = asset.get("preview")
    return (asset["sha256"], asset["size"],
            (preview["sha256"], preview["size"]) if preview else None,
            asset.get("document_id"), asset.get("role"), asset.get("region"), asset.get("group_marker"))


def inspect(root):
    root = Path(root).resolve(strict=True)
    data, _ = read_state(root)
    checks = []
    for asset in data["assets"]:
        check = {"id": asset["id"], "path": asset["path"]}
        try:
            digest, size = fingerprint(root, asset["path"])
            check["status"] = "ok" if (digest, size) == (asset["sha256"], asset["size"]) else "changed"
        except FileNotFoundError:
            check["status"] = "missing"
        except (OSError, ProjectError) as exc:
            check.update(status="invalid", error=str(exc))
        checks.append(check)
    references = []
    refresh_reviews(root, data)  # effective read-only view; no lock/file writes
    for doc in data["documents"]:
        try:
            fingerprint(root, doc["path"])
            status = "working_file_present_not_approval"
        except (OSError, ProjectError):
            status = "unbound_needs_confirmation"
        references.append({"document_id": doc["id"], "status": status})
    for idea in data["ideas"]:
        if idea["text_ref"]:
            try:
                digest, _ = fingerprint(root, idea["text_ref"].split("#", 1)[0])
                status = "ok" if digest == idea["text_ref_sha256"] else "changed_needs_confirmation"
            except (OSError, ProjectError):
                status = "unbound_needs_confirmation"
            references.append({"idea_id": idea["id"], "status": status})
    return {"state": data, "asset_checks": checks, "reference_checks": references,
            "workflow": WORKFLOW,
            "read_only": True}


def identifier(value):
    if not isinstance(value, str) or not value or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in value):
        raise ProjectError("ID/版本仅允许非空 ASCII 字母数字、下划线与连字符")
    return value


def lookup(entries, value):
    matches = [e for e in entries if e["id"] == value]
    if len(matches) != 1:
        raise ProjectError("引用未绑定或多义: " + str(value))
    return matches[0]


def new_state(name):
    timestamp = now()
    return {"schema_version": 2, "workflow_id": WORKFLOW, "project_id": str(uuid.uuid4()),
            "name": name, "revision": 0, "created_at": timestamp, "updated_at": timestamp,
            "current_stage": 1, "intent_card": "", "design_pillars": [], "style_anchors": [],
            "ideas": [], "selected_concept_id": None, "documents": [], "assets": [], "decisions": [],
            "stages": [{"stage": i, "stage_id": item[0], "status": "in_progress" if i == 1 else "pending",
                        "outcomes": [], "approval_id": None, "needs_review": False}
                       for i, item in enumerate(WORKFLOW_STAGES, 1)],
            "stage_history": [{"from": None, "to": 1, "at": timestamp, "note": "初始化",
                               "workflow_id": WORKFLOW}]}


def validate(data):
    """Validate relationships without treating disk existence as a schema invariant."""
    try:
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 2:
            raise ValueError("不支持的项目状态版本；需要 schema_version=2")
        if data["workflow_id"] != WORKFLOW:
            raise ValueError("未知 workflow_id")
        uuid.UUID(data["project_id"])
        if not isinstance(data["name"], str) or not data["name"].strip():
            raise ValueError("无效项目名")
        if not integer(data["revision"]) or not integer(data["current_stage"], 1, 8):
            raise ValueError("无效修订或阶段")
        for key in ("created_at", "updated_at", "intent_card"):
            if not isinstance(data[key], str):
                raise ValueError("无效文本: " + key)
        for key in ("design_pillars", "style_anchors"):
            if not isinstance(data[key], list) or not all(isinstance(x, str) for x in data[key]):
                raise ValueError("无效列表: " + key)
        for key in ("ideas", "documents", "assets", "decisions", "stages", "stage_history"):
            if not isinstance(data[key], list):
                raise ValueError("无效列表: " + key)
        for key in ("ideas", "documents", "assets", "decisions"):
            seen = set()
            for entry in data[key]:
                identifier(entry["id"])
                if entry["id"] in seen:
                    raise ValueError("重复 ID: " + key)
                seen.add(entry["id"])
        for idea in data["ideas"]:
            identifier(idea["version"])
            if not isinstance(idea["title"], str) or not idea["title"].strip() or not isinstance(idea["text"], str):
                raise ValueError("无效起点文本")
            if idea["text_ref"] is not None:
                relative_path(idea["text_ref"].split("#", 1)[0])
                digest = idea["text_ref_sha256"]
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("全文引用缺少登记摘要")
            elif idea["text_ref_sha256"] is not None:
                raise ValueError("纯文本起点不应伪造全文摘要")
            if not idea["text"] and not idea["text_ref"]:
                raise ValueError("起点需要短文本或全文位置")
            if not isinstance(idea["source_idea_ids"], list) or not isinstance(idea["cluster_ids"], list):
                raise ValueError("无效起点来源")
            for source in idea["source_idea_ids"]:
                lookup(data["ideas"], source)
                if source == idea["id"]:
                    raise ValueError("起点不能引用自身")
            for cluster in idea["cluster_ids"]:
                identifier(cluster)
        # Iterative DAG check: no small-pool quota or recursion-depth limit.
        idea_index = {i["id"]: i for i in data["ideas"]}
        done, active = set(), set()
        for idea_id in idea_index:
            stack = [(idea_id, False)]
            while stack:
                node, leaving = stack.pop()
                if leaving:
                    active.discard(node)
                    done.add(node)
                elif node not in done:
                    if node in active:
                        raise ValueError("起点来源循环")
                    active.add(node)
                    stack.append((node, True))
                    stack.extend((s, False) for s in idea_index[node]["source_idea_ids"] if s not in done)
        if data["selected_concept_id"] is not None:
            lookup(data["ideas"], data["selected_concept_id"])
        for doc in data["documents"]:
            relative_path(doc["path"])
            if Path(doc["path"]).suffix.lower() != ".kra" or doc["role"] not in DOCUMENT_STAGES:
                raise ValueError("无效 KRA 文档角色")
            if doc["concept_id"] is not None:
                lookup(data["ideas"], doc["concept_id"])
            if doc["role"] != "ideation" and doc["concept_id"] is None:
                raise ValueError("角色/插画文档需关联创意")
            if doc["role"] == "illustration":
                identifier(doc["illustration_id"])
        versions, checkpoints = set(), set()
        for asset in data["assets"]:
            relative_path(asset["path"])
            identifier(asset["object_id"])
            identifier(asset["version"])
            pair = asset["object_id"], asset["version"]
            if pair in versions:
                raise ValueError("重复对象版本")
            versions.add(pair)
            if not integer(asset["stage"], 1, 8) or asset["category"] not in ("temporary", "final"):
                raise ValueError("无效资产类别/阶段")
            digest = asset["sha256"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("无效摘要")
            if not integer(asset["size"]) or not isinstance(asset["note"], str) or not isinstance(asset["recorded_at"], str):
                raise ValueError("无效资产记录")
            if asset["workflow_id"] != WORKFLOW or asset["stage_id"] != WORKFLOW_STAGES[asset["stage"] - 1][0]:
                raise ValueError("资产阶段语义不匹配")
            if not isinstance(asset["role"], str) or not asset["role"].strip():
                raise ValueError("资产需明确角色")
            if asset["document_id"] is not None:
                doc = lookup(data["documents"], asset["document_id"])
                if asset["stage"] not in DOCUMENT_STAGES[doc["role"]]:
                    raise ValueError("资产/文档阶段不匹配")
            if asset["kind"] not in ("working", "checkpoint"):
                raise ValueError("未知资产身份")
            if asset["kind"] == "checkpoint":
                prefix = "checkpoints/" + asset["object_id"] + "/" + asset["version"] + "/"
                if not asset["path"].startswith(prefix) or asset["path"] in checkpoints:
                    raise ValueError("确认快照位置冲突")
                checkpoints.add(asset["path"])
            region = asset["region"]
            if region is not None and (set(region) != {"x", "y", "width", "height"} or
                    any(not integer(region[k], 1 if k in ("width", "height") else 0) for k in region)):
                raise ValueError("区域必须为画布非负坐标和正整数尺寸")
            if asset["group_marker"] is not None:
                if str(uuid.UUID(asset["group_marker"])) != asset["group_marker"]:
                    raise ValueError("无效持久组标记")
            if asset["preview"] is not None:
                preview = asset["preview"]
                relative_path(preview["path"])
                if len(preview["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in preview["sha256"]) or not integer(preview["size"]):
                    raise ValueError("无效预览摘要")
                if asset["kind"] == "checkpoint" and not preview["path"].startswith(prefix):
                    raise ValueError("确认预览不在快照目录")
        for decision in data["decisions"]:
            if decision["target_kind"] not in ("idea", "asset", "stage") or decision["action"] not in ("approve", "select", "reject", "defer"):
                raise ValueError("无效决定")
            if decision["actor"] != "human" or not decision["source"].strip() or not decision["scope"].strip() or not isinstance(decision["reason"], str) or not isinstance(decision["at"], str):
                raise ValueError("需要实际人类来源/范围/时间")
            if decision["validity"] not in ("valid", "needs_review"):
                raise ValueError("无效批准有效性")
            if not isinstance(decision["outcomes"], list) or not decision["outcomes"]:
                raise ValueError("决定需要版本化成果引用")
            for outcome in decision["outcomes"]:
                if outcome["kind"] not in ("asset", "idea"):
                    raise ValueError("未知成果引用类型")
                target = lookup(data["assets"] if outcome["kind"] == "asset" else data["ideas"], outcome["id"])
                if target["version"] != outcome["version"] or (outcome["kind"] == "asset" and target["sha256"] != outcome["sha256"]):
                    raise ValueError("批准版本不匹配")
            if decision["target_kind"] == "stage":
                if decision["action"] != "approve" or decision["target_version"] != WORKFLOW:
                    raise ValueError("无效版本化阶段批准")
                stage_number = [s[0] for s in WORKFLOW_STAGES].index(decision["target_id"]) + 1
                check_outcomes(data, stage_number, decision["outcomes"])
            else:
                target = lookup(data["assets"] if decision["target_kind"] == "asset" else data["ideas"], decision["target_id"])
                if decision["target_version"] != target["version"]:
                    raise ValueError("决定版本不匹配")
                if not any(r["kind"] == decision["target_kind"] and r["id"] == target["id"] for r in decision["outcomes"]):
                    raise ValueError("决定未绑定目标成果")
                if decision["action"] == "approve" and decision["target_kind"] == "asset" and target["kind"] != "checkpoint":
                    raise ValueError("不能批准可变工作文件")
                if decision["action"] == "select" and decision["target_kind"] != "idea":
                    raise ValueError("select 仅支持创意")
        if len(data["stages"]) != 8:
            raise ValueError("需要八条阶段记录")
        for number, stage in enumerate(data["stages"], 1):
            if type(stage["stage"]) is not int or stage["stage"] != number or stage["stage_id"] != WORKFLOW_STAGES[number - 1][0] or stage["status"] not in ("pending", "in_progress", "awaiting_review", "completed") or type(stage["needs_review"]) is not bool or not isinstance(stage["outcomes"], list):
                raise ValueError("无效版本化阶段")
            if stage["status"] == "completed":
                decision = lookup(data["decisions"], stage["approval_id"])
                if decision["target_kind"] != "stage" or decision["target_id"] != stage["stage_id"] or decision["action"] != "approve" or decision["outcomes"] != stage["outcomes"]:
                    raise ValueError("完成缺少对应批准/成果")
                if decision["validity"] != "valid" and not stage["needs_review"]:
                    raise ValueError("失效批准未标复核")
        for history in data["stage_history"]:
            if history["workflow_id"] != WORKFLOW or not integer(history["to"], 1, 8) or (history["from"] is not None and not integer(history["from"], 1, 8)) or not isinstance(history["at"], str) or not isinstance(history["note"], str):
                raise ValueError("阶段历史缺少流程来源")
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise ProjectError("项目状态结构损坏: " + str(exc)) from exc
    return data


def add_idea(root, data, idea_id, title, text="", text_ref=None, sources=(), clusters=()):
    identifier(idea_id)
    text_ref_sha256 = None
    if text_ref:
        text_ref_sha256, _ = fingerprint(root, text_ref.split("#", 1)[0])
    for source in sources:
        lookup(data["ideas"], source)
    fields = dict(id=idea_id, title=title, text=text, text_ref=text_ref,
                  text_ref_sha256=text_ref_sha256, source_idea_ids=list(sources), cluster_ids=list(clusters))
    if any(i["id"] == idea_id for i in data["ideas"]):
        old = lookup(data["ideas"], idea_id)
        if all(old[k] == v for k, v in fields.items()):
            return old
        raise ProjectError("起点 ID 已存在；拆分/合并/替代请新建 ID 并保留来源")
    idea = {**fields, "version": str(uuid.uuid4()), "created_at": now()}
    data["ideas"].append(idea)
    return idea


def add_document(root, data, doc_id, path, role, concept=None, illustration=None):
    identifier(doc_id)
    fingerprint(root, path)  # working KRA hash deliberately not pinned to an approval
    entry = dict(id=doc_id, path=path, role=role, concept_id=concept, illustration_id=illustration)
    if any(d["id"] == doc_id for d in data["documents"]):
        if lookup(data["documents"], doc_id) != entry:
            raise ProjectError("文档身份已绑定；新版本/另存文件请使用新 document_id")
    else:
        data["documents"].append(entry)


def outcome_ref(data, value):
    kind, sep, item_id = value.partition(":")
    if not sep or kind not in ("idea", "asset"):
        raise ProjectError("成果引用格式为 idea:ID 或 asset:ID")
    item = lookup(data["ideas"] if kind == "idea" else data["assets"], item_id)
    return {"kind": kind, "id": item_id, "version": item["version"],
            "sha256": item.get("sha256")}


def check_outcomes(data, number, outcomes):
    if not outcomes:
        raise ProjectError("阶段批准必须引用必要成果")
    assets = [lookup(data["assets"], o["id"]) for o in outcomes if o["kind"] == "asset"]
    ideas = [o for o in outcomes if o["kind"] == "idea"]
    if number == 1 and not ideas:
        raise ProjectError("文本起点阶段需起点成果")
    if number == 3 and not ideas:
        raise ProjectError("创意选择需创意成果引用")
    if number == 2 and not {"research_materials", "research_board"}.intersection(a["role"] for a in assets):
        raise ProjectError("缺少阶段必要成果角色: research_materials 或 research_board")
    if not REQUIRED_ROLES.get(number, set()).issubset({a["role"] for a in assets}):
        raise ProjectError("缺少阶段必要成果角色: " + str(sorted(REQUIRED_ROLES.get(number, set()))))
    for asset in assets:
        if asset["workflow_id"] != WORKFLOW or asset["stage"] != number or asset["kind"] != "checkpoint":
            raise ProjectError("阶段批准只接受本流程本阶段的不变确认快照")
        if asset["role"] not in ("delivery_manifest", "editable_source") and (asset["document_id"] is None or (asset["region"] is None and asset["group_marker"] is None)):
            raise ProjectError("视觉成果需文档与持久组标记或画布区域")


def add_decision(root, data, kind, target_id, action, scope, source, reason="", outcomes=()):
    if not source.strip() or not scope.strip():
        raise ProjectError("必须保存实际人类指令来源或标明人工转录，以及适用范围")
    if kind == "stage":
        if action != "approve":
            raise ProjectError("stage 决定仅支持 approve")
        number = [s[0] for s in WORKFLOW_STAGES].index(target_id) + 1
        refs = [outcome_ref(data, value) for value in outcomes]
        check_outcomes(data, number, refs)
        if number == 3 and not any(r["kind"] == "idea" and r["id"] == data["selected_concept_id"] for r in refs):
            raise ProjectError("创意选择需已由人类选中的创意")
        target_version = WORKFLOW
    else:
        target = lookup(data["ideas"] if kind == "idea" else data["assets"], target_id)
        target_version = target["version"]
        refs = [outcome_ref(data, kind + ":" + target_id)]
        if action == "select" and kind != "idea":
            raise ProjectError("select 仅用于创意；不会切换 Krita 可见状态")
        if action == "approve" and kind == "asset" and target["kind"] != "checkpoint":
            raise ProjectError("批准需要独立确认快照，不批准可变工作文件")
    for ref in refs:
        verify_outcome(root, data, ref)
    decision = dict(id=str(uuid.uuid4()), target_kind=kind, target_id=target_id,
                    target_version=target_version, action=action, scope=scope, source=source,
                    actor="human", attribution="user_supplied_or_manual_transcription_not_authenticated",
                    at=now(), reason=reason, outcomes=refs, validity="valid", execution="recorded_not_applied")
    data["decisions"].append(decision)
    if action == "select":
        data["selected_concept_id"] = target_id
    return decision


def verify_outcome(root, data, ref):
    item = lookup(data["assets"] if ref["kind"] == "asset" else data["ideas"], ref["id"])
    if item["version"] != ref["version"]:
        raise ProjectError("成果版本已变化")
    if ref["kind"] == "asset":
        if fingerprint(root, item["path"]) != (ref["sha256"], item["size"]):
            raise ProjectError("确认快照内容已变化，需复核")
        if item["preview"] is not None:
            p = item["preview"]
            if fingerprint(root, p["path"]) != (p["sha256"], p["size"]):
                raise ProjectError("确认预览已变化，需复核")
    elif item["text_ref"]:
        if fingerprint(root, item["text_ref"].split("#", 1)[0])[0] != item["text_ref_sha256"]:
            raise ProjectError("全文引用已变化，需新起点版本与人工复核")


def invalidate(data, asset_id, stage):
    affected = {stage}
    if stage == 5:
        affected.update((6, 7, 8))
    if stage == 6:
        affected.update((7, 8))
    if stage == 7:
        affected.add(8)
    for decision in data["decisions"]:
        if any(r["kind"] == "asset" and r["id"] == asset_id for r in decision["outcomes"]) or (decision["target_kind"] == "stage" and decision["target_id"] in {WORKFLOW_STAGES[n - 1][0] for n in affected}):
            decision["validity"] = "needs_review"
    for number in affected:
        stage_record = data["stages"][number - 1]
        stage_record["needs_review"] = True
        if stage_record["status"] == "completed":
            stage_record["status"] = "awaiting_review"


def refresh_reviews(root, data):
    for decision in data["decisions"]:
        if decision["validity"] != "valid":
            continue
        for ref in decision["outcomes"]:
            try:
                verify_outcome(root, data, ref)
            except (OSError, ProjectError):
                decision["validity"] = "needs_review"
                if ref["kind"] == "asset":
                    asset = lookup(data["assets"], ref["id"])
                    invalidate(data, asset["id"], asset["stage"])
                elif decision["target_kind"] == "stage":
                    n = [s[0] for s in WORKFLOW_STAGES].index(decision["target_id"])
                    data["stages"][n].update(needs_review=True, status="awaiting_review")


def run(args):
    command = args.project_cmd
    if command == "init":
        if args.root is None:
            if args.name in (".", "..") or any(c in args.name for c in ("/", "\\", ":", "\x00")):
                raise ProjectError("默认目录的项目名必须是单个目录名；可用 --root 指定目录")
            root = Path(__file__).resolve().parents[1] / "项目" / args.name
        else:
            root = args.root
        result = initialize(root, args.name)
    elif command == "status":
        result = inspect(args.root)
    else:
        def operation(root, data):
            if command == "edit":
                if (args.pillar and args.clear_pillars) or (args.anchor and args.clear_anchors):
                    raise ProjectError("不能同时替换和清空同一列表")
                if args.intent is not None:
                    data["intent_card"] = args.intent
                for key, value, clear in (("design_pillars", args.pillar, args.clear_pillars), ("style_anchors", args.anchor, args.clear_anchors)):
                    if value is not None or clear:
                        data[key] = [] if clear else value
            elif command == "stage":
                set_stage(data, args.number, args.note, args.status, args.approval)
            elif command == "idea":
                add_idea(root, data, args.id, args.title, args.text, args.text_ref, args.source, args.cluster)
            elif command == "document":
                add_document(root, data, args.id, args.path, args.role, args.concept, args.illustration)
            elif command == "decision":
                add_decision(root, data, args.kind, args.target, args.action, args.scope, args.source, args.reason, args.outcome)
            else:
                region = dict(zip(("x", "y", "width", "height"), args.region)) if args.region is not None else None
                record(root, data, args.path, args.stage, args.category, args.note,
                       object_id=args.object_id, version=args.version, role=args.role,
                       document_id=args.document_id, kind=args.kind,
                       group_marker=args.group_marker, region=region, preview=args.preview)
        result = update(args.root, operation, args.expect_revision)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result
