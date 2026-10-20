"""CAD-30: offline, explicit-selection export; never writes project assets/state."""
import ctypes
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile

from cwb_project import ProjectError, validate

ROLES = ("kra", "final_png", "setting", "workflow", "design_description",
         "reference_sources", "version_record")


def _relative(value):
    if (not isinstance(value, str) or not value or any(c in value for c in ("\\", ":", "\x00"))
            or any(p in ("", ".", "..") for p in value.split("/"))
            or PurePosixPath(value).is_absolute()):
        raise ProjectError("不安全的项目相对路径: " + str(value))
    return value.split("/")


def _absolute(path):
    """Reject symlinks in every existing component, including dangling links."""
    path = Path(os.path.abspath(path))
    for node in reversed((path, *path.parents)):
        if node.is_symlink():
            raise ProjectError("路径含符号链接: " + str(node))
    return path


def _signature(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _open(root, relative):
    # Walk with openat/O_NOFOLLOW: no intermediate symlink race can escape root.
    parts = _relative(relative)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open("/", flags | os.O_DIRECTORY)
    try:
        for part in [*Path(root).parts[1:], *parts[:-1]]:
            new = os.open(part, flags | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = new
        return os.open(parts[-1], flags, dir_fd=fd)
    finally:
        os.close(fd)


def _snapshot(root, relative, destination=None):
    fd = _open(root, relative)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ProjectError("只允许普通文件: " + relative)
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
        after = os.fstat(source.fileno())
        if _signature(before) != _signature(after):
            raise ProjectError("读取期间源文件改变: " + relative)
    # Reopen through the safe path to detect replacement or path-component changes.
    try:
        fd = _open(root, relative)
    except FileNotFoundError as exc:
        raise ProjectError("读取后源路径消失: " + relative) from exc
    try:
        if _signature(after) != _signature(os.fstat(fd)):
            raise ProjectError("源路径已改变: " + relative)
    finally:
        os.close(fd)
    return (digest.hexdigest(), after.st_size, _signature(after))


def _remember_snapshot(snapshots, relative, snapshot):
    """Every source role shares the first observation; later reads may not replace it."""
    if snapshots.setdefault(relative, snapshot) != snapshot:
        raise ProjectError("导出期间源文件改变: " + relative)


def _file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_json(root, relative):
    buffer = io.BytesIO()
    snapshot = _snapshot(root, relative, buffer)
    return json.loads(buffer.getvalue()), buffer.getvalue(), snapshot


def _rename_new(source, target):
    """Linux atomic directory publication without overwriting even an empty directory."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise ProjectError("当前平台缺少 renameat2；未发布输出")
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def export_project(root, output, assets, roles=(), metadata=None, zipped=False):
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise ProjectError("安全导出需要 POSIX O_NOFOLLOW；当前平台不支持")
    root = _absolute(root)
    if not root.is_dir():
        raise ProjectError("项目目录不存在")
    output = _absolute(output)
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ProjectError("输出与项目目录不得互相嵌套")
    if not output.parent.is_dir():
        raise ProjectError("输出父目录必须已存在")
    if os.path.lexists(output):
        raise FileExistsError(errno.EEXIST, "输出已存在，拒绝覆盖", str(output))
    snapshots = {}
    data, raw, state_snapshot = _read_json(root, "project_state.json")
    _remember_snapshot(snapshots, "project_state.json", state_snapshot)
    validate(data)  # consume CAD-19 without changing its schema or implementation
    if not assets or len(assets) != len(set(assets)):
        raise ProjectError("必须显式选择资产，且不能重复")
    index = {entry["id"]: entry for entry in data["assets"]}
    selected = []
    paths = []
    for asset_id in assets:
        if asset_id not in index or index[asset_id]["category"] != "final":
            raise ProjectError("只能选择已登记的 final 资产: " + asset_id)
        entry = index[asset_id]
        parts = _relative(entry["path"])
        path = PurePosixPath(*parts)
        if any(path == old or path in old.parents or old in path.parents for old in paths):
            raise ProjectError("重复或嵌套的成果目标: " + entry["path"])
        paths.append(path)
        selected.append(entry)
    role_map = {role: [] for role in ROLES}
    for value in roles:
        role, sep, asset_id = value.partition("=")
        if not sep or role not in role_map or asset_id not in assets or asset_id in role_map[role]:
            raise ProjectError("无效或重复的用途映射: " + value)
        role_map[role].append(asset_id)
    extra, extra_raw, extra_snapshot = {}, None, None
    if metadata is not None:
        extra, extra_raw, extra_snapshot = _read_json(root, metadata)
        _remember_snapshot(snapshots, metadata, extra_snapshot)
        if (not isinstance(extra, dict) or set(extra) - {"description", "references", "versions"}
                or not isinstance(extra.get("description", ""), str)
                or any(not isinstance(extra.get(key, []), list)
                       or not all(isinstance(v, str) for v in extra.get(key, []))
                       for key in ("references", "versions"))):
            raise ProjectError("sidecar 仅接受 description 字符串和 references/versions 字符串列表")
    # Private staging is outside the project. Only this directory is ever cleaned up.
    stage = Path(tempfile.mkdtemp(prefix=".cwb-export-", dir=output.parent))
    try:
        bundle = stage / "bundle"
        bundle.mkdir()
        (bundle / "project_state.json").write_bytes(raw)
        if metadata is not None:
            (bundle / "export_metadata.json").write_bytes(extra_raw)
        records, missing_assets, available = [], [], set()
        for entry in sorted(selected, key=lambda e: (e["path"], e["id"])):
            target = bundle / "assets" / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open("xb") as stream:
                    snapshot = _snapshot(root, entry["path"], stream)
            except FileNotFoundError:
                if target.exists():
                    target.unlink()
                missing_assets.append({"id": entry["id"], "path": entry["path"], "reason": "源文件缺失"})
                continue
            _remember_snapshot(snapshots, entry["path"], snapshot)
            if (snapshot[:2] != (entry["sha256"], entry["size"])
                    or _file_hash(target) != snapshot[0] or target.stat().st_size != snapshot[1]):
                raise ProjectError("源资产 hash/size 与登记不符: " + entry["path"])
            available.add(entry["id"])
            records.append({"id": entry["id"], "path": "assets/" + entry["path"],
                            "sha256": snapshot[0], "size": snapshot[1], "stage": entry["stage"],
                            "roles": sorted(k for k, ids in role_map.items() if entry["id"] in ids)})
        missing = [role for role, ids in role_map.items() if not available.intersection(ids)]
        supplied = {"design_description": bool(extra.get("description", "").strip()),
                    "reference_sources": any(v.strip() for v in extra.get("references", [])),
                    "version_record": any(v.strip() for v in extra.get("versions", []))}
        # Stage history is preserved verbatim, not inferred to be artwork-version history.
        missing = [role for role in missing if not supplied.get(role, False)]
        if not data["ideas"]:
            missing.append("ideas")
        report = {"format_version": 1, "project_id": data["project_id"], "revision": data["revision"],
                  "current_stage": data["current_stage"], "assets": records,
                  "missing": sorted(missing), "missing_assets": missing_assets,
                  "verification": "仅文件完整性；未验证真实 KRA 保存/重开或完整八阶段作品"}
        description = {"name": data["name"], "intent_card": data["intent_card"],
                       "design_pillars": data["design_pillars"], "style_anchors": data["style_anchors"],
                       "stage_history": data["stage_history"], "notes": extra,
                       "missing": report["missing"], "verification": report["verification"]}
        report.update(schema_version=2, workflow_id=data["workflow_id"])
        description.update({k: data[k] for k in ("workflow_id", "ideas", "selected_concept_id", "documents", "decisions")})
        for record in records:
            source = index[record["id"]]
            record.update({k: source[k] for k in ("object_id", "version", "role", "workflow_id", "stage_id", "document_id", "kind")})
        (bundle / "design_notes.json").write_bytes(_json_bytes(description))
        lines = ["# " + data["name"], "", report["verification"], "", "## 意图卡", data["intent_card"],
                 "", "## 设计支柱", *["- " + v for v in data["design_pillars"]], "", "## 设计说明",
                 extra.get("description", "（未提供文本；另见显式选中成果）"), "", "## 缺失项",
                 *["- " + v for v in report["missing"]],
                 *["- 源文件缺失: " + v["path"] for v in missing_assets], ""]
        (bundle / "README.md").write_text("\n".join(lines), encoding="utf-8")
        # Integrity inventory covers every payload file, excluding the manifest itself.
        report["files"] = [{"path": str(p.relative_to(bundle)),
                            "sha256": _file_hash(p), "size": p.stat().st_size}
                           for p in sorted(bundle.rglob("*")) if p.is_file()]
        (bundle / "export_manifest.json").write_bytes(_json_bytes(report))
        archive = stage / "bundle.zip"
        if zipped:
            with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as zf:
                for p in sorted(bundle.rglob("*")):
                    if p.is_file():
                        info = zipfile.ZipInfo(p.relative_to(bundle).as_posix(), (1980, 1, 1, 0, 0, 0))
                        info.external_attr = 0o100644 << 16
                        with p.open("rb") as source, zf.open(info, "w", force_zip64=True) as dest:
                            shutil.copyfileobj(source, dest)
        for relative, snapshot in snapshots.items():
            if _snapshot(root, relative) != snapshot:
                raise ProjectError("导出期间源文件改变: " + relative)
        for entry in missing_assets:
            try:
                fd = _open(root, entry["path"])
            except FileNotFoundError:
                continue
            else:
                os.close(fd)
                raise ProjectError("导出期间缺失文件出现: " + entry["path"])
        # Recheck path components before publication; never replace a concurrent target.
        _absolute(output)
        if zipped:
            os.link(archive, output, follow_symlinks=False)
        else:
            _rename_new(bundle, output)
        return report
    finally:
        if stage.parent != output.parent or not stage.name.startswith(".cwb-export-"):
            raise ProjectError("拒绝清理非导出暂存目录")
        shutil.rmtree(stage)


def run(args):
    result = export_project(args.root, args.output, args.asset, args.role, args.metadata, args.zip)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result
