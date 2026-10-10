# Coagentdraw — Krita × ComfyUI × 智能体 复合工作台

> [!IMPORTANT]
> **项目状态：早期实验，个人开发工作台。** 本仓库主要作为作者与 AI 助手协作开发的公开工作区，用 GitHub Issues 组织任务与记录。它目前**不是**面向最终用户的产品：接口与行为随时变动，不做兼容性承诺，不提供使用支持。欢迎围观和开 Issue 记录问题，但请自行评估风险。

原创(零第三方 Krita 插件依赖)的三方闭环:

```
人(Krita GUI) ⇄ krita_cowork 插件(HTTP 127.0.0.1:51080) ⇄ 智能体(cwb.py / 任意 HTTP 客户端)
                                    ⇅ 智能体直接调用 ComfyUI(127.0.0.1:8188, Qwen-Image-2.1)
```

## 为什么开源

- 让 AI 编程助手能够读取仓库上下文，配合公开仓库参与开发；
- 用 GitHub Issues 驱动开发任务管理；
- 分享一套「Krita × ComfyUI × 智能体」的协作流程、角色设计方法论与调研记录。

文档默认使用中文。交流请直接开 [Issue](https://github.com/CongliangK/Coagentdraw/issues)；暂不接受外部代码贡献，也不承诺按路线图演进。

## 许可证

| 内容 | 许可证 |
|---|---|
| 源代码（`krita_cowork/`、`cwb.py`、`install.sh` 等） | [MIT](LICENSE) |
| 文档、方法论与研究记录 | [CC BY-NC 4.0](LICENSES/CC-BY-NC-4.0.txt)（署名—非商业性使用） |

- 调研记录中引用的第三方图片（Wikimedia Commons 公版 / CC0 素材、网页截图）不随仓库分发，版权归原作者 / 上传者所有；来源 URL、校验值与授权信息见[图片获取实测记录](<子智能体调研/图片获取实测/实测记录.md>)等文本记录。
- 绘画源文件（`.kra`）不包含在本仓库中，仅保留在作者本地。

## 组件

- `krita_cowork/` Krita 6 插件(Python3.13/PyQt6,仅标准库网络层)
  - `bridge_qt.py` 跨线程 marshal(信号→主线程,超时保护)
  - `server.py` HTTP/1.1 JSON 控制面(ThreadingHTTPServer,daemon)
  - `ops.py` 图层原语(RGBA8 守卫、RGBA8888 逐行拷贝、先挂树再写像素)
- `cwb.py` 智能体/命令行客户端(含 ComfyUI 提交/轮询/取图)
- `install.sh` 安装到 `~/.local/share/krita/pykrita/` 并在 kritarc `[python]` 写 `enable_krita_cowork=true`。注意：安装脚本会修改仓库外的 Krita 插件与用户配置，仅在明确需要时执行。

## API

| 路由 | 方法 | 功能 |
|---|---|---|
| `/ping` `/docs` `/layers` | GET | 心跳 / 文档列表 / 图层树(id 来自 uniqueId) |
| `/doc/new` `/doc/save` | POST | 新建 RGBA8 文档 / 另存 .kra |
| `/layer/png` | POST | 图层像素导出 PNG |
| `/layer/add` | POST | PNG → 新 paint 图层(非破坏式,可指定 above/offset) |
| `/layer/visible` `/layer/rename` | POST | 可见性 / 重命名 |

## cwb 用法

```bash
python3 cwb.py ping
python3 cwb.py new 作品 1328 1328
python3 cwb.py comfy-t2i "..." --w 1328 --h 832 --add 背景层
python3 cwb.py comfy-edit base.png "..." --image2 ref.png --add 编辑层
python3 cwb.py save 作品.kra
```

环境变量：

- `CWB_WF_DIR`：ComfyUI 工作流目录，默认 `~/AI/qwen-image-2.1`。这是本机示例路径，跨环境使用前请先核实工作流与模型可用性。

## 已实证要点(Krita 6.0.4 AppImage)

- PyQt6(无 PyQt5);启用键在 kritrc 组 `[python]`:`enable_<模块名>=true`
- `createDocument(w,h,name,"RGBA","U8","",dpi)`(colorDepth 是 "U8",非 "RGBA U8")
- root 层无 `x()/y()`,统一用 `bounds()`;无 `setDirty`
- PyQt6 `constBits()` 是 voidptr,需 `asarray(bpl*h)`
