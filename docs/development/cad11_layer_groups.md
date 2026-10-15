# CAD-11：图层组 API 与 CLI

## 契约

- `POST /layer/group`：必填 `name`；可选 `parent_id`、`above`。只创建 `groupLayer`，不移动/合并已有节点。返回 `id`、实际 `name`、`type`（Krita 返回 `grouplayer`）、`parent_id`。
- `POST /layer/add`：原有 `png_path`、`name`、`above`、`offset_x`、`offset_y` 不变；新增 `parent_id`。只创建 `paintLayer`；返回原字段及 `parent_id`。
- `parent_id` 省略或为 JSON `null` 时使用文档根容器；显式 ID 必须指向当前文档中的组。显式根 ID、paintLayer、其他类型均拒绝，空字符串不是默认根。
- `above` 省略或为 `null` 时使用 Krita 默认插入位置；指定时必须是父级的**直接子节点**，且为 groupLayer/paintLayer。不能跨组、不能指向父级自身或根。不根据 above 隐式推断父级。
- 同级顺序按 Krita `childNodes()` 的从下到上顺序核对：新节点必须紧随 above。界面图层面板通常反向展示该列表。
- ID 保留 `uniqueId().toString()` 协议，接受去花括号 UUID 与旧 QUuid 表示。重开文档后应重新 GET `/layers`，不要依赖旧 ID。
- 参数/状态错误 HTTP 400，失效 ID 404，主线程桥超时 503。所有 Krita 调用仍通过主线程桥；网络监听地址未改动。
- 保留 RGBA/U8 守卫、RGBA8888 逐行去填充、offset 越界检查、先挂树再写像素。检查 createNode 类型、addChildNode/setPixelData 返回值；创建后从实际文档树回读父子和 above 顺序，不只相信返回对象。
- 失败不自动删除任何节点：若挂树成功后写像素失败，可能留下一个新空层，错误包含该层 ID。树核验失败也可能留下新层；请重新读取 `/layers` 人工检查，不盲目重试。没有实现自动回滚、移动、删除或其他节点类型。

## CLI 示例

```bash
python3 core/cwb.py group 剪影
# 使用上一步返回的 id：
python3 core/cwb.py group 结构 --parent '<剪影组id>'
python3 core/cwb.py add /绝对路径/test.png 明暗 --parent '<结构组id>'
python3 core/cwb.py group 配色 --parent '<结构组id>' --above '<明暗层id>'
python3 core/cwb.py layers
```

`group --help`、`add --help` 和顶层 `--help` 均离线、无请求、无缓存目录创建。此功能不扩展 ComfyUI/云端/模型工作流。

## 验证状态与真实验收步骤

离线单元测试覆盖两级组与 PNG 参数流、同级顺序、默认根、旧 ID 表示、父节点类型/根保护/失效 ID/跨组 above、RGBA/U8、图像/offset 守卫、挂树返回失败/游离节点/错父级/错顺序、像素失败、路由主线程桥与 CLI 请求/帮助无副作用。**替身不证明真实 Krita、Qt 像素、图层面板或 .kra 重开行为。**

真实环境验收仍须在用户授权后，用专用测试文档执行，不能修改现有作品：

1. 通过已安装、已明确加载本提交插件的 Krita 6 / Python 3.13 / PyQt6，确认 ping、docs 和 layers；不要仅因 HTTP 可通就认定运行的是本提交。
2. 新建 RGBA/U8 测试文档，创建外组“剪影”和内组“结构”；导入小 PNG 为内组的“明暗”，再创建“配色”同级定位测试。
3. 对照 GET `/layers` 与图层面板记录实际父子关系、顺序和 PNG 可见像素。另测根保护、paintLayer 父级及跨组 above 拒绝。
4. 将测试文档另存到检出外专用 `.kra`；在 Krita 中关闭并重新打开该测试文件，重新查询图层树和 ID，核对两级组、PNG 像素和层级仍保留。
5. 证据存于 `/tmp/coagentdraw-orchestrate-20261015/`（截图/树响应/版本/插件加载来源/保存与重开结果）。未完成这组实测之前，只能报告离线实现与测试通过，不能声称 Issue 实机验收通过。

本次实现阶段不安装插件、不重启 Krita、不在线修改用户作品，故上述真实验收未执行。
