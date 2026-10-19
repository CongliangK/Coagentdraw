# CAD-13：整画布投影与选区快照（离线交付）

## 状态和边界

本次仅交付 API、CLI、标准库 PNG 编码及离线回归；**没有安装/重启 Krita，没有在线读取或写入用户作品，没有执行模型**。测试中的 Document / Selection / Notifier 都是明确标注的 Python 替身。PNG 编码、CRC、像素数值、文件发布与 CLI/路由测试是真实本地执行，但不能据此声称 Krita 6 的通道顺序、投影刷新、选区原生布局或文档关闭信号已经实机通过。

独立实现：[snapshot.py](<../../core/krita_cowork/snapshot.py>)；薄入口：[ops.py](<../../core/krita_cowork/ops.py>)、[server.py](<../../core/krita_cowork/server.py>)、[cwb.py](<../../core/cwb.py>)；专属回归：[test_snapshot.py](<../../tests/test_snapshot.py>)。没有修改主线程桥、监听边界或旧接口；不扩展 #17、不实现 AI 回填。

## API 与 CLI

全部为 `POST` JSON，仍只由现有 `127.0.0.1:51080` 控制面提供。路径指 **Krita 所在机器**，父目录须已存在；CLI 将路径展开为绝对路径。不自动创建输出目录。

| 路由 | 必填 | 可选 |
|---|---|---|
| `/doc/export` | `path` | `overwrite`、`expected_document_token` |
| `/doc/selection_png` | `path` | `selection_mode`、`overwrite`、`expected_document_token` |
| `/doc/snapshot` | `projection_path`、`selection_path` | `selection_mode`、`overwrite`、`expected_document_token` |

`overwrite` 默认 `false`，必须为 JSON 布尔；`selection_mode` 默认 `selection`，另一个允许值为 `full_canvas`。不存在选区、选区在画布内全零均明确失败，不会静默变成全白。显式 `full_canvas` 会忽略当前选区并生成全 255 覆盖。

```bash
python3 core/cwb.py export /tmp/projection.png
python3 core/cwb.py selection-png /tmp/selection.png
python3 core/cwb.py snapshot /tmp/projection.png /tmp/selection.png
python3 core/cwb.py snapshot /tmp/projection.png /tmp/selection.png --full-canvas
python3 core/cwb.py snapshot /tmp/projection.png /tmp/selection.png --overwrite --expect-document-token doc-...
```

配对请求示例：

```json
{
  "projection_path": "/tmp/projection.png",
  "selection_path": "/tmp/selection.png",
  "selection_mode": "selection",
  "overwrite": false
}
```

### 返回契约

三个入口返回统一元数据，只有实际请求的 `projection` / `selection` 子对象存在：

- `capture_id`：每次采样随机唯一；**不是**文档身份。
- `document_token`：当前服务会话的保守文档 token，见下一节；不是内容版本号。
- `token_contract = "session/notifier-epoch/root-uuid-v1"`、`token_lifecycle_runtime_verified = false`：本交付尚未证明实机生命周期信号，不得忽略该验收状态。
- `paired`：仅配对端点完整成功时为 `true`；单独请求为 `false`。
- `capture_scope`：配对为 `one-main-thread-callback`，单独为 `single-export-only`。
- `width`、`height`、`origin = {"x": 0, "y": 0}`：文档整画布，坐标不随选区或节点 bounds 移动。
- `sampled_at`：投影刷新完成后、第一次像素读取前的 UTC 时间（含微秒和时区）；`sample_started_monotonic_ns` / `sample_finished_monotonic_ns` 为读像素窗口，不包含 PNG 编码与磁盘写入。
- 每个图像的 `pixel_sha256`、`pixel_bytes`：PNG 解码后规范像素数组的 SHA-256 和长度（投影 RGBA8、蒙版 Gray8）；`path`、`file_bytes`、`file_sha256`：已发布 PNG 的文件信息。
- `projection` 另有 `source = Document.pixelData`、`source_layout = BGRA8`、`source_sha256`（交换前原始数据）、`pixel_layout = RGBA8`、`alpha`、`color_profile`。
- `selection` 另有 `source`、`mode`、`pixel_layout = Gray8`、`alpha = none`、`coverage`；`native_bounds` 保留原生选区边界（可能越出画布），`bounds` 是完整画布单通道数据中所有非零覆盖像素的紧致包围盒。两者均为 `x/y/width/height`，不用于裁剪。显式全画布模式 `native_bounds = null`。
- `resource_budget`：`document-export-v1` 准入策略、总像素、持有的规范像素字节、各 PNG 输出上限、同时持有缓冲区估计及限额。它是本次请求的资源计划，不是实测 RSS 或运行时验收证书，见下节。

**不能**把两个单独请求的相同 token、尺寸、文件名、甚至时间戳当成同一快照。只有配对入口会在 **一次**现有 `MainThreadBridge.call` 的回调内获取两份像素，然后统一编码/发布；不会发出两个桥调用，也不调用 `processEvents()`。同一回调不是 Krita 内部数据库事务；当前未声称拥有对所有 Krita 后台作业的排他锁。

## 像素、色彩与只读约束

1. 只接受根节点 `RGBA/U8`，并明确收窄到 `Document.colorProfile() == sRGB-elle-V2-srgbtrc.icc`。CMYK、U16/F16 等和其他 ICC（包括线性 sRGB / 广色域）均拒绝，不静默转换或丢弃色彩解释。后续扩大白名单需要独立数值证据。
2. 投影调用 `Document.pixelData(0, 0, width, height)`，不读取任何节点的 `bounds()` / `pixelData()`，不复用旧的 `/layer/png`。要求长度严格为 `width*height*4`。
3. `Document.pixelData` 返回 **BGRA 字节序**（Krita 原生 ARGB 内存布局），投影编码前交换 B/R。2026-10 实机确认（Krita 6.0.4 AppImage）：把 RGBA 字节直写 `setPixelData` 会在画布呈现红蓝互换；同期一次依据对称黑白样本作出的"RGBA 直通"判断是误读——**字节序验证必须使用非对称彩色样本并对照画布外观**。PNG 为 8-bit straight RGBA（color type 6），保留半透明 Alpha 和透明像素的 RGB；不自行预乘/反预乘。PNG **不带 sRGB/gAMA/iCCP 色彩元数据 chunk**：像素已在上游收窄为 sRGB profile，未标记 PNG 按惯例即按 sRGB 解读；且历史离线对照记录显示，部分图像消费者遇到显式 sRGB chunk 会把整帧误渲染为黑帧（2026-10；不代表本次 Krita 实机验收），故不再写入。
4. 选区只用 `Selection.pixelData(0, 0, width, height)`，严格检查长度 `width*height`，不通过 QColor、QImage RGBA 或 bounds 重构蒙版；0、128、255 和所有羽化值原样保存。
5. 蒙版 PNG 是 8-bit **单通道灰度**（color type 0），**没有 Alpha，也不含 tRNS/gamma/色彩配置**。`M=0` 未选，`M=255` 全选，`M=128` 为 128/255 覆盖，不反转、不阈值化、不做 gamma。渲染器将灰度图显示为不透明属于预期。未来消费者必须按灰度通道读取；不能直接假定 ComfyUI `LoadImage.MASK`（通常由 Alpha 得到）就是这份蒙版。
6. 采样顺序：`waitForDone()` → `refreshProjection()` → `waitForDone()` → 文档/选区读取。仅刷新投影缓存，没有文档/图层/选区 setter、合并、保存、undo 操作。刷新是否充分、是否保持实际 modified/undo 状态，必须用真实 Krita 验证，不能仅凭本测试替身计数声称作品未变。
7. 采样前后校验 token/epoch 与文档尺寸；变化则丢弃数据、不发布文件。不实现内容锁，token 也不能证明用户没有在两次请求之间编辑。
8. 输入/输出路径在 OS 调用层一律用显式 UTF-8 字节：Krita AppImage 内置 Python 的文件系统编码可能非 UTF-8（2026-10 实测：str 路径含任何非 ASCII 字节即 ENOENT，文件实际存在）；字节路径完全绕过该编码器。路径校验语义（.png 后缀/符号链接拒绝/存在性/同文件判定）不变。

PNG 编码全部使用 Python 标准库，按行无损编码，无 Qt 的 `QImage.save()` 成败歧义。固定输入像素得到相同解码数值与像素摘要；不同 Python/zlib 版本不承诺 PNG 压缩字节完全一致，应分别使用文件摘要与像素摘要。

## 导出资源准入与有界编码（#13 资源子任务）

三个文档导出入口使用相同固定策略，在 `waitForDone`、`refreshProjection`、`Document.pixelData`、`Selection.pixelData`、全白蒙版分配与 PNG 发布之前核准：

- 单轴仍为 `1..32768`；总像素最多 **16,777,216（16 Mi pixels）**，每份原始像素缓冲区最多 **64 MiB**。
- 同时持有的已知缓冲区估计最多 **256 MiB**。分别计入 BGRA / 交换用 bytearray / 返回 RGBA 的峰值，及规范投影 + 蒙版 + 已完成 PNG + 当前编码增长缓冲区及最终 bytes。当前 PNG 按输出上限的 2.5 倍估计，另留 4 MiB 块拷贝、zlib / Python 开销余量；完成投影摘要后立即释放局部 BGRA 引用。这里的“释放”不承诺底层分配器归还 RSS。
- 同一模块实例内最多执行一个文档导出，包括不同 `SnapshotService` 实例或重入调用。后续请求立即报错，不等待、不分配第二份捕获缓冲区；失败也释放守卫。它不锁定文档编辑，也不管理其他进程、模块副本或 `/layer/png` 的内存。
- 失败时先清理异常 traceback 中属于 snapshot 模块的已结束同步帧的局部引用，再释放导出守卫；包含本模块编码/发布深层帧、cause/context 链与 ExceptionGroup 内的本模块帧。这样桥接层即使暂时保留异常/结果盒循环，也不会继续持有本次捕获和 PNG 大缓冲。仍保留异常类型、消息、部分发布路径、异常链和 traceback 代码位置；仅这些已结束同步帧的调试局部变量会被清除；外部帧不清理，generator / coroutine / async generator 帧即使属于本模块也不清理，正在执行的同步帧拒绝清理。不会为了释放异常引用而关闭仍可继续的外部 generator 或执行其 finally。不主动触发全局 GC、不吞异常、不改桥的超时/重试语义。
- 异常图通过 `BaseException` / `BaseExceptionGroup` 基类 descriptor 读取，不执行异常子类可覆写的 `__traceback__`、`__cause__`、`__context__`、`exceptions` 或 `__class__` getter。先登记链与分组子节点，再清理各帧；逐帧按模块 globals 身份与代码标志筛选，单帧清理拒绝或失败时继续其他帧/节点，不因一个坏 getter/清理步骤放弃整个组。清理属于 best effort，意外清理故障不能替换原业务异常；运行时本身内存耗尽等严重故障不保证所有清理成功。不会删除异常对象自带的任意业务属性或外部所有者的 payload。

上限选择与 CAD-14 的 16 Mi pixels / 64 MiB 源像素准入一致，同时给配对捕获和不可压缩 PNG 留出空间。1328²、4096²、8192×2048 均可准入；例如 4096² 配对估计为 **257,704,447 字节**，低于 268,435,456 字节限额。该数值是公式估计，**不是实际内存测量**。合法逐轴尺寸不再代表必定可导出：8192²、32768²，以及 4961×3508 等超过总像素限额的画布会明确拒绝。接口不会自动缩小、裁剪或修改原文档，也不增加请求参数绕过限额。较大画布支持需要另行调整预算和验证，不能静默放开。

`_pixels` 对原生返回值先用 buffer 协议读取真实 `nbytes`，长度正确才复制；不执行任意可迭代对象或自定义 `__bytes__` 来猜测长度。支持 bytes、bytearray 和可读 memoryview；真实 Krita/PyQt6 的 QByteArray buffer 行为仍需目标环境回归。原生 API 已经开始执行后的内部大分配不可由此抢占，异常长度的返回值只能在返回后、Python 复制前拒绝。

PNG 编码不再拼接整张带 filter 字节的 scanlines：每次送入 zlib 的块及每个 IDAT payload 最多 **64 KiB**，保持逐行 filter=0、RGBA8/Gray8 和全部像素值不变。编码文件上限为 `filtered_bytes + floor(filtered_bytes / 100) + 65536`，其中 `filtered_bytes = (width * channels + 1) * height`；这是含 1% 膨胀/chunk 余量的策略上限，不宣称为任意 zlib 版本的数学压缩上界。每次追加 chunk 前检查，超限则失败且不发布；两份 PNG 都完成后才暂存/发布。

公共 `swap_rb` / `_pixels` / `encode_png` 也被图层导入导出复用，因此 **文档导出的固定总像素/字节/并发限额没有套到这些公共函数或其他 API**；编码器共享有界块策略，原通道/逐轴尺寸合同保留。相交范围内，多 IDAT PNG 通过既有 CAD-14 `prepare_png` 准入并保持解码像素。可导出不代表可原尺寸重新导入：CAD-14 仍限制 PNG 文件 32 MiB、输出采样 4 Mi pixels 等，高熵 4096² PNG 可以超过其文件限额。此切片不扩大 CAD-14 或其他入口的预算。

该策略只限制本模块可核算的捕获/编码工作，不限制文档存储、Krita/Qt API 内部资源、Python 解释器/分配器或整个进程 RSS，也不是硬实时或取消协议。桥超时、Krita 原生调用和文件发布的既有边界保持不变。离线测试含实际 export 入口的读取前 trap、微型限额正负/等值边界、并发/重入与失败释放、宽行/高熵多 IDAT、全部 Alpha 值/透明隐藏 RGB，以及旧 `/layer/png` 和导入准入兼容。失败寿命测试原样执行实际 `_Runner._on_run` 的 AST 方法体（免 QObject/Qt），覆盖默认/停用 GC 时连续原生数据校验、第二编码、zlib、fsync 与第一/第二发布失败后的大对象释放；还验证保留异常链/分组/代码位置，释放本模块捕获大缓冲，同时保留外部帧 payload 与暂停执行的 continuation。外部 Python 故障注入器如果自行持有传入缓冲，该外部引用属于调用方所有，不承诺代为删除。这些都是 CPU / Python 替身证据，不能代替下方实机清单。

## 会话文档 token：有限契约与未证明项

**不使用**文件名、尺寸、Python `id(doc)` 或 wrapper `is`。每次从原生 `rootNode().uniqueId().toString()` 读取非空 UUID，检查打开文档列表中该 UUID 唯一；重复 UUID（例如同源文档同时打开）拒绝，宁可不可用也不错误绑定。

第一次导出在主线程注册 `Notifier.imageClosed`、`imageCreated`，并启用 notifier。任意一个创建/关闭事件会清空 **全部文档** token、轮换随机 epoch；因此即使 `.kra` 重开保留 root UUID，正常收到生命周期信号后也不能复用旧 token。副作用是关闭/新建不相关文档也会使当前 token 失效，调用方需重新捕获。服务会话重建同样重新发 token。文件改名、尺寸改变不会充当身份依据。

`expected_document_token` 是采样前的同文档会话前提，不匹配时 400，且不写 PNG。不是跨请求的像素一致性或作品未编辑证明。

### 证据与限制

- 当前可证明：源代码没有 wrapper/文件名身份替代；离线测试模拟关闭、同 UUID 重开、创建其他文档、不同 wrapper、重复 UUID、服务会话更换、notifier 缺失/停用时，按上述规则失效/拒绝。
- 当前 **不能证明**：目标 Krita 6 上所有 GUI 关闭/重开路径确实投递这些信号；打开 `.kra` 的 root UUID 行为；Qt 信号线程/生命周期；投影读取期间的后台任务可见性。没有伪造实机证据或从替身推导这些保证。
- 缺少必需生命周期 API、采样时发现 notifier 停用、缺失/冲突 UUID 时 fail closed。其他插件在两次检查之间停用并重新启用 notifier、从而丢失关闭事件，目前无法识别；若关闭/重开同 UUID 且信号未投递，则本方案无法区分，应视作尚未满足强身份门槛。**不能把现在的 token 作为 #17 安全回填凭证。**
- 在真实生命周期门槛通过前保留 `token_lifecycle_runtime_verified = false`；不是可由请求者改成 true 的参数。后续 #17 还需同时验证尺寸/原点、投影及选区的规范像素摘要、明确用户接受与冲突处理；本任务未实现回填，也没有给出内容未改的保证。

## 输出覆盖、失败与超时

- 默认禁止覆盖，拒绝符号链接、两个路径相同或已有硬链接别名、缺失父目录、非 PNG 扩展名。所有路径在读取像素前校验。
- 先获取/校验两份像素、编码两份 PNG，再在各目标同目录暂存并 `fsync` **全部**文件；任何编码/暂存失败均不发布输出。临时文件尽力清理。
- 无覆盖模式以同文件系统硬链接原子发布，不支持该能力的文件系统明确失败；发布前出现同名文件也不会覆盖它。
- `overwrite=true` 用单文件 `os.replace`；它不允许两个文件成为一个原子事务。**第二个发布失败时第一个可能已经发布/覆盖，不回滚、不删除调用者文件。** 报错包含 `published_paths=[...]`，HTTP 非 200，绝不返回配对成功。覆盖原文件不可恢复，应优先使用全新输出路径。
- 崩溃、磁盘故障和 HTTP 超时可能留下一个或两个输出及临时文件。没有磁盘多文件事务或目录级崩溃耐久保证；返回结果后其他进程也可修改 PNG。
- 保留现有主线程桥 10 秒超时。若回调已开始，超时不能中断 Krita 作业或正在进行的输出；503/客户端中断不代表没有文件。CLI 120 秒客户端 timeout 不会扩大桥的 10 秒。检查输出再决定是否以新路径重试，勿盲目覆盖。

## 验收映射和实机待验清单

| 要求 | 本次离线证据 | 实机状态 |
|---|---|---|
| API/CLI、主线程一次配对、127.0.0.1 | 路由/bridge 调用数、必填字段、CLI payload/帮助零网络零缓存测试 | 未启动/安装插件 |
| Document 整画布，不用节点 bounds | 替身 root 没有 bounds/pixelData，断言文档读取 (0,0,w,h) | Krita 可见合成与隐藏/组层需核对 |
| BGRA→RGBA 交换、半透明、透明 RGB | 真 PNG 解码 CRC/type 与精确通道测试 | 字节序 2026-10 实机验证为 BGRA（彩色样本+画布对照）；其他 Krita 版本一致性待回归 |
| 1328²、矩形/羽化、画布边缘 | 1328² 全尺寸 PNG，右下角矩形覆盖 1/128/255；另覆盖所有 0..255 数值和边界 | Krita 建立同尺寸选区后逐点核对待验 |
| 无选区、全零、显式全画布 | 拒绝前两者且不发布；显式全白并标模式 | 真 Selection 空值语义待验 |
| 原生完整单通道、边界/长度 | 完整画布、越界 native_bounds、短长数据拒绝 | Krita Selection API 待验 |
| 同会话身份/重开旧 token 失效 | 不同 wrapper、同 UUID 重开/关闭/创建信号、重复 UUID、notifier 停用测试 | 生命周期强门槛未通过，见上节 |
| 投影刷新、只读 | 等待/刷新调用顺序、无 setter；采样期间变更拒绝 | modified/undo 与刷新充分性待验 |
| 编码失败/输出覆盖/部分文件失败 | 编码失败、第二暂存失败、第二发布失败、显式覆盖失败、并发同名保护 | 不代表断电/任意文件系统故障实测 |
| 读取前总资源准入、有界编码 | `test_snapshot_budget.py` 覆盖实际入口超限 trap、三类预算边界、多 IDAT 精确解码、同时导出拒绝及失败释放 | 原生 buffer / Qt / Krita 分配与耗时未验；非 RSS 或取消保证 |

以后获准实机测试时，使用专用测试文档，不碰用户作品：

1. 1328×1328、RGBA/U8、上述 sRGB profile；在不同组、隐藏层和透明背景上放红/绿/蓝与半透明色块，记录 modified/undo 状态。
2. 建立右下角触边矩形与羽化选区；使用原生 Selection 数据记录 M=0/128/255 和中间羽化值的位置，保存原生边界，然后配对导出。
3. 独立解码两个 PNG，核对尺寸/原点、投影与 GUI 合成、RGBA 四通道（特别 Alpha=128）、蒙版所有覆盖值与边界；验证刷新后像素已更新且作品 modified/undo 不变。
4. 取消选区：默认失败；显式 full_canvas：全 255。测试不支持 profile/U16 拒绝。
5. 记录 token，关闭再打开同一专用 `.kra`，旧 token 请求必须失败；测试标签页关闭、最后视图关闭、未保存文档关闭、重复打开同源文档。记录实际 notifier 事件、root UUID 与 token，不仅比较文件名。
6. 在完成以上真实证据审阅前，不将本说明改成“实机通过”，不把 #13/#17 的完整用户画布验收写为完成。
7. 在同一专用文档验证 QByteArray buffer 长度检查、受支持的大画布和超限读取前拒绝、真实 PNG 消费者对多 IDAT 的解码；分别记录 Python 与 Qt/Krita 资源/耗时。不能把离线缓冲区估计写成整个进程峰值上限。

## 离线运行

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 -B core/cwb.py --help
python3 -B core/cwb.py snapshot --help
bash -n core/install.sh
git diff --check
```

另按 [AGENTS.md](../../AGENTS.md) 的离线验证要求解析全部 core 与插件 Python 的 AST（包含新增模块），并解析及运行专属测试。历史本地日志未随仓库交付；可复现检查以上述命令和仓库测试为准，具体运行结果以对应版本的审查记录为准。
