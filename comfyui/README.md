# ComfyUI 工作流

[cwb.py](<../core/cwb.py>) 通过 ComfyUI `/prompt` API 提交的 API 格式工作流（Qwen-Image-2.1）。

| 文件 | 用途 | 备注 |
|---|---|---|
| [wf_qwen21_t2i_api.json](wf_qwen21_t2i_api.json) | 文生图 | `cwb comfy-t2i` 默认加载 |
| [wf_qwen21_edit_api.json](wf_qwen21_edit_api.json) | 图像编辑（单图/双图） | `cwb comfy-edit` 默认加载 |
| [wf_t2i_2k_api.json](wf_t2i_2k_api.json) | 文生图（2K 变体） | 备用 |
| [wf_edit_w4a8_api.json](wf_edit_w4a8_api.json) | 图像编辑（w4a8 量化变体） | 备用 |

## 依赖模型

工作流通过文件名引用模型，需自行放置到 ComfyUI 的 `models/` 对应目录：

- `qwen_image_2.1_int8_convrot.safetensors`（UNET，int8）
- `qwen3vl_8b_int8_convrot.safetensors`（CLIP，int8；w4a8 变体使用 `qwen3vl_8b_w4a8.safetensors`）
- `qwen_image_2.1_vae_bf16.safetensors`（VAE）

## 路径约定

`cwb.py` 默认从仓库内本目录加载工作流；可用环境变量 `CWB_WF_DIR` 指向自定义工作流目录（缓存 `cowork_cache/` 也会创建在该目录下）。

## CLI 参数与契约（CAD-10）

```bash
python3 core/cwb.py comfy-t2i "a castle" --seed 42 --steps 25 --cfg 1 --denoise 1
python3 core/cwb.py comfy-t2i "a castle" --wf wf_t2i_2k_api.json --seed 43 --steps 10
python3 core/cwb.py comfy-edit base.png "change the outfit" --image2 ref.png --seed 42
python3 core/cwb.py comfy-replay comfyui/cowork_cache/<UUID>/run.json
```

- `--help`、参数解析及契约校验不创建缓存目录，不访问网络。缓存仅在有效生成任务开始时创建。
- `--wf` 为 `CWB_WF_DIR` 内的 JSON 路径，禁止跳出目录（包括符号链接）。需同时提供同名 `*.contract.json`。四份已入库工作流均附契约；原工作流文件没有改动。
- 契约版本为 1，包含 `mode`（t2i/edit）、`parameters`、`outputs`。每个参数显式声明 `node`、`input`、`class_type`，不推断任意节点；目标节点、类型、字段、默认值不匹配时立即失败。参数与图像映射的全部目标不得重叠，所有 LoadImage 节点均须由编辑契约映射。单图模式仅移除 collection 中的 image_2 引用；若图中任何其他输入（包括嵌套输入和非下载目标分支）仍引用第二个加载节点，则在文件读取、网络及缓存创建前明确拒绝，要求提供 --image2 或修改工作流。第一图的其他分支与双图的多输出仍受支持。编辑图还声明 `images.image1/image2/collection`。
- `--seed` 为 0 至 2^64−1 的整数；`--steps` 为正整数；`--cfg` 为非负有限数；`--denoise` 为 0 至 1；T2I 的 `--w/--h` 为正整数。实际模型/节点的额外约束由服务端校验，离线校验不代表模型支持。
- 未指定的参数保留工作流 JSON 中的值。既有默认 T2I 保持 1328×1328、seed=42、steps=25、cfg=1、denoise=1；显式选择 2K 变体时保留其 2048×2048。不支持的参数明确报错。
- `--timeout` 默认为 1800 秒，覆盖上传、提交、轮询与下载；每次网络请求使用剩余预算，返回后继续检查总预算。底层 urllib 的 timeout 为阻塞 I/O 超时，不是操作系统硬中断；极端慢速分块响应可能延迟本次调用返回。不会因此重复提交。
- 上传使用 UUID 文件名且不覆盖已有文件，按服务端返回的 name/subfolder/type 填入 LoadImage（带 `[type]` 标记），不假设服务端沿用请求名称。输入文件全部成功读取、契约通过后才上传。
- 输出按契约列出的节点及各节点图像顺序保存全部图片；保留 node_id、index、filename、subfolder、type。下载查询用 URL 编码，路径使用本地序号，服务端文件名不能改写本地目录。`--add` 仅在恰好一个输出时导入新图层；多输出保留全部文件并报错要求手动用 `add` 选择，不任意取首图。

### 运行记录与重放

每次显式调用生成独立 `cowork_cache/<UUID>/`，包含原输入副本、完整提交 `workflow.json`、输出和原子更新的 `run.json`。记录有效参数、契约、模板、prompt_id、上传/输出定位、SHA-256、运行状态与失败阶段。成功、失败或 Ctrl-C 都保留记录；提交请求丢失响应时 prompt_id 可能未知，不能据此判断服务端未执行。异常不会自动重试 POST、上传或导入，也不会自动取消服务端任务。磁盘不可写等情况下记录可能不完整，应保留错误排查。

`comfy-replay` 是用户明确要求的**新一次生成**，不恢复旧任务、不覆盖旧目录。读取并校验快照哈希，在同一服务地址提交完全相同的图；提交前从图内实际图像集合推导单/双输入，严格核对上传清单的结构、唯一键、精确覆盖、SHA-256 格式，以及每个 locator 与图内 LoadImage 字符串的绑定，再从原模板加上传绑定重建并比对整个提交图。结构损坏时不建立新运行目录或发网络请求；编辑输入需仍在服务端，先按原定位下载并比对 SHA-256，失效或变动立即停止，不静默重新上传。模型、节点、服务版本变化可能使同一请求得到不同结果，记录不能保证图像逐像素确定性。这些校验用于发现记录损坏和不一致，不是签名或身份认证：能够同时修改图、模板、契约、清单与哈希的人可改写实验。只重放自己信任的本地记录，不将未知来源记录视为安全可执行文件。输入下载校验失败不会提交；确认旧任务状态后才决定是否重放，避免不确定提交被重复执行。

### 验证范围

`python3 -m unittest discover -s tests -v` 使用内存 mock transport，不监听 socket、不启动服务；覆盖四份图、两组参数、无效映射/参数、上传返回值、HTTP/JSON/执行错误、空输出、多输出、总预算、重复运行、重放和中断。离线结果不是真实生成证据。真实 ComfyUI 两组参数执行与重放由 #9 接收；编辑和模型效果仍由 #15/#16 等真实环境验收。
