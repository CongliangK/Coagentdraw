"""Offline evidence only: synthetic files/mock trees, never real art or Krita."""
import copy
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
import cwb_stage as stage
from cwb_workflow import WORKFLOW, DOCUMENT_STAGES
from test_stage import Fake


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


    def test_unsupported_project_versions_are_rejected_without_rewriting(self):
        original = self.state()
        for version in (0, 1, 3, True, "2"):
            with self.subTest(version=version):
                raw = json.dumps({**original, "schema_version": version}).encode()
                (self.root / p.STATE).write_bytes(raw)
                with self.assertRaises(p.ProjectError):
                    p.inspect(self.root)
                with self.assertRaises(p.ProjectError):
                    self.update(lambda r, d: d.update(name="must not write"))
                self.assertEqual((self.root / p.STATE).read_bytes(), raw)
        (self.root / p.STATE).write_text(json.dumps(original))

    def test_same_character_document_materials_complete_research_with_human_evidence(self):
        self.document()
        material = self.checkpoint(role="research_materials", number=2, obj="materials")
        approval = self.decision("stage", "research_imagination", outcomes=["asset:" + material["id"]])
        self.update(lambda r, d: p.set_stage(d, 2, "fixture only", "completed", approval["id"]))
        self.assertEqual(self.state()["stages"][1]["status"], "completed")
        self.assertEqual(len(self.state()["documents"]), 1)
        # Same document supports exploration; materials do not approve that stage.
        self.checkpoint(role="character_exploration", number=4, obj="exploration")
        with self.assertRaises(p.ProjectError):
            self.update(lambda r, d: p.set_stage(d, 4, "invalid", "completed", approval["id"]))
        self.assertNotEqual(self.state()["stages"][3]["status"], "completed")

    def test_research_requires_materials_and_snapshot_and_visual_binding(self):
        self.document()
        unrelated = self.checkpoint(role="character_exploration", number=2, obj="unrelated")
        with self.assertRaisesRegex(p.ProjectError, "必要成果"):
            self.decision("stage", "research_imagination", outcomes=["asset:" + unrelated["id"]])
        path = self.file("previews/materials.png")
        self.update(lambda r, d: p.record(r, d, path, 2, "final", "fixture", role="research_materials", document_id="doc"))
        working = self.state()["assets"][-1]
        with self.assertRaisesRegex(p.ProjectError, "确认快照"):
            self.decision("stage", "research_imagination", outcomes=["asset:" + working["id"]])
        self.update(lambda r, d: p.record(r, d, self.file("checkpoints/unbound/v1/image.png"), 2,
                                          "final", "fixture", object_id="unbound", version="v1",
                                          kind="checkpoint", role="research_materials"))
        unbound = self.state()["assets"][-1]
        with self.assertRaisesRegex(p.ProjectError, "视觉成果"):
            self.decision("stage", "research_imagination", outcomes=["asset:" + unbound["id"]])
        self.assertEqual(self.state()["decisions"], [])

    def test_independent_research_board_remains_a_valid_optional_document(self):
        self.document("ideation")
        material = self.checkpoint(role="research_board", number=2, obj="board")
        approval = self.decision("stage", "research_imagination", outcomes=["asset:" + material["id"]])
        self.update(lambda r, d: p.set_stage(d, 2, "fixture only", "completed", approval["id"]))
        self.assertEqual(self.state()["stages"][1]["status"], "completed")

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
        commands = ("init", "status", "idea", "document", "decision", "record", "stage", "edit")
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

    def test_unsupported_sidecar_versions_do_not_call_services_or_rewrite(self):
        self.execute()
        original = self.data()
        calls = len(self.fake.calls)
        for version in (0, 1, 3, True, "2"):
            with self.subTest(version=version):
                raw = json.dumps({**original, "schema_version": version}).encode()
                self.sidecar.write_bytes(raw)
                with self.assertRaises(stage.StageError):
                    self.execute()
                self.assertEqual(self.sidecar.read_bytes(), raw)
                self.assertEqual(len(self.fake.calls), calls)

    def test_research_then_exploration_share_character_canvas_without_duplicate_groups(self):
        result = self.execute(2)
        self.assertIn("character/10_参考", result["groups"])
        self.assertIn("character/20_探索/拼贴原型", result["groups"])
        posts = self.fake.posts
        self.assertEqual(self.execute(4)["created"], [])
        self.assertEqual(self.execute(5)["created"], [])
        self.assertEqual(self.fake.posts, posts)

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
        for number, workflow, role in ((6, WORKFLOW, "character"), (4, "unknown", "character"), (4, "unsupported", "character")):
            with self.assertRaises(stage.StageError):
                self.execute(number, role, workflow)
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
