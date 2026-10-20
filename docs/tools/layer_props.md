# 确定性图层属性

## 状态与边界

本项提供本地 HTTP → 主线程桥 → Krita 属性 API 和 CLI；离线替身测试不代表真实 Krita 6/Python 3.13/PyQt6 或画布效果通过验收。**normal/multiply/overlay/screen 是代码采用的 Krita 稳定 ID，尚未完成本机混合模式或 Alpha Lock 视觉验证。** 不扩大到滤镜层/蒙版体系，不安装插件、不重启、不修改现有作品、不调用模型。

## HTTP 契约

`POST /layer/props` 的 JSON 对象：

```json
{"id":"<会话内图层UUID>","alpha_locked":true,"opacity":0.5,"blending_mode":"multiply"}
```

- `id` 必填；三个属性至少指定一个，省略的属性不变；未知字段拒绝，防止拼写错误静默成功。`null` 不是省略。
- `alpha_locked`：严格 JSON bool，仅 paintLayer 且具备 Alpha Lock API 才可写；组及其他节点明确 400。
- `opacity`：严格数值 0..1，不接受 bool、字符串、null、数组、对象、NaN、Infinity 或越界。按 `floor(opacity * 255 + 0.5)` 四舍五入（非 bankers rounding）得到 Krita 整数 0..255。0 → 0；0.5 → 128；1 → 255。由于量化，50% 对应 128/255，归一化返回 `0.502`，不是假装精确 0.5。
- `blending_mode`：仅稳定 ID `normal`（普通）、`multiply`（正片叠底）、`overlay`（叠加）、`screen`（滤色）。不接受 GUI 翻译名、任意字符串或别名；每次 setter 后读取 `blendingMode()`，不匹配即失败。
- opacity/blending_mode 写入仅允许非 root 的 paintLayer/groupLayer/vectorLayer，且所需 getter/setter 可调用；其他节点明确失败。保留 RGBA/U8 守卫、root 只读、过期 ID → 404。
- 成功 200 返回 `id` 和本次请求的属性实际回读值。opacity 与`/layers` 一致，除以 255 后保留三位小数。

### 失败与非事务语义

所有输入、节点类型和请求属性的 getter/setter 能力与预读在首次 setter 前完成。参数/能力失败 → 400，无本次属性写入；找不到节点 → 404。写入固定顺序 alpha_locked → opacity → blending_mode，各项立即回读；setter 返回 `None` 可接受（Krita void 方法），显式 False、异常或回读不符则失败并停止后续属性。

**不是原子事务，也不尝试回滚。** setter 即使抛异常也可能已经改动节点。写阶段失败 HTTP 400 示例：

```json
{"error":"属性写入失败;可能已部分写入,未回滚,请重新读取 /layers: ...","id":"...","failed":"blending_mode","attempted":["alpha_locked","opacity","blending_mode"],"verified":["alpha_locked","opacity"],"rolled_back":false}
```

`attempted` 包括可能已修改后才抛异常的 setter；`verified` 仅表示当时回读匹配，不承诺画布最终效果或没有后续副作用。投影刷新失败时 `failed` 为 `refreshProjection`，属性也可能全部已写。桥超时仍为 503，**不保证主线程任务取消/未写入**。发生上述失败先回读 `/layers`，检查 GUI，勿盲目重试。`/layers` 对可选 getter 故障仍返回树和必需字段，但该属性会是 `null` 并附 `property_status` 错误；这表示**当前无法核实**，不表示写入未生效或已恢复到旧值。列表恢复不等于写入成功，不能据此重试 setter 或将失败请求改判为成功；需结合 GUI 或 getter 恢复后的再次查询确认。必需字段、文档守卫和主线程桥错误仍可能阻止查询，本契约不承诺任意故障下都能恢复。

## GET /layers 属性

每个节点的 opacity 使用 0..1 约定，并返回以下属性：

- `blending_mode`：实际 `blendingMode()` ID；读取时保留未知既有模式，不擅自映射为 normal。API 缺失或 getter 异常为 JSON null。
- `alpha_lock_supported`：本接口的节点/API 能力（paintLayer 且 getter/setter 可调用），不是本次 getter 成功保证。可调用 getter 抛错时仍为 true；已知节点不适用或 API 缺失为 false；能力探测本身抛错、无法判断时为 null。
- `alpha_locked`：支持节点的实际 bool；不支持、API 缺失或读取异常均为 JSON null，不能误读为“已解锁”。root 仍然只读，即使列表返回其属性。
- `property_status`：每个节点分别包含 `alpha_locked`、`blending_mode` 的状态对象：`{"status":"ok"}` 表示读取成功；`unsupported` 表示 Alpha Lock 节点类型不适用；`unavailable` 表示所需 API 缺失/不可调用，附 `error` 列出方法名；`error` 表示读取或能力探测异常，附 `error`（异常类型与消息）。不把 getter 内抛出的 AttributeError 伪装成 API 缺失。

两项独立读取：单项或两项 getter 失败都不影响必需字段、健康属性、兄弟/父子节点或树顺序，HTTP 仍为 200。这是**仅列表可选属性**的降级契约；必需字段（包括 opacity）与树遍历错误不吞掉，仍按既有错误路径失败。`/layer/props` 不使用此容错读取，写前预读异常仍 400 且不写，写后回读异常仍 400 并保留 `attempted`/`verified`/`rolled_back:false`。

例如 paintLayer 的 Alpha Lock getter 异常而混合模式正常时（其他必需字段省略）：

```json
{"alpha_locked":null,"alpha_lock_supported":true,"blending_mode":"normal","property_status":{"alpha_locked":{"status":"error","error":"RuntimeError: read unavailable"},"blending_mode":{"status":"ok"}}}
```

## CLI 示例

仅在已授权专用测试文档、已安装相应版本插件的前提下运行写操作：

```bash
python3 core/cwb.py props --help
python3 core/cwb.py layers
python3 core/cwb.py props '<剪影层ID>' --alpha-locked true
python3 core/cwb.py props '<明暗层ID>' --opacity 0.5 --blending-mode multiply
python3 core/cwb.py props '<明暗层ID>' --alpha-locked false --opacity 1 --blending-mode normal
```

CLI bool 仅 `true`/`false`；有限区间和模式在发 HTTP 前检查；省略属性不发送。顶层/子命令帮助不访问服务、不创建缓存目录。

## 证据与待验

离线回归：[专属属性测试](../../tests/test_layer_props.py) 验证数值边界、非法类型、全部稳定 ID 的参数/回读契约、root/节点类型/色彩守卫、全部预检先于写入、静默 setter/异常/部分写入、列表属性字段、路由主线程桥调用、错误状态与 CLI 无副作用帮助。另有永久错误注入回归覆盖两项 getter 单独/联合抛错、无关节点、完整健康树保留、缺 API/不适用节点/能力未知的区分、必需字段错误不降级，以及 setter 已生效而回读失败后通过真实 Handler 的 GET `/layers` 恢复查询（无 socket），并确认重试仍严格预读失败。既有图层组测试继续执行。它们使用替身，不证明真实主线程调度、Qt 像素或 Krita 方法行为。

**真实 Krita 待验项（使用已授权专用测试文档）：**

1. 在专用测试文档准备有明显剪影边界的 paintLayer 和明暗 paintLayer，记录其 ID；对比 GUI 属性面板和 `/layers`。
2. 剪影层设置 Alpha Lock true，在透明边界外手绘，确认不增加不透明像素；false 后比较；组上请求 Alpha Lock 应明确失败且其他属性不写入。
3. 明暗层分别设置 opacity 0、0.5、1，检查面板 0/约50/100% 和画布半透明合成，回读 0/0.502/1。
4. 在有底色的测试图层逐一设置 normal/multiply/overlay/screen，记录实际回读 ID、面板名称和画布效果，确认叠加/滤色确实工作。组的混合/穿透行为也需独立确认，不能由 paintLayer 替代。
5. 验证 HTTP 桥实际主线程执行和超时说明；有必要时在专用文件手动保存/重开检查，不覆盖用户作品。

在这些证据齐全前，本机混合模式与最终画布/面板验收仍未完成。
