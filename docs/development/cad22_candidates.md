# CAD-22：有限离线候选计划与现成剪影拼板

> **PR-01 目标流程/待迁移提示**：PR-03 目标：图像候选工具不等于前三阶段跨创意灵感池。素材与候选须逐图独立进入 Krita 普通层/组，图文并置比较、作者选择并保留未选，绑定文档/区域/ID；分批分页而非取消数量/像素保护。当前 board/selection 仍仅离线 PNG/未执行 payload。 见[现行总纲](<../../design/Krita中枢创作流程_正式执行规范.md>)。此提示不增加现有命令、参数或运行保证。

本轮只交付 `candidates plan` / `candidates board` 两个子命令。**不生成图、不调用模型、不分割、不生成黑剪影、不连接或写入 Krita**；不修改 CAD-10 执行器，不复制 CAD-18 诊断/剪影算法，也不接入云端或调度平台。没有读取或复制 compare 内部接口。

## 1. 待执行计划（不需要 Pillow）

在父目录已存在、输出目录尚不存在的情况下：

```bash
python3 core/cwb.py candidates plan '角色基础 prompt' /tmp/my-candidate-plan \
  --count 8 --seed-start 100 --seed-end 120 \
  --direction '高窄轮廓（待验证）' --direction '低宽轮廓（待验证）' \
  --workflow-source '人工指定：某工作流版本；尚未执行'
```

输出 `manifest.json` 的状态为 `prepared_not_executed`。相同输入生成相同字节：

- 编号固定 `C001` 起，最多 64 项；seed 从 start 连续递增，end 为允许范围上界，不随机采样。
- 范围须能容纳 count 个不同 seed，seed 为 `0..2^64-1`。
- 多个 direction 轮流分配，只是人工计划，不证明结构差异；省略方向/工作流来源为 `null`。
- workflow-source 只是未验证说明，不读取所指文件、不验证模型/工作流可用性。
- 保存基础 prompt、seed 范围、每项 planned_seed、概念方向、来源和未执行状态。没有执行/重放入口。

## 2. 显式登记已经存在的 PNG

人工单独准备登记 JSON。所有计划 ID 必须出现一次，包括失败项；顺序可不同，拼板总是按计划 ID 排序。顶层只允许 `schema`、`candidates`。每项必须有下例全部字段，不接受未知字段。

```json
{
  "schema": "cwb-candidates-registration-v1",
  "candidates": [
    {
      "id": "C001",
      "status": "ready",
      "png": "/absolute/path/existing-silhouette.png",
      "sha256": "这里必须替换为该文件实际的64位小写SHA256",
      "seed": null,
      "description": "人工登记的现成素材；seed 未知，非本轮模型证据",
      "workflow_source": null,
      "concept_direction": null,
      "failure": null
    },
    {
      "id": "C002",
      "status": "failed",
      "png": null,
      "sha256": null,
      "seed": 101,
      "description": "人工记录的失败尝试",
      "workflow_source": "人工报告，未验证",
      "concept_direction": null,
      "failure": "主体 mask 不可靠；等待人工圈选/修剪后另行提供 PNG"
    }
  ]
}
```

以上仅展示两项字段，**不是八项计划可直接消费的登记文件**。须再显式登记 C003–C008；不能用推测的计划 seed 补充实际 seed。

- `ready`：必须有 PNG 路径、预先登记的 SHA256 和 `failure: null`。SHA 可用离线 `sha256sum /absolute/path/existing-silhouette.png` 获取；工具读入时逐项核对。
- `failed`：必须有原因，png/sha256 为 null，不读取失败素材。保留已知 seed/来源；未知仍为 null。不会自动分割或提供 Krita 圈选工具，圈选修剪须由人在工具外完成。
- `seed`、`workflow_source`、`concept_direction` 为明确的人工来源声明；未知必须显式 null，不从 plan 推断为实际关联。description 必填；每个文本字段最多 8 KiB。
- PNG 路径相对**进程当前工作目录**，不是登记文件目录；推荐绝对路径。不展开 `~`。同一文件不能登记为多个候选。
- 只接受单帧、8 位 RGBA/LA/调色板 PNG；必须至少有一个 alpha=0 的像素和一个 alpha>0 的主体像素，所有 alpha>0 的像素 RGB 必须已经是纯黑。允许半透明黑边缘。没有 EXIF/颜色变换或主体自动裁切。
- 以上只是格式/像素检查，**不是 mask 可靠性或剪影质量评估**；全透明、全不透明、非黑或损坏 PNG 记为失败并保留槽位。不会把彩色图静默转黑。

## 3. 拼板与仅记录的入选清单

```bash
python3 core/cwb.py candidates board \
  /tmp/my-candidate-plan/manifest.json /absolute/path/registration.json \
  /tmp/my-candidate-board \
  --columns 4 --cell-width 256 --cell-height 256 \
  --select C001 --select C008 --parent-ref '人工提供的目标组ID'
```

只有实际成功的候选能入选。此例要求 C001/C008 在登记及检查中成功；省略 select 可只制作拼板。

输出新目录包含：

- `board.png`：固定格子，每格顶部独立 24px 编号带；**白色不透明显示底**，避免黑剪影在深色预览中不可见。按**整张来源 PNG**比例 `contain`，居中，可放大或缩小，绝不独立拉伸宽高；nearest 缩放后仅作 source-over 显示合成，半透明黑在板上呈灰色，原始 PNG 副本保留完整 alpha。整数像素取整可能有不足 1px 的比例误差。统一格子不代表统一角色真实身高或模型空间尺度，来源四周留白也不裁掉。
- `C001.png` 等：仅成功素材的原始字节副本，不改源。失败项保留在 manifest 及拼板原槽位，用编号 + FAILED + 红叉占位，不因失败重新编号。
- `manifest.json`：最后写入，包含计划文件/登记文件/PNG 的绝对路径、SHA256、字节数、计划参数、独立实际来源声明、格子/contain 位置/比例、失败原因及拼板 SHA。无真实模型执行关联的声明仍为 null。状态 `created` 或 `created_with_failures`。
- `selection.json`：明确选中 ID、SHA 和 `new_layer_payloads` 草稿，`status: not_applied`、`krita_applied: false`。parent_ref 未验证，null 表示未指定。草稿附 candidate_id/operation/SHA 审计信息，不是可盲发的 HTTP 请求；以后若人工授权导入，必须重新验证 PNG/目标文档与组，并显式提取新层参数。**没有任何实际导入、覆盖、隐藏、合并或保存操作。**

退出码：0 表示本次有限操作完成且无失败候选；2 表示拼板已输出但含失败占位，**不是全体候选成功**；1 表示参数/路径/SHA/来源变化/资源/写入等错误。失败入选、未知或重复选择均为错误，不静默忽略。错误时不打印成功结果。

Pillow 是外部 CLI 的可选依赖，只在实际 board 渲染时 lazy import；不自动安装，不是 Krita 插件依赖。`--help`、`candidates --help`、`plan --help`、`board --help` 不导入候选实现/PIL、不联网、不创建缓存；plan 实际执行也不使用 PIL。

## 4. 路径、资源与失败保护

- Linux `dir_fd` + `O_NOFOLLOW` 逐级打开路径，所有输入和输出父目录拒绝符号链接；拒绝 `.`, `..`、重复/末尾分隔符、NUL 等路径别名。不把不安全路径变成占位以掩盖错误。
- 输入必须为普通文件且硬链接计数为 1；FIFO 以 NONBLOCK 打开后立即拒绝，不等待写端；拒绝设备/目录/软链接/硬链接及候选间同 inode 来源。元数据同样受保护。
- 读前/后检查 inode、大小、mtime、ctime、nlink 与路径身份；登记 SHA 必须匹配，渲染后、输出完成前重读核对计划/登记/所有 PNG。来源发生变化直接失败，不能误记为候选成功。SHA 是完整性证据，不是作者、模型或 seed 的真实性证明。
- 数量 1–64；JSON 输入/输出每份 1 MiB；单 PNG 16 MiB；总输入 128 MiB；单图 400 万像素；解码累计 3200 万像素（包括进入像素检查但失败的图）；拼板 1600 万像素；columns 1–64，格子各边 32–2048px。超总资源限制中止，不伪装为成功。
- 输出必须全新，父目录必须存在；文件/目录/符号链接旧输出一律拒绝，无 overwrite 或恢复覆盖选项。每个输出文件 O_EXCL；新目录 0700，文件 0600。
- 所有失败路径**不删除源、不删除旧输出，也不自动删除本轮部分输出**。缺少/无效 manifest 的目录不得视为完整结果，换新输出名重试。判断成功须同时检查退出码、有效 manifest 及其 status/失败数；仅文件存在不能证明成功。
- 完成标志最后写入、文件/目录 fsync，但多文件输出不是原子事务；不承诺掉电恢复或对同 UID 恶意并发改写的完全隔离。最终校验后的来源变化不会追溯改变既有快照；消费/导入前须再次核对 SHA。

## 5. 验收映射与未满足项

[离线回归](<../../tests/test_candidates.py>) 在临时目录制作 **8 个手工几何 PNG 教学 fixture**，验证稳定 ID/seed、unknown 不推测、透明度、contain/编号、失败占位、SHA/来源变动、恶意路径/FIFO/链接、旧输出/部分输出保护、资源上限、仅记录选择与帮助无副作用。fixture 不入仓库；本轮存证副本放到任务指定的 `/tmp` 证据目录。

| 原任务要求 | 本轮证据及限制 |
|---|---|
| 基础 prompt/数量/seed 范围 | 有限、确定的待执行计划；没有批量生成 |
| 保留 seed/来源/说明/失败 | 显式人工登记 + SHA；未知为 null，不证明真实生成关联 |
| 剪影与 mask | 只消费现成透明黑 PNG；不分割、不生成剪影；失败保留人工处理说明 |
| 统一板、编号、选中 | 8 教学 fixture 验证 contain 排版/失败槽位/清单，不证明视觉方向有效 |
| 指定组内新层 | 仅未执行 payload；没有真实 Krita 组内导入证据 |

**仍未满足原 Issue 的完整验收**：真实八候选生成；至少两种结构方向的人类对照确认；可靠主体 mask/人工修剪实测；真实指定图层组非破坏式导入。暂缓的 Qwen 模型测试未执行。不能以此提交宣称 #22 完成、方向有效或 mask 可靠。
