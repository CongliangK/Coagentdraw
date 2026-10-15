"""CAD-26 offline, human-authored style sidecar. No generation or project writes.

Immutable full revisions, not fields added to CAD-19. Linux dir_fd/flock required.
Pillow is optional, lazy and CLI-only, for registering PNG references.
"""
import contextlib
import copy
import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import uuid
import warnings

SCHEMA = "cwb-style-sidecar-v1"
STORE = "style_tokens.cwb"
DIMENSIONS = ("palette", "proportion", "dominant_shape", "detail_density")
LIMIT = 1024 * 1024
IMAGE_LIMIT = 128 * LIMIT
NOTICE = "独立离线 sidecar；未写入项目主状态，未调用模型，不保证跨图一致性。"


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _load_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON 重复字段: " + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("JSON 非有限值: " + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 8192:
        raise ValueError("文本须非空且不超过 8 KiB")
    return value


def _integer(value):
    return type(value) is int and value >= 0


def _keys(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError("配置字段缺失或含未知字段: " + names)


def _absolute(path):
    try:
        raw = os.fspath(path)
    except TypeError as exc:
        raise ValueError("路径须为非空 POSIX 文本") from exc
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
        raise ValueError("路径须为非空 POSIX 文本")
    parts = raw.split("/")[1:] if raw.startswith("/") else raw.split("/")
    if raw != "/" and any(p in (".", "..", "") for p in parts):
        raise ValueError("拒绝词法路径别名（.、..、重复/末尾分隔符）")
    return os.path.abspath(raw)


@contextlib.contextmanager
def _dir(path):
    absolute = _absolute(path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(absolute).parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, absolute
    finally:
        os.close(fd)


def _read(fd, name, limit=LIMIT):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(handle)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError("只接受限额内无硬链接的普通文件: " + name)
        with os.fdopen(os.dup(handle), "rb") as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(handle)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        current = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if len(raw) > limit or signature(before) != signature(after) or signature(after) != signature(current):
            raise ValueError("读取期间文件改变: " + name)
        return raw
    finally:
        os.close(handle)


def _file(path, limit=LIMIT):
    absolute = _absolute(path)
    with _dir(str(Path(absolute).parent)) as (fd, _):
        return _read(fd, Path(absolute).name, limit)


def _publish(fd, name, raw):
    """Publish a new complete file, never replace an existing name (even symlink)."""
    if len(raw) > LIMIT:
        raise ValueError("sidecar/导出超过 1 MiB")
    temporary = ".style-" + str(uuid.uuid4()) + ".tmp"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
    finally:
        os.unlink(temporary, dir_fd=fd)
    # An error here means publication happened; callers must reopen, not retry blindly.
    try:
        os.fsync(fd)
    except OSError as exc:
        raise OSError("新文件已发布但目录持久化失败；请重开查询: " + name) from exc


def _project(fd, root):
    from cwb_project import validate
    raw = _read(fd, "project_state.json")
    data = validate(_load_json(raw))
    return dict(project_id=data["project_id"], revision=data["revision"], sha256=_sha(raw),
                path=root + "/project_state.json")


@contextlib.contextmanager
def _store(root, *, initialize=False, exclusive=False):
    with _dir(root) as (parent, absolute):
        project = _project(parent, absolute)
        if initialize:
            os.mkdir(STORE, 0o700, dir_fd=parent)  # existing/alias always fails
            os.fsync(parent)  # failure leaves evidence; never remove/reuse the directory
        fd = os.open(STORE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield fd, project
        finally:
            os.close(fd)


def _scope(value):
    if (not isinstance(value, list) or not value or len(value) > 64
            or any(not isinstance(v, str) for v in value) or len(set(value)) != len(value)):
        raise ValueError("scope 须为非空、不重复的文本列表")
    for item in value:
        _text(item)
    if "*" in value and len(value) != 1:
        raise ValueError("全局 scope 只能是 [\"*\"]")


def _config(data, *, captured=False):
    _keys(data, "style_tokens references")
    ref_ids = set()
    for kind in ("references", "style_tokens"):
        entries = data[kind]
        if not isinstance(entries, list) or len(entries) > 128:
            raise ValueError("每类配置须为列表且最多 128 项")
        ids = set()
        for entry in entries:
            fields = "id scope enabled locked "
            fields += ("path source sha256 bytes dimensions" if captured else "path source") if kind == "references" else "dimension value reference_ids"
            _keys(entry, fields)
            if not isinstance(entry["id"], str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", entry["id"]) or entry["id"] in ids:
                raise ValueError("id 必须唯一且为小写字母开头的 ASCII 标识")
            ids.add(entry["id"])
            _scope(entry["scope"])
            if type(entry["enabled"]) is not bool or type(entry["locked"]) is not bool:
                raise ValueError("enabled/locked 必须为布尔值")
            if kind == "references":
                if captured and _absolute(entry["path"]) != entry["path"]:
                    raise ValueError("保存的参考路径须为绝对路径")
                _absolute(entry["path"])
                if entry["source"] is not None:
                    _text(entry["source"])
                if captured:
                    if (not isinstance(entry["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                            or not _integer(entry["bytes"]) or not 0 < entry["bytes"] <= IMAGE_LIMIT
                            or not isinstance(entry["dimensions"], list) or len(entry["dimensions"]) != 2
                            or any(type(v) is not int or v <= 0 for v in entry["dimensions"])
                            or entry["dimensions"][0] * entry["dimensions"][1] > 16_000_000):
                        raise ValueError("参考摘要/尺寸无效")
            else:
                if entry["dimension"] not in DIMENSIONS:
                    raise ValueError("dimension 必须为 " + ", ".join(DIMENSIONS))
                _text(entry["value"])
                refs = entry["reference_ids"]
                if (not isinstance(refs, list) or any(not isinstance(v, str) for v in refs)
                        or len(refs) != len(set(refs)) or not set(refs) <= ref_ids):
                    raise ValueError("token reference_ids 必须引用现有参考 id（可空，不虚构来源）")
        if kind == "references":
            ref_ids = ids
    return data


def _image_dimensions(raw):
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("登记参考图需要 CLI-only 可选 Pillow；不自动安装，不是 Krita 插件依赖") from exc
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            if (image.format != "PNG" or getattr(image, "n_frames", 1) != 1 or raw[24] > 8
                    or image.mode not in ("1", "L", "LA", "P", "RGB", "RGBA")
                    or image.width * image.height > 16_000_000):
                raise ValueError("参考须为单帧 RGBA8 可显示 PNG，最多 1600 万像素")
            image.load()
            return list(image.size)


def _capture_config(config):
    result = copy.deepcopy(_config(config))
    for entry in result["references"]:
        entry["path"] = _absolute(entry["path"])
        raw = _file(entry["path"], IMAGE_LIMIT)
        entry.update(sha256=_sha(raw), bytes=len(raw), dimensions=_image_dimensions(raw))
    for kind in result:
        result[kind].sort(key=lambda e: e["id"])
        for entry in result[kind]:
            entry["scope"].sort()
            if kind == "style_tokens":
                entry["reference_ids"].sort()
    return _config(result, captured=True)


def _transition(previous, current, operation):
    """Validate edits, including lock enforcement, also on reopening the journal."""
    action, kind, item_id = operation["action"], operation["kind"], operation["id"]
    if action == "edit" and (kind is not None or item_id is not None):
        raise ValueError("edit 不接受单项目标")
    if action not in ("edit", "lock", "unlock", "cancel", "enable"):
        raise ValueError("未知修订动作")
    found = False
    for group in ("references", "style_tokens"):
        old = {e["id"]: e for e in previous[group]}
        new = {e["id"]: e for e in current[group]}
        if not set(old) <= set(new):
            raise ValueError("不能删除历史锚点；请显式 cancel 停用")
        for key, entry in new.items():
            prior = old.get(key)
            if action == "edit":
                if prior is None and entry["locked"]:
                    raise ValueError("新项必须先登记，再显式 lock")
                if prior is not None and (entry["locked"] != prior["locked"] or (prior["locked"] and entry != prior)):
                    raise ValueError("锁定项禁止修改/取消/刷新来源；请先显式 unlock")
                if prior is not None and entry["enabled"] != prior["enabled"]:
                    raise ValueError("启用状态须通过显式 cancel/enable 记录，不得用 edit 绕过")
            elif group == kind and key == item_id and prior is not None:
                found = True
                expected = copy.deepcopy(prior)
                if action != "unlock" and prior["locked"]:
                    raise ValueError("锁定项禁止修改/取消；请先显式 unlock")
                field, value = {"lock": ("locked", True), "unlock": ("locked", False),
                                "cancel": ("enabled", False), "enable": ("enabled", True)}[action]
                expected[field] = value
                if entry != expected or entry == prior:
                    raise ValueError("动作无变化或非法修改")
            elif prior != entry:
                raise ValueError("单项动作不得修改其他配置")
    if action != "edit" and not found:
        raise ValueError("未找到动作目标")
    if current == previous:
        raise ValueError("配置没有变化；未追加修订")


def _load(fd, project):
    names = sorted(os.listdir(fd))
    revisions = [name for name in names if name.startswith("revision-")]
    if not revisions:
        raise ValueError("sidecar 缺少初始修订；保留现场并使用新的项目/人工恢复")
    history, previous_raw = [], None
    for index, name in enumerate(revisions):
        if name != f"revision-{index:06d}.json":
            raise ValueError("修订序号缺失/无效")
        raw = _read(fd, name)
        data = _load_json(raw)
        _keys(data, "schema revision project config operation actor reason recorded_at previous_sha256")
        if data["schema"] != SCHEMA or type(data["revision"]) is not int or data["revision"] != index:
            raise ValueError("sidecar 版本/修订无效")
        _keys(data["project"], "project_id revision sha256 path")
        saved_project = data["project"]
        if (saved_project["project_id"] != project["project_id"] or saved_project["path"] != project["path"]
                or not _integer(saved_project["revision"]) or not isinstance(saved_project["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", saved_project["sha256"])):
            raise ValueError("sidecar 所属项目不匹配/项目记录无效")
        if data["previous_sha256"] != (_sha(previous_raw) if previous_raw is not None else None):
            raise ValueError("修订哈希链无效")
        for field in ("actor", "reason", "recorded_at"):
            _text(data[field])
        _config(data["config"], captured=True)
        _keys(data["operation"], "action kind id")
        if index == 0:
            if data["config"] != {"style_tokens": [], "references": []} or data["operation"] != dict(action="init", kind=None, id=None):
                raise ValueError("初始修订无效")
        else:
            _transition(history[-1]["config"], data["config"], data["operation"])
        history.append(data)
        previous_raw = raw
    return history, _sha(previous_raw)


def _revision(fd, project, config, action, actor, reason, history=(), previous_sha256=None, kind=None, item_id=None):
    _text(actor)
    _text(reason)
    data = dict(schema=SCHEMA, revision=len(history), project=project, config=config,
                operation=dict(action=action, kind=kind, id=item_id), actor=actor, reason=reason,
                recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), previous_sha256=previous_sha256)
    _publish(fd, f"revision-{len(history):06d}.json", _json(data))
    return dict(revision=data["revision"], sha256=_sha(_json(data)), notice=NOTICE)


def initialize(root, *, actor, reason):
    _text(actor)
    _text(reason)
    with _store(root, initialize=True, exclusive=True) as (fd, project):
        return _revision(fd, project, dict(style_tokens=[], references=[]), "init", actor, reason)


def _reference_checks(config):
    checks = []
    for entry in config["references"]:
        check = dict(id=entry["id"], path=entry["path"], recorded_sha256=entry["sha256"], current_sha256=None)
        try:
            raw = _file(entry["path"], IMAGE_LIMIT)
            check["current_sha256"] = _sha(raw)
            check["status"] = "ok" if (check["current_sha256"], len(raw)) == (entry["sha256"], entry["bytes"]) else "changed"
        except FileNotFoundError:
            check["status"] = "missing"
        except (ValueError, OSError) as exc:
            check.update(status="invalid", error=str(exc))
        checks.append(check)
    return checks


def _require_current(config):
    for check in _reference_checks(config):
        if check["status"] != "ok":
            raise ValueError("参考图已改变/缺失/无效，请解锁后 edit 显式刷新: " + check["id"])


def update(root, expect_revision, *, actor, reason, config_path=None, action="edit", kind=None, item_id=None):
    if not _integer(expect_revision):
        raise ValueError("必须提供非负 expect-revision")
    _text(actor)
    _text(reason)
    with _store(root, exclusive=True) as (fd, project):
        history, digest = _load(fd, project)
        previous = history[-1]
        if previous["revision"] != expect_revision:
            raise ValueError("修订冲突；请先 style show，不写入")
        if action == "edit":
            config = _capture_config(_load_json(_file(config_path)))
        else:
            if kind not in ("references", "style_tokens") or action not in ("lock", "unlock", "cancel", "enable"):
                raise ValueError("未知状态动作/目标类型")
            config = copy.deepcopy(previous["config"])
            for entry in config[kind]:
                if entry["id"] == item_id:
                    field, value = {"lock": ("locked", True), "unlock": ("locked", False),
                                    "cancel": ("enabled", False), "enable": ("enabled", True)}[action]
                    entry[field] = value
        operation = dict(action=action, kind=kind, id=item_id)
        _transition(previous["config"], config, operation)
        # Unlock/cancel must remain possible even when a source is missing/changed.
        # Other actions may not bless a stale stored source as current.
        if action not in ("unlock", "cancel"):
            _require_current(config)
        return _revision(fd, project, config, action, actor, reason, history, digest, kind, item_id)


def show(root):
    with _store(root) as (fd, project):
        history, digest = _load(fd, project)
        return dict(schema=SCHEMA, latest=history[-1], revision_sha256=digest, history=history,
                    current_project=project, reference_checks=_reference_checks(history[-1]["config"]), notice=NOTICE)


def _applicable(entry, scope):
    return entry["enabled"] and ("*" in entry["scope"] or scope in entry["scope"])


def _prompt(history, digest, scope):
    _text(scope)
    if scope == "*":
        raise ValueError("请选择具体 scope，不用 * 代替当前作品范围")
    latest = history[-1]
    config = latest["config"]
    tokens = sorted((e for e in config["style_tokens"] if _applicable(e, scope)), key=lambda e: e["id"])
    refs = sorted((e for e in config["references"] if _applicable(e, scope)), key=lambda e: e["id"])
    ids = {e["id"] for e in refs}
    if any(not set(e["reference_ids"]) <= ids for e in tokens):
        raise ValueError("适用 token 的参考图已停用或不适用当前 scope；不静默丢弃来源")
    _require_current(dict(references=refs))
    return dict(schema="cwb-style-prompt-v1", scope=scope, project=latest["project"],
                style_revision=latest["revision"], style_revision_sha256=digest,
                style_tokens=tokens, references=refs,
                fragment="\n".join(e["dimension"] + ": " + e["value"] for e in tokens), notice=NOTICE)


def _output_outside_store(output, project):
    absolute = _absolute(output)
    store = str(Path(project["path"]).parent / STORE)
    if absolute == store or absolute.startswith(store + "/"):
        raise ValueError("导出不可写入修订目录")
    return absolute


def prompt(root, scope, output=None):
    with _store(root) as (fd, project):
        history, digest = _load(fd, project)
        result = _prompt(history, digest, scope)
        if output is not None:
            absolute = _output_outside_store(output, project)
            with _dir(str(Path(absolute).parent)) as (parent, _):
                _publish(parent, Path(absolute).name, _json(result))
        return result


def compare_reference(root, reference_id, current, output, *, scope, crop, review=None, candidate_version=None):
    """Public CAD-23 create/show only; before=chosen reference, after=current PNG."""
    from cwb_compare import create, show as show_comparison
    with _store(root) as (fd, project):
        history, digest = _load(fd, project)
        provenance = _prompt(history, digest, scope)
        selected = next((e for e in provenance["references"] if e["id"] == reference_id), None)
        if selected is None:
            raise ValueError("须显式选择一张已启用且适用当前 scope 的参考图")
        if crop is None:
            raise ValueError("须显式提供两图共同原生像素 crop；不自动缩放/对齐")
        review = dict.fromkeys(DIMENSIONS) if review is None else review
        _keys(review, " ".join(DIMENSIONS))
        for value in review.values():
            if value is not None:
                _text(value)
        absolute = _output_outside_store(output, project)
        provenance.update(schema="cwb-style-comparison-ref-v1", reference_id=reference_id,
                          manual_review=review, review_evidence="human_supplied_unverified; null means not reviewed",
                          validation_contract="create has no expected-source-sha; saved provenance is not adapter success; "
                          "success requires saved digest/dimensions and live references checked after create/show; "
                          "no continuous source lock or protection against change-and-restore")
        source_version = _json(provenance).decode("utf-8")
        # CAD-23 owns image IO/rendering and never overwrites output. No seed,
        # workflow or generation request is inferred or supplied by this adapter.
        _require_current(dict(references=provenance["references"]))
        result = create(selected["path"], current, absolute, crop=crop,
                        source_version=source_version, candidate_version=candidate_version)
        try:
            saved = show_comparison(absolute)["manifest"]["sources"]["before"]
            if (saved["sha256"], saved["dimensions"], saved["bytes"]) != (selected["sha256"], selected["dimensions"], selected["bytes"]):
                raise ValueError("比较捕获的参考与风格修订不同")
            _require_current(dict(references=provenance["references"]))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            # We cannot retroactively prevent CAD-23 publication without an
            # expected-source-sha API. Preserve evidence, never blindly delete.
            raise ValueError("比较后验证失败；本次新目录仅供诊断，不是成功的风格比较: " + absolute + "; " + str(exc)) from exc
        result.update(reference_id=reference_id, style_revision=history[-1]["revision"],
                      style_revision_sha256=digest, manual_review=review, style_notice=NOTICE,
                      reference_validation="saved digest/dimensions/bytes and post-call live hashes matched; not a continuous lock")
        return result


def run(args):
    command = args.style_cmd
    if command == "init":
        result = initialize(args.root, actor=args.actor, reason=args.reason)
    elif command == "show":
        result = show(args.root)
    elif command == "edit":
        result = update(args.root, args.expect_revision, actor=args.actor, reason=args.reason, config_path=args.config)
    elif command == "state":
        result = update(args.root, args.expect_revision, actor=args.actor, reason=args.reason,
                        action=args.action, kind=args.kind, item_id=args.id)
    elif command == "prompt":
        result = prompt(args.root, args.scope, args.output)
    else:
        result = compare_reference(args.root, args.reference, args.current, args.output, scope=args.scope,
                                   crop=args.crop, review={name: getattr(args, name) for name in DIMENSIONS},
                                   candidate_version=args.candidate_version)
    print(_json(result).decode("utf-8"), end="")
    return result
