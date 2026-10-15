"""CAD-22 teaching fixtures only: no model, mask extraction, or Krita evidence."""
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
import cwb_candidates as candidates

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None


class PlanTests(unittest.TestCase):
    def test_stable_eight_ids_seeds_directions_without_pillow(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(candidates, "_pillow", side_effect=AssertionError("PIL executed")):
                for output in ("a", "b"):
                    result = candidates.plan("教学，不证明模型有效", Path(root) / output, count=8,
                                             seed_start=100, seed_end=120,
                                             directions=["高窄结构（人工计划）", "低宽结构（人工计划）"],
                                             workflow_source="人工说明：未执行 workflow")
                    self.assertEqual(result["status"], "prepared_not_executed")
            a = (Path(root) / "a" / "manifest.json").read_bytes()
            self.assertEqual(a, (Path(root) / "b" / "manifest.json").read_bytes())
            manifest = json.loads(a)
            self.assertEqual([e["id"] for e in manifest["candidates"]], [f"C{i:03d}" for i in range(1, 9)])
            self.assertEqual([e["planned_seed"] for e in manifest["candidates"]], list(range(100, 108)))
            self.assertEqual(manifest["candidates"][2]["concept_direction"], "高窄结构（人工计划）")

    def test_unknowns_and_seed_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "a"
            candidates.plan("prompt", output, count=1, seed_start=0, seed_end=0)
            item = json.loads((output / "manifest.json").read_bytes())["candidates"][0]
            self.assertIsNone(item["workflow_source"])
            self.assertIsNone(item["concept_direction"])
            cases = [dict(count=0), dict(count=65), dict(count=True), dict(seed_start=-1),
                     dict(seed_end=2**64), dict(seed_end=0), dict(prompt=""), dict(prompt="x" * 8193)]
            for change in cases:
                args = dict(prompt="prompt", count=8, seed_start=1, seed_end=8)
                args.update(change)
                with self.subTest(change=change), self.assertRaises(ValueError):
                    candidates.plan(output=Path(root) / "bad", **args)
            self.assertFalse((Path(root) / "bad").exists())

    def test_old_output_and_partial_never_removed(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "a"
            output.mkdir()
            history = output / "history.txt"
            history.write_text("keep")
            with self.assertRaises(FileExistsError):
                candidates.plan("prompt", output, count=1, seed_start=0, seed_end=0)
            self.assertEqual(history.read_text(), "keep")
            partial = Path(root) / "partial"
            with patch.object(candidates, "_write_file", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    candidates.plan("prompt", partial, count=1, seed_start=0, seed_end=0)
            self.assertTrue(partial.is_dir())
            self.assertFalse((partial / "manifest.json").exists())
            with self.assertRaises(FileExistsError):
                candidates.plan("prompt", partial, count=1, seed_start=0, seed_end=0)

    def test_plan_publish_rejects_aliases_and_symlink_parents(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / "link").symlink_to(base, target_is_directory=True)
            for name in (str(base) + "/./bad", str(base) + "/../bad", str(base) + "//bad",
                         str(base) + "/bad/", str(base / "link" / "bad")):
                with self.subTest(name=name), self.assertRaises((ValueError, OSError)):
                    candidates.plan("prompt", name, count=1, seed_start=0, seed_end=0)
            self.assertFalse((base / "bad").exists())

    def test_help_all_levels_no_pil_network_or_cache(self):
        script = '''
import importlib.abc, os, runpy, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PIL" or fullname.startswith("PIL.") or fullname == "cwb_candidates":
            raise AssertionError("help loaded optional implementation: " + fullname)
sys.meta_path.insert(0, Block())
def audit(event, args):
    if event in ("os.mkdir", "socket.connect", "socket.bind", "urllib.Request"):
        raise AssertionError("help side effect: " + event)
sys.addaudithook(audit)
sys.argv = [sys.argv[1], *sys.argv[2:]]
runpy.run_path(sys.argv[0], run_name="__main__")
'''
        with tempfile.TemporaryDirectory() as root:
            for args in (["--help"], ["candidates", "--help"], ["candidates", "plan", "--help"],
                         ["candidates", "board", "--help"]):
                result = subprocess.run([sys.executable, "-B", "-c", script, str(ROOT / "core" / "cwb.py"), *args],
                                        cwd=root, env=dict(os.environ, HOME=root, CWB_WF_DIR=root),
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)
                self.assertEqual(list(Path(root).iterdir()), [])

    def test_missing_pillow_clear_error(self):
        import builtins
        original = builtins.__import__
        def blocked(name, *args, **kwargs):
            if name == "PIL":
                raise ImportError("deliberately absent")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=blocked):
            with self.assertRaisesRegex(ValueError, "可选 Pillow"):
                candidates._pillow()


@unittest.skipIf(Image is None, "optional CLI Pillow not installed")
class BoardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cad22-teaching-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = self.root / "plan" / "manifest.json"
        self.registration = self.root / "registration.json"
        self.output = self.root / "board"
        candidates.plan("教学几何图，不是模型候选", self.plan.parent, count=8,
                        seed_start=200, seed_end=207, directions=["人工高窄", "人工低宽"],
                        workflow_source="planned-only-never-executed")
        self.entries = []
        for index in range(8):
            path = self.root / f"fixture-{index + 1}.png"
            size = (20, 40) if index % 2 == 0 else (40, 20)
            self.png(path, size, alpha=128 if index == 0 else 255)
            self.entries.append(dict(id=f"C{index + 1:03d}", status="ready", png=str(path),
                                     sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                     seed=None if index == 0 else index, description=f"人工教学 fixture {index + 1}",
                                     workflow_source=None, concept_direction=None, failure=None))
        self.save_registration()

    def png(self, path, size=(20, 40), alpha=255, color=(0, 0, 0)):
        with Image.new("RGBA", size, (0, 0, 0, 0)) as image:
            draw = ImageDraw.Draw(image)
            draw.rectangle((size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4),
                           fill=(*color, alpha))
            image.save(path)

    def save_registration(self):
        self.registration.write_text(json.dumps(dict(schema=candidates.REGISTRY_SCHEMA, candidates=self.entries),
                                                ensure_ascii=False), encoding="utf-8")

    def update_png(self, index, path):
        self.entries[index]["png"] = str(path)
        self.entries[index]["sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        self.save_registration()

    def build(self, **kwargs):
        return candidates.board(self.plan, self.registration, self.output, cell_width=80, cell_height=80, **kwargs)

    def manifest(self):
        return json.loads((self.output / "manifest.json").read_bytes())

    def test_eight_fixture_contain_numbering_alpha_and_source_hash(self):
        originals = {e["png"]: Path(e["png"]).read_bytes() for e in self.entries}
        result = self.build(selected=["C001", "C008"], parent_ref="manual-unverified-group")
        self.assertEqual(result["failures"], 0)
        manifest = self.manifest()
        self.assertEqual(manifest["display"]["dimensions"], [320, 208])
        for index, entry in enumerate(manifest["candidates"]):
            x, y = index % 4 * 80, index // 4 * 104 + 24
            self.assertEqual(entry["cell_xywh"], [x, y, 80, 80])
            expected = [x + 20, y, 40, 80] if index % 2 == 0 else [x, y + 20, 80, 40]
            self.assertEqual(entry["rendered_xywh"], expected)
            self.assertEqual(entry["fit"], "contain")
            self.assertEqual(entry["source"]["sha256"], self.entries[index]["sha256"])
            self.assertEqual((self.output / entry["asset"]).read_bytes(), originals[entry["png"]])
            self.assertIsNone(entry["workflow_source"])
            self.assertIsNone(entry["concept_direction"])
            self.assertEqual(Path(entry["png"]).read_bytes(), originals[entry["png"]])
        self.assertIsNone(manifest["candidates"][0]["seed"])
        self.assertEqual(manifest["planned_candidates"][0]["planned_seed"], 200)
        with Image.open(self.output / "board.png") as board:
            self.assertEqual(board.getpixel((40, 64)), (127, 127, 127, 255))
            self.assertEqual(board.getpixel((0, 24)), (255, 255, 255, 255))
            self.assertEqual(board.getpixel((20, 24)), (255, 255, 255, 255))
            self.assertEqual(board.getpixel((0, 0))[3], 255)
        with Image.open(self.output / "C001.png") as original_png:
            self.assertEqual(original_png.getpixel((10, 20)), (0, 0, 0, 128))
            self.assertEqual(original_png.getpixel((0, 0))[3], 0)
        self.assertEqual(manifest["display"]["background"], "opaque_white")
        selection = json.loads((self.output / "selection.json").read_bytes())
        self.assertFalse(selection["krita_applied"])
        self.assertFalse(selection["parent_ref_verified"])
        self.assertEqual(selection["selected"], ["C001", "C008"])
        self.assertEqual(selection["new_layer_payloads"][0]["parent_id"], "manual-unverified-group")
        self.assertEqual(selection["new_layer_payloads"][0]["sha256"], self.entries[0]["sha256"])

    def test_registered_and_invalid_png_failures_keep_slots_nonzero_cli(self):
        self.entries[1].update(status="failed", png=None, sha256=None, failure="人工：分割失败，等待圈选")
        bad = self.root / "bad.png"
        bad.write_bytes(b"not a PNG")
        self.update_png(3, bad)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                cwb.main(["candidates", "board", str(self.plan), str(self.registration), str(self.output),
                          "--cell-width", "80", "--cell-height", "80"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("created_with_failures", output.getvalue())
        manifest = self.manifest()
        self.assertEqual(manifest["failures"], 2)
        self.assertEqual(len(manifest["candidates"]), 8)
        self.assertEqual(manifest["candidates"][1]["failure"], "人工：分割失败，等待圈选")
        self.assertEqual(manifest["candidates"][3]["status"], "failed")
        self.assertIsNotNone(manifest["candidates"][3]["source"])
        with Image.open(self.output / "board.png") as board:
            self.assertEqual(board.getpixel((90, 24)), (235, 220, 220, 255))
        self.assertFalse((self.output / "C002.png").exists())
        self.assertTrue((self.output / "C008.png").exists())

    def test_no_blackening_or_mask_algorithm(self):
        path = Path(self.entries[0]["png"])
        self.png(path, color=(1, 0, 0))
        self.update_png(0, path)
        original = path.read_bytes()
        result = self.build()
        self.assertEqual(result["failures"], 1)
        self.assertIn("不会执行剪影/分割算法", self.manifest()["candidates"][0]["failure"])
        self.assertEqual(path.read_bytes(), original)

    def test_opaque_and_empty_images_are_failures(self):
        for index, color in ((0, (0, 0, 0, 255)), (1, (0, 0, 0, 0))):
            path = Path(self.entries[index]["png"])
            with Image.new("RGBA", (10, 10), color) as image:
                image.save(path)
            self.update_png(index, path)
        self.assertEqual(self.build()["failures"], 2)

    def test_source_sha_change_aborts_not_placeholder(self):
        path = Path(self.entries[0]["png"])
        self.png(path, (12, 13))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertTrue(path.exists())

    def test_source_change_during_render_and_final_write_is_detected(self):
        path = Path(self.entries[0]["png"])
        original = candidates._decode
        def mutate(*args):
            image = original(*args)
            self.png(path, (11, 12))
            return image
        with patch.object(candidates, "_decode", side_effect=mutate):
            with self.assertRaises((ValueError, OSError)):
                self.build()
        self.assertFalse(self.output.exists())
        self.update_png(0, path)
        writer = candidates._write_file
        def change_after_write(fd, name, data):
            writer(fd, name, data)
            if name == "board.png":
                self.png(path, (15, 19))
        with patch.object(candidates, "_write_file", side_effect=change_after_write):
            with self.assertRaises((ValueError, OSError)):
                self.build()
        self.assertTrue(self.output.is_dir())
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertTrue((self.output / "board.png").exists())
        self.assertTrue(path.exists())

    def test_registry_change_during_render_detected(self):
        original = candidates._decode
        def mutate(*args):
            image = original(*args)
            self.registration.write_text("changed")
            return image
        with patch.object(candidates, "_decode", side_effect=mutate):
            with self.assertRaises((ValueError, OSError)):
                self.build()
        self.assertFalse(self.output.exists())

    def test_malicious_sources_symlink_hardlink_fifo_duplicate_alias(self):
        original = self.entries[0]["png"]
        symlink = self.root / "source-link.png"
        symlink.symlink_to(original)
        hardlink = self.root / "source-hard.png"
        os.link(original, hardlink)
        # A hard link makes both names unsafe. Use another source for remaining checks.
        with self.assertRaises(ValueError):
            self.build()
        hardlink.unlink()
        fifo = self.root / "source-fifo.png"
        os.mkfifo(fifo)
        linkdir = self.root / "linkdir"
        linkdir.symlink_to(self.root, target_is_directory=True)
        paths = [str(symlink), str(fifo), str(self.root) + "/./fixture-1.png",
                 str(self.root) + "//fixture-1.png", str(self.root / "x" / ".." / "fixture-1.png"),
                 str(linkdir / "fixture-1.png"), self.entries[1]["png"]]
        for path in paths:
            self.entries[0]["png"] = path
            if path == self.entries[1]["png"]:
                self.entries[0]["sha256"] = self.entries[1]["sha256"]
            self.save_registration()
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                self.build()
            self.assertFalse(self.output.exists())
        self.assertTrue(Path(original).exists())

    def test_old_outputs_symlinks_files_not_overwritten(self):
        for mode in ("directory", "file", "symlink"):
            output = self.root / ("existing-" + mode)
            if mode == "directory":
                output.mkdir()
                (output / "history").write_text("keep")
            elif mode == "file":
                output.write_text("keep")
            else:
                output.symlink_to(self.root, target_is_directory=True)
            with self.subTest(mode=mode), self.assertRaises(FileExistsError):
                candidates.board(self.plan, self.registration, output)
            self.assertTrue(output.exists())
        self.assertEqual((self.root / "existing-directory" / "history").read_text(), "keep")
        self.assertEqual((self.root / "existing-file").read_text(), "keep")

    def test_explicit_registry_and_selection_validation(self):
        baseline = json.loads(self.registration.read_text())
        variants = []
        missing = json.loads(json.dumps(baseline)); missing["candidates"].pop(); variants.append(missing)
        duplicate = json.loads(json.dumps(baseline)); duplicate["candidates"][1]["id"] = "C001"; variants.append(duplicate)
        unknown = json.loads(json.dumps(baseline)); unknown["candidates"][0]["id"] = "../evil"; variants.append(unknown)
        seed = json.loads(json.dumps(baseline)); del seed["candidates"][0]["seed"]; variants.append(seed)
        invalid = json.loads(json.dumps(baseline)); invalid["candidates"][0]["seed"] = True; variants.append(invalid)
        for variant in variants:
            self.registration.write_text(json.dumps(variant))
            with self.assertRaises(ValueError):
                self.build()
            self.assertFalse(self.output.exists())
        self.registration.write_text(json.dumps(baseline))
        for selected in (["C001", "C001"], ["C999"]):
            with self.assertRaises(ValueError):
                self.build(selected=selected)
        self.entries[0].update(status="failed", png=None, sha256=None, failure="未生成")
        self.save_registration()
        with self.assertRaisesRegex(ValueError, "不能选择失败"):
            self.build(selected=["C001"])
        self.assertFalse(self.output.exists())

    def test_input_json_duplicate_keys_and_tampered_plan(self):
        saved = self.plan.read_text()
        changed = json.loads(saved)
        for seed in (201, 200.0):
            changed["candidates"][0]["planned_seed"] = seed
            self.plan.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                self.build()
        self.plan.write_text(saved)
        self.registration.write_text('{"schema":"a","schema":"b","candidates":[]}')
        with self.assertRaisesRegex(ValueError, "重复字段"):
            self.build()

    def test_resource_limits_before_output(self):
        for options in (dict(columns=0), dict(columns=65), dict(cell_width=31),
                        dict(cell_width=2049), dict(cell_width=2048, cell_height=2048, columns=8)):
            kwargs = dict(cell_width=80, cell_height=80)
            kwargs.update(options)
            with self.subTest(options=options), self.assertRaises(ValueError):
                candidates.board(self.plan, self.registration, self.output, **kwargs)
        with patch.object(candidates, "MAX_FILE", 1), self.assertRaises(ValueError):
            self.build()
        with patch.object(candidates, "MAX_TOTAL_BYTES", 1), self.assertRaises(ValueError):
            self.build()
        with patch.object(candidates, "MAX_TOTAL_PIXELS", 100), self.assertRaises(ValueError):
            self.build()
        with patch.object(candidates, "MAX_IMAGE_PIXELS", 100):
            result = self.build()
        self.assertEqual(result["failures"], 8)
        self.assertEqual(len(self.manifest()["candidates"]), 8)

    def test_plan_file_change_during_render_detected(self):
        original = candidates._decode
        def mutate(*args):
            image = original(*args)
            self.plan.write_text("changed plan")
            return image
        with patch.object(candidates, "_decode", side_effect=mutate):
            with self.assertRaises((ValueError, OSError)):
                self.build()
        self.assertFalse(self.output.exists())

    def test_metadata_paths_are_regular_single_link_files(self):
        link = self.root / "registry-link.json"
        link.symlink_to(self.registration)
        with self.assertRaises(OSError):
            candidates.board(self.plan, link, self.output)
        hardlink = self.root / "plan-hard.json"
        os.link(self.plan, hardlink)
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_output_replacement_fails_without_deleting_either_directory(self):
        writer = candidates._write_file
        archived = self.root / "interrupted-original-board"
        def replace_output(fd, name, data):
            writer(fd, name, data)
            if name == "board.png":
                self.output.rename(archived)
                self.output.mkdir()
                (self.output / "history.txt").write_text("untouched replacement")
        with patch.object(candidates, "_write_file", side_effect=replace_output):
            with self.assertRaisesRegex(ValueError, "输出目录"):
                self.build()
        self.assertTrue((archived / "board.png").exists())
        self.assertFalse((archived / "manifest.json").exists())
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertEqual((self.output / "history.txt").read_text(), "untouched replacement")

    def test_write_error_preserves_sources_and_partial_assets(self):
        writer = candidates._write_file
        original = Path(self.entries[0]["png"]).read_bytes()
        def disk_error(fd, name, data):
            if name == "board.png":
                raise OSError("simulated disk full")
            writer(fd, name, data)
        with patch.object(candidates, "_write_file", side_effect=disk_error):
            with self.assertRaises(OSError):
                self.build()
        self.assertTrue((self.output / "C001.png").exists())
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertEqual(Path(self.entries[0]["png"]).read_bytes(), original)

    def test_deterministic_board_and_no_services(self):
        with patch.object(cwb, "http", side_effect=AssertionError("service called")):
            with contextlib.redirect_stdout(io.StringIO()):
                cwb.main(["candidates", "board", str(self.plan), str(self.registration), str(self.output)])
        other = self.root / "other"
        candidates.board(self.plan, self.registration, other)
        self.assertEqual((self.output / "board.png").read_bytes(), (other / "board.png").read_bytes())
        self.assertEqual((self.output / "manifest.json").read_bytes(), (other / "manifest.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
