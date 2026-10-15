"""CAD-26 offline sidecar contracts; no Krita/ComfyUI or model execution."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
import cwb
import cwb_project as project
import cwb_style as style
import cwb_compare as compare

try:
    from PIL import Image
except ImportError:
    Image = None


class StyleFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cad26-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        project.initialize(self.project, "两个角色离线测试")
        self.project_bytes = (self.project / project.STATE).read_bytes()
        self.config_path = self.root / "config.json"
        self.store = self.project / style.STORE
        style.initialize(self.project, actor="人工甲", reason="建立风格档案")

    def config(self):
        return {"references": [], "style_tokens": [
            {"id": "shape", "dimension": "dominant_shape", "value": "圆形主导",
             "scope": ["character-b", "character-a"], "enabled": True, "locked": False, "reference_ids": []},
            {"id": "color", "dimension": "palette", "value": "低饱和蓝与橙点缀",
             "scope": ["*"], "enabled": True, "locked": False, "reference_ids": []}]}

    def edit(self, config=None, revision=None, **kwargs):
        self.config_path.write_text(json.dumps(self.config() if config is None else config, ensure_ascii=False), encoding="utf-8")
        if revision is None:
            revision = style.show(self.project)["latest"]["revision"]
        return style.update(self.project, revision, actor="人工甲", reason="编辑约束", config_path=self.config_path, **kwargs)

    def state(self, action, item_id="color", kind="style_tokens", revision=None):
        if revision is None:
            revision = style.show(self.project)["latest"]["revision"]
        return style.update(self.project, revision, actor="人工乙", reason="人工决定-" + action,
                            action=action, kind=kind, item_id=item_id)

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.store.iterdir() if p.is_file()}


class StyleTests(StyleFixture, unittest.TestCase):
    def test_init_full_history_and_project_untouched(self):
        initial = self.snapshot()
        result = self.edit()
        self.assertEqual(result["revision"], 1)
        data = style.show(self.project)
        self.assertEqual(data["schema"], style.SCHEMA)
        self.assertEqual(len(data["history"]), 2)
        self.assertEqual(data["history"][1]["previous_sha256"], hashlib.sha256(initial["revision-000000.json"]).hexdigest())
        self.assertEqual(data["latest"]["actor"], "人工甲")
        self.assertEqual(data["latest"]["reason"], "编辑约束")
        self.assertTrue(data["latest"]["recorded_at"].endswith("+00:00"))
        self.assertEqual(data["latest"]["project"]["sha256"], hashlib.sha256(self.project_bytes).hexdigest())
        self.assertEqual(initial["revision-000000.json"], (self.store / "revision-000000.json").read_bytes())
        self.assertEqual(self.project_bytes, (self.project / project.STATE).read_bytes())
        self.assertNotIn("style_tokens", project.inspect(self.project)["state"])
        self.assertIn("未写入项目主状态", data["notice"])

    def test_existing_init_and_partial_store_not_reused(self):
        before = self.snapshot()
        with self.assertRaises(FileExistsError):
            style.initialize(self.project, actor="人工", reason="不能重置")
        self.assertEqual(before, self.snapshot())
        other = self.root / "other"
        project.initialize(other, "其他")
        (other / style.STORE).mkdir()
        sentinel = other / style.STORE / "keep.txt"
        sentinel.write_text("旧文件")
        with self.assertRaises(FileExistsError):
            style.initialize(other, actor="人工", reason="不接管旧目录")
        with self.assertRaisesRegex(ValueError, "缺少初始修订"):
            style.show(other)
        self.assertEqual(sentinel.read_text(), "旧文件")

    def test_lock_unlock_edit_cancel_enable_are_explicit_and_reopenable(self):
        self.edit()
        self.state("lock")
        locked = self.snapshot()
        config = self.config()
        config["style_tokens"][1]["value"] = "红色"
        with self.assertRaisesRegex(ValueError, "锁定项"):
            self.edit(config)
        with self.assertRaisesRegex(ValueError, "锁定项"):
            self.state("cancel")
        self.assertEqual(locked, self.snapshot())
        self.state("unlock")
        self.edit(config)
        self.state("cancel")
        self.assertNotIn("palette:", style.prompt(self.project, "character-a")["fragment"])
        self.state("enable")
        output = subprocess.run([sys.executable, "-B", str(ROOT / "core/cwb.py"), "style", "show", str(self.project)],
                                cwd=self.root, text=True, capture_output=True, check=True)
        history = json.loads(output.stdout)["history"]
        self.assertEqual([h["operation"]["action"] for h in history], ["init", "edit", "lock", "unlock", "edit", "cancel", "enable"])
        for h in history[2:]:
            self.assertTrue(h["actor"])
            self.assertTrue(h["reason"])
        self.assertEqual(self.project_bytes, (self.project / project.STATE).read_bytes())

    def test_edit_cannot_delete_lock_or_toggle_enabled(self):
        self.edit()
        before = self.snapshot()
        configs = []
        deleted = self.config()
        deleted["style_tokens"].pop()
        configs.append(deleted)
        for field in ("locked", "enabled"):
            config = self.config()
            config["style_tokens"][0][field] = not config["style_tokens"][0][field]
            configs.append(config)
        added_locked = self.config()
        token = copy.deepcopy(added_locked["style_tokens"][0])
        token.update(id="new", locked=True)
        added_locked["style_tokens"].append(token)
        configs.append(added_locked)
        for config in configs:
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.edit(config)
            self.assertEqual(before, self.snapshot())

    def test_noop_unknown_target_bad_actor_reason_revision_do_not_write(self):
        self.edit()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "没有变化"):
            self.edit()
        for action, kind, item_id, revision in (("unlock", "style_tokens", "color", 1),
                ("enable", "style_tokens", "color", 1), ("lock", "style_tokens", "missing", 1),
                ("delete", "style_tokens", "color", 1), ("lock", "invalid", "color", 1),
                ("lock", "style_tokens", "color", True), ("lock", "style_tokens", "color", -1)):
            with self.subTest(action=action, item_id=item_id), self.assertRaises(ValueError):
                self.state(action, item_id, kind, revision)
        for actor, reason in ((" ", "r"), ("a", ""), (None, "r"), ("a", "x" * 8193)):
            with self.assertRaises(ValueError):
                style.update(self.project, 1, actor=actor, reason=reason, action="lock", kind="style_tokens", item_id="color")
        self.assertEqual(before, self.snapshot())

    def test_revision_conflict_checked_before_reading_config(self):
        self.edit()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "修订冲突"):
            style.update(self.project, 0, actor="人工", reason="过时", config_path=self.root / "missing")
        self.assertEqual(before, self.snapshot())

    def test_two_processes_same_expected_revision_only_one_wins(self):
        self.edit()
        def attempt(item):
            return subprocess.run([sys.executable, "-B", str(ROOT / "core/cwb.py"), "style", "state", str(self.project),
                "style_tokens", item, "lock", "--actor", "人工", "--reason", "竞争", "--expect-revision", "1"],
                cwd=self.root, capture_output=True, text=True)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ("color", "shape")))
        self.assertEqual(sorted(p.returncode == 0 for p in results), [False, True])
        self.assertIn("修订冲突", next(p.stderr for p in results if p.returncode))
        self.assertEqual(style.show(self.project)["latest"]["revision"], 2)

    def test_prompt_stable_scoped_export_has_revision_sources_not_generation(self):
        self.edit()
        first = style.prompt(self.project, "character-a")
        self.assertEqual(first, style.prompt(self.project, "character-a"))
        self.assertEqual(first["fragment"], "palette: 低饱和蓝与橙点缀\ndominant_shape: 圆形主导")
        self.assertEqual(first["style_revision"], 1)
        self.assertEqual(first["style_revision_sha256"], style.show(self.project)["revision_sha256"])
        self.assertEqual(first["fragment"], style.prompt(self.project, "character-b")["fragment"])
        self.assertEqual(style.prompt(self.project, "other")["fragment"], "palette: 低饱和蓝与橙点缀")
        output = self.root / "prompt.json"
        style.prompt(self.project, "character-a", output)
        self.assertEqual(json.loads(output.read_bytes()), first)
        before = output.read_bytes()
        with self.assertRaises(FileExistsError):
            style.prompt(self.project, "character-b", output)
        self.assertEqual(before, output.read_bytes())
        for scope in ("*", " ", None):
            with self.assertRaises(ValueError):
                style.prompt(self.project, scope)
        self.assertEqual(self.project_bytes, (self.project / project.STATE).read_bytes())
        self.assertNotIn("workflow", first)
        self.assertIn("不保证跨图一致性", first["notice"])

    def test_reordering_input_is_noop_and_prompt_is_deterministic(self):
        self.edit()
        before = style.prompt(self.project, "character-a")
        config = self.config()
        config["style_tokens"].reverse()
        config["style_tokens"][1]["scope"].reverse()
        with self.assertRaisesRegex(ValueError, "没有变化"):
            self.edit(config)
        self.assertEqual(style.prompt(self.project, "character-a"), before)

    def test_malformed_config_fields_values_json_fail_closed(self):
        configs = [[], {}, {"references": [], "style_tokens": [], "unknown": 1}]
        for field, values in {
            "id": [None, "Bad", "a/b", "", []], "dimension": ["unknown", None, []],
            "value": [None, " ", "x" * 8193], "enabled": [1, None, "true"], "locked": [0, []],
            "scope": [[], ["*", "a"], ["a", "a"], [None], "*"],
            "reference_ids": [["missing"], [None], "x", ["a", "a"]]
        }.items():
            for value in values:
                config = self.config()
                config["style_tokens"][0][field] = value
                configs.append(config)
        duplicate = self.config()
        duplicate["style_tokens"].append(copy.deepcopy(duplicate["style_tokens"][0]))
        configs.append(duplicate)
        missing = self.config()
        del missing["style_tokens"][0]["value"]
        configs.append(missing)
        huge = self.config()
        huge["style_tokens"] *= 129
        configs.append(huge)
        before = self.snapshot()
        for config in configs:
            with self.subTest(config=str(config)[:200]), self.assertRaises(ValueError):
                self.edit(config, 0)
            self.assertEqual(before, self.snapshot())
        for raw in (b"not json", b'{"references":[],"references":[],"style_tokens":[]}',
                    b'{"references":[],"style_tokens":NaN}', b"\xff"):
            self.config_path.write_bytes(raw)
            with self.assertRaises(ValueError):
                style.update(self.project, 0, actor="人工", reason="配置错误", config_path=self.config_path)
            self.assertEqual(before, self.snapshot())

    def test_config_symlink_hardlink_fifo_directory_and_limit_rejected(self):
        self.config_path.write_text(json.dumps(self.config()))
        alias = self.root / "config-alias"
        alias.symlink_to(self.config_path)
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        for path in (alias, fifo, self.root):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                style.update(self.project, 0, actor="人工", reason="路径错误", config_path=path)
        alias.unlink()
        os.link(self.config_path, alias)
        with self.assertRaisesRegex(ValueError, "硬链接"):
            style.update(self.project, 0, actor="人工", reason="链接", config_path=self.config_path)
        alias.unlink()
        self.config_path.write_bytes(b" " * (style.LIMIT + 1))
        with self.assertRaises(ValueError):
            style.update(self.project, 0, actor="人工", reason="超限", config_path=self.config_path)
        self.assertEqual(len(self.snapshot()), 1)

    def test_paths_lexical_and_link_aliases_never_traversed(self):
        before = self.snapshot()
        for path in (str(self.project) + "/", str(self.project) + "/.", str(self.root) + "/x/../project",
                     str(self.root) + "//project", None, b"project"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                style.show(path)
        alias = self.root / "alias"
        alias.symlink_to(self.project, target_is_directory=True)
        with self.assertRaises(OSError):
            style.show(alias)
        output = self.root / "out.json"
        output.symlink_to(self.project / project.STATE)
        with self.assertRaises(FileExistsError):
            style.prompt(self.project, "a", output)
        parent_alias = self.root / "parent-alias"
        parent_alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            style.prompt(self.project, "a", parent_alias / "new.json")
        with self.assertRaisesRegex(ValueError, "修订目录"):
            style.prompt(self.project, "a", self.store / "export.json")
        self.assertFalse((self.root / "new.json").exists())
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.project_bytes, (self.project / project.STATE).read_bytes())

    def test_store_and_revision_symlinks_and_hardlinks_rejected(self):
        revision = self.store / "revision-000000.json"
        raw = revision.read_bytes()
        revision.unlink()
        revision.symlink_to(self.project / project.STATE)
        with self.assertRaises(OSError):
            style.show(self.project)
        revision.unlink()
        revision.write_bytes(raw)
        alias = self.root / "hardlink"
        os.link(revision, alias)
        with self.assertRaisesRegex(ValueError, "硬链接"):
            style.show(self.project)
        alias.unlink()
        other = self.root / "other"
        project.initialize(other, "其他")
        (other / style.STORE).symlink_to(self.store, target_is_directory=True)
        with self.assertRaises(OSError):
            style.show(other)
        self.assertEqual(raw, revision.read_bytes())

    def test_project_malformed_or_aliased_fails_without_sidecar_write(self):
        path = self.project / project.STATE
        before = self.snapshot()
        for raw in (b"[]", b"{}", b'{"schema_version":1,"schema_version":1}'):
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                style.show(self.project)
            self.assertEqual(before, self.snapshot())
        path.write_bytes(self.project_bytes)
        alias = self.root / "project-state-hardlink"
        os.link(path, alias)
        with self.assertRaises(ValueError):
            style.show(self.project)
        alias.unlink()
        path.unlink()
        path.symlink_to(self.config_path)
        with self.assertRaises(OSError):
            style.show(self.project)
        self.assertEqual(before, self.snapshot())

    def test_history_chain_sequence_schema_and_project_identity_fail_closed(self):
        self.edit()
        path = self.store / "revision-000000.json"
        original = path.read_bytes()
        for field, value in (("actor", "tampered"), ("schema", "future"), ("revision", True),
                             ("previous_sha256", "a" * 64), ("operation", {})):
            data = json.loads(original)
            data[field] = value
            path.write_text(json.dumps(data))
            with self.subTest(field=field), self.assertRaises(ValueError):
                style.show(self.project)
        path.write_bytes(original)
        missing = self.store / "revision-000001.json"
        saved = missing.read_bytes()
        missing.unlink()
        gap = self.store / "revision-000002.json"
        gap.write_bytes(saved)
        with self.assertRaisesRegex(ValueError, "序号"):
            style.show(self.project)
        gap.unlink()
        missing.write_bytes(saved)
        state_path = self.project / project.STATE
        data = json.loads(self.project_bytes)
        data["project_id"] = "11111111-1111-1111-1111-111111111111"
        state_path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "所属项目"):
            style.show(self.project)
        state_path.write_bytes(self.project_bytes)
        self.assertEqual(style.show(self.project)["latest"]["revision"], 1)

    def test_observed_project_snapshot_not_main_state_extension(self):
        self.edit()
        project.update(self.project, lambda root, data: data.update(name="主状态另行修改"), expected=0)
        data = style.show(self.project)
        self.assertEqual(data["current_project"]["revision"], 1)
        self.assertEqual(data["latest"]["project"]["revision"], 0)
        self.state("lock")
        self.assertEqual(style.show(self.project)["latest"]["project"]["revision"], 1)
        self.assertNotIn("style_tokens", project.inspect(self.project)["state"])

    def test_write_failure_never_overwrites_history_and_postpublish_failure_is_explicit(self):
        self.edit()
        before = self.snapshot()
        fsync = style.os.fsync
        def fail_file(fd):
            if not os.path.isdir("/proc/self/fd/" + str(fd)):
                raise OSError("injected file fsync failure")
            return fsync(fd)
        with patch.object(style.os, "fsync", side_effect=fail_file), self.assertRaises(OSError):
            self.state("lock")
        self.assertEqual(before, self.snapshot())
        self.assertFalse(any(p.name.startswith(".style-") for p in self.store.iterdir()))
        def fail_directory(fd):
            if os.path.isdir("/proc/self/fd/" + str(fd)):
                raise OSError("injected directory fsync failure")
            return fsync(fd)
        with patch.object(style.os, "fsync", side_effect=fail_directory), self.assertRaisesRegex(OSError, "已发布"):
            self.state("lock")
        self.assertEqual(style.show(self.project)["latest"]["revision"], 2)
        for name, raw in before.items():
            self.assertEqual((self.store / name).read_bytes(), raw)

    def test_cli_parser_lazy_dispatch_and_prompt_output(self):
        self.edit()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(cwb, "http", side_effect=AssertionError("network")):
            cwb.main(["style", "prompt", str(self.project), "--scope", "character-a"])
        self.assertEqual(json.loads(out.getvalue())["style_revision"], 1)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            cwb.main(["style", "state", str(self.project), "style_tokens", "color", "lock", "--actor", "人工", "--reason", "r"])
        self.assertEqual(exc.exception.code, 2)


@unittest.skipIf(Image is None, "optional external CLI Pillow unavailable")
class StyleReferenceTests(StyleFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.reference = self.root / "reference.png"
        self.reference2 = self.root / "reference2.png"
        self.current = self.root / "current.png"
        self.output = self.root / "comparison"
        for path, color in ((self.reference, (255, 0, 0, 255)), (self.reference2, (0, 255, 0, 255)), (self.current, (0, 0, 255, 80))):
            self.png(path, (4, 3), color)

    def png(self, path, size, color):
        with Image.new("RGBA", size, color) as image:
            image.save(path)

    def reference_config(self, multiple=False):
        config = self.config()
        config["references"] = [{"id": "benchmark", "path": str(self.reference), "source": "人工来源说明/许可待核",
                                  "scope": ["*"], "enabled": True, "locked": False}]
        if multiple:
            config["references"].append({"id": "second", "path": str(self.reference2), "source": None,
                                         "scope": ["character-b"], "enabled": True, "locked": False})
        config["style_tokens"][1]["reference_ids"] = ["benchmark"]
        return config

    def comparison(self, **kwargs):
        return style.compare_reference(self.project, "benchmark", self.current, self.output,
                                       scope="character-a", crop=(1, 1, 2, 1), **kwargs)

    def test_one_and_multiple_references_hash_size_scope_and_sources(self):
        self.edit(self.reference_config(multiple=True))
        data = style.show(self.project)
        refs = data["latest"]["config"]["references"]
        self.assertEqual(len(refs), 2)
        for entry, path in zip(refs, (self.reference, self.reference2)):
            self.assertEqual(entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(entry["bytes"], path.stat().st_size)
            self.assertEqual(entry["dimensions"], [4, 3])
        self.assertEqual([e["status"] for e in data["reference_checks"]], ["ok", "ok"])
        self.assertEqual(len(style.prompt(self.project, "character-a")["references"]), 1)
        self.assertEqual(len(style.prompt(self.project, "character-b")["references"]), 2)
        self.assertIsNone(refs[1]["source"])

    def test_changed_reference_not_relabelled_and_unlock_refresh_has_history(self):
        self.edit(self.reference_config())
        self.state("lock", "benchmark", "references")
        old = self.snapshot()
        old_sha = style.show(self.project)["latest"]["config"]["references"][0]["sha256"]
        self.png(self.reference, (4, 3), (40, 50, 60, 255))
        self.assertEqual(style.show(self.project)["reference_checks"][0]["status"], "changed")
        with self.assertRaisesRegex(ValueError, "参考图已改变"):
            style.prompt(self.project, "character-a", self.root / "stale-prompt.json")
        with self.assertRaisesRegex(ValueError, "参考图已改变"):
            self.comparison()
        with self.assertRaisesRegex(ValueError, "锁定项"):
            self.edit(self.reference_config())
        self.assertFalse(self.output.exists())
        self.assertFalse((self.root / "stale-prompt.json").exists())
        self.assertEqual(old, self.snapshot())
        self.state("unlock", "benchmark", "references")
        self.edit(self.reference_config())
        fresh = style.prompt(self.project, "character-a")
        self.assertNotEqual(fresh["references"][0]["sha256"], old_sha)
        self.assertEqual(style.show(self.project)["history"][1]["config"]["references"][0]["sha256"], old_sha)
        for name, raw in old.items():
            self.assertEqual(raw, (self.store / name).read_bytes())

    def test_missing_or_invalid_reference_reported_can_unlock_cancel_but_not_reuse(self):
        self.edit(self.reference_config())
        self.state("lock", "benchmark", "references")
        self.reference.unlink()
        self.assertEqual(style.show(self.project)["reference_checks"][0]["status"], "missing")
        self.state("unlock", "benchmark", "references")
        self.state("cancel", "benchmark", "references")
        with self.assertRaisesRegex(ValueError, "停用或不适用"):
            style.prompt(self.project, "character-a")
        with self.assertRaisesRegex(ValueError, "参考图已改变"):
            self.state("enable", "benchmark", "references")
        self.state("cancel", "color")
        self.assertEqual(style.prompt(self.project, "character-a")["references"], [])
        self.reference.symlink_to(self.current)
        self.assertEqual(style.show(self.project)["reference_checks"][0]["status"], "invalid")

    def test_ref_scope_mismatch_does_not_silently_discard_token_provenance(self):
        config = self.reference_config()
        config["references"][0]["scope"] = ["character-b"]
        self.edit(config)
        with self.assertRaisesRegex(ValueError, "不适用"):
            style.prompt(self.project, "character-a")
        self.assertEqual(len(style.prompt(self.project, "character-b")["references"]), 1)

    def test_reference_config_unknown_captured_fields_invalid_paths_and_sources(self):
        before = self.snapshot()
        for field, value in (("path", None), ("path", ""), ("source", []), ("source", " "), ("sha256", "a" * 64)):
            config = self.reference_config()
            config["references"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.edit(config)
            self.assertEqual(before, self.snapshot())
        alias = self.root / "ref-alias"
        alias.symlink_to(self.reference)
        for path in (str(alias), str(self.root) + "/./reference.png"):
            config = self.reference_config()
            config["references"][0]["path"] = path
            with self.assertRaises((OSError, ValueError)):
                self.edit(config)
        alias.unlink()
        os.link(self.reference, alias)
        with self.assertRaisesRegex(ValueError, "硬链接"):
            self.edit(self.reference_config())

    def test_bad_reference_images_and_capture_changes_do_not_append(self):
        for raw in (b"not png", b"\x89PNG\r\n\x1a\n"):
            self.reference.write_bytes(raw)
            with self.assertRaises((OSError, ValueError)):
                self.edit(self.reference_config())
        with Image.new("RGB", (4, 3)) as image:
            image.save(self.reference, format="JPEG")
        with self.assertRaisesRegex(ValueError, "单帧"):
            self.edit(self.reference_config())
        with Image.new("I;16", (4, 3)) as image:
            image.save(self.reference)
        with self.assertRaises(ValueError):
            self.edit(self.reference_config())
        self.png(self.reference, (4, 3), (1, 2, 3, 4))
        capture = style._capture_config
        def mutate(config):
            captured = capture(config)
            self.reference.write_bytes(self.reference.read_bytes() + b"changed")
            return captured
        with patch.object(style, "_capture_config", side_effect=mutate), self.assertRaisesRegex(ValueError, "参考图已改变"):
            self.edit(self.reference_config())
        self.assertEqual(len(self.snapshot()), 1)

    def test_comparison_reuses_public_api_native_common_crop_and_provenance(self):
        self.edit(self.reference_config())
        with Image.open(self.reference) as image:
            image.putpixel((1, 1), (10, 20, 30, 0))
            image.save(self.reference)
        self.edit(self.reference_config())
        review = dict(palette="人工：蓝橙差异待修", proportion=None, dominant_shape="人工：主导圆形", detail_density=None)
        before = self.snapshot()
        result = self.comparison(review=review, candidate_version="手工作品-v2")
        manifest = compare.show(self.output)["manifest"]
        provenance = json.loads(manifest["sources"]["before"]["source_version"])
        self.assertEqual(provenance["style_revision"], result["style_revision"])
        self.assertEqual(provenance["manual_review"], review)
        self.assertIn("no expected-source-sha", provenance["validation_contract"])
        self.assertEqual(manifest["sources"]["after"]["source_version"], "手工作品-v2")
        self.assertIsNone(manifest["provenance"]["seed"])
        self.assertIsNone(manifest["provenance"]["workflow"])
        self.assertEqual(manifest["display"]["scale"], 1)
        self.assertEqual(manifest["display"]["crop_xywh"], [1, 1, 2, 1])
        with Image.open(self.output / "comparison.png") as image:
            self.assertEqual(image.size, (4, 21))
            self.assertEqual(image.getpixel((0, 20)), (10, 20, 30, 0))
            self.assertEqual(image.getpixel((2, 20)), (0, 0, 255, 80))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.project_bytes, (self.project / project.STATE).read_bytes())
        self.assertIn("post-call", result["reference_validation"])

    def test_comparison_mismatch_crop_and_selection_fail_without_stretch(self):
        self.edit(self.reference_config())
        self.png(self.current, (5, 3), (0, 0, 0, 255))
        with self.assertRaisesRegex(ValueError, "尺寸不匹配"):
            self.comparison()
        self.assertFalse(self.output.exists())
        self.png(self.current, (4, 3), (0, 0, 0, 255))
        for reference, crop in (("missing", (0, 0, 1, 1)), ("benchmark", None), ("benchmark", (-1, 0, 1, 1))):
            with self.assertRaises(ValueError):
                style.compare_reference(self.project, reference, self.current, self.output, scope="character-a", crop=crop)
        with self.assertRaisesRegex(ValueError, "修订目录"):
            style.compare_reference(self.project, "benchmark", self.current, self.store / "comparison", scope="character-a", crop=(0, 0, 1, 1))
        self.assertFalse(self.output.exists())

    def test_existing_comparison_or_source_output_never_deleted_or_overwritten(self):
        self.edit(self.reference_config())
        self.comparison()
        saved = {p.name: p.read_bytes() for p in self.output.iterdir()}
        with self.assertRaises(FileExistsError):
            self.comparison()
        self.assertEqual(saved, {p.name: p.read_bytes() for p in self.output.iterdir()})
        raw = self.current.read_bytes()
        with self.assertRaises(FileExistsError):
            style.compare_reference(self.project, "benchmark", self.current, self.current, scope="character-a", crop=(0, 0, 1, 1))
        self.assertEqual(raw, self.current.read_bytes())

    def test_race_between_precheck_and_public_create_keeps_diagnostic_not_success(self):
        self.edit(self.reference_config())
        create = compare.create
        def mutate(*args, **kwargs):
            self.png(self.reference, (4, 3), (99, 88, 77, 255))
            return create(*args, **kwargs)
        with patch.object(compare, "create", side_effect=mutate), self.assertRaisesRegex(ValueError, "不是成功的风格比较"):
            self.comparison()
        self.assertTrue((self.output / "compare.json").exists())
        self.assertEqual(compare.show(self.output)["state"], "pending")
        self.assertEqual(style.show(self.project)["reference_checks"][0]["status"], "changed")

    def test_live_reference_change_after_create_detected_even_when_saved_before_matches(self):
        self.edit(self.reference_config())
        create = compare.create
        def mutate(*args, **kwargs):
            result = create(*args, **kwargs)
            self.png(self.reference, (4, 3), (9, 8, 7, 255))
            return result
        with patch.object(compare, "create", side_effect=mutate), self.assertRaisesRegex(ValueError, "比较后验证失败"):
            self.comparison()
        self.assertTrue(self.output.is_dir())
        saved_sha = compare.show(self.output)["manifest"]["sources"]["before"]["sha256"]
        self.assertEqual(saved_sha, style.show(self.project)["latest"]["config"]["references"][0]["sha256"])

    def test_comparison_show_failure_preserves_diagnostic_and_prints_no_success(self):
        self.edit(self.reference_config())
        stdout = io.StringIO()
        with patch.object(compare, "show", side_effect=ValueError("injected verification failure")), contextlib.redirect_stdout(stdout):
            with self.assertRaisesRegex(ValueError, "仅供诊断"):
                cwb.main(["style", "compare", str(self.project), str(self.current), str(self.output),
                          "--scope", "character-a", "--reference", "benchmark", "--crop", "0", "0", "2", "2"])
        self.assertEqual(stdout.getvalue(), "")
        self.assertTrue((self.output / "compare.json").exists())


class StyleHelpTests(unittest.TestCase):
    def test_all_help_loads_no_execution_dependencies_network_or_cache(self):
        with tempfile.TemporaryDirectory(prefix="cad26-help-") as tmp:
            code = '''import sys
sys.path.insert(0, sys.argv[1])
def audit(event, args):
    if event.startswith("socket.") or event in ("os.mkdir", "subprocess.Popen"):
        raise AssertionError("help side effect: " + event)
sys.addaudithook(audit)
class Block:
    def find_spec(self, fullname, *args):
        if fullname.split(".")[0] in {"PIL", "cwb_style", "cwb_compare", "cwb_project", "cwb_stage", "cwb_export", "cwb_comfy", "krita", "PyQt6"}:
            raise AssertionError("help execution dependency: " + fullname)
sys.meta_path.insert(0, Block())
import cwb
for args in (["--help"], ["style", "--help"], *(["style", name, "--help"] for name in ("init", "show", "edit", "state", "prompt", "compare"))):
    try:
        cwb.main(args)
    except SystemExit as exc:
        assert exc.code == 0
'''
            env = dict(os.environ, HOME=tmp, CWB_WF_DIR=str(Path(tmp) / "absent"), PYTHONDONTWRITEBYTECODE="1")
            process = subprocess.run([sys.executable, "-B", "-S", "-c", code, str(ROOT / "core")], cwd=tmp,
                                     env=env, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_style_import_and_non_image_operations_need_no_pillow(self):
        code = '''import sys
sys.path.insert(0, sys.argv[1])
import cwb_style
assert "PIL" not in sys.modules
import cwb_project
from pathlib import Path
root = Path(sys.argv[2]) / "project"
cwb_project.initialize(root, "无Pillow")
cwb_style.initialize(root, actor="人工", reason="无依赖")
assert cwb_style.show(root)["latest"]["revision"] == 0
assert cwb_style.prompt(root, "character")["fragment"] == ""
assert "PIL" not in sys.modules
'''
        with tempfile.TemporaryDirectory(prefix="cad26-nopillow-") as tmp:
            process = subprocess.run([sys.executable, "-B", "-S", "-c", code, str(ROOT / "core"), tmp],
                                     cwd=tmp, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)

    def test_optional_pillow_missing_error_is_clear_and_never_installs(self):
        original = __import__
        def blocked(name, *args, **kwargs):
            if name == "PIL":
                raise ImportError("simulated optional dependency absence")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=blocked), self.assertRaisesRegex(ValueError, "CLI-only 可选 Pillow"):
            style._image_dimensions(b"not an image")


if __name__ == "__main__":
    unittest.main()
