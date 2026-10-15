# CAD-14：确定性图像导入与坐标规则（离线实现）

## 范围与证据边界

扩展既有 `/layer/add`，不替代已有 `parent_id`、`above`、`offset_x/y`。仅新建透明 `paintLayer`，不覆盖、删除、移动或合并旧层；画布尺寸不变。当前仅完成纯 Python 映射与 Qt/Krita 替身回归，**没有运行真实 Qt 解码或 Krita 通道/像素落点验收**。历史 README 不是本项的实机证据。

不提供模型编辑、选区自动回填、#17 强文档安全。#13 token 不接入此写接口，也不是安全写回凭证：接口仍在主线程操作当时的活动文档，调用前人工确认文档；网络超时不能证明未发生写入。

## 请求与兼容性

必填：`png_path`、`name`（非空字符串）。可选：`parent_id`、`above`（组 ID/同级 ID）、`offset_x/y`（默认 0）、`mode`、`fit_canvas`。

`mode` 是以下三个受支持名称之一。`crop_at_bbox` **未实现**，返回 OpError / HTTP 400，绝不降级到原尺寸、适配或裁切。

- 未指定 `mode`（或为 null）：兼容旧接口，offset 均为 0 则 `keep_size`，否则 `insert_at_offset`。
- `fit_canvas: true` 是 `mode: "fit_canvas"` 的兼容别名；`false` 表示不缩放（未给 mode 时采用上述旧接口规则）。只接受 JSON bool，null 视为省略。
- 同时指定 mode 与 bool 时必须一致：fit 模式只能搭配 true，其余模式只能搭配 false；冲突报 400。显式 `keep_size`/`fit_canvas` 不接受非零 offset，不能隐式覆盖用户坐标。
- 坐标只接受 JSON 整数 0..32768，拒绝 bool、所有 float（包括 `1.0`）、NaN/Infinity、数字字符串、负数、超范围整数；不使用 `int()` 截断。源图/画布每轴为 1..32768 的整数；最终矩形必须完整落在画布内，越界直接拒绝，不依赖 Krita 静默裁切。
- 父节点只能是组（省略/null 使用 root 容器）；above 必须是该父级的直接 paint/group 子节点。保留原有会话节点 ID 与挂树顺序检查。

## 有界导入与资源预算（修复轮 1）

每轴 32768 **不是**总资源预算。所有模式额外采用同一组固定准入上限，超出为 OpError / HTTP 400，不创建/挂载新层：

| 项目 | 上限 / 估算 |
|---|---|
| 源总像素 `Sw*Sh` | 16,777,216（例如 4096²；不是任意 8192²） |
| 解码 RGBA 字节 | 67,108,864（64MiB）；转换后再检查 `bytesPerLine*Sh`，包含 padding |
| 输出像素 / 最近邻采样工作 | 4,194,304（例如 2048²），原尺寸模式也遵守 |
| 编码 PNG 普通文件 / chunk 数 | 32MiB / 4096 个；拒绝管道、设备、目录 |
| 所有权缓冲区工作字节估算 | `2*32MiB + 2*解码字节 + 2*输出字节 + W*40 + 65536`，不得超过 256MiB |
| 预处理经过时间 | operation 进入后单调时钟 5 秒，阶段/每行协作检查 |

估算覆盖编码输入+去元数据快照、Qt 解码+RGBA 转换、输出 bytearray+bytes、横坐标索引；它**不是整个 Krita 进程 RSS 上限**，不约束 Qt/Krita 内部临时数据、解释器/分配器开销或画布投影。1328² 原尺寸、4096² → 1328² / 2048² 适配仍可用；更大的源可按相同乘积预算判断（例如极细长图），不是放开每轴就都可用。旧正常 CLI/API 参数和三模式映射规则不变，超过本节资源/PNG 范围的旧输入有意收窄，不静默降质。

在 Qt 前先读取 33 字节 PNG 签名/IHDR，校验 CRC、合法格式和源尺寸，并计算最终计划/工作预算；之后才读取有界完整文件。只支持**非交错、静态、至多 8bit 标准 PNG**：灰度/索引可 1/2/4/8bit，RGB/灰度+Alpha/RGBA 为 8bit。16bit、Adam7、APNG、非 PNG、未知关键 chunk、坏 CRC、越界/截断 chunk、重复/乱序必要 chunk、IEND 后尾随数据均拒绝，不回退到其他 Qt 格式插件。像素压缩流由 Qt 验证，预检不是完整 PNG 解码器；不合法的有界 IDAT 仍可能到 Qt 后失败。

保留像素必要的 PLTE/tRNS 及固定长度 sRGB/gAMA/cHRM/pHYs；剥离其余 ancillary 元数据（包括文本、压缩文本、ICC）后才交给 Qt，避免解压元数据炸弹；ComfyUI 文本元数据的存在本身不导致失败，但不会导入到图层，也不承诺保留嵌入 ICC 配置。无新增颜色空间转换/BGRA 交换。所有 chunk 先有界扫描/CRC，再解码内存中的已检查 PNG 快照（`QImage.fromData(..., "PNG")`），**不再次按路径打开文件**。读取前后比较 fd 的设备/inode/大小/mtime/ctime，核对快照头和读出大小，尽力发现读取中修改；没有文件锁或安全来源证明，不能保证对并发改写的原子快照语义。修改后的路径不影响已取得快照的预算。

缩小时直接从 QImage 的 strided 缓冲采样，不先整幅 `bytes` 复制/去 padding 再缩小；不缩放的连续缓冲最多一次不可变打包，现成精确 bytes 可原样复用。QImage 保活到采样完成。转换后、指针取尺寸前再次核对解码字节预算，输出分配/打包前与采样每行检查时间。

5 秒预算从本次主线程 operation 进入开始，涵盖预检、解码、转换、缓冲和采样；Qt 返回后、创建节点前、`createNode` 返回后挂树前、挂树回读后写像素前均再检查。耗时预处理返回后若已耗尽预算，不继续开始新层写入。输入资源超限和预处理过期都在建层前拒绝；若 `createNode` 或挂树本身阻塞到过期，则不再开始下一步，游离节点或已挂载空层仍可能存在，**无事务回滚承诺**。

这不是硬实时保证：检查不能抢占磁盘读取、CRC/单次复制、Qt 解码、Krita 调用；bridge 10 秒超时也不取消已运行回调。预算不包含 bridge 队列等待，亦不能阻止已在进行的挂树/写入在 HTTP 超时后完成。本轮未修改桥接或提高超时。若以后要求拒绝所有已过 HTTP 截止时间的写入，最小跨层接口应由 bridge 传递从入队起的绝对截止时间/失效状态，并在操作开始及每次新写前检查；仍不能声称可以取消已进入的 Krita API。

## 尺寸、原点与变换

所有坐标均为**文档像素**，原点左上，x 向右、y 向下，矩形为半开区间。不是屏幕、缩放视图、组局部或图层 bounds 坐标。透明源边缘计入完整输入矩形，**不做 alpha trim**。

记源尺寸 `(Sw,Sh)`，画布 `(Cw,Ch)`，实际写入尺寸 `(W,H)`，写入原点 `(X,Y)`：

| mode | 输入 → 实际写入尺寸 | 原点 | 留白/缩放 |
|---|---|---|---|
| `keep_size` | `(Sw,Sh) → (Sw,Sh)` | `(0,0)` | 不缩放，右侧/下侧保持透明；图大于画布报错 |
| `insert_at_offset` | `(Sw,Sh) → (Sw,Sh)` | `(offset_x,offset_y)` | 不缩放；局部 PNG 完整矩形平移；周围透明 |
| `fit_canvas` | `(Sw,Sh) → (W,H)`，如下 | `((Cw-W)//2,(Ch-H)//2)` | 按比例 contain、只缩不放、居中；不拉伸至画布、不裁切、不填背景 |

fit 的名义比例采用精确分数 `s=min(1,Cw/Sw,Ch/Sh)`，两轴共享该比例；`W=max(1,floor(Sw*s))`、`H=max(1,floor(Sh*s))`。栅格量化可能使实际宽高比有不足 1 像素的误差（极细图最小保留 1 像素）；不以强制画布宽高拉伸代替等比适配。奇数留白向右/下多 1 像素，小图不放大，仍居中。例如 13×9 → 8×8：写入 8×5，原点 (0,1)，下方留白 2；3×2 → 8×7：尺寸不变，原点 (2,2)。

采用**最近邻、像素中心取样**，不调用 Qt 的隐含缩放/平滑算法，不进行混色或预乘 alpha：目标局部像素 `(u,v)` 读取源像素

```text
sx = floor((2*u+1)*Sw/(2*W))
sy = floor((2*v+1)*Sh/(2*H))
文档目标像素 = (X+u, Y+v)
```

源连续边界到目标边界的变换为 `(X + x*W/Sw, Y + y*H/Sh)`；它与上面的离散反向取样相区别。最近邻保留被选中像素的四个通道及 alpha=0 下隐藏 RGB，但缩小时细线/透明边框可能因取样消失，并非裁切。需要平滑插值须未来明确新契约，不能悄悄改变当前算法。

## 结果及写入顺序

结果保留 `id/name/width/height/parent_id`，width/height 指**写入矩形**，不是图层非透明 bounds 或画布。新增 `placement`，版本 1：

- `mode`、`source_size`、`canvas_size`、`output_size`、`origin`；
- `source_rect=[0,0,Sw,Sh]`、`destination_rect=[X,Y,W,H]`；
- `nominal_scale=[分子,分母]`，`edge_transform` 精确记录实际两轴比 `[W,Sw]` / `[H,Sh]` 及平移；
- `rounding`、`sampling` 算法名称；`upscale=false`、`crop=false`；
- `transparent_margins=[左,上,右,下]` 表示新层写入矩形之外的透明范围，不表示源图内部透明边缘；
- `resources` 新增源像素、`decoded_rgba_bytes`、输出采样/字节、`estimated_working_bytes`，实际导入时包含转换后的真实行 stride；纯计划使用紧密 RGBA stride。它们是准入估算，不是测量值。

所有参数类型、尺寸、边界、父组/同级关系、QImage 转换及缓冲区校验在创建节点前完成。保留 RGBA/U8 文档守卫、`Format_RGBA8888`、`constBits().asarray(bytesPerLine*height)` 与逐行去 padding，不引入推测的 BGRA 交换。先创建/挂树并回读父组与 above 顺序，再 `setPixelData`，检查返回值，再回读树。失败会报错；写入失败后可能留下新空层/部分层，**不自动删除任何层或重试**，不承诺事务回滚。返回成功也不等于已做 Krita 像素 readback。

## CLI / HTTP 示例

```bash
python3 core/cwb.py add original.png 原尺寸 --mode keep_size
python3 core/cwb.py add large.png 适配 --mode fit_canvas --parent GROUP_ID --above SIBLING_ID
python3 core/cwb.py add local.png 局部 --mode insert_at_offset --x 120 --y 64
python3 core/cwb.py add large.png 适配别名 --fit-canvas
```

```json
{"png_path":"/absolute/local.png","name":"局部备选","mode":"insert_at_offset","offset_x":120,"offset_y":64,"parent_id":"{group-id}","above":"{sibling-id}"}
```

CLI `--mode` 与 `--fit-canvas` 互斥。默认旧参数 payload 不变；帮助不联网、不创建缓存。ComfyUI/模型执行路径不属于此项，没有新增自动编辑。

## 离线验收与后续实机门槛

[映射测试](<../../tests/test_placement.py>) 使用内存 7×5 奇数宽度彩色棋盘：R/G 编码坐标、蓝色交替、透明边缘保留隐藏 RGB，替身每行追加 4 字节 padding。手算 7×5 → 4×2 读取 x=(0,2,4,6)、y=(1,3)，验证原尺寸、局部平移、缩小/小图居中、留白、极细图和元数据；HTTP handler/bridge 替身验证传参及非法参数 400，坏输入创建节点数必须为 0。真实 QImage 的 RGBA8888 奇数宽度通常无需额外 padding，替身强制 padding 仅验证防御分支。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
```

修复轮 1 先在旧实现运行快速失败回归：8192² 源、4096² → 4095² 输出，以及解码/整幅复制/输出分配陷阱触发 13 处失败；没有重新分配旧 1GiB 实验。新实现回归通过，新增坏头/CRC/chunk、极端长宽/stride 字节、稀疏超长文件/FIFO、并发修改检测、压缩 ancillary 剥离、1328² 和有界大图计划、预处理各阶段过期不建层及挂树晚返回不开始像素写入的测试。既有 CAD-11 虚构 `input.png` fixture 只作必要替身兼容，不代表真实 PNG 解码。

本机离线子进程设置 384MiB 地址空间和 CPU 6/8 秒软/硬限制，以 memoryview 源调用生产 `resize_rgba`（**不含 Qt/Krita**）：1328² 原尺寸单次打包约 0.0024s / 峰值 RSS 26.7MiB；4096² → 1328² 约 0.292s / 90.2MiB；4096² → 2048² 约 0.703s / 109.8MiB。旧 8192² → 8191² 计划约 6 微秒拒绝、源缓冲未分配。数值是该次采样/进程 RSS 证据，不是性能上界、整个主线程耗时或全链路内存保证。

仍需得到授权后，在专用 Krita 文档中测量而非肉眼猜测：

1. 用上述棋盘/坐标标记/透明边框 PNG，经真实 PyQt6 QImage 解码和 Krita 写入，分别读取源、目标、整画布投影数值，确认红蓝不交换及透明像素语义。若 Krita 实际通道不同，应独立修正并重新验收，不以替身结论替代。
2. 在小画布导入大图，核对每个采样点、上下留白与奇数舍入；确认 fit 小图没有被放大。
3. 将完整局部 PNG 以已知非零坐标写入新层，数值核对边角/内部标记及透明边缘，核对原层未变、组/above 顺序正确；保存/重开 `.kra` 再核对。
4. 测量准入上限内的真实 Qt/Krita 主线程耗时/内存及预算拒绝。纯 Python 资源量测不能替代真实解码、写层、画布投影或 bridge 排队超时测试；现有桥接超时不取消已开始操作。

上述实机项本次全部**未验**，因此不能据离线代码直接宣称 issue 的真实 Krita 像素验收完成。
