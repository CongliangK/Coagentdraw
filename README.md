# Coagentdraw — Krita × ComfyUI × 智能体 复合工作台

## 现行创作路线与能力边界

**Web 图库负责早期资料浏览，Krita 负责部件设计、试装与绘制。** 角色设计默认试点：当前设计锚点 → 搜索角色及三视图/设定资料 → 本地 Web 图库广泛预览、标注与筛选 → 本角色服装／核心物件设计草案 → 比较修改 ↔ 整身试装／拼贴 → 重构原创线稿与基础色 → 本角色正侧背设定核对。部件草案可由AI生成或绘制，可先设计全部所需部件再组合，也可边试装边补设计；不要求所有部件先定稿。已有有效原型可直接继续。只有锚点也能起步，不强制先填整卡、独立手绘剪影或建立素材库；详细方法见[04 角色视觉探索](<design/执行手册/04_角色视觉探索.md>)。

早期资料保留为项目内图片与独立 JSON 数据集，在 Web 图库比较，不将全部素材提前放入 Krita。进入试拼后，仅选中的素材与草图、编辑结果在同一工作画布旁置，原稿与候选独立分层；最终导出隔离参考。

这是待真实作品验证的默认方向，工具能力见[能力与验证边界](<design/工具能力与验证.md>)。开放联想仍可用于寻找方向；已定角色不重做选题。参考来源三视图不能代替本角色正侧背设定。独立插画按07/08探索确认与制作交付。

日常入口：[正式执行规范](<design/Krita中枢创作流程_正式执行规范.md>)、[八阶段手册](<design/执行手册/README.md>)。四类本地可编辑Krita文档按需建立：想象/提案板、角色设计稿、三视图设定、插画稿；不初始化空KRA。项目索引使用schema 2，工作流固定为 `krita-character-illustration-v1`，阶段组sidecar使用版本2。

### 插画日常制作（07—08）

| 阶段 | 日常工作与交接 |
|---|---|
| [07 插画方案探索](<design/执行手册/07_插画方案探索.md>) | 围绕本幅表达任务，三轮比较构图动作、补结构空间、确定明暗光色，形成能指导制作的结构与光色确认稿 |
| [08 插画制作与交付](<design/执行手册/08_插画制作与交付.md>) | 四轮连续推进当前主稿：8A＋B 整幅底画、8C＋D 相关局部与必要细节、8E 全图统一、8F＋G 按用途收尾交付；8A—8G 保留 |

作者持续统筹表达、取舍与确认，助手围绕当前稿的具体问题协助；表达可以是事件、状态、情绪、关系或形象气质。轮次可以合并、往返，不新增阶段、逐轮审批或候选配额。沿用既定画幅与三视图源，缩小只用于观察/副本；候选经比较、作者接受并实际采用后才成为下一轮基底。有效可编辑源须有真正影响当前画面的层或蒙版，被不透明 AI 图盖住的旧线稿/平涂不算最终分层能力。

详细动作、画改短表与回退入口以上述两份手册为准。

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
| [design/](design/) | 设计工作流文档：[现行正式执行规范](<design/Krita中枢创作流程_正式执行规范.md>)、[调研报告](design/调研报告/README.md)、[八阶段执行手册](design/执行手册/README.md)（现行八阶段一体简表） |
| [comfyui/](comfyui/) | ComfyUI 工作流（Qwen-Image-2.1，API 格式）与依赖模型说明 |
| [docs/tools/](docs/tools/) | 当前工具的参数契约、失败语义与验证边界 |
| [tests/](tests/) | 离线回归与合成测试素材，不代表真实作品验收 |

## AI 创作技能入口

[design 目录](<design/README.md>)同时作为 **`coagentdraw-design`** 技能包：[SKILL.md](<design/SKILL.md>)负责触发、八阶段按需加载、原表格执行和交付前自检，现有规范与手册仍是唯一内容源，不另复制流程。

在仓库根执行：

```bash
bash design/scripts/install_skill.sh
```

将整个 `design` 软链接到 `~/.agents/skills/coagentdraw-design`；同目标重复安装为空操作，遇到其他同名项拒绝覆盖。脚本不安装 Krita 插件、不重启宿主、不接触创作资料。宿主未发现时重新扫描技能或开启新会话；没有技能调用能力时直接读入口及当前阶段手册。详细边界见[技能包说明](<design/README.md>)。

## 本地设计项目

**工具代码与真实创作分开：所有个人设计项目默认放在仓库根目录的 `项目/<项目名>/`。** 仓库 [.gitignore](<.gitignore>) 忽略整个 `项目/`，不只忽略 KRA；项目内的文字、图片、任务记录和状态文件也不进入工具仓库。

```text
Coagentdraw/
├── core/、design/、comfyui/   # 可公开的工具与通用流程
└── 项目/                    # 本地私有项目，不入 Git
    └── 我的第一个设计/
        ├── 任务.md          # 本项目需求、问题与下一步
        ├── 资料/            # 调研与参考，保留来源说明
        ├── documents/       # 可编辑 KRA 与相关 sidecar
        ├── previews/        # 候选、比较和预览
        ├── checkpoints/     # 已确认版本的独立快照
        └── exports/         # 交付导出
```

以上是按需使用的组织建议，不是必须先建齐的模板，也不对应八阶段固定目录。一个独立创作任务通常一个项目，子任务放在项目内；与工具开发有关的通用问题才提炼进公开文档或 Issue，不直接公开项目素材。

从仓库根目录初始化本地项目索引：

```bash
python3 core/cwb.py project init "我的第一个设计"
python3 core/cwb.py project status "项目/我的第一个设计"
```

初始化只创建状态索引及锁，不自动创建上述任务、资料目录或作品。后续素材、生成结果、保存与导出需明确指定到该项目内；并非所有 CLI 输出都自动跟随项目。详细命令见 [项目状态 CLI](<design/项目状态_CLI.md>)。原有 `_本地暂存/` 继续保留且不入库，不自动搬迁、清理或升级其中内容；访问旧项目请使用原路径。

**隐私边界：Git 忽略不等于网络隔离。** 此约定防止正常 `git add` / 提交 / 推送纳入项目文件，但不能阻止强制添加、云盘同步或云端 AI 接收主动提供的文件/提示词。默认不上传项目内容，不把私有素材贴进公开 Issue；需要发送给云端服务时须先获得作者明确授权。本地项目应另做本地备份，Git 忽略不提供备份保护。

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
| `/layer/png` | POST | 图层像素导出 PNG；默认 `underlay=magenta` 把透明区渲染为不透明洋红底衬（供智能体读图，响应含 alpha 覆盖数/画布包围盒/近洋红内容告警），`underlay=none` 保留原生 alpha（如喂 ComfyUI） |
| `/layer/group` | POST | 新建图层组，可用 parent_id 嵌套、above 定位同级 |
| `/layer/add` | POST | PNG → 新透明 paint 图层（keep_size / fit_canvas / insert_at_offset，保留 parent_id/above/offset） |
| `/layer/vector` | POST | 受控短文字/line/rect SVG → 新 vectorlayer；仅明确专用已保存活动文档，严格结构回读，不承诺实机编辑 |
| `/layer/visible` `/layer/rename` | POST | 可见性 / 重命名 |
| `/layer/props` | POST | Alpha Lock / 0..1 不透明度 / 稳定 ID 混合模式（写后回读，非原子事务） |

当前插件没有通用笔刷、变形或滤镜接口；手册中的相关画改由作者在 Krita GUI，或具备相应界面能力且获授权的助手执行。普通 edit 的场景草稿＋角色参考不是几何锁定；独立HQ局部编辑交互模板使用当前底画＋真实蒙版，没有额外独立角色/Pose/Depth输入，详见 [ComfyUI 说明](<comfyui/README.md>)。

图层组与组内 PNG 导入的参数约束、CLI 示例及真实 Krita 验收步骤见 [图层组说明](docs/tools/layer_groups.md)。

`/layer/add` 的模式选择、严格整数坐标与确定性映射元数据见 [图像导入规则](<docs/tools/placement.md>)。`fit_canvas` 只等比缩小、居中透明留白、不放大小图；像素通道与实际落点本项尚未在真实 Qt/Krita 验证。

图层属性契约、CLI 示例、部分写入语义与实机待验清单见 [图层属性说明](docs/tools/layer_props.md)。`/layers` 返回 `alpha_locked`、`alpha_lock_supported`、`blending_mode`；混合模式画布效果尚未在本机验收。

`cwb.py export`、`selection-png`、`snapshot` 的参数、RGBA/U8+sRGB 限制、蒙版灰度/Alpha、无选区与覆盖失败边界见 [快照契约](<docs/tools/snapshot.md>)。仅配对入口可声明同回调捕获；token 的实机关闭/重开门槛尚待验证，当前不能作为安全回填凭证。

## 参考图库

```bash
python3 core/cwb.py references serve examples/reference_gallery
python3 core/cwb.py references serve "项目/我的项目/资料/参考集"
```

在终端显示的本机地址打开图库。通用 HTML/JS 读取外部 `references.json`，每张记录设计意义、来源、作者、使用限制与标签；宽高、长宽比、透明度和 SHA256 由工具测量。支持搜索、分组、长宽比筛选、大图与试拼清单导出。仅读取本地资料，不自动下载、上传或导入 Krita。数据与标注命令见[参考图库](<docs/tools/references.md>)；仓库仅提供原创几何教学图，真实项目参考留本地。

早期调研的常用顺序是 `init → add → validate → serve → shortlist`：助手收集原图并逐图标注，网页比较后导出本轮试用清单；意义或来源变化用 `annotate`，原图确实变化才用 `refresh`。网页自身只读，勾选不会反写数据集。具体命令与标注示例集中在[工具说明](<docs/tools/references.md>)。

能说明选哪些图、取哪个部位和要验证什么大形关系后，再将这些素材带入 Krita。02可先交付可看的图库；04的试拼成果仍须在真实画布验证。备份资料需保存 `references.json` 与它引用的全部原图，HTML 或截图不能独立恢复资料集。加载与执行规则见[技能入口](<design/SKILL.md>)，阶段操作见[02](<design/执行手册/02_联想与前期调研.md>)。

## cwb 用法

```bash
python3 core/cwb.py ping
python3 core/cwb.py new 作品 1328 1328
python3 core/cwb.py comfy-t2i "..." --w 1328 --h 832 --add 背景层
python3 core/cwb.py comfy-edit base.png "..." --image2 ref.png --add 编辑层
python3 core/cwb.py save 作品.kra
```

参数、显式工作流契约、运行快照和 `comfy-replay` 详见 [ComfyUI CLI 说明](comfyui/README.md#cli-参数与契约)。离线回归：`python3 -m unittest discover -s tests -v`；它不代表已验证真实 Krita/ComfyUI 生成。

离线项目状态与成果索引：`python3 core/cwb.py project --help`，目录、修订冲突和恢复说明见 [项目状态 CLI](design/项目状态_CLI.md)。

按文档角色建组：`python3 core/cwb.py stage --help`。首次setup指定 `--role ideation|character|turnaround|illustration`，仅支持现行工作流；同名不接管，需已保存文档与独立sidecar。身份、部分失败与重开限制见[阶段组织](<docs/tools/stage.md>)。

其他工具按实际任务选用，`--help` 均离线、无服务访问或缓存创建：

| 命令 | 当前能力与边界 | 详细契约 |
|---|---|---|
| `references` | 外部 JSON 资料集、本地 Web 预览、标注、尺寸核验与试拼清单；不访问 Krita | [参考图库](docs/tools/references.md) |
| `compare` | before/after PNG 同尺度比较与人工决定记录；不自动替换 Krita 主稿 | [比较](<docs/tools/compare.md>) |
| `layout` | 已有主图、正侧背与装备图排版，准备 PNG/SVG 人工导入请求；不生成三视图或核验一致性，矢量编辑与保存重开待实测 | [排版](<docs/tools/layout.md>) |
| `review` | 登记已有原图/剪影/灰阶，校验并导出用户提供的反馈；无已验证的模型审评后端 | [审评](<docs/tools/review.md>) |
| `candidates` | 待执行候选计划、现成透明黑 PNG 拼板、入选清单；不生成或分割图像，不导入 Krita | [候选](<docs/tools/candidates.md>) |
| `style` | 独立 sidecar 的风格 token、参考摘要、锁定记录及提示片段；不自动修改生成请求，不保证跨图一致性 | [风格](<docs/tools/style.md>) |
| `export-project` | 显式选定资产的安全导出与来源记录；不判断作品完成或 KRA 可编辑性 | [导出](<docs/tools/export.md>) |
| `python3 core/cwb_research.py` | 本地 JSON 来源计划、关键词检索与采用记录；不自动联网、解析 PDF 或核验来源 | [事实检索](<docs/tools/factual_research.md>) |

候选拼板的教学素材与离线测试不构成真实创作验证，剪影也不是角色流程的起步门槛。事实检索只辅助[阶段02](<design/执行手册/02_联想与前期调研.md>)中的具体事实问题，不限制灵感池或替代 Web 预览与 Krita 试拼。

环境变量：

- `CWB_WF_DIR`：ComfyUI 工作流目录，默认为仓库内 `comfyui/`。使用前请先核实依赖模型已在 ComfyUI 中就位（见 [comfyui/README.md](comfyui/README.md)）。

## 已实证要点(Krita 6.0.4 AppImage)

- PyQt6(无 PyQt5);启用键在 kritrc 组 `[python]`:`enable_<模块名>=true`
- `createDocument(w,h,name,"RGBA","U8","",dpi)`(colorDepth 是 "U8",非 "RGBA U8")
- root 层无 `x()/y()`,统一用 `bounds()`;无 `setDirty`
- PyQt6 `constBits()` 是 voidptr,需 `asarray(bpl*h)`
- `pixelData`/`setPixelData` 实测为 BGRA 字节序(2026-10, 6.0.4 AppImage, 彩色样本+画布对照);投影导出与图层导入均在边界交换 B/R
- Krita AppImage 内置 Python 文件系统编码可能非 UTF-8(实测 str 非ASCII路径 ENOENT);PNG 导入预读与导出使用显式 UTF-8 字节路径，KRA 保存仍依赖 Krita 的 `saveAs` 接口
