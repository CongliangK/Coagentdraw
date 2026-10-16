"""PR-02: finite, versioned workflow vocabulary; no I/O or artistic approval."""
LEGACY_WORKFLOW = "legacy-eight-stage-v1"
WORKFLOW = "krita-character-illustration-v1"
STAGES = (
    ("text_seeds", "文本创作起点"),
    ("research_imagination", "联想与前期调研"),
    ("concept_selection", "创意汇总与选择"),
    ("character_exploration", "角色视觉探索"),
    ("character_resolution", "角色方案收敛"),
    ("turnaround", "三视图设定与核对"),
    ("illustration_planning", "插画方案探索"),
    ("illustration_production", "插画制作与交付"),
)
DOCUMENT_STAGES = {"ideation": (1, 2, 3), "character": (4, 5),
                   "turnaround": (6,), "illustration": (7, 8)}
# Each tuple describes a group and its children, not generated artwork.
DOCUMENT_GROUPS = {
    "ideation": (("00_阅读说明与当前问题", ()), ("10_素材簇", ()),
                 ("20_待比较创意", ()), ("30_本轮选中", ()), ("80_暂存与未选", ())),
    "character": (("00_简要意图与保持要点", ()), ("10_参考", ()),
                  ("20_探索", ("剪影", "服装结构", "粗线稿", "配色")),
                  ("30_主稿", ("线稿", "基础色", "局部说明")),
                  ("40_修改候选", ()), ("90_诊断与比较", ())),
    "turnaround": (("00_说明与基准线", ()),
                   ("10_正面", ("线稿", "基础色")),
                   ("20_侧面_待确认角色自身左右", ("线稿", "基础色")),
                   ("30_背面", ("线稿", "基础色")),
                   ("40_局部结构与色板", ()), ("80_旧版本与待确认候选", ())),
    "illustration": (("00_角色参考与画面意图", ()), ("10_构图候选", ()),
                     ("20_确认稿", ()),
                     ("30_制作", ("结构与底画", "光影", "关键区域", "材质细节", "全图调整")),
                     ("40_AI修改候选", ()), ("90_诊断与比较", ())),
}
# Minimal declared outcomes; software checks references, never aesthetic sufficiency.
REQUIRED_ROLES = {
    2: {"research_board"}, 4: {"character_exploration"},
    5: {"character_master_lineart", "character_master_basecolor"},
    6: {"turnaround_front", "turnaround_side", "turnaround_back"},
    7: {"illustration_plan"}, 8: {"final_illustration", "editable_source", "delivery_manifest"},
}


def document_specs(role):
    result = []
    for label, children in DOCUMENT_GROUPS[role]:
        key = role + "/" + label
        result.append((key, label, None))
        result.extend((key + "/" + child, child, key) for child in children)
    return result
