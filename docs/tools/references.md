# 本地 Web 参考图库

早期先在 Web 图库广泛预览、标注和筛选，方向较明确后，仅把选中素材导入 Krita 试拼。通用网页从 `/api/catalog` 读取项目内的外部 `references.json`，HTML/JS 不包含项目图片清单、角色或固定标签；数据修改后点击“重新读取资料集”即可更新。

## 使用

```bash
# 仓库自带的原创几何教学数据；不含真实项目素材
python3 core/cwb.py references serve examples/reference_gallery

# 项目内建立资料集（目录可已有图片；不覆盖references.json）
python3 core/cwb.py references init "项目/我的项目/资料/参考集" --title "角色参考"

# 先把原图下载到该目录，登记时自动测量；命令本身不下载或复制图片
python3 core/cwb.py references add "项目/我的项目/资料/参考集" "原图/cape.png" \
  --id cape-01 --title "披衣参考" --meaning "观察肩部展开和下摆收束；初步联想，未试拼" \
  --source-url "https://example.org/artwork" --creator unknown --rights unknown \
  --group "外披" --material "单视图，需裁选" --tag 披风 --tag 轮廓 --priority recommended

python3 core/cwb.py references validate "项目/我的项目/资料/参考集"
python3 core/cwb.py references serve "项目/我的项目/资料/参考集" --port 8167
```

`serve` 默认由系统选择空闲端口，终端打印实际地址；固定监听 `127.0.0.1`，Ctrl+C 退出。服务和网页不读取外部网址、不上传、不调用 Krita/ComfyUI、不写原图或创建缩略图缓存。缩略图按请求在内存生成，原图保持原字节；浏览器不支持的 TIFF 使用最长边2048像素的PNG观察副本，原图链接仍返回TIFF；PNG/JPEG/WebP/GIF/TIFF 解码及测量需要 **CLI 环境的可选 Pillow**，不影响 Krita 插件环境。帮助命令不导入 Pillow、不访问服务、不创建目录。

图片必须在资料目录内；目录内原图的相对路径可含中文和空格，使用 `/` 分隔，不允许 `..`、绝对路径或符号链接。来源网址、作者与权限未知时明确填 `unknown`。`meaning` 记录这张图与当前设计的关系、观察或联想，例如“比较帽体后垂与肩部宽度”；用途尚不明确可如实写“用途待探索”。标注是输入者的判断，不自动成为事实核验或艺术批准。

## 标注与原图变化

```bash
python3 core/cwb.py references annotate "项目/我的项目/资料/参考集" cape-01 \
  --meaning "已看侧背：帽体后聚拢，拟比较肩部体量；未导入" \
  --tag 披风 --tag 侧背 --priority recommended

# 原图确实被替换或修改后，明确重新测量；不会修改图像
python3 core/cwb.py references refresh "项目/我的项目/资料/参考集" cape-01
```

`annotate` 只改变提供的字段，`--tag` 替换标签列表，`--clear-tags` 清空；不改变稳定 ID 或文件路径。`add/annotate/refresh` 可传 `--expect-sha256 <数据集摘要>` 拒绝过期写入；使用持久锁和同目录原子替换，失败保留原数据。信任本地目录并避免非协作程序同时改文件，不承诺跨文件原子事务。

`validate` 核对文件存在、实际解码结果与摘要；原图变化会报错，不静默沿用旧宽高或选择。`refresh` 后浏览器重新读取，已改变图片的旧勾选失效，须重新选择。网页只读；长期标注保存在数据集，由 CLI 或文本编辑器维护。

## 数据格式

Schema 为 `cwb-reference-gallery-v1`；这是独立资料集格式，不改变项目主索引的 schema 2 或阶段 sidecar 2。完整可运行例子见[示例数据](<../../examples/reference_gallery/references.json>)。

| 位置 | 字段及职责 |
|---|---|
| 数据集 | `schema`、稳定 `id`、`title`、`description`、`records` |
| 每图身份 | 稳定 `id`、`title`、相对原图路径 `file` |
| 设计标注 | `meaning`、`tags`、`group`、`material` |
| 浏览优先级 | `priority`: `normal` / `recommended` / `parked`；仅为资料整理建议 |
| 来源 | `source.url` 原页面、`creator` 作者/机构、`rights` 使用限制、`retrieved_at` 获取日期；可选 `image_url` 原图网址 |
| 自动测量 | `image.width/height` 按 EXIF 校正后的像素宽高；`aspect_ratio = width / height`；`has_alpha` 是否有实际透明像素，含调色板透明PNG；`mime`、`bytes`、`sha256` |

页面显示原始宽高、约分比例和数值长宽比，可按横幅、近方形、竖幅及任意数据中的分组、标签、类型筛选。原图与来源链接、大图详情都从当前数据集生成；未知来源不生成链接。标注以文本显示，禁止作为 HTML 执行。只允许白名单网页资源、数据接口及按 ID 查找的登记图片，不暴露整个项目目录。

## 选中资料交给 Krita

网页勾选保存于该浏览器、该数据集 ID 的本地存储中，绑定图片 SHA256；不会反写数据集。点击“导出试拼清单”下载独立 JSON，携带数据集摘要和选中图片的完整标注。也可在命令行明确选择：

```bash
python3 core/cwb.py references shortlist "项目/我的项目/资料/参考集" \
  --id cape-01 --output "项目/我的项目/资料/试拼清单.json"
```

输出文件须不存在。清单 schema 为 `cwb-reference-shortlist-v1`，仅列出拟试用资料；执行者核对当前画布身份和本轮问题后，再按已有图层导入能力带入 Krita。勾选、推荐、下载清单均不等于已经导入、作者采纳或阶段批准；实际试拼反馈按02/04手册记录。

## 阶段02的可恢复确认快照

图库的日常标注不自动改变项目阶段。需要通过项目 CLI 登记02成果时，先在独立 `checkpoints/<对象>/<版本>/` 目录保存完整资料集（`references.json` 与其相对路径引用的全部原图）和实际网页截图，再登记：

```bash
python3 core/cwb.py project record "项目/我的项目" checkpoints/research/v1/references.json \
  --stage 2 --category final --kind checkpoint --object-id research --version v1 \
  --role research_gallery --preview checkpoints/research/v1/preview.png
```

`research_gallery` 仅用于02，独立于 KRA 文档；阶段批准须有同快照目录的可视预览，空图库不算成果。项目状态会核验数据集、全部引用原图与截图，变化或缺失使旧批准要求复核。登记本身不批准阶段；仍按项目规范取得绑定版本的真实人类判断。没有登记需求时直接用 Web 图库继续设计，不补建空 KRA。

## 公开仓库与本地数据

主干保存通用程序、格式、文档、测试与可运行的原创几何示例。真实角色资料集、第三方原图和标注保存在 `项目/<项目名>/`，遵守已有本地项目约定，不因预览功能而自动上传。公开保存其他数据集前，明确其隐私和素材授权。

离线回归见[参考图库测试](<../../tests/test_references.py>)。浏览器人工检查搜索、过滤、大图和清单；Krita 的实际导入与艺术质量另行验证。
