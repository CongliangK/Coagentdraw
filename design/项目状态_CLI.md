# 项目状态与成果索引（CAD-19 首版）

> **PR-02 待迁移目标**：新 schema_version 2 与 workflow_id `krita-character-illustration-v1` 分别表达结构与阶段语义，增加多 ideas、selected_concept_id、多角色 documents、版本化 assets/decisions 与带 workflow 历史；新 current_stage 不证明完成。旧 schema 1 保持旧解释，新完成需实际成果和人类确认。显式 migration-plan/升级接口尚未实现：应先只读计划、授权、备份，再建新索引；保留旧路径/sidecar，未知映射不自动完成。工作KRA与不变确认快照分开，角色/三视图变更触发下游需复核。见[迁移合同](<Krita中枢创作流程_正式执行规范.md#新旧语义与迁移边界>)。下方命令与状态仍是旧版接口，不是新schema用法。

这是离线 JSON 状态管理，不需要 Krita、ComfyUI、模型或数据库。仍按旧八阶段/schema 1解释；现行创作合同已更新，但本 CLI 尚未迁移。首版可以手工填写意图（新[多文本起点](<执行手册/01_文本创作起点.md>)不等于当前单 intent_card 能力）；本功能的离线测试不代表真实作品、模型或 Krita 运行验收。

## 与八阶段执行手册配合

历史 I/Q/S/P/H/F 及 X04/R06/PK07/C08 标识仅属旧人工交接约定，非现行手册必填协议。本 CLI 不解析这些标识、不自动维护引用关系，也不根据它们推进或验收阶段。

- `intent_card` 保存手工输入的文本；`design_pillars` / `style_anchors` 保存手工输入的字符串列表。可以写入 I/F 等标识或文件引用，但不会自动导入、同步或校验手册里的卡片、支柱、来源与证据。
- `project_id` 与成果 `id` 是状态管理的独立 UUID，不替代 I/Q/P/F 等含义标识，也不对应 Krita 会话内节点 UUID。成果索引记录实际文件路径和字节指纹；`--note` 可手工注明关联记录。
- 八个 `stages/` 目录按原有“意图→调研→提案→探索→锁定→收敛→交付→插画”命名，不与现行执行手册按相同编号对应（尤其旧06收敛不等于新06三视图）。手册中的文件名及版本号仍由执行者维护；CLI 只建空目录，不生成卡片、示意图、真实作品或 `.kra` 文件。
- `pending` / `in_progress` / `completed` 是人工记录的管理状态；`temporary` / `final` 是人工指定的成果类别。它们不证明手册门槛已通过、方向已获人类锁定、真实作品已验收或下游依赖已满足。跨阶段跳转、回退及重新验收仍由人决定；教学暂选也不能因登记为 `final` 而变成真实完成稿。

## 快速使用

在仓库目录运行：

```bash
python3 core/cwb.py project init "旅人"
python3 core/cwb.py project status "_本地暂存/旅人"
python3 core/cwb.py project edit "_本地暂存/旅人" --intent "角色意图文本" --pillar "克制" --pillar "仪式感" --anchor "水彩"
python3 core/cwb.py project stage "_本地暂存/旅人" 1 --status completed --note "用户确认阶段一完成"
python3 core/cwb.py project stage "_本地暂存/旅人" 2 --note "开始调研"
# 手工将已有成果保存到项目目录后登记；此命令不创建/复制/移动/覆盖作品。
python3 core/cwb.py project record "_本地暂存/旅人" "stages/02-调研/调研.md" --category temporary
python3 core/cwb.py project status "_本地暂存/旅人"
```

`init` 默认使用**仓库**下的 `_本地暂存/<项目名>/`，不依赖调用时的工作目录。`--root /任意/项目目录` 可覆盖；其他命令的 `root` 相对于调用者的当前目录。带空格的路径需加引号。项目名默认必须是单个目录名。

成果 `path` 始终相对于**项目目录**，用 `/` 分隔；拒绝绝对路径、`..`、`.`、反斜杠、驱动器路径、符号链接、非普通文件及状态/锁文件。文件可以位于项目内任意子目录，建议存放在对应阶段目录。不会读取或写入项目外的成果。登记时文件必须已存在。

## 目录与 JSON

```text
_本地暂存/旅人/
  project_state.json
  .project_state.lock
  stages/
    01-意图/
    02-调研/
    03-提案/
    04-探索/
    05-锁定/
    06-收敛/
    07-交付/
    08-插画/
```

项目本身及 `.kra` 继续遵循仓库现有忽略规则，不上传公开仓库。

`project_state.json` 结构版本为 `schema_version: 1`：

- `project_id`：初始化时生成的 UUID，移动整个项目目录后仍然保留。不是 Krita 临时图层 UUID。
- `name`、`created_at`、`updated_at`：项目名与 UTC 时间。
- `revision`：从 0 开始，每次实际状态变更递增；重复相同操作不增加。
- `current_stage`：1–8；`stages` 保存每阶段的相对目录和 `pending` / `in_progress` / `completed` 状态。
- `intent_card`：手工填写的完整文本；`design_pillars`、`style_anchors`：字符串列表。
- `stage_history`：初始化、切换阶段及阶段状态变更的时间、原阶段、目标阶段、备注；变更还包含目标阶段的原状态和新状态。
- `assets`：追加式成果索引。每条包含独立 UUID `id`、项目相对 `path`、`sha256`、字节数 `size`、阶段 `stage`、`temporary` / `final` 类别 `category`、登记时间和备注。

`edit --intent` 替换完整意图文本；重复 `--pillar` / `--anchor` 替换对应列表，不提供的字段保持不变。用 `--clear-pillars` / `--clear-anchors` 清空；`--intent ""` 清空意图。风格锚点可记录文字或来源网址；本命令不会联网、下载或验证授权。

`stage` 支持回退和跳转，以人的决定为准，不自动把前序阶段标为完成。`--status` 默认 `in_progress`；`completed` 是用户显式记录，不是程序替用户验收作品。

`record --stage` 可明确成果所属阶段，否则使用当前阶段。相同路径、哈希、阶段、类别和备注的重复登记是空操作。资产修改后再次登记会追加新记录，旧 ID/哈希不被覆盖；这不是文件版本备份，同路径旧字节不会自动保存。请另存不同文件名以保留旧作品。

`status` 返回 `state` 和 `asset_checks`，逐项标记 `ok`、`changed`、`missing` 或 `invalid`。同路径旧版本会报告 `changed`。检查不修改状态或资产；命令成功表示完成检查，资产异常看 `asset_checks`，而不是只看退出码。非法/损坏状态、写入冲突等命令错误返回 1。

## 并发、失败与恢复

- 首版写入面向支持 POSIX 文件锁、原子替换的本地文件系统；Windows 写入明确报错，网络盘/同步盘锁语义不在本轮验证范围。
- 所有 CLI 写入先取得非阻塞独占锁，再读取最新状态并在锁内修改；忙时明确报错，可重新查看状态后重试。锁随进程退出释放，`.project_state.lock` 永久保留，**不要删除/替换该文件**。
- 需要“基于已查看版本修改”时，对 `edit` / `stage` / `record` 提供 `--expect-revision N`。不提供时修改锁内最新状态；两个调用显式修改同字段可能先后生效。过期前置条件会拒绝写入。
- 写入使用同目录独立临时文件、flush/fsync，再原子替换 JSON；首次创建使用原子“不存在才创建”，不会覆盖已有状态。目录也进行 fsync。替换前检查原始状态字节，检测手工编辑。
- 锁是**协作式**保护，不能阻止不遵循协议的编辑器恰好在最后检查与替换之间写入。手工编辑前应停用其他写入者；需要并行编辑时使用 CLI 和修订前置条件。此工具不是恶意进程隔离或通用同步系统。
- 替换前中断保留旧状态；初始化中断可能留下已建目录，可重复初始化。正常异常清理临时文件；强杀可能留下 `.project-state-*.tmp`，这些不会被恢复或当作正式状态。确认无写入进程后可自行保留检查或清理，工具不自动清理用户文件。
- 替换成功后若目录持久化失败，会明确提示“状态已替换”；重新查看实际状态再决定后续操作，不应把错误当成未写入。
- 坏 JSON/未知结构版本不会被初始化覆盖。保留原文件，人工从备份恢复；不提供自动修复、备份或迁移。重复初始化同名项目不重置任何内容；不同名报错。
- 哈希只反映登记/检查时读到的字节，不锁住创作软件。检测到读取过程中变化会报错，作品稳定后再登记。登记后继续编辑会在下一次 `status` 报告变化。

## 离线验证

```bash
python3 -m unittest discover -s tests -v
```

测试使用临时作品，覆盖独立 CLI 进程重开、中文/相对路径、重复调用、缺失/修改资产、异常路径、坏 JSON、并发/过期修订、写入失败与中断；不调用 Krita 或 ComfyUI，不操作现有作品。
