"""CAD-20 CPU fixtures. Files and human-source strings are synthetic, never art evidence."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
import cwb
import cwb_project as p
import cwb_gatekeeper as gate
from cwb_workflow import WORKFLOW, STAGES, REQUIRED_ROLES


class GatekeeperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cad20-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        p.initialize(self.root, "仅软件测试")

    def state(self):
        return p.read_state(self.root)[0]

    def snapshot(self):
        return {str(f.relative_to(self.root)): f.read_bytes() for f in self.root.rglob("*") if f.is_file()}

    def write_file(self, path, content=b"synthetic fixture, not a valid artwork"):
        f = self.root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content)
        return path

    def update(self, operation):
        return p.update(self.root, operation)

    def idea(self, name="seed", text_ref=None):
        self.update(lambda r, d: p.add_idea(r, d, name, "fixture", "" if text_ref else "测试起点", text_ref))
        return self.state()["ideas"][-1]

    def decide(self, kind, target, action="approve", outcomes=()):
        self.update(lambda r, d: p.add_decision(r, d, kind, target, action, "fixture范围", "人工转录测试，不是作者作品批准", "fixture", outcomes))
        return self.state()["decisions"][-1]

    def checkpoint(self, number, role, *, kind="checkpoint", version="v1", preview=False, bound=True,
                   doc_id=None, illustration="painting", object_id=None):
        if not self.state()["ideas"]:
            self.idea()
        doc_role = "ideation" if number <= 3 else "character" if number <= 5 else "turnaround" if number == 6 else "illustration"
        doc_id = doc_id or doc_role
        if not any(d["id"] == doc_id for d in self.state()["documents"]):
            path = self.write_file("documents/" + doc_id + ".kra")
            self.update(lambda r, d: p.add_document(r, d, doc_id, path, doc_role, "seed", illustration if doc_role == "illustration" else None))
        obj = object_id or role
        path = self.write_file("checkpoints/{}/{}/source.kra".format(obj, version), version.encode())
        preview_path = self.write_file("checkpoints/{}/{}/preview.png".format(obj, version), b"fixture preview") if preview else None
        self.update(lambda r, d: p.record(r, d, path, number, "final", "fixture", object_id=obj,
                                          version=version, role=role, document_id=doc_id if bound else None,
                                          kind=kind, region={"x": 0, "y": 0, "width": 10, "height": 10} if bound else None,
                                          preview=preview_path))
        return self.state()["assets"][-1]

    def prepare(self, number, inputs=True):
        if inputs:
            for n in gate.INPUT_STAGES[number]:
                if not any(d["target_kind"] == "stage" and d["target_id"] == STAGES[n - 1][0] for d in self.state()["decisions"]):
                    self.prepare(n)
        if number in (1, 3):
            if not self.state()["ideas"]:
                self.idea()
            if number == 3:
                self.decide("idea", "seed", "select")
            refs = ["idea:seed"]
        else:
            refs = ["asset:" + self.checkpoint(number, role)["id"] for role in sorted(REQUIRED_ROLES[number])]
        return self.decide("stage", STAGES[number - 1][0], outcomes=refs)

    def focus(self, number):
        self.update(lambda r, d: p.set_stage(d, number, "fixture navigation"))

    def codes(self, report):
        return {w["code"] for w in report["warnings"]}

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "core/cwb.py"), "stage", *map(str, args)],
                              text=True, capture_output=True, timeout=10)

    def test_missing_check_is_read_only_and_explicit(self):
        (self.root / p.LOCK).unlink()  # read-only check must not recreate even a lock
        before = self.snapshot()
        report = gate.check(self.root)
        self.assertEqual(report["software_status"], "warning")
        self.assertEqual(report["human_confirmation"]["status"], "pending")
        self.assertIn("missing_idea", self.codes(report))
        self.assertEqual(report["artistic_acceptance"], "unverified")
        self.assertEqual(self.snapshot(), before)

    def test_missing_then_supplement_then_advance_without_completed(self):
        before = self.snapshot()
        with self.assertRaises(gate.AdvanceStopped) as stopped:
            gate.advance(self.root)
        self.assertEqual(stopped.exception.result["status"], "stopped")
        self.assertEqual(self.snapshot(), before)
        self.idea()
        self.assertEqual(gate.check(self.root)["software_status"], "warning")
        approval = self.decide("stage", "text_seeds", outcomes=["idea:seed"])
        self.assertEqual(gate.check(self.root)["software_status"], "ready")
        result = gate.advance(self.root, expected=self.state()["revision"])
        self.assertEqual(result["status"], "advanced")
        data = self.state()
        self.assertEqual(data["current_stage"], 2)
        self.assertTrue(all(s["status"] != "completed" for s in data["stages"]))
        self.assertEqual(data["decisions"][-1]["id"], approval["id"])
        self.assertFalse(data["stage_history"][-1]["gatekeeper"]["completion_inferred"])

    def test_all_eight_rules_have_inputs_outputs_and_human_items(self):
        self.prepare(8)
        for number in range(1, 9):
            with self.subTest(number=number):
                report = gate.check(self.root, number)
                self.assertEqual(report["software_status"], "ready", report)
                self.assertTrue(report["human_check"])
                self.assertEqual(report["human_confirmation"]["status"], "recorded_unverified")
                self.assertEqual(report["artistic_acceptance"], "unverified")
                self.assertEqual(report["runtime_validation"], "unrun")

    def test_partial_turnaround_lists_exact_missing_roles(self):
        self.checkpoint(6, "turnaround_front")
        report = gate.check(self.root, 6)
        self.assertEqual(report["outcomes"]["missing_roles"], ["turnaround_back", "turnaround_side"])
        self.assertEqual(report["human_confirmation"]["status"], "pending")
        self.focus(6)
        result = gate.advance(self.root, override="临时查看插画计划，三视图仍待补齐")
        self.assertEqual(result["status"], "advanced")
        self.assertNotEqual(self.state()["stages"][5]["status"], "completed")
        self.assertEqual(gate.check(self.root, 6)["outcomes"]["missing_roles"], ["turnaround_back", "turnaround_side"])

    def test_skip_requires_reason_and_records_every_warning(self):
        before = self.snapshot()
        with self.assertRaises(gate.AdvanceStopped) as stopped:
            gate.advance(self.root, 7)
        self.assertIn("skipped_stages", self.codes(stopped.exception.result))
        self.assertEqual(self.snapshot(), before)
        with self.assertRaisesRegex(p.ProjectError, "非空"):
            gate.advance(self.root, 7, override="   ")
        result = gate.advance(self.root, 7, override="记录早期插画灵感，完整路线仍未完成")
        event = self.state()["stage_history"][-1]["gatekeeper"]
        self.assertEqual(event["override_reason"], result["override_reason"])
        self.assertEqual(event["warnings"], result["warnings"])
        self.assertTrue(all(s["status"] != "completed" for s in self.state()["stages"]))
        self.assertEqual(self.state()["decisions"], [])

    def test_backward_preserves_completion_and_records_direction(self):
        approval = self.prepare(1)
        self.update(lambda r, d: p.set_stage(d, 1, "fixture completed", "completed", approval["id"]))
        gate.advance(self.root, 6, override="仅导航测试")
        approved = copy.deepcopy(self.state()["stages"][0])
        result = gate.advance(self.root, 1, note="回看起点")
        self.assertEqual(result["status"], "returned")
        self.assertEqual(self.state()["stages"][0], approved)
        self.assertEqual(self.state()["stage_history"][-1]["gatekeeper"]["direction"], "backward")
        self.assertEqual(self.state()["stage_history"][-1]["note"], "回看起点")

    def test_unknown_stage_and_workflow_never_pass_or_write(self):
        before = self.snapshot()
        for number in (0, 9, -1, True, 1.0):
            report = gate.check(self.root, number)
            self.assertEqual(report["software_status"], "not_configured")
        self.assertEqual(self.snapshot(), before)
        data = self.state()
        data["workflow_id"] = "future-workflow"
        (self.root / p.STATE).write_text(json.dumps(data))
        before = self.snapshot()
        self.assertEqual(gate.check(self.root)["software_status"], "not_configured")
        with self.assertRaises(p.ProjectError):
            gate.advance(self.root, 2, override="不能跳过未知规则")
        self.assertEqual(self.snapshot(), before)

    def test_legacy_six_remains_unconfigured_after_migration_missing_new_views(self):
        legacy = {"schema_version": 1, "project_id": str(uuid.uuid4()), "name": "旧项目", "revision": 0,
                  "created_at": "fixture", "updated_at": "fixture", "current_stage": 6,
                  "intent_card": "旧意图", "design_pillars": [], "style_anchors": [], "assets": [],
                  "stages": [{"stage": n, "directory": p.stage_dir(n), "status": "completed" if n == 6 else "pending"} for n in range(1, 9)],
                  "stage_history": [{"from": 5, "to": 6, "at": "fixture", "note": "旧收敛"}]}
        (self.root / p.STATE).write_text(json.dumps(legacy))
        before = self.snapshot()
        self.assertEqual(gate.check(self.root)["software_status"], "not_configured")
        self.assertEqual(self.snapshot(), before)
        plan = p.migration_plan(self.root)
        p.upgrade(self.root, plan["source_sha256"], "fixture授权", 0)
        report = gate.check(self.root, 6)
        self.assertEqual(report["outcomes"]["missing_roles"], sorted(REQUIRED_ROLES[6]))
        self.assertTrue(all(s["status"] != "completed" for s in self.state()["stages"]))

    def test_working_and_unbound_visual_files_cannot_supply_required_role(self):
        self.checkpoint(4, "character_exploration", kind="working")
        self.checkpoint(4, "character_exploration", version="unbound", bound=False)
        report = gate.check(self.root, 4)
        self.assertEqual(report["outcomes"]["missing_roles"], ["character_exploration"])
        self.assertEqual(len(report["outcomes"]["unavailable"]), 2)

    def test_missing_or_changed_checkpoint_prevents_advance_without_mutation(self):
        self.prepare(4)
        self.focus(4)
        asset = next(a for a in self.state()["assets"] if a["stage"] == 4)
        for action in ("change", "remove"):
            with self.subTest(action=action):
                if action == "change":
                    (self.root / asset["path"]).write_bytes(b"different")
                else:
                    (self.root / asset["path"]).unlink()
                before = self.snapshot()
                report = gate.check(self.root)
                self.assertEqual(report["human_confirmation"]["status"], "needs_review")
                self.assertEqual(report["outcomes"]["missing_roles"], ["character_exploration"])
                with self.assertRaises(gate.AdvanceStopped):
                    gate.advance(self.root)
                self.assertEqual(self.snapshot(), before)

    def test_changed_preview_and_text_ref_are_not_silently_approved(self):
        self.write_file("ideas.md", b"original")
        self.idea(text_ref="ideas.md#seed")
        self.decide("stage", "text_seeds", outcomes=["idea:seed"])
        (self.root / "ideas.md").write_bytes(b"changed")
        self.assertEqual(gate.check(self.root)["human_confirmation"]["status"], "needs_review")
        asset = self.checkpoint(4, "character_exploration", preview=True)
        self.decide("stage", "character_exploration", outcomes=["asset:" + asset["id"]])
        (self.root / asset["preview"]["path"]).write_bytes(b"changed preview")
        self.assertEqual(gate.check(self.root, 4)["human_confirmation"]["status"], "needs_review")

    def test_reject_and_defer_after_approval_require_new_confirmation(self):
        self.prepare(4)
        asset = next(a for a in self.state()["assets"] if a["stage"] == 4)
        for action in ("reject", "defer"):
            with self.subTest(action=action):
                conflict = self.decide("asset", asset["id"], action)
                report = gate.check(self.root, 4)
                self.assertEqual(report["human_confirmation"]["status"], "conflict")
                self.assertEqual(report["human_confirmation"]["conflicting_decision_id"], conflict["id"])
                self.decide("stage", "character_exploration", outcomes=["asset:" + asset["id"]])
                self.assertEqual(gate.check(self.root, 4)["software_status"], "ready")

    def test_newest_invalid_stage_approval_does_not_fall_back(self):
        first = self.prepare(1)
        second = self.decide("stage", "text_seeds", outcomes=["idea:seed"])
        self.update(lambda r, d: d["decisions"][-1].update(validity="needs_review"))
        report = gate.check(self.root)
        self.assertEqual(report["human_confirmation"]["approval_id"], second["id"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(report["human_confirmation"]["status"], "needs_review")

    def test_new_selected_concept_conflicts_with_old_stage3_approval(self):
        self.prepare(3)
        self.idea("alternative")
        self.decide("idea", "alternative", "select")
        self.assertEqual(gate.check(self.root, 3)["human_confirmation"]["status"], "needs_review")

    def test_new_concept_approval_does_not_reuse_old_character_document(self):
        self.prepare(4)
        self.idea("alternative")
        self.decide("idea", "alternative", "select")
        self.decide("stage", "concept_selection", outcomes=["idea:alternative"])
        report = gate.check(self.root, 4)
        self.assertEqual(report["human_confirmation"]["status"], "needs_review")
        self.assertIn("创意", report["human_confirmation"]["reason"])

    def test_current_concept_reject_defer_blocks_stages5_to8_and_navigation(self):
        self.prepare(8)
        for action in ("reject", "defer"):
            conflict = self.decide("idea", "seed", action)
            for number in range(5, 9):
                with self.subTest(action=action, number=number):
                    self.focus(number)
                    before = self.snapshot()
                    check = self.cli("check", self.root, "--strict")
                    self.assertEqual(check.returncode, 2, check.stderr)
                    report = json.loads(check.stdout)
                    self.assertEqual(report["human_confirmation"]["active_concept"]["conflicting_decision_id"], conflict["id"])
                    if number < 8:
                        stopped = self.cli("advance", self.root)
                        self.assertEqual(stopped.returncode, 2, stopped.stderr)
                        self.assertEqual(json.loads(stopped.stdout)["status"], "stopped")
                    self.assertEqual(self.snapshot(), before)
            # Renewing a downstream art approval does not erase a mainline conflict.
            decision = next(d for d in self.state()["decisions"] if d["target_kind"] == "stage" and d["target_id"] == "character_resolution")
            self.decide("stage", "character_resolution", outcomes=[r["kind"] + ":" + r["id"] for r in decision["outcomes"]])
            self.assertEqual(gate.check(self.root, 5)["software_status"], "warning")
            # Explicit mainline reconciliation recovers without manufacturing completion.
            self.decide("stage", "concept_selection", outcomes=["idea:seed"])
            for number in range(3, 9):
                self.assertEqual(gate.check(self.root, number)["software_status"], "ready")
            self.assertTrue(all(s["status"] != "completed" for s in self.state()["stages"]))

    def test_current_concept_source_change_blocks_downstream_and_has_explicit_recovery(self):
        self.write_file("seed.md", b"original")
        self.idea(text_ref="seed.md")
        self.prepare(8)
        self.write_file("seed.md", b"changed source")
        for number in range(5, 9):
            self.focus(number)
            before = self.snapshot()
            check = self.cli("check", self.root, "--strict")
            self.assertEqual(check.returncode, 2, check.stderr)
            self.assertEqual(json.loads(check.stdout)["human_confirmation"]["active_concept"]["status"], "needs_review")
            if number < 8:
                self.assertEqual(self.cli("advance", self.root).returncode, 2)
            self.assertEqual(self.snapshot(), before)
        # The refresh has been persisted by focus; restoring bytes alone cannot
        # resurrect its invalidated selection or stage-03 approval.
        self.write_file("seed.md", b"original")
        self.assertEqual(gate.check(self.root, 5)["software_status"], "warning")
        self.decide("idea", "seed", "select")
        self.decide("stage", "concept_selection", outcomes=["idea:seed"])
        for number in range(5, 9):
            self.assertEqual(gate.check(self.root, number)["software_status"], "ready")

    def test_renewed_selection_recovers_concept_conflict_but_unselected_rejection_does_not_block(self):
        self.prepare(8)
        self.decide("idea", "seed", "defer")
        self.assertEqual(gate.check(self.root, 8)["software_status"], "warning")
        self.decide("idea", "seed", "select")
        for number in range(3, 9):
            self.assertEqual(gate.check(self.root, number)["software_status"], "ready")
        self.idea("unused")
        self.decide("idea", "unused", "reject")
        self.assertEqual(gate.check(self.root, 8)["software_status"], "ready")

    def test_active_concept_check_does_not_require_recursive_completed_history(self):
        self.prepare(8)
        # Stage05 uses stage04 and the current selected input, not all historical
        # stage approvals or completed flags. A valid direct selection is enough.
        self.update(lambda r, d: d.update(decisions=[x for x in d["decisions"] if not (
            x["target_kind"] == "stage" and x["target_id"] in ("text_seeds", "research_imagination", "concept_selection"))]))
        self.assertEqual(gate.check(self.root, 5)["software_status"], "ready")
        self.assertTrue(all(s["status"] != "completed" for s in self.state()["stages"]))

    def test_different_illustration_and_saved_document_require_matching_plan_handoff(self):
        self.prepare(7)
        for doc_id, illustration in (("other-painting", "painting-b"), ("saved-copy", "painting")):
            with self.subTest(doc_id=doc_id, illustration=illustration):
                refs = ["asset:" + self.checkpoint(8, role, doc_id=doc_id, illustration=illustration,
                        object_id=doc_id + "_" + role)["id"] for role in sorted(REQUIRED_ROLES[8])]
                self.decide("stage", "illustration_production", outcomes=refs)
                self.focus(7)
                before = self.snapshot()
                check = self.cli("check", self.root, "--number", 8, "--strict")
                self.assertEqual(check.returncode, 2, check.stderr)
                report = json.loads(check.stdout)
                self.assertEqual(report["human_confirmation"]["illustration_handoff"]["status"], "needs_review")
                stopped = self.cli("advance", self.root)
                self.assertEqual(stopped.returncode, 2, stopped.stderr)
                self.assertIn("target_illustration_mismatch", self.codes(json.loads(stopped.stdout)))
                self.assertEqual(self.snapshot(), before)
                # Explicitly bind and approve the real saved plan for this work;
                # same concept/illustration name alone never supplies the lineage.
                plan = self.checkpoint(7, "illustration_plan", doc_id=doc_id, illustration=illustration,
                                       object_id=doc_id + "_plan")
                self.decide("stage", "illustration_planning", outcomes=["asset:" + plan["id"]])
                self.assertEqual(gate.check(self.root, 8)["software_status"], "ready")
                self.assertEqual(self.cli("advance", self.root).returncode, 0)

    def test_ambiguous_plan_and_mixed_bound_delivery_documents_are_not_ready(self):
        self.prepare(8)
        other_plan = self.checkpoint(7, "illustration_plan", doc_id="other", illustration="painting-b", object_id="other_plan")
        original_plan = next(a for a in self.state()["assets"] if a["role"] == "illustration_plan")
        self.decide("stage", "illustration_planning", outcomes=["asset:" + a["id"] for a in (original_plan, other_plan)])
        self.assertEqual(gate.check(self.root, 8)["software_status"], "warning")
        self.decide("stage", "illustration_planning", outcomes=["asset:" + original_plan["id"]])
        mixed_source = self.checkpoint(8, "editable_source", doc_id="other", illustration="painting-b", object_id="other_source")
        final_refs = ["asset:" + a["id"] for a in self.state()["assets"] if a["stage"] == 8 and a["role"] != "editable_source"]
        self.decide("stage", "illustration_production", outcomes=final_refs + ["asset:" + mixed_source["id"]])
        self.assertEqual(gate.check(self.root, 8)["software_status"], "warning")

    def test_unbound_source_and_manifest_keep_existing_schema_contract(self):
        self.prepare(7)
        refs = ["asset:" + self.checkpoint(8, role, bound=role == "final_illustration")["id"]
                for role in sorted(REQUIRED_ROLES[8])]
        self.decide("stage", "illustration_production", outcomes=refs)
        self.assertEqual(gate.check(self.root, 8)["software_status"], "ready")

    def test_manual_selection_without_human_select_record_is_pending(self):
        self.prepare(3)
        self.update(lambda r, d: d.update(decisions=[x for x in d["decisions"] if x["action"] != "select"]))
        report = gate.check(self.root, 3)
        self.assertEqual(report["human_confirmation"]["status"], "needs_review")
        self.assertIn("选择记录", report["human_confirmation"]["reason"])

    def test_object_approval_cannot_silently_reconcile_stage_scope_conflict(self):
        self.prepare(4)
        asset = next(a for a in self.state()["assets"] if a["stage"] == 4)
        self.decide("asset", asset["id"], "reject")
        self.decide("asset", asset["id"], "approve")
        self.assertEqual(gate.check(self.root, 4)["human_confirmation"]["status"], "conflict")

    def test_missing_source_version_mismatch_and_missing_field_reject_without_write(self):
        self.prepare(1)
        original = self.state()
        variants = []
        missing = copy.deepcopy(original)
        del missing["decisions"][0]["source"]
        variants.append(missing)
        unknown = copy.deepcopy(original)
        unknown["decisions"][0]["source"] = " "
        variants.append(unknown)
        mismatch = copy.deepcopy(original)
        mismatch["decisions"][0]["outcomes"][0]["version"] = "wrong-version"
        variants.append(mismatch)
        contradictory = copy.deepcopy(original)
        contradictory["stages"][0]["status"] = "completed"
        contradictory["stages"][0]["approval_id"] = None
        variants.append(contradictory)
        missing_stage = copy.deepcopy(original)
        del missing_stage["current_stage"]
        variants.append(missing_stage)
        for variant in variants:
            (self.root / p.STATE).write_text(json.dumps(variant))
            before = self.snapshot()
            with self.assertRaises(p.ProjectError):
                gate.check(self.root)
            with self.assertRaises(p.ProjectError):
                gate.advance(self.root, override="不能绕过结构损坏")
            self.assertEqual(self.snapshot(), before)

    def test_detail_intent_warns_and_never_calls_http(self):
        report = gate.check(self.root, 4, "detail-render")
        self.assertIn("detail_before_confirmation", self.codes(report))
        with patch.object(cwb, "http", side_effect=AssertionError("offline command contacted Krita")):
            with patch("builtins.print"):
                cwb.main(["stage", "check", str(self.root), "--number", "4", "--intent", "detail-render"])
        self.prepare(4)
        self.assertNotIn("detail_before_confirmation", self.codes(gate.check(self.root, 4, "detail-render")))

    def test_declared_structure_change_and_observed_version_change_warn(self):
        self.prepare(6)
        report = gate.check(self.root, 6, "structural-change")
        self.assertIn("declared_structural_change", self.codes(report))
        self.checkpoint(5, "character_master_lineart", version="v2")
        report = gate.check(self.root, 6)
        self.assertEqual(report["human_confirmation"]["status"], "needs_review")
        self.assertIn("downstream_needs_review", self.codes(report))
        self.assertEqual(report["downstream_review_stages"], [5, 6, 7, 8])

    def test_new_approval_after_version_change_recovers_without_inventing_completed(self):
        self.prepare(5)
        self.checkpoint(5, "character_master_lineart", version="v2")
        assets = self.state()["assets"]
        line = [a for a in assets if a["role"] == "character_master_lineart"][-1]
        color = next(a for a in assets if a["role"] == "character_master_basecolor")
        self.decide("stage", "character_resolution", outcomes=["asset:" + line["id"], "asset:" + color["id"]])
        self.assertEqual(gate.check(self.root, 5)["software_status"], "ready")
        self.assertNotEqual(self.state()["stages"][4]["status"], "completed")

    def test_stale_revision_busy_lock_and_atomic_failure_recover(self):
        self.prepare(1)
        before = self.snapshot()
        with self.assertRaisesRegex(p.ProjectError, "修订冲突"):
            gate.advance(self.root, expected=0)
        with p.locked(self.root):
            with self.assertRaisesRegex(p.ProjectError, "另一进程"):
                gate.advance(self.root)
        with patch.object(p.os, "replace", side_effect=OSError("synthetic replace failure")):
            with self.assertRaises(OSError):
                gate.advance(self.root)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(gate.advance(self.root)["status"], "advanced")

    def test_repeated_same_target_and_final_boundary_do_not_add_history(self):
        before = self.snapshot()
        result = gate.advance(self.root, 1)
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(self.snapshot(), before)
        gate.advance(self.root, 8, override="只查看最后阶段")
        before = self.snapshot()
        with self.assertRaisesRegex(p.ProjectError, "最后阶段"):
            gate.advance(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_evaluate_does_not_modify_callers_data(self):
        self.prepare(1)
        data = self.state()
        original = copy.deepcopy(data)
        gate.evaluate(self.root, data)
        self.assertEqual(data, original)

    def test_same_target_with_changed_outcome_does_not_persist_refresh(self):
        self.write_file("ideas.md", b"original")
        self.idea(text_ref="ideas.md")
        self.decide("stage", "text_seeds", outcomes=["idea:seed"])
        (self.root / "ideas.md").write_bytes(b"changed")
        before = self.snapshot()
        result = gate.advance(self.root, 1)
        self.assertFalse(result["changed"])
        self.assertEqual(result["check"]["human_confirmation"]["status"], "needs_review")
        self.assertEqual(self.snapshot(), before)

    def test_cli_warning_strict_stop_override_backward_and_help(self):
        before = self.snapshot()
        warning = self.cli("check", self.root)
        self.assertEqual(warning.returncode, 0, warning.stderr)
        self.assertEqual(json.loads(warning.stdout)["software_status"], "warning")
        strict = self.cli("check", self.root, "--strict")
        self.assertEqual(strict.returncode, 2, strict.stderr)
        stopped = self.cli("advance", self.root)
        self.assertEqual(stopped.returncode, 2, stopped.stderr)
        self.assertEqual(json.loads(stopped.stdout)["status"], "stopped")
        self.assertEqual(self.snapshot(), before)
        skipped = self.cli("advance", self.root, "--to", 6, "--override", "fixture跳过，仍未完成")
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        back = self.cli("advance", self.root, "--to", 1, "--note", "fixture回退")
        self.assertEqual(back.returncode, 0, back.stderr)
        before = self.snapshot()
        for command in ("check", "advance", "setup", "status", "import-diagnostic"):
            help_result = self.cli(command, "--help")
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
