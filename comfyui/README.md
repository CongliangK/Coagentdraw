# ComfyUI 工作流

> **PR-01 目标流程/待迁移提示**：目标流程服务 Krita 线稿/平涂与安全候选循环。下列 t2i/2K 仅声明文生图，edit/w4a8 仅声明单/双参考图编辑；localedit 是 **latent 噪声掩码 + 裁剪重绘 + 像素贴回** 的受控区域合成，不是模型原生 Mask，也不具备专用 Lineart/Depth/Pose 条件。新路径须记录真实节点、输入角色、蒙版极性与对照运行证据；输出先候选，不以参考图输入或 prompt 描述冒称结构控制。localedit 已有 B 实机对照证据（见[掩码规范](#局部改图掩码规范2026-10-实测)与 [CAD-R02](<../design/调研报告/CAD-R02_局部精确改图与剪影调整实测.md>)）；真实模型效果以实机为准，不自动下载/换栈。 见[现行总纲](<../design/Krita中枢创作流程_正式执行规范.md>)。此提示不增加现有命令、参数或运行保证。

[cwb.py](<../core/cwb.py>) 通过 ComfyUI `/prompt` API 提交的 API 格式工作流（Qwen-Image-2.1）。

| 文件 | 用途 | 备注 |
|---|---|---|
| [wf_qwen21_t2i_api.json](wf_qwen21_t2i_api.json) | 文生图 | `cwb comfy-t2i` 默认加载 |
| [wf_qwen21_edit_api.json](wf_qwen21_edit_api.json) | 图像编辑（单图/双图） | `cwb comfy-edit` 默认加载 |
| [wf_t2i_2k_api.json](wf_t2i_2k_api.json) | 文生图（2K 变体） | 备用 |
| [wf_edit_w4a8_api.json](wf_edit_w4a8_api.json) | 图像编辑（w4a8 量化变体） | 备用 |
| [wf_qwen21_localedit_api.json](wf_qwen21_localedit_api.json) | 局部精确改图：底图+掩码双输入，掩码外像素贴回 | 必须提供 `--image2`（掩码） |

## 基线：用户提供的 Viggle Turbo 工作流

当前两条 API 模板改编自用户提供的 `▶▷Qwen-image21-图像编辑&生图-Viggle Turb_my.json`（SHA-256：`2a47a20d0795b2a54a4770ac5499c6814ecbb6c3092d3f66160bd916cc58a9e7`）。详细节点对应、取舍和证据边界见 [改造说明](<qwen21_viggle_适配说明.md>)。附件本身已经是 API 格式，不是前端布局 JSON。

- **编辑默认单图**：上传第一张图，Lanczos 缩放至约 1.5MP、32 像素倍数；编码器 `resolution=0`；直接使用编码器返回的 latent 及其尺寸。输出尺寸可能不同于输入，不是同尺寸像素替换。
- **编辑必经双 LoRA**：UNET → Turbo LoRA（1）→ consistency LoRA（1）→ QwenImage21Cache → SamplerCustom。两者不可混为一谈；文生图按附件仅使用 Turbo LoRA，不凭空添加编辑一致性 LoRA。
- **保留配套采样**：Euler、CFG=1、开启噪声、六节点 `1.0, 0.9375, 0.875, 0.75, 0.5, 0.25`，由 ViggleTurboSigmas 按实际 latent 尺寸处理；不再用普通 KSampler/simple/25步替代。
- 双图仅通过显式 `--image2` 使用，为实验入口，不作为一致性可靠路径。优先从已确认主稿逐次单图编辑，每次人工复看，保留主稿和备选。
- 按用户实测经验，中英文理解差距不大、可使用长文本；不强制英文、不截短 prompt。明确描述修改项与保留项，但不把提示词承诺当作区域外像素锁定。
- 附件的默认负面词包含 `rough sketch`，此处为忠实基线予以保留；若目标为粗线稿，需在自定义模板中显式调整，避免与正面提示冲突。

## 依赖模型

工作流通过文件名引用模型，需自行放置到 ComfyUI 的 `models/` 对应目录：

- `qwen_image_2.1_int8_convrot.safetensors`（UNET，int8）
- `qwen3vl_8b_int8_convrot.safetensors`（CLIP，int8；w4a8 变体使用 `qwen3vl_8b_w4a8.safetensors`）
- `qwen_image_2.1_vae_bf16.safetensors`（VAE）
- `Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r128.safetensors`（LoRA，文生图和编辑均必需）
- `qwen-image-2.1-consistency.safetensors`（LoRA，编辑必需）

节点依赖包括 `ViggleTurboLora`、`ViggleTurboSigmas`（本机注册模块为 `custom_nodes.viggle_turbo`）、`QwenImage21Cache` 与 `TextEncodeQwenImage21`，以及标准加载/缩放/采样/解码/保存节点。缺失时补齐相同版本依赖，不静默跳过 LoRA 或回退旧采样器。

### 自定义节点（localedit 必需）

- [ComfyUI-Inpaint-CropAndStitch](https://github.com/lquesada/ComfyUI-Inpaint-CropAndStitch)（`InpaintCropImproved`/`InpaintStitchImproved`；本机已装于 `custom_nodes/`，依赖 cv2/numpy）

## 路径约定

`cwb.py` 默认从仓库内本目录加载工作流；可用环境变量 `CWB_WF_DIR` 指向自定义工作流目录（缓存 `cowork_cache/` 也会创建在该目录下）。

## 局部改图掩码规范（2026-10 实测）

localedit 的精度来自机制保证而非提示词：底图经 VAEEncode 进入 latent，`SetLatentNoiseMask` 只在掩码区采样，重绘结果由 `InpaintStitchImproved` 在像素空间贴回。实测（Qwen-Image-2.1 int8，1024² 底图，768² 裁剪窗，掩码外扩 50px、羽化 32px，见 [CAD-R02](<../design/调研报告/CAD-R02_局部精确改图与剪影调整实测.md>)）：

- 掩码外（扣除羽化带）与底图 **逐像素一致（差值全零）**；羽化带内允许混合。对照：纯参考图编辑圈外 12.3% 像素大变；全图 latent 掩码圈外仍有 VAE 往返噪声（均值约 1/255）。
- 提示词只能影响掩码内；"圈外不变/无渐变"之类字句对模型无效，不要依赖。
- 掩码设计规则（违反即失败，实测记录见 cache）：
  1. 部件调整：掩码框住目标部件全形 + 余量，边界落在部件间自然空隙，不横切实体；
  2. 大形/比例调整：保留一个锚点部件（通常是头/帽），掩码覆盖其余整体重做，提示词描述新大形而非"局部微调"；
  3. 提示词不得含几何矛盾（如"大衣加长到膝下"且"腿部不变"）；
  4. 剪影用途掩码内可能有淡灰残留，必要时在 Krita 侧阈值化后处理。
- 掩码极性：白 = 重绘区，黑 = 保留区；掩码经 `ImageToMask`（红通道）输入，同时作为 `<image2>` 参考进入文本编码器（模型"看得见"标注区域）。
- 边界渐变是模型在掩码边缘的固有行为，掩码切过服装时会出现；缓解靠掩码沿结构边界画或覆盖完整部件，机制本身不解决。

## CLI 参数与契约（CAD-10）

```bash
python3 core/cwb.py comfy-t2i "古堡，清晰线稿与简单平涂" --seed 42 --cfg 1
python3 core/cwb.py comfy-t2i "古堡，清晰线稿与简单平涂" --w 2048 --h 2048 --seed 43
python3 core/cwb.py comfy-edit base.png "仅将外套改为深蓝色，保留脸部、发型、姿态、服装结构和背景。" --seed 42
# 实验双图；不建议作为默认角色一致性路线
python3 core/cwb.py comfy-edit base.png "仅将图1外套配色改为图2的配色，保留图1其余内容。" --image2 ref.png --seed 42
python3 core/cwb.py comfy-replay comfyui/cowork_cache/<UUID>/run.json
```

- `--help`、参数解析及契约校验不创建缓存目录，不访问网络。缓存仅在有效生成任务开始时创建。
- `--wf` 为 `CWB_WF_DIR` 内的 JSON 路径，禁止跳出目录（包括符号链接）。需同时提供同名 `*.contract.json`。各工作流均附契约；当前默认文生图/编辑模板已更新为 Turbo 配套配置，其他历史变体不代表本次已适配。
- 契约版本为 1，包含 `mode`（t2i/edit）、`parameters`、`outputs`。每个参数显式声明 `node`、`input`、`class_type`，不推断任意节点；目标节点、类型、字段、默认值不匹配时立即失败。参数与图像映射的全部目标不得重叠，所有 LoadImage 节点均须由编辑契约映射。单图模式仅移除 collection 中的 image_2 引用；若图中任何其他输入（包括嵌套输入和非下载目标分支）仍引用第二个加载节点，则在文件读取、网络及缓存创建前明确拒绝，要求提供 --image2 或修改工作流。第一图的其他分支与双图的多输出仍受支持。编辑图还声明 `images.image1/image2/collection`。
- `--seed` 为 0 至 2^64−1 的整数；`--steps` 为正整数；`--cfg` 为非负有限数；`--denoise` 为 0 至 1；T2I 的 `--w/--h` 为正整数。实际模型/节点的额外约束由服务端校验，离线校验不代表模型支持。
- 未指定的参数保留工作流 JSON 中的值。当前 T2I 默认 928×1664（约9:16、1.5MP、32倍数的显式尺寸），seed=42、cfg=1；2K 输出通过显式尺寸参数指定，其效果/显存另需实测。当前两条 Turbo 模板**不支持 `--steps`、`--denoise`**，提供时在联网/创建缓存前报错，不假装映射到不相关字段。六节点调度固定于模板；CLI 保留这两个参数以兼容其他显式声明它们的旧/自定义契约。
- 契约版本1新增可选 `image_preprocessing.image1`，目标严格指向 `ImageScaleToTotalPixels.image` 且必须引用第一张 LoadImage；图像集合引用缩放节点输出。旧契约无此字段时仍直接引用 LoadImage，旧运行快照不迁移。单图去除第二张引用时保留第一张缩放链，重放亦核对同一链路。`images` 使用项目已有嵌套 autogrow 表示；附件使用 `images.image_1` 扁平表示，语义对应，目标 ComfyUI 版本的实际执行仍须联调。
- `--timeout` 默认为 1800 秒，覆盖上传、提交、轮询与下载；每次网络请求使用剩余预算，返回后继续检查总预算。底层 urllib 的 timeout 为阻塞 I/O 超时，不是操作系统硬中断；极端慢速分块响应可能延迟本次调用返回。不会因此重复提交。
- 上传使用 UUID 文件名且不覆盖已有文件，按服务端返回的 name/subfolder/type 填入 LoadImage（带 `[type]` 标记），不假设服务端沿用请求名称。输入文件全部成功读取、契约通过后才上传。
- 输出按契约列出的节点及各节点图像顺序保存全部图片；保留 node_id、index、filename、subfolder、type。下载查询用 URL 编码，路径使用本地序号，服务端文件名不能改写本地目录。`--add` 仅在恰好一个输出时导入新图层；多输出保留全部文件并报错要求手动用 `add` 选择，不任意取首图。

### 运行记录与重放

每次显式调用生成独立 `cowork_cache/<UUID>/`，包含原输入副本、完整提交 `workflow.json`、输出和原子更新的 `run.json`。记录有效参数、契约、模板、prompt_id、上传/输出定位、SHA-256、运行状态与失败阶段。成功、失败或 Ctrl-C 都保留记录；提交请求丢失响应时 prompt_id 可能未知，不能据此判断服务端未执行。异常不会自动重试 POST、上传或导入，也不会自动取消服务端任务。磁盘不可写等情况下记录可能不完整，应保留错误排查。

`comfy-replay` 是用户明确要求的**新一次生成**，不恢复旧任务、不覆盖旧目录。读取并校验快照哈希，在同一服务地址提交完全相同的图；提交前从图内实际图像集合推导单/双输入，严格核对上传清单的结构、唯一键、精确覆盖、SHA-256 格式，以及每个 locator 与图内 LoadImage 字符串的绑定，再从原模板加上传绑定重建并比对整个提交图。结构损坏时不建立新运行目录或发网络请求；编辑输入需仍在服务端，先按原定位下载并比对 SHA-256，失效或变动立即停止，不静默重新上传。模型、节点、服务版本变化可能使同一请求得到不同结果，记录不能保证图像逐像素确定性。这些校验用于发现记录损坏和不一致，不是签名或身份认证：能够同时修改图、模板、契约、清单与哈希的人可改写实验。只重放自己信任的本地记录，不将未知来源记录视为安全可执行文件。输入下载校验失败不会提交；确认旧任务状态后才决定是否重放，避免不确定提交被重复执行。

### 验证范围

`python3 -m unittest discover -s tests -v` 使用内存 mock transport，不监听 socket、不启动服务；覆盖四份图、两组参数、无效映射/参数、上传返回值、HTTP/JSON/执行错误、空输出、多输出、总预算、重复运行、重放和中断。离线结果不是真实生成证据。真实 ComfyUI 两组参数执行与重放由 #9 接收；编辑和模型效果仍由 #15/#16 等真实环境验收。
