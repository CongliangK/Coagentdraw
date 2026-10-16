# CAD-24 离线视觉审评接口准备 v1（未完成后端/效果验收）

> **PR-01 目标流程/待迁移提示**：目标：审评/诊断绑定本轮创意与作品版本，区分实际观察、推断、建议和作者决定；版本变更需复核，不能用 AI 总分自动批准。真实图像修订在 Krita 完成并复看，离线格式/反馈导入不证明艺术通过。 见[现行总纲](<../../design/Krita中枢创作流程_正式执行规范.md>)。此提示不增加现有命令、参数或运行保证。

## 范围与状态

本轮只有确定性的本地 `review prepare` / `review import`。**项目没有指定且没有实际验证可用的多模态审评后端**：没有模型/API/网络调用、Qwen 测试、凭据读取、自动画改或生成画改提示。当前编程 Agent 不是本项目已可用的审评后端。不得据此关闭 #24。

- `prepare` → `prepared_not_executed`：角色图片、SHA256、尺寸、手工意图/支柱、单一[审评提示模板](<cad24_review_prompt.md>)及反馈契约，仅准备、不发送。
- `import` → `response_user_supplied`：用户显式提供 human/model JSON；model 只是来源声明，不证明模型真的运行过。人工动作也是用户声明，不进行身份认证。
- 只消费用户**已有**的原图/剪影/灰阶 PNG 与手工意图/支柱；不生成 #18 诊断、不读写 #19 项目状态、不建 #25 决策库，不改变 compare/project。有限人工确认只发生在本次导入，不提供后续通用事件平台。

## CLI 与工件

```bash
python3 core/cwb.py review --help
python3 core/cwb.py review prepare \
  --original /absolute/input/original.png \
  --silhouette /absolute/input/silhouette.png \
  --grayscale /absolute/input/grayscale.png \
  --context /absolute/input/context.json /absolute/output/prepared-v1
# 用户自行取得/手写反馈；本命令不会发送任何内容
python3 core/cwb.py review import \
  /absolute/output/prepared-v1 /absolute/input/feedback.json /absolute/output/import-v1
```

所有输出必须是**全新目录**，父目录已存在。相对路径按 CLI 工作目录解析；拒绝 `.`、`..`、重复/末尾 `/`、软链接父目录与文件、硬链接、FIFO。不要把示例占位路径当成实际素材。所有帮助不导入审评实现/Pillow、不建立缓存、不访问服务；只有实际 PNG 校验才惰性导入外部 CLI Python 的可选 Pillow，不安装依赖、不联网，不给 Krita 插件增加依赖。

手工 context 契约（未知用 `null` / `[]`，不能省略字段或补造内容）：

```json
{
  "intent": "用户明确提供的意图；这里仅为说明示例",
  "pillars": [{"id": "P1", "text": "用户明确提供的支柱；这里仅为说明示例"}]
}
```

`prepare` 保存三个角色的字节原样副本、独立有版本的 `request.json`、最后写入的 `complete.json`（工件摘要清单）。请求包含：schema、status、request_id、coordinate_system、context、feedback_contract、prompt、images。每张图片显式绑定角色、源绝对路径、原始字节 SHA256、字节数、原生尺寸、源文件身份签名和固定副本名。图片内容不转换、不缩放、不自动判断是否真为剪影/灰阶；其语义由用户声明，不实施诊断算法。

`import` 保存 `review.json`（`cwb-review-report-v1`）、`review.md`、原始字节 `supplied-feedback.json`、原始 `request.json` 和最后的 `complete.json`。源作品从不写入。报告同时包含用户原始意见、当前意见、人工决定与理由、来源声明及 `backend_executed=false` / `artwork_modified=false` / `generated_prompt=false`。Markdown 中外部文本用 JSON 引用的缩进代码块展示，避免执行来源内的 Markdown/图片链接。

> 本节以外所有文件名均指各自命令的目录内工件，不是已有仓库示例作品。

## 唯一反馈契约：cwb-review-feedback-v1

下面是**合成格式示例，不是真实角色审评，不是模型执行证据**。占位 UUID/SHA 必须从这次 prepare 的实际请求原样取得（request_sha256 是 request.json **原始字节**的摘要，不能重排 JSON 后再计算）。引用字段只作文字记录，不读取所指文件/网址。

```json
{
  "schema": "cwb-review-feedback-v1",
  "request_id": "从实际 request.json 取值",
  "request_sha256": "实际 request.json 原始字节 SHA256",
  "source_sha256": {
    "original": "images.original.sha256",
    "silhouette": "images.silhouette.sha256",
    "grayscale": "images.grayscale.sha256"
  },
  "origin": {
    "kind": "human",
    "name": "手工填写的作者或模型名称（未验证）",
    "reference": "用户手工记录的来源标识（不读取）"
  },
  "opinions": [{
    "id": "R1",
    "bbox_xywh": [1, 2, 3, 4],
    "pillar_ids": ["P1"],
    "evidence": {
      "observations": "观察到的内容；格式示例，不是已证实缺陷",
      "source_roles": ["original", "grayscale"],
      "inference": "单独记录从观察到影响的推断，不能冒充实测事实"
    },
    "uncertainty": {"level": "high", "reason": "尚无真实作品/人工对照"},
    "impact": "对已提供意图/支柱可能造成的影响",
    "action": "明确可执行的手工修改；本工具不执行",
    "priority": "P2",
    "classification": "suggestion",
    "claim_type": "hypothesis",
    "reason": "选择该分类的理由"
  }],
  "decisions": []
}
```

### 定位、分类和不确定性

- 只采用 `bbox_xywh`，不接受未声明的区域 ID：左上角原点、original 原生 PNG 像素，整数 x/y/宽/高，正宽高且完全界内，右下边界为开区间。剪影/灰阶必须与 original 同尺寸，无缩放/旋转/裁切映射或 EXIF 变换。不能由尺寸相同推断内容一定对齐，用户须提供对齐素材。
- ID 为以字母开头的 1–64 位 ASCII 字母/数字/`_`/`-`，同份意见唯一；修改必须保留 ID。每条意见非空引用已提供支柱 ID，证据非空引用三个角色中的至少一项，观察和推断分开写。
- classification：`must_change`（必须改）、`suggestion`（建议）、`personal_preference`（个人偏好），均须 reason；priority 为 P0/P1/P2（也是用户提供的意见，不是事实判定）。
- claim_type 为 `intent_conflict` / `hypothesis` / `subjective_preference`。personal_preference 与 subjective_preference 必须同时出现；must_change 只能配 intent_conflict。不能把主观偏好标为实证客观缺陷。
- uncertainty.level 为 low/medium/high，reason 不得为空。即使 low，也仍是 `user_supplied_unverified`。**校验器不能理解艺术语义，无法阻止有人在观察文字里撒谎；结构通过不等于意见真实/有效。** 提示明确禁止主观评价冒充实证缺陷，最终由人判断。
- 所有契约层拒绝未知/缺失字段、重复 JSON 键、NaN/Infinity。意见 0–100 条，没有“三条”配额；意图未提供或无支柱只能导入空意见，不能编造意图。

### 本次导入中的人工决定

每条意见最多一个人工决定，不存在则 `pending`。动作例子：

```json
{"opinion_id":"R1","action":"confirm","actor":"用户手填身份","reason":"确认理由"}
```

`reject` 结构同上。`amend` 额外且必须有完整 `amended_opinion`（同一 ID，重新校验定位/支柱/三分类等所有字段）。非 amend 不允许此字段。actor/reason 必须非空；不提供身份则不冒造确认。

导出状态为 confirmed/amended/rejected。original_opinion 永远保留，effective_opinion 只在 amend 改变；reject 只标状态，不删除内容。decision_history 原样保留人工来源/理由。完整原始反馈另存，以免丢掉原意见的来源与措辞。不自动修改作品、隐藏图层、生成提示或写入 #25。

## 文件安全与限制

- 复用现有安全 I/O 原语（[比较实现](<../../core/cwb_compare.py>)），不复写或修改其模块。Linux `dir_fd`/`O_NOFOLLOW`/`O_NONBLOCK`、普通文件且 nlink=1、独占新目录/文件、fsync；不允许链接/FIFO/别名/旧输出覆盖。
- JSON/提示最大 1 MiB；字段文本最大 16 KiB、支柱最多 50 项、意见最多 100 项；PNG 每张最多 32 MiB、4096 个 chunk（含 IHDR/IDAT/IEND）、16,000,000 像素、边长 16384、静态 <=8 位。校验 PNG 结构并实际解码，拒绝损坏/截断、其他格式/动画/解压炸弹；只校验，不做任何艺术分析。
- prepare/import 对**同一份安全读取的 bytes**，先用标准库做有界 PNG 容器预检，再交给 Pillow `verify()` + `load()` 实际像素解码；不重新打开图片路径解码。预检检查签名、每个 chunk 的长度/边界/完整 CRC、合法类型与保留位、IHDR 唯一且首/13 字节与尺寸/色型/位深/方法、IEND 空数据且完整唯一末尾（不允许尾随字节）、必须有连续 IDAT、PLTE/tRNS 的基本长度/色型/顺序及常见色彩元数据的前置顺序，拒绝未知 critical chunk。Pillow 能解码不再被当作上述容器完整性的替代证明。
- **静态策略：显式拒绝所有含 acTL/fcTL/fdAT 的 APNG，包括单帧 APNG**；不能仅以 `n_frames == 1` 声称非 APNG。4096 chunk 上限及全部 APNG 拒绝是显式资源/格式边界。保留既有合法静态灰阶/索引图 1/2/4/8 位、RGB/灰阶+Alpha/RGBA 8 位及 Adam7 交错支持；合法 PLTE/tRNS/ICC 等元数据随原始来源字节原样保存，不剥离/重编码/转色。既有 16 位拒绝策略不变。
- 这是局部容器门禁加既有解码器，不取代完整 PNG 解码器、不穷举所有 ancillary 元数据语义或证明解码器没有漏洞；未知 ancillary chunk 在边界/CRC/类型检查后可保留。图片角色/艺术语义仍不作判断。
- 准备和导入前后再次核对输入身份/大小/mtime/ctime 与摘要。导入不仅验证请求/副本/完成清单，还要求三个**原路径仍存在且身份与内容不变**；同字节替换 inode 也拒绝。来源变了就重新 prepare，不把旧反馈绑定到新图。
- complete.json 最后写入并持久化；写入异常/源变化时清理本次专有工件且命令失败，不误报成功。进程被硬杀可能留下没有有效完成标记的半目录；导入拒绝且永不复用，该目录由用户检查后处理。文件系统不能提供来源一直不被外部修改的永久保证；成功只指检查时的一致性。
- 清单 SHA 只检验完整性/绑定，不是签名/可信身份认证。拥有目录写权限者可重写整份清单，不能据此证明来源真实性。

## 验收映射与未完成项

| Issue 要求 | 本轮实际结果 / 离线证据 |
|---|---|
| 原图/剪影/灰阶/意图 | prepare 消费已有同尺寸 PNG，记录角色/SHA/尺寸，手工 context；不实现 #18/#19 |
| 实际可用多模态后端 | **未完成**；无后端指定或可用性验证；prepared_not_executed |
| 单一结构化提示/反馈 | 一个版本模板 + 请求契约；不发送，拒绝未知字段/错误绑定 |
| 定位/支柱/证据/影响/修改/优先级 | 严格结构校验；bbox 原图坐标、支柱引用与越界拒绝 |
| 三分类/不确定性 | 字段与组合校验；主观偏好不能显式标为必须改/实证缺陷；不验证文字内容真伪 |
| Markdown + JSON | import 导出两种格式并保留原始请求/反馈字节，明确 response_user_supplied |
| 人工确认/修改/驳回 | 本次导入显式字段；原意见/来源与决定保留；不触及作品或 #25 |
| 至少三条有艺术价值的建议/真实 Krita 画改 | **未完成**；三条仅合成 fixture 的格式/人工状态证据，不替代艺术有效性 |
| 小规模人工对照，10–20 样本无效/错误定位/驳回统计 | **未完成**；没有真实样本/后端调用，不能伪造统计或宣称实验通过 |

[离线回归](<../../tests/test_review.py>)使用合成小矩形，不是真实角色图：覆盖角色/摘要、定位/支柱、三分类/不确定性、未知字段、来源绑定/变化、原意见与人工状态、链接/FIFO/别名/旧输出/资源限制、失败写入回滚、帮助无可选依赖/缓存/服务访问；另有标准库小 PNG 负例覆盖错误/缺失 IEND CRC、未知 critical chunk、截断/长度/全部 chunk CRC/关键顺序/尾随数据/APNG，及合法 Alpha/PLTE+tRNS/ICC/交错原字节保留。旧格式准备件即使摘要完全自洽，import 也须重验 PNG；真实 CLI 先红后绿证据另存，不以 mock 结果替代退出码。全套 unittest、Python AST、bash-n、diff-check 的实际结果及合成 CLI 工件保存在本轮指定的本地证据目录，不提交图片/临时结果。静态/离线检查不是后端、Krita 或艺术验收。
