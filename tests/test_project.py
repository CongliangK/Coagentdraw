"""项目状态的离线回归；所有作品和状态只在临时目录内创建。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import cwb_project as project

CLI = Path(__file__).resolve().parents[1] / "core" / "cwb.py"


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "项目 空格"
        self.initial = project.initialize(self.root, "角色")

    def cli(self, *args, ok=True, cwd=None):
        result = subprocess.run([sys.executable, str(CLI), "project", *map(str, args)],
                                cwd=cwd, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stderr)
        return json.loads(result.stdout) if ok else result.stderr

    def state(self):
        return project.read_state(self.root)[0]

    def asset(self, path="stages/01-意图/作品.kra", content=b"original editable art"):
        target = self.root / path
        target.write_bytes(content)
        return path

    def test_restart_relative_paths_unicode_and_metadata(self):
        self.cli("edit", self.root.name, "--intent", "完整手工意图", "--pillar", "仪式", "--pillar", "克制", "--anchor", "水彩", cwd=self.root.parent)
        self.cli("stage", self.root, 2, "--note", "开始调研")
        path = self.asset()
        self.cli("record", self.root, path, "--category", "final", "--stage", 1)
        status = self.cli("status", self.root)
        state = status["state"]
        self.assertEqual(state["project_id"], self.initial["project_id"])
        self.assertEqual(state["current_stage"], 2)
        self.assertEqual(state["intent_card"], "完整手工意图")
        self.assertEqual(state["design_pillars"], ["仪式", "克制"])
        self.assertEqual(state["style_anchors"], ["水彩"])
        self.assertEqual(state["assets"][0]["path"], path)
        self.assertEqual(state["assets"][0]["category"], "final")
        self.assertEqual(status["asset_checks"][0]["status"], "ok")
        self.assertEqual((self.root / path).read_bytes(), b"original editable art")
        self.assertTrue(all((self.root / project.stage_dir(i)).is_dir() for i in range(1, 9)))

    def test_repeated_initialization_and_calls_are_noops(self):
        path = self.asset()
        self.cli("record", self.root, path, "--category", "temporary")
        self.cli("stage", self.root, 4)
        before = (self.root / project.STATE).read_bytes()
        self.cli("init", "角色", "--root", self.root)
        self.cli("stage", self.root, 4)
        self.cli("record", self.root, path, "--category", "temporary", "--stage", 1)
        self.assertEqual(before, (self.root / project.STATE).read_bytes())
        self.cli("init", "其他名称", "--root", self.root, ok=False)
        self.assertEqual(before, (self.root / project.STATE).read_bytes())

    def test_explicit_completion_does_not_invent_acceptance(self):
        self.cli("stage", self.root, 1, "--status", "completed")
        self.cli("stage", self.root, 3)
        state = self.state()
        self.assertEqual(state["stages"][0]["status"], "completed")
        self.assertEqual(state["stages"][1]["status"], "pending")
        self.cli("stage", self.root, 1)
        self.assertEqual(self.state()["stage_history"][-1]["from"], 3)

    def test_changed_missing_assets_reported_without_rewriting(self):
        path = self.asset()
        self.cli("record", self.root, path, "--category", "temporary")
        before = (self.root / project.STATE).read_bytes()
        (self.root / path).write_bytes(b"new user edit")
        self.assertEqual(self.cli("status", self.root)["asset_checks"][0]["status"], "changed")
        (self.root / path).unlink()
        self.assertEqual(self.cli("status", self.root)["asset_checks"][0]["status"], "missing")
        self.assertEqual(before, (self.root / project.STATE).read_bytes())

    def test_new_version_retains_old_hash_and_identity(self):
        path = self.asset()
        self.cli("record", self.root, path, "--category", "temporary")
        old = self.state()["assets"][0]
        (self.root / path).write_bytes(b"revised by user")
        self.cli("record", self.root, path, "--category", "final")
        assets = self.state()["assets"]
        self.assertEqual(assets[0], old)
        self.assertNotEqual(assets[1]["id"], old["id"])
        self.assertNotEqual(assets[1]["sha256"], old["sha256"])

    def test_bad_paths_and_symlinks_do_not_write(self):
        outside = self.root.parent / "outside.kra"
        outside.write_bytes(b"never change")
        (self.root / "link.kra").symlink_to(outside)
        (self.root / "link-dir").symlink_to(self.root / "stages", target_is_directory=True)
        before = (self.root / project.STATE).read_bytes()
        for path in ("../outside.kra", str(outside), "C:/outside.kra", "a\\b", "stages/../outside.kra", "./outside.kra", "missing.kra", "link.kra", "link-dir/01-意图/a", "project_state.json", ".project_state.lock", "stages"):
            with self.subTest(path=path):
                self.cli("record", self.root, path, "--category", "final", ok=False)
        self.assertEqual(before, (self.root / project.STATE).read_bytes())
        self.assertEqual(outside.read_bytes(), b"never change")

    def test_corrupt_json_and_invalid_schema_never_reset(self):
        state_path = self.root / project.STATE
        for raw in (b"{broken", b"\xff", b"[]", b"{}", b'{"schema_version": 99}'):
            state_path.write_bytes(raw)
            self.cli("status", self.root, ok=False)
            self.cli("init", "角色", "--root", self.root, ok=False)
            self.cli("stage", self.root, 2, ok=False)
            self.assertEqual(state_path.read_bytes(), raw)

    def test_invalid_nested_schema(self):
        for field, value in (("stages", [None] * 8), ("stage_history", [{}]), ("assets", [{}]), ("revision", True), ("schema_version", True)):
            bad = copy.deepcopy(self.initial)
            bad[field] = value
            with self.assertRaises(project.ProjectError):
                project.validate(bad)

    def test_stale_revision_conflict_and_repeated_edit(self):
        self.cli("edit", self.root, "--intent", "first", "--expect-revision", 0)
        self.assertIn("修订冲突", self.cli("edit", self.root, "--intent", "stale", "--expect-revision", 0, ok=False))
        self.cli("edit", self.root, "--intent", "first", "--expect-revision", 1)
        self.assertEqual(self.state()["revision"], 1)
        self.assertEqual(self.state()["intent_card"], "first")

    def test_live_lock_conflict_then_release(self):
        with project.locked(self.root):
            self.assertIn("另一进程", self.cli("stage", self.root, 2, ok=False))
        self.cli("stage", self.root, 2)
        self.assertEqual(self.state()["current_stage"], 2)

    def test_two_simultaneous_stale_writers_exactly_one_wins(self):
        command = [sys.executable, str(CLI), "project", "edit", str(self.root), "--expect-revision", "0", "--intent"]
        children = [subprocess.Popen(command + [word], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for word in ("甲", "乙")]
        for child in children:
            child.communicate(timeout=10)
        self.assertEqual(sorted(child.returncode for child in children), [0, 1])
        self.assertEqual(self.state()["revision"], 1)

    def test_atomic_replace_failure_and_keyboard_interrupt_leave_old_state(self):
        before = (self.root / project.STATE).read_bytes()
        for failure in (OSError("disk failure"), KeyboardInterrupt()):
            with mock.patch.object(project.os, "replace", side_effect=failure):
                with self.assertRaises(type(failure)):
                    project.update(self.root, lambda root, data: data.update(intent_card="new"))
            self.assertEqual(before, (self.root / project.STATE).read_bytes())
            self.assertEqual(list(self.root.glob(".project-state-*.tmp")), [])
        self.cli("edit", self.root, "--intent", "recovered")

    def test_fsync_failure_before_replace_preserves_state(self):
        before = (self.root / project.STATE).read_bytes()
        with mock.patch.object(project.os, "fsync", side_effect=OSError("full disk")):
            with self.assertRaises(OSError):
                project.update(self.root, lambda root, data: data.update(intent_card="new"))
        self.assertEqual(before, (self.root / project.STATE).read_bytes())

    def test_noncooperating_edit_detected(self):
        external = copy.deepcopy(self.initial)
        external["intent_card"] = "manual editor"
        def edit(root, data):
            data["intent_card"] = "stale writer"
            (root / project.STATE).write_text(json.dumps(external), encoding="utf-8")
        with self.assertRaisesRegex(project.ProjectError, "已改变"):
            project.update(self.root, edit)
        self.assertEqual(self.state()["intent_card"], "manual editor")

    def test_interrupted_initialization_resumes_without_overwriting_assets(self):
        root = self.root.parent / "new"
        with mock.patch.object(project.os, "link", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                project.initialize(root, "new")
        asset = root / project.stage_dir(1) / "existing.kra"
        asset.write_bytes(b"untouched")
        project.initialize(root, "new")
        self.assertEqual(asset.read_bytes(), b"untouched")

    def test_state_and_lock_symlinks_and_fifo_rejected(self):
        path = self.root / project.STATE
        path.unlink()
        outside = self.root.parent / "state.json"
        outside.write_text(json.dumps(self.initial))
        path.symlink_to(outside)
        self.cli("stage", self.root, 2, ok=False)
        path.unlink()
        os.mkfifo(path)
        self.cli("status", self.root, ok=False)
        path.unlink()
        path.write_text(json.dumps(self.initial))
        lock = self.root / project.LOCK
        lock.unlink()
        lock.symlink_to(outside)
        self.cli("stage", self.root, 2, ok=False)
        self.assertEqual(json.loads(outside.read_text()), self.initial)

    def test_help_has_no_side_effects(self):
        empty = self.root.parent / "empty"
        empty.mkdir()
        for args in (("--help",), ("project", "--help"), ("project", "init", "--help")):
            result = subprocess.run([sys.executable, str(CLI), *args], cwd=empty, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0)
        self.assertEqual(list(empty.iterdir()), [])

    def test_killed_writer_leaves_old_json_and_releases_lock(self):
        before = (self.root / project.STATE).read_bytes()
        marker = self.root / "writer-ready"
        script = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import cwb_project as p
root = Path(sys.argv[2])
def pause(*args):
    (root / 'writer-ready').write_text('ready')
    time.sleep(30)
p.os.replace = pause
p.update(root, lambda root, data: data.update(intent_card='interrupted'))
"""
        child = subprocess.Popen([sys.executable, "-c", script, str(CLI.parent), str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists())
            child.kill()
            child.communicate(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
        self.assertEqual(before, (self.root / project.STATE).read_bytes())
        self.assertEqual(len(list(self.root.glob(".project-state-*.tmp"))), 1)
        self.cli("edit", self.root, "--intent", "resumed")
        self.assertEqual(self.state()["intent_card"], "resumed")

    def test_project_move_preserves_relative_asset_identity(self):
        path = self.asset()
        self.cli("record", self.root, path, "--category", "final")
        old = self.state()
        moved = self.root.parent / "moved"
        self.root.rename(moved)
        result = self.cli("status", moved)
        self.assertEqual(result["state"], old)
        self.assertEqual(result["asset_checks"][0]["status"], "ok")

    def test_initial_create_race_cannot_overwrite_external_state(self):
        root = self.root.parent / "new-race"
        real_link = os.link
        def raced_link(source, destination):
            Path(destination).write_bytes(b"external state")
            return real_link(source, destination)
        with mock.patch.object(project.os, "link", side_effect=raced_link):
            with self.assertRaises(FileExistsError):
                project.initialize(root, "race")
        self.assertEqual((root / project.STATE).read_bytes(), b"external state")

    def test_directory_fsync_failure_discloses_committed_state(self):
        actual = os.fsync
        calls = []
        def fail_second(fd):
            calls.append(fd)
            if len(calls) == 2:
                raise OSError("directory failure")
            return actual(fd)
        with mock.patch.object(project.os, "fsync", side_effect=fail_second):
            with self.assertRaisesRegex(project.ProjectError, "状态已替换"):
                project.update(self.root, lambda root, data: data.update(intent_card="committed"))
        self.assertEqual(self.state()["intent_card"], "committed")

    def test_clear_lists_and_empty_intent(self):
        self.cli("edit", self.root, "--intent", "a", "--pillar", "p", "--anchor", "s")
        self.cli("edit", self.root, "--intent", "", "--clear-pillars", "--clear-anchors")
        data = self.state()
        self.assertEqual((data["intent_card"], data["design_pillars"], data["style_anchors"]), ("", [], []))


if __name__ == "__main__":
    unittest.main()
