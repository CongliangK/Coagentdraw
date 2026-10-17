#!/usr/bin/env python3
"""cwb - Cowork Bridge 客户端(智能体/命令行侧)。

连接 Krita 内的 krita_cowork 控制面(127.0.0.1:51080)。
用法示例:
  cwb ping
  cwb layers
  cwb png <layer_id> out.png
  cwb new "作品" 1328 1328
  cwb add out.png "背景"
  cwb save 作品.kra
  cwb comfy-t2i "a castle on a cliff" --w 1328 --h 1328 --add "天空"
  cwb comfy-edit base.png "change ..." --image2 ref.png --add "换装"
"""
import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request

Krita = "http://127.0.0.1:51080"
Comfy = "http://127.0.0.1:8188"
_HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.environ.get("CWB_WF_DIR",
                        os.path.normpath(os.path.join(_HERE, os.pardir, "comfyui")))
WORKDIR = os.path.join(WF_DIR, "cowork_cache")


def http(url, payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        sys.exit("HTTP {0}: {1}".format(e.code, body))


def add_project_parser(sub):
    parser = sub.add_parser("project", help="离线项目状态与成果索引（不修改作品）")
    commands = parser.add_subparsers(dest="project_cmd", required=True)
    p = commands.add_parser("init", help="初始化 schema2/workflow 索引；不创建空表/阶段目录/KRA")
    p.add_argument("name")
    p.add_argument("--root", help="项目目录；默认仓库 _本地暂存/<项目名>")
    for command in ("status", "migration-plan", "upgrade", "edit", "stage", "record", "idea", "document", "decision"):
        p = commands.add_parser(command)
        p.add_argument("root", help="项目目录；相对于当前工作目录")
        if command not in ("status", "migration-plan"):
            p.add_argument("--expect-revision", type=int, required=command == "upgrade", help="拒绝基于过期状态的写入；upgrade 必填")
        if command == "upgrade":
            p.add_argument("--source-sha256", required=True, help="migration-plan 返回的原状态摘要")
            p.add_argument("--authorize", required=True, help="明确升级授权的实际来源/人工转录；不是身份认证")
        if command == "edit":
            p.add_argument("--intent", help="完整意图卡文本（可手工填写）")
            p.add_argument("--pillar", action="append", help="替换设计支柱列表，可重复")
            p.add_argument("--anchor", action="append", help="替换风格锚点列表，可重复")
            p.add_argument("--clear-pillars", action="store_true")
            p.add_argument("--clear-anchors", action="store_true")
        elif command == "stage":
            p.add_argument("number", type=int, choices=range(1, 9))
            p.add_argument("--status", choices=("pending", "in_progress", "awaiting_review", "completed"), default="in_progress")
            p.add_argument("--approval", help="completed 必须引用本阶段有效批准 UUID")
            p.add_argument("--note", default="")
        elif command == "record":
            p.add_argument("path", help="相对于项目目录的现有文件路径；不复制/移动/修改文件")
            p.add_argument("--stage", type=int, choices=range(1, 9))
            p.add_argument("--category", choices=("temporary", "final"), required=True)
            p.add_argument("--note", default="")
            p.add_argument("--object-id", help="稳定作品对象 ID；多版本沿用，默认新 UUID")
            p.add_argument("--version", help="不可复用的对象版本 ID；默认新 UUID")
            p.add_argument("--role", default="unspecified", help="成果身份，如 character_master_lineart；不推断批准")
            p.add_argument("--document-id", help="已登记的工作文档 ID")
            p.add_argument("--kind", choices=("working", "checkpoint"), default="working")
            p.add_argument("--group-marker", help="已核对的持久随机 UUID；不是 runtime layer ID/token")
            p.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"), help="整文档像素坐标；不使用屏幕坐标")
            p.add_argument("--preview", help="项目内现有预览；只记录字节摘要，不冒称采样像素摘要")
        elif command == "idea":
            p.add_argument("id", help="新的稳定 ID；拆分/合并/替代用新 ID，保留来源")
            p.add_argument("title")
            text = p.add_mutually_exclusive_group(required=True)
            text.add_argument("--text", default="", help="轻量短文本；不要求完整意图卡")
            text.add_argument("--text-ref", help="项目内 Markdown 路径，可带 #稳定段落ID")
            p.add_argument("--source", action="append", default=[], help="来源起点 ID，可重复；一源多分支/多源合并")
            p.add_argument("--cluster", action="append", default=[], help="素材簇 ID，仅引用，不导入画布")
        elif command == "document":
            p.add_argument("id")
            p.add_argument("path", help="项目内已保存 KRA；工作文件可正常保存，不改变旧确认快照")
            p.add_argument("--role", choices=("ideation", "character", "turnaround", "illustration"), required=True)
            p.add_argument("--concept", help="关联起点/创意 ID；非 ideation 必填")
            p.add_argument("--illustration", help="插画 ID；illustration 角色必填")
        elif command == "decision":
            p.add_argument("kind", choices=("idea", "asset", "stage"))
            p.add_argument("target", help="对象 ID；stage 使用稳定 stage_id")
            p.add_argument("action", choices=("approve", "select", "reject", "defer"))
            p.add_argument("--scope", required=True, help="具体适用范围；不产生全局永久禁令")
            p.add_argument("--source", required=True, help="实际人类指令来源/人工转录，非模型自报 human")
            p.add_argument("--reason", default="")
            p.add_argument("--outcome", action="append", default=[], help="stage approve 必要成果 idea:ID / asset:ID，可重复")



def _opacity_arg(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("opacity 必须是 0..1 的有限数值")
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("opacity 必须是 0..1 的有限数值")
    return number


def add_stage_parser(sub):
    parser = sub.add_parser("stage", help="八阶段组预设（独立身份 sidecar；不修改项目状态）")
    commands = parser.add_subparsers(dest="stage_cmd", required=True)
    for command in ("setup", "import-diagnostic", "status"):
        p = commands.add_parser(command)
        p.add_argument("--state", required=True, help="独立 *.cwb-stage.json 文件；保留并备份")
        if command == "setup":
            p.add_argument("number", type=int, choices=range(1, 9))
            p.add_argument("--workflow", choices=("legacy-eight-stage-v1", "krita-character-illustration-v1"),
                           help="新 sidecar 默认旧流程；已有 sidecar 省略则沿用，不自动迁移")
            p.add_argument("--role", choices=("ideation", "character", "turnaround", "illustration"),
                           help="新 workflow 首次必填；按文档角色建组，不在一文档建八阶段")
        if command != "status":
            p.add_argument("--document", required=True, help="已保存的活动 .kra 绝对或相对路径")
        if command == "import-diagnostic":
            p.add_argument("png", help="显式提供 #18 已有 PNG；不生成诊断图")
            p.add_argument("--name", default="诊断 PNG")


def add_compare_parser(sub):
    parser = sub.add_parser("compare", help="离线 PNG 并排比较与决定记录（不修改 Krita，不接入 #25）")
    commands = parser.add_subparsers(dest="compare_cmd", required=True)
    p = commands.add_parser("create", help="手工 before/after → 新目录；可选 CLI-only Pillow")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("output", help="尚不存在的目录，父目录必须存在；绝不覆盖旧结果")
    p.add_argument("--crop", nargs=4, type=int, metavar=("X", "Y", "W", "H"), help="共同原生像素裁切，不缩放")
    for field in ("source-version", "candidate-version", "prompt", "workflow", "document-ref", "candidate-layer-ref"):
        p.add_argument("--" + field, help="手工提供的未验证关联；省略为未知，不读取所指文件")
    p.add_argument("--seed", type=int, help="手工提供；省略为未知")
    p = commands.add_parser("show", help="重开查询资产、完整事件和理由；无需 Pillow")
    p.add_argument("output")
    p = commands.add_parser("decide", help="仅记录；hide/withdraw 不会隐藏图层或恢复原投影")
    p.add_argument("output")
    p.add_argument("action", choices=("accept", "reject", "hide", "withdraw"))
    p.add_argument("--reason", required=True)
    p.add_argument("--expect-sequence", type=int, required=True, help="show 返回的事件序号（首次为 0）")


def add_layout_parser(sub):
    parser = sub.add_parser("layout", help="已有六图离线设定页；不生成视图、不写 Krita")
    commands = parser.add_subparsers(dest="layout_cmd", required=True)
    p = commands.add_parser("fixture", help="新目录中创建教学占位 PNG，非真实角色案例")
    p.add_argument("output")
    p = commands.add_parser("plan", help="校验来源/尺度/人工锚点并输出 manifest；不需 Pillow")
    p.add_argument("spec")
    p = commands.add_parser("render", help="新目录保留原图/独立缩放 PNG/文本 SVG；仅CLI可选Pillow")
    p.add_argument("spec")
    p.add_argument("output")
    p = commands.add_parser("prepare", help="只打印人工执行 payload；绝不自动 apply/联网")
    p.add_argument("bundle")
    p.add_argument("--document", required=True, help="已保存的专用目标 .kra；离线不验证活动状态")


def add_review_parser(sub):
    parser = sub.add_parser("review", help="离线审评接口准备/手工反馈导入；无后端调用")
    commands = parser.add_subparsers(dest="review_cmd", required=True)
    p = commands.add_parser("prepare", help="仅准备请求；不发送；PNG 校验需可选 CLI Pillow")
    for role in ("original", "silhouette", "grayscale"):
        p.add_argument("--" + role, required=True, help="用户已有的同尺寸 PNG；不生成诊断图")
    p.add_argument("--context", required=True, help="手工 JSON：intent（未知 null）、pillars（未知 []）")
    p.add_argument("output", help="全新独立目录；父目录须存在，不覆盖旧工件")
    p = commands.add_parser("import", help="显式导入用户提供的 human/model JSON；不调用模型")
    p.add_argument("prepared", help="prepare 输出目录；原始来源必须未改变")
    p.add_argument("feedback", help="用户手工提供的反馈 JSON，可含人工 confirm/amend/reject")
    p.add_argument("output", help="全新导出目录：Markdown + JSON + 原始反馈；不覆盖历史")


def add_style_parser(sub):
    parser = sub.add_parser("style", help="离线版本化风格 sidecar/提示/标杆比较；不生成、不写项目主状态")
    commands = parser.add_subparsers(dest="style_cmd", required=True)
    for command in ("init", "show", "edit", "state", "prompt", "compare"):
        p = commands.add_parser(command)
        p.add_argument("root", help="已有 CAD-19 项目目录；固定独立 style_tokens.cwb sidecar")
        if command in ("init", "edit", "state"):
            p.add_argument("--actor", required=True, help="人工记录操作者；不验证身份")
            p.add_argument("--reason", required=True)
        if command in ("edit", "state"):
            p.add_argument("--expect-revision", type=int, required=True)
        if command == "edit":
            p.add_argument("--config", required=True, help="完整 JSON 配置；未知字段/删除/锁定修改拒绝")
        elif command == "state":
            p.add_argument("kind", choices=("references", "style_tokens"))
            p.add_argument("id")
            p.add_argument("action", choices=("lock", "unlock", "cancel", "enable"))
        if command in ("prompt", "compare"):
            p.add_argument("--scope", required=True, help="具体作品范围；精确匹配或配置的全局 *")
        if command == "prompt":
            p.add_argument("--output", help="可选显式导出 JSON 新文件；不覆盖，不接入生成")
        elif command == "compare":
            p.add_argument("current", help="手工当前 PNG；与所选参考原始尺寸须相同")
            p.add_argument("output", help="尚不存在的 CAD-23 比较目录；左参考、右当前")
            p.add_argument("--reference", required=True, help="显式选择唯一参考 id")
            p.add_argument("--crop", type=int, nargs=4, required=True, metavar=("X", "Y", "W", "H"))
            p.add_argument("--candidate-version", help="手工版本；未知则 null")
            for name in ("palette", "proportion", "dominant-shape", "detail-density"):
                p.add_argument("--" + name, help="人工比较文字；省略为未评审，不自动艺术评分")


def add_candidates_parser(sub):
    parser = sub.add_parser("candidates", help="有限离线候选计划/现成剪影拼板；不生成、不写 Krita")
    commands = parser.add_subparsers(dest="candidates_cmd", required=True)
    p = commands.add_parser("plan", help="稳定编号/seed 的 prepared_not_executed 计划；无需 Pillow")
    p.add_argument("prompt")
    p.add_argument("output", help="尚不存在的目录；父目录须存在，旧输出不覆盖")
    p.add_argument("--count", type=int, required=True)
    p.add_argument("--seed-start", type=int, required=True)
    p.add_argument("--seed-end", type=int, required=True)
    p.add_argument("--direction", action="append", default=[], help="人工概念方向，轮流分配；省略为未知")
    p.add_argument("--workflow-source", help="人工来源说明；不读取工作流、不执行")
    p = commands.add_parser("board", help="显式 SHA 登记的透明黑 PNG → contain 拼板；失败保留占位/退出 2")
    p.add_argument("plan", help="plan 输出的 manifest.json")
    p.add_argument("registration", help="逐候选显式登记 JSON；未知 seed/方向/工作流用 null")
    p.add_argument("output", help="尚不存在的目录；父目录须存在，旧输出不覆盖")
    p.add_argument("--columns", type=int, default=4)
    p.add_argument("--cell-width", type=int, default=256)
    p.add_argument("--cell-height", type=int, default=256)
    p.add_argument("--select", action="append", default=[], help="入选候选 ID；仅导出清单/新层 payload")
    p.add_argument("--parent-ref", help="未验证的目标组 ID；不连接 Krita")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version="cwb: project schema1(read-only)/2; workflow krita-character-illustration-v1; stage sidecar1/2")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping")
    sub.add_parser("docs")
    sub.add_parser("layers")
    p = sub.add_parser("png"); p.add_argument("id"); p.add_argument("path")
    p.add_argument("--underlay", choices=("magenta", "none"), default="magenta",
                   help="透明区渲染为不透明洋红底衬(默认,供智能体读图);none 保留原生 alpha(如喂 ComfyUI)")
    for command in ("export", "selection-png", "snapshot"):
        p = sub.add_parser(command, help="整画布只读 PNG 导出（snapshot 同回调配对）")
        if command == "snapshot":
            p.add_argument("projection_path")
            p.add_argument("selection_path")
        else:
            p.add_argument("path")
        if command != "export":
            p.add_argument("--full-canvas", action="store_true", help="显式以全白蒙版替代当前选区")
        p.add_argument("--overwrite", action="store_true", help="显式覆盖输出；两个文件不是原子事务")
        p.add_argument("--expect-document-token", help="拒绝失效或非当前文档的会话 token")
    p = sub.add_parser("new"); p.add_argument("name"); p.add_argument("w", type=int)
    p.add_argument("h", type=int)
    p = sub.add_parser("add"); p.add_argument("png"); p.add_argument("name")
    p.add_argument("--above"); p.add_argument("--x", type=int, default=0)
    p.add_argument("--y", type=int, default=0)
    p.add_argument("--parent", help="父级组 ID；省略则挂到根")
    placement = p.add_mutually_exclusive_group()
    placement.add_argument("--mode", choices=("keep_size", "fit_canvas", "insert_at_offset"),
                           help="原尺寸原点 / 等比缩小居中（不放大） / 原尺寸偏移；默认兼容已有 offset")
    placement.add_argument("--fit-canvas", action="store_true", default=None,
                           help="--mode fit_canvas 的兼容别名，不可搭配非零 --x/--y")
    p = sub.add_parser("group", help="创建空图层组（可嵌套）")
    p.add_argument("name")
    p.add_argument("--parent", help="父级组 ID；省略则挂到根")
    p.add_argument("--above", help="父级中的同级节点 ID")
    p = sub.add_parser("props", help="设置图层属性（非原子事务，失败后回读 layers）")
    p.add_argument("id")
    p.add_argument("--alpha-locked", choices=("true", "false"))
    p.add_argument("--opacity", type=_opacity_arg, help="0..1；0.5 量化为 128/255")
    p.add_argument("--blending-mode", choices=("normal", "multiply", "overlay", "screen"))
    p = sub.add_parser("visible"); p.add_argument("id"); p.add_argument("v")
    p = sub.add_parser("save"); p.add_argument("path")
    for command, default in (("comfy-t2i", "wf_qwen21_t2i_api.json"),
                             ("comfy-edit", "wf_qwen21_edit_api.json")):
        p = sub.add_parser(command)
        if command == "comfy-edit":
            p.add_argument("image1")
            p.add_argument("--image2")
        else:
            p.add_argument("--w", type=int)
            p.add_argument("--h", type=int)
        p.add_argument("prompt")
        p.add_argument("--wf", default=default, help="CWB_WF_DIR 内的工作流 JSON（需要同名 .contract.json）")
        p.add_argument("--seed", type=int)
        p.add_argument("--steps", type=int)
        p.add_argument("--cfg", type=float)
        p.add_argument("--denoise", type=float)
        p.add_argument("--timeout", type=float, default=1800, help="上传、提交、轮询、下载总秒数")
        p.add_argument("--add", help="单一输出时导入为新的 Krita 图层；多输出必须手动选择")
    p = sub.add_parser("comfy-replay", help="显式再次提交保存的完整快照（会创建新任务）")
    p.add_argument("record", help="UUID 运行目录内的 run.json")
    p.add_argument("--timeout", type=float, default=1800)

    add_project_parser(sub)
    add_stage_parser(sub)
    add_compare_parser(sub)
    add_candidates_parser(sub)
    add_style_parser(sub)
    add_review_parser(sub)
    add_layout_parser(sub)
    p = sub.add_parser("export-project", help="离线复制明确选定的正式成果，不上传")
    p.add_argument("root", help="项目根目录")
    p.add_argument("output", help="尚不存在的目录或 --zip 文件；父目录须存在")
    p.add_argument("--asset", action="append", required=True, help="明确选择 final 资产 UUID，可重复")
    p.add_argument("--role", action="append", default=[], metavar="ROLE=UUID",
                   help="用途：kra/final_png/setting/workflow/design_description/reference_sources/version_record")
    p.add_argument("--metadata", help="项目内 JSON sidecar：description、references、versions")
    p.add_argument("--zip", action="store_true", help="仅生成本地 ZIP")

    a = ap.parse_args(argv)
    if a.cmd == "candidates":
        from cwb_candidates import run as run_candidates
        run_candidates(a)
    elif a.cmd == "style":
        from cwb_style import run as run_style
        run_style(a)
    elif a.cmd == "review":
        from cwb_review import run as run_review
        run_review(a)
    elif a.cmd == "layout":
        from cwb_layout import run as run_layout
        run_layout(a)
    elif a.cmd == "compare":
        from cwb_compare import run as run_compare
        run_compare(a)
    elif a.cmd == "stage":
        from cwb_stage import run as run_stage
        run_stage(a, http, Krita)
    elif a.cmd == "project":
        from cwb_project import run as run_project
        run_project(a)
    elif a.cmd == "export-project":
        from cwb_export import run as run_export
        run_export(a)
    elif a.cmd == "ping":
        print(json.dumps(http(Krita + "/ping"), ensure_ascii=False))
    elif a.cmd == "docs":
        print(json.dumps(http(Krita + "/docs"), ensure_ascii=False, indent=1))
    elif a.cmd == "layers":
        print(json.dumps(http(Krita + "/layers"), ensure_ascii=False, indent=1))
    elif a.cmd == "png":
        print(json.dumps(http(Krita + "/layer/png",
                              {"id": a.id, "path": os.path.abspath(a.path),
                               "underlay": a.underlay},
                              timeout=120), ensure_ascii=False))
    elif a.cmd in ("export", "selection-png", "snapshot"):
        payload = {"overwrite": a.overwrite}
        if a.expect_document_token is not None:
            payload["expected_document_token"] = a.expect_document_token
        if a.cmd == "snapshot":
            payload.update(projection_path=os.path.abspath(os.path.expanduser(a.projection_path)),
                           selection_path=os.path.abspath(os.path.expanduser(a.selection_path)))
        else:
            payload["path"] = os.path.abspath(os.path.expanduser(a.path))
        if a.cmd != "export":
            payload["selection_mode"] = "full_canvas" if a.full_canvas else "selection"
        route = {"export": "/doc/export", "selection-png": "/doc/selection_png",
                 "snapshot": "/doc/snapshot"}[a.cmd]
        print(json.dumps(http(Krita + route, payload, timeout=120), ensure_ascii=False, indent=1))
    elif a.cmd == "new":
        print(json.dumps(http(Krita + "/doc/new",
                              {"name": a.name, "width": a.w, "height": a.h}),
                          ensure_ascii=False))
    elif a.cmd == "add":
        payload = {"png_path": os.path.abspath(os.path.expanduser(a.png)), "name": a.name,
                   "above": a.above, "offset_x": a.x, "offset_y": a.y, "parent_id": a.parent}
        if a.mode is not None:
            payload["mode"] = a.mode
        if a.fit_canvas is not None:
            payload["fit_canvas"] = a.fit_canvas
        print(json.dumps(http(Krita + "/layer/add", payload, timeout=120), ensure_ascii=False))
    elif a.cmd == "group":
        print(json.dumps(http(Krita + "/layer/group", {
            "name": a.name, "parent_id": a.parent, "above": a.above,
        }), ensure_ascii=False))
    elif a.cmd == "props":
        payload = {key: getattr(a, key) for key in ("alpha_locked", "opacity", "blending_mode")
                   if getattr(a, key) is not None}
        if not payload:
            ap.error("props 至少需要一个属性选项")
        if "alpha_locked" in payload:
            payload["alpha_locked"] = payload["alpha_locked"] == "true"
        print(json.dumps(http(Krita + "/layer/props", {"id": a.id, **payload}), ensure_ascii=False))
    elif a.cmd == "visible":
        print(json.dumps(http(Krita + "/layer/visible",
                              {"id": a.id, "visible": a.v in ("1", "true")}),
                          ensure_ascii=False))
    elif a.cmd == "save":
        print(json.dumps(http(Krita + "/doc/save", {"path": os.path.abspath(a.path)},
                              timeout=120), ensure_ascii=False))
    elif a.cmd.startswith("comfy-"):
        from cwb_comfy import ComfyError, execute, load_replay, prepare
        if a.cmd == "comfy-replay":
            graph, contract, previous = load_replay(a.record, Comfy)
            result, path = execute(graph, contract, WORKDIR, Comfy, timeout=a.timeout, replay=previous)
        else:
            mode = "t2i" if a.cmd == "comfy-t2i" else "edit"
            overrides = {key: getattr(a, key, None) for key in ("prompt", "seed", "steps", "cfg", "denoise", "w", "h")}
            graph, contract = prepare(WF_DIR, a.wf, mode, overrides)
            inputs = {key: getattr(a, key) for key in ("image1", "image2") if getattr(a, key, None)}
            result, path = execute(graph, contract, WORKDIR, Comfy, inputs=inputs, timeout=a.timeout)
        print("运行记录:", path)
        for output in result["outputs"]:
            print("生成:", output["path"])
        if getattr(a, "add", None):
            if len(result["outputs"]) != 1:
                raise ComfyError("多输出已全部保存；未自动导入，请用 add 明确选择文件")
            print(json.dumps(http(Krita + "/layer/add", {
                "png_path": result["outputs"][0]["path"], "name": a.add}, timeout=120), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("错误:", exc, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("已中断；服务端任务可能仍在运行，请查看运行记录，勿盲目重试。", file=sys.stderr)
        sys.exit(130)
