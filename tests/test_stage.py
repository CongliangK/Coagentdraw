"""CAD-21 标准库/mock 回归；不证明真实 Krita、PNG 像素或 .kra 重开。"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
import cwb
import cwb_stage as stage

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082")


class Fake:
    def __init__(self, document):
        self.document = str(document)
        self.pid = 123
        self.root = {"id": "root", "name": "root", "type": "grouplayer", "root": True, "children": []}
        self.calls = []
        self.serial = 0
        self.posts = 0
        self.fail_at = None
        self.mode = "normal"
        self.read_fail = False

    def nodes(self):
        result = {}
        def walk(node):
            result[node["id"]] = node
            for child in node["children"]:
                walk(child)
        walk(self.root)
        return result

    def add(self, name, parent=None, kind="grouplayer"):
        self.serial += 1
        node = {"id": "node-" + str(self.serial), "name": name, "type": kind, "root": False, "children": []}
        (self.nodes()[parent] if parent else self.root)["children"].append(node)
        return node

    def reopen(self):
        # 明确模拟所有会话 ID 失效；不冒充真实 Krita 重开证据。
        self.pid += 1
        for node in list(self.nodes().values()):
            node["id"] += "-reopen"

    def request(self, path, payload=None):
        self.calls.append((path, copy.deepcopy(payload)))
        if path == "/docs":
            return {"documents": [{"name": "测试", "filename": self.document,
                                    "width": 100, "height": 100, "active": True}]}
        if path == "/ping":
            return {"pid": self.pid, "ok": True}
        if path == "/layers":
            if self.read_fail:
                raise OSError("mock readback unavailable")
            return {"document": "测试", "layers": [copy.deepcopy(self.root)]}
        if path in ("/layer/group", "/layer/add"):
            self.posts += 1
            failing = self.posts == self.fail_at
            if failing and self.mode == "before":
                raise OSError("mock no response; may still execute")
            parent = payload.get("parent_id")
            node = self.add(payload["name"], parent,
                            "grouplayer" if path == "/layer/group" else "paintlayer")
            response = {"id": node["id"], "name": node["name"], "parent_id": parent or self.root["id"]}
            if failing:
                if self.mode == "after":
                    raise OSError("mock response lost after attach")
                if self.mode == "readback":
                    self.read_fail = True
                if self.mode == "wrong_parent":
                    self.nodes()[parent]["children"].remove(node)
                    self.root["children"].append(node)
                if self.mode == "wrong_id":
                    response["id"] = "nonexistent"
                if self.mode == "exit":
                    raise SystemExit("HTTP 400: mock partial create")
                if self.mode == "interrupt":
                    raise KeyboardInterrupt()
            return response
        raise AssertionError(path)


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cad21-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.document = self.root / "测试.kra"
        self.document.write_bytes(b"offline placeholder; not a real kra")
        self.state = self.root / "测试.cwb-stage.json"
        self.fake = Fake(self.document)

    def setup_stage(self, number=4, **kwargs):
        return stage.execute(number, self.document, self.state, self.fake.request, role="character", **kwargs)

    def data(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def save_data(self, data):
        self.state.write_text(json.dumps(data), encoding="utf-8")


    def test_unowned_same_name_conflict_preflight_no_partial_creation(self):
        for label in ("00_简要意图与保持要点", "拼贴原型", "服装结构", "粗线稿", "配色"):
            with self.subTest(label=label):
                self.fake.root["children"] = []
                self.fake.add(label, kind="paintlayer")
                with self.assertRaisesRegex(stage.StageError, "同名"):
                    self.setup_stage()
                self.assertEqual(self.fake.posts, 0)
                self.assertFalse(self.state.exists())

    def test_rename_reorder_move_without_reset(self):
        self.setup_stage()
        data = self.data()
        outer = self.fake.nodes()[data["groups"]["character/20_探索"]["id"]]
        child = self.fake.nodes()[data["groups"]["character/20_探索/剪影"]["id"]]
        child["name"] = "用户改名，无标记"
        outer["name"] = "用户外组"
        outer["children"].reverse()
        outer["children"].remove(child)
        user_group = self.fake.add("用户工作区")
        user_group["children"].append(child)
        before = copy.deepcopy(self.fake.root)
        count = self.fake.posts
        self.setup_stage()
        self.assertEqual(self.fake.root, before)
        self.assertEqual(self.fake.posts, count)

    def test_repeated_setup_for_same_document_role(self):
        first = self.setup_stage(4)
        self.assertEqual(len(first["created"]), len(stage.specs(4, role="character")))
        count = self.fake.posts
        self.assertEqual(self.setup_stage(5)["created"], [])
        self.assertEqual(self.fake.posts, count)

    def test_prewrite_read_failure_resumes_only_not_yet_attempted_groups(self):
        original = self.fake.request
        reads_after_root = [0]
        def interrupted(path, payload=None):
            if path == "/layers" and self.fake.posts == 1:
                reads_after_root[0] += 1
                if reads_after_root[0] == 2:
                    raise OSError("read failed before second POST")
            return original(path, payload)
        with self.assertRaises(OSError):
            stage.execute(4, self.document, self.state, interrupted, role="character")
        self.assertIsNone(self.data()["pending"])
        self.assertEqual(list(self.data()["groups"]), ["character/00_简要意图与保持要点"])
        original_root = copy.deepcopy(self.fake.root["children"][0])
        result = self.setup_stage()
        self.assertEqual(result["created"], [key for key, _, _ in stage.specs(4, role="character")][1:])
        self.assertEqual(self.fake.posts, len(stage.specs(4, role="character")))
        self.assertEqual(self.fake.root["children"][0]["id"], original_root["id"])

    def test_stale_id_reparsed_by_token_not_same_name(self):
        self.setup_stage()
        data = self.data()
        data["groups"]["character/20_探索/剪影"]["id"] = "expired"
        self.save_data(data)
        count = self.fake.posts
        self.setup_stage()
        self.assertEqual(self.fake.posts, count)
        self.assertNotEqual(self.data()["groups"]["character/20_探索/剪影"]["id"], "expired")

    def test_expired_id_and_plain_same_name_stop(self):
        self.setup_stage()
        data = self.data()
        record = data["groups"]["character/20_探索/剪影"]
        node = self.fake.nodes()[record["id"]]
        node["name"], node["id"] = "剪影", "new-session-id"
        count = self.fake.posts
        with self.assertRaisesRegex(stage.StageError, "身份失效"):
            self.setup_stage(5)
        self.assertEqual(self.fake.posts, count)

    def test_reopen_reparse_preserves_layout_and_no_duplicate(self):
        self.setup_stage()
        data = self.data()
        child = self.fake.nodes()[data["groups"]["character/20_探索/剪影"]["id"]]
        child["name"] = "重命名但保留 " + stage.marker(data["groups"]["character/20_探索/剪影"]["token"])
        self.fake.root["children"][0]["children"].reverse()
        self.fake.reopen()
        before, count = copy.deepcopy(self.fake.root), self.fake.posts
        self.setup_stage()
        self.assertEqual(self.fake.root, before)
        self.assertEqual(self.fake.posts, count)
        self.assertTrue(all(g["id"].endswith("-reopen") for g in self.data()["groups"].values()))

    def test_reopen_removed_marker_even_if_old_node_id_survives_stops(self):
        self.setup_stage()
        node = self.fake.nodes()[self.data()["groups"]["character/20_探索/剪影"]["id"]]
        node["name"] = "用户完全改名"
        self.fake.pid += 1
        count = self.fake.posts
        with self.assertRaisesRegex(stage.StageError, "身份失效"):
            self.setup_stage()
        self.assertEqual(self.fake.posts, count)

    def test_duplicate_marker_and_id_marker_disagreement_stop(self):
        self.setup_stage()
        data = self.data()
        record = data["groups"]["character/20_探索/剪影"]
        existing = self.fake.nodes()[record["id"]]
        duplicate = self.fake.add(existing["name"])
        count = self.fake.posts
        with self.assertRaisesRegex(stage.StageError, "歧义"):
            self.setup_stage()
        existing["name"] = "没有标记"
        with self.assertRaisesRegex(stage.StageError, "冲突"):
            self.setup_stage()
        self.assertEqual(self.fake.posts, count)
        self.assertNotEqual(duplicate["id"], existing["id"])

    def test_wrong_node_type_and_root_never_adopted(self):
        self.setup_stage()
        data = self.data()
        group = self.fake.nodes()[data["groups"]["character/20_探索/剪影"]["id"]]
        group["type"] = "paintlayer"
        with self.assertRaisesRegex(stage.StageError, "身份失效"):
            self.setup_stage()
        group["type"], group["root"] = "grouplayer", True
        with self.assertRaisesRegex(stage.StageError, "身份失效"):
            self.setup_stage()

    def test_wrong_document_sidecar_and_duplicate_open_path_rejected(self):
        other = self.root / "别的.kra"
        other.write_bytes(b"fake")
        self.fake.document = str(other)
        with self.assertRaisesRegex(stage.StageError, "活动文档错误"):
            self.setup_stage()
        self.assertFalse(self.state.exists())
        self.fake.document = str(self.document)
        self.setup_stage()
        count = self.fake.posts
        with self.assertRaisesRegex(stage.StageError, "另一文档"):
            stage.execute(4, other, self.state, self.fake.request, role="character")
        original = self.fake.request
        def duplicate(path, payload=None):
            result = original(path, payload)
            if path == "/docs":
                result["documents"].append({**result["documents"][0], "active": False})
            return result
        with self.assertRaisesRegex(stage.StageError, "多份"):
            stage.execute(4, self.document, self.state, duplicate, role="character")
        self.assertEqual(self.fake.posts, count)

    def test_document_switch_before_write_stops_and_after_write_is_unconfirmed(self):
        other = self.root / "切换.kra"
        other.write_bytes(b"fake")
        original = self.fake.request
        docs = [0]
        def before(path, payload=None):
            if path == "/docs":
                docs[0] += 1
                if docs[0] == 3:
                    self.fake.document = str(other)
            return original(path, payload)
        with self.assertRaisesRegex(stage.StageError, "活动文档错误"):
            stage.execute(4, self.document, self.state, before, role="character")
        self.assertEqual(self.fake.posts, 0)
        self.fake.document = str(self.document)
        def after(path, payload=None):
            result = original(path, payload)
            if path == "/layer/group":
                self.fake.document = str(other)
            return result
        with self.assertRaisesRegex(stage.StageError, "未确认"):
            stage.execute(4, self.document, self.state, after, role="character")
        self.assertEqual(self.fake.posts, 1)
        self.assertIsNotNone(self.data()["pending"])
        self.assertIn("failure_readback_failed", [e["kind"] for e in self.data()["events"]])

    def test_unknown_sidecar_does_not_adopt_old_marked_groups(self):
        self.setup_stage()
        count = self.fake.posts
        with self.assertRaisesRegex(stage.StageError, "不属于此 sidecar"):
            stage.execute(4, self.document, self.root / "other.cwb-stage.json", self.fake.request, role="character")
        self.assertEqual(self.fake.posts, count)

    def test_partial_failures_journal_and_never_automatically_retry(self):
        for mode in ("before", "after", "readback", "wrong_parent", "wrong_id", "exit", "interrupt"):
            with self.subTest(mode=mode):
                state = self.root / (mode + ".cwb-stage.json")
                fake = Fake(self.document)
                fake.fail_at, fake.mode = 4, mode
                expected = KeyboardInterrupt if mode == "interrupt" else stage.StageError
                with self.assertRaises(expected):
                    stage.execute(4, self.document, state, fake.request, role="character")
                data = json.loads(state.read_text(encoding="utf-8"))
                self.assertIsNotNone(data["pending"])
                self.assertEqual(data["groups"]["character/00_简要意图与保持要点"]["status"], "verified")
                self.assertIn("request", data["pending"])
                self.assertIn("unconfirmed", [e["kind"] for e in data["events"]])
                self.assertIn("failure_readback_failed" if mode == "readback" else "failure_readback",
                              [e["kind"] for e in data["events"]])
                count = fake.posts
                fake.read_fail = False
                with self.assertRaisesRegex(stage.StageError, "未确认操作"):
                    stage.execute(4, self.document, state, fake.request, role="character")
                self.assertEqual(fake.posts, count)

    def test_first_journal_write_failure_prevents_post(self):
        with patch.object(stage.Store, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.setup_stage()
        self.assertEqual(self.fake.posts, 0)

    def test_pending_journal_write_failure_prevents_post(self):
        original = stage.Store.save
        def fail(store, data):
            if data["pending"]:
                raise OSError("cannot persist intent")
            return original(store, data)
        with patch.object(stage.Store, "save", fail):
            with self.assertRaises(OSError):
                self.setup_stage()
        self.assertEqual(self.fake.posts, 0)

    def test_post_response_journal_failure_retains_durable_intent(self):
        original = stage.Store.save
        def no_result_writes(store, data):
            if data["pending"] and "response" in data["pending"]:
                raise OSError("disk full after POST")
            return original(store, data)
        with patch.object(stage.Store, "save", no_result_writes):
            with self.assertRaisesRegex(stage.StageError, "记录写入失败"):
                self.setup_stage()
        data = self.data()
        self.assertIsNotNone(data["pending"])
        self.assertEqual(data["pending"]["request"]["name"], self.fake.root["children"][0]["name"])
        self.assertEqual(self.fake.posts, 1)
        with self.assertRaisesRegex(stage.StageError, "未确认操作"):
            self.setup_stage()
        self.assertEqual(self.fake.posts, 1)


    def test_bad_sidecar_and_project_state_never_overwritten(self):
        self.state.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(stage.StageError, "损坏"):
            self.setup_stage()
        self.assertEqual(self.state.read_text(), "{broken")
        self.state.write_text('{"schema_version":99}')
        with self.assertRaisesRegex(stage.StageError, "未知版本"):
            self.setup_stage()
        self.assertEqual(self.state.read_text(), '{"schema_version":99}')
        project = self.root / "project_state.json"
        project.write_text('{"schema_version":1}')
        with self.assertRaisesRegex(stage.StageError, "独立 sidecar"):
            stage.execute(4, self.document, project, self.fake.request, role="character")
        self.assertEqual(project.read_text(), '{"schema_version":1}')
        self.assertEqual(self.fake.posts, 0)

    def test_symlink_state_rejected_and_lock_serializes(self):
        real = self.root / "real.json"
        real.write_text("{}")
        self.state.symlink_to(real)
        with self.assertRaisesRegex(stage.StageError, "符号链接"):
            self.setup_stage()
        state = self.root / "locktest.cwb-stage.json"
        store = stage.Store(state)
        with store.locked():
            with self.assertRaisesRegex(stage.StageError, "忙"):
                stage.execute(4, self.document, state, self.fake.request, role="character")
        self.assertEqual(self.fake.posts, 0)

    def test_cli_setup_and_status_offline(self):
        def http(url, payload=None, timeout=60):
            self.assertTrue(url.startswith(cwb.Krita))
            return self.fake.request(url[len(cwb.Krita):], payload)
        with patch.object(cwb, "http", side_effect=http), contextlib.redirect_stdout(io.StringIO()) as out:
            cwb.main(["stage", "setup", "2", "--role", "ideation", "--document", str(self.document), "--state", str(self.state)])
        self.assertEqual(json.loads(out.getvalue())["stage"], 2)
        with patch.object(cwb, "http", side_effect=AssertionError("no network")), contextlib.redirect_stdout(io.StringIO()) as out:
            cwb.main(["stage", "status", "--state", str(self.state)])
        self.assertEqual(json.loads(out.getvalue())["schema_version"], 2)


    def test_help_without_stage_module_service_cache_or_bytecode(self):
        script = self.root / "core" / "cwb.py"
        script.parent.mkdir()
        shutil.copyfile(ROOT / "core" / "cwb.py", script)
        for args in (["--help"], ["stage", "--help"], ["stage", "setup", "--help"],
                     ["stage", "status", "--help"]):
            wf = self.root / "absent-workflows"
            process = subprocess.run([sys.executable, str(script), *args], cwd=self.root,
                                     env={**os.environ, "CWB_WF_DIR": str(wf)}, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertFalse(wf.exists())
            self.assertFalse((script.parent / "__pycache__").exists())
            self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
