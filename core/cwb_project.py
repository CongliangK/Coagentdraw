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


STAGES = ("意图", "调研", "提案", "探索", "锁定", "收敛", "交付", "插画")
STATE = "project_state.json"
LOCK = ".project_state.lock"


class ProjectError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def stage_dir(stage):
    return "stages/{:02d}-{}".format(stage, STAGES[stage - 1])


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


def validate(data):
    try:
        if not isinstance(data, dict) or type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("不支持的 schema_version")
        uuid.UUID(data["project_id"])
        if not isinstance(data["name"], str) or not data["name"].strip():
            raise ValueError("无效项目名")
        if not integer(data["revision"]) or not integer(data["current_stage"], 1, 8):
            raise ValueError("无效修订或阶段")
        for field in ("created_at", "updated_at", "intent_card"):
            if not isinstance(data[field], str):
                raise ValueError("无效字段: " + field)
        for field in ("design_pillars", "style_anchors"):
            if not isinstance(data[field], list) or not all(isinstance(v, str) for v in data[field]):
                raise ValueError("无效字段: " + field)
        if not isinstance(data["stages"], list) or len(data["stages"]) != 8:
            raise ValueError("需要八个阶段状态")
        for index, stage in enumerate(data["stages"], 1):
            if stage["stage"] != index or stage["directory"] != stage_dir(index) or stage["status"] not in ("pending", "in_progress", "completed"):
                raise ValueError("无效阶段状态")
        if not isinstance(data["stage_history"], list) or not isinstance(data["assets"], list):
            raise ValueError("无效历史或资产索引")
        for entry in data["stage_history"]:
            if not integer(entry["to"], 1, 8) or (entry["from"] is not None and not integer(entry["from"], 1, 8)) or not isinstance(entry["at"], str) or not isinstance(entry["note"], str):
                raise ValueError("无效阶段历史")
        ids = set()
        for entry in data["assets"]:
            uuid.UUID(entry["id"])
            if entry["id"] in ids:
                raise ValueError("重复资产 ID")
            ids.add(entry["id"])
            relative_path(entry["path"])
            if not integer(entry["stage"], 1, 8) or entry["category"] not in ("temporary", "final"):
                raise ValueError("无效成果阶段或类别")
            if not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in entry["sha256"]):
                raise ValueError("无效资产哈希")
            if not integer(entry["size"]) or not isinstance(entry["recorded_at"], str) or not isinstance(entry["note"], str):
                raise ValueError("无效资产记录")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ProjectError("项目状态结构损坏: " + str(exc)) from exc
    return data


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
        for stage in range(1, 9):
            path = asset_path(root, stage_dir(stage))
            path.mkdir(parents=True, exist_ok=True)
        timestamp = now()
        data = {"schema_version": 1, "project_id": str(uuid.uuid4()), "name": name,
                "revision": 0, "created_at": timestamp, "updated_at": timestamp,
                "current_stage": 1, "intent_card": "", "design_pillars": [], "style_anchors": [],
                "stages": [{"stage": i, "directory": stage_dir(i), "status": "in_progress" if i == 1 else "pending"} for i in range(1, 9)],
                "stage_history": [{"from": None, "to": 1, "at": timestamp, "note": "初始化"}], "assets": []}
        atomic_write(root, data, None)
        return data


def update(root, operation, expected=None):
    root = Path(root).resolve(strict=True)
    with locked(root):
        data, raw = read_state(root)
        if expected is not None and data["revision"] != expected:
            raise ProjectError("修订冲突：预期 {}，当前 {}；未写入".format(expected, data["revision"]))
        before = json.dumps(data, sort_keys=True)
        operation(root, data)
        if json.dumps(data, sort_keys=True) != before:
            data["revision"] += 1
            data["updated_at"] = now()
            atomic_write(root, data, raw)
        return data


def set_stage(data, stage, note, status="in_progress"):
    if not integer(stage, 1, 8):
        raise ProjectError("阶段必须是 1–8")
    old = data["current_stage"]
    old_status = data["stages"][stage - 1]["status"]
    if status not in ("pending", "in_progress", "completed"):
        raise ProjectError("无效阶段状态")
    if old != stage or old_status != status:
        # 不推断旧阶段已验收完成；完成状态由用户显式填写。
        data["current_stage"] = stage
        data["stages"][stage - 1]["status"] = status
        data["stage_history"].append({"from": old, "to": stage, "at": now(), "note": note,
                                      "previous_target_status": old_status, "status": status})


def record(root, data, path, stage, category, note):
    digest, size = fingerprint(root, path)
    stage = stage or data["current_stage"]
    for asset in data["assets"]:
        if (asset["path"], asset["sha256"], asset["stage"], asset["category"], asset["note"]) == (path, digest, stage, category, note):
            return
    data["assets"].append({"id": str(uuid.uuid4()), "path": path, "sha256": digest,
                           "size": size, "stage": stage, "category": category,
                           "recorded_at": now(), "note": note})


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
    return {"state": data, "asset_checks": checks}


def run(args):
    command = args.project_cmd
    if command == "init":
        if args.root is None:
            if args.name in (".", "..") or any(c in args.name for c in ("/", "\\", ":", "\x00")):
                raise ProjectError("默认目录的项目名必须是单个目录名；可用 --root 指定目录")
            root = Path(__file__).resolve().parents[1] / "_本地暂存" / args.name
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
                set_stage(data, args.number, args.note, args.status)
            else:
                record(root, data, args.path, args.stage, args.category, args.note)
        result = update(args.root, operation, args.expect_revision)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result
