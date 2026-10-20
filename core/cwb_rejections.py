#!/usr/bin/env python3
"""CAD-25 S1: canonical schema-2 decisions and immutable offline evidence only."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

import cwb_project as project
from cwb_workflow import WORKFLOW, STAGES, DOCUMENT_STAGES

LIBRARY = "rejection_library"
CHECKLIST = "must_not_forget.md"
EVENT = "cwb-rejection-event-v1"
INPUT = "cwb-rejection-input-v1"
QUERY = "cwb-rejection-query-v1"
REOPEN = "cwb-rejection-reopen-v1"
ARCHIVE = "cwb-rejection-archive-v1"
TAXONOMY = "cwb-rejection-reasons-v1"
KINDS = {"round_preference": "本轮偏好", "character_constraint": "角色限制",
         "structural_issue": "结构问题", "generation_artifact": "生成瑕疵",
         "undecided": "未判断（暂缓，无限制）"}
MAX_JSON = 16 * 1024 * 1024
MAX_IMAGE = 128 * 1024 * 1024
BEGIN = b"<!-- cwb-rejections:begin "
END = b"<!-- cwb-rejections:end -->"
BINDING_KEYS = {"project_id", "workflow_id", "asset_ref", "concept_ref", "document_id"}
RECORD_KEYS = BINDING_KEYS | {"schema", "event_key", "candidate", "generation", "prompt",
    "seed", "workflow", "reason_taxonomy_version", "reason_kind", "reason", "basis",
    "reminder", "reopen_when", "stage_ids", "topics", "applies_when", "decided_by", "decision_source"}


class RejectionError(project.ProjectError):
    pass


def exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise RejectionError(label + " 字段不完整或含未知字段")


def text(value, label, limit=2000, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > limit or "\x00" in value:
        raise RejectionError(label + " 文本无效或过长")
    return value


def identifier(value):
    text(value, "ID", 100)
    return project.identifier(value)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RejectionError("无效 SHA256")


def topics(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 12:
        raise RejectionError("topics 需 1–12 个精确主题词")
    for value in values:
        text(value, "topic", 80)
        if value != value.strip() or "\n" in value or "\r" in value:
            raise RejectionError("主题词不能含首尾空白或换行")
    if len({v.casefold() for v in values}) != len(values):
        raise RejectionError("主题词不能重复")


def validate_binding_input(value):
    for key in ("project_id", "document_id"):
        identifier(value[key])
    if value["workflow_id"] != WORKFLOW:
        raise RejectionError("必须明确指定现行 workflow；旧阶段编号不能自动迁移")
    exact(value["asset_ref"], {"id", "object_id", "version", "sha256"}, "asset_ref")
    exact(value["concept_ref"], {"id", "version"}, "concept_ref")
    for key in ("id", "object_id", "version"):
        identifier(value["asset_ref"][key])
    sha(value["asset_ref"]["sha256"])
    for key in ("id", "version"):
        identifier(value["concept_ref"][key])


def validate_record(value):
    exact(value, RECORD_KEYS, "手工记录")
    if value["schema"] != INPUT or value["reason_taxonomy_version"] != TAXONOMY:
        raise RejectionError("不支持的输入/原因语义版本")
    validate_binding_input(value)
    identifier(value["event_key"])
    for field in ("candidate", "reason", "reopen_when", "applies_when", "decided_by", "decision_source"):
        text(value[field], field)
    if not isinstance(value["reason_kind"], str) or value["reason_kind"] not in KINDS:
        raise RejectionError("未知 reason_kind")
    unknown = value["reason_kind"] == "undecided"
    text(value["basis"], "basis", empty=unknown)
    text(value["reminder"], "reminder", 500, empty=unknown)
    if unknown and value["reminder"] != "":
        raise RejectionError("未判断必须使用空 reminder，不得伪造限制")
    text(value["prompt"], "prompt", 50000, empty=True)
    if value["generation"] not in ("manual", "generated"):
        raise RejectionError("generation 需 manual/generated")
    if value["seed"] is not None and not project.integer(value["seed"], 0, 2**64 - 1):
        raise RejectionError("seed 需 uint64 或明确 null")
    if value["workflow"] is not None:
        project.relative_path(value["workflow"])
    if value["generation"] == "generated" and (not value["prompt"].strip() or value["workflow"] is None):
        raise RejectionError("生成候选需 prompt 和已存在的项目内 workflow JSON；未知 seed 填 null")
    if value["generation"] == "manual" and (value["seed"] is not None or value["workflow"] is not None):
        raise RejectionError("手绘 seed/workflow 必须为 null")
    stages = value["stage_ids"]
    known = {item[0] for item in STAGES}
    if not isinstance(stages, list) or not stages or any(not isinstance(s, str) or s not in known for s in stages) or len(set(stages)) != len(stages):
        raise RejectionError("stage_ids 需现行流程中不重复的明确阶段名，不接受数字")
    topics(value["topics"])
    return value


def validate_query(value):
    exact(value, BINDING_KEYS | {"schema", "stage_id", "topics", "purpose"}, "查询范围")
    if value["schema"] != QUERY or not isinstance(value["stage_id"], str) or value["stage_id"] not in {s[0] for s in STAGES}:
        raise RejectionError("查询需明确语义版本与阶段名")
    validate_binding_input(value)
    topics(value["topics"])
    if value["purpose"] not in ("generate", "review"):
        raise RejectionError("purpose 需 generate/review")


def signature(item):
    return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns


def read_bytes(root, relative, limit=MAX_JSON):
    path = project.asset_path(root, relative)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise RejectionError("输入需有界普通文件: " + relative)
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(raw) > limit or signature(before) != signature(after) or signature(after) != signature(path.lstat()):
            raise RejectionError("输入过大或读取时改变: " + relative)
    return raw


def read_json(root, relative):
    raw = read_bytes(root, relative)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("重复字段 " + key)
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("非有限数字"))), raw
    except (ValueError, UnicodeError) as exc:
        raise RejectionError("JSON 损坏: " + relative) from exc


def source_path(root, relative):
    path = project.asset_path(root, relative)
    parts = project.relative_path(relative).parts
    if parts[0] == LIBRARY or relative == CHECKLIST or any(p.startswith(".rejection-") for p in parts):
        raise RejectionError("不可把拒绝库/清单作为输入")
    if path.exists() and path.stat().st_nlink != 1:
        raise RejectionError("输入存在硬链接别名；请提供明确独立文件")
    # Reject aliases of control files as well as their spelled paths.
    for reserved in (project.STATE, project.LOCK, CHECKLIST):
        other = root / reserved
        if other.exists() and path.exists() and os.path.samefile(path, other):
            raise RejectionError("输入是状态/锁/清单的文件别名")
    return path


def ensure_library(root, create=False):
    folder = project.asset_path(root, LIBRARY)
    if project.asset_path(root, LIBRARY + "/index.json").exists():
        raise RejectionError("旧独立 index 库仅只读；先保留旧库，再人工明确重新绑定，不自动迁移")
    records = project.asset_path(root, LIBRARY + "/records")
    for path in (folder, records):
        if path.exists() and not path.is_dir():
            raise RejectionError("归档路径不是目录")
        if create and not path.exists():
            path.mkdir()
            fsync_dir(path.parent)
    return records


def basis_snapshot(state):
    return {k: copy.deepcopy(state[k]) for k in ("intent_card", "design_pillars", "style_anchors")}


def binding(state, request):
    project.require_v2(state)
    if state["project_id"] != request["project_id"] or state["workflow_id"] != request["workflow_id"]:
        raise RejectionError("项目/workflow 绑定不一致")
    asset = project.lookup(state["assets"], request["asset_ref"]["id"])
    idea = project.lookup(state["ideas"], request["concept_ref"]["id"])
    doc = project.lookup(state["documents"], request["document_id"])
    if any(asset[k] != v for k, v in request["asset_ref"].items()) or any(idea[k] != v for k, v in request["concept_ref"].items()):
        raise RejectionError("对象/创意版本或摘要不一致")
    if asset["workflow_id"] != WORKFLOW or asset["kind"] == "legacy" or asset["document_id"] != doc["id"] or doc["concept_id"] != idea["id"]:
        raise RejectionError("候选必须明确绑定同一 workflow/concept/document")
    return {"project_id": state["project_id"], "workflow_id": WORKFLOW,
            "asset": copy.deepcopy(asset), "concept": copy.deepcopy(idea), "document": copy.deepcopy(doc)}


def verify_binding(root, state, bound, selected=True):
    request = {"project_id": bound["project_id"], "workflow_id": bound["workflow_id"],
               "asset_ref": {k: bound["asset"][k] for k in ("id", "object_id", "version", "sha256")},
               "concept_ref": {k: bound["concept"][k] for k in ("id", "version")},
               "document_id": bound["document"]["id"]}
    if binding(state, request) != bound:
        raise RejectionError("登记对象/创意/文档上下文已变，需复核")
    if selected and state["selected_concept_id"] != bound["concept"]["id"]:
        raise RejectionError("当前选择已不是记录中的创意")
    for kind, item in (("asset", bound["asset"]), ("idea", bound["concept"])):
        project.verify_outcome(root, state, project.outcome_ref(state, kind + ":" + item["id"]))
    # Working document existence is checked, never interpreted as artistic approval.
    project.fingerprint(root, bound["document"]["path"])


def scope_text(request):
    return json_bytes({k: request[k] for k in sorted(BINDING_KEYS | {"stage_ids", "topics", "applies_when"})}).decode("utf-8").strip()


def snapshot(decision):
    result = copy.deepcopy(decision)
    result.pop("validity", None)  # canonical review invalidation is permitted
    result["rejection_event"].pop("archive", None)
    return result


def file_ref(path, raw):
    return {"path": path, "sha256": digest(raw), "size": len(raw)}


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, raw):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def publish_archive(root, decision, request, basis, inputs):
    records = ensure_library(root, create=True)
    folder = records / decision["id"]
    # Exclusive reservation; never rename over or replace an existing directory.
    folder.mkdir()
    prefix = LIBRARY + "/records/" + decision["id"] + "/"
    files = {}
    for name, raw in inputs.items():
        write_new(folder / name, raw)
        files[name] = file_ref(prefix + name, raw)
    manifest = {"schema": ARCHIVE, "decision": snapshot(decision), "request": request,
                "basis_snapshot": basis, "files": files}
    raw = json_bytes(manifest)
    if len(raw) > MAX_JSON:
        raise RejectionError("归档清单超出上限")
    write_new(folder / "manifest.json", raw)
    fsync_dir(folder)
    fsync_dir(records)
    decision["rejection_event"]["archive"] = file_ref(prefix + "manifest.json", raw)
    return manifest


def checked_file(root, ref, expected_path, limit=MAX_JSON):
    exact(ref, {"path", "sha256", "size"}, "归档文件引用")
    sha(ref["sha256"])
    if ref["path"] != expected_path or not project.integer(ref["size"], 0, limit):
        raise RejectionError("归档路径/大小不属于此决定")
    raw = read_bytes(root, expected_path, limit)
    if project.asset_path(root, expected_path).stat().st_nlink != 1:
        raise RejectionError("不可变归档存在硬链接别名")
    if (digest(raw), len(raw)) != (ref["sha256"], ref["size"]):
        raise RejectionError("归档摘要不匹配: " + expected_path)
    return raw


def validate_event(root, state, decision):
    """Validate the extension and immutable evidence; do not require live sources."""
    event = decision.get("rejection_event")
    common = {"schema", "operation", "event_key", "request_sha256", "binding", "reason_taxonomy_version", "reason_kind", "basis_sha256", "archive"}
    if not isinstance(event, dict) or event.get("schema") != EVENT:
        raise RejectionError("未知 rejection_event 版本")
    operation = event.get("operation")
    extra = {"reopens_decision_id", "reopens_event_sha256"} if operation == "reopen_for_exploration" else set()
    if operation not in ("record", "reopen_for_exploration"):
        raise RejectionError("未知 rejection_event 操作")
    exact(event, common | extra, "rejection_event")
    identifier(event["event_key"])
    for key in ("request_sha256", "basis_sha256"):
        sha(event[key])
    bound = event["binding"]
    exact(bound, {"project_id", "workflow_id", "asset", "concept", "document"}, "binding")
    if bound["project_id"] != state["project_id"] or bound["workflow_id"] != WORKFLOW:
        raise RejectionError("归档项目/workflow 不匹配")
    # Use current registry to check identities, but allow archive viewing after an
    # ordinary live-source change. Metadata changes are dealt with by context.
    if event["reason_taxonomy_version"] != TAXONOMY or event["reason_kind"] not in KINDS:
        raise RejectionError("未知分类版本/类别")
    asset = bound["asset"]
    wanted = {"kind": "asset", "id": asset["id"], "version": asset["version"], "sha256": asset["sha256"]}
    action = "reject" if operation == "record" and event["reason_kind"] != "undecided" else "defer"
    if (decision["target_kind"], decision["target_id"], decision["target_version"], decision["action"], decision["outcomes"]) != ("asset", asset["id"], asset["version"], action, [wanted]):
        raise RejectionError("扩展不能改变规范 action/target/outcomes")
    if decision.get("actor") != "human" or decision.get("execution") != "recorded_not_applied" or decision.get("attribution") != "user_supplied_or_manual_transcription_not_authenticated":
        raise RejectionError("扩展不能伪造来源或执行状态")
    prefix = LIBRARY + "/records/" + decision["id"] + "/"
    raw = checked_file(root, event["archive"], prefix + "manifest.json")
    try:
        manifest = json.loads(raw)
        exact(manifest, {"schema", "decision", "request", "basis_snapshot", "files"}, "archive manifest")
        if manifest["schema"] != ARCHIVE or manifest["decision"] != snapshot(decision):
            raise RejectionError("归档与规范决定不一致")
        request = manifest["request"]
        if digest(json_bytes(request)) != event["request_sha256"] or digest(json_bytes(manifest["basis_snapshot"])) != event["basis_sha256"]:
            raise RejectionError("请求/意图快照摘要不一致")
        if request["event_key"] != event["event_key"]:
            raise RejectionError("事件键不一致")
        if operation == "record":
            validate_record(request)
            if ({k: bound["asset"][k] for k in ("id", "object_id", "version", "sha256")} != request["asset_ref"] or
                {k: bound["concept"][k] for k in ("id", "version")} != request["concept_ref"] or
                request["project_id"] != bound["project_id"] or request["workflow_id"] != bound["workflow_id"] or
                request["document_id"] != bound["document"]["id"] or bound["document"]["concept_id"] != bound["concept"]["id"] or
                bound["asset"]["document_id"] != bound["document"]["id"] or bound["asset"]["workflow_id"] != WORKFLOW or
                event["reason_kind"] != request["reason_kind"] or decision["scope"] != scope_text(request) or
                decision["source"] != request["decision_source"] or decision["reason"] != request["reason"]):
                raise RejectionError("记录范围/来源/版本不一致")
            names = {"candidate" + Path(asset["path"]).suffix.lower(), "prompt.txt", "input.json"}
            if request["workflow"] is not None:
                names.add("workflow.json")
        else:
            validate_reopen(request)
            if request["project_id"] != bound["project_id"] or request["decision_id"] != event["reopens_decision_id"] or decision["source"] != request["source"] or decision["reason"] != request["note"]:
                raise RejectionError("重开来源/引用不一致")
            identifier(event["reopens_decision_id"])
            sha(event["reopens_event_sha256"])
            names = {"input.json"}
        exact(manifest["files"], names, "归档文件集")
        for name in names:
            content = checked_file(root, manifest["files"][name], prefix + name, MAX_IMAGE if name.startswith("candidate.") else MAX_JSON)
            if name == "input.json" and content != json_bytes(request):
                raise RejectionError("归档输入与规范请求不一致")
            if name == "prompt.txt" and content != request["prompt"].encode("utf-8"):
                raise RejectionError("归档 prompt 不一致")
            if name.startswith("candidate.") and (digest(content), len(content)) != (asset["sha256"], asset["size"]):
                raise RejectionError("归档候选不是目标版本")
            if name == "workflow.json" and not isinstance(json.loads(content), dict):
                raise RejectionError("workflow 需 JSON 对象")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise RejectionError("归档/扩展损坏: " + str(exc)) from exc
    return manifest


def validated_events(root, state):
    records, reopens, invalid = {}, {}, []
    seen_keys = {}
    for index, decision in enumerate(state["decisions"]):
        if "rejection_event" not in decision:
            continue
        try:
            manifest = validate_event(root, state, decision)
            event = decision["rejection_event"]
            key = event["event_key"]
            if key in seen_keys:
                raise RejectionError("重复 canonical event_key")
            seen_keys[key] = decision["id"]
            if event["operation"] == "record":
                records[decision["id"]] = (decision, manifest)
            else:
                prior = records.get(event["reopens_decision_id"])
                if prior is None:
                    raise RejectionError("重开必须引用在先且可验证的本工具拒绝决定")
                original = prior[0]
                old = original["rejection_event"]
                if (original["action"] != "reject" or event["binding"] != old["binding"] or
                    event["reason_kind"] != old["reason_kind"] or event["basis_sha256"] != old["basis_sha256"] or
                    event["reopens_event_sha256"] != digest(json_bytes(old)) or decision["scope"] != original["scope"]):
                    raise RejectionError("重开引用不完全一致；不停止旧提醒")
                reopens.setdefault(original["id"], []).append(decision)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            invalid.append({"id": decision["id"], "reason": "invalid_event_or_archive", "error": str(exc), "index": index})
    # Duplicate keys cannot leave the earlier record eligible either.
    counts = {}
    for decision in state["decisions"]:
        event = decision.get("rejection_event")
        if isinstance(event, dict) and isinstance(event.get("event_key"), str):
            counts[event["event_key"]] = counts.get(event["event_key"], 0) + 1
    for ident, (decision, _) in list(records.items()):
        if counts[decision["rejection_event"]["event_key"]] > 1:
            records.pop(ident)
            reopens.pop(ident, None)
            invalid.append({"id": ident, "reason": "duplicate_event_key"})
    for ident, decisions in list(reopens.items()):
        reopens[ident] = [d for d in decisions if counts[d["rejection_event"]["event_key"]] == 1]
    return records, reopens, invalid


def existing(root, state, key, request):
    matches = [d for d in state["decisions"] if isinstance(d.get("rejection_event"), dict) and d["rejection_event"].get("event_key") == key]
    if not matches:
        return None
    if len(matches) != 1 or matches[0]["rejection_event"].get("request_sha256") != digest(json_bytes(request)):
        raise RejectionError("事件键冲突；同键不同内容不得覆盖或重复消费")
    manifest = validate_event(root, state, matches[0])
    records, reopens, invalid = validated_events(root, state)
    decision = matches[0]
    event = decision["rejection_event"]
    if event["operation"] == "record":
        if decision["id"] not in records:
            raise RejectionError("重复事件的原归档/引用未通过验证")
        verify_binding(root, state, event["binding"], selected=False)
        workflow = manifest["request"]["workflow"]
        if workflow is not None:
            source_path(root, workflow)
            archived = manifest["files"]["workflow.json"]
            if project.fingerprint(root, workflow) != (archived["sha256"], archived["size"]):
                raise RejectionError("同一事件键的 workflow 源字节已变；不得当作相同输入重复消费")
    else:
        if decision not in reopens.get(event["reopens_decision_id"], []):
            raise RejectionError("重复重开事件的引用未通过验证")
        # A duplicate mutation request still promises a currently valid target.
        # Fail before project.update commits its in-memory refresh; immutable
        # historical receipts remain available separately through list/history.
        verify_binding(root, state, event["binding"], selected=False)
    return decision


def event_header(request, bound, basis, kind, operation):
    return {"schema": EVENT, "operation": operation, "event_key": request["event_key"],
            "request_sha256": digest(json_bytes(request)), "binding": bound,
            "reason_taxonomy_version": TAXONOMY, "reason_kind": kind,
            "basis_sha256": digest(json_bytes(basis))}


def write_operation(root, expected, operation):
    if expected is not None and not project.integer(expected):
        raise RejectionError("expected 需非负整数项目修订")
    result = {}
    def apply(path, state):
        ensure_library(path)
        operation(path, state, result)
    try:
        state = project.update(root, apply, expected)
    except (OSError, project.ProjectError) as exc:
        if "id" in result:
            # State replacement may have happened before a directory fsync error.
            committed = "unknown"
            try:
                state, _ = project.read_state(Path(root).resolve(strict=True))
                committed = any(d["id"] == result["id"] for d in state["decisions"])
            except (OSError, project.ProjectError):
                pass
            raise RejectionError("操作未完整确认；canonical_saved={}；保留归档/中断目录 {}。先 list 回读再重试: {}".format(committed, result["id"], exc)) from exc
        raise
    result.update(revision=state["revision"], canonical_saved=True)
    return synced_result(root, result)


def add(root, decision_path, expected=None):
    def operation(path, state, result):
        source_path(path, decision_path)
        request, original_raw = read_json(path, decision_path)
        validate_record(request)
        old = existing(path, state, request["event_key"], request)
        if old is not None:
            result.update(id=old["id"], duplicate=True)
            return
        bound = binding(state, request)
        verify_binding(path, state, bound)
        allowed = {STAGES[n - 1][0] for n in DOCUMENT_STAGES[bound["document"]["role"]]}
        if not set(request["stage_ids"]).issubset(allowed):
            raise RejectionError("适用阶段超出明确文档角色；需单独记录其他范围")
        asset = bound["asset"]
        suffix = Path(asset["path"]).suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
            raise RejectionError("候选扩展名需 png/jpg/jpeg/webp；本工具不解码或证明画面质量")
        source_path(path, asset["path"])
        image = read_bytes(path, asset["path"], MAX_IMAGE)
        if (digest(image), len(image)) != (asset["sha256"], asset["size"]):
            raise RejectionError("候选已变，不能归档为旧版本")
        inputs = {"candidate" + suffix: image, "prompt.txt": request["prompt"].encode("utf-8"), "input.json": json_bytes(request)}
        workflow_raw = None
        if request["workflow"] is not None:
            source_path(path, request["workflow"])
            workflow, workflow_raw = read_json(path, request["workflow"])
            if not isinstance(workflow, dict):
                raise RejectionError("workflow 需 JSON 对象")
            inputs["workflow.json"] = workflow_raw
        action = "defer" if request["reason_kind"] == "undecided" else "reject"
        decision = project.add_decision(path, state, "asset", asset["id"], action,
                    scope_text(request), request["decision_source"], request["reason"])
        basis = basis_snapshot(state)
        decision["rejection_event"] = event_header(request, bound, basis, request["reason_kind"], "record")
        result.update(id=decision["id"], duplicate=False)
        publish_archive(path, decision, request, basis, inputs)
        # Recheck all external inputs after publication and before canonical commit.
        if read_bytes(path, decision_path) != original_raw or (workflow_raw is not None and read_bytes(path, request["workflow"]) != workflow_raw):
            raise RejectionError("归档期间手工记录/workflow 已变；未写入规范决定")
        verify_binding(path, state, bound)
        validate_event(path, state, decision)
    return write_operation(root, expected, operation)


def validate_reopen(request):
    exact(request, {"schema", "event_key", "project_id", "decision_id", "note", "by", "source"}, "重开请求")
    if request["schema"] != REOPEN:
        raise RejectionError("未知重开请求版本")
    for key in ("event_key", "project_id", "decision_id"):
        identifier(request[key])
    for key in ("note", "by", "source"):
        text(request[key], key)


def reopen(root, request, expected=None):
    # Dict callers provide an explicit value snapshot; CLI paths are read and
    # rechecked under the same project transaction as the canonical append.
    request_path = request if isinstance(request, str) else None
    frozen_request = None if request_path else copy.deepcopy(request)
    if request_path is None:
        validate_reopen(frozen_request)
    def operation(path, state, result):
        original_raw = None
        if request_path is not None:
            source_path(path, request_path)
            request, original_raw = read_json(path, request_path)
            validate_reopen(request)
        else:
            request = frozen_request
        if request["project_id"] != state["project_id"]:
            raise RejectionError("重开项目身份不匹配")
        old = existing(path, state, request["event_key"], request)
        if old is not None:
            result.update(id=old["id"], duplicate=True)
            return
        records, _, invalid = validated_events(path, state)
        if request["decision_id"] not in records:
            raise RejectionError("原拒绝缺失或扩展/归档未通过验证")
        original, manifest = records[request["decision_id"]]
        if original["action"] != "reject":
            raise RejectionError("未判断并非拒绝，不伪造撤回")
        bound = original["rejection_event"]["binding"]
        verify_binding(path, state, bound, selected=False)
        decision = project.add_decision(path, state, "asset", original["target_id"], "defer",
                    original["scope"], request["source"], request["note"])
        basis = manifest["basis_snapshot"]
        event = event_header(request, bound, basis, original["rejection_event"]["reason_kind"], "reopen_for_exploration")
        event.update(reopens_decision_id=original["id"], reopens_event_sha256=digest(json_bytes(original["rejection_event"])))
        decision["rejection_event"] = event
        result.update(id=decision["id"], duplicate=False, reopens_decision_id=original["id"])
        publish_archive(path, decision, request, basis, {"input.json": json_bytes(request)})
        if request_path is not None and read_bytes(path, request_path) != original_raw:
            raise RejectionError("归档期间重开请求已变；未写入规范决定")
        verify_binding(path, state, bound, selected=False)
        _, reopens, invalid = validated_events(path, state)
        if decision not in reopens.get(original["id"], []):
            raise RejectionError("重开事件未通过引用验证: " + str(invalid))
    return write_operation(root, expected, operation)


def read_view(root):
    root = Path(root).resolve(strict=True)
    state, raw = project.read_state(root)
    project.require_v2(state)
    ensure_library(root)
    project.refresh_reviews(root, state)
    return root, state, raw


def eligibility(root, state, decision, manifest, reopens):
    event = decision["rejection_event"]
    if decision["validity"] != "valid":
        return "canonical_needs_review"
    try:
        verify_binding(root, state, event["binding"])
    except (OSError, project.ProjectError):
        return "source_or_binding_stale"
    if event["basis_sha256"] != digest(json_bytes(basis_snapshot(state))):
        return "project_basis_changed"
    # Only a strictly validated, still-effective canonical defer stops this tool's reminder.
    if any(d["validity"] == "valid" for d in reopens.get(decision["id"], [])):
        return "reopened_for_exploration"
    if event["reason_kind"] == "undecided":
        return "undecided_not_constraint"
    return None


def context(root, request, limit=5, include_preferences=False):
    validate_query(request)
    if not project.integer(limit, 1, 10) or type(include_preferences) is not bool:
        raise RejectionError("limit 需 1–10；include_preferences 需布尔值")
    root, state, raw = read_view(root)
    # Check query identity even when there are no records. Do not require a live
    # source here: stale-source queries must still report why reminders were skipped.
    bound = binding(state, request)
    records, reopens, invalid = validated_events(root, state)
    wanted = {t.casefold() for t in request["topics"]}
    grouped, skipped = {}, list(invalid)
    for decision, manifest in records.values():
        entry = manifest["request"]
        if any(entry[k] != request[k] for k in BINDING_KEYS) or request["stage_id"] not in entry["stage_ids"] or not wanted.intersection(t.casefold() for t in entry["topics"]):
            continue
        reason = eligibility(root, state, decision, manifest, reopens)
        if reason is None and entry["reason_kind"] == "round_preference" and not include_preferences:
            reason = "preference_not_requested"
        if reason:
            skipped.append({"id": decision["id"], "reason": reason})
            continue
        key = digest(json_bytes({k: entry[k] for k in ("reason_kind", "basis", "reminder", "stage_ids", "topics", "applies_when", "reopen_when")}))
        if key not in grouped:
            grouped[key] = {"reason_kind": entry["reason_kind"], "taxonomy_version": TAXONOMY,
                "basis": entry["basis"], "reminder": entry["reminder"], "stage_ids": entry["stage_ids"],
                "topics": entry["topics"], "applies_when": entry["applies_when"], "reopen_when": entry["reopen_when"], "decision_ids": [], "record_count": 0}
        item = grouped.pop(key)
        item["record_count"] += 1
        item["decision_ids"] = (item["decision_ids"] + [decision["id"]])[-10:]
        grouped[key] = item
    matches = sorted(reversed(list(grouped.values())), key=lambda e: e["reason_kind"] == "round_preference")
    constraints = matches[:limit]
    lines = ["仅供本次明确范围的离线人工参考；未进入生成请求，不代表艺术批准。"]
    lines += ["- [{}] {}；依据：{}；仅当：{}；重开条件：{}".format(KINDS[e["reason_kind"]], one_line(e["reminder"]), one_line(e["basis"]), one_line(e["applies_when"]), one_line(e["reopen_when"])) for e in constraints]
    if not constraints:
        lines.append("没有可附带提醒；不代表作品通过或没有风险。")
    lines.append("另请人工阅读 must_not_forget.md 的手工区域。")
    if project.state_bytes(root) != raw:
        raise RejectionError("查询期间项目已变，请重读；未返回混合修订提醒")
    return {"revision": state["revision"], "request": request, "constraints": constraints,
            "omitted_by_limit": max(0, len(matches) - limit), "skipped": skipped[:100],
            "skipped_count": len(skipped), "text": "\n".join(lines), "read_only": True,
            "generation_request_integrated": False}


def one_line(value):
    return " ".join(value.split()).replace("<", "&lt;").replace(">", "&gt;")


def checklist_parts(raw):
    if raw is None:
        return "# 必记清单\n\n## 手工补充\n\n工具保留自动区外的所有字节。\n\n".encode(), b"", None
    if BEGIN not in raw and END not in raw:
        return raw + (b"\n\n" if raw else b""), b"", None
    if raw.count(BEGIN) != 1 or raw.count(END) != 1:
        raise RejectionError("清单自动区标记不完整/重复，未覆盖")
    before, rest = raw.split(BEGIN, 1)
    header, sep, rest = rest.partition(b" -->\n")
    if not sep or not re.fullmatch(b"[0-9a-f]{64}", header) or END not in rest:
        raise RejectionError("清单自动区标记被编辑，未覆盖")
    body, after = rest.split(END, 1)
    if digest(body).encode() != header:
        raise RejectionError("自动区有人工修改；先移到自动区外，未覆盖")
    return before, after, body


def checklist_body(root, state):
    records, reopens, invalid = validated_events(root, state)
    lines = ["\n## 自动历史摘要（不是生成约束或批准）", "",
             "规范项目修订：{}。只展示最新 100 条；完整历史用 list 分页，下一轮用 context 明确范围。".format(state["revision"]), ""]
    for decision, manifest in list(records.values())[-100:]:
        entry = manifest["request"]
        status = eligibility(root, state, decision, manifest, reopens) or "recorded_scoped_only"
        lines.append("- {} [{} / {}] {}".format(one_line(entry["candidate"]), KINDS[entry["reason_kind"]], status, one_line(entry["reason"])))
        lines.append("  范围：{} / {} / {} / {}；决定 {}".format(one_line(entry["concept_ref"]["id"]), one_line(entry["asset_ref"]["version"]), ",".join(entry["stage_ids"]), "/".join(map(one_line, entry["topics"])), decision["id"]))
    lines += ["", "有效记录 {}；扩展/归档异常 {}。偏好只在显式选择后参考；未判断不形成限制；重开不等于批准。".format(len(records), len(invalid)), ""]
    return "\n".join(lines).encode()


def atomic_file(root, relative, payload, previous):
    target = project.asset_path(root, relative)
    # Replacing an alias would silently detach a user's unrelated source file.
    if target.exists() and target.stat().st_nlink != 1:
        raise RejectionError("清单存在硬链接别名，拒绝替换")
    fd, name = tempfile.mkstemp(prefix=".rejection-", suffix=".tmp", dir=target.parent)
    committed = False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            current = read_bytes(root, relative)
        except FileNotFoundError:
            current = None
        if current != previous:
            raise RejectionError("清单已被手工修改；未覆盖")
        if previous is None:
            os.link(name, target)
        else:
            os.replace(name, target)
        committed = True
        fsync_dir(target.parent)
    except OSError as exc:
        if committed:
            raise RejectionError("清单已替换但持久化未确认；先回读") from exc
        raise
    finally:
        if os.path.exists(name):
            os.unlink(name)


def canonical_source_paths(state):
    """Only schema-defined file references, including unselected ideas.

    Inline intent/pillar/style text is not a file-reference contract and must
    never be guessed to be a path. Document paths share the same alias checks.
    """
    paths = {asset["path"] for asset in state["assets"]}
    paths.update(asset["preview"]["path"] for asset in state["assets"] if asset["preview"] is not None)
    paths.update(idea["text_ref"].split("#", 1)[0] for idea in state["ideas"] if idea["text_ref"] is not None)
    paths.update(doc["path"] for doc in state["documents"])
    return sorted(paths)


def sync(root, expected=None):
    root = Path(root).resolve(strict=True)
    with project.locked(root):
        root, state, raw_state = read_view(root)
        if expected is not None and (not project.integer(expected) or state["revision"] != expected):
            raise RejectionError("项目修订冲突，清单未写入")
        ensure_library(root, create=True)
        checklist_path = project.asset_path(root, CHECKLIST)
        for relative in canonical_source_paths(state):
            if relative == CHECKLIST:
                raise RejectionError("清单路径已登记为规范来源（资产/预览/创意全文/文档）；不覆盖")
            source = project.asset_path(root, relative)
            if checklist_path.exists() and source.exists() and os.path.samefile(checklist_path, source):
                raise RejectionError("清单是已登记规范来源的别名；不覆盖")
        try:
            raw = read_bytes(root, CHECKLIST)
        except FileNotFoundError:
            raw = None
        before, after, old = checklist_parts(raw)
        body = checklist_body(root, state)
        payload = before + BEGIN + digest(body).encode() + b" -->\n" + body + END + after
        if len(payload) > MAX_JSON:
            raise RejectionError("清单超出上限，未覆盖")
        if project.state_bytes(root) != raw_state:
            raise RejectionError("项目状态在清单同步时改变，未覆盖")
        if old != body:
            atomic_file(root, CHECKLIST, payload, raw)
        return {"revision": state["revision"], "checklist_synced": True}


def synced_result(root, result):
    try:
        synced = sync(root)
        result.update(checklist_synced=True, checklist_revision=synced["revision"])
    except (OSError, project.ProjectError) as exc:
        result.update(checklist_synced=False, warning="规范决定已保存；清单未同步: " + str(exc))
    return result


def inspect(root, query=None, limit=20, offset=0):
    if not project.integer(limit, 1, 100) or not project.integer(offset):
        raise RejectionError("list limit 需 1–100；offset 需非负整数")
    if query is not None:
        text(query, "query", 200)
    root, state, raw = read_view(root)
    records, reopens, invalid = validated_events(root, state)
    rows = []
    for decision, manifest in records.values():
        if query and query.casefold() not in json.dumps(manifest["request"], ensure_ascii=False).casefold() and query != decision["id"]:
            continue
        rows.append({"decision": decision, "request": manifest["request"], "files": manifest["files"],
                     "effective_status": eligibility(root, state, decision, manifest, reopens) or "recorded_scoped_only",
                     "reopen_history": reopens.get(decision["id"], [])[-10:],
                     "reopen_history_count": len(reopens.get(decision["id"], []))})
    folder = ensure_library(root)
    known = {d["id"] for d in state["decisions"] if "rejection_event" in d}
    orphans = sorted(p.name for p in folder.iterdir() if p.name not in known) if folder.exists() else []
    try:
        _, _, body = checklist_parts(read_bytes(root, CHECKLIST))
        checklist = "current" if body == checklist_body(root, state) else "stale"
    except FileNotFoundError:
        checklist = "missing"
    except (OSError, project.ProjectError) as exc:
        checklist = "needs_attention: " + str(exc)
    if project.state_bytes(root) != raw:
        raise RejectionError("查询期间项目已变，请重读")
    return {"revision": state["revision"], "records": rows[offset:offset + limit], "total": len(rows),
            "next_offset": offset + limit if offset + limit < len(rows) else None,
            "invalid_events": invalid[:100], "invalid_event_count": len(invalid),
            "orphan_entries": orphans[:100], "orphan_count": len(orphans), "checklist": checklist,
            "read_only": True, "notice": "归档可读不代表旧来源仍可重新决定；历史来源由人工提供，未认证。"}


def history(root, decision_id, limit=20, offset=0):
    """Page the original canonical record and every strictly linked reopen event."""
    identifier(decision_id)
    if not project.integer(limit, 1, 100) or not project.integer(offset):
        raise RejectionError("history limit 需 1–100；offset 需非负整数")
    root, state, raw = read_view(root)
    records, reopens, invalid = validated_events(root, state)
    if decision_id not in records:
        raise RejectionError("原记录未通过验证；请 list 查看异常，不伪造历史")
    events = [records[decision_id][0], *reopens.get(decision_id, [])]
    if project.state_bytes(root) != raw:
        raise RejectionError("历史查询期间项目已变，请重读")
    return {"decision_id": decision_id, "events": events[offset:offset + limit],
            "total": len(events), "next_offset": offset + limit if offset + limit < len(events) else None,
            "invalid_event_count": len(invalid), "read_only": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description="CAD25 S1：离线规范决定、不可变归档与范围提醒；不调用 Krita/ComfyUI")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "sync", "add", "reopen", "list", "history", "context"):
        cmd = commands.add_parser(name)
        cmd.add_argument("root")
        if name in ("add", "reopen", "sync", "init"):
            cmd.add_argument("--expect-revision", type=int)
        if name in ("add", "reopen", "context"):
            cmd.add_argument("request", help="项目内显式 JSON 相对路径；格式见文档")
        if name == "history":
            cmd.add_argument("id", help="原拒绝或未判断记录的规范决定 ID")
        if name in ("list", "history"):
            if name == "list":
                cmd.add_argument("--query")
            cmd.add_argument("--limit", type=int, default=20)
            cmd.add_argument("--offset", type=int, default=0)
        if name == "context":
            cmd.add_argument("--limit", type=int, default=5)
            cmd.add_argument("--include-preferences", action="store_true")
            cmd.add_argument("--text", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command in ("init", "sync"):
            result = sync(args.root, args.expect_revision)
        elif args.command == "add":
            result = add(args.root, args.request, args.expect_revision)
        elif args.command == "reopen":
            result = reopen(args.root, args.request, args.expect_revision)
        elif args.command == "list":
            result = inspect(args.root, args.query, args.limit, args.offset)
        elif args.command == "history":
            result = history(args.root, args.id, args.limit, args.offset)
        else:
            root = Path(args.root).resolve(strict=True)
            source_path(root, args.request)
            request, _ = read_json(root, args.request)
            result = context(root, request, args.limit, args.include_preferences)
        print(result["text"] if getattr(args, "text", False) else json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("checklist_synced", True) else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("错误: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
