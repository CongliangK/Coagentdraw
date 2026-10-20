"""Offline checks for the design skill bundle and its non-destructive installer."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "design"
INSTALLER = DESIGN / "scripts" / "install_skill.sh"
SKILL_NAME = "coagentdraw-design"
STAGES = {
    "text_seeds": "01_文本创作起点.md",
    "research_imagination": "02_联想与前期调研.md",
    "concept_selection": "03_创意汇总与选择.md",
    "character_exploration": "04_角色视觉探索.md",
    "character_resolution": "05_角色方案收敛.md",
    "turnaround": "06_三视图设定与核对.md",
    "illustration_planning": "07_插画方案探索.md",
    "illustration_production": "08_插画制作与交付.md",
}


class DesignSkillBundleTests(unittest.TestCase):
    def test_manifest_has_discoverable_metadata(self):
        text = (DESIGN / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(match)
        metadata = dict(line.split(": ", 1) for line in match[1].splitlines())
        self.assertEqual(metadata["name"], SKILL_NAME)
        self.assertRegex(metadata["name"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertTrue(metadata["description"].strip())
        self.assertIn("角色", metadata["description"])
        self.assertNotIn(str(ROOT), text)
        self.assertNotIn("/home/", text)

    def test_all_eight_stages_route_to_existing_manuals(self):
        text = (DESIGN / "SKILL.md").read_text(encoding="utf-8")
        routes = re.findall(r"\]\(<(执行手册/[0-9]{2}_[^>]+\.md)>\)", text)
        self.assertEqual(set(routes), {f"执行手册/{name}" for name in STAGES.values()})
        self.assertEqual(len(routes), 8)
        for stage_id, name in STAGES.items():
            with self.subTest(stage=stage_id):
                self.assertIn(f"`{stage_id}`", text)
                self.assertTrue((DESIGN / "执行手册" / name).is_file())

    def test_bundle_and_manual_navigation_links_resolve(self):
        documents = [
            DESIGN / "SKILL.md",
            DESIGN / "README.md",
            DESIGN / "执行手册" / "README.md",
            DESIGN / "执行手册" / "01_文本创作起点.md",
        ]
        for document in documents:
            links = re.findall(r"\]\(<([^>]+)>\)", document.read_text(encoding="utf-8"))
            self.assertTrue(links, document)
            for link in links:
                with self.subTest(document=document.name, link=link):
                    self.assertNotIn("://", link)
                    self.assertFalse(Path(link).is_absolute())
                    self.assertTrue((document.parent / link.split("#", 1)[0]).exists())

    def phase_one_tables(self):
        text = (DESIGN / "执行手册" / "01_文本创作起点.md").read_text(encoding="utf-8")
        tables = []
        current = []
        for line in text.splitlines() + [""]:
            if line.startswith("|"):
                current.append([cell.strip() for cell in line.strip("|").split("|")])
            elif current:
                tables.append(current)
                current = []
        return text, tables

    def test_phase_one_has_only_requirements_and_visible_design(self):
        text, tables = self.phase_one_tables()
        self.assertEqual(len(tables), 2)
        self.assertEqual(re.findall(r"^## .+$", text, re.MULTILINE), [
            "## 一、作者要求与设计锚点", "## 二、可见形象",
        ])
        self.assertEqual([row[0] for row in tables[0][2:]], [
            "作者要求", "角色标识", "设计目的", "核心幻想", "视觉重心",
            "表现风格", "时间锚点", "身份与行动需求", "时代与文化参照",
        ])
        self.assertIn("`text_seeds`", text)
        self.assertIn("视觉设计版", text)
        self.assertNotIn("<details>", text)
        self.assertNotIn("</details>", text)

    def test_visual_card_covers_distinct_appearance_dimensions(self):
        _, tables = self.phase_one_tables()
        visual = tables[1]
        self.assertEqual(visual[0], [
            "维度", "填写提示", "已有条件（注明依据）", "初步探索方向（未定稿）",
        ])
        dimensions = [row[0] for row in visual[2:]]
        self.assertCountEqual(dimensions, [
            "身体与比例", "面部与辨识特征", "发型与头部造型", "剪影与形状语言",
            "服装", "武器／施法媒介", "工具与配饰", "配色", "材质与表面",
            "纹样与识别标记", "神态与姿态",
        ])
        self.assertEqual(len(dimensions), len(set(dimensions)))
        for row in visual[2:]:
            with self.subTest(dimension=row[0]):
                self.assertEqual(len(row), 4)
                self.assertTrue(row[1])
                self.assertEqual(row[2:], ["", ""])
        prompts = {row[0]: row[1] for row in visual[2:]}
        for term in ["主色", "辅助色", "强调色", "面积", "位置"]:
            self.assertIn(term, prompts["配色"])
        self.assertIn("有无", prompts["武器／施法媒介"])
        for term in ["主形", "辅形", "方向", "比例", "分布", "整身轮廓"]:
            self.assertIn(term, prompts["剪影与形状语言"])

    def test_card_fields_are_unique(self):
        _, tables = self.phase_one_tables()
        all_fields = [row[0] for table in tables for row in table[2:]]
        self.assertEqual(len(all_fields), len(set(all_fields)))

    def test_core_fantasy_is_explicit_and_audience_facing(self):
        _, tables = self.phase_one_tables()
        for table in [tables[0]]:
            prompts = {row[0]: row[1] for row in table[2:]}
            self.assertIn("核心幻想", prompts)
            for term in ["观众", "玩家", "读者", "幻想", "想象"]:
                with self.subTest(table=table[2][0], term=term):
                    self.assertIn(term, prompts["核心幻想"])
        for document in [DESIGN / "SKILL.md", DESIGN / "Krita中枢创作流程_正式执行规范.md"]:
            with self.subTest(document=document.name):
                self.assertIn("核心幻想", document.read_text(encoding="utf-8"))

    def test_design_purpose_and_style_prompts_use_precise_terms(self):
        _, tables = self.phase_one_tables()
        anchors = {row[0]: row[1] for row in tables[0][2:]}
        self.assertIn("人生/故事阶段", anchors["时间锚点"])
        self.assertIn("时代与文化参照", anchors)
        self.assertEqual(anchors["角色标识"], "角色的名字或代号")
        self.assertEqual(anchors["设计目的"], "用在什么上面，要起到什么效果？")
        for term in ["专业风格名称", "赛璐璐", "ligne claire"]:
            self.assertIn(term, anchors["表现风格"])
        self.assertEqual(tables[0][0], ["项目", "填写提示", "填写内容"])
        self.assertTrue(all(len(row) == 3 for row in tables[0]))

    def test_prompts_are_direct_without_defensive_tails(self):
        _, tables = self.phase_one_tables()
        for table in tables:
            for row in table[2:]:
                with self.subTest(field=row[0]):
                    self.assertNotRegex(row[1], r"不以|不用|不要|不得|不把|而非|说明一次|不写|不为")

    def test_visual_focus_records_concrete_identity_combination(self):
        text, tables = self.phase_one_tables()
        anchors = {row[0]: row[1] for row in tables[0][2:]}
        for term in ["服装", "剪影", "装备", "发廓", "主色块", "共同构成"]:
            self.assertIn(term, anchors["视觉重心"])
        visual_intro = text.split("## 二、可见形象", 1)[1].split("| 维度 |", 1)[0]
        for term in ["一组共同承担角色识别的具体视觉要素", "多项构件协同表达同一核心命题",
                     "服装大形", "身体比例", "关键装备", "发廓", "大色块"]:
            self.assertIn(term, visual_intro)
        documents = [ROOT / "AGENTS.md", DESIGN / "SKILL.md", DESIGN / "README.md",
                     DESIGN / "执行手册/README.md", DESIGN / "Krita中枢创作流程_正式执行规范.md"]
        for document in documents:
            with self.subTest(document=document):
                guidance = document.read_text(encoding="utf-8")
                self.assertRegex(guidance, r"核心(?:视觉|识别)组合")

    def test_whole_figure_structure_precedes_dependent_details(self):
        text, tables = self.phase_one_tables()
        visual = tables[1]
        self.assertEqual([row[0] for row in visual[2:4]], ["剪影与形状语言", "服装"])
        prompts = {row[0]: row[1] for row in visual[2:]}
        for term in ["主服装", "类别", "长度", "体量", "整体剪裁", "开合", "内搭", "局部结构"]:
            self.assertIn(term, prompts["服装"])
        for term in ["整身剪影与主体构成 → 依附结构 → 表面细节",
                     "主体改变时", "重看依附设计", "与作者讨论主体构成及其取舍",
                     "后续研究与草图继续修正"]:
            self.assertIn(term, text)
        skill = (DESIGN / "SKILL.md").read_text(encoding="utf-8")
        for term in ["可讨论的整身粗方案", "不要只提问、留白或罗列小配件",
                     "多项核心要素可以协同承载同一核心命题", "允许反馈后推翻或重构"]:
            self.assertIn(term, skill)

    def test_shape_language_supports_collage_without_blocking_entry(self):
        text, _ = self.phase_one_tables()
        sequence = "整身剪影与主体构成 → 依附结构 → 表面细节"
        self.assertIn(sequence, text)
        self.assertLess(text.index(sequence), text.index("核心是一组"))
        for term in ["圆、三角、方形", "方向、比例、分布", "轮廓节奏",
                     "调整身体、服装与装备形成的大形", "情绪倾向结合重心、曲直与语境判断",
                     "当前锚点可以直接带入检索和拼贴", "不必先完成整卡、抽象几何方案或独立剪影"]:
            self.assertIn(term, text)
        skill = (DESIGN / "SKILL.md").read_text(encoding="utf-8")
        for term in ["有锚点就能进入试拼", "形状语言用于分析与调整",
                     "不把抽象几何讨论作为找图的前置", "不把几何形状机械等同于人格",
                     "拼贴任务同时读取02和04", "进入清晰主稿时再读05"]:
            self.assertIn(term, skill)

    def test_agents_and_readme_link_to_skill(self):
        for name in ["AGENTS.md", "README.md"]:
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(document=name):
                self.assertIn(SKILL_NAME, text)
                self.assertIn("(<design/SKILL.md>)", text)


class DesignSkillInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="design-skill-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.destination = self.base / "技能 目录"
        self.environment = dict(os.environ, HOME=str(self.base / "home"))

    def install(self, *args, script=INSTALLER):
        return subprocess.run(
            ["bash", str(script), *map(str, args)],
            cwd=self.base,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_fresh_install_and_idempotence(self):
        result = self.install(self.destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        link = self.destination / SKILL_NAME
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), DESIGN.resolve())
        before = link.lstat().st_ino
        result = self.install(self.destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(link.lstat().st_ino, before)
        self.assertIn("Already linked", result.stdout)

    def test_default_destination_uses_home(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        link = Path(self.environment["HOME"]) / ".agents" / "skills" / SKILL_NAME
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), DESIGN.resolve())

    def test_installed_bundle_keeps_routes_and_installer_accessible(self):
        self.assertEqual(self.install(self.destination).returncode, 0)
        link = self.destination / SKILL_NAME
        self.assertEqual((link / "SKILL.md").resolve(), DESIGN / "SKILL.md")
        for manual in STAGES.values():
            with self.subTest(manual=manual):
                self.assertTrue((link / "执行手册" / manual).is_file())
        result = self.install(self.destination, script=link / "scripts" / "install_skill.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Already linked", result.stdout)

    def test_existing_file_is_not_overwritten(self):
        self.destination.mkdir()
        target = self.destination / SKILL_NAME
        target.write_text("keep this file", encoding="utf-8")
        result = self.install(self.destination)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Refusing to replace", result.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep this file")

    def test_existing_directory_is_not_replaced(self):
        target = self.destination / SKILL_NAME
        target.mkdir(parents=True)
        marker = target / "user-work.txt"
        marker.write_text("keep this work", encoding="utf-8")
        result = self.install(self.destination)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(target.is_symlink())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep this work")

    def test_wrong_and_broken_symlinks_are_not_replaced(self):
        other = self.base / "other-skill"
        other.mkdir()
        for label, source in [("wrong", other), ("broken", self.base / "missing")]:
            with self.subTest(link=label):
                destination = self.base / label
                destination.mkdir()
                target = destination / SKILL_NAME
                target.symlink_to(source, target_is_directory=True)
                before = target.readlink()
                result = self.install(destination)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(target.readlink(), before)

    def test_help_and_extra_arguments_have_no_install_side_effects(self):
        result = self.install("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)
        result = self.install(self.destination, "unexpected")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.destination.exists())
        self.assertFalse(Path(self.environment["HOME"]).exists())


if __name__ == "__main__":
    unittest.main()
