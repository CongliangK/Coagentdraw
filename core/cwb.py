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
    p = commands.add_parser("init", help="初始化八阶段目录，重复调用不会重置")
    p.add_argument("name")
    p.add_argument("--root", help="项目目录；默认仓库 _本地暂存/<项目名>")
    for command in ("status", "edit", "stage", "record"):
        p = commands.add_parser(command)
        p.add_argument("root", help="项目目录；相对于当前工作目录")
        if command != "status":
            p.add_argument("--expect-revision", type=int, help="拒绝基于过期状态的写入")
        if command == "edit":
            p.add_argument("--intent", help="完整意图卡文本（可手工填写）")
            p.add_argument("--pillar", action="append", help="替换设计支柱列表，可重复")
            p.add_argument("--anchor", action="append", help="替换风格锚点列表，可重复")
            p.add_argument("--clear-pillars", action="store_true")
            p.add_argument("--clear-anchors", action="store_true")
        elif command == "stage":
            p.add_argument("number", type=int, choices=range(1, 9))
            p.add_argument("--status", choices=("pending", "in_progress", "completed"), default="in_progress")
            p.add_argument("--note", default="")
        elif command == "record":
            p.add_argument("path", help="相对于项目目录的现有文件路径；不复制/移动/修改文件")
            p.add_argument("--stage", type=int, choices=range(1, 9))
            p.add_argument("--category", choices=("temporary", "final"), required=True)
            p.add_argument("--note", default="")



def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping")
    sub.add_parser("docs")
    sub.add_parser("layers")
    p = sub.add_parser("png"); p.add_argument("id"); p.add_argument("path")
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
    p = sub.add_parser("group", help="创建空图层组（可嵌套）")
    p.add_argument("name")
    p.add_argument("--parent", help="父级组 ID；省略则挂到根")
    p.add_argument("--above", help="父级中的同级节点 ID")
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
    p = sub.add_parser("export-project", help="离线复制明确选定的正式成果，不上传")
    p.add_argument("root", help="项目根目录")
    p.add_argument("output", help="尚不存在的目录或 --zip 文件；父目录须存在")
    p.add_argument("--asset", action="append", required=True, help="明确选择 final 资产 UUID，可重复")
    p.add_argument("--role", action="append", default=[], metavar="ROLE=UUID",
                   help="用途：kra/final_png/setting/workflow/design_description/reference_sources/version_record")
    p.add_argument("--metadata", help="项目内 JSON sidecar：description、references、versions")
    p.add_argument("--zip", action="store_true", help="仅生成本地 ZIP")

    a = ap.parse_args(argv)
    if a.cmd == "project":
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
                              {"id": a.id, "path": os.path.abspath(a.path)},
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
        print(json.dumps(http(Krita + "/layer/add", {
            "png_path": os.path.abspath(a.png), "name": a.name,
            "above": a.above, "offset_x": a.x, "offset_y": a.y,
            "parent_id": a.parent,
        }, timeout=120), ensure_ascii=False))
    elif a.cmd == "group":
        print(json.dumps(http(Krita + "/layer/group", {
            "name": a.name, "parent_id": a.parent, "above": a.above,
        }), ensure_ascii=False))
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
