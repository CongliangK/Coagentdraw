"""Synthetic schema fixtures only; no historical/artistic or online validation."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
import cwb_research as research


def record(identifier="r1", *, object_id="test-object", status="candidate"):
    return {"id": identifier,
            "source": {"institution": "TEST ONLY", "title": "测试标题：肩甲连接", "url": "https://example.org/object/1", "object_id": object_id},
            "locator": {}, "terms": ["肩甲", "pauldron"], "excerpt": "测试索引文本：连接与铆钉",
            "claims": {"fact": [], "observation": [], "inference": [], "unknown": ["测试未见部分"]},
            "verification": {"text_read": False, "image_seen": False},
            "rights": {"metadata": "unknown", "text": "unknown", "image": "unknown"},
            "decision": {"status": status, "reason": "合成测试记录，不是事实参考"}}


def catalog(*records):
    return {"schema": research.SCHEMA, "question": {"id": "q1", "decision": "测试决策问题", "intent_version": "test-v1"}, "records": list(records)}


def adopted(identifier="r1", *, object_id="test-object"):
    item = record(identifier, object_id=object_id, status="accepted")
    item["claims"]["fact"] = ["仅为测试校验规则的文本"]
    item["verification"]["text_read"] = True
    return item


class ResearchTests(unittest.TestCase):
    def test_plan_factual_routes_and_global_budget(self):
        plan = research.plan("确定连接关系", "function", ["铆钉", "rivets"])
        self.assertEqual([r["source"] for r in plan["routes"]], ["books", "institution"])
        self.assertEqual(plan["status"], "planned_not_searched")
        self.assertEqual(plan["budget"], {"candidate_objects_total": 5, "full_reads_total": 2, "rewrite_rounds_max": 2})
        self.assertTrue(plan["gaps"])
        self.assertIn("2 轮", plan["stop_when"][-1])

    def test_unicode_query_links_encode_each_alias_separately(self):
        term = "肩甲 &/?#测试"
        plan = research.plan("决定术语", "term", [term, "pauldron"], sources=["wiki", "commons"])
        for route in plan["routes"]:
            self.assertEqual(len(route["queries"]), 2)
            self.assertEqual(parse_qs(urlsplit(route["queries"][0]["url"]).query)["search"], [term])
            self.assertEqual(route["queries"][1]["term"], "pauldron")
        self.assertEqual(plan["gaps"], [])

    def test_route_bounds_and_invalid_urls(self):
        invalid = [{"candidates": True}, {"candidates": 11}, {"reads": 4}, {"candidates": 1, "reads": 2},
                   {"rewrites": 3}, {"sources": []}, {"sources": ["wiki"] * 2},
                   {"sources": ["aat", "wiki", "commons"]}, {"institution_url": "https://example.org"}]
        for kw in invalid:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                research.plan("问题", "term", ["词"], **kw)
        for terms in ([], ["a"] * 4, ["A", "a"], [""]):
            with self.assertRaises(ValueError):
                research.plan("问题", "term", terms)
        for url in ("javascript:alert(1)", "file:///tmp/a", "https://user:pass@example.org", "https://example.org/a b"):
            with self.assertRaises(ValueError):
                research.plan("问题", "form", ["词"], institution_url=url)
        with self.assertRaises(ValueError):
            research.plan("问题", "aesthetic", ["词"])

    def test_local_search_rank_limit_chinese_nfkc_and_no_inference_boost(self):
        a, b, c, d = [record(f"r{i}", object_id=f"object-{i}") for i in range(4)]
        b["terms"] = []; b["source"]["title"] = "body only"
        c["terms"] = ["ＰＡＵＬＤＲＯＮ"]; c["source"]["title"] = "alias only"
        d["terms"] = []; d["source"]["title"] = "none"; d["excerpt"] = ""
        d["claims"]["inference"] = ["PAULDRON"]
        result = research.search(catalog(d, b, c, a), ["PAULDRON", "铆钉"], limit=2)
        self.assertEqual([r["id"] for r in result["results"]], ["r0", "r2"])
        self.assertEqual(result["matching_source_groups"], 3)
        self.assertEqual(result["omitted_source_groups"], 1)
        self.assertEqual(result["results"][0]["evidence_status"], "candidate")
        self.assertEqual(research.search(catalog(a), ["不存在"])["results"], [])
        with self.assertRaises(ValueError):
            research.search(catalog(a), ["x"], limit=0)

    def test_dedup_original_object_keep_mirrors_and_editions(self):
        a, b = record("a"), record("b")
        b["access_url"] = "https://example.net/mirror"
        self.assertEqual(len(research.search(catalog(b, a), ["肩甲"])["results"]), 1)
        info = research.inspect(catalog(a, b), "b")
        self.assertEqual(info["same_source_ids"], ["a", "b"])
        self.assertEqual(info["open_locator"]["access_url"], b["access_url"])
        b["source"]["institution"] = "OTHER TEST INSTITUTION"
        self.assertEqual(len(research.search(catalog(a, b), ["肩甲"])["results"]), 2)
        for r in (a, b):
            del r["source"]["object_id"]
        b["source"]["url"] += "#page=2"
        self.assertEqual(len(research.search(catalog(a, b), ["肩甲"])["results"]), 1)
        b["source"]["edition"] = "different edition"
        self.assertEqual(len(research.search(catalog(a, b), ["肩甲"])["results"]), 2)

    def test_book_locator_preserves_printed_pdf_pages_figure(self):
        a = adopted()
        del a["source"]["object_id"]
        a["source"]["edition"] = "测试版本"
        a["locator"] = {"url": "https://example.org/book.pdf#old", "pdf_page": 4, "printed_page": "111", "figure": "3–4", "region": "图注"}
        result = research.inspect(catalog(a), "r1")
        self.assertEqual(result["open_locator"]["open_url"], "https://example.org/book.pdf#page=4")
        self.assertEqual(result["open_locator"]["printed_page"], "111")
        self.assertEqual(result["record"]["locator"]["figure"], "3–4")
        self.assertEqual(result["evidence_status"], "adopted_user_supplied")
        for locator in ({"pdf_page": 4}, {"path": "../book.pdf", "pdf_page": 1}, {"path": "/tmp/book.pdf", "pdf_page": 1}, {"path": "book.pdf"}, {"url": "https://example.org/book.pdf", "pdf_page": True}):
            bad = copy.deepcopy(a); bad["locator"] = locator
            with self.assertRaises(ValueError):
                research.validate(catalog(bad))
        a["locator"] = {"path": "books/legal-copy.pdf", "pdf_page": 4, "printed_page": "iv"}
        self.assertEqual(research.inspect(catalog(a), "r1")["open_locator"]["path"], "books/legal-copy.pdf")

    def test_claims_cannot_upgrade_without_user_verification(self):
        for claim, verification in (("fact", "text_read"), ("observation", "image_seen")):
            a = record(); a["claims"][claim] = ["测试断言"]
            with self.assertRaises(ValueError):
                research.validate(catalog(a))
            a["verification"][verification] = True
            self.assertEqual(research.inspect(catalog(a), "r1")["evidence_status"], "verified_user_supplied")
        a = record(status="accepted"); a["claims"]["inference"] = ["测试推断"]
        with self.assertRaises(ValueError):
            research.validate(catalog(a))
        a = adopted(); del a["source"]["object_id"]
        with self.assertRaises(ValueError):
            research.validate(catalog(a))

    def test_rights_unknown_and_divergent_labels_preserved(self):
        a = adopted(); a["rights"] = {"metadata": "CC0", "text": "CC BY", "image": "unknown"}
        packet = research.evidence(catalog(a), outcome="sufficient", stop_reason="人工停止理由")
        self.assertEqual(packet["accepted_source_groups"][0][0]["rights"], a["rights"])
        del a["rights"]["image"]
        with self.assertRaises(ValueError):
            research.validate(catalog(a))

    def test_evidence_dedup_pending_rejected_and_no_silent_truncation(self):
        records = [adopted(f"r{i}", object_id=f"object-{i}") for i in range(4)]
        mirror = copy.deepcopy(records[0]); mirror["id"] = "mirror"
        records += [mirror, record("pending"), record("rejected", status="rejected")]
        result = research.evidence(catalog(*records), outcome="blocked", stop_reason="仍缺背面视角")
        self.assertEqual(result["accepted_group_count"], 4)
        self.assertEqual(len(result["accepted_source_groups"][0]), 2)
        self.assertTrue(result["budget_warning"])
        self.assertEqual(result["pending"][0]["id"], "pending")
        self.assertEqual(result["rejected"][0]["id"], "rejected")
        self.assertNotIn("rejected", str(research.search(catalog(records[-1]), ["肩甲"])["results"]))
        with self.assertRaises(ValueError):
            research.evidence(catalog(), outcome="sufficient", stop_reason="无依据")
        with self.assertRaises(ValueError):
            research.evidence(catalog(), outcome="blocked", stop_reason="")

    def test_schema_rejects_unknown_duplicate_missing_and_invalid_values(self):
        cases = []
        a = catalog(record()); a["extra"] = 1; cases.append(a)
        cases.append(catalog(record(), record()))
        a = record(); a["verification"]["text_read"] = 1; cases.append(catalog(a))
        a = record(); a["decision"]["status"] = "verified"; cases.append(catalog(a))
        a = record(); a["source"]["url"] = "data:text/plain,test"; cases.append(catalog(a))
        a = record(); a["claims"]["fact"] = "not a list"; cases.append(catalog(a))
        a = record(); a["locator"] = {"pdf_page": 4, "url": "https://example.org/book.pdf"}; cases.append(catalog(a))
        a = catalog(record()); a["question"]["intent_version"] = ""; cases.append(a)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                research.validate(data)
        with self.assertRaises(ValueError):
            research.inspect(catalog(record()), "missing")

    def test_load_limits_duplicates_symlinks_fifo_no_network(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "catalog.json"
            raw = json.dumps(catalog(record()), ensure_ascii=False)
            path.write_text(raw)
            with patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
                data, digest = research.load(path)
                self.assertEqual(data, catalog(record()))
                self.assertEqual(len(digest), 64)
                research.search(data, ["肩甲"])
                research.inspect(data, "r1")
            for raw in ('{"schema":1,"schema":2}', '{"value":NaN}', '[]', '"' + 'x' * research.LIMIT + '"', '\xff'):
                path.write_text(raw)
                with self.assertRaises(ValueError):
                    research.load(path)
            path.write_text(json.dumps(catalog(record())))
            alias = Path(temp) / "alias"; alias.symlink_to(path)
            with self.assertRaises(OSError):
                research.load(alias)
            fifo = Path(temp) / "fifo"; os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                research.load(fifo)

    def test_unknown_identity_uses_original_url_not_placeholder_group(self):
        for key in ("institution", "object_id"):
            a, b = record("a"), record("b")
            a["source"][key] = "unknown"
            b["source"][key] = " ＵＮＫＮＯＷＮ "
            b["source"]["url"] = "https://example.org/different-object"
            result = research.search(catalog(a, b), ["肩甲"])
            self.assertEqual(result["matching_source_groups"], 2)
            self.assertEqual(research.inspect(catalog(a), "a")["record"]["source"][key], "unknown")
            b["source"]["url"] = a["source"]["url"] + "#different-view"
            self.assertEqual(research.search(catalog(a, b), ["肩甲"])["matching_source_groups"], 1)

    def test_unknown_source_url_is_not_fabricated_or_accepted(self):
        a = record()
        a["source"]["institution"] = "unknown"
        result = research.inspect(catalog(a), "r1")
        self.assertEqual(result["evidence_status"], "candidate")
        self.assertEqual(result["record"]["rights"], {"metadata": "unknown", "text": "unknown", "image": "unknown"})
        a["source"]["url"] = "unknown"
        with self.assertRaises(ValueError):
            research.validate(catalog(a))

    def test_unknown_locator_is_not_precise_adoption_evidence(self):
        a = adopted()
        a["source"]["object_id"] = "unknown"
        for locator in ({}, {"region": "unknown"}, {"section": " ＵＮＫＮＯＷＮ "}):
            a["locator"] = locator
            with self.assertRaises(ValueError):
                research.validate(catalog(a))
        a["locator"] = {"section": "synthetic fixture section"}
        self.assertEqual(research.inspect(catalog(a), "r1")["evidence_status"], "adopted_user_supplied")

    def test_unread_text_and_unseen_page_image_cli_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.json"
            for claim, flag in (("fact", "text_read"), ("observation", "image_seen")):
                item = record(status="accepted")
                item["source"]["edition"] = "unknown"
                item["locator"] = {"path": "local-copy/book.pdf", "pdf_page": 7,
                                   "printed_page": "iv", "figure": "2"}
                item["claims"][claim] = ["合成测试声明，不是真实来源证据"]
                for checked in (False, True):
                    item["verification"][flag] = checked
                    path.write_text(json.dumps(catalog(item)), encoding="utf-8")
                    before = path.read_bytes()
                    proc = subprocess.run([sys.executable, "-B", str(ROOT / "core/cwb_research.py"),
                                           "inspect", str(path), "r1"], capture_output=True, text=True,
                                          cwd=temp, timeout=5)
                    self.assertEqual(proc.returncode == 0, checked, proc.stderr)
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(sorted(p.name for p in Path(temp).iterdir()), ["input.json"])
                    if checked:
                        info = json.loads(proc.stdout)
                        self.assertEqual(info["open_locator"]["pdf_page"], 7)
                        self.assertEqual(info["open_locator"]["printed_page"], "iv")
                        self.assertIn("未独立查证", info["notice"])
                    else:
                        self.assertEqual(proc.stdout, "")
                        self.assertNotIn("Traceback", proc.stderr)

    def test_missing_and_unknown_edition_share_object_or_url_identity(self):
        for use_object in (True, False):
            records = [adopted("missing"), adopted("unknown"), adopted("normalized")]
            for item in records:
                if not use_object:
                    del item["source"]["object_id"]
                    item["locator"] = {"section": "synthetic section"}
            records[1]["source"]["edition"] = "unknown"
            records[2]["source"]["edition"] = " ＵＮＫＮＯＷＮ "
            data = catalog(*records)
            self.assertEqual(research.search(data, ["肩甲"])["matching_source_groups"], 1)
            self.assertEqual(research.inspect(data, "missing")["same_source_ids"], ["missing", "normalized", "unknown"])
            result = research.evidence(data, outcome="sufficient", stop_reason="合成重复记录测试")
            self.assertEqual(result["accepted_group_count"], 1)
            self.assertEqual(len(result["accepted_source_groups"][0]), 3)
            self.assertNotIn("edition", result["accepted_source_groups"][0][0]["source"])
            records[0]["source"]["edition"] = "edition-a"
            records[1]["source"]["edition"] = "edition-b"
            self.assertEqual(research.search(catalog(*records[:2]), ["肩甲"])["matching_source_groups"], 2)

    def test_pdf_page_requires_explicit_known_locator_carrier(self):
        item = adopted()
        item["source"].update(object_id="unknown", institution="unknown", edition="unknown",
                              url="https://example.org/original.pdf")
        for locator in ({"pdf_page": 7}, {"path": "unknown", "pdf_page": 7},
                        {"path": " ＵＮＫＮＯＷＮ ", "pdf_page": 7}):
            item["locator"] = locator
            with self.assertRaises(ValueError):
                research.inspect(catalog(item), "r1")
        item["locator"] = {"url": item["source"]["url"], "path": "unknown", "pdf_page": 7,
                           "printed_page": "unknown", "figure": "unknown"}
        result = research.inspect(catalog(item), "r1")
        self.assertEqual(result["open_locator"]["open_url"], "https://example.org/original.pdf#page=7")
        self.assertEqual(result["open_locator"]["path"], "unknown")
        item["locator"] = {"path": "books/explicit-copy.pdf", "pdf_page": 7,
                           "printed_page": "unknown", "figure": "unknown"}
        result = research.inspect(catalog(item), "r1")
        self.assertEqual(result["open_locator"]["path"], "books/explicit-copy.pdf")
        self.assertEqual(result["open_locator"]["pdf_page"], 7)
        self.assertNotIn("open_url", result["open_locator"])

    def test_evidence_preserves_unresolved_claims_for_all_decisions(self):
        accepted, pending, rejected = adopted(), record("pending"), record("rejected", status="rejected")
        for item in (accepted, pending, rejected):
            item["claims"]["unknown"] = ["合成缺口 " + item["id"], "不是 decision.reason 的问题"]
        data = catalog(accepted, pending, rejected)
        before = copy.deepcopy(data)
        for outcome in ("blocked", "sufficient"):
            result = research.evidence(data, outcome=outcome, stop_reason="合成停止原因")
            self.assertEqual(result["accepted_source_groups"][0][0]["claims"]["unknown"], accepted["claims"]["unknown"])
            for name, item in (("pending", pending), ("rejected", rejected)):
                self.assertEqual(result[name], [{"id": item["id"], "reason": item["decision"]["reason"],
                                                "unknown": item["claims"]["unknown"]}])
            self.assertEqual(result["stop_reason"], "合成停止原因")
            self.assertEqual(result["outcome_user_supplied"], outcome)
        self.assertEqual(data, before)

    def test_all_cli_commands_stdout_only_and_help_no_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root / "catalog.json"
            path.write_text(json.dumps(catalog(adopted())))
            snapshot = {p.name: p.read_bytes() for p in root.iterdir()}
            commands = [["--help"], ["plan", "--help"], ["plan", "测试问题", "--kind", "term", "--term", "肩甲"],
                        ["search", str(path), "--term", "肩甲"], ["inspect", str(path), "r1"],
                        ["evidence", str(path), "--outcome", "sufficient", "--stop-reason", "测试理由"]]
            for args in commands:
                proc = subprocess.run([sys.executable, "-B", str(ROOT / "core/cwb_research.py"), *args], cwd=root, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                if "--help" not in args:
                    result = json.loads(proc.stdout)
                    self.assertIn("notice", result)
                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, snapshot)
            proc = subprocess.run([sys.executable, "-B", str(ROOT / "core/cwb_research.py"), "search", str(path), "--term", "x", "--limit", "11"], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
