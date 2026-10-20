# CAD-23 最小离线比较与候选决定

## 范围与未完成门槛

这是**手工文件输入的离线模块**，不是生成器或 Krita 操作入口。无需网络、模型、Krita、ComfyUI；不使用 CAD-13 token，不写图层，不调用 CAD-18 图像诊断，不修改 CAD-19 项目 schema。比较资产和事件存放在独立 sidecar 目录。

- 已实现：原始 PNG 关联、1:1 横向 Before/After、共同裁切、来源摘要、离线接受/拒绝/隐藏/撤回记录、重开查询理由。
- **未实现/未联调**：#25 决策库（云端任务）、#17 局部重绘链路、真实 Krita 图层选择/可见性操作、一键恢复原投影及保存 `.kra` 后恢复。本模块不构成这些任务完成或 issue #23 整体关闭的证据。
- `accept` 只记录选择；`reject` 不删除任何资产；`hide` 只记录离线 hidden 状态；`withdraw` 只把离线状态置回 pending。**hide/withdraw 均不会实际隐藏/显示图层，也不会恢复原画面。** 每条命令响应都包含 `offline_record_only`/中文边界提示。

## CLI

从仓库根目录运行；路径相对于运行 CLI 的当前目录。输出父目录必须已存在，输出目录必须不存在。

```bash
python3 core/cwb.py compare --help
python3 core/cwb.py compare create /tmp/before.png /tmp/after.png /tmp/new-comparison \
  --source-version '手工源记录 v3' --candidate-version '手工候选 v1' \
  --crop 100 80 240 180 \
  --prompt '用户提供的关联文字' --workflow '用户提供的工作流标识' --seed 123
python3 core/cwb.py compare show /tmp/new-comparison
python3 core/cwb.py compare decide /tmp/new-comparison accept \
  --expect-sequence 0 --reason '选择这个候选，但没有回填 Krita'
python3 core/cwb.py compare decide /tmp/new-comparison hide \
  --expect-sequence 1 --reason '仅记录暂时隐藏意图；画布未改变'
python3 core/cwb.py compare decide /tmp/new-comparison reject \
  --expect-sequence 2 --reason '轮廓不合适，候选仍保留'
python3 core/cwb.py compare decide /tmp/new-comparison withdraw \
  --expect-sequence 3 --reason '撤回当前决定，重新审评；未恢复投影'
```

不提供 `--crop` 时比较整图。无论是否裁切，before/after 原始尺寸必须相同；不匹配显式失败。`--crop X Y W H` 使用两张图共同的原生像素坐标，必须正宽高且完全在边界内；没有自动扩边、缩放、对齐或坐标映射。

可选 `--document-ref`、`--candidate-layer-ref` 仅保存用户给出的未验证文本关联，不读取文档、不验证图层，不将会话 ID 当重开后的稳定身份。`--workflow` 也是文本关联，**不会读取/执行工作流**。来源版本、prompt、workflow、seed、图层/文档关联未提供时均为 JSON `null`（未知）；不会推断 seed=0、伪造生成记录、追认配对快照或同回调捕获。

## 显示与文件契约

仅支持单帧、位深不超过 8 的 PNG（包括透明、灰度、调色板模式）；拒绝动画、16 位 PNG 和伪装的其他格式。CLI 外部 Python 可选依赖 **Pillow (PIL)** 只在 `create` 解码时惰性导入。缺失时清楚报错，不自动安装；Krita 插件不引入依赖。模块导入、所有帮助、`show`、`decide` 均不需 Pillow。当前安全文件访问依赖 Linux 的 `dir_fd`、`O_NOFOLLOW` 与 `flock`。

新目录包含：

| 文件 | 含义 |
|---|---|
| `before.png`、`after.png` | 原始输入**逐字节**副本，不重编码、不删除 |
| `comparison.png` | 左 Before、右 After；上方 20px 独立标题带；内容面板 1:1 RGBA8，保留 Alpha，包括透明像素 RGB |
| `compare.json` | 独立版本标识 `cwb-compare-offline-v1`、比较 UUID、before/after 绝对原路径、SHA256、字节数、尺寸、源版本、用户关联和显示坐标 |
| `event-000001.json` 起 | 新决定的独立顺序事件；原事件与 manifest 不覆写 |

比较图不做 EXIF 自动旋转、ICC 色彩变换或背景合成，坐标是文件原生像素；查看器如何显示透明背景/色彩不在此契约内。面板位置 `panel_boxes_xywh` 明确记录标题带偏移。相同输入/裁切在同一 Pillow 环境生成相同 PNG 字节；UUID、创建时间和事件时间不是确定性内容，不承诺跨 Pillow 版本 PNG 编码逐字节一致。输入单文件最多 128 MiB、最多 1600 万像素；比较图最多 128 MiB；每个来源文本/理由最多 64 KiB，manifest 最多 1 MiB。

## 决定与重开

初始状态 `pending`、序号 0。允许在不同状态间追加 accept→accepted、reject→rejected、hide→hidden、withdraw→pending。重复同状态、空理由、非法操作和过期 `--expect-sequence` 显式失败，不追加事件。文件锁串行化本模块的并发决定，两个相同序号的决定只有一个成功。

每个事件保存比较 UUID、序号、action、previous_state/state、理由、UTC 时间、前事件字节 SHA256，以及 `mode=offline_record_only`、`krita_applied=false`。`show` 重新读取资产和完整事件，检查资产/比较图摘要、事件连续顺序、状态迁移和前事件哈希，返回当前状态、序号和全部理由。没有源文件也能重开已保存比较；追加决定则要求原路径仍可读取且摘要与原比较一致。

事件关联 manifest 的比较 UUID，manifest 关联源版本及资产摘要。以上是未来 #25 适配器**可读取的输入**，不是 #25 schema、API、同步协议或联调结果。适配器未来需要显式处理稳定项目/作品身份、外部事件去重和真实图层动作证据，本任务不实现它们。

## 安全、失败与限制

- 不覆盖输入、既有文件、既有结果目录或旧事件；目录从不复用。输入 before/after 不能为同一 inode。拒绝硬链接文件、多组件符号链接、`.`/`..` 路径别名、重复分隔符、FIFO/目录等非普通文件；输出父目录也不能经符号链接访问。
- 所有输入先捕获字节与 SHA256；读取前后核对文件身份/大小/mtime/ctime。创建完成前再次核对源字节与身份；决定记录前确认源摘要仍属原比较，写入后再核对源。检测到改变则失败，清理本次新文件/事件，不产生本次成功响应，不清理旧资产。
- 捕获到的写盘、校验异常或 Ctrl-C 会清理本次新输出；成功事件只有独立追加，不触碰既有事件。文件和目录 fsync 降低丢失风险；这不是数据库事务或防篡改审计存储，不承诺断电/SIGKILL 后自动恢复。残留不完整目录不可继续追加，保留调查后改用新的输出目录；不要把残留文件当成功响应。
- SHA 链可发现常见损坏/前事件改动，但不是签名，不防有权限者整体重写。并发安全面向遵守同一锁的本 CLI，不抵御同 UID 恶意进程在操作过程中替换目录/篡改文件。源文件在最终检查之后的未来修改不会被本次调用阻止；之后 `decide` 会重验原路径，`show` 仍展示已保存快照。
- 不读取 `.kra`，不对源版本文字真实性、用户给定 prompt/workflow 与图像之间的因果作保证。所有示例都需用户自行准备输入；没有真实生成或真实 Krita 恢复证据。

## 离线验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_compare.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

新增测试实际解码合成 PNG，核对原图/透明度/共同 crop/显示坐标、尺寸/格式/位深错误、SHA 和未知字段、源在生成/记录中变化、事件顺序/并发/跨进程重开/理由/拒绝资产保留、符号链接/硬链接/路径别名/FIFO、写盘失败清理。帮助在 `python -B -S` 下运行且用审计钩子禁止 socket 和 mkdir，验证无 Pillow、无联网、无缓存创建。测试不是 #17、#25 或 Krita 实机证据。
