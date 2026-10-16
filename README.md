# Coagentdraw — Krita × ComfyUI × 智能体 复合工作台

## 现行创作路线与能力边界（PR-01）

**Krita 是视觉中枢**：大量文本起点 ↔ 联想调研 → 跨创意选择 → 角色视觉探索 → 角色方案收敛（选择、实际修改、复看）→ 正侧背三视图设定核对 → 独立插画方案探索确认 → 插画制作交付。三视图是默认完整路线必需成果；角色确认不等于插画确认。

日常入口：[正式执行规范](<design/Krita中枢创作流程_正式执行规范.md>)、[八阶段手册](<design/执行手册/README.md>)、[改造跟踪](<design/Krita中枢改造跟踪.md>)。四类本地可编辑 Krita 文档按需建立：想象/提案板（02—03承接01）、角色设计稿（04—05）、三视图设定（06）、插画稿（07—08）；PNG 只是派生预览，原稿与候选保留。KRA 与第三方原始资料不入公开仓库。

| 能力层次 | 本次状态 |
|---|---|
| 已有实现 | 下文列出的 CLI、离线索引/候选/比较/布局及插件原语；各自仍受原说明的限制，本次未重跑或升级其能力 |
| 待迁移/联调 | 程序仍为旧阶段/schema 1；新 schema 2、workflow、多起点/多文档索引及真实板/比较/安全改图/三视图链路按 PR-02—06 待实施，不能将现有命令编号按新手册解释 |
| 待艺术验收 | 真实创作的选择、修订、三视图一致性及插画表达须作者确认，本次无作品验收 |

本提交仅更新文档目标，不新增 Krita 可见程序行为；不表示软件已支持全部新流程。

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
| [design/](design/) | 设计工作流文档：[现行正式执行规范](<design/Krita中枢创作流程_正式执行规范.md>)、[调研报告](design/调研报告/README.md)、[八阶段执行手册](design/执行手册/README.md)（新八阶段一体简表；旧路径保留迁移说明） |
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
| `/doc/export` `/doc/selection_png` | POST | 整画布合成投影 / 原生灰度选区 PNG（各自独立采样） |
| `/doc/snapshot` | POST | 一次主线程回调配对捕获投影和选区，附会话 token 与像素摘要 |
| `/layer/png` | POST | 图层像素导出 PNG |
| `/layer/group` | POST | 新建图层组，可用 parent_id 嵌套、above 定位同级 |
| `/layer/add` | POST | PNG → 新透明 paint 图层（keep_size / fit_canvas / insert_at_offset，保留 parent_id/above/offset） |
| `/layer/vector` | POST | 受控短文字/line/rect SVG → 新 vectorlayer；仅明确专用已保存活动文档，严格结构回读，不承诺实机编辑 |
| `/layer/visible` `/layer/rename` | POST | 可见性 / 重命名 |
| `/layer/props` | POST | Alpha Lock / 0..1 不透明度 / 稳定 ID 混合模式（写后回读，非原子事务） |

图层组与组内 PNG 导入的参数约束、CLI 示例及真实 Krita 验收步骤见 [CAD-11 图层组说明](docs/development/cad11_layer_groups.md)。

`/layer/add` 的命名适配、严格整数坐标与确定性映射元数据见 [CAD-14 图像导入规则](<docs/development/cad14_placement.md>)。`fit_canvas` 只等比缩小、居中透明留白、不放大小图；`crop_at_bbox` 明确不支持。像素通道与实际落点本项尚未在真实 Qt/Krita 验证。

图层属性契约、CLI 示例、部分写入语义与实机待验清单见 [CAD-12 图层属性说明](docs/development/cad12_layer_props.md)。`/layers` 增加 `alpha_locked`、`alpha_lock_supported`、`blending_mode`；混合模式画布效果尚未在本机验收。

`cwb.py export`、`selection-png`、`snapshot` 的参数、RGBA/U8+sRGB 限制、蒙版灰度/Alpha、无选区与覆盖失败边界见 [CAD-13 快照契约](<docs/development/cad13_snapshot.md>)。仅配对入口可声明同回调捕获；token 的实机关闭/重开门槛尚待验证，当前不能作为安全回填凭证。

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

确定性旧八阶段图层组（非新流程预设）：`python3 core/cwb.py stage --help`。需显式已保存文档与独立身份 sidecar；同名不接管、只补未登记组，重开降级/部分失败及已有诊断 PNG 导入边界见 [CAD-21 阶段组织](docs/development/cad21_stage.md)（未实机验证保存/重开）。

最小离线文件比较：`python3 core/cwb.py compare --help`。接受手工 before/after PNG，原尺度共同裁切、来源 SHA256 和非破坏性决定事件见 [CAD-23 比较说明](<docs/development/cad23_compare.md>)。仅 `create` 使用外部 CLI 可选 Pillow；所有决定仅记录，不实际隐藏/恢复 Krita 投影，不代表 #17 完成或 #25 联调。

已有主图、前/侧/背与两个装备拆解的最小离线设定页：`python3 core/cwb.py layout --help`。`fixture` 仅教学占位、非真实角色；`plan/render/prepare` 保留来源 SHA、六张独立 PNG、标注 SVG 与人工执行 payload，不自动写 Krita/生成三视图/保证一致性。`render/prepare` 延迟使用 CLI-only 可选 Pillow，prepare 从已核原源按 CAD-14 重采样并逐 RGBA 核对渲染物（本地一致性，非同 UID 防篡改认证）。受控矢量入口、300 DPI points 映射、部分写入与 B 门槛见 [CAD-28 排版说明](<docs/development/cad28_layout.md>)；Krita 真正改字/齐线/字体与 KRA 保存重开均未验，不关闭 #28。本项仅将同级 `above` 扩展到 vectorlayer，`props` 为 vectorlayer 开放能力守卫下的 opacity/blending_mode，Alpha Lock/root/其他类型守卫不放开。

离线视觉审评接口准备：`python3 core/cwb.py review --help`。`prepare` 仅记录用户已有原图/剪影/灰阶 PNG 和手工意图/支柱（`prepared_not_executed`），`import` 仅校验用户手工提供的反馈及人工确认/修改/驳回，导出 Markdown+JSON（`response_user_supplied`）。见 [CAD-24 契约与未完成验收](<docs/development/cad24_review.md>)。**无已验证的审评后端、模型调用或艺术效果证据；合成 fixture 不是真实角色验收，#24 不关闭。**

有限离线候选计划/现成剪影拼板：`python3 core/cwb.py candidates --help`。仅 `plan` 记录 `prepared_not_executed`；`board` 消费显式 SHA 登记的透明黑 PNG，contain 不拉伸，失败占位不消失（退出 2），仅输出入选清单/未执行新层 payload。可选 Pillow 仅在实际拼板时导入。字段、路径与资源上限见 [CAD-22 离线候选说明](<docs/development/cad22_candidates.md>)。教学 fixture 不证明真实八候选生成、两方向有效、mask 可靠或真实组内导入；这些验收仍未满足。

有限离线风格配置：`python3 core/cwb.py style --help`。版本化 `style_tokens` **仅存独立 sidecar，不在项目主状态**；支持参考图摘要/锁定历史、显式提示片段导出和复用 #23 的同尺度人工标杆比较。契约与未完成门槛见 [CAD-26 风格工具](<docs/development/cad26_style.md>)；不自动改生成请求，不保证跨图一致性，未运行两个角色生成。

环境变量：

- `CWB_WF_DIR`：ComfyUI 工作流目录，默认为仓库内 `comfyui/`。使用前请先核实依赖模型已在 ComfyUI 中就位（见 [comfyui/README.md](comfyui/README.md)）。

## 已实证要点(Krita 6.0.4 AppImage)

- PyQt6(无 PyQt5);启用键在 kritrc 组 `[python]`:`enable_<模块名>=true`
- `createDocument(w,h,name,"RGBA","U8","",dpi)`(colorDepth 是 "U8",非 "RGBA U8")
- root 层无 `x()/y()`,统一用 `bounds()`;无 `setDirty`
- PyQt6 `constBits()` 是 voidptr,需 `asarray(bpl*h)`
