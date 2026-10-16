"""PR-02 A evidence only: synthetic files/mock trees, never real art or Krita."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
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
import cwb_export as export
import cwb_stage as stage
from cwb_workflow import WORKFLOW, LEGACY_WORKFLOW, DOCUMENT_STAGES
from test_stage import Fake


def legacy_state():
    """Frozen schema1 fixture: do not use new init to manufacture old semantics."""
    return {"schema_version": 1, "project_id": str(uuid.uuid4()), "name": "旧测试项目",
            "revision": 6, "created_at": "fixture-time", "updated_at": "fixture-time",
            "current_stage": 6, "intent_card": "旧意图", "design_pillars": [], "style_anchors": [],
            "stages": [{"stage": i, "directory": p.stage_dir(i), "status": "completed" if i == 6 else "pending"} for i in range(1, 9)],
            "stage_history": [{"from": 5, "to": 6, "at": "fixture-time", "note": "旧收敛"}], "assets": []}


class WorkflowProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pr02-")
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.root = self.parent / "project"
        self.initial = p.initialize(self.root, "测试，不是真实作品")

    def state(self):
        return p.read_state(self.root)[0]

    def update(self, operation, expected=None):
        return p.update(self.root, operation, expected)

    def file(self, path, content=b"synthetic fixture, not a real KRA"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return path

    def idea(self, name="seed", sources=(), text_ref=None):
        return self.update(lambda r, d: p.add_idea(r, d, name, name, "" if text_ref else "纯测试起点", text_ref, sources))

    def document(self, role="character", doc_id="doc"):
        if not self.state()["ideas"]:
            self.idea()
        path = self.file("documents/" + doc_id + ".kra")
        self.update(lambda r, d: p.add_document(r, d, doc_id, path, role, "seed" if role != "ideation" else None,
                                               "painting" if role == "illustration" else None))
        return path

    def checkpoint(self, role="character_master_lineart", number=5, obj="master", version="v1", doc="doc"):
        path = self.file("checkpoints/" + obj + "/" + version + "/saved.kra")
        preview = self.file("checkpoints/" + obj + "/" + version + "/preview.png", b"fixture-preview-" + version.encode())
        self.update(lambda r, d: p.record(r, d, path, number, "final", "fixture", object_id=obj, version=version,
                                          role=role, document_id=doc, kind="checkpoint", region={"x": 0, "y": 0, "width": 10, "height": 20}, preview=preview))
        return self.state()["assets"][-1]

    def decision(self, kind, target, action="approve", outcomes=()):
        self.update(lambda r, d: p.add_decision(r, d, kind, target, action, "仅本轮测试", "人工转录：测试指令，不是用户作品批准", "fixture", outcomes))
        return self.state()["decisions"][-1]

    def install_legacy(self):
        data = legacy_state()
        path = self.file("stages/06-收敛/old.png", b"old fixture")
        digest, size = p.fingerprint(self.root, path)
        data["assets"] = [{"id": str(uuid.uuid4()), "path": path, "stage": 6, "category": "final", "sha256": digest,
                           "size": size, "note": "legacy", "recorded_at": "fixture-time"}]
        (self.root / p.STATE).write_text(json.dumps(data, ensure_ascii=False))
        return data, (self.root / p.STATE).read_bytes()

    def test_T01_schema1_read_only_old_stage6_and_export(self):
        data, raw = self.install_legacy()
        (self.root / p.LOCK).unlink()  # fixture verifies reads don't even create a lock
        listing = p.inspect(self.root)
        self.assertEqual(listing["workflow"], LEGACY_WORKFLOW)
        self.assertEqual(listing["state"]["stages"][5]["directory"], "stages/06-收敛")
        self.assertEqual(listing["asset_checks"][0]["status"], "ok")
        self.assertFalse((self.root / p.LOCK).exists())
        report = export.export_project(self.root, self.parent / "old-export", [data["assets"][0]["id"]])
        self.assertEqual(report["current_stage"], 6)
        self.assertEqual((self.parent / "old-export" / p.STATE).read_bytes(), raw)
        self.assertEqual(p.initialize(self.root, data["name"]), data)
        with self.assertRaisesRegex(p.ProjectError, "只读"):
            self.update(lambda r, d: d.update(name="mutated"))
        self.assertEqual((self.root / p.STATE).read_bytes(), raw)

    def test_T02_plan_read_only_upgrade_preserves_origins_not_completion(self):
        old, raw = self.install_legacy()
        before = {f: f.read_bytes() for f in self.root.rglob("*") if f.is_file()}
        plan = p.migration_plan(self.root)
        self.assertEqual(plan["stages"][5]["candidate_stages"], [5])
        self.assertFalse(plan["stages"][5]["inherits_completed"])
        self.assertEqual(before, {f: f.read_bytes() for f in self.root.rglob("*") if f.is_file()})
        data = p.upgrade(self.root, plan["source_sha256"], "人工转录：授权升级fixture", 6)
        self.assertEqual(data["workflow_id"], WORKFLOW)
        self.assertEqual(data["stages"][5]["stage_id"], "turnaround")
        self.assertEqual(data["stages"][5]["status"], "pending")
        self.assertTrue(all(s["status"] != "completed" for s in data["stages"]))
        self.assertIsNone(data["selected_concept_id"])
        self.assertEqual(data["decisions"], [])
        self.assertEqual(data["assets"][0]["path"], old["assets"][0]["path"])
        self.assertEqual(data["assets"][0]["workflow_id"], LEGACY_WORKFLOW)
        self.assertEqual(data["assets"][0]["legacy_origin"]["stage"], 6)
        self.assertEqual(data["stage_history"][0]["workflow_id"], LEGACY_WORKFLOW)
        self.assertEqual((self.root / data["migration"]["backup"]).read_bytes(), raw)
        self.assertFalse((self.root / "stages/06-三视图").exists())
        # Existing legacy final export remains consumable after upgrade, with explicit origin.
        report = export.export_project(self.root, self.parent / "migrated-export", [old["assets"][0]["id"]])
        self.assertEqual(report["assets"][0]["workflow_id"], LEGACY_WORKFLOW)
        with self.assertRaisesRegex(p.ProjectError, "已是 schema 2"):
            p.upgrade(self.root, plan["source_sha256"], "fixture", 6)
        self.assertEqual(len(list(self.root.glob("*.bak"))), 1)

    def test_upgrade_missing_authorization_stale_digest_revision_no_write(self):
        _, raw = self.install_legacy()
        digest = hashlib.sha256(raw).hexdigest()
        for sha, auth, rev in ((digest, "", 6), ("0" * 64, "yes", 6), (digest, "yes", 5)):
            with self.assertRaises(p.ProjectError):
                p.upgrade(self.root, sha, auth, rev)
            self.assertEqual((self.root / p.STATE).read_bytes(), raw)
            self.assertEqual(list(self.root.glob("*.bak")), [])

    def test_T19_failed_upgrade_keeps_old_and_non_overwritten_backups(self):
        _, raw = self.install_legacy()
        digest = hashlib.sha256(raw).hexdigest()
        with patch.object(p.os, "replace", side_effect=OSError("injected full disk")):
            with self.assertRaisesRegex(p.ProjectError, "backup="):
                p.upgrade(self.root, digest, "fixture authorization", 6)
        self.assertEqual((self.root / p.STATE).read_bytes(), raw)
        backups = list(self.root.glob("*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), raw)
        p.upgrade(self.root, digest, "retry after inspecting state", 6)
        self.assertEqual(len(list(self.root.glob("*.bak"))), 2)
        self.assertEqual(backups[0].read_bytes(), raw)

    def test_existing_backup_name_never_overwritten_even_on_collision(self):
        _, raw = self.install_legacy()
        token = uuid.UUID("12345678-1234-4234-8234-123456789012")
        backup = self.root / ("project_state.schema1-fixed-" + str(token) + ".json.bak")
        backup.write_bytes(b"keep existing backup")
        with patch.object(p, "datetime") as clock, patch.object(p.uuid, "uuid4", return_value=token):
            clock.now.return_value.strftime.return_value = "fixed"
            clock.now.return_value.isoformat.return_value = "fixture-time"
            with self.assertRaisesRegex(p.ProjectError, "升级未确认"):
                p.upgrade(self.root, hashlib.sha256(raw).hexdigest(), "fixture", 6)
        self.assertEqual(backup.read_bytes(), b"keep existing backup")
        self.assertEqual((self.root / p.STATE).read_bytes(), raw)

    def test_migration_external_editor_conflict_retains_backup_and_user_change(self):
        _, raw = self.install_legacy()
        actual = p.atomic_write
        def raced(root, data, previous):
            (root / p.STATE).write_bytes(raw + b" ")
            return actual(root, data, previous)
        with patch.object(p, "atomic_write", side_effect=raced):
            with self.assertRaisesRegex(p.ProjectError, "已改变"):
                p.upgrade(self.root, hashlib.sha256(raw).hexdigest(), "fixture", 6)
        self.assertEqual((self.root / p.STATE).read_bytes(), raw + b" ")
        self.assertEqual(list(self.root.glob("*.bak"))[0].read_bytes(), raw)

    def test_T03_T20_many_light_ideas_split_merge_selection_and_scoped_rejection(self):
        for i in range(20):
            self.idea("seed-" + str(i))
        self.idea("split-a", ("seed-0",))
        self.idea("split-b", ("seed-0",))
        self.idea("merged", ("split-a", "seed-1"))
        self.decision("idea", "merged", "select")
        rejected = self.decision("idea", "split-b", "reject")
        data = self.state()
        self.assertEqual(len(data["ideas"]), 23)
        self.assertEqual(data["selected_concept_id"], "merged")
        self.assertEqual(data["ideas"][-1]["source_idea_ids"], ["split-a", "seed-1"])
        self.assertEqual(rejected["scope"], "仅本轮测试")
        self.assertEqual(rejected["execution"], "recorded_not_applied")
        self.assertNotIn("negative_prompt", data)
        self.assertTrue(all(s["status"] != "completed" for s in data["stages"]))
        with self.assertRaises(p.ProjectError):
            self.idea("broken", ("not-bound",))
        self.assertEqual(self.state(), data)

    def test_T04_missing_text_reference_and_duplicate_ids(self):
        self.file("notes/ideas.md", b"# stable-id fixture")
        self.idea(text_ref="notes/ideas.md#stable-id")
        self.decision("idea", "seed")
        before = (self.root / p.STATE).read_bytes()
        (self.root / "notes/ideas.md").unlink()
        view = p.inspect(self.root)
        self.assertEqual(view["reference_checks"][0]["status"], "unbound_needs_confirmation")
        self.assertEqual(view["state"]["decisions"][0]["validity"], "needs_review")
        self.assertEqual((self.root / p.STATE).read_bytes(), before)
        bad = self.state()
        bad["ideas"].append(copy.deepcopy(bad["ideas"][0]))
        with self.assertRaisesRegex(p.ProjectError, "重复"):
            p.validate(bad)

    def test_T21_mutable_documents_multiple_versions_do_not_repoint_snapshot(self):
        working = self.document()
        self.document("character", "second-version")
        asset = self.checkpoint()
        decision = self.decision("asset", asset["id"])
        old_digest = asset["sha256"]
        self.file(working, b"normal saved working edit")
        view = p.inspect(self.root)
        self.assertEqual(view["state"]["decisions"][0]["validity"], "valid")
        self.assertEqual(view["state"]["assets"][0]["sha256"], old_digest)
        self.assertEqual(view["state"]["decisions"][0]["target_version"], "v1")
        self.assertEqual(len(view["state"]["documents"]), 2)
        self.assertEqual(decision["execution"], "recorded_not_applied")

    def test_T14_changed_checkpoint_preview_reports_review_read_only(self):
        self.document()
        asset = self.checkpoint()
        self.decision("asset", asset["id"])
        before = (self.root / p.STATE).read_bytes()
        self.file(asset["preview"]["path"], b"user modified preview")
        view = p.inspect(self.root)
        self.assertEqual(view["state"]["decisions"][0]["validity"], "needs_review")
        self.assertTrue(all(view["state"]["stages"][i]["needs_review"] for i in (4, 5, 6, 7)))
        self.assertEqual((self.root / p.STATE).read_bytes(), before)
        with self.assertRaisesRegex(p.ProjectError, "预览已变化"):
            self.decision("asset", asset["id"])

    def test_T14_new_master_version_invalidates_approval_and_downstream(self):
        self.document()
        asset = self.checkpoint()
        self.decision("asset", asset["id"])
        # A relocated explicit version with identical content does not create false review churn.
        identical = self.file("checkpoints/master/v1-copy/saved.kra", (self.root / asset["path"]).read_bytes())
        preview = self.file("checkpoints/master/v1-copy/preview.png", (self.root / asset["preview"]["path"]).read_bytes())
        self.update(lambda r, d: p.record(r, d, identical, 5, "final", "fixture", object_id="master", version="v1-copy",
                                          role=asset["role"], document_id="doc", kind="checkpoint", region=asset["region"], preview=preview))
        self.assertEqual(self.state()["decisions"][0]["validity"], "valid")
        self.assertFalse(self.state()["stages"][5]["needs_review"])
        self.checkpoint(version="v2")
        data = self.state()
        self.assertEqual(data["decisions"][0]["validity"], "needs_review")
        self.assertEqual(data["assets"][0], asset)
        self.assertTrue(all(data["stages"][i]["needs_review"] for i in (4, 5, 6, 7)))
        self.assertEqual(data["assets"][-1]["version"], "v2")

    def test_stage_completion_needs_all_outcomes_human_source_and_current_approval(self):
        self.document()
        line = self.checkpoint()
        with self.assertRaisesRegex(p.ProjectError, "必要成果"):
            self.decision("stage", "character_resolution", outcomes=["asset:" + line["id"]])
        color = self.checkpoint(role="character_master_basecolor", obj="colors")
        approval = self.decision("stage", "character_resolution", outcomes=["asset:" + line["id"], "asset:" + color["id"]])
        self.update(lambda r, d: p.set_stage(d, 5, "人工测试", "completed", approval["id"]))
        self.assertEqual(self.state()["stages"][4]["status"], "completed")
        with self.assertRaises(p.ProjectError):
            self.update(lambda r, d: p.set_stage(d, 6, "wrong stage", "completed", approval["id"]))
        self.file(line["path"], b"changed approved bytes")
        with self.assertRaises(p.ProjectError):
            self.update(lambda r, d: p.set_stage(d, 5, "stale", "completed", approval["id"]))
        self.assertEqual(p.inspect(self.root)["state"]["stages"][4]["status"], "awaiting_review")
        self.assertEqual(p.read_state(self.root)[0]["stages"][4]["status"], "completed")  # read-only effective view

    def test_working_file_cannot_be_approved_and_version_cannot_be_rewritten(self):
        path = self.file("working.png")
        self.update(lambda r, d: p.record(r, d, path, 4, "final", "fixture", object_id="a", version="v1"))
        asset = self.state()["assets"][0]
        with self.assertRaisesRegex(p.ProjectError, "确认快照"):
            self.decision("asset", asset["id"])
        self.file(path, b"user edit")
        with self.assertRaisesRegex(p.ProjectError, "版本已登记"):
            self.update(lambda r, d: p.record(r, d, path, 4, "final", "fixture", object_id="a", version="v1"))
        self.assertEqual(self.state()["assets"][0], asset)

    def test_schema2_corrupt_workflow_or_references_never_write(self):
        self.document()
        asset = self.checkpoint()
        self.decision("asset", asset["id"])
        original = self.state()
        changes = [lambda d: d.update(workflow_id="unknown"),
                   lambda d: d["stages"][5].update(stage_id="收敛"),
                   lambda d: d["assets"][0].update(document_id="missing"),
                   lambda d: d["assets"][0].update(region={"x": True, "y": 0, "width": 10, "height": 10}),
                   lambda d: d["decisions"][0].update(source=""),
                   lambda d: d["decisions"][0]["outcomes"][0].update(version="wrong"),
                   lambda d: d["ideas"][0].update(source_idea_ids=["seed"])]
        for change in changes:
            bad = copy.deepcopy(original)
            change(bad)
            raw = json.dumps(bad).encode()
            (self.root / p.STATE).write_bytes(raw)
            with self.assertRaises(p.ProjectError):
                self.update(lambda r, d: d.update(name="must not write"))
            self.assertEqual((self.root / p.STATE).read_bytes(), raw)
        (self.root / p.STATE).write_text(json.dumps(original))

    def test_no_empty_tables_directories_or_works_and_help_no_imports(self):
        self.assertEqual({f.name for f in self.root.iterdir()}, {p.STATE, p.LOCK})
        script = self.parent / "standalone" / "cwb.py"
        script.parent.mkdir()
        shutil.copyfile(ROOT / "core/cwb.py", script)
        commands = ("init", "status", "migration-plan", "upgrade", "idea", "document", "decision", "record", "stage", "edit")
        for command in commands:
            result = subprocess.run([sys.executable, str(script), "project", command, "--help"],
                                    env={**os.environ, "CWB_WF_DIR": str(self.parent / "absent-workflow")},
                                    cwd=self.parent, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((script.parent / "__pycache__").exists())
        self.assertFalse((self.parent / "absent-workflow").exists())

    def test_cli_new_commands_are_offline_and_conflicts_do_not_write(self):
        import contextlib
        import io
        def cli(*args):
            with patch.object(cwb, "http", side_effect=AssertionError("must stay offline")), contextlib.redirect_stdout(io.StringIO()) as output:
                cwb.main(["project", *map(str, args)])
            return json.loads(output.getvalue())
        cli("idea", self.root, "seed", "轻量", "--text", "fixture", "--expect-revision", 0)
        working = self.file("documents/ideation/board.kra")
        cli("document", self.root, "board", working, "--role", "ideation")
        cli("decision", self.root, "idea", "seed", "select", "--scope", "本轮", "--source", "人工转录测试")
        before = (self.root / p.STATE).read_bytes()
        with self.assertRaisesRegex(p.ProjectError, "修订冲突"):
            cli("idea", self.root, "stale", "过期", "--text", "fixture", "--expect-revision", 0)
        self.assertEqual((self.root / p.STATE).read_bytes(), before)
        self.assertEqual(cli("status", self.root)["state"]["selected_concept_id"], "seed")


class WorkflowStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pr02-stage-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.doc = self.root / "fixture.kra"
        self.doc.write_bytes(b"not a real kra")
        self.sidecar = self.root / "fixture.cwb-stage.json"
        self.fake = Fake(self.doc)

    def execute(self, number=4, role="character", workflow=WORKFLOW):
        return stage.execute(number, self.doc, self.sidecar, self.fake.request, workflow=workflow, role=role)

    def data(self):
        return json.loads(self.sidecar.read_text())

    def test_roles_only_create_applicable_document_groups_and_repeat_reopen(self):
        for role, numbers in DOCUMENT_STAGES.items():
            with self.subTest(role=role):
                self.sidecar = self.root / (role + ".cwb-stage.json")
                self.fake = Fake(self.doc)
                first = self.execute(numbers[0], role)
                self.assertEqual(len(first["created"]), len(stage.specs(numbers[0], WORKFLOW, role)))
                self.assertTrue(all(k.startswith(role + "/") for k in first["groups"]))
                self.assertEqual(self.data()["schema_version"], 2)
                before = self.fake.posts
                self.fake.reopen()
                self.assertEqual(self.execute(numbers[-1], role)["created"], [])
                self.assertEqual(self.fake.posts, before)
                self.assertTrue(all(g["id"].endswith("-reopen") for g in self.data()["groups"].values()))

    def test_T04_unregistered_same_name_and_ambiguous_markers_not_adopted(self):
        self.fake.add("30_主稿", kind="paintlayer")
        with self.assertRaisesRegex(stage.StageError, "同名"):
            self.execute()
        self.assertEqual(self.fake.posts, 0)
        self.fake = Fake(self.doc)
        self.execute()
        record = next(iter(self.data()["groups"].values()))
        self.fake.add(record["name"])
        before = self.fake.posts
        with self.assertRaisesRegex(stage.StageError, "歧义"):
            self.execute()
        self.assertEqual(self.fake.posts, before)

    def test_legacy_sidecar_uses_frozen_keys_new_context_rejected(self):
        stage.execute(6, self.doc, self.sidecar, self.fake.request)
        data = self.data()
        self.assertIn("6/Paintover", data["groups"])
        self.assertEqual(stage.validate(data), data)
        before = self.sidecar.read_bytes(), self.fake.posts
        with self.assertRaisesRegex(stage.StageError, "冲突"):
            self.execute(6, "turnaround")
        self.assertEqual((self.sidecar.read_bytes(), self.fake.posts), before)
        self.assertEqual(stage.execute(6, self.doc, self.sidecar, self.fake.request)["created"], [])

    def test_T19_new_sidecar_partial_failure_keeps_pending_never_retries(self):
        self.fake.fail_at, self.fake.mode = 2, "after"
        with self.assertRaisesRegex(stage.StageError, "未确认"):
            self.execute()
        data = self.data()
        self.assertEqual(data["schema_version"], 2)
        self.assertIsNotNone(data["pending"])
        self.assertEqual(list(data["groups"].values())[0]["status"], "verified")
        posts = self.fake.posts
        with self.assertRaisesRegex(stage.StageError, "未确认操作"):
            self.execute()
        self.assertEqual(self.fake.posts, posts)

    def test_user_modified_sidecar_conflict_prevents_first_post(self):
        original = stage.Store.save
        def raced(store, data):
            self.sidecar.write_bytes(b"user manual edit")
            return original(store, data)
        with patch.object(stage.Store, "save", raced):
            with self.assertRaisesRegex(stage.StageError, "其他写入者"):
                self.execute()
        self.assertEqual(self.fake.posts, 0)
        self.assertEqual(self.sidecar.read_bytes(), b"user manual edit")

    def test_new_cli_context_and_existing_sidecar_inference(self):
        import contextlib
        import io
        def http(url, payload=None, timeout=60):
            return self.fake.request(url[len(cwb.Krita):], payload)
        with patch.object(cwb, "http", side_effect=http), contextlib.redirect_stdout(io.StringIO()) as output:
            cwb.main(["stage", "setup", "6", "--workflow", WORKFLOW, "--role", "turnaround",
                      "--state", str(self.sidecar), "--document", str(self.doc)])
        self.assertEqual(json.loads(output.getvalue())["document_role"], "turnaround")
        before = self.fake.posts
        self.assertEqual(stage.execute(6, self.doc, self.sidecar, self.fake.request, workflow=WORKFLOW)["created"], [])
        self.assertEqual(self.fake.posts, before)
        self.assertEqual(self.data()["schema_version"], 2)

    def test_incompatible_role_or_workflow_fails_before_service(self):
        for number, workflow, role in ((6, WORKFLOW, "character"), (4, "unknown", "character"), (4, LEGACY_WORKFLOW, "character")):
            with self.assertRaises(stage.StageError):
                self.execute(number, role, workflow)
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
