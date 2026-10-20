# 整画布投影与选区快照（离线交付）

## 状态和边界

当前提供 API、CLI、标准库 PNG 编码与离线回归；真实 Krita 的完整快照验收仍未完成。测试中的 Document / Selection / Notifier 都是明确标注的 Python 替身。PNG 编码、CRC、像素数值、文件发布与 CLI/路由有离线回归；BGRA 字节序已有彩色样本实机记录，投影刷新、选区原生布局与文档关闭信号仍须完整实机验收。

独立实现：[snapshot.py](<../../core/krita_cowork/snapshot.py>)；薄入口：[ops.py](<../../core/krita_cowork/ops.py>)、[server.py](<../../core/krita_cowork/server.py>)、[cwb.py](<../../core/cwb.py>)；专属回归：[test_snapshot.py](<../../tests/test_snapshot.py>)。所有 Krita 调用经主线程桥执行，服务仅监听本机回环地址；不实现 AI 回填。

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

**不能**把两个单独请求的相同 token、尺寸、文件名、甚至时间戳当成同一快照。只有配对入口会在 **一次**现有 `MainThreadBridge.call` 的回调内获取两份像素，然后统一编码/发布；不会发出两个桥调用，也不调用 `processEvents()`。同一回调不是 Krita 内部数据库事务；当前未声称拥有对所有 Krita 后台作业的排他锁。

## 像素、色彩与只读约束

1. 只接受根节点 `RGBA/U8`，并明确收窄到 `Document.colorProfile() == sRGB-elle-V2-srgbtrc.icc`。CMYK、U16/F16 等和其他 ICC（包括线性 sRGB / 广色域）均拒绝，不静默转换或丢弃色彩解释。后续扩大白名单需要独立数值证据。
2. 投影调用 `Document.pixelData(0, 0, width, height)`，不读取任何节点的 `bounds()` / `pixelData()`，不调用图层像素导出 `/layer/png`。要求长度严格为 `width*height*4`。
3. `Document.pixelData` 返回 **BGRA 字节序**（Krita 原生 ARGB 内存布局），投影编码前交换 B/R。2026-10 实机确认（Krita 6.0.4 AppImage）：把 RGBA 字节直写 `setPixelData` 会在画布呈现红蓝互换。**字节序验证必须使用非对称彩色样本并对照画布外观**。PNG 为 8-bit straight RGBA（color type 6），保留半透明 Alpha 和透明像素的 RGB；不自行预乘/反预乘。PNG **不带 sRGB/gAMA/iCCP 色彩元数据 chunk**：像素已在上游收窄为 sRGB profile，未标记 PNG 按惯例即按 sRGB 解读；且实测部分智能体视觉解码管线遇到显式 sRGB chunk 会把整帧误渲染为黑帧（2026-10 离线对照确认），因此不写入。
4. 选区只用 `Selection.pixelData(0, 0, width, height)`，严格检查长度 `width*height`，不通过 QColor、QImage RGBA 或 bounds 重构蒙版；0、128、255 和所有羽化值原样保存。
5. 蒙版 PNG 是 8-bit **单通道灰度**（color type 0），**没有 Alpha，也不含 tRNS/gamma/色彩配置**。`M=0` 未选，`M=255` 全选，`M=128` 为 128/255 覆盖，不反转、不阈值化、不做 gamma。渲染器将灰度图显示为不透明属于预期。未来消费者必须按灰度通道读取；不能直接假定 ComfyUI `LoadImage.MASK`（通常由 Alpha 得到）就是这份蒙版。
6. 采样顺序：`waitForDone()` → `refreshProjection()` → `waitForDone()` → 文档/选区读取。仅刷新投影缓存，没有文档/图层/选区 setter、合并、保存、undo 操作。刷新是否充分、是否保持实际 modified/undo 状态，必须用真实 Krita 验证，不能仅凭本测试替身计数声称作品未变。
7. 采样前后校验 token/epoch 与文档尺寸；变化则丢弃数据、不发布文件。不实现内容锁，token 也不能证明用户没有在两次请求之间编辑。
8. 输入/输出路径在 OS 调用层一律用显式 UTF-8 字节：Krita AppImage 内置 Python 的文件系统编码可能非 UTF-8（2026-10 实测：str 路径含任何非 ASCII 字节即 ENOENT，文件实际存在）；字节路径完全绕过该编码器。路径校验语义（.png 后缀/符号链接拒绝/存在性/同文件判定）不变。

PNG 编码全部使用 Python 标准库，按行无损编码，无 Qt 的 `QImage.save()` 成败歧义。固定输入像素得到相同解码数值与像素摘要；不同 Python/zlib 版本不承诺 PNG 压缩字节完全一致，应分别使用文件摘要与像素摘要。

## 会话文档 token：有限契约与未证明项

**不使用**文件名、尺寸、Python `id(doc)` 或 wrapper `is`。每次从原生 `rootNode().uniqueId().toString()` 读取非空 UUID，检查打开文档列表中该 UUID 唯一；重复 UUID（例如同源文档同时打开）拒绝，宁可不可用也不错误绑定。

第一次导出在主线程注册 `Notifier.imageClosed`、`imageCreated`，并启用 notifier。任意一个创建/关闭事件会清空 **全部文档** token、轮换随机 epoch；因此即使 `.kra` 重开保留 root UUID，正常收到生命周期信号后也不能复用旧 token。副作用是关闭/新建不相关文档也会使当前 token 失效，调用方需重新捕获。服务会话重建同样重新发 token。文件改名、尺寸改变不会充当身份依据。

`expected_document_token` 是采样前的同文档会话前提，不匹配时 400，且不写 PNG。不是跨请求的像素一致性或作品未编辑证明。

### 证据与限制

- 当前可证明：源代码没有 wrapper/文件名身份替代；离线测试模拟关闭、同 UUID 重开、创建其他文档、不同 wrapper、重复 UUID、服务会话更换、notifier 缺失/停用时，按上述规则失效/拒绝。
- 当前 **不能证明**：目标 Krita 6 上所有 GUI 关闭/重开路径确实投递这些信号；打开 `.kra` 的 root UUID 行为；Qt 信号线程/生命周期；投影读取期间的后台任务可见性。没有伪造实机证据或从替身推导这些保证。
- 缺少必需生命周期 API、采样时发现 notifier 停用、缺失/冲突 UUID 时 fail closed。其他插件在两次检查之间停用并重新启用 notifier、从而丢失关闭事件，目前无法识别；若关闭/重开同 UUID 且信号未投递，则本方案无法区分，应视作尚未满足强身份门槛。**不能把现在的 token 作为安全回填凭证。**
- 在真实生命周期门槛通过前保留 `token_lifecycle_runtime_verified = false`；不是可由请求者改成 true 的参数。安全回填还需尺寸/原点、投影及选区像素摘要、明确用户接受与冲突处理的同次服务端核对；当前未实现回填，也不保证内容未改。

## 输出覆盖、失败与超时

- 默认禁止覆盖，拒绝符号链接、两个路径相同或已有硬链接别名、缺失父目录、非 PNG 扩展名。所有路径在读取像素前校验。
- 先获取/校验两份像素、编码两份 PNG，再在各目标同目录暂存并 `fsync` **全部**文件；任何编码/暂存失败均不发布输出。临时文件尽力清理。
- 无覆盖模式以同文件系统硬链接原子发布，不支持该能力的文件系统明确失败；发布前出现同名文件也不会覆盖它。
- `overwrite=true` 用单文件 `os.replace`；它不允许两个文件成为一个原子事务。**第二个发布失败时第一个可能已经发布/覆盖，不回滚、不删除调用者文件。** 报错包含 `published_paths=[...]`，HTTP 非 200，绝不返回配对成功。覆盖原文件不可恢复，应优先使用全新输出路径。
- 崩溃、磁盘故障和 HTTP 超时可能留下一个或两个输出及临时文件。没有磁盘多文件事务或目录级崩溃耐久保证；返回结果后其他进程也可修改 PNG。
- 保留现有主线程桥 10 秒超时。若回调已开始，超时不能中断 Krita 作业或正在进行的输出；503/客户端中断不代表没有文件。CLI 120 秒客户端 timeout 不会扩大桥的 10 秒。检查输出再决定是否以新路径重试，勿盲目覆盖。

## 验收映射和实机待验清单

| 要求 | 离线证据 | 实机状态 |
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

以后获准实机测试时，使用专用测试文档，不碰用户作品：

1. 1328×1328、RGBA/U8、上述 sRGB profile；在不同组、隐藏层和透明背景上放红/绿/蓝与半透明色块，记录 modified/undo 状态。
2. 建立右下角触边矩形与羽化选区；使用原生 Selection 数据记录 M=0/128/255 和中间羽化值的位置，保存原生边界，然后配对导出。
3. 独立解码两个 PNG，核对尺寸/原点、投影与 GUI 合成、RGBA 四通道（特别 Alpha=128）、蒙版所有覆盖值与边界；验证刷新后像素已更新且作品 modified/undo 不变。
4. 取消选区：默认失败；显式 full_canvas：全 255。测试不支持 profile/U16 拒绝。
5. 记录 token，关闭再打开同一专用 `.kra`，旧 token 请求必须失败；测试标签页关闭、最后视图关闭、未保存文档关闭、重复打开同源文档。记录实际 notifier 事件、root UUID 与 token，不仅比较文件名。
6. 在完成以上真实证据审阅前，不将本说明改成“实机通过”，不宣称完整用户画布或安全回填验收完成。

## 离线运行

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 -B core/cwb.py --help
python3 -B core/cwb.py snapshot --help
bash -n core/install.sh
git diff --check
```
