"""CAD-24 synthetic fixtures only: structure/safety, never artistic validity."""
import copy
import hashlib
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
import cwb_review as review

try:
    from PIL import Image
except ImportError:
    Image = None


class ReviewHelpTests(unittest.TestCase):
    def test_help_without_optional_modules_network_or_cache(self):
        script = '''
import builtins, sys
sys.path.insert(0, sys.argv.pop(1))
original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('PIL', 'cwb_review', 'cwb_compare')):
        raise AssertionError('help imported optional/review module: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
import cwb
cwb.http = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network forbidden'))
cwb.main(sys.argv[1:])
'''
        with tempfile.TemporaryDirectory(prefix="cad24-help-") as root:
            env = dict(os.environ, CWB_WF_DIR=str(Path(root) / "nonexistent"), PYTHONDONTWRITEBYTECODE="1")
            for args in (["--help"], ["review", "--help"], ["review", "prepare", "--help"], ["review", "import", "--help"]):
                with self.subTest(args=args):
                    result = subprocess.run([sys.executable, "-S", "-c", script, str(ROOT / "core"), *args],
                                            cwd=root, env=env, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage:", result.stdout)
            self.assertEqual(list(Path(root).iterdir()), [])


@unittest.skipIf(Image is None, "optional CLI Pillow unavailable")
class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cad24-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.images = [self.root / (role + ".png") for role in review.ROLES]
        for index, path in enumerate(self.images):
            with Image.new("RGB", (12, 10), (index * 70, index * 70, index * 70)) as image:
                image.save(path)
        self.context = self.root / "context.json"
        self.dump(self.context, {"intent": "合成几何样例；不是角色作品", "pillars": [{"id": "P1", "text": "测试可读性"}]})
        self.prepared = self.root / "prepared"
        self.feedback = self.root / "manual.json"
        self.output = self.root / "imported"

    def dump(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def prepare(self, **kwargs):
        values = dict(original=self.images[0], silhouette=self.images[1], grayscale=self.images[2],
                      context=self.context, output=self.prepared)
        values.update(kwargs)
        return review.prepare(**values)

    def load(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def opinion(self, ident="R1", classification="suggestion", claim_type="hypothesis"):
        return dict(id=ident, bbox_xywh=[1, 2, 3, 4], pillar_ids=["P1"],
                    evidence={"observations": "合成矩形区域为纯灰色（fixture 文本，未审评）", "source_roles": ["original", "grayscale"],
                              "inference": "仅用于验证结构，非艺术结论"},
                    uncertainty={"level": "high", "reason": "没有真实作品/人工对照"},
                    impact="合成影响描述", action="人工检查矩形边缘（不执行）", priority="P2",
                    classification=classification, claim_type=claim_type, reason="fixture 分类理由")

    def response(self):
        self.prepare()
        request = self.load(self.prepared / "request.json")
        return dict(schema=review.RESPONSE_SCHEMA, request_id=request["request_id"],
                    request_sha256=hashlib.sha256((self.prepared / "request.json").read_bytes()).hexdigest(),
                    source_sha256={r: request["images"][r]["sha256"] for r in review.ROLES},
                    origin={"kind": "human", "name": "synthetic fixture author", "reference": "fixture:not-real-character"},
                    opinions=[self.opinion()], decisions=[])

    def do_import(self, value):
        self.dump(self.feedback, value)
        return review.import_feedback(self.prepared, self.feedback, self.output)

    def rejected(self, value):
        with self.assertRaises((ValueError, OSError, TypeError)):
            self.do_import(value)
        self.assertFalse(self.output.exists())

    def test_prepare_roles_dimensions_sha_status_unchanged_sources(self):
        before = [p.read_bytes() for p in self.images]
        result = self.prepare()
        request = self.load(self.prepared / "request.json")
        self.assertEqual(result["status"], "prepared_not_executed")
        self.assertEqual(request["feedback_contract"], review.CONTRACT)
        for role, original, data in zip(review.ROLES, self.images, before):
            record = request["images"][role]
            self.assertEqual(record["dimensions"], [12, 10])
            self.assertEqual(record["path"], str(original))
            self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual((self.prepared / (role + ".png")).read_bytes(), data)
            self.assertEqual(original.read_bytes(), data)
        self.assertIn("不得为了凑三条", request["prompt"])
        self.assertTrue((self.prepared / "complete.json").exists())

    def test_fixture_three_categories_and_human_confirm_amend_reject(self):
        value = self.response()
        value["origin"]["kind"] = "model"  # explicitly supplied; never an actual backend call
        value["opinions"] = [self.opinion("R1", "must_change", "intent_conflict"), self.opinion("R2"),
                             self.opinion("R3", "personal_preference", "subjective_preference")]
        amendment = copy.deepcopy(value["opinions"][1])
        amendment["action"] = "手工修订动作，仅记录"
        value["decisions"] = [dict(opinion_id="R1", action="confirm", actor="fixture-human", reason="确认结构"),
                              dict(opinion_id="R2", action="amend", actor="fixture-human", reason="修改措辞", amended_opinion=amendment),
                              dict(opinion_id="R3", action="reject", actor="fixture-human", reason="偏好不采用")]
        raw_sources = [p.read_bytes() for p in self.images]
        result = self.do_import(value)
        report = self.load(self.output / "review.json")
        self.assertEqual(result["status"], "response_user_supplied")
        self.assertEqual([x["state"] for x in report["opinions"]], ["confirmed", "amended", "rejected"])
        self.assertEqual(report["origin"], value["origin"])
        self.assertEqual(report["origin_verification"], "user_supplied_unverified")
        for item, original, decision in zip(report["opinions"], value["opinions"], value["decisions"]):
            self.assertEqual(item["original_opinion"], original)
            self.assertEqual(item["decision_history"], [decision])
        self.assertEqual(report["opinions"][1]["effective_opinion"], amendment)
        self.assertFalse(report["backend_executed"])
        self.assertFalse(report["artwork_modified"])
        self.assertFalse(report["generated_prompt"])
        self.assertEqual((self.output / "supplied-feedback.json").read_bytes(), self.feedback.read_bytes())
        self.assertEqual([p.read_bytes() for p in self.images], raw_sources)
        text = (self.output / "review.md").read_text()
        for marker in ("R1", "R2", "R3", "confirmed", "amended", "rejected", "原意见", "未验证"):
            self.assertIn(marker, text)

    def test_pending_empty_and_no_three_opinion_quota(self):
        value = self.response()
        self.do_import(value)
        self.assertEqual(self.load(self.output / "review.json")["opinions"][0]["state"], "pending")
        value["opinions"] = []
        self.dump(self.feedback, value)
        other = self.root / "empty"
        review.import_feedback(self.prepared, self.feedback, other)
        self.assertEqual(self.load(other / "review.json")["opinions"], [])

    def test_unknown_intent_not_invented(self):
        self.dump(self.context, {"intent": None, "pillars": []})
        value = self.response()
        self.rejected(value)
        value["opinions"] = []
        self.do_import(value)
        self.assertEqual(self.load(self.output / "review.json")["context"], {"intent": None, "pillars": []})

    def test_unknown_fields_at_every_feedback_level(self):
        value = self.response()
        for target in ((), ("origin",), ("opinions", 0), ("opinions", 0, "evidence"), ("opinions", 0, "uncertainty")):
            bad = copy.deepcopy(value)
            obj = bad
            for key in target:
                obj = obj[key]
            obj["made_up"] = "not allowed"
            with self.subTest(target=target):
                self.rejected(bad)

    def test_context_unknown_fields_and_duplicate_pillars(self):
        for context in ({"intent": "x", "pillars": [], "unknown": True},
                        {"intent": "x", "pillars": [{"id": "P", "text": "a"}, {"id": "P", "text": "b"}]},
                        {"intent": "x", "pillars": [{"id": "P", "text": "a", "extra": 2}]}):
            self.dump(self.context, context)
            with self.assertRaises(ValueError):
                self.prepare()
            self.assertFalse(self.prepared.exists())

    def test_binding_mismatch(self):
        value = self.response()
        for field, replacement in (("schema", "v99"), ("request_id", "another"), ("request_sha256", "0" * 64),
                                   ("source_sha256", {"original": "0" * 64})):
            bad = copy.deepcopy(value)
            bad[field] = replacement
            self.rejected(bad)

    def test_bbox_original_coordinates_and_bounds(self):
        value = self.response()
        for box in ([-1, 0, 1, 1], [0, 0, 0, 1], [10, 0, 3, 1], [0, 9, 1, 2], [False, 0, 1, 1],
                    [0.1, 0, 1, 1], [0, 0, 1], "region1", None):
            bad = copy.deepcopy(value)
            bad["opinions"][0]["bbox_xywh"] = box
            with self.subTest(box=box):
                self.rejected(bad)

    def test_pillar_reference_evidence_and_uncertainty_required(self):
        value = self.response()
        mutations = [("pillar_ids", []), ("pillar_ids", ["unknown"]), ("pillar_ids", ["P1", "P1"]),
                     ("evidence", {"observations": "x", "source_roles": ["unknown"], "inference": "x"}),
                     ("uncertainty", {"level": "certain", "reason": "x"}),
                     ("uncertainty", {"level": "high", "reason": ""}), ("impact", ""), ("action", ""),
                     ("priority", "P9"), ("reason", "")]
        for field, replacement in mutations:
            bad = copy.deepcopy(value)
            bad["opinions"][0][field] = replacement
            self.rejected(bad)

    def test_subjective_preference_cannot_be_objective_or_must_change(self):
        value = self.response()
        for classification, claim in (("must_change", "subjective_preference"), ("must_change", "hypothesis"),
                                      ("suggestion", "subjective_preference"), ("personal_preference", "intent_conflict"),
                                      ("must_change", "proven_objective_defect")):
            bad = copy.deepcopy(value)
            bad["opinions"][0].update(classification=classification, claim_type=claim)
            self.rejected(bad)

    def test_duplicate_stable_ids_and_missing_origin(self):
        value = self.response()
        bad = copy.deepcopy(value)
        bad["opinions"] *= 2
        self.rejected(bad)
        for origin in ({"kind": "automatic", "name": "x", "reference": "x"}, {"kind": "human", "name": "x"},
                       {"kind": "human", "name": "", "reference": "x"}):
            bad = copy.deepcopy(value)
            bad["origin"] = origin
            self.rejected(bad)

    def test_human_decision_validation_and_amendment_preserves_id(self):
        value = self.response()
        decision = dict(opinion_id="R1", action="confirm", actor="human", reason="理由")
        bad_decisions = [dict(decision, action="auto"), dict(decision, actor=""), dict(decision, reason=""),
                         dict(decision, opinion_id="unknown"), dict(decision, extra=1),
                         dict(decision, amended_opinion=self.opinion()), dict(decision, action="amend"),
                         dict(decision, action="amend", amended_opinion=self.opinion("R2"))]
        out_of_bounds = self.opinion()
        out_of_bounds["bbox_xywh"] = [999, 0, 1, 1]
        bad_decisions.append(dict(decision, action="amend", amended_opinion=out_of_bounds))
        for item in bad_decisions:
            bad = copy.deepcopy(value)
            bad["decisions"] = [item]
            self.rejected(bad)
        value["decisions"] = [decision, decision]
        self.rejected(value)

    def test_dimensions_mismatch(self):
        with Image.new("L", (13, 10)) as image:
            image.save(self.images[1])
        with self.assertRaisesRegex(ValueError, "尺寸"):
            self.prepare()
        self.assertFalse(self.prepared.exists())

    def test_invalid_png_animation_and_pixel_limit(self):
        for data in (b"not PNG", b"\x89PNG\r\n\x1a\n"):
            self.images[1].write_bytes(data)
            with self.assertRaises((ValueError, OSError)):
                self.prepare()
        with Image.new("RGB", (12, 10)) as image:
            image.save(self.images[1], format="JPEG")
        with self.assertRaises(ValueError):
            self.prepare()
        with Image.new("RGB", (12, 10)) as image:
            image.save(self.images[1], format="PNG")
        with patch.object(review, "MAX_PIXELS", 10), self.assertRaises(ValueError):
            self.prepare()

    def test_sources_changed_content_or_replaced_even_same_bytes(self):
        value = self.response()
        original = self.images[0].read_bytes()
        self.images[0].write_bytes(original + b"changed")
        self.rejected(value)
        replacement = self.root / "replacement.png"
        replacement.write_bytes(original)
        replacement.replace(self.images[0])
        self.rejected(value)

    def test_prepared_digest_receipt_or_extra_file_rejected(self):
        value = self.response()
        request = self.prepared / "request.json"
        raw = request.read_bytes()
        request.write_bytes(raw + b" ")
        self.rejected(value)
        request.write_bytes(raw)
        extra = self.prepared / "unexpected"
        extra.write_bytes(b"x")
        self.rejected(value)
        extra.unlink()
        (self.prepared / "complete.json").unlink()
        self.rejected(value)

    def test_alias_links_fifo_and_parent_links_rejected(self):
        original = self.images[0]
        alias = self.root / "alias.png"
        alias.symlink_to(original)
        hard = self.root / "hard.png"
        os.link(original, hard)
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        link_parent = self.root / "linked"
        link_parent.symlink_to(self.root, target_is_directory=True)
        for path in (alias, hard, fifo, str(self.root) + "/./original.png", str(self.root) + "//original.png",
                     link_parent / "original.png"):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.prepare(original=path)
        hard.unlink()
        with self.assertRaises(ValueError):
            self.prepare(silhouette=original)
        with self.assertRaises(OSError):
            self.prepare(output=link_parent / "out")
        self.assertFalse(self.prepared.exists())

    def test_old_outputs_never_overwritten(self):
        value = self.response()
        old = (self.prepared / "request.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual((self.prepared / "request.json").read_bytes(), old)
        self.do_import(value)
        report = (self.output / "review.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.do_import(value)
        self.assertEqual((self.output / "review.json").read_bytes(), report)

    def test_output_file_symlink_and_alias_rejected(self):
        target = self.root / "taken"
        target.write_text("keep")
        for output in (target, str(self.root) + "/./out", str(self.root) + "/out/"):
            with self.assertRaises((ValueError, OSError)):
                self.prepare(output=output)
        self.prepared.symlink_to(target)
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(target.read_text(), "keep")

    def test_oversized_png_and_json_rejected_before_read(self):
        huge = self.root / "huge"
        with huge.open("wb") as stream:
            stream.truncate(review.MAX_PNG + 1)
        with self.assertRaisesRegex(ValueError, "大小限制"):
            self.prepare(original=huge)
        with huge.open("wb") as stream:
            stream.truncate(review.MAX_JSON + 1)
        with self.assertRaisesRegex(ValueError, "大小限制"):
            self.prepare(context=huge)
        value = self.response()
        with self.feedback.open("wb") as stream:
            stream.truncate(review.MAX_JSON + 1)
        with self.assertRaisesRegex(ValueError, "大小限制"):
            review.import_feedback(self.prepared, self.feedback, self.output)
        self.assertFalse(self.output.exists())

    def test_duplicate_json_fields_nonfinite_and_deep_json_rejected(self):
        value = self.response()
        for raw in ('{"schema":1,"schema":2}', '{"x":NaN}', '[' * 2000 + '0' + ']' * 2000):
            self.feedback.write_text(raw)
            with self.assertRaises((ValueError, RecursionError)):
                review.import_feedback(self.prepared, self.feedback, self.output)
            self.assertFalse(self.output.exists())

    def test_partial_write_failure_cleans_and_reports_no_success(self):
        original = review._write_at
        count = 0
        def fail(fd, name, data):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("synthetic write failure")
            return original(fd, name, data)
        with patch.object(review, "_write_at", fail), self.assertRaisesRegex(OSError, "synthetic"):
            self.prepare()
        self.assertFalse(self.prepared.exists())

    def test_source_changes_during_output_roll_back(self):
        original = review._write_at
        def changed(fd, name, data):
            original(fd, name, data)
            if name == "request.json":
                self.images[0].write_bytes(b"changed")
        with patch.object(review, "_write_at", changed), self.assertRaisesRegex(ValueError, "改变"):
            self.prepare()
        self.assertFalse(self.prepared.exists())

    def test_import_feedback_symlink_fifo_and_hardlink_rejected(self):
        value = self.response()
        real = self.root / "real-feedback.json"
        self.dump(real, value)
        self.feedback.symlink_to(real)
        with self.assertRaises(OSError):
            review.import_feedback(self.prepared, self.feedback, self.output)
        self.feedback.unlink()
        os.mkfifo(self.feedback)
        with self.assertRaises(ValueError):
            review.import_feedback(self.prepared, self.feedback, self.output)
        self.feedback.unlink()
        os.link(real, self.feedback)
        with self.assertRaises(ValueError):
            review.import_feedback(self.prepared, self.feedback, self.output)
        self.assertFalse(self.output.exists())

    def test_import_failure_or_source_race_cannot_leave_success_marker(self):
        value = self.response()
        self.dump(self.feedback, value)
        original = review._write_at
        def failed(fd, name, data):
            if name == "review.md":
                raise OSError("synthetic import failure")
            original(fd, name, data)
        with patch.object(review, "_write_at", failed), self.assertRaises(OSError):
            review.import_feedback(self.prepared, self.feedback, self.output)
        self.assertFalse(self.output.exists())
        def changed(fd, name, data):
            original(fd, name, data)
            if name == "review.md":
                self.images[1].write_bytes(b"changed")
        with patch.object(review, "_write_at", changed), self.assertRaisesRegex(ValueError, "改变"):
            review.import_feedback(self.prepared, self.feedback, self.output)
        self.assertFalse(self.output.exists())

    def test_request_unknown_fields_or_dimensions_refused_even_with_new_receipt(self):
        value = self.response()
        path = self.prepared / "request.json"
        original = self.load(path)
        receipt_path = self.prepared / "complete.json"
        receipt = self.load(receipt_path)
        for mutate in (lambda r: r.update(extra=True),
                       lambda r: r["images"]["original"].update(dimensions=[999, 10]),
                       lambda r: r["images"]["original"].update(extra=True),
                       lambda r: r["context"].update(extra=True)):
            bad = copy.deepcopy(original)
            mutate(bad)
            self.dump(path, bad)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            value["request_sha256"] = digest
            receipt["files"]["request.json"] = digest
            self.dump(receipt_path, receipt)
            self.rejected(value)

    def test_apng_truncated_and_16bit_png_rejected(self):
        with Image.new("RGB", (12, 10), "red") as first, Image.new("RGB", (12, 10), "blue") as second:
            first.save(self.images[1], format="PNG", save_all=True, append_images=[second])
        with self.assertRaises(ValueError):
            self.prepare()
        with Image.new("I;16", (12, 10)) as image:
            image.save(self.images[1], format="PNG")
        with self.assertRaises(ValueError):
            self.prepare()
        data = self.images[0].read_bytes()
        self.images[1].write_bytes(data[:-15])
        with self.assertRaises((ValueError, OSError)):
            self.prepare()
        self.assertFalse(self.prepared.exists())

    def test_missing_pillow_is_actionable_only_on_execution(self):
        with patch.dict(sys.modules, {"PIL": None}), self.assertRaisesRegex(ValueError, "可选 CLI Pillow"):
            self.prepare()
        self.assertFalse(self.prepared.exists())

    def test_markdown_external_strings_are_inert(self):
        value = self.response()
        value["opinions"][0]["reason"] = "\n# injected heading\n![x](https://invalid.example)"
        self.do_import(value)
        md = (self.output / "review.md").read_text()
        self.assertNotIn("\n# injected heading", md)
        self.assertIn("\\n# injected heading", md)


if __name__ == "__main__":
    unittest.main()
