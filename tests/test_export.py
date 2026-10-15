"""CAD-30 fixture regressions; no Krita, network, or real artwork claims."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import cwb
import cwb_export as export
import cwb_project as project

CLI = Path(__file__).resolve().parents[1] / "core" / "cwb.py"


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "角色 项目"
        project.initialize(self.root, "角色")
        project.update(self.root, lambda r, d: project.set_stage(d, 4, "fixture 探索"))
        self.a = self.asset("stages/04-探索/正式 图.png", b"fixture PNG")
        self.out = self.parent / "交付 包"

    def asset(self, path, content, category="final"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        project.update(self.root, lambda r, d: project.record(r, d, path, 4, category, "fixture"))
        return project.read_state(self.root)[0]["assets"][-1]

    def save_state(self, state):
        (self.root / project.STATE).write_text(json.dumps(state), encoding="utf-8")

    def do_export(self, **kwargs):
        return export.export_project(self.root, self.out, [self.a["id"]], **kwargs)

    def assert_clean(self):
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.parent.glob(".cwb-export-*")), [])

    def test_stage_four_minimal_explicit_selection_preserves_sources(self):
        self.asset("stages/04-探索/未选正式.png", b"excluded")
        self.asset("stages/04-探索/临时.png", b"temporary", "temporary")
        (self.root / "private-token").write_text("secret")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        report = self.do_export()
        self.assertEqual(report["current_stage"], 4)
        self.assertEqual(len(report["assets"]), 1)
        self.assertIn("kra", report["missing"])
        self.assertIn("final_png", report["missing"])  # no extension-based role invention
        self.assertIn("intent_card", report["missing"])
        self.assertEqual((self.out / "project_state.json").read_bytes(), before[self.root / project.STATE])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(len(list((self.out / "assets").rglob("*.png"))), 1)
        self.assertFalse((self.out / "private-token").exists())
        for record in report["files"]:
            payload = (self.out / record["path"]).read_bytes()
            self.assertEqual(record["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(record["size"], len(payload))

    def test_existing_metadata_and_explicit_roles(self):
        kra = self.asset("stages/04-探索/源.kra", b"not a real KRA")
        wf = self.asset("stages/04-探索/workflow.json", b'{"nodes": []}')
        project.update(self.root, lambda r, d: d.update(intent_card="意图", design_pillars=["剪影"]))
        sidecar = {"description": "设计说明", "references": ["作者 / https://example.test / 授权未知"],
                   "versions": ["v1：手工探索"]}
        (self.root / "export.json").write_text(json.dumps(sidecar), encoding="utf-8")
        report = export.export_project(self.root, self.out, [self.a["id"], kra["id"], wf["id"]],
                                        ["final_png=" + self.a["id"], "kra=" + kra["id"],
                                         "workflow=" + wf["id"]], "export.json")
        self.assertEqual(report["missing"], ["setting"])
        notes = json.loads((self.out / "design_notes.json").read_text())
        self.assertEqual(notes["notes"], sidecar)
        self.assertEqual(notes["intent_card"], "意图")
        self.assertIn("未验证", report["verification"])

    def test_zip_is_deterministic_and_matches_directory(self):
        report = self.do_export()
        z1, z2 = self.parent / "one.zip", self.parent / "two.zip"
        for path in (z1, z2):
            self.assertEqual(export.export_project(self.root, path, [self.a["id"]], zipped=True), report)
        self.assertEqual(z1.read_bytes(), z2.read_bytes())
        with zipfile.ZipFile(z1) as archive:
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                self.assertFalse(name.startswith("/"))
                self.assertNotIn("..", Path(name).parts)
                self.assertEqual(archive.read(name), (self.out / name).read_bytes())

    def test_missing_selected_file_is_truthful(self):
        (self.root / self.a["path"]).unlink()
        report = self.do_export(roles=["final_png=" + self.a["id"]])
        self.assertEqual(report["assets"], [])
        self.assertEqual(report["missing_assets"][0]["path"], self.a["path"])
        self.assertIn("final_png", report["missing"])

    def test_changed_hash_and_size_fail_and_cleanup(self):
        (self.root / self.a["path"]).write_bytes(b"user edit")
        with self.assertRaisesRegex(project.ProjectError, "hash/size"):
            self.do_export()
        self.assert_clean()
        self.assertEqual((self.root / self.a["path"]).read_bytes(), b"user edit")

    def test_recorded_hash_damage_fails(self):
        data = project.read_state(self.root)[0]
        data["assets"][0]["sha256"] = "0" * 64
        self.save_state(data)
        with self.assertRaises(project.ProjectError):
            self.do_export()
        self.assert_clean()

    def test_existing_targets_never_overwritten(self):
        for kind in ("file", "directory", "symlink"):
            with self.subTest(kind=kind):
                target = self.parent / kind
                if kind == "file":
                    target.write_bytes(b"keep")
                elif kind == "directory":
                    target.mkdir()
                else:
                    target.symlink_to(self.parent / "absent")
                with self.assertRaises((OSError, project.ProjectError)):
                    export.export_project(self.root, target, [self.a["id"]], zipped=kind == "file")
                self.assertTrue(os.path.lexists(target))
                if kind == "file":
                    self.assertEqual(target.read_bytes(), b"keep")

    def test_traversal_and_malformed_asset_paths(self):
        original = project.read_state(self.root)[0]
        for path in ("../escape", "/absolute", "a/../../escape", "a//b", "./a", "a\\b", "C:secret", "a/../b"):
            with self.subTest(path=path):
                data = copy.deepcopy(original)
                data["assets"][0]["path"] = path
                self.save_state(data)
                with self.assertRaises(project.ProjectError):
                    self.do_export()
                self.assert_clean()

    def test_source_and_parent_symlinks_rejected(self):
        path = self.root / self.a["path"]
        path.unlink()
        path.symlink_to(self.parent / "secret")
        (self.parent / "secret").write_bytes(b"secret")
        with self.assertRaises(OSError):
            self.do_export()
        self.assert_clean()
        path.unlink()
        path.parent.rename(path.parent.with_name("real"))
        path.parent.symlink_to(path.parent.with_name("real"), target_is_directory=True)
        with self.assertRaises(OSError):
            self.do_export()
        self.assert_clean()

    def test_root_output_and_state_symlinks_rejected(self):
        alias = self.parent / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(project.ProjectError):
            export.export_project(alias, self.out, [self.a["id"]])
        output_parent = self.parent / "out-alias"
        output_parent.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(project.ProjectError):
            export.export_project(self.root, output_parent / "new", [self.a["id"]])
        state = self.root / project.STATE
        state.rename(self.root / "real-state")
        state.symlink_to("real-state")
        with self.assertRaises(OSError):
            self.do_export()
        self.assert_clean()

    def test_nonregular_source_rejected(self):
        path = self.root / self.a["path"]
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(project.ProjectError):
            self.do_export()
        self.assert_clean()

    def test_nested_output_rejected(self):
        for path in (self.root, self.root / "export", self.parent):
            with self.assertRaises(project.ProjectError):
                export.export_project(self.root, path, [self.a["id"]])

    def test_duplicate_and_ancestor_targets_rejected(self):
        data = project.read_state(self.root)[0]
        duplicate = copy.deepcopy(data["assets"][0])
        duplicate["id"] = str(uuid.uuid4())
        for path in (self.a["path"], self.a["path"] + "/child", "stages/04-探索"):
            duplicate["path"] = path
            data["assets"] = [data["assets"][0], duplicate]
            self.save_state(data)
            with self.assertRaisesRegex(project.ProjectError, "重复或嵌套"):
                export.export_project(self.root, self.out, [self.a["id"], duplicate["id"]])
            self.assert_clean()

    def test_invalid_selection_and_roles(self):
        temporary = self.asset("temporary", b"not final", "temporary")
        for ids, roles in (([], []), ([self.a["id"]] * 2, []), (["unknown"], []),
                           ([temporary["id"]], []), ([self.a["id"]], ["bogus=" + self.a["id"]]),
                           ([self.a["id"]], ["kra=unknown"]),
                           ([self.a["id"]], ["kra=" + self.a["id"]] * 2)):
            with self.assertRaises(project.ProjectError):
                export.export_project(self.root, self.out, ids, roles)
            self.assert_clean()

    def test_sidecar_escape_symlink_and_unknown_fields_rejected(self):
        for name, content in (("bad.json", {"token": "secret"}), ("bad.json", {"references": "not a list"})):
            (self.root / name).write_text(json.dumps(content))
            with self.assertRaises(project.ProjectError):
                self.do_export(metadata=name)
            self.assert_clean()
        with self.assertRaises(project.ProjectError):
            self.do_export(metadata="../secret")
        (self.root / "link.json").symlink_to(self.root / "bad.json")
        with self.assertRaises(OSError):
            self.do_export(metadata="link.json")

    def test_write_failure_cleanup(self):
        with mock.patch.object(export, "_file_hash", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                self.do_export()
        self.assert_clean()

    def test_mid_export_source_or_state_change_fails(self):
        original = export._file_hash
        for changed in (self.root / self.a["path"], self.root / project.STATE):
            with self.subTest(changed=changed):
                old = changed.read_bytes()
                def mutate(path):
                    result = original(path)
                    changed.write_bytes(old + b" ")
                    return result
                with mock.patch.object(export, "_file_hash", side_effect=mutate):
                    with self.assertRaises(project.ProjectError):
                        self.do_export()
                self.assert_clean()
                changed.write_bytes(old)

    def test_concurrent_destination_creation_is_not_overwritten(self):
        original = export._rename_new
        def race(source, target):
            target.mkdir()
            return original(source, target)
        with mock.patch.object(export, "_rename_new", side_effect=race):
            with self.assertRaises(FileExistsError):
                self.do_export()
        self.assertTrue(self.out.is_dir())
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertEqual(list(self.parent.glob(".cwb-export-*")), [])

    def test_zip_publication_failure_cleanup(self):
        with mock.patch.object(export.os, "link", side_effect=OSError("publish failure")):
            with self.assertRaises(OSError):
                self.do_export(zipped=True)
        self.assert_clean()

    def test_help_is_offline_and_does_not_create_cache(self):
        cache = self.parent / "wf" / "cowork_cache"
        with mock.patch.object(cwb, "http", side_effect=AssertionError("network")), \
                mock.patch.dict(os.environ, {"CWB_WF_DIR": str(cache.parent)}):
            for args in (["--help"], ["export-project", "--help"]):
                with self.assertRaises(SystemExit) as exit_info:
                    cwb.main(args)
                self.assertEqual(exit_info.exception.code, 0)
        result = subprocess.run([sys.executable, "-B", str(CLI), "export-project", "--help"],
                                cwd=self.parent, env={**os.environ, "CWB_WF_DIR": str(cache.parent)},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(cache.parent.exists())

    def test_cli_export(self):
        result = subprocess.run([sys.executable, "-B", str(CLI), "export-project", str(self.root),
                                 str(self.out), "--asset", self.a["id"], "--role", "final_png=" + self.a["id"]],
                                cwd=self.parent, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("final_png", json.loads(result.stdout)["missing"])


if __name__ == "__main__":
    unittest.main()
