"""CAD-20: offline workflow checks and reversible navigation, never art approval."""
import copy
import json
from pathlib import Path

import cwb_project as project
from cwb_workflow import WORKFLOW, LEGACY_WORKFLOW, STAGES, REQUIRED_ROLES


RULESET = "krita-character-illustration-v1/gatekeeper-1"
# These are handoff checks, not requirements to close the corresponding Issues.
INPUT_STAGES = {1: (), 2: (1,), 3: (2,), 4: (3,), 5: (4,),
                6: (5,), 7: (5, 6), 8: (6, 7)}
HUMAN_CHECKS = {
    1: "作者确认当前文本起点可继续探索；不要求完整意图卡或固定数量",
    2: "作者确认联想板与本轮事实问题；来源可追溯，不把推断当事实",
    3: "作者选择本轮创意主线，保留其他起点与未选去向",
    4: "作者复看大形、结构、粗线稿与基础色，确认可供收敛的候选",
    5: "作者确认主稿、实际修订或无须修改的理由及复看结果",
    6: "作者并排核对正侧背、角色自身左右、结构比例与基础色一致性",
    7: "作者独立确认插画事件、镜头、动作、构图与基本明暗光色",
    8: "作者核对表达、规格、源文件重开与导出，决定是否停止制作",
}
INTENTS = ("handoff", "detail-render", "structural-change")


def problem(code, message, **details):
    return {"code": code, "message": message, **details}


def _not_configured(data, number):
    workflow = data.get("workflow_id", LEGACY_WORKFLOW if data.get("schema_version") == 1 else None)
    return {"ruleset": RULESET, "workflow_id": workflow, "stage": number,
            "software_status": "not_configured", "read_only": True,
            "artistic_acceptance": "unverified", "runtime_validation": "unrun",
            "warnings": [problem("rules_not_configured", "该 schema/workflow/阶段未配置规则；不按同编号套用新流程，也不判通过")],
            "next_action": "核对流程版本；旧 schema1 先查看 migration-plan，未经明确授权不升级"}


def _rejection_after(data, ref, index):
    decisions = [d for d in data["decisions"][index + 1:]
                 if d["target_kind"] == ref["kind"] and d["target_id"] == ref["id"]
                 and d["action"] in ("reject", "defer")]
    # A later object-level approval may have another scope. Only a renewed stage
    # handoff explicitly reconciles a rejection with the aggregate approval.
    if decisions:
        return decisions[-1]
    return None


def _latest_stage_decision(data, number):
    candidates = [(i, d) for i, d in enumerate(data["decisions"])
                  if d["target_kind"] == "stage" and d["target_id"] == STAGES[number - 1][0]]
    return candidates[-1] if candidates else (None, None)


def _active_concept(root, data):
    """Check the actual production input, not completion of all earlier stages."""
    selected = data["selected_concept_id"]
    selections = [(i, d) for i, d in enumerate(data["decisions"])
                  if d["target_kind"] == "idea" and d["action"] == "select"]
    result = {"concept_id": selected, "status": "needs_review"}
    if not selections or selections[-1][1]["target_id"] != selected:
        return {**result, "reason": "本轮主线缺少一致有效的人类选择记录"}
    index, selection = selections[-1]
    result.update(selection_id=selection["id"])
    if selection["validity"] != "valid":
        return {**result, "reason": "当前主线的选择记录已失效，需重新核对来源/版本并明确选择"}
    ref = project.outcome_ref(data, "idea:" + selected)
    try:
        project.verify_outcome(root, data, ref)
    except (OSError, project.ProjectError) as exc:
        return {**result, "reason": "当前主线来源不可用: " + str(exc)}
    # A renewed explicit selection or stage-03 handoff can reconcile an earlier
    # scope conflict. A later character/illustration approval cannot silently do so.
    stage_index, stage_decision = _latest_stage_decision(data, 3)
    if (stage_decision is not None and stage_decision["validity"] == "valid"
            and stage_index > index and ref in stage_decision["outcomes"]):
        index = stage_index
        result["reconciled_by_stage_approval"] = stage_decision["id"]
    conflict = _rejection_after(data, ref, index)
    if conflict:
        return {**result, "status": "conflict", "conflicting_decision_id": conflict["id"],
                "reason": "当前主线选择后有人工拒绝/暂缓；须核对范围并重新选择或明确阶段03交接确认"}
    return {**result, "status": "recorded_unverified"}


def _illustration_handoff(data, production_outcomes):
    """Use existing document identities; a shared character is not a shared painting."""
    _, plan = _latest_stage_decision(data, 7)
    if plan is None or plan["validity"] != "valid":
        return {"status": "needs_review", "reason": "缺少当前有效的独立阶段07插画方案批准"}

    def identities(outcomes, roles):
        result = set()
        for ref in outcomes:
            if ref["kind"] != "asset":
                continue
            asset = project.lookup(data["assets"], ref["id"])
            if asset["role"] in roles and asset["document_id"] is not None:
                doc = project.lookup(data["documents"], asset["document_id"])
                result.add((doc["id"], doc["illustration_id"]))
        return result

    planned = identities(plan["outcomes"], {"illustration_plan"})
    production = identities(production_outcomes, REQUIRED_ROLES[8])
    details = {"plan_approval_id": plan["id"],
               "plan_documents": [dict(document_id=d, illustration_id=i) for d, i in sorted(planned)],
               "production_documents": [dict(document_id=d, illustration_id=i) for d, i in sorted(production)]}
    if len(planned) != 1:
        return {**details, "status": "needs_review", "reason": "阶段07批准未绑定唯一插画方案文档，不能推断阶段08基底"}
    if production != planned:
        return {**details, "status": "needs_review",
                "reason": "阶段08成果与阶段07确认的document_id/illustration_id不一致；另存/衍生文档须先登记并明确确认对应方案交接"}
    return {**details, "status": "matched"}


def _approval(root, data, number):
    index, decision = _latest_stage_decision(data, number)
    if decision is None:
        return {"status": "pending", "approval_id": None,
                "reason": "缺少本阶段版本绑定的人类批准记录"}
    # Never fall back past a newer invalid decision.
    result = {"status": "recorded_unverified", "approval_id": decision["id"],
              "source": decision["source"], "scope": decision["scope"],
              "attribution": "user_supplied_or_manual_transcription_not_authenticated"}
    if decision["validity"] != "valid":
        return {**result, "status": "needs_review", "reason": "最新批准引用已失效，需要真实复核后重新记录"}
    try:
        project.check_outcomes(data, number, decision["outcomes"])
        if number == 3 and not any(r["kind"] == "idea" and r["id"] == data["selected_concept_id"]
                                   for r in decision["outcomes"]):
            raise project.ProjectError("本轮选中创意与批准不一致")
        if number >= 3:
            concept = _active_concept(root, data)
            result["active_concept"] = concept
            if concept["status"] != "recorded_unverified":
                return {**result, "status": concept["status"], "reason": concept["reason"]}
        for ref in decision["outcomes"]:
            project.verify_outcome(root, data, ref)
            if number >= 4 and ref["kind"] == "asset":
                asset = project.lookup(data["assets"], ref["id"])
                if asset["document_id"] is not None:
                    doc = project.lookup(data["documents"], asset["document_id"])
                    if data["selected_concept_id"] is None or doc["concept_id"] != data["selected_concept_id"]:
                        raise project.ProjectError("成果文档所属创意与本轮选择不一致，需核对版本与绑定")
            # The selected idea's renewed select/03 confirmation was reconciled
            # above; unrelated outcomes still need a fresh stage handoff.
            is_active_selection = (number == 3 and ref["kind"] == "idea"
                                   and ref["id"] == data["selected_concept_id"])
            conflict = None if is_active_selection else _rejection_after(data, ref, index)
            if conflict:
                return {**result, "status": "conflict", "reason": "批准之后该成果有人工拒绝/暂缓，须核对具体范围再重新确认",
                        "conflicting_decision_id": conflict["id"], "outcome": ref}
        if number == 8:
            handoff = _illustration_handoff(data, decision["outcomes"])
            result["illustration_handoff"] = handoff
            if handoff["status"] != "matched":
                return {**result, "status": "needs_review", "reason": handoff["reason"]}
    except (OSError, project.ProjectError) as exc:
        return {**result, "status": "needs_review", "reason": str(exc)}
    return result


def _inventory(root, data, number):
    """Inspect declared outcomes, without upgrading working files to checkpoints."""
    refs, unavailable = [], []
    entries = [("idea", i) for i in data["ideas"]] if number in (1, 3) else [
        ("asset", a) for a in data["assets"] if a["stage"] == number and a["workflow_id"] == WORKFLOW]
    for kind, entry in entries:
        if number == 3 and entry["id"] != data["selected_concept_id"]:
            continue
        ref = project.outcome_ref(data, kind + ":" + entry["id"])
        try:
            if kind == "asset":
                if entry["kind"] != "checkpoint":
                    raise project.ProjectError("可变工作文件不是确认快照")
                if entry["role"] not in ("delivery_manifest", "editable_source") and (
                        entry["document_id"] is None or (entry["region"] is None and entry["group_marker"] is None)):
                    raise project.ProjectError("视觉成果缺少文档与持久组标记或画布区域")
            project.verify_outcome(root, data, ref)
            refs.append(ref)
        except (OSError, project.ProjectError) as exc:
            unavailable.append({"kind": kind, "id": entry["id"], "path": entry.get("path", entry.get("text_ref")),
                                "role": entry.get("role"), "reason": str(exc)})
    roles = {project.lookup(data["assets"], r["id"])["role"] for r in refs if r["kind"] == "asset"}
    return {"usable_refs": refs, "unavailable": unavailable,
            "required_roles": sorted(REQUIRED_ROLES.get(number, set())),
            "missing_roles": sorted(REQUIRED_ROLES.get(number, set()) - roles),
            "requires_idea": number in (1, 3),
            "missing_idea": number in (1, 3) and not refs}


def _inputs(root, data, number):
    return [{"stage": n, "stage_id": STAGES[n - 1][0], **_approval(root, data, n)}
            for n in INPUT_STAGES[number]]


def evaluate(root, data, number=None, intent="handoff"):
    """Return a fresh effective view; neither caller data nor project files change."""
    number = data.get("current_stage") if number is None else number
    if data.get("schema_version") != 2 or data.get("workflow_id") != WORKFLOW:
        return _not_configured(data, number)
    data = copy.deepcopy(project.validate(data))
    if not project.integer(number, 1, 8):
        return _not_configured(data, number)
    if intent not in INTENTS:
        raise project.ProjectError("未知操作意图")
    project.refresh_reviews(root, data)
    inventory = _inventory(root, data, number)
    approval = _approval(root, data, number)
    inputs = _inputs(root, data, number)
    warnings = []
    for role in inventory["missing_roles"]:
        warnings.append(problem("missing_role", "缺少可用的本阶段确认快照: " + role, role=role))
    if inventory["missing_idea"]:
        warnings.append(problem("missing_idea", "缺少可用文本起点" if number == 1 else "缺少本轮已选中的可用创意"))
    # Unused damaged/working alternatives are listed, but cannot invalidate a sound approved handoff.
    if approval["status"] != "recorded_unverified":
        warnings.append(problem("human_confirmation_required", approval["reason"], status=approval["status"]))
    for item in inputs:
        if item["status"] != "recorded_unverified":
            warnings.append(problem("input_confirmation_required", "输入阶段 {}（{}）需核对: {}".format(
                item["stage"], STAGES[item["stage"] - 1][1], item["reason"]), stage=item["stage"]))
    if intent == "detail-render":
        base = 4 if number <= 4 else (7 if number >= 7 else 5)
        if _approval(root, data, base)["status"] != "recorded_unverified":
            warnings.append(problem("detail_before_confirmation", "复杂细节前尚缺当前大形/主稿或插画方案的版本绑定确认；先回看结构，不能从文件名推断大形已定", stage=base))
    if intent == "structural-change" and number >= 5:
        warnings.append(problem("declared_structural_change", "已声明重大结构修改：先保留候选，并复核角色主稿、三视图与插画的受影响版本；此检查不执行改图、不自动批准"))
    review_stages = [s["stage"] for s in data["stages"] if s["stage"] >= 5 and s["needs_review"]]
    unresolved = [n for n in review_stages if n <= number and _approval(root, data, n)["status"] != "recorded_unverified"]
    if number >= 5 and unresolved:
        warnings.append(problem("downstream_needs_review", "已登记版本变更影响阶段复核；请逐项查看批准，而非沿用旧完成标记", stages=review_stages))
    return {"ruleset": RULESET, "workflow_id": WORKFLOW, "project_id": data["project_id"],
            "revision": data["revision"], "stage": number, "stage_id": STAGES[number - 1][0],
            "stage_name": STAGES[number - 1][1], "intent": intent,
            "software_status": "warning" if warnings else "ready", "read_only": True,
            "inputs": inputs, "outcomes": inventory, "human_confirmation": approval,
            "downstream_review_stages": review_stages,
            "human_check": HUMAN_CHECKS[number], "recorded_stage_status": data["stages"][number - 1]["status"],
            "artistic_acceptance": "unverified", "runtime_validation": "unrun", "warnings": warnings,
            "next_action": "补齐所列成果/真实确认，重新 check；试验导航可 advance --override 明确理由，回退用 --to。" if warnings
                           else "软件交接检查已满足；可 advance。不会自动标 completed，也不证明艺术质量或 Krita 实机验收。"}


def check(root, number=None, intent="handoff"):
    root = Path(root).resolve(strict=True)
    # Unknown versions are explicitly unconfigured, never reinterpreted as today's stages.
    try:
        data = json.loads(project.state_bytes(root))
    except (ValueError, UnicodeError) as exc:
        raise project.ProjectError("project_state.json 损坏；保留原文件，请人工恢复备份") from exc
    if not isinstance(data, dict):
        raise project.ProjectError("项目状态必须是对象")
    if data.get("schema_version") == 1:
        project.validate(data)
    return evaluate(root, data, number, intent)


class AdvanceStopped(project.ProjectError):
    def __init__(self, result):
        self.result = result
        super().__init__(result["message"])


class AdvanceUnchanged(AdvanceStopped):
    """Exit the write transaction without persisting an incidental refresh."""


def advance(root, target=None, override=None, note="", expected=None, intent="handoff"):
    if override is not None and (not isinstance(override, str) or not override.strip()):
        raise project.ProjectError("override 必须提供非空具体理由")
    if not isinstance(note, str) or intent not in INTENTS:
        raise project.ProjectError("无效备注或操作意图")
    result = {}

    def operation(root, data):
        current = data["current_stage"]
        destination = current + 1 if target is None else target
        if not project.integer(destination, 1, 8):
            raise project.ProjectError("没有该目标阶段；08已是最后阶段，检查交付不会自动生成completed")
        report = evaluate(root, data, current, intent)
        warnings = list(report["warnings"])
        direction = "backward" if destination < current else "forward" if destination > current else "unchanged"
        if direction == "forward":
            if destination > current + 1:
                warnings.append(problem("skipped_stages", "跳过阶段只改变关注位置，必需成果与艺术验收仍未完成", stages=list(range(current + 1, destination))))
            for item in _inputs(root, data, destination):
                if item["status"] != "recorded_unverified":
                    warnings.append(problem("target_input_required", "目标阶段输入 {} 需核对: {}".format(item["stage"], item["reason"]), stage=item["stage"]))
            if destination == 8:
                _, production = _latest_stage_decision(data, 8)
                if production is not None:
                    handoff = _illustration_handoff(data, production["outcomes"])
                    if handoff["status"] != "matched":
                        warnings.append(problem("target_illustration_mismatch", handoff["reason"], handoff=handoff))
        result.update(action="advance", direction=direction, source_stage=current, target_stage=destination,
                      check=report, warnings=warnings, override_reason=override,
                      completion_inferred=False, artistic_acceptance="unverified", runtime_validation="unrun")
        if direction == "forward" and warnings and override is None:
            result.update(status="stopped", changed=False,
                          message="检查有警告，未推进、未写状态。补齐后重试，或用 --override 提供明确理由仅导航；不能替代必需成果/作者批准。")
            raise AdvanceStopped(result)
        if direction == "unchanged":
            result.update(status="unchanged", changed=False, revision=data["revision"],
                          message="已经关注该阶段；未写状态或新增阶段历史")
            raise AdvanceUnchanged(result)
        # Preserve all completion/approval/outcome facts. Only a previously pending target starts.
        target_record = data["stages"][destination - 1]
        old_status = target_record["status"]
        if old_status == "pending":
            target_record["status"] = "in_progress"
        data["current_stage"] = destination
        data["stage_history"].append({"from": current, "to": destination, "at": project.now(),
                                      "note": note, "workflow_id": WORKFLOW,
                                      "previous_target_status": old_status, "status": target_record["status"],
                                      "gatekeeper": {"ruleset": RULESET, "source_revision": data["revision"],
                                                     "direction": direction, "intent": intent,
                                                     "override_reason": override,
                                                     "warnings": warnings, "completion_inferred": False}})
        result.update(status="advanced" if direction == "forward" else "returned", changed=True,
                      message="已更新关注阶段并记录理由/警告；未替任何阶段判完成。")

    try:
        data = project.update(root, operation, expected)
    except AdvanceUnchanged as exc:
        return exc.result
    result["revision"] = data["revision"]
    return result


def run(args):
    if args.stage_cmd == "check":
        result = check(args.root, args.number, args.intent)
        code = 2 if args.strict and result["software_status"] != "ready" else 0
    else:
        try:
            result = advance(args.root, args.to, args.override, args.note, args.expect_revision, args.intent)
            code = 0
        except AdvanceStopped as exc:
            result, code = exc.result, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if code:
        raise SystemExit(code)
    return result
