# ComfyUI 工作流

> 本目录根目录只维护 T2I（文生图）和 I2I/ITI（图像编辑）两条 API 工作流。编辑支持单图/双图参考，不提供独立遮罩局部重绘或扩图流程，不保证指定区域外像素不变。输出先作为候选，由作者确认。

[cwb.py](<../core/cwb.py>) 通过 ComfyUI `/prompt` API 提交的 API 格式工作流（Qwen-Image-2.1）。

| 文件 | 用途 | 备注 |
|---|---|---|
| [wf_qwen21_t2i_api.json](wf_qwen21_t2i_api.json) | 文生图 | `cwb comfy-t2i` 默认加载 |
| [wf_qwen21_edit_api.json](wf_qwen21_edit_api.json) | I2I/ITI 图像编辑（单图/双图） | `cwb comfy-edit` 默认加载 |

另提供独立的 [画质优先局部编辑交互工作流](interactive/README.md)，可拖入 ComfyUI 使用；它不属于 CLI API 模板。

## API 模板配置

- **编辑默认单图**：上传第一张图，Lanczos 缩放至约 1.5MP、32 像素倍数；编码器 `resolution=0`；直接使用编码器返回的 latent 及其尺寸。输出尺寸可能不同于输入，不是同尺寸像素替换。
- **编辑必经双 LoRA**：UNET → Turbo LoRA（1）→ consistency LoRA（1）→ QwenImage21Cache → SamplerCustom。两者不可混为一谈；文生图仅使用 Turbo LoRA，不凭空添加编辑一致性 LoRA。
- **保留配套采样**：Euler、CFG=1、开启噪声、六节点 `1.0, 0.9375, 0.875, 0.75, 0.5, 0.25`，由 ViggleTurboSigmas 按实际 latent 尺寸处理；不支持用steps参数改变固定调度。
- 双图仅通过显式 `--image2` 使用，为实验入口，不作为一致性可靠路径。优先从已确认主稿逐次单图编辑，每次人工复看，保留主稿和备选。
- 按用户实测经验，中英文理解差距不大、可使用长文本；不强制英文、不截短 prompt。明确描述修改项与保留项，但不把提示词承诺当作区域外像素锁定。
- 模板默认负面词包含 `rough sketch`，若目标为粗线稿，需在自定义模板中显式调整，避免与正面提示冲突。

## 07—08 插画中的输入分工

| 目的 | 当前输入 | 采用前的核对 |
|---|---|---|
| 07 探索 / 8A＋8B 建整幅底画 | 默认从已确认的场景/角色主稿单图编辑；场景草稿＋角色参考仅作显式双图实验 | 参考驱动，不保证几何锁定；同尺度核对镜头、动作遮挡、角色身份、焦点与明暗 |
| 8C＋8D 当前主稿局部修订 | 独立交互模板：当前底画＋手绘真实蒙版，见 [操作说明](<interactive/README.md>) | 底画先承载正确身份；没有额外独立的三视图、Pose 或 Depth 输入，红圈诊断不等于已传入有效蒙版 |

上表的场景/角色是创作用途，不是程序内建的专用语义通道。HQ局部编辑模板仅在ComfyUI交互使用，不能通过 `comfy-edit --wf` 调用；外扩16像素、融合32像素是交互图内设置，非CLI参数。条件与VAE编码共用裁切底图，手绘蒙版用于噪声约束和像素拼回，不作为第二张参考图。

每次输出先作独立候选，核对本轮问题及全图关系；作者接受并实际采用后才作为下一轮底画，不把旧基底的相关局部强拼。调用、产出、导入、接受和实际采用分别记录；[compare](<../docs/development/cad23_compare.md>) 只作离线比较与决定记录，不自动替换主稿，[review](<../docs/development/cad24_review.md>) 无已验证的实际审评后端，不能替作者作艺术判断。

选区、蒙版、变形、滤镜与绘制等手工画改由作者在 Krita GUI，或具备相应界面能力且获授权的助手完成；当前[插件路由](<../core/krita_cowork/server.py>) 不提供通用笔刷、变形或滤镜接口。完整工作方法与失败出口见 [07](<../design/执行手册/07_插画方案探索.md>) / [08](<../design/执行手册/08_插画制作与交付.md>)，本页不另建创作流程。

## 依赖模型

工作流通过文件名引用模型，需自行放置到 ComfyUI 的 `models/` 对应目录：

- `qwen_image_2.1_int8_convrot.safetensors`（UNET，int8）
- `qwen3vl_8b_int8_convrot.safetensors`（CLIP，int8）
- `qwen_image_2.1_vae_bf16.safetensors`（VAE）
- `Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r128.safetensors`（LoRA，文生图和编辑均必需）
- `qwen-image-2.1-consistency.safetensors`（LoRA，编辑必需）

节点依赖包括 `ViggleTurboLora`、`ViggleTurboSigmas`（本机注册模块为 `custom_nodes.viggle_turbo`）、`QwenImage21Cache` 与 `TextEncodeQwenImage21`，以及标准加载/缩放/采样/解码/保存节点。缺失时补齐相同版本依赖，不静默跳过 LoRA 或回退旧采样器。

### 自定义节点（独立局部编辑交互工作流必需）

- [ComfyUI-Inpaint-CropAndStitch](https://github.com/lquesada/ComfyUI-Inpaint-CropAndStitch)（`InpaintCropImproved`/`InpaintStitchImproved`；运行前核对本机节点及其依赖）

## 路径约定

`cwb.py` 默认从仓库内本目录加载工作流；可用环境变量 `CWB_WF_DIR` 指向自定义工作流目录（缓存 `cowork_cache/` 也会创建在该目录下）。

## 输出尺寸与蒙版

2K输出通过T2I的 `--w 2048 --h 2048` 指定；编辑输出随输入缩放链决定，不能按原尺寸坐标直接当作replacement。输出先作为独立候选。

局部编辑先画有效蒙版：白为重绘、黑为保留，覆盖完整相关部件和必要余量，边界尽量落在自然分界。变动范围包含外扩与融合带，须复看邻域和全图身份、结构、光色；扩大羽化不能修复透视或连接错误。配置和操作以[交互模板说明](<interactive/README.md>)为准，尚无当前模板的真实生成/艺术验收。

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
- `--wf` 为 `CWB_WF_DIR` 内的 JSON 路径，禁止跳出目录（包括符号链接）。需同时提供同名 `*.contract.json`。两份工作流各附一份契约，契约不是额外工作流。
- 契约版本为 1，包含 `mode`（t2i/edit）、`parameters`、`outputs`。每个参数显式声明 `node`、`input`、`class_type`，不推断任意节点；目标节点、类型、字段、默认值不匹配时立即失败。参数与图像映射的全部目标不得重叠，所有 LoadImage 节点均须由编辑契约映射。单图模式仅移除 collection 中的 image_2 引用；若图中任何其他输入（包括嵌套输入和非下载目标分支）仍引用第二个加载节点，则在文件读取、网络及缓存创建前明确拒绝，要求提供 --image2 或修改工作流。第一图的其他分支与双图的多输出仍受支持。编辑图还声明 `images.image1/image2/collection`。
- `--seed` 为 0 至 2^64−1 的整数；`--steps` 为正整数；`--cfg` 为非负有限数；`--denoise` 为 0 至 1；T2I 的 `--w/--h` 为正整数。实际模型/节点的额外约束由服务端校验，离线校验不代表模型支持。
- 未指定的参数保留工作流 JSON 中的值。当前 T2I 默认 928×1664（约9:16、1.5MP、32倍数的显式尺寸），seed=42、cfg=1；2K 输出通过显式尺寸参数指定，其效果/显存另需实测。当前两条 Turbo 模板**不支持 `--steps`、`--denoise`**，提供时在联网/创建缓存前报错，不假装映射到不相关字段。六节点调度固定于模板；CLI 保留这两个参数用于显式声明它们的自定义契约。
- 契约版本1包含可选 `image_preprocessing.image1`，目标严格指向 `ImageScaleToTotalPixels.image` 且必须引用第一张 LoadImage；图像集合引用缩放节点输出。自定义图无缩放时可直接引用LoadImage。单图去除第二张引用时保留第一张缩放链，重放亦核对同一链路。`images` 使用项目已有嵌套 autogrow 表示，目标ComfyUI版本的实际执行仍须联调。
- `--timeout` 默认为 1800 秒，覆盖上传、提交、轮询与下载；每次网络请求使用剩余预算，返回后继续检查总预算。底层 urllib 的 timeout 为阻塞 I/O 超时，不是操作系统硬中断；极端慢速分块响应可能延迟本次调用返回。不会因此重复提交。
- 上传使用 UUID 文件名且不覆盖已有文件，按服务端返回的 name/subfolder/type 填入 LoadImage（带 `[type]` 标记），不假设服务端沿用请求名称。输入文件全部成功读取、契约通过后才上传。
- 输出按契约列出的节点及各节点图像顺序保存全部图片；保留 node_id、index、filename、subfolder、type。下载查询用 URL 编码，路径使用本地序号，服务端文件名不能改写本地目录。`--add` 仅在恰好一个输出时导入新图层；多输出保留全部文件并报错要求手动用 `add` 选择，不任意取首图。

### 运行记录与重放

每次显式调用生成独立 `cowork_cache/<UUID>/`，包含原输入副本、完整提交 `workflow.json`、输出和原子更新的 `run.json`。记录有效参数、契约、模板、prompt_id、上传/输出定位、SHA-256、运行状态与失败阶段。成功、失败或 Ctrl-C 都保留记录；提交请求丢失响应时 prompt_id 可能未知，不能据此判断服务端未执行。异常不会自动重试 POST、上传或导入，也不会自动取消服务端任务。磁盘不可写等情况下记录可能不完整，应保留错误排查。

`comfy-replay` 是用户明确要求的**新一次生成**，不恢复旧任务、不覆盖旧目录。读取并校验快照哈希，在同一服务地址提交完全相同的图；提交前从图内实际图像集合推导单/双输入，严格核对上传清单的结构、唯一键、精确覆盖、SHA-256 格式，以及每个 locator 与图内 LoadImage 字符串的绑定，再从原模板加上传绑定重建并比对整个提交图。结构损坏时不建立新运行目录或发网络请求；编辑输入需仍在服务端，先按原定位下载并比对 SHA-256，失效或变动立即停止，不静默重新上传。模型、节点、服务版本变化可能使同一请求得到不同结果，记录不能保证图像逐像素确定性。这些校验用于发现记录损坏和不一致，不是签名或身份认证：能够同时修改图、模板、契约、清单与哈希的人可改写实验。只重放自己信任的本地记录，不将未知来源记录视为安全可执行文件。输入下载校验失败不会提交；确认旧任务状态后才决定是否重放，避免不确定提交被重复执行。

### 验证范围

`python3 -m unittest discover -s tests -v` 使用内存 mock transport，不监听 socket、不启动服务；覆盖两份图、两组参数、无效映射/参数、上传返回值、HTTP/JSON/执行错误、空输出、多输出、总预算、重复运行、重放和中断。离线结果不是真实生成证据。真实 ComfyUI 两组参数执行与重放由 #9 接收；编辑和模型效果仍由 #15/#16 等真实环境验收。
