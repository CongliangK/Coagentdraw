# CAD-26：有限离线风格 sidecar（不是主状态接入）

## 范围与尚未完成的门槛

这是 #26 的**配置、显式提示导出与人工标杆比较工具**，不是整个 Issue 的验收完成。

- `style_tokens` 只保存在已有 CAD-19 项目目录中的独立 `style_tokens.cwb/`。**项目主状态只有 `style_anchors`，没有新增 `style_tokens` 字段**；本实现不修改 [cwb_project.py](<../../core/cwb_project.py>)、主状态 schema 或既有锚点，不将两者自动同步。
- 不自动读取执行手册，不改 #5 手册、#10 模型代码、生成请求或工作流。提示仅在用户明确执行 `style prompt` 时组织/导出，后续是否采用由人决定。
- 没有调用 Krita、ComfyUI 或任何模型，没有联网、下载参考图或生成缓存。Pillow 只在登记 PNG 参考或调用比较 `create` 时 lazy 加载，是**外部 CLI-only 可选依赖**，不是 Krita 插件依赖，不自动安装。
- `style_tokens`、提示词与标杆不保证模型严格跨图一致；不提供自动艺术评价/风格评分，不引入 LoRA。
- **仍待：**经另行授权的主状态 schema/迁移接入、生成主流程接入，以及同一项目的两个不同角色真实生成、同组可追溯约束下人工对照与修复记录。本次离线合成色块/单元测试不替代这些门槛，不据此关闭 #26。

## 配置与命令

以下命令均在仓库根目录执行；`PROJECT` 为已有 CAD-19 项目，示例中的文件/路径须由操作者明确准备。所有 style 写操作都要求 `--actor`、非空 `--reason`；这是人工自报记录，**不是身份认证**。

```bash
python3 core/cwb.py style --help
python3 core/cwb.py style init "$PROJECT" --actor 人工甲 --reason 建立风格档案
python3 core/cwb.py style show "$PROJECT"
```

`init` 新建固定 sidecar 目录与空修订 0。旧文件、目录、链接均不接管、不重置；未完成的空目录也保留，需人工检查/恢复，而非再次初始化覆盖。

完整输入 JSON 示例（`/absolute/...` 换成现有 PNG；来源可以是人工说明/URL，`null` 表示未知，不会抓取或验证版权）：

```json
{
  "references": [
    {"id": "benchmark", "path": "/absolute/benchmark.png", "source": "作者/来源/授权限制待人工核实", "scope": ["*"], "enabled": true, "locked": false},
    {"id": "detail", "path": "/absolute/detail.png", "source": null, "scope": ["character-b"], "enabled": true, "locked": false}
  ],
  "style_tokens": [
    {"id": "palette", "dimension": "palette", "value": "低饱和蓝，橙色作为少量点缀", "reference_ids": ["benchmark"], "scope": ["*"], "enabled": true, "locked": false},
    {"id": "proportion", "dimension": "proportion", "value": "记录头身比及夸张程度，留待人工核对", "reference_ids": ["benchmark"], "scope": ["character-a", "character-b"], "enabled": true, "locked": false},
    {"id": "shape", "dimension": "dominant_shape", "value": "圆形主导，三角形作对比", "reference_ids": ["benchmark"], "scope": ["*"], "enabled": true, "locked": false},
    {"id": "detail", "dimension": "detail_density", "value": "脸部密集、躯干简化", "reference_ids": [], "scope": ["*"], "enabled": true, "locked": false}
  ]
}
```

```bash
python3 core/cwb.py style edit "$PROJECT" --config style-input.json \
  --expect-revision 0 --actor 人工甲 --reason 登记标杆与四类约束
python3 core/cwb.py style state "$PROJECT" references benchmark lock \
  --expect-revision 1 --actor 人工甲 --reason 人工确认标杆
python3 core/cwb.py style state "$PROJECT" style_tokens palette lock \
  --expect-revision 2 --actor 人工甲 --reason 人工确认配色
```

配置规则：

- 仅接受 `references`、`style_tokens` 两个列表，每类最多 128 项；严格校验字段、类型、重复 JSON 键、非有限值、大小和 ID。ID 为小写字母开头、最多 64 字符的 ASCII 字母/数字/下划线/连字符，在各自列表内唯一。
- 维度固定为 `palette`、`proportion`、`dominant_shape`、`detail_density`，值是人工文字，不是可执行模板。`reference_ids` 只能引用存在的参考，空列表不虚构来源。
- `scope` 是非空、不重复的精确文本列表；`["*"]` 表示全局，不能与具体范围混写。导出时必须指定具体范围，不接受 `--scope '*'`。
- 登记一张或多张单帧 PNG，支持能显示为 RGBA8 的模式，拒绝 16 位/动画/其他格式；最多 128 MiB、1600 万像素。保存**实际读取字节**的 SHA256、字节数、原生宽高与规范绝对路径，不拷贝原参考到 style sidecar。
- 参考相对路径以**调用时工作目录**为准，不是相对项目目录；不展开 `~`。路径中的 `.`、`..`、重复/末尾 `/`、符号链接（包括父目录）、普通文件的硬链接别名及 FIFO 等均拒绝。不要先用 `resolve()` 悄悄消除输入别名再传入。
- `edit` 输入是完整配置，不能省略既有条目、删除旧 ID 或注入保存时的 `sha256`、`bytes`、`dimensions` 字段。`show.latest.config` 含这些捕获字段，**不能原样作为 edit 输入**；人工编辑输入时应去掉参考的这三个捕获字段，并保留全部原有条目及状态。
- 输入排列不影响提示：条目按 ID 排序，scope/引用 ID 也排序；相同配置不追加空修订。

## 人工锁定、改动、取消与来源变化

锁定项的文字、路径、摘要、来源、scope、启用状态均不能通过 `edit` 改动。新增条目不能直接带 `locked: true`。现有条目的 `locked`、`enabled` 不能用 `edit` 偷换；必须执行明确的状态动作。

```bash
python3 core/cwb.py style state "$PROJECT" style_tokens palette unlock \
  --expect-revision 3 --actor 人工乙 --reason 解锁后修正人工配色约束
# 修改完整输入 JSON 中的文字，保持全部既有 ID 和当前状态，再执行 edit（expected 为 4）。
python3 core/cwb.py style edit "$PROJECT" --config style-input.json \
  --expect-revision 4 --actor 人工乙 --reason 修正配色约束
python3 core/cwb.py style state "$PROJECT" style_tokens palette cancel \
  --expect-revision 5 --actor 人工乙 --reason 本轮取消配色锚点
# enable 可显式重新启用；cancel 不是删除，历史与条目均保留。
```

每次动作需使用 `show` 返回的最新修订。锁定时取消也必须先 `unlock`。重复动作、未知目标、空理由、布尔/负修订或修订冲突均不写入。

`show.reference_checks` 按原路径重新检查，区分 `ok`、`changed`、`missing`、`invalid`，旧修订的 SHA **不自动刷新、不冒用为新参考的来源**。适用且启用的参考改变/缺失/无效时，提示与比较失败；适用 token 引用已停用或不适用的参考时也失败，不静默省略来源。

参考变化后的明确路径是：保留历史 → 对锁定参考执行 `unlock` → 完整 `edit` 重新捕获摘要/尺寸，或 `cancel` 停用参考并显式处理仍引用它的启用 token。`unlock`/`cancel` 即使原参考已丢失也可记录；其他写动作保守地检查配置中全部参考，而提示/比较仅检查当前适用且启用的参考。

## 显式提示片段与来源

```bash
python3 core/cwb.py style prompt "$PROJECT" --scope character-a
python3 core/cwb.py style prompt "$PROJECT" --scope character-b --output character-b-style.json
```

输出 JSON 含有稳定的 `fragment`（`dimension: value`，按 token ID 排序）、适用 token/参考完整记录、`style_revision`、该修订文件 SHA256、当时观察到的项目 ID/修订/摘要及边界提示。相同修订、scope 与未变参考的重复导出稳定，没有导出时间戳。`--output` 仅创建完整的新文件，绝不覆盖任何既有文件/目录/链接；禁止写入 style 修订目录。即便没有适用 token，也如实导出空片段，不假装产生风格约束。

**这不是生成请求。** 没有自动改 prompt、seed、模型、workflow 或 #10 运行记录；来源摘要证明文件追溯关系，不证明文字在真实生成中被采用或产生一致性。

## 标杆对比：只消费已合并 CAD-23 的公开接口

```bash
python3 core/cwb.py style compare "$PROJECT" current.png comparison-new \
  --reference benchmark --scope character-a --crop 0 0 512 512 \
  --candidate-version 人工作品-v2 \
  --palette '人工：主色偏暖，待修正' \
  --proportion '人工：头身比偏差待检查' \
  --dominant-shape '人工：主导形状可辨识' \
  --detail-density '人工：躯干细节偏密'
python3 core/cwb.py compare show comparison-new
```

- 适配器只调用 `cwb_compare.create` 与 `cwb_compare.show`，不改 compare 实现，不调用其私有图像/存储函数。
- 明确选择一张适用且启用的参考；左侧 Before 为该参考，右侧 After 为手工提供的当前 PNG。多参考配置不自动混合/选“最佳”。
- **两图原生尺寸必须相同，必须显式给共同 `x y w h` crop，scale=1**；不拉伸、不自动对齐/重采样。不同大小图片须由操作者在本工具外另存准备并登记来源。共同像素尺度不是相同人物身高/语义尺度，也不执行色彩管理校准。
- CAD-23 保存原始 PNG 字节及 SHA、并排图、共同 crop、尺寸与 scale。style 来源 JSON 放入公开的 `sources.before.source_version`；里面包含修订/参考/适用约束/人工四维评语及验证契约。`candidate_version` 是人工提供，未知为 null；seed/workflow 等未观测字段保持 null。
- 四维评语为**人工自报、未核验**，省略为 null（未评审），不是自动一致性分数。比较仅离线产物，不改 Krita 图层或主状态。
- 已有输出、输入输出同名/别名、错误尺寸/crop、失败验证都不作为成功。不会盲删已有结果。公开 compare 接口每个来源文本字段最多 64 KiB；超大的风格来源 JSON 会明确失败，不截断追溯信息。

### 来源竞态的准确边界

调用前重新验证适用参考当前 SHA；`create` 后以公开 `show` 验证保存的参考 SHA/字节数/尺寸，并再次检查适用参考原路径的当前 SHA。只有这些检查通过，`style compare` 才打印成功 JSON，带有 `reference_validation`。

**公开 `create` 没有 `expected-source-sha` 参数，也没有与 style 共同持有的参考写锁。** 若参考在预检查和 `create` 捕获之间变动，CAD-23 可能先创建一个对它自己有效的目录，style 后检查才失败；若在返回后变动但保存字节仍是旧版，额外的原路径后检查也会报错。此时命令失败并指出目录仅供诊断，**保留新目录，不删除证据、不伪装成功，也不修改 compare manifest**。其 `source_version.validation_contract` 明确“保存的来源信息不等于适配器成功”；单独 `compare show` 可以查看诊断目录，不代表通过 style 验收。

检查不能防止检查点之后的修改，也不能保证检测“改动又恢复”的 ABA 过程。没有宣称原子快照或连续锁保护。对完整适用参考集合的后检查也不能把多个原路径变成原子一致快照。严格竞态封闭仍需将来另行批准的公开接口能力，不能虚构参数或私改 #23 来绕过。

## sidecar 持久化与恢复

目录内是 `revision-000000.json` 起的不可覆盖完整修订，每条记录包含版本、配置、项目观察快照、操作目标、actor/reason、UTC 时间及前一修订原始字节 SHA256。

- Linux `dir_fd` / `O_NOFOLLOW` 锚定目录、普通文件限额/链接检查；读文件前后校验元数据和当前名字。目录 `flock` 共享读/排他写，写入时强制 `expect-revision`，两个遵守协议的写者基于同一修订仅一个成功。
- 先写独占临时文件并 fsync，再 hard-link 不存在的最终名字发布、清理本次临时名并 fsync 目录；不 replace 旧修订。写前失败不追加；目录 fsync 的后发布失败明确提示“已发布，请重开查询”，不能盲目重试。初始化失败留下的目录保留现场。
- 读历史验证 schema、序号、字段、项目归属、链及状态转换。断号、损坏、链接或链不一致都失败关闭，不自行修复/清理。不认识的非修订文件（例如中断留下的临时文件）不自动删除。
- 这是应用层不覆盖与可追溯历史，**不是签名/防篡改存储**；有文件写权限的人仍可重写整条链或删除尾部，不能证明操作者真实身份，需自行备份。
- style 锁独立于 #19 项目锁；项目只读快照不与主状态事务绑定。主状态另行更新时 `show` 区分 `latest.project`（当时观察）与 `current_project`（本次观察），后续 style 修订重新记录观察值。没有回写或覆盖主状态。
- 项目 ID 与绝对项目路径绑定，参考也保存绝对路径；目录移动/复制到不同位置不属于自动可移植/迁移能力，不能静默接管。保持目录稳定并备份全部历史与原参考；人工恢复应另行审批，不用 reset/删目录掩盖问题。

## 离线验收映射

`python3 -B -m unittest discover -s tests -p test_style.py -v` 覆盖：

| 有限能力/风险 | 测试证据 |
|---|---|
| sidecar 与主状态分离、完整修订链、只读观察快照 | `test_init_full_history_and_project_untouched`、`test_observed_project_snapshot_not_main_state_extension` |
| 人工 lock/unlock/edit/cancel/enable 明确记录、不能通过 edit 绕过 | `test_lock_unlock_edit_cancel_enable_are_explicit_and_reopenable`、`test_edit_cannot_delete_lock_or_toggle_enabled` |
| flock + stale revision、错误输入不写入 | `test_two_processes_same_expected_revision_only_one_wins`、`test_revision_conflict_checked_before_reading_config`、`test_malformed_config_fields_values_json_fail_closed` |
| 文件别名/链接/大小/旧输出/历史保护、持久化失败 | `test_paths_lexical_and_link_aliases_never_traversed`、`test_config_symlink_hardlink_fifo_directory_and_limit_rejected`、`test_history_chain_sequence_schema_and_project_identity_fail_closed`、`test_write_failure_never_overwrites_history_and_postpublish_failure_is_explicit` |
| 一/多参考 SHA/尺寸/来源、参考变化和锁定刷新 | `test_one_and_multiple_references_hash_size_scope_and_sources`、`test_changed_reference_not_relabelled_and_unlock_refresh_has_history`、`test_missing_or_invalid_reference_reported_can_unlock_cancel_but_not_reuse` |
| 提示稳定、精确 scope、来源未丢弃、没有生成 | `test_prompt_stable_scoped_export_has_revision_sources_not_generation`、`test_reordering_input_is_noop_and_prompt_is_deterministic`、`test_ref_scope_mismatch_does_not_silently_discard_token_provenance` |
| 复用公开 compare、共同 crop 不拉伸、RGBA 原像素保留、人工评语 | `test_comparison_reuses_public_api_native_common_crop_and_provenance`、`test_comparison_mismatch_crop_and_selection_fail_without_stretch` |
| 参考调用间竞态、保存字节正确但当前来源变动、后验证失败不误成功/不盲删 | `test_race_between_precheck_and_public_create_keeps_diagnostic_not_success`、`test_live_reference_change_after_create_detected_even_when_saved_before_matches`、`test_comparison_show_failure_preserves_diagnostic_and_prints_no_success`、`test_existing_comparison_or_source_output_never_deleted_or_overwritten` |
| 所有层级帮助不加载执行依赖/不联网/不建缓存，非图像操作不需 Pillow | `test_all_help_loads_no_execution_dependencies_network_or_cache`、`test_style_import_and_non_image_operations_need_no_pillow`、`test_optional_pillow_missing_error_is_clear_and_never_installs` |

另需执行全套 unittest、全 core Python AST、`bash -n core/install.sh`、`git diff --check`。真实两个角色、人工视觉验收、主状态接入及真实生成集成均未由上述离线检查证明。
