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
- 分享一套「Krita × ComfyUI × 智能体」的协作流程、角色设计方法论与调研报告。

文档默认使用中文。交流请直接开 [Issue](https://github.com/CongliangK/Coagentdraw/issues)；暂不接受外部代码贡献，也不承诺按路线图演进。

## 目录结构

| 目录 | 内容 |
|---|---|
| [core/](core/) | 内核：Krita 插件 `krita_cowork`、命令行客户端 `cwb.py`、安装脚本 |
| [design/](design/) | 设计工作流文档：八阶段工作规范、意图卡模板、[调研报告](design/调研报告/README.md)、[八阶段工作表](design/执行手册/README.md)（填写提示与设计记录整合在同一张表中） |
| [comfyui/](comfyui/) | ComfyUI 工作流（Qwen-Image-2.1，API 格式）与依赖模型说明 |

## 许可证

| 内容 | 许可证 |
|---|---|
| 源代码（`core/`） | [MIT](LICENSE) |
| 文档、方法论与调研报告（`design/`） | [CC BY-NC 4.0](LICENSES/CC-BY-NC-4.0.txt)（署名—非商业性使用） |

- 调研报告中的引用与来源链接仅作研究引用，版权归原作者所有。
- 绘画源文件（`.kra`）与原始调研素材不包含在本仓库中，仅保留在作者本地。

## 组件

- `core/krita_cowork/` Krita 6 插件(Python3.13/PyQt6,仅标准库网络层)
  - `bridge_qt.py` 跨线程 marshal(信号→主线程,超时保护)
  - `server.py` HTTP/1.1 JSON 控制面(ThreadingHTTPServer,daemon)
  - `ops.py` 图层原语(RGBA8 守卫、RGBA8888 逐行拷贝、先挂树再写像素)
- `core/cwb.py` 智能体/命令行客户端(含 ComfyUI 提交/轮询/取图)
- `core/install.sh` 安装到 `~/.local/share/krita/pykrita/` 并在 kritarc `[python]` 写 `enable_krita_cowork=true`。注意：安装脚本会修改仓库外的 Krita 插件与用户配置，仅在明确需要时执行。
- `comfyui/` 工作流定义（`cwb.py` 默认从这里加载）

## API

| 路由 | 方法 | 功能 |
|---|---|---|
| `/ping` `/docs` `/layers` | GET | 心跳 / 文档列表 / 图层树(id 来自 uniqueId) |
| `/doc/new` `/doc/save` | POST | 新建 RGBA8 文档 / 另存 .kra |
| `/layer/png` | POST | 图层像素导出 PNG |
| `/layer/group` | POST | 新建图层组，可用 parent_id 嵌套、above 定位同级 |
| `/layer/add` | POST | PNG → 新 paint 图层(非破坏式,可指定 parent_id/above/offset) |
| `/layer/visible` `/layer/rename` | POST | 可见性 / 重命名 |

图层组与组内 PNG 导入的参数约束、CLI 示例及真实 Krita 验收步骤见 [CAD-11 图层组说明](docs/development/cad11_layer_groups.md)。

## cwb 用法

```bash
python3 core/cwb.py ping
python3 core/cwb.py new 作品 1328 1328
python3 core/cwb.py comfy-t2i "..." --w 1328 --h 832 --add 背景层
python3 core/cwb.py comfy-edit base.png "..." --image2 ref.png --add 编辑层
python3 core/cwb.py save 作品.kra
```

参数、显式工作流契约、运行快照和 `comfy-replay` 详见 [ComfyUI CLI 说明](comfyui/README.md#cli-参数与契约cad-10)。离线回归：`python3 -m unittest discover -s tests -v`；它不代表已验证真实 Krita/ComfyUI 生成。

离线项目状态与成果索引：`python3 core/cwb.py project --help`，目录、修订冲突和恢复说明见 [项目状态 CLI](design/项目状态_CLI.md)。

确定性八阶段图层组：`python3 core/cwb.py stage --help`。需显式已保存文档与独立身份 sidecar；同名不接管、只补未登记组，重开降级/部分失败及已有诊断 PNG 导入边界见 [CAD-21 阶段组织](docs/development/cad21_stage.md)（未实机验证保存/重开）。

环境变量：

- `CWB_WF_DIR`：ComfyUI 工作流目录，默认为仓库内 `comfyui/`。使用前请先核实依赖模型已在 ComfyUI 中就位（见 [comfyui/README.md](comfyui/README.md)）。

## 已实证要点(Krita 6.0.4 AppImage)

- PyQt6(无 PyQt5);启用键在 kritrc 组 `[python]`:`enable_<模块名>=true`
- `createDocument(w,h,name,"RGBA","U8","",dpi)`(colorDepth 是 "U8",非 "RGBA U8")
- root 层无 `x()/y()`,统一用 `bounds()`;无 `setDirty`
- PyQt6 `constBits()` 是 voidptr,需 `asarray(bpl*h)`
