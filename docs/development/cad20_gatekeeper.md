# CAD-20｜离线阶段交接检查

本切片复用 PR-02 的 `project_state.json`、schema2、`krita-character-illustration-v1`、资产版本与人类来源记录。它检查可证明的索引/文件/批准记录一致性，不看图、不访问 Krita/ComfyUI、不自动认证作者身份。`ready` 只表示当前软件规则满足；B 实机与 C 作者艺术验收仍为 `unrun` / `unverified`。

现行创作以[八阶段一体简表](../../design/执行手册/README.md)为入口。此处是命令说明，不另建填写模板，也不要求重复手工抄录表格。旧 `stage setup/status/import-diagnostic` 仍只处理独立图层组 sidecar；新增 `check/advance` 的位置参数必须是项目目录。

## 一处看清检查、补齐与人工判断

| 阶段与输入交接 | 检查的现有成果/记录 | 人工仍需判断与下一步 |
|---|---|---|
| 01 文本起点，无前置 | 至少一个可用 `idea:ID`；本阶段批准 | 当前起点能否继续探索；不要求完整意图卡或固定数量 |
| 02 联想调研，参考01 | `research_board` 确认快照；01及02批准 | 联想聚簇、事实来源与推断边界；不要求每图证明用途 |
| 03 创意选择，参考02 | 当前 `selected_concept_id` 对应起点；02及03批准 | 跨起点比较与本轮选择；保留未选方向 |
| 04 角色探索，参考03 | `character_exploration`；03及04批准 | 大形、穿戴结构、粗线稿、基础色是否可供收敛 |
| 05 角色收敛，参考04 | `character_master_lineart` + `character_master_basecolor`；04及05批准 | 实际修订/无需修改理由及复看；软件不能证明真的画过 |
| 06 三视图，参考05 | `turnaround_front/side/back` 三类；05及06批准 | 正侧背结构、左右、比例、基础色；文件数量/拼版不代表一致 |
| 07 插画方案，参考05及06 | `illustration_plan`；05、06及独立07批准 | 事件、镜头、动作、构图、明暗光色；角色确认不代替插画确认 |
| 08 制作交付，参考06及07 | `final_illustration` + `editable_source` + `delivery_manifest`；06、07及08批准 | 8A—8G实际制作、打开重检KRA/导出、作者决定停止 |

以上输入是现行路线的交接警告，不是等待所有手册/所关联 Issue 关闭。只核对表中所列输入，不递归要求所有历史阶段已标 `completed`；实验阶段可说明理由导航跳过。成果登记方法见[项目状态 CLI](../../design/项目状态_CLI.md#阶段完成与必要成果)。除文本起点外须本流程、本阶段、不变 checkpoint；视觉成果需文档与组标记/画布区域。普通工作文件、旧流程资产、文件改变/缺失不充作必要成果。

`check` 同时返回：
- `outcomes.missing_roles/missing_idea`：具体缺项；`unavailable` 给出不可用文件/绑定及原因。未用于当前批准的旧候选可以保留，不因存在无关坏候选一概阻断。
- `inputs`、`human_confirmation`：最新版阶段批准是否有来源、版本一致、文件仍在。最新批准失效不会偷偷回退使用更早批准；批准之后对应成果又被 `reject/defer`，提示范围冲突并待新的阶段确认；单独资产approve不能自动消解另一范围的拒绝。03—08持续核对当前主线的选择证据、版本及后续拒绝/暂缓，04以后还核对成果文档所属创意，避免换主线后沿用旧角色文档。
- `human_check`：作者需看的事项。人类来源只是使用者提供或人工转录，未经身份认证；文字 `human`、role或CI通过都不是艺术批准。
- `downstream_review_stages`：PR-02版本失效所影响的阶段。已有新有效批准可重新满足该阶段软件交接，未来阶段的复核提示不等于当前已完成。

当前主线的 `text_ref` 改写/丢失或选择记录失效时，05—08也会提示；不能仅靠后来角色/插画批准掩盖主线冲突。先核对真实来源和版本，必要时用新起点ID重新选择并绑定文档。对于仍有效的同一版本，明确重新 `select` 或新的03阶段交接批准可以处理此前主线reject/defer；已失效的选择须重新记录，不能只把文件改回原字节就抹掉已持久化的needs_review。不要求所有历史阶段completed，也不把未选起点的拒绝变成全局禁令。

07→08使用批准成果的 `document_id` 和 `illustration_id` 核对同一插画交接。07需明确一个方案文档，08的最终图以及有文档绑定的源文件/清单须对应它；同角色甚至同illustration_id的另一个文档都不自动继承构图批准。合法另存/衍生路线应先登记实际保存的方案快照到新文档，并明确记录对应07交接确认，再检查08；不伪造继承关系。没有绑定文档的源文件/清单继续按PR-02原合同仅检查文件/版本，其内容与实际画布的对应仍未验。若08已登记的批准指向另一幅画，07→08导航也给警告，需补齐对应交接或明确理由override。

## 只读检查、停止与恢复

```bash
python3 core/cwb.py stage check /明确/项目目录
python3 core/cwb.py stage check /明确/项目目录 --number 6 --strict
python3 core/cwb.py stage check /明确/项目目录 --number 4 --intent detail-render
python3 core/cwb.py stage check /明确/项目目录 --number 6 --intent structural-change
```

默认报告 `warning`，退出0表示检查已执行；`--strict` 有警告或规则未配置时退出2。`check` 不创建锁、不写项目/作品/缓存，只返回有效复核视图。旧schema1、未知schema/workflow/阶段报告 `not_configured`，不按编号套用新规则。损坏的已知schema或矛盾引用是错误（退出1），不能用override越过；保留原文件按原项目恢复说明处理。

`detail-render` 是本次调用者声明的意图：缺少当前角色探索/主稿或插画方案确认时，明确提醒先看大形/结构。`structural-change` 在05以后提醒保留候选并复核受影响版本。PR-02已经登记的版本变化可自动读出 `needs_review`；**尚未监听生成/画布事件，不会自动发现未登记改动的语义或重大结构变化，不会拦截普通生成命令**。这部分仍依赖后续生成/版本事件与真实创作接线，不能宣称 #20 整单自动警告验收已完成。

## 推进只改变关注位置

```bash
python3 core/cwb.py stage advance /明确/项目目录 --expect-revision 12
python3 core/cwb.py stage advance /明确/项目目录 --to 7 \
  --override "先保留早期插画灵感，三视图仍待补齐" --note "不是完整路线交付"
python3 core/cwb.py stage advance /明确/项目目录 --to 4 --note "回看角色结构"
```

`advance` 默认检查当前阶段交接与目标输入。有警告则退出2、停止导航、不写项目状态，并返回具体缺项和恢复方法；修复文件/登记正确版本、取得实际人类确认后重跑。若只是探索或需要跳阶段，明确 `--override 理由` 才导航；理由空白拒绝。它绕过的是导航警告，**不会创建作者批准、补出正侧背、把来源不明或矛盾状态判completed**。有格式错误的项目仍拒绝写入。

回退 `--to` 无须以当前阶段合格为前提，记录方向、备注和当时警告；同一目标不写状态或重复历史。08没有自动第09阶段；交付/停止仍由作者判断。成功导航仅把目标 `pending` 改为 `in_progress`，保留原有完成与批准事实；锁内会依既有PR-02语义刷新失效批准，必要时把旧completed降为awaiting_review，绝不推断新completed。人工需要明确完成记录时继续使用原 `project stage --status completed --approval ...`，它与导航分开；Gatekeeper不是所有旧命令的强制代理或身份认证边界。

在原 `stage_history` 加 `gatekeeper` 字段，保存规则版本、来源revision、方向、显式操作意图、override理由、具体警告与 `completion_inferred:false`。不另造状态文件/批准系统。复用项目POSIX合作锁、修订冲突和原子写；过期修订/忙锁/写失败不当作成功。若报告目录fsync失败，文件可能已替换，先 `project status` 核对当前阶段、revision与历史，勿盲重试；对非合作写者及恶意目录替换的边界沿用原合同。

## 验证与尚未完成的责任

`python3 -m unittest discover -s tests -p test_gatekeeper.py -v` 使用临时合成文件和来源说明，覆盖缺失→补齐、八阶段、正侧背部分完成、未知流程/阶段、旧06迁移、批准版本/来源损坏、最新批准失效、人工拒绝/暂缓、显式警告、override跳过、回退、锁/修订/原子失败恢复、重复调用及CLI退出语义。运行完整suite还需 `python3 -m unittest discover -s tests -v`；测试成功不是艺术验收。

| 原 #20 要求 | 本切片可验证范围与剩余 |
|---|---|
| 基于project_state构建阶段输入/成果/人工项规则 | 复用schema2/role/来源/版本，按上表聚合具体软件缺项；不重做PR-01/02 |
| stage check / stage advance | 已提供离线入口；导航与completed分离 |
| 大形未定时细节提示；05后重大结构变更警告 | 显式意图与已登记版本失效有提示；未接生成/画布事件，自动识别/及时触发仍待实现/实测 |
| 默认警告、理由override、历史与回退 | check默认警告；有警告的advance需显式理由，回退允许，记录具体历史 |
| 缺失/补齐/跳过/回退fixture、未知阶段未配置 | 纯CPU fixture覆盖；不是真实作品 |
| 旧06不变新三视图完成；缺成果/版本不一致/来源不明不假完成 | 原PR-02校验保留；检查报告不足/错误，override不创建completed/作者批准 |
| 审美最终确认与原完整验收 | 来源仅声明；真实作者判断、实际画布/生成事件接线、B实机与C创作仍未验；#20保持未完成整单 |

关联原需求：[Issue #20](https://github.com/CongliangK/Coagentdraw/issues/20)。#50—#54承担对应创作证据，不以它们关闭为本CLI运行前提；本切片不把未完成原验收转嫁给父项或取消。
