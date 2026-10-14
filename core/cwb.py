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
  cwb comfy-edit base.png ref.png "change ..." --add "换装"
"""
import argparse
import json
import os
import sys
import time
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


def comfy_post(path, payload):
    req = urllib.request.Request(Comfy + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def comfy_run(wf, max_wait=1800):
    """提交工作流并轮询到完成,返回 (subfolder, filename) 列表。"""
    resp = comfy_post("/prompt", {"prompt": wf, "client_id": "cwb"})
    if resp.get("node_errors"):
        sys.exit("ComfyUI node_errors:\n" + json.dumps(
            resp["node_errors"], ensure_ascii=False)[:2000])
    pid = resp["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < max_wait:  # 双保险:总时限,防 /history 丢失死轮询
        time.sleep(3)
        try:
            with urllib.request.urlopen(Comfy + "/history/" + pid, timeout=15) as r:
                h = json.loads(r.read())
        except urllib.error.URLError:
            continue
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                sys.exit("ComfyUI 执行出错: " + json.dumps(
                    h[pid].get("status", {}), ensure_ascii=False)[:1000])
            outs = []
            for node_id, node_out in h[pid].get("outputs", {}).items():
                for img in node_out.get("images", []):
                    outs.append((img["subfolder"], img["filename"], node_id))
            if outs:
                return outs
            if st.get("completed") or st.get("status_str") == "success":
                sys.exit("工作流已完成但无图像输出(检查是否接了 SaveImage 节点)")
    sys.exit("ComfyUI 轮询超时({0}s)".format(max_wait))


def comfy_fetch(subfolder, filename, dest):
    url = "{0}/view?subfolder={1}&filename={2}".format(Comfy, subfolder, filename)
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def load_wf(name):
    with open(os.path.join(WF_DIR, name)) as f:
        return json.load(f)


def main():
    os.makedirs(WORKDIR, exist_ok=True)
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping")
    sub.add_parser("docs")
    sub.add_parser("layers")
    p = sub.add_parser("png"); p.add_argument("id"); p.add_argument("path")
    p = sub.add_parser("new"); p.add_argument("name"); p.add_argument("w", type=int)
    p.add_argument("h", type=int)
    p = sub.add_parser("add"); p.add_argument("png"); p.add_argument("name")
    p.add_argument("--above"); p.add_argument("--x", type=int, default=0)
    p.add_argument("--y", type=int, default=0)
    p = sub.add_parser("visible"); p.add_argument("id"); p.add_argument("v")
    p = sub.add_parser("save"); p.add_argument("path")
    p = sub.add_parser("comfy-t2i"); p.add_argument("prompt")
    p.add_argument("--w", type=int, default=1328); p.add_argument("--h", type=int, default=1328)
    p.add_argument("--seed", type=int, default=None); p.add_argument("--add")
    p = sub.add_parser("comfy-edit"); p.add_argument("image1")
    p.add_argument("prompt"); p.add_argument("--image2", default=None)
    p.add_argument("--seed", type=int, default=None); p.add_argument("--add")

    a = ap.parse_args()
    if a.cmd == "ping":
        print(json.dumps(http(Krita + "/ping"), ensure_ascii=False))
    elif a.cmd == "docs":
        print(json.dumps(http(Krita + "/docs"), ensure_ascii=False, indent=1))
    elif a.cmd == "layers":
        print(json.dumps(http(Krita + "/layers"), ensure_ascii=False, indent=1))
    elif a.cmd == "png":
        print(json.dumps(http(Krita + "/layer/png",
                              {"id": a.id, "path": os.path.abspath(a.path)},
                              timeout=120), ensure_ascii=False))
    elif a.cmd == "new":
        print(json.dumps(http(Krita + "/doc/new",
                              {"name": a.name, "width": a.w, "height": a.h}),
                          ensure_ascii=False))
    elif a.cmd == "add":
        print(json.dumps(http(Krita + "/layer/add", {
            "png_path": os.path.abspath(a.png), "name": a.name,
            "above": a.above, "offset_x": a.x, "offset_y": a.y,
        }, timeout=120), ensure_ascii=False))
    elif a.cmd == "visible":
        print(json.dumps(http(Krita + "/layer/visible",
                              {"id": a.id, "visible": a.v in ("1", "true")}),
                          ensure_ascii=False))
    elif a.cmd == "save":
        print(json.dumps(http(Krita + "/doc/save", {"path": os.path.abspath(a.path)},
                              timeout=120), ensure_ascii=False))
    elif a.cmd == "comfy-t2i":
        wf = load_wf("wf_qwen21_t2i_api.json")
        wf["encode"]["inputs"]["prompt"] = a.prompt
        wf["latent"]["inputs"]["width"] = a.w
        wf["latent"]["inputs"]["height"] = a.h
        if a.seed is not None:
            wf["sampler"]["inputs"]["seed"] = a.seed
        outs = comfy_run(wf)
        dest = comfy_fetch(*outs[0][:2],
                           os.path.join(WORKDIR, "t2i_{0}.png".format(int(time.time()))))
        print("生成:", dest)
        if a.add:
            print(json.dumps(http(Krita + "/layer/add",
                                  {"png_path": dest, "name": a.add}, timeout=120),
                              ensure_ascii=False))
    elif a.cmd == "comfy-edit":
        def upload(path):
            # 文件名统一 ascii 时间戳,避免中文/引号/扩展名问题
            name = "cwb_in_{0}.png".format(int(time.time() * 1000))
            with open(path, "rb") as f:
                body = (b"--X\r\nContent-Disposition: form-data; name=\"image\"; "
                        b"filename=\"" + name.encode() + b"\"\r\n"
                        b"Content-Type: application/octet-stream\r\n\r\n"
                        + f.read() + b"\r\n--X--\r\n")
            req = urllib.request.Request(
                Comfy + "/upload/image?overwrite=true&type=input", data=body,
                headers={"Content-Type": "multipart/form-data; boundary=X"})
            with urllib.request.urlopen(req, timeout=30) as r:
                json.loads(r.read())
            return name

        wf = load_wf("wf_qwen21_edit_api.json")
        wf["load1"]["inputs"]["image"] = upload(os.path.abspath(a.image1))
        if a.image2:
            wf["load2"]["inputs"]["image"] = upload(os.path.abspath(a.image2))
        else:  # 单图编辑:去掉 image_2 引用
            wf["encode"]["inputs"]["images"] = {"image_1": ["load1", 0]}
        wf["encode"]["inputs"]["prompt"] = a.prompt
        if a.seed is not None:
            wf["sampler"]["inputs"]["seed"] = a.seed
        outs = comfy_run(wf)
        dest = comfy_fetch(*outs[0][:2],
                           os.path.join(WORKDIR, "edit_{0}.png".format(int(time.time()))))
        print("生成:", dest)
        if a.add:
            print(json.dumps(http(Krita + "/layer/add",
                                  {"png_path": dest, "name": a.add}, timeout=120),
                              ensure_ascii=False))


if __name__ == "__main__":
    main()
