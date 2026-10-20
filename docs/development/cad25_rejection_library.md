# CAD-25 S1：规范决定、候选归档与范围化提醒

此切片只实现离线手工记录。`project_state.json` 的 schema 2 `decisions` 是唯一决定权威；`rejection_library/` 保存不可变证据，`must_not_forget.md` 是可重建的人工清单。没有另一份 active/reopened 状态索引。

**#25 整单仍未完成。** 本切片不消费 #23 比较事件，不向实际生成 graph 或 `/prompt` 注入限制，不接风格 sidecar，不验证 Krita 隐藏/恢复、ComfyUI 生成或作者艺术判断。`context --text` 只是人工可读导出，不能当作生成请求消费或来源证明。三候选合成测试是软件 A；目标机 B、真实两个角色及作者 C 均未运行。

## 入口与先决条件

独立入口，不改 `cwb.py` 的 parser，也不修改项目 schema 或原 action 语义：

```bash
python3 core/cwb_rejections.py --help
python3 core/cwb_rejections.py init /path/to/project
python3 core/cwb_rejections.py add /path/to/project record.json --expect-revision 12
python3 core/cwb_rejections.py list /path/to/project --limit 20 --offset 0
python3 core/cwb_rejections.py list /path/to/project --query 肩灯
python3 core/cwb_rejections.py context /path/to/project query.json --limit 5 --text
python3 core/cwb_rejections.py reopen /path/to/project reopen.json --expect-revision 13
python3 core/cwb_rejections.py history /path/to/project DECISION_ID --limit 20 --offset 0
python3 core/cwb_rejections.py sync /path/to/project
```

上面的项目路径、修订和 ID 需替换为实际值。JSON 路径相对于项目；不接受绝对路径、`..`、符号链接、硬链接别名、FIFO 或控制文件别名。先用现有项目工具登记创意、明确选中主线、登记工作文档与版本化候选，再从 `project show` 的真实状态复制引用。不得从文件名猜 concept/asset/version。只支持 `krita-character-illustration-v1`；schema 1 和旧独立拒绝库不会隐式迁移。

| 入口 | 输入与实际作用 |
|---|---|
| `init` / `sync` | 创建证据目录并重算清单；不创建规范决定，不改变项目修订。清单写入有项目锁及字节比较 |
| `add` | 验证手工输入，复制已有候选与参数；追加规范 `reject`，未判断则追加 `defer` |
| `reopen` | 追加严格引用原拒绝的规范 `defer`；不修改原拒绝，不批准资产或完成阶段 |
| `list` | 字面子串检索；默认 20、最多 100 条、`next_offset` 分页。展示最近 10 条重开及完整数量 |
| `history` | 指定原决定 ID，分页读取原记录和全部有效引用的重开事件；默认 20、最多 100 条 |
| `context` | 显式绑定项目/流程/创意/文档/目标版本/阶段/主题，默认最多 5 条、上限 10；同类重复理由合并计数，最多展示最近 10 个来源 ID |

读操作不创建锁或写回失效状态。`list` 的异常和孤儿各最多展示 100 条，并给出总数；大量历史从规范主状态及留存目录进一步人工核对。本工具的输出数量有界，但会扫描、核验已有规范事件和归档，暂不承诺大型数据库查询性能。

## 手工记录格式

所有字段必填，未知字段拒绝。以下是字段合同，填写真实值；不提供虚构贯穿创作案例。

| 字段 | 填写内容 / 约束 |
|---|---|
| `schema` | 固定 `cwb-rejection-input-v1` |
| `event_key` | 本次人工决定的稳定键；ASCII 字母数字、下划线、连字符，最多 100 字符。重试用原键；确有新一次决定才用新键 |
| `project_id` / `workflow_id` | 实际项目 ID / 固定 `krita-character-illustration-v1` |
| `asset_ref` | 对象，精确且仅含 `id`、`object_id`、`version`、`sha256`；从已登记候选复制 |
| `concept_ref` / `document_id` | 精确且仅含 `id`、`version` 的创意引用 / 候选登记的文档 ID；文档须属于该创意，记录时须为当前选中创意 |
| `candidate` | 人工可读候选名；不充当身份 |
| `generation` | `manual` 或 `generated`。参数都是人工提供，未证明实际生成执行 |
| `prompt` / `seed` / `workflow` | 原 prompt 文本 / uint64 整数或明确 `null` / 项目内已存在的 workflow JSON 对象文件路径或 `null`。生成候选必须有非空 prompt 和 workflow；手绘的 seed/workflow 必须为 `null`，prompt 可为空 |
| `reason_taxonomy_version` | 固定 `cwb-rejection-reasons-v1` |
| `reason_kind` | 下表五种之一，不承接旧版 `locked_constraint/preference` 的隐含语义 |
| `reason` / `basis` | 实际判断理由 / 指向意图、支柱或具体观察的依据；不可把未判断编成已违反约束 |
| `reminder` | 最多 500 字的本轮提醒；未判断必须是空字符串 |
| `reopen_when` | 什么变化或新增证据值得重新探索；这不是自动触发条件 |
| `stage_ids` | 不重复的现行阶段语义 ID 列表。只允许候选文档角色兼容的阶段；不接受数字或同号迁移 |
| `topics` / `applies_when` | 1–12 个明确主题词 / 适用条件。主题按大小写不敏感的完整词匹配，不做模糊、向量或全文推断 |
| `decided_by` / `decision_source` | 实际决定者 / 真实用户指令位置或明确人工转录来源；合成演练必须明示。该字段不进行身份认证 |

| 分类 | 固定语义 | 规范 action / 默认提醒 |
|---|---|---|
| `round_preference` | 本轮未选或审美偏好，不外推为角色永久限制 | `reject`；默认不附带，需 `--include-preferences` |
| `character_constraint` | 违反本角色本轮明确依据的约束 | `reject`；仅相同明确范围 |
| `structural_issue` | 当前候选的结构问题 | `reject`；仅相同明确范围 |
| `generation_artifact` | 当前候选中的生成瑕疵 | `reject`；仅相同明确范围 |
| `undecided` | 未形成判断、需要复看 | `defer`；永不作为限制，不能以本工具“重开拒绝”伪造原拒绝 |

当前阶段 ID 为 `text_seeds`、`research_imagination`、`concept_selection`、`character_exploration`、`character_resolution`、`turnaround`、`illustration_planning`、`illustration_production`。例如 character 文档只允许其既有 04/05 语义。旧阶段 6 不是现行 `turnaround` 的授权。

## 范围查询格式

`query.json` 精确包含以下字段，不填隐式全局默认：

| 字段 | 填写内容 |
|---|---|
| `schema` | `cwb-rejection-query-v1` |
| `project_id`, `workflow_id`, `asset_ref`, `concept_ref`, `document_id` | 与手工记录使用相同结构；必须是已登记的明确身份和版本 |
| `stage_id` | 本次要查看的一个现行阶段 ID |
| `topics` | 本次明确需要的 1–12 个主题词 |
| `purpose` | `generate` 或 `review`；只是导出用途标签，二者都不执行生成或审评 |

查询同时要求当前选中创意与记录相符、原目标/创意/工作文档可核验、完整归档未损坏、原决定仍有效、项目意图/支柱/风格锚点快照未变。记录的项目、workflow、concept 版本、document、asset ID/object_id/version/SHA 全部精确匹配才有资格进入本次最多 N 条结果。

其他概念、文档或资产版本不会继承旧拒绝；新图不能借旧版 SHA 取得旧决定。旧引用失效、归档改动、重开和未判断会记录为 `skipped`，其明细最多 100 条并给出完整计数。没有提醒不代表作品通过。清单中的自由文本不会自动解析进 context，更不会成为全局 negative prompt。

## 重开格式与不可绕过的边界

`reopen.json` 精确包含：

| 字段 | 填写内容 |
|---|---|
| `schema` | `cwb-rejection-reopen-v1` |
| `event_key` | 本次明确重开的稳定键 |
| `project_id` | 实际项目 ID |
| `decision_id` | `add` 返回的原规范拒绝 ID；不能指向未判断、另一项目、普通无扩展决定或另一次重开 |
| `note`, `by`, `source` | 实际重开理由、决定者、真实指令/人工转录来源 |

重开只追加原有 action `defer`。其 `rejection_event` 必须使用版本 `cwb-rejection-event-v1`、操作 `reopen_for_exploration`，精确绑定原拒绝 ID、原扩展摘要、同一 asset outcomes、project/workflow/concept/document、原范围与分类。原拒绝和归档不改写；普通 `defer` 不会停止旧提醒。新一次同理由可以用新的事件键记录，旧重开不抑制它。

**源目标字节变化、丢失或不能通过既有 `project.verify_outcome` 时，重开必须失败，包括相同 event_key 的重复重开。** 失败不会提交内存中 refresh 产生的修订/validity 变化；这不撤销旧成功记录，原历史仍可只读查询。工具不会跳过原合同手造合法决定。旧归档仍可用 `list/history` 查询，但“归档能读”不等于“可以针对不可读旧目标重新决定”。先人工核对归档、恢复明确旧快照或登记新版本，再作相应真实决定。允许不可读目标独立撤销需要未来引用型生命周期设计，不在 S1 内。

原有批准失效仍由项目模块处理。新 `defer` 不清除先前冲突、不自动重新批准、不标记 completed。其他消费者可以忽略这个可选扩展；它不重新定义 schema 2 的四种 action。

## 文件与一致性合同

```text
project_state.json                         唯一规范 decisions
rejection_library/records/<decision-id>/
  candidate.png|jpg|jpeg|webp               原图逐字节副本，原路径不写不删
  prompt.txt                               原 prompt UTF-8
  workflow.json                            生成工作流原字节，手绘不伪造
  input.json                               规范化的人工输入；含 seed、原因、范围和来源
  manifest.json                            规范决定快照、绑定、意图快照、所有文件摘要
must_not_forget.md                          自动区 + 区外任意人工字节
```

重开目录只有 `input.json` 与 `manifest.json`，通过原扩展 SHA 连接原证据。规范决定的 `rejection_event.archive` 固定自己的目录、文件 SHA 和长度；manifest 反向绑定该规范决定。正常项目刷新可以将 `validity` 改为 needs_review，所以归档快照不把这一可变字段作为不可变原判断。其他历史字段不被本工具修改。

- 输入记录和 workflow 上限 16 MiB，候选上限 128 MiB。仅检查文件类型、稳定读取和摘要，不解码或鉴定图片，不认证人类身份，不证明参数实际执行。
- 所有写入复用项目锁和主修订。`--expect-revision` 指主项目修订，不是另一个库修订。
- 先独占创建全新决定目录，逐文件独占写入并 fsync，再经项目原子替换提交 canonical decision。不会覆盖同名目录、原图或旧证据。
- 归档和主状态不是跨文件原子事务。归档成功、主状态失败时保留孤儿；错误明确包含 `canonical_saved=False/True/unknown` 与记录 ID，`list` 报告孤儿数量。不要靠“退出非零”猜测决定未写入。未引用的孤儿绝不成为提醒，不自动删除或导入。
- 主状态替换后目录持久化出错会回读是否已写入，再明确报错。先 list/read-back，再以原事件键核对；不盲目新建决定。
- 相同键与相同规范请求、有效原归档和未变工作流输入幂等，不增加规范决定；在没有额外待失效引用时也不增加修订。项目原有 refresh 仍可独立标记其他失效引用。同键不同请求/工作流字节冲突。源已变或丢失不伪称同输入。重新记录同一理由须有明确新键，不靠改名偷偷替换旧决定。
- 清单自动区带内容 SHA。区外字节原样保留，手工编辑自动区或损坏/重复标记时不覆盖；先将手工内容移到区外再 sync。清单与任一规范已登记文件路径冲突或存在链接别名也不替换：包括所有 `asset.path`、`asset.preview.path`、`idea.text_ref` 去掉 `#fragment` 后的文件部分（包含未选创意），以及文档路径。内联 `intent_card/design_pillars/style_anchors` 不猜测为文件路径。决定已提交时仍按下面的 partial-success 合同回读，来源字节和已登记摘要保持不变。
- 决定已保存但清单失败时，结果明确 `canonical_saved=true, checklist_synced=false`；CLI 退出 2。其他未完整确认错误退出 1；完整成功退出 0。
- 对协作写入提供锁、修订与字节 CAS，检测读中/写前的输入变化。它不是对具有同用户文件写权限者的防篡改安全边界；SHA 绑定证据一致性，不是数字签名。

旧 `rejection_library/index.json` 一旦存在，本工具只报保留旧库、待显式重新绑定，不导入其 active/reopened 权威、不改任何旧文件。原普通 schema-2 决定缺少扩展不会损坏，也不会被工具猜测成完整拒绝记录。保留旧库或使用旧版只读查看后，由作者为当前明确对象输入新记录；本版不提供自动迁移。

## 验证与剩余门

```bash
python3 -B -m unittest discover -s tests -p test_rejections.py -v
python3 -B -m unittest discover -s tests -v
```

专属测试使用三个自制/内嵌 1px PNG 与明确标记的模拟人类决定。覆盖归档字节、五类语义、跨范围/版本、重开和再次出现、普通 defer 不重开、未知/伪造扩展、归档损坏、输入变化、锁/修订/CAS、链接/FIFO、目录/文件/状态发布失败、主状态已落盘回读、清单部分成功、手工区保留、有界结果及新进程 CLI。

CAD20 候选兼容性只作离线精确版本探针，不是 main 依赖：原阶段批准后本工具追加 reject，再追加 reopen/defer，阶段门都应继续报告冲突；需新真实阶段批准才可协调。探针应同时标明候选 SHA 和实际运行结果，不能把旧候选测试宣称为 main 功能。

| 门 | 本切片结论 |
|---|---|
| 三候选、参数/原因留存、原图保护、人工清单、相关提醒、可重开 | 可用合成材料验证的软件 A；不代替作者真实判断 |
| #23 事件去重适配 | 未实现；人工 event_key 幂等不等于 comparison_id/sequence/事件链消费 |
| 明确生成请求消费与 provenance、style 接线 | 未实现；context 文本不算 graph 注入或生成来源证据 |
| Krita 真实修改/接受/隐藏/恢复、ComfyUI 执行 | UNRUN，本切片没有访问服务/设备/模型 |
| 真实两个角色对照与艺术有效性 | UNRUN，保留 #26 及关联创作验收 |
| #25 关闭 | 不满足整单门槛，保持开放 |
