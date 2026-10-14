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
