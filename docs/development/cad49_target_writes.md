# CAD-49：受保护追加写入 v1 候选

Refs [#49](https://github.com/CongliangK/Coagentdraw/issues/49)、[#55](https://github.com/CongliangK/Coagentdraw/issues/55)。本次是**逐对象目标保护切片**，不是 PR-03 全实现，也不是整批原子提交。

## 状态与原验收边界

- A（本切片）：版本化协议、同回调目标/已有组身份核对、不可变单操作 ID、进程期收据、同份 PNG 字节摘要及 sidecar 故障恢复证据。测试调用生产服务及 ops，但 Krita/Qt 对象为 CPU 替身。
- 未完成的原 A：整个多对象批次的不可变清单/批次 operation_id 与完整恢复协议；资料/短注/素材簇/跨创意选择的全部接线、资产角色及区域绑定。`stage setup` 每个对象各有 operation_id；不能把这些收据称为整批事务或完整批次恢复。
- B：真实 PyQt6/Krita 主线程、原生对象生命周期、特殊图层、落点/通道、实际移动/隐藏、拆合/选主线、KRA 保存关闭重开均未运行。此候选涉及实际写入，**不能仅因 CPU 测试通过就跳过真实 Krita 门合入/部署**。
- C：无作者创意或艺术批准。#49 原 A+B 关闭门、#50 的作者责任及 #55 全流程要求保持，不关闭任何 Issue。

## 兼容性与入口

| 方法/端点 | 合同 |
|---|---|
| `GET /write/v1/target` | 单次 GUI bridge 回调取得活动文档身份、结构树、服务会话和资源上限；不写画布 |
| `POST /write/v1/group` | 必须携带 `name, operation_id, expected_target, expected_bindings`；可选 `parent_id, above` |
| `POST /write/v1/add` | 上述字段及 `png_path, source_sha256`；原 `mode/fit_canvas/offset_x/offset_y/parent_id/above` 仍适用 |
| `POST /write/v1/receipt` | 只读查询，必须提供 `operation_id, service_id`；不重发创建 |

`stage setup/import-diagnostic` 只用新端点。旧插件会拒绝未知路径，客户端停止；**绝不向旧 `/layer/group`、`/layer/add` 回退**。仅把额外字段附到旧路径不安全，因为旧服务会静默忽略未知字段。

旧 `/layer/group`、`/layer/add` 与普通 `cwb group/add`、生成后的 `--add` 保留旧合同，**没有本项的目标条件或收据保护**。本项不宣称已保护全部写入口，也不实现 #17 条件源像素/选区回填、overlay/replacement 或 #23 真实预览恢复。

旧 schema1/2 sidecar 仍可读。新一次 snapshot 的 session 增加 `service_id/document_handle`；与旧弱会话不相等时，已有组必须通过持久随机标记重新解析。旧 pending 仍然阻断，不自动迁移成可重试请求。用户删除标记且跨会话时无法重认，必须人工处理；不能靠同名接管。

## 同回调目标合同

`expected_target` 包含：`protocol=cwb-guarded-write-v1`、`service_id`、`document_handle`、明确的已保存 `.kra` 绝对路径、root UUID、画布 width/height。协议不是鉴权凭据；网络仍仅 `127.0.0.1`，不改变权限或安全设置。

`document_handle` 在服务内保存 `activeDocument()` 的原生 Document wrapper，后续与 `activeDocument()/documents()` 用 **libkis 原生 `__eq__`** 比较，必须是严格 bool，不接受 NotImplemented、异常或 Python object identity 替代。先要求活动对象非空、root 有效且在打开列表恰有一份，路径与 root UUID 也必须唯一。保留 wrapper 不以文档名称或文件/root UUID 单独证明当前文档实例。

源码依据固定为 KDE/krita commit `0ddbd236974da74a59f85d799e22915eb208e1c5`：

- [Document.cpp](https://github.com/KDE/krita/blob/0ddbd236974da74a59f85d799e22915eb208e1c5/libs/libkis/Document.cpp)：Private 持有 `QPointer<KisDocument>`，operator== 比较底层指针；销毁原文档会使保留句柄失效。
- [Document.sip](https://github.com/KDE/krita/blob/0ddbd236974da74a59f85d799e22915eb208e1c5/plugins/extensions/pykrita/sip/krita/Document.sip)：Python 绑定暴露相等运算符。
- [Krita.cpp](https://github.com/KDE/krita/blob/0ddbd236974da74a59f85d799e22915eb208e1c5/libs/libkis/Krita.cpp)：activeDocument/documents 返回非 owning 的 Document wrapper。

这解决的是 notifier 停用期间关闭重开、root UUID 复用仍被旧 token 接受的代码级缺口：新合同不依赖 notifier 是否连续活跃，不复用 #13 token。实际安装版本的 SIP/QPointer 行为仍须 B 验证；官方源码不等于目标机实测。

同一个主线程桥回调内先保留收据，再核目标与全部 `expected_bindings=[{id,token}]`，再调用绑定该 Document 的原 ops。在 create、挂树、写像素前再次核对目标及绑定。组缺失、非组/根、重复标记、标记与 ID 冲突拒绝；同一可靠会话内无标记的已知 ID 可保留作者改名/移动，后续脚本不恢复名称/顺序/父级。新服务没有更改原桥、没有开启事件循环或新增跨线程 Krita 调用。

这是追加层的条件执行，**没有跨 Krita 内部 API 的事务/回滚或文档锁保证**。原生 API 中途失败、重新进入事件处理或目标变化可能已经留下 detached/attached 新对象；记为 partial_or_unknown，不删作者对象也不删除本次半成品掩盖失败。

## 收据、重试与未知

同一个服务会话内，一个规范 UUID `operation_id` 绑定一次完整规范 JSON 请求的 SHA256（含目标、绑定、参数和来源摘要）：

- 首次执行在首个可能有副作用的 API 调用前登记；记录 create 返回的实际对象 ID，再记录挂树核验情况。
- 相同 ID、相同请求：返回历史收据，不调用创建。即使对象被作者后来删除，也不会重新建回。
- 相同 ID、不同内容/目标/来源摘要/操作类型：拒绝。不能更改旧 ID 的目标以“续跑”。
- `succeeded`：这次回调已完成并返回原 ops 的结构结果；这是历史执行收据，不证明对象目前仍存在、未被用户改动或艺术已接受。
- `rejected`：本服务这次受保护操作在首个可能写入 API 之前拒绝。sidecar 仍保留 pending，首版不自动续跑。
- `running`：正在执行，结果未知，不是“尚未写入”。相同请求重入只返回此收据。
- `partial_or_unknown`：可能已经创建、挂树或写了像素；`created_objects` 保留已知 ID，`attached=unknown` 不能解释为未挂树。失败记录不保证已知所有原生副作用。
- 查询不存在的 ID 为 `unknown/not_recorded`；服务重启后查询旧 `service_id` 为 `unknown/service_changed`。两者均不能解释为未执行。

桥超时/取消有两种不同情况：排队尚未开始时当前桥会跳过回调；已开始的回调可能在 HTTP 503/客户端中断后继续写入。本项不宣称任意时点可抢占或原子取消，不自动重发。调用方应查询原 ID；即使已查得 succeeded，也要把画布对象/sidecar 对齐后才能人工决定恢复。

服务收据只在进程内保存；不在 KRA 加注、不自动保存作品、不写外部数据库。旧 `expected_target` 在新服务会话拒绝，防止重启后再次执行旧请求。不能自动换新 ID、丢弃 pending 或重启服务以绕开未知结果。

## 来源、资源与索引

受保护 add 要求 `source_sha256`。`placement.prepare_png` 在现有 32MiB 普通文件/PNG 准入下，计算**将被校验、清理并交给 Qt 解码的同一份原始字节快照**的摘要。摘要不符在 Qt 解码与创建之前拒绝；不是 hash 路径后让 Qt 再打开路径。返回 placement 记录实际 source_sha256。读取快照之后路径变化不会替换本次解码字节；不保证路径在操作之后仍是同一文件。保留既有 BGRA 边界转换和 placement 预算。

客户端来源摘要也为有界分块普通文件读取，拒绝超过 32MiB、FIFO 和读取期间变化；它仅提出意图，最终服务端摘要才绑定实际输入。

上限：新 POST JSON 1..64KiB；128 个绑定；树最多4096节点、64层深、1MiB JSON；名称1024字节；服务最多128个文档句柄、4096条收据、每条收据16KiB。收据只保留摘要/状态/有限错误与对象结果，不永久保留源PNG、解码像素或完整树。句柄/收据预算满时拒绝新增，不驱逐旧记录；旧 ID 查询仍可用。这不是 RSS 硬上限或全局硬实时保证。

预算耗尽需先保存外部审计结果并人工核对全部未决操作，再规划下一服务会话；本工具不自动重启、清空、重新编号或重放。source/body/tree 拒绝均不靠提升内存/超时解决。

`stage` 发 POST 前持久保存完整 pending 请求，收到结果后保存收据和结构回读。请求/响应/索引失败时只查询原 ID 并记录已知树；pending 阻断下一次自动执行。前 N−1 个成功组保留，失败的第 N 个不盲重建。索引最后写失败时，磁盘上可能只剩发送前 pending，不能依据“无 response”判断未执行。状态与艺术验收保持分离。

## 真实 B 接力清单（此轮未执行）

只在另行授权的专用真实测试文档与环境运行，固定候选 SHA/tree、Krita/Python/PyQt 版本及输入摘要：

1. 从实际 HTTP/GUI bridge 取得目标；fresh wrapper 相等，同路径/root 的关闭重开（含 notifier 停用窗口）旧句柄拒绝。
2. 捕获目标后切换活动文档；受保护 group/add 均不得写入另一文档。尺寸/路径变化、重复打开路径、丢失/多义组标记分别拒绝。
3. 不同源测试图逐个导入，验证 BGRA/RGBA/透明度/落点、独立层移动/隐藏及父组结构；真实短注/拆合/选线仍需 PR03 后续实现。
4. 请求相同 ID 重复发送与不同内容拒绝；排队超时、执行中超时、attach/pixel/refresh 故障、响应丢失和索引失败保留实际对象/收据，不重建成功对象。
5. 保存、关闭、重开 KRA 后按持久标记重定位；移除标记的歧义诚实停止。重启服务后旧 ID 结果未知必须人工对照保存的收据/对象。
6. 独立复审、最终固定 head 完整回归和正常 PR 流程另行完成；本文件及替身测试均不勾选 #49 A+B 全门。
