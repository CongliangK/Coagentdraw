"""CAD-23 real offline PNG tests; no Krita, model or decision-store integration."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
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
import cwb_compare as compare

try:
    from PIL import Image
except ImportError:
    Image = None


@unittest.skipIf(Image is None, "optional external CLI Pillow unavailable")
class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cad23-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.before = self.root / "before.png"
        self.after = self.root / "after.png"
        self.output = self.root / "comparison"
        self.png(self.before, (4, 3), (255, 0, 0, 255))
        self.png(self.after, (4, 3), (0, 200, 255, 80))

    def png(self, path, size, color):
        image = Image.new("RGBA", size, color)
        image.save(path)
        image.close()

    def create(self, **kwargs):
        return compare.create(self.before, self.after, self.output, **kwargs)

    def test_normal_png_hash_provenance_and_original_bytes(self):
        self.create(source_version="manual-v7", candidate_version="candidate-v1",
                    prompt="手工重绘关联", workflow="unknown-local-workflow", seed=0,
                    document_ref="manual.kra", candidate_layer_ref="unverified-id")
        data = compare.show(self.output)
        manifest = data["manifest"]
        self.assertEqual(data["state"], "pending")
        self.assertEqual(data["events"], [])
        for key, source in (("before", self.before), ("after", self.after)):
            saved = manifest["sources"][key]
            self.assertEqual(saved["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(saved["dimensions"], [4, 3])
            self.assertEqual(saved["path"], str(source))
            self.assertEqual((self.output / saved["asset"]).read_bytes(), source.read_bytes())
        self.assertEqual(manifest["sources"]["before"]["source_version"], "manual-v7")
        self.assertEqual(manifest["sources"]["after"]["source_version"], "candidate-v1")
        self.assertEqual(manifest["provenance"]["seed"], 0)
        self.assertFalse(manifest["krita_applied"])
        self.assertFalse(manifest["decision_store_integrated"])
        with Image.open(self.output / "comparison.png") as image:
            self.assertEqual(image.size, (8, 23))
            self.assertEqual(image.getpixel((0, 20)), (255, 0, 0, 255))
            self.assertEqual(image.getpixel((4, 20)), (0, 200, 255, 80))

    def test_unknown_fields_are_null(self):
        self.create()
        manifest = compare.show(self.output)["manifest"]
        for key in ("prompt", "workflow", "seed", "document_ref", "candidate_layer_ref"):
            self.assertIsNone(manifest["provenance"][key])
        self.assertIsNone(manifest["sources"]["before"]["source_version"])
        self.assertIsNone(manifest["sources"]["after"]["source_version"])
        self.assertIn("no generation performed", manifest["provenance"]["evidence"])

    def test_common_crop_pixel_mapping_and_alpha(self):
        with Image.open(self.before) as image:
            image.putpixel((1, 1), (5, 6, 7, 0))
            image.save(self.before)
        self.create(crop=(1, 1, 2, 1))
        data = compare.show(self.output)["manifest"]["display"]
        self.assertEqual(data["crop_xywh"], [1, 1, 2, 1])
        self.assertEqual(data["scale"], 1)
        self.assertEqual(data["panel_boxes_xywh"], [[0, 20, 2, 1], [2, 20, 2, 1]])
        with Image.open(self.output / "comparison.png") as image:
            self.assertEqual(image.size, (4, 21))
            self.assertEqual(image.getpixel((0, 20)), (5, 6, 7, 0))
            self.assertEqual(image.getpixel((2, 20)), (0, 200, 255, 80))

    def test_render_is_deterministic(self):
        self.create()
        other = self.root / "other"
        compare.create(self.before, self.after, other)
        self.assertEqual((other / "comparison.png").read_bytes(), (self.output / "comparison.png").read_bytes())

    def test_dimension_mismatch_even_with_crop_fails(self):
        self.png(self.after, (5, 3), (0, 0, 0, 0))
        for crop in (None, (0, 0, 1, 1)):
            with self.subTest(crop=crop), self.assertRaisesRegex(ValueError, "尺寸不匹配"):
                self.create(crop=crop)
            self.assertFalse(self.output.exists())

    def test_crop_invalid_and_bounds_fail(self):
        for crop in ((-1, 0, 2, 2), (0, 0, 0, 1), (0, 0, 1, -1), (3, 0, 2, 1),
                     (0, 2, 1, 2), (0, 0, 1), (False, 0, 1, 1)):
            with self.subTest(crop=crop), self.assertRaises(ValueError):
                self.create(crop=crop)
            self.assertFalse(self.output.exists())

    def test_invalid_png_format_animation_and_pixel_limit(self):
        for data in (b"not an image", b"\x89PNG\r\n\x1a\n"):
            self.after.write_bytes(data)
            with self.assertRaises((OSError, ValueError)):
                self.create()
            self.assertFalse(self.output.exists())
        Image.new("RGB", (4, 3)).save(self.after, format="JPEG")
        with self.assertRaisesRegex(ValueError, "单帧 PNG"):
            self.create()
        a, b = Image.new("RGBA", (4, 3), "red"), Image.new("RGBA", (4, 3), "blue")
        a.save(self.after, format="PNG", save_all=True, append_images=[b], duration=100)
        a.close()
        b.close()
        with self.assertRaisesRegex(ValueError, "单帧 PNG"):
            self.create()
        self.png(self.after, (4, 3), (1, 2, 3, 4))
        with patch.object(compare, "MAX_PIXELS", 1), self.assertRaisesRegex(ValueError, "像素数"):
            self.create()
        self.assertFalse(self.output.exists())

    def test_16bit_png_is_explicitly_rejected(self):
        image = Image.new("I;16", (4, 3))
        image.save(self.after)
        image.close()
        with self.assertRaisesRegex(ValueError, "16 位"):
            self.create()
        self.assertFalse(self.output.exists())

    def test_fsync_failure_cleans_partial_output_and_event(self):
        with patch.object(compare.os, "fsync", side_effect=OSError("injected fsync failure")), self.assertRaises(OSError):
            self.create()
        self.assertFalse(self.output.exists())
        self.create()
        compare.decide(self.output, "accept", "first", 0)
        with patch.object(compare.os, "fsync", side_effect=OSError("injected fsync failure")), self.assertRaises(OSError):
            compare.decide(self.output, "reject", "failed", 1)
        self.assertEqual(compare.show(self.output)["sequence"], 1)
        self.assertFalse((self.output / "event-000002.json").exists())

    def test_source_mutation_during_render_fails_before_publication(self):
        render = compare._render
        def mutate(*args):
            result = render(*args)
            self.before.write_bytes(self.before.read_bytes() + b"changed")
            return result
        with patch.object(compare, "_render", side_effect=mutate), self.assertRaisesRegex(ValueError, "源文件改变"):
            self.create()
        self.assertFalse(self.output.exists())

    def test_source_mutation_during_output_cleans_all(self):
        write = compare._write_at
        def mutate(fd, name, data):
            write(fd, name, data)
            if name == "compare.json":
                self.after.write_bytes(self.after.read_bytes() + b"changed")
        with patch.object(compare, "_write_at", side_effect=mutate), self.assertRaises(ValueError):
            self.create()
        self.assertFalse(self.output.exists())

    def test_failed_write_cleans_new_directory_without_touching_inputs(self):
        original = self.before.read_bytes(), self.after.read_bytes()
        write = compare._write_at
        def fail(fd, name, data):
            if name == "comparison.png":
                raise OSError("injected disk failure")
            write(fd, name, data)
        with patch.object(compare, "_write_at", side_effect=fail), self.assertRaises(OSError):
            self.create()
        self.assertFalse(self.output.exists())
        self.assertEqual(original, (self.before.read_bytes(), self.after.read_bytes()))

    def test_events_reopen_reasons_and_nondestructive_assets(self):
        self.create()
        original = {name: (self.output / name).read_bytes() for name in ("before.png", "after.png", "comparison.png", "compare.json")}
        for sequence, action in enumerate(("accept", "hide", "reject", "withdraw")):
            result = compare.decide(self.output, action, "reason-" + action, sequence)
            self.assertFalse(result["event"]["krita_applied"])
            self.assertIn("仅离线记录", result["notice"])
            # Fresh CLI process is the reopen evidence, not just in-memory state.
            process = subprocess.run([sys.executable, "-B", str(ROOT / "core/cwb.py"), "compare", "show", str(self.output)],
                                     cwd=self.root, capture_output=True, text=True, check=True)
            data = json.loads(process.stdout)
            self.assertEqual(data["sequence"], sequence + 1)
            self.assertEqual(data["events"][-1]["reason"], "reason-" + action)
            self.assertEqual(data["state"], compare.STATES[action])
        self.assertEqual([event["action"] for event in compare.show(self.output)["events"]], ["accept", "hide", "reject", "withdraw"])
        for name, raw in original.items():
            self.assertEqual((self.output / name).read_bytes(), raw)
        self.assertTrue(self.after.exists())

    def test_stale_sequence_empty_reason_and_noop_do_not_append(self):
        self.create()
        compare.decide(self.output, "reject", "保留候选但不选择", 0)
        for action, reason, sequence in (("accept", "stale", 0), ("reject", "repeat", 1),
                                         ("accept", "  ", 1), ("delete", "invalid", 1),
                                         ("accept", "invalid", True)):
            with self.subTest(action=action, reason=reason), self.assertRaises(ValueError):
                compare.decide(self.output, action, reason, sequence)
        self.assertEqual(compare.show(self.output)["sequence"], 1)
        self.assertFalse((self.output / "event-000002.json").exists())

    def test_concurrent_decisions_same_sequence_only_one_wins(self):
        self.create()
        def record(action):
            try:
                compare.decide(self.output, action, "race", 0)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(record, ("accept", "reject")))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(compare.show(self.output)["sequence"], 1)

    def test_changed_original_blocks_decide_but_saved_query_still_works(self):
        self.create()
        self.after.write_bytes(b"different source")
        with self.assertRaisesRegex(ValueError, "源文件已改变"):
            compare.decide(self.output, "accept", "old comparison", 0)
        self.assertEqual(compare.show(self.output)["state"], "pending")
        self.before.unlink()
        self.assertEqual(compare.show(self.output)["sequence"], 0)

    def test_change_during_decision_removes_only_new_event(self):
        self.create()
        compare.decide(self.output, "accept", "first", 0)
        check = compare._unchanged
        def mutate(captures):
            self.after.write_bytes(self.after.read_bytes() + b"changed")
            check(captures)
        with patch.object(compare, "_unchanged", side_effect=mutate), self.assertRaises(ValueError):
            compare.decide(self.output, "reject", "will fail", 1)
        self.assertEqual(compare.show(self.output)["sequence"], 1)
        self.assertFalse((self.output / "event-000002.json").exists())

    def test_tampered_asset_and_event_order_fail_closed(self):
        self.create()
        compare.decide(self.output, "accept", "one", 0)
        event = self.output / "event-000001.json"
        old = event.read_bytes()
        event.unlink()
        (self.output / "event-000002.json").write_bytes(old)
        with self.assertRaisesRegex(ValueError, "序号"):
            compare.show(self.output)
        (self.output / "event-000002.json").unlink()
        event.write_bytes(old)
        (self.output / "after.png").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "摘要不匹配"):
            compare.show(self.output)

    def test_event_chain_previous_state_and_hash_are_checked(self):
        self.create()
        compare.decide(self.output, "accept", "one", 0)
        compare.decide(self.output, "reject", "two", 1)
        path = self.output / "event-000001.json"
        event = json.loads(path.read_text())
        event["reason"] = "edited reason"
        path.write_text(json.dumps(event))
        with self.assertRaisesRegex(ValueError, "事件链"):
            compare.show(self.output)

    def test_existing_outputs_and_input_output_alias_never_overwrite(self):
        original = self.before.read_bytes()
        for output in (self.before, self.after):
            with self.assertRaises(FileExistsError):
                compare.create(self.before, self.after, output)
        self.create()
        saved = (self.output / "compare.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(saved, (self.output / "compare.json").read_bytes())
        self.assertEqual(original, self.before.read_bytes())

    def test_same_input_and_lexical_alias_fail(self):
        with self.assertRaisesRegex(ValueError, "同一文件"):
            compare.create(self.before, self.before, self.output)
        for path in (str(self.root) + "/./before.png", str(self.root) + "/x/../before.png", str(self.root) + "//before.png"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                compare.create(path, self.after, self.output)
        self.assertFalse(self.output.exists())

    def test_symlink_file_parent_and_output_rejected(self):
        alias = self.root / "alias.png"
        alias.symlink_to(self.before)
        parent_alias = self.root / "linked"
        parent_alias.symlink_to(self.root, target_is_directory=True)
        for source in (alias, parent_alias / "before.png"):
            with self.subTest(source=source), self.assertRaises(OSError):
                compare.create(source, self.after, self.output)
        self.output.symlink_to(self.before)
        with self.assertRaises(FileExistsError):
            self.create()
        with self.assertRaises(OSError):
            compare.create(self.before, self.after, parent_alias / "new")
        self.assertFalse((self.root / "new").exists())

    def test_hardlinks_fifo_directory_and_large_input_fail(self):
        alias = self.root / "hard.png"
        os.link(self.before, alias)
        with self.assertRaisesRegex(ValueError, "硬链接"):
            self.create()
        alias.unlink()
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        for path in (fifo, self.root):
            with self.subTest(path=path), self.assertRaises(ValueError):
                compare.create(path, self.after, self.output)
        with patch.object(compare, "MAX_BYTES", 1), self.assertRaises(ValueError):
            self.create()
        self.assertFalse(self.output.exists())

    def test_sidecar_and_event_symlink_refused_without_modifying_target(self):
        self.create()
        compare.decide(self.output, "accept", "one", 0)
        event = self.output / "event-000001.json"
        event.unlink()
        event.symlink_to(self.before)
        original = self.before.read_bytes()
        with self.assertRaises(OSError):
            compare.show(self.output)
        self.assertEqual(original, self.before.read_bytes())
        event.unlink()
        manifest = self.output / "compare.json"
        manifest.unlink()
        manifest.symlink_to(self.before)
        with self.assertRaises(OSError):
            compare.decide(self.output, "accept", "one", 0)
        self.assertEqual(original, self.before.read_bytes())

    def test_malformed_manifest_fails_without_event_or_target_change(self):
        self.create()
        manifest = self.output / "compare.json"
        original = manifest.read_bytes()
        bad_uuid = json.loads(original)
        bad_uuid["comparison_id"] = 17
        for data in (b"[]", b'{"schema":1,"schema":2}', json.dumps(bad_uuid).encode()):
            manifest.write_bytes(data)
            with self.assertRaises(ValueError):
                compare.decide(self.output, "accept", "malformed", 0)
            self.assertFalse((self.output / "event-000001.json").exists())
            self.assertEqual(manifest.read_bytes(), data)
        manifest.write_bytes(original)
        self.assertEqual(compare.show(self.output)["sequence"], 0)

    def test_cli_reports_record_only(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cwb.main(["compare", "create", str(self.before), str(self.after), str(self.output)])
            cwb.main(["compare", "decide", str(self.output), "hide", "--reason", "仅离线", "--expect-sequence", "0"])
        self.assertIn("offline_record_only", stdout.getvalue())
        self.assertIn("未改变 Krita", stdout.getvalue())


class HelpTests(unittest.TestCase):
    def test_import_and_all_help_without_pillow_network_or_cache(self):
        with tempfile.TemporaryDirectory(prefix="cad23-help-") as tmp:
            code = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
def audit(event, args):
    if event.startswith("socket.") or event == "os.mkdir":
        raise AssertionError("help attempted side effect: " + event)
sys.addaudithook(audit)
import cwb_compare
assert "PIL" not in sys.modules
import cwb
for args in (["--help"], ["compare", "--help"], ["compare", "create", "--help"], ["compare", "show", "--help"], ["compare", "decide", "--help"]):
    try:
        cwb.main(args)
    except SystemExit as exc:
        assert exc.code == 0
assert "PIL" not in sys.modules
'''
            env = dict(os.environ, HOME=tmp, CWB_WF_DIR=str(Path(tmp) / "absent"), PYTHONDONTWRITEBYTECODE="1")
            process = subprocess.run([sys.executable, "-B", "-S", "-c", code, str(ROOT / "core")],
                                     cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_optional_dependency_error_is_clear(self):
        original = __import__
        def blocked(name, *args, **kwargs):
            if name == "PIL":
                raise ImportError("simulated optional dependency absence")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=blocked), self.assertRaisesRegex(ValueError, "可选 Pillow"):
            compare._pillow()


if __name__ == "__main__":
    unittest.main()
