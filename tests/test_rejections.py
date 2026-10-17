"""CAD25 S1 CPU contracts; all people, PNGs and decisions are synthetic fixtures."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import struct
import zlib
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import cwb_project as project
import cwb_rejections as rejection
from cwb_workflow import WORKFLOW

def make_png(rgba=(255, 255, 255, 255)):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\x00" + bytes(rgba))) + chunk(b"IEND", b""))


PNG = make_png()


class RejectionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        project.initialize(self.root, "合成拒绝记录测试")
        (self.root / "character.kra").write_bytes(b"synthetic document, not a real KRA")
        (self.root / "workflow.json").write_bytes(b'{"3": {"class_type": "fixture", "inputs": {"seed": 42}}}\n')
        def setup(root, state):
            project.add_idea(root, state, "concept-a", "模拟创意 A", "模拟文本 A")
            project.add_idea(root, state, "concept-b", "模拟创意 B", "模拟文本 B")
            project.add_decision(root, state, "idea", "concept-a", "select", "模拟本轮", "synthetic fixture")
            project.add_document(root, state, "doc-a", "character.kra", "character", "concept-a")
        project.update(self.root, setup)
        self.assets = []
        for number in range(3):
            name = "candidate-{}.png".format(number)
            (self.root / name).write_bytes(PNG if number == 0 else make_png((number * 80, 40, 100, 255)))
            self.assets.append(self.register(name, "obj-{}".format(number), "v1"))
        self.originals = {a["path"]: (self.root / a["path"]).read_bytes() for a in self.assets}

    def state(self):
        return project.read_state(self.root)[0]

    def register(self, name, object_id, version, **extra):
        result = {}
        def op(root, state):
            result.update(project.record(root, state, name, 4, "temporary", "synthetic",
                object_id=object_id, version=version, role="character_exploration", document_id="doc-a", **extra))
        project.update(self.root, op)
        return result

    def request(self, number=0, key=None, kind="character_constraint"):
        state = self.state()
        asset = self.assets[number]
        idea = project.lookup(state["ideas"], "concept-a")
        return {"schema": rejection.INPUT, "event_key": key or "manual-{}".format(number),
            "project_id": state["project_id"], "workflow_id": WORKFLOW,
            "asset_ref": {k: asset[k] for k in ("id", "object_id", "version", "sha256")},
            "concept_ref": {k: idea[k] for k in ("id", "version")}, "document_id": "doc-a",
            "candidate": "模拟候选 {}".format(number), "generation": "generated", "prompt": "模拟 prompt\n保留换行", "seed": 42,
            "workflow": "workflow.json", "reason_taxonomy_version": rejection.TAXONOMY,
            "reason_kind": kind, "reason": "模拟原因：肩灯挡住动作", "basis": "模拟意图：动作可读",
            "reminder": "" if kind == "undecided" else "检查肩灯与手臂轮廓", "reopen_when": "姿态改变后重新比较",
            "stage_ids": ["character_exploration", "character_resolution"], "topics": ["肩灯", "动作"],
            "applies_when": "继续采用当前方案和目标版本", "decided_by": "synthetic human",
            "decision_source": "synthetic manual transcription, not authenticated"}

    def query(self, number=0):
        request = self.request(number)
        return {**{k: request[k] for k in rejection.BINDING_KEYS}, "schema": rejection.QUERY,
                "stage_id": "character_exploration", "topics": ["肩灯"], "purpose": "generate"}

    def add(self, request=None, **kwargs):
        request = request or self.request()
        name = "request-" + request["event_key"] + ".json"
        (self.root / name).write_bytes(rejection.json_bytes(request))
        return rejection.add(self.root, name, **kwargs)

    def reopen(self, ident, key="reopen-1", **kwargs):
        request = {"schema": rejection.REOPEN, "event_key": key, "project_id": self.state()["project_id"],
                   "decision_id": ident, "note": "模拟作者允许重新探索", "by": "synthetic human", "source": "synthetic instruction 2"}
        return rejection.reopen(self.root, request, **kwargs)

    def mutate_state(self, mutator):
        state = self.state()
        mutator(state)
        project.validate(state)
        (self.root / project.STATE).write_bytes(rejection.json_bytes(state))

    def assert_untouched(self):
        for name, raw in self.originals.items():
            self.assertEqual((self.root / name).read_bytes(), raw)

    def test_three_candidates_full_archive_and_history(self):
        before = self.state()
        ids = [self.add(self.request(i))["id"] for i in range(3)]
        state = self.state()
        self.assertEqual(len(state["decisions"]), len(before["decisions"]) + 3)
        self.assertEqual(state["stages"], before["stages"])
        self.assertEqual(state["selected_concept_id"], before["selected_concept_id"])
        view = rejection.inspect(self.root)
        self.assertEqual(view["total"], 3)
        self.assertEqual(view["orphan_count"], 0)
        self.assertEqual(view["invalid_event_count"], 0)
        self.assertFalse((self.root / rejection.LIBRARY / "index.json").exists())
        for i, row in enumerate(view["records"]):
            decision = row["decision"]
            self.assertEqual(decision["id"], ids[i])
            self.assertEqual(decision["action"], "reject")
            self.assertEqual(decision["execution"], "recorded_not_applied")
            self.assertEqual(row["request"]["seed"], 42)
            for name, ref in row["files"].items():
                raw = (self.root / ref["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), ref["sha256"])
                if name == "candidate.png":
                    self.assertEqual(raw, self.originals[self.assets[i]["path"]])
                if name == "workflow.json":
                    self.assertEqual(raw, (self.root / "workflow.json").read_bytes())
                if name == "prompt.txt":
                    self.assertEqual(raw, self.request(i)["prompt"].encode())
            result = rejection.context(self.root, self.query(i))
            self.assertEqual(len(result["constraints"]), 1)
            self.assertEqual(result["constraints"][0]["decision_ids"], [ids[i]])
            self.assertFalse(result["generation_request_integrated"])
        self.assert_untouched()

    def test_five_classes_and_undecided_semantics(self):
        for kind in rejection.KINDS:
            request = self.request(key="kind-" + kind, kind=kind)
            result = self.add(request)
            decision = project.lookup(self.state()["decisions"], result["id"])
            self.assertEqual(decision["action"], "defer" if kind == "undecided" else "reject")
        query = rejection.context(self.root, self.query())
        self.assertEqual(len(query["constraints"]), 3)
        reasons = {e["reason"] for e in query["skipped"]}
        self.assertEqual(reasons, {"preference_not_requested", "undecided_not_constraint"})
        self.assertEqual(len(rejection.context(self.root, self.query(), include_preferences=True)["constraints"]), 4)

    def test_unknown_judgment_cannot_have_reminder(self):
        request = self.request(kind="undecided")
        request["reminder"] = "禁止任何姿态"
        with self.assertRaises(rejection.RejectionError):
            self.add(request)
        self.assertEqual(rejection.inspect(self.root)["total"], 0)

    def test_reopen_is_explicit_defer_and_immutable_history(self):
        ident = self.add()["id"]
        old = copy.deepcopy(project.lookup(self.state()["decisions"], ident))
        archive = {p.relative_to(self.root): p.read_bytes() for p in (self.root / rejection.LIBRARY / "records" / ident).iterdir()}
        stages = copy.deepcopy(self.state()["stages"])
        reopened = self.reopen(ident)
        state = self.state()
        self.assertEqual(project.lookup(state["decisions"], ident), old)
        later = project.lookup(state["decisions"], reopened["id"])
        self.assertEqual(later["action"], "defer")
        self.assertEqual(later["outcomes"], old["outcomes"])
        self.assertEqual(later["rejection_event"]["reopens_decision_id"], ident)
        self.assertEqual(state["stages"], stages)
        self.assertFalse(rejection.context(self.root, self.query())["constraints"])
        self.assertEqual(len(rejection.inspect(self.root)["records"][0]["reopen_history"]), 1)
        for path, raw in archive.items():
            self.assertEqual((self.root / path).read_bytes(), raw)
        self.assert_untouched()

    def test_plain_defer_does_not_reopen(self):
        self.add()
        project.update(self.root, lambda root, state: project.add_decision(root, state, "asset", self.assets[0]["id"], "defer", "ordinary unrelated scope", "synthetic", "plain defer"))
        self.assertEqual(len(rejection.context(self.root, self.query())["constraints"]), 1)

    def test_undecided_cannot_be_reopened_as_rejection(self):
        ident = self.add(self.request(kind="undecided"))["id"]
        with self.assertRaises(rejection.RejectionError):
            self.reopen(ident)

    def test_same_reason_can_recur_after_reopening(self):
        ident = self.add()["id"]
        self.reopen(ident)
        newer = self.add(self.request(key="explicit-new-occurrence"))["id"]
        result = rejection.context(self.root, self.query())
        self.assertEqual(result["constraints"][0]["decision_ids"], [newer])
        self.assertEqual(rejection.inspect(self.root)["total"], 2)

    def test_idempotency_and_same_key_conflict(self):
        result = self.add()
        revision = self.state()["revision"]
        again = self.add()
        self.assertEqual(again["id"], result["id"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(self.state()["revision"], revision)
        request = self.request()
        request["reason"] += " changed"
        with self.assertRaises(rejection.RejectionError):
            self.add(request)
        self.assertEqual(self.state()["revision"], revision)
        self.assertEqual(rejection.inspect(self.root)["orphan_count"], 0)

    def test_reopen_idempotency_and_key_conflict(self):
        ident = self.add()["id"]
        first = self.reopen(ident)
        revision = self.state()["revision"]
        second = self.reopen(ident)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.state()["revision"], revision)
        with self.assertRaises(rejection.RejectionError):
            self.reopen(ident, key="manual-0")

    def test_invalid_reopen_cannot_stop_reminder(self):
        ident = self.add()["id"]
        reopened = self.reopen(ident)["id"]
        def mutate(state):
            event = project.lookup(state["decisions"], reopened)["rejection_event"]
            event["reopens_decision_id"] = "wrong-id"
        self.mutate_state(mutate)
        result = rejection.context(self.root, self.query())
        self.assertEqual(len(result["constraints"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "invalid_event_or_archive")

    def test_forged_defer_extension_without_archive_fails_closed(self):
        ident = self.add()["id"]
        def op(root, state):
            original = project.lookup(state["decisions"], ident)
            fake = project.add_decision(root, state, "asset", original["target_id"], "defer", original["scope"], "synthetic")
            fake["rejection_event"] = {"schema": rejection.EVENT, "operation": "reopen_for_exploration", "reopens_decision_id": ident}
        project.update(self.root, op)
        result = rejection.context(self.root, self.query())
        self.assertEqual(len(result["constraints"]), 1)
        self.assertEqual(result["skipped_count"], 1)

    def test_canonical_rejection_mutation_does_not_inject(self):
        ident = self.add()["id"]
        self.mutate_state(lambda state: project.lookup(state["decisions"], ident).update(reason="tampered"))
        result = rejection.context(self.root, self.query())
        self.assertFalse(result["constraints"])
        self.assertEqual(result["skipped_count"], 1)

    def test_changed_or_missing_target_blocks_reopen_but_archive_readable(self):
        for operation in ("changed", "missing"):
            with self.subTest(operation=operation):
                number = 0 if operation == "changed" else 1
                ident = self.add(self.request(number))["id"]
                target = self.root / self.assets[number]["path"]
                if operation == "changed":
                    target.write_bytes(b"new bytes")
                else:
                    target.unlink()
                state_before = (self.root / project.STATE).read_bytes()
                result = rejection.context(self.root, self.query(number))
                self.assertFalse(result["constraints"])
                self.assertIn(result["skipped"][0]["reason"], ("canonical_needs_review", "source_or_binding_stale"))
                with self.assertRaises((project.ProjectError, OSError)):
                    self.reopen(ident, key="reopen-" + operation)
                self.assertEqual((self.root / project.STATE).read_bytes(), state_before)
                row = next(r for r in rejection.inspect(self.root)["records"] if r["decision"]["id"] == ident)
                self.assertEqual((self.root / row["files"]["candidate.png"]["path"]).read_bytes(), self.originals[self.assets[number]["path"]])

    def test_current_selection_changed_suppresses_old_context(self):
        self.add()
        project.update(self.root, lambda root, state: project.add_decision(root, state, "idea", "concept-b", "select", "synthetic scope", "synthetic source"))
        result = rejection.context(self.root, self.query())
        self.assertFalse(result["constraints"])
        self.assertEqual(result["skipped"][0]["reason"], "source_or_binding_stale")

    def test_new_asset_version_never_inherits_old_reminder(self):
        self.add()
        (self.root / "v2.png").write_bytes(PNG + b"new version")
        newer = self.register("v2.png", self.assets[0]["object_id"], "v2")
        query = self.query()
        query["asset_ref"] = {k: newer[k] for k in ("id", "object_id", "version", "sha256")}
        self.assertFalse(rejection.context(self.root, query)["constraints"])
        self.assertFalse(rejection.context(self.root, self.query())["constraints"])

    def test_wrong_scope_and_foreign_project_rejected(self):
        self.add()
        for key, value in (("project_id", "foreign-project"), ("workflow_id", "legacy-eight-stage-v1"), ("document_id", "missing-document")):
            with self.subTest(key=key):
                query = self.query()
                query[key] = value
                with self.assertRaises(project.ProjectError):
                    rejection.context(self.root, query)
        for key, value in (("object_id", "another-object"), ("version", "v2"), ("sha256", "f" * 64)):
            query = self.query()
            query["asset_ref"][key] = value
            with self.assertRaises(project.ProjectError):
                rejection.context(self.root, query)
        query = self.query()
        other = project.lookup(self.state()["ideas"], "concept-b")
        query["concept_ref"] = {k: other[k] for k in ("id", "version")}
        with self.assertRaises(project.ProjectError):
            rejection.context(self.root, query)

    def test_numeric_legacy_stage_and_broad_cross_document_scope_rejected(self):
        for stages in ([6], ["turnaround"], ["legacy-6"], [], ["character_exploration"] * 2):
            request = self.request()
            request["stage_ids"] = stages
            with self.subTest(stages=stages), self.assertRaises(project.ProjectError):
                self.add(request)
        query = self.query()
        query["stage_id"] = 6
        with self.assertRaises(project.ProjectError):
            rejection.context(self.root, query)

    def test_unselected_concept_not_recorded(self):
        project.update(self.root, lambda root, state: project.add_decision(root, state, "idea", "concept-b", "select", "synthetic", "synthetic"))
        with self.assertRaises(project.ProjectError):
            self.add()

    def test_unbound_document_not_guessed_from_path(self):
        self.mutate_state(lambda state: project.lookup(state["assets"], self.assets[0]["id"]).update(document_id=None))
        with self.assertRaises(project.ProjectError):
            self.add()

    def test_basis_change_requires_review(self):
        self.add()
        project.update(self.root, lambda root, state: state.update(intent_card="changed intent"))
        result = rejection.context(self.root, self.query())
        self.assertFalse(result["constraints"])
        self.assertEqual(result["skipped"][0]["reason"], "project_basis_changed")

    def test_changed_concept_text_and_missing_document_are_stale(self):
        self.add()
        self.mutate_state(lambda state: project.lookup(state["ideas"], "concept-a").update(text="changed concept text"))
        self.assertFalse(rejection.context(self.root, self.query())["constraints"])
        (self.root / "character.kra").unlink()
        self.assertFalse(rejection.context(self.root, self.query())["constraints"])

    def test_archive_tamper_or_missing_never_injects(self):
        self.add()
        files = rejection.inspect(self.root)["records"][0]["files"]
        for name in ("candidate.png", "workflow.json", "prompt.txt", "input.json"):
            path = self.root / files[name]["path"]
            raw = path.read_bytes()
            path.write_bytes(raw + b"tamper")
            with self.subTest(name=name):
                self.assertFalse(rejection.context(self.root, self.query())["constraints"])
                with self.assertRaises(project.ProjectError):
                    self.add()
            path.write_bytes(raw)
        (self.root / files["candidate.png"]["path"]).unlink()
        self.assertFalse(rejection.context(self.root, self.query())["constraints"])

    def test_exact_topics_default_limit_and_literal_search(self):
        for i in range(12):
            request = self.request(key="reason-{}".format(i))
            request["reminder"] = "reminder {}".format(i)
            self.add(request)
        self.assertEqual(len(rejection.context(self.root, self.query())["constraints"]), 5)
        result = rejection.context(self.root, self.query(), limit=10)
        self.assertEqual(len(result["constraints"]), 10)
        self.assertEqual(result["omitted_by_limit"], 2)
        query = self.query()
        query["topics"] = ["肩"]
        self.assertFalse(rejection.context(self.root, query)["constraints"])
        view = rejection.inspect(self.root, "reminder 1", limit=2)
        self.assertEqual(view["total"], 3)
        self.assertEqual(view["next_offset"], 2)
        self.assertEqual(len(rejection.inspect(self.root, "reminder 1", limit=2, offset=2)["records"]), 1)
        self.assertEqual(rejection.inspect(self.root, ".*")["total"], 0)
        for value in (0, 11, True, 1.5):
            with self.assertRaises(project.ProjectError):
                rejection.context(self.root, self.query(), limit=value)

    def test_same_reminder_grouped_without_unbounded_source_ids(self):
        for i in range(12):
            self.add(self.request(key="occurrence-{}".format(i)))
        constraints = rejection.context(self.root, self.query())["constraints"]
        self.assertEqual(len(constraints), 1)
        self.assertEqual(constraints[0]["record_count"], 12)
        self.assertEqual(len(constraints[0]["decision_ids"]), 10)

    def test_manual_regions_preserved_byte_for_byte(self):
        prefix = b"USER prefix\r\n\xff\n"
        suffix = b"\r\nUSER suffix\xfe"
        (self.root / rejection.CHECKLIST).write_bytes(prefix)
        self.add()
        before, after, _ = rejection.checklist_parts((self.root / rejection.CHECKLIST).read_bytes())
        self.assertEqual(before, prefix + b"\n\n")
        with (self.root / rejection.CHECKLIST).open("ab") as f:
            f.write(suffix)
        self.add(self.request(1))
        before2, after2, _ = rejection.checklist_parts((self.root / rejection.CHECKLIST).read_bytes())
        self.assertEqual(before2, before)
        self.assertEqual(after2, after + suffix)

    def test_manual_auto_region_edits_are_not_overwritten(self):
        self.add()
        path = self.root / rejection.CHECKLIST
        raw = path.read_bytes().replace("自动历史摘要".encode(), b"HAND EDITED")
        path.write_bytes(raw)
        result = self.add(self.request(1))
        self.assertTrue(result["canonical_saved"])
        self.assertFalse(result["checklist_synced"])
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(rejection.inspect(self.root)["total"], 2)
        with self.assertRaises(project.ProjectError):
            rejection.sync(self.root)

    def test_duplicate_markers_are_preserved_with_warning(self):
        self.add()
        path = self.root / rejection.CHECKLIST
        raw = path.read_bytes() + rejection.END
        path.write_bytes(raw)
        result = self.add(self.request(1))
        self.assertFalse(result["checklist_synced"])
        self.assertEqual(path.read_bytes(), raw)

    def test_revision_conflict_no_new_archive_or_state_change(self):
        original = (self.root / project.STATE).read_bytes()
        with self.assertRaises(project.ProjectError):
            self.add(expected=self.state()["revision"] - 1)
        self.assertEqual((self.root / project.STATE).read_bytes(), original)
        self.assertFalse((self.root / rejection.LIBRARY).exists())

    def test_lock_collision_fails_without_writes(self):
        with project.locked(self.root):
            with self.assertRaises(project.ProjectError):
                self.add()
        self.assertFalse((self.root / rejection.LIBRARY).exists())

    def test_state_write_failure_keeps_orphan_no_canonical_rejection(self):
        original = (self.root / project.STATE).read_bytes()
        with patch.object(project, "atomic_write", side_effect=OSError("injected state publish failure")):
            with self.assertRaisesRegex(project.ProjectError, "canonical_saved=False"):
                self.add()
        self.assertEqual((self.root / project.STATE).read_bytes(), original)
        view = rejection.inspect(self.root)
        self.assertEqual(view["total"], 0)
        self.assertEqual(view["orphan_count"], 1)
        self.assert_untouched()
        result = self.add()
        self.assertTrue(result["canonical_saved"])
        self.assertEqual(rejection.inspect(self.root)["orphan_count"], 1)

    def test_state_fsync_failure_reports_actual_saved_state(self):
        actual = project.atomic_write
        def failure(*args):
            actual(*args)
            raise project.ProjectError("injected after replacement")
        with patch.object(project, "atomic_write", side_effect=failure):
            with self.assertRaisesRegex(project.ProjectError, "canonical_saved=True"):
                self.add()
        self.assertEqual(rejection.inspect(self.root)["total"], 1)
        self.assertTrue(self.add()["duplicate"])

    def test_archive_write_failure_no_state_mutation(self):
        original = (self.root / project.STATE).read_bytes()
        actual = rejection.write_new
        def failure(path, raw):
            if path.name == "prompt.txt":
                raise OSError("injected archive write failure")
            actual(path, raw)
        with patch.object(rejection, "write_new", side_effect=failure):
            with self.assertRaisesRegex(project.ProjectError, "canonical_saved=False"):
                self.add()
        self.assertEqual((self.root / project.STATE).read_bytes(), original)
        self.assertEqual(rejection.inspect(self.root)["orphan_count"], 1)
        self.assert_untouched()

    def test_archive_directory_collision_never_overwrites(self):
        ident = "fixed-collision"
        directory = self.root / rejection.LIBRARY / "records" / ident
        directory.mkdir(parents=True)
        (directory / "manual.txt").write_bytes(b"preserve")
        actual = project.add_decision
        def collide(*args, **kwargs):
            result = actual(*args, **kwargs)
            result["id"] = ident
            return result
        with patch.object(project, "add_decision", side_effect=collide):
            with self.assertRaises(project.ProjectError):
                self.add()
        self.assertEqual((directory / "manual.txt").read_bytes(), b"preserve")
        self.assertEqual(list(directory.iterdir()), [directory / "manual.txt"])

    def test_changed_input_after_archive_is_orphan_not_canonical(self):
        original = (self.root / project.STATE).read_bytes()
        actual = rejection.publish_archive
        def mutate(*args, **kwargs):
            result = actual(*args, **kwargs)
            (self.root / "request-manual-0.json").write_text("{}")
            return result
        with patch.object(rejection, "publish_archive", side_effect=mutate):
            with self.assertRaises(project.ProjectError):
                self.add()
        self.assertEqual((self.root / project.STATE).read_bytes(), original)
        self.assertEqual(rejection.inspect(self.root)["orphan_count"], 1)

    def test_changed_image_after_archive_is_orphan_not_canonical(self):
        original = (self.root / project.STATE).read_bytes()
        actual = rejection.publish_archive
        def mutate(*args, **kwargs):
            result = actual(*args, **kwargs)
            (self.root / self.assets[0]["path"]).write_bytes(b"changed externally")
            return result
        with patch.object(rejection, "publish_archive", side_effect=mutate):
            with self.assertRaises(project.ProjectError):
                self.add()
        self.assertEqual((self.root / project.STATE).read_bytes(), original)
        self.assertEqual(rejection.inspect(self.root)["orphan_count"], 1)

    def test_project_cas_prevents_overwriting_manual_edit(self):
        actual = rejection.publish_archive
        def mutate(*args, **kwargs):
            result = actual(*args, **kwargs)
            self.mutate_state(lambda state: state.update(name="external manual edit"))
            return result
        with patch.object(rejection, "publish_archive", side_effect=mutate):
            with self.assertRaises(project.ProjectError):
                self.add()
        self.assertEqual(self.state()["name"], "external manual edit")
        self.assertEqual(rejection.inspect(self.root)["total"], 0)

    def test_checklist_publish_failure_is_explicit_partial_success(self):
        with patch.object(rejection, "atomic_file", side_effect=OSError("checklist failure")):
            result = self.add()
        self.assertTrue(result["canonical_saved"])
        self.assertFalse(result["checklist_synced"])
        self.assertEqual(rejection.inspect(self.root)["total"], 1)
        self.assertEqual(rejection.inspect(self.root)["checklist"], "missing")
        rejection.sync(self.root)
        self.assertEqual(rejection.inspect(self.root)["checklist"], "current")

    def test_checklist_cas_preserves_external_edit(self):
        self.add()
        actual = rejection.atomic_file
        path = self.root / rejection.CHECKLIST
        changed = b"external manual text"
        def race(root, relative, payload, previous):
            path.write_bytes(changed)
            return actual(root, relative, payload, previous)
        with patch.object(rejection, "atomic_file", side_effect=race):
            result = self.add(self.request(1))
        self.assertFalse(result["checklist_synced"])
        self.assertEqual(path.read_bytes(), changed)

    def test_symlink_candidate_workflow_archive_and_checklist_rejected(self):
        image = self.root / self.assets[0]["path"]
        image.unlink()
        image.symlink_to(self.root / self.assets[1]["path"])
        with self.assertRaises((OSError, project.ProjectError)):
            self.add()
        image.unlink()
        image.write_bytes(PNG)
        wf = self.root / "workflow.json"
        wf.unlink()
        wf.symlink_to(self.root / "character.kra")
        with self.assertRaises((OSError, project.ProjectError)):
            self.add()
        wf.unlink()
        wf.write_text("{}")
        (self.root / rejection.LIBRARY).symlink_to(self.root / "outside", target_is_directory=True)
        with self.assertRaises(project.ProjectError):
            self.add()
        (self.root / rejection.LIBRARY).unlink()
        (self.root / "manual-note.txt").write_bytes(b"manual note")
        (self.root / rejection.CHECKLIST).symlink_to(self.root / "manual-note.txt")
        result = self.add()
        self.assertFalse(result["checklist_synced"])
        self.assertEqual(image.read_bytes(), PNG)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_fifo_inputs_never_block(self):
        wf = self.root / "workflow.json"
        wf.unlink()
        os.mkfifo(wf)
        with self.assertRaises(project.ProjectError):
            self.add()
        wf.unlink()
        wf.write_text("{}")
        image = self.root / self.assets[0]["path"]
        image.unlink()
        os.mkfifo(image)
        with self.assertRaises(project.ProjectError):
            self.add()

    def test_hardlink_control_input_and_checklist_not_overwritten(self):
        image = self.root / self.assets[0]["path"]
        image.unlink()
        os.link(self.root / project.STATE, image)
        with self.assertRaises(project.ProjectError):
            self.add()
        image.unlink()
        image.write_bytes(PNG)
        original = b"manual checklist"
        (self.root / rejection.CHECKLIST).write_bytes(original)
        os.link(self.root / rejection.CHECKLIST, self.root / "alias.txt")
        result = self.add()
        self.assertFalse(result["checklist_synced"])
        self.assertEqual((self.root / rejection.CHECKLIST).read_bytes(), original)
        self.assertEqual((self.root / "alias.txt").read_bytes(), original)

    def test_invalid_schema_unknown_fields_seed_and_duplicate_json_keys(self):
        for key, value in (("schema", "v0"), ("reason_taxonomy_version", "v0"), ("reason_kind", "locked_constraint"), ("seed", True), ("seed", 2**64)):
            request = self.request()
            request[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(project.ProjectError):
                self.add(request)
        request = self.request()
        request["unexpected"] = True
        with self.assertRaises(project.ProjectError):
            self.add(request)
        (self.root / "duplicate.json").write_text('{"schema": 1, "schema": 2}')
        with self.assertRaises(project.ProjectError):
            rejection.add(self.root, "duplicate.json")

    def test_manual_generation_does_not_forge_parameters(self):
        request = self.request()
        request.update(generation="manual", prompt="", seed=None, workflow=None)
        self.add(request)
        row = rejection.inspect(self.root)["records"][0]
        self.assertNotIn("workflow.json", row["files"])
        self.assertIsNone(row["request"]["seed"])

    def test_legacy_independent_index_never_imported_or_overwritten(self):
        library = self.root / rejection.LIBRARY
        library.mkdir()
        raw = b'{"schema_version":1,"records":[{"status":"active"}]}'
        (library / "index.json").write_bytes(raw)
        original = (self.root / project.STATE).read_bytes()
        for op in (lambda: self.add(), lambda: rejection.sync(self.root), lambda: rejection.context(self.root, self.query())):
            with self.assertRaisesRegex(project.ProjectError, "旧独立 index"):
                op()
        self.assertEqual((library / "index.json").read_bytes(), raw)
        self.assertEqual((self.root / project.STATE).read_bytes(), original)

    def test_list_context_are_read_only_and_ordinary_cli_still_reads(self):
        self.add()
        files = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        rejection.inspect(self.root)
        rejection.context(self.root, self.query())
        self.assertEqual(files, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        project.inspect(self.root)
        project.validate(self.state())

    def test_cli_new_process_reopen_and_zero_io_help(self):
        cli = Path(rejection.__file__)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        help_result = subprocess.run([sys.executable, "-B", str(cli), "--help"], cwd=self.root, capture_output=True, text=True, env=env)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertFalse((self.root / rejection.LIBRARY).exists())
        ident = self.add()["id"]
        request = {"schema": rejection.REOPEN, "event_key": "subprocess-reopen", "project_id": self.state()["project_id"], "decision_id": ident, "note": "synthetic reopen", "by": "synthetic human", "source": "synthetic manual instruction"}
        (self.root / "reopen.json").write_bytes(rejection.json_bytes(request))
        result = subprocess.run([sys.executable, "-B", str(cli), "reopen", str(self.root), "reopen.json"], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(rejection.context(self.root, self.query())["constraints"])

    def test_history_pagination_and_recent_list_summary(self):
        ident = self.add()["id"]
        for n in range(12):
            self.reopen(ident, key="history-reopen-{}".format(n))
        row = rejection.inspect(self.root)["records"][0]
        self.assertEqual(len(row["reopen_history"]), 10)
        self.assertEqual(row["reopen_history_count"], 12)
        first = rejection.history(self.root, ident, limit=5)
        self.assertEqual(first["total"], 13)
        self.assertEqual(first["events"][0]["action"], "reject")
        self.assertEqual(first["next_offset"], 5)
        last = rejection.history(self.root, ident, limit=5, offset=10)
        self.assertEqual(len(last["events"]), 3)
        self.assertIsNone(last["next_offset"])

    def test_duplicate_with_changed_workflow_is_not_same_input(self):
        self.add()
        before = (self.root / project.STATE).read_bytes()
        (self.root / "workflow.json").write_bytes(b'{"new": true}')
        with self.assertRaisesRegex(project.ProjectError, "workflow"):
            self.add()
        self.assertEqual((self.root / project.STATE).read_bytes(), before)

    def test_registered_checklist_asset_is_never_replaced(self):
        raw = b"user registered source, preserve"
        (self.root / rejection.CHECKLIST).write_bytes(raw)
        self.register(rejection.CHECKLIST, "manual-source", "v1")
        result = self.add()
        self.assertTrue(result["canonical_saved"])
        self.assertFalse(result["checklist_synced"])
        self.assertEqual((self.root / rejection.CHECKLIST).read_bytes(), raw)

    def test_changed_workflow_after_archive_never_commits(self):
        before = (self.root / project.STATE).read_bytes()
        actual = rejection.publish_archive
        def mutate(*args, **kwargs):
            result = actual(*args, **kwargs)
            (self.root / "workflow.json").write_bytes(b'{"changed": true}')
            return result
        with patch.object(rejection, "publish_archive", side_effect=mutate):
            with self.assertRaises(project.ProjectError):
                self.add()
        self.assertEqual((self.root / project.STATE).read_bytes(), before)
        self.assertEqual(rejection.inspect(self.root)["orphan_count"], 1)

    def test_hardlink_archive_alias_rejected_even_with_same_hash(self):
        self.add()
        ref = rejection.inspect(self.root)["records"][0]["files"]["candidate.png"]
        archive = self.root / ref["path"]
        archive.unlink()
        os.link(self.root / self.assets[0]["path"], archive)
        self.assertFalse(rejection.context(self.root, self.query())["constraints"])

    def test_read_view_race_does_not_return_mixed_revision(self):
        self.add()
        actual = rejection.validated_events
        def mutate(*args):
            result = actual(*args)
            self.mutate_state(lambda state: state.update(name="concurrent state"))
            return result
        with patch.object(rejection, "validated_events", side_effect=mutate):
            with self.assertRaisesRegex(project.ProjectError, "查询期间项目已变"):
                rejection.context(self.root, self.query())

    def test_invalid_expected_revision_type_never_writes(self):
        for value in (True, -1, "5", 1.2):
            with self.subTest(value=value), self.assertRaises(project.ProjectError):
                self.add(expected=value)
        self.assertFalse((self.root / rejection.LIBRARY).exists())

    def test_managed_archive_directory_fsync_failure_never_commits(self):
        before = (self.root / project.STATE).read_bytes()
        actual = rejection.fsync_dir
        def failure(path):
            if path.parent.name == "records":
                raise OSError("injected archive durability failure")
            actual(path)
        with patch.object(rejection, "fsync_dir", side_effect=failure):
            with self.assertRaisesRegex(project.ProjectError, "canonical_saved=False"):
                self.add()
        self.assertEqual((self.root / project.STATE).read_bytes(), before)
        self.assertEqual(rejection.inspect(self.root)["orphan_count"], 1)

    def test_schema1_rejected_without_implicit_upgrade(self):
        from test_workflow_project import legacy_state
        old = legacy_state()
        raw = rejection.json_bytes(old)
        (self.root / project.STATE).write_bytes(raw)
        with self.assertRaisesRegex(project.ProjectError, "只读"):
            rejection.sync(self.root)
        with self.assertRaisesRegex(project.ProjectError, "只读"):
            rejection.add(self.root, "unused.json")
        self.assertEqual((self.root / project.STATE).read_bytes(), raw)
        self.assertFalse((self.root / rejection.LIBRARY).exists())

    def test_duplicate_canonical_event_key_fails_closed_for_both(self):
        ident = self.add()["id"]
        def duplicate(state):
            old = project.lookup(state["decisions"], ident)
            clone = copy.deepcopy(old)
            clone["id"] = "duplicate-canonical-key"
            state["decisions"].append(clone)
        self.mutate_state(duplicate)
        result = rejection.context(self.root, self.query())
        self.assertFalse(result["constraints"])
        self.assertEqual(result["skipped_count"], 2)
        with self.assertRaises(project.ProjectError):
            self.add()

    def test_empty_query_never_guesses_identity(self):
        for key in rejection.BINDING_KEYS:
            query = self.query()
            del query[key]
            with self.subTest(key=key), self.assertRaises(project.ProjectError):
                rejection.context(self.root, query)

    def test_reopen_file_change_after_archive_never_commits(self):
        ident = self.add()["id"]
        request = {"schema": rejection.REOPEN, "event_key": "file-reopen", "project_id": self.state()["project_id"], "decision_id": ident, "note": "synthetic reopen", "by": "synthetic human", "source": "synthetic source"}
        (self.root / "reopen.json").write_bytes(rejection.json_bytes(request))
        before = (self.root / project.STATE).read_bytes()
        actual = rejection.publish_archive
        def mutate(*args, **kwargs):
            result = actual(*args, **kwargs)
            (self.root / "reopen.json").write_text("{}")
            return result
        with patch.object(rejection, "publish_archive", side_effect=mutate):
            with self.assertRaisesRegex(project.ProjectError, "重开请求已变"):
                rejection.reopen(self.root, "reopen.json")
        self.assertEqual((self.root / project.STATE).read_bytes(), before)
        self.assertEqual(rejection.inspect(self.root)["orphan_count"], 1)
        self.assertEqual(len(rejection.context(self.root, self.query())["constraints"]), 1)

    def test_three_png_fixtures_have_valid_chunks_and_distinct_pixels(self):
        self.assertEqual(len(set(self.originals.values())), 3)
        for raw in self.originals.values():
            self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
            offset = 8
            while offset < len(raw):
                size = struct.unpack(">I", raw[offset:offset + 4])[0]
                kind = raw[offset + 4:offset + 8]
                data = raw[offset + 8:offset + 8 + size]
                crc = struct.unpack(">I", raw[offset + 8 + size:offset + 12 + size])[0]
                self.assertEqual(zlib.crc32(kind + data), crc)
                if kind == b"IHDR":
                    self.assertEqual(struct.unpack(">IIBBBBB", data), (1, 1, 8, 6, 0, 0, 0))
                if kind == b"IDAT":
                    self.assertEqual(len(zlib.decompress(data)), 5)
                offset += size + 12
            self.assertEqual(offset, len(raw))

    def duplicate_reopen_stale_target(self, missing):
        ident = self.add()["id"]
        reopened = self.reopen(ident)["id"]
        path = self.root / self.assets[0]["path"]
        original_image = path.read_bytes()
        if missing:
            path.unlink()
        else:
            path.write_bytes(b"changed target after successful reopen")
        before = (self.root / project.STATE).read_bytes()
        state_before = self.state()
        checklist_before = (self.root / rejection.CHECKLIST).read_bytes()
        request = {"schema": rejection.REOPEN, "event_key": "reopen-1", "project_id": state_before["project_id"],
                   "decision_id": ident, "note": "模拟作者允许重新探索", "by": "synthetic human", "source": "synthetic instruction 2"}
        (self.root / "duplicate-reopen.json").write_bytes(rejection.json_bytes(request))
        result = subprocess.run([sys.executable, "-B", rejection.__file__, "reopen", str(self.root), "duplicate-reopen.json"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual((self.root / project.STATE).read_bytes(), before)
        self.assertEqual((self.root / rejection.CHECKLIST).read_bytes(), checklist_before)
        self.assertEqual(self.state()["revision"], state_before["revision"])
        self.assertEqual(self.state()["decisions"], state_before["decisions"])
        history = rejection.history(self.root, ident)
        self.assertEqual([d["id"] for d in history["events"]], [ident, reopened])
        self.assertEqual(rejection.inspect(self.root)["total"], 1)
        self.assertEqual((self.root / project.STATE).read_bytes(), before)
        # Restoring the exact declared source permits the same legitimate retry.
        path.write_bytes(original_image)
        duplicate = self.reopen(ident)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["id"], reopened)
        self.assertEqual((self.root / project.STATE).read_bytes(), before)

    def test_duplicate_reopen_changed_target_fails_without_refresh_commit(self):
        self.duplicate_reopen_stale_target(False)

    def test_duplicate_reopen_missing_target_fails_without_refresh_commit(self):
        self.duplicate_reopen_stale_target(True)

    def source_collision(self, kind, fragment=False):
        raw = PNG if kind == "preview" else b"Original canonical concept source\r\nPreserve exact bytes.\n"
        path = self.root / rejection.CHECKLIST
        path.write_bytes(raw)
        request = self.request()
        if kind in ("selected-idea", "other-idea"):
            ref = rejection.CHECKLIST + ("#specific-section" if fragment else "")
            def add_idea(root, state):
                project.add_idea(root, state, "linked-idea", "linked source", "", text_ref=ref)
                if kind == "selected-idea":
                    project.add_decision(root, state, "idea", "linked-idea", "select", "synthetic scope", "synthetic source")
                    project.add_document(root, state, "linked-document", "character.kra", "character", "linked-idea")
            project.update(self.root, add_idea)
            if kind == "selected-idea":
                result = {}
                def add_asset(root, state):
                    result.update(project.record(root, state, self.assets[0]["path"], 4, "temporary", "synthetic linked source",
                        object_id="linked-object", version="v1", role="character_exploration", document_id="linked-document"))
                project.update(self.root, add_asset)
                idea = project.lookup(self.state()["ideas"], "linked-idea")
                request.update(document_id="linked-document", concept_ref={k: idea[k] for k in ("id", "version")},
                               asset_ref={k: result[k] for k in ("id", "object_id", "version", "sha256")})
        else:
            self.assets[0] = self.register(self.assets[0]["path"], "preview-object", "v1", preview=rejection.CHECKLIST)
            request = self.request()
        (self.root / "collision-record.json").write_bytes(rejection.json_bytes(request))
        before = self.state()
        result = subprocess.run([sys.executable, "-B", rejection.__file__, "add", str(self.root), "collision-record.json"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        outcome = json.loads(result.stdout)
        self.assertTrue(outcome["canonical_saved"])
        self.assertFalse(outcome["checklist_synced"])
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.state()["revision"], before["revision"] + 1)
        self.assertEqual(self.state()["decisions"][:-1], before["decisions"])
        effective = project.inspect(self.root)
        self.assertTrue(all(d["validity"] == "valid" for d in effective["state"]["decisions"]))
        if kind in ("selected-idea", "other-idea"):
            source_check = next(c for c in effective["reference_checks"] if c.get("idea_id") == "linked-idea")
            self.assertEqual(source_check["status"], "ok")
        else:
            project.verify_outcome(self.root, self.state(), project.outcome_ref(self.state(), "asset:" + self.assets[0]["id"]))
        query = {**{k: request[k] for k in rejection.BINDING_KEYS}, "schema": rejection.QUERY,
                 "stage_id": "character_exploration", "topics": ["肩灯"], "purpose": "review"}
        self.assertEqual(len(rejection.context(self.root, query)["constraints"]), 1)
        before_sync = (self.root / project.STATE).read_bytes()
        with self.assertRaises(project.ProjectError):
            rejection.sync(self.root)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual((self.root / project.STATE).read_bytes(), before_sync)
        self.assertEqual(rejection.inspect(self.root)["total"], 1)

    def test_checklist_selected_concept_source_is_preserved(self):
        self.source_collision("selected-idea")

    def test_checklist_unselected_fragment_concept_source_is_preserved(self):
        self.source_collision("other-idea", fragment=True)

    def test_checklist_registered_preview_source_is_preserved(self):
        self.source_collision("preview")

    def test_inline_path_like_text_does_not_block_ordinary_checklist_update(self):
        project.update(self.root, lambda root, state: state.update(intent_card=rejection.CHECKLIST,
            design_pillars=[rejection.CHECKLIST], style_anchors=[rejection.CHECKLIST + "#not-a-file-ref"]))
        prefix = b"Unregistered manual note\r\n"
        path = self.root / rejection.CHECKLIST
        path.write_bytes(prefix)
        first = self.add()
        self.assertTrue(first["checklist_synced"])
        self.assertTrue(path.read_bytes().startswith(prefix))
        before, after, _ = rejection.checklist_parts(path.read_bytes())
        second = self.add(self.request(1))
        self.assertTrue(second["checklist_synced"])
        new_before, new_after, _ = rejection.checklist_parts(path.read_bytes())
        self.assertEqual((new_before, new_after), (before, after))
        self.assertEqual(rejection.inspect(self.root)["checklist"], "current")

    def test_new_source_fields_keep_alias_and_symlink_protection(self):
        for link_type in ("hardlink", "symlink"):
            with self.subTest(link_type=link_type):
                path = self.root / rejection.CHECKLIST
                path.write_bytes(b"canonical source bytes")
                source = self.root / (link_type + "-source.txt")
                os.link(path, source)
                project.update(self.root, lambda root, state: project.add_idea(root, state,
                    "idea-" + link_type, "synthetic source", "", text_ref=source.name + "#part"))
                if link_type == "symlink":
                    source.unlink()
                    source.symlink_to(path)
                raw = path.read_bytes()
                with self.assertRaises(project.ProjectError):
                    rejection.sync(self.root)
                self.assertEqual(path.read_bytes(), raw)
                source.unlink()


if __name__ == "__main__":
    unittest.main()
