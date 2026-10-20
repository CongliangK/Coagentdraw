# 项目状态与成果索引

离线索引，不访问 Krita/ComfyUI/网络，不绘图、不保存 KRA、不复制作品、不批准艺术质量。入口为 `python3 core/cwb.py project --help`；`--version` 显示合同版本。创作合同见[现行总纲](<Krita中枢创作流程_正式执行规范.md>)。A 测试不代替 B 实机或 C 作者验收。

## 版本与初始化

- 新建使用 `schema_version: 2`、`workflow_id: krita-character-illustration-v1`；结构版本和创作流程分别辨认。八条阶段记录含稳定 `stage_id`，不依赖中文目录。
- `init` 只建立状态及永久锁，不生成八表、阶段目录、空 KRA、虚构作品或批准。重复同名不重置，不同名报错，损坏/未知版本不覆盖。

```bash
python3 core/cwb.py project init "测试项目" --root /明确/项目目录
python3 core/cwb.py project status /明确/项目目录
```

省略 `--root` 时为**仓库**下 `项目/<项目名>/`，项目名须单个目录名，不随调用者工作目录改变；显式 `--root` 的相对路径仍相对调用者工作目录。真实创作的任务记录、资料、KRA、候选、导出和状态索引均放入对应项目目录，整棵目录由仓库 [.gitignore](<../.gitignore>) 排除。结构建议与隐私边界见 [本地项目约定](<../README.md#本地设计项目>)。已有 `_本地暂存/` 项目不自动迁移，继续用明确路径访问；不要同名初始化来代替打开旧项目。所有示例均需改为已授权项目，开发只在临时 fixture 中验证。

`current_stage` 仅关注位置，不推断前后阶段完成。`stages` 按01—08：`text_seeds`、`research_imagination`、`concept_selection`、`character_exploration`、`character_resolution`、`turnaround`、`illustration_planning`、`illustration_production`。`stage_history` 每条携带 workflow。

## 多起点、来源拆合与选择

```bash
python3 core/cwb.py project idea /项目 seed-a "一句话起点" --text "少量文字，不要求完整人设"
python3 core/cwb.py project idea /项目 split-a "分支" --text "另一种可能" --source seed-a
python3 core/cwb.py project idea /项目 merged "组合" --text "组合线索" --source seed-a --source split-a --cluster cluster-01
# 或 --text-ref notes/ideas.md#稳定段落ID，二选一，不要求重复手工维护全文。
python3 core/cwb.py project decision /项目 idea merged select \
  --scope "本轮生产主线" --source "人工转录：作者在对话中的选择，附实际定位" --reason "作者给出的理由"
```

起点 ID/版本只用非空 ASCII 字母数字、下划线、连字符。没有两三个配额。一个来源可拆成多条，多来源可合成新条；旧条不删除，来源必须已登记、禁止自身/循环引用。每条有独立版本。相同起点调用幂等；改写/替代用新 ID 并引用旧条。全文引用登记文件摘要，文件变更/缺失会提示复核，不声称逐段语义校验。

`decision` 对 `idea/asset/stage` 记录 `approve/select/reject/defer`（stage仅approve，select仅idea），包括对象版本、成果摘要、范围、时间、理由、实际人类来源。`actor=human` 是**使用者提供/人工转录的归因，不是身份认证**；不得让模型编造作者确认。所有决定均 `recorded_not_applied`，不会切换 Krita 显示、自动执行或生成 negative prompt。“本轮未选”不成为全局永久禁令。选择仅更新 `selected_concept_id`，未选方向保留。

## 多文档与工作文件

```bash
python3 core/cwb.py project document /项目 design-001 documents/character/design-001.kra \
  --role character --concept merged
```

文件必须是项目内已存在 `.kra`。角色为 `ideation`（01—03）、`character`（02素材、04—05）、`turnaround`（06）、`illustration`（07—08）；非 ideation 需 `--concept`，插画还需 `--illustration ID`。同角色可登记多个文档/版本。ID绑定后不静默改指向，新路径另给 ID；工作 KRA 可以正常保存，登记不会把每次保存当作批准快照变化。CLI 仅验证本地文件存在，不证明是有效 KRA、已完成保存或与内存画面相同。

## 版本化资产与不变批准快照

`record` 只读取已有文件，追加记录，绝不改写旧 ID/哈希。`--category temporary/final` 是用途分类，final 不等于批准。`edit --intent/--pillar/--anchor` 可填写项目摘要元数据，多个起点在 `ideas` 中管理。

```bash
python3 core/cwb.py project record /项目 previews/exploration.png --category temporary --stage 4 \
  --object-id exploration --version v1 --role character_exploration --document-id design-001 \
  --region 0 0 800 1200
```

- `object_id` 为稳定作品对象，多版本沿用；`version` 独立不可复用（省略均生成 UUID）。同对象同版本内容/元数据不同拒绝；相同内容重复登记空操作。
- `role` 是实际成果身份，`document_id` 定位工作文档；`--region X Y W H` 是整文档非负像素坐标/正整数尺寸，或 `--group-marker UUID` 是已人工/运行核对的持久组标记，不是 runtime layer ID 或会话 token。离线 CLI **不验证当前画布绑定**，不凭同名层接管。
- `--preview` 登记实际文件字节 SHA-256/大小，不冒称已从 Krita 采样或计算导出像素摘要；KRA 哈希也不等于内存源版本。
- 默认 `kind=working` 可继续编辑，但不可批准。批准用 `--kind checkpoint` 引用已经另存、不会覆盖的确认版本：`checkpoints/<object-id>/<version>/...`；预览也在同目录。程序不自动复制巨型 KRA，不擅自保存作品；**须先由授权保存动作建立真实快照，再登记**。同快照路径禁止复用，正常工作保存与旧批准互不改指向。

```bash
# 此处文件必须已经由明确授权的保存动作建立，不是 CLI 生成的占位作品。
python3 core/cwb.py project record /项目 checkpoints/master/v1/source.kra --category final --stage 5 \
  --object-id master --version v1 --kind checkpoint --role character_master_lineart \
  --document-id design-001 --region 0 0 800 1200 --preview checkpoints/master/v1/preview.png
python3 core/cwb.py project decision /项目 asset <资产UUID> approve \
  --scope "角色主视图线稿v1" --source "真实作者指令/人工转录及保存来源定位"
```

旧资产哈希永不静默更新。登记同对象改变的内容/预览/区域/组绑定，旧批准标记 `needs_review`；05变化保守触发06—08复核，06变化触发07—08，07变化触发08（不自动推断具体图像依赖）。快照或预览被改/丢失时 `status` 返回有效复核视图，**不写状态**；下次成功写命令持久化复核。无变化不反复要求确认。原快照损坏不能靠重登记同路径重新批准，需建立新版本。

## 阶段完成与必要成果

状态为 `pending/in_progress/awaiting_review/completed`。跳转/回退不自动完成其他阶段。`completed` 必须引用本阶段有效的 stage 批准；该批准需非空人类来源与必要成果，且引用文件未变。艺术判断仍由作者负责，不由 CI 或 role 名字证明。

| 阶段 | 最低已登记成果 |
|---|---|
| 01 | `idea:ID` 纯文本起点即可 |
| 02 | `research_materials`（当前工作画布中的素材）或 `research_board`（独立想象板） |
| 03 | 人类 select 选中的 `idea:ID` |
| 04 | `character_exploration` |
| 05 | `character_master_lineart` + `character_master_basecolor` |
| 06 | `turnaround_front` + `turnaround_side` + `turnaround_back`（每项应含对应线稿/基础色，不是数量验收） |
| 07 | `illustration_plan` |
| 08 | `final_illustration` + `editable_source` + `delivery_manifest` |

除起点外，必要资产须本 workflow、本阶段、独立 checkpoint；视觉成果还需文档与组标记/区域。记录不足则拒绝，此表是索引门槛，不是绘图/核对实现，也不验证线稿质量、KRA层可编辑性、左右或成画表达。

```bash
python3 core/cwb.py project decision /项目 stage text_seeds approve --outcome idea:seed-a \
  --scope "阶段一当前起点交接" --source "实际人类确认/人工转录"
python3 core/cwb.py project stage /项目 1 --status completed --approval <上一步批准UUID>
python3 core/cwb.py project stage /项目 2 --status awaiting_review --expect-revision <当前修订>
```

## 同画布素材的登记

02收集的素材可以直接旁置在角色稿中，与草图和编辑结果同画布对照、拼贴。同一个 `character` 文档可承接02、04、05，不必为登记另建想象板或重复登记KRA。

- 角色稿中的素材按阶段02、`research_materials`登记，绑定素材所在的真实区域或持久组标记；试拼结果另按04、`character_exploration`登记。
- 确实使用独立想象板时，仍可在 `ideation` 文档登记 `research_board`；两种载体都是有效选择。
- 02完成仍需要独立checkpoint、可定位视觉成果及真实人类批准。来源链接、空组或“已找到”不等于素材已进画布；04拼贴结果也不能冒记02来源材料。
- 机器关注位置、执行情况与艺术判断分别记录；只用画布和短表时可以不做机器登记，不手写JSON或伪造批准。

```bash
python3 core/cwb.py stage setup 2 --role character \
  --document /项目/documents/design.kra --state /项目/documents/design.cwb-stage.json
python3 core/cwb.py project record /项目 previews/materials.png --stage 2 --category temporary \
  --role research_materials --document-id design-001 --region 0 0 800 1200
```

示例区域必须替换为当前素材实际所在的区域；本命令只登记已有文件，不导入、绘制或保存KRA。

## 路径、锁、修订、原子写与导出

- 资产/文档/全文/预览路径均相对项目，POSIX `/` 分隔；拒绝绝对路径、空组件、`.`/`..`、反斜杠、驱动器、符号链接、非普通文件、状态/锁作为作品。来源说明仅文本，不读取网址或项目外文件。
- 每次写先 POSIX 非阻塞独占锁；永久锁不可删除/替换。`--expect-revision N` 拒绝过期写入；省略则修改锁内最新状态，非比较并交换语义。
- 单文件同目录临时写、flush/fsync、原始状态字节复核后原子替换，首次采用“不存在才创建”。检测用户手改/忙锁/过期修订返回冲突，不压回旧状态。修订只在实际变更时递增。
- 协作锁不隔离恶意进程；最终校验与替换间仍有非协作竞争窗口。项目根及祖先目录须可信、操作期间稳定，不承诺网络盘/同步盘、断电或跨Krita/JSON事务。哈希只描述磁盘读到的字节。
- `status` 不创建锁或写任何项目数据，返回 `state`（包含计算出的复核有效视图）、`asset_checks`（ok/changed/missing/invalid）、`reference_checks` 和workflow。成功退出仅表示完成检查，须查看异常项；损坏状态/写冲突/命令错误退出1。
- `export-project` 显式选择final资产并安全复制，携带workflow、对象版本与成果身份。导出只验证文件，不等于批准或完整交付；详见[导出说明](<../docs/tools/export.md>)。
- 项目、KRA、确认快照和第三方原始材料留本地，不纳入公开仓库。

## 验证边界

[状态回归](<../tests/test_project.py>)和[工作流回归](<../tests/test_workflow_project.py>)验证并发、路径、修订、文件失败守卫、多起点、成果引用与批准失效。测试使用临时合成文件和mock，不代表真实KRA保存重开、画布绑定或作者艺术验收。
