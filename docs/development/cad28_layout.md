# CAD-28：已有六图的离线设定页与受控矢量标注

## 状态与验收边界

本项只组织**用户已经有的主图、前/侧/背、两个装备拆解**，不调用模型、不生成三视图、不推断人体尺度，更不保证角色一致性。当前交付是离线版式/资源 bundle、人工 apply 准备与受限新建矢量层入口；不是 Krita 实机完成案例，**不关闭 #28**。

- 六张图片准备为六个独立透明 `paintlayer`，短文字/参考线/框/指向线准备为另一个 `vectorlayer`；不合并、删除或覆盖旧层。
- `<text>` 保留字符，不预先转 path/栅格。导入回读成功也只证明有限结构，不能证明 GUI 可改字或 `.kra` 重开。
- `layout fixture` 产物明确是**教学占位 PNG、非真实角色 fixture**。用户的六张真实素材尚未提供/验收。
- 没有执行安装、服务重启、真实 Krita/模型请求，也没有写入真实文档。以下真实 apply 需要另获授权，只能使用专用测试文档。

## 离线使用

所有输出目录须尚不存在、父目录已存在；不会覆盖已有 bundle。帮助不执行布局/插件/Pillow 模块，不联网、不创建缓存。只有 `render` 延迟导入外部 CLI Python 的可选 Pillow；插件不依赖它，不自动安装。

```bash
python3 core/cwb.py layout --help
# 教学占位案例，不是用户真实角色
python3 core/cwb.py layout fixture /tmp/cad28-teaching
python3 core/cwb.py layout plan /tmp/cad28-teaching/fixture.json
python3 core/cwb.py layout render /tmp/cad28-teaching/fixture.json /tmp/cad28-bundle
# 只输出人工执行请求序列，不发送请求，不存在自动 apply 子命令
python3 core/cwb.py layout prepare /tmp/cad28-bundle --document /absolute/dedicated-test.kra
```

已有图片用 `fixture` 生成的配置结构作模板，将 `fixture` 改为 `false` 并明确填写六张已有 PNG 路径、cell、人工锚点与 callout。`fixture=false` 仅是人工来源标记，不是版权/真实性证明。相对图片路径基于配置所在目录，不是进程 cwd。配置不接受未知字段、重复键、NaN/Infinity。

顶层字段恰为：

```json
{
  "schema": "cwb-layout-v1",
  "unit": "px",
  "canvas": [500, 420],
  "dpi": [300, 300],
  "fixture": true,
  "images": {
    "main": {"path":"main.png","cell":[10,30,100,160],"anchor_source":[50,8],"anchor_target":[60,38]},
    "front": {"path":"front.png","cell":[130,30,100,160],"anchor_source":[50,8],"anchor_target":[180,38]},
    "side": {"path":"side.png","cell":[250,30,100,160],"anchor_source":[50,8],"anchor_target":[300,38]},
    "back": {"path":"back.png","cell":[370,30,100,160],"anchor_source":[50,8],"anchor_target":[420,38]},
    "equipment1": {"path":"equipment1.png","cell":[10,230,100,160],"anchor_source":[50,8],"anchor_target":[60,238]},
    "equipment2": {"path":"equipment2.png","cell":[130,230,100,160],"anchor_source":[50,8],"anchor_target":[180,238]}
  },
  "references": {"head":38,"shoulder":65,"waist":110,"knee":155},
  "callouts": [
    {"text":"TEACHING PLACEHOLDER - NOT A CHARACTER","label":[10,220],"target":[60,260]},
    {"text":"Manual equipment review","label":[250,250],"target":[180,280]}
  ]
}
```

## 几何与资源契约

读取并消费 [CAD-14](<cad14_placement.md>) 的 [placement](<../../core/krita_cowork/placement.py>)，没有修改该模块或引入另一套采样算法。

- 坐标是画布像素、左上原点、x 右/y 下；cell 为 `[x,y,width,height]`，不重叠且完整在画布内。所有几何输入为严格整数，不截断 float/bool/字符串。未知单位拒绝。
- 每个原图按 CAD-14 `fit_canvas` 等比 contain 到 cell，**只缩不放、不裁切、不 alpha trim**；记录名义分数尺度及实际 `W/Sw,H/Sh`。前/侧/背必须有相同 cell 宽高、相同名义缩放及相同 cell 内目标锚点，否则拒绝，要求人工先准备共同像素尺度，不自动猜解剖比例。像素舍入仍可能产生不足 1px 的两轴比例差。
- `anchor_source` 是人手指定的源图边界坐标（例如头顶），`anchor_target` 是画布目标。最终整数平移为 `origin = target - floor(source_anchor * output_size / source_size)`；量化后真实连续边界锚点相对目标的误差以 `anchor_error_px` 两轴精确分数记录，均在 `[0,1)`，不是虚报零误差。
- 前/侧/背的头顶目标 y 必须等于 head 参考线；head/shoulder/waist/knee 依次向下且在统一视图区内。肩、腰、膝只是人工参考，不自动定位源图身体部位，仍需人校对。
- 最终缩放 PNG 通过 CAD-14 `resize_rgba` 的最近邻像素中心采样；保留所选像素 RGBA（含 Alpha=0 下 RGB）。生成 payload 再用 `mode=insert_at_offset` 以整数原点放置，不二次缩放；画布其余部分由新 paint 层保持透明。
- PNG 准入与 CAD-14 相同：每轴 32768、源 16Mi 像素、解码 64MiB、每图输出 4Mi 像素、PNG 32MiB/4096 chunks、估算工作 256MiB、每图预处理协作 5s；非交错、静态、至多8bit标准PNG。此处额外限制六图来源总字节 64MiB。总墙钟/RSS不是硬上限，磁盘/解码不能抢占。
- 每次只保留/解码一张图，跨图只保留来源身份与摘要，避免六图同时持有原图/解码缓冲。每张渲染的读取/PNG预检/解码/转换/采样/编码共享协作预算；空白cell面积不是采样分配，不错误套用输出像素预算。
- 受控 SVG 标注无嵌图；六 PNG 一直独立。`render` 输出的是独立缩放图片及标注源，不会伪造一张扁平 PNG 作为“可编辑设定页”。文字只检查字符/字号/锚点，不猜字体实际外接框或基线；是否溢出/替代字体仍留B门槛。

## bundle、来源和输出保护

bundle 保存原配置副本、每张原 PNG 原始字节、每张独立缩放 PNG、标注 SVG 源、布局 manifest。记录源绝对路径、SHA256、字节数、源/输出尺寸、cell、锚点、参考线、DPI、采样和最终坐标；标注 SVG 与缩放 PNG 也记录摘要。

目录遍历逐级 `dir_fd + O_DIRECTORY + O_NOFOLLOW`，普通文件以 `O_NOFOLLOW|O_NONBLOCK` 打开，拒绝软/硬链接别名、FIFO、设备和路径中的 `.`/`..`/重复分隔符。读取前后比较设备/inode/大小/mtime/ctime，并在输出前后回读来源摘要；图片预检使用钉住的普通文件 fd，不能再次通过未经检查路径解码。PNG 的元数据去除仍遵循 CAD-14；原始副本保留原文件字节，不冒称嵌入 ICC 已参与色彩管理。

输出使用新目录及 `O_EXCL` 文件；manifest 最后写入作为完成标记。磁盘失败可能留下**不完整新目录**，不自动删除、不覆盖重试；先人工检查，另选新目录。`prepare` 重读当前原始配置及六 PNG 重新计算布局，核对保留源、SVG、独立PNG与manifest，来源改变拒绝。它不是文件锁、签名或原子事务；并发写方始终存在竞态窗口，不能将 SHA 当安全回填凭据。

## 受控矢量 API（只新增层）

`POST /layer/vector` 必填：`svg`（**字符串**）、`name`、`target_document`、`canvas`、`dpi`、`dedicated_document: true`。可选 `parent_id`、`above`；未知字段拒绝（包括 #13 token）。JSON body 1..128KiB，SVG UTF-8最多64KiB、160元素、单行1..120字/共1600字、2..8个callout由布局工具限制。SVG数值有限，0..32768，字体1..96，线宽>0且<=16；不接受负数/越界。

- 只允许扁平 `svg/text/line/rect` 及逐元素属性白名单、十六进制颜色/none、简单字体名；不提供任意 SVG 上传/编辑旧形状。首版不需要 path，直接拒绝；箭头 marker、tspan 等也不承诺。
- 拒绝 DTD/entities/处理指令、脚本/事件、CSS/style/url、foreignObject/use/image、任何外部/本地 href、嵌套/未知命名空间/未知属性。文字由 XML 正常转义，原文字符保留。
- 所有 Krita API 仍通过 [server](<../../core/krita_cowork/server.py>) 既有 `MainThreadBridge` 分派；没有改变桥/超时取消语义/监听安全边界。此接口不是强文档锁。
- 目标必须是人工确认的**已保存专用活动 .kra 的确切绝对路径**；不切换文档。回调每次新写前及回读后检查活动对象、文件路径、画布、DPI稳定。只能使用本回调稳定活动文档的资源管理器；不把 #13 token 作为安全证明。
- 仅使用 callable `Document.createVectorLayer(name)`，核对 `type()==vectorlayer`、`addShapesFromSvg`/`shapes` 能力；绝不以 `createNode('vectorlayer')` fallback。创建不等于挂树。
- parent 默认 root 容器，显式只能 group；root 不能成为显式目标。检查 `addChildNode` bool，并从真实树回读父子、type与above顺序，不重写整树。`above` 共用守卫**仅新增 vectorlayer**，故旧paint/group创建也可定位在同级vector之上。`props` **仅新增vector的opacity/blending_mode能力探测与写后回读**；Alpha Lock仍仅paint，root/其他类型不放开。

### points / DPI 映射

Shape几何单位是points，即1/72inch；不能当作画布px，也不能硬设72/96DPI。标注根显式 `width=Cw*72/DPI pt`、`height=Ch*72/DPI pt`、`viewBox="0 0 Cw Ch"`；子元素使用画布px用户坐标。当前只接受明确相等的x/y DPI（1..2400），要求目标有可调用 `Document.xRes()/yRes()`，按公开契约的pixels per inch逐轴匹配请求（容忍1e-8绝对/1e-10相对浮点误差）；缺任一能力或非方形DPI显式拒绝，不猜测绑定或图像分辨率。不能以标量 `resolution()` 替代：官方实现只将x分辨率四舍五入成整数，既不证明y也损失精度。

这仅明确序列化物理映射意图。官方parser还使用document/image resolution，**实际300DPI下文字、参考线与paint锚点是否齐准仍必须实测**，离线分数/单位测试不是落点证据。

### 返回值与部分失败

空 `addShapesFromSvg` 返回不视为成功；非空也不等于完整导入。入口回读 `shapes()`，以 `Shape.toSvg(False, False)` 保留文本模式导出，保守核对每个扁平元素的tag/文字/属性多重集，不按顶层形状数量等于元素数量断言。

**严格核对会产生有意的假阴性**：Krita若将line改写为path、添加变换/样式/重写数值格式，即使画面正确也会返回 `partial_or_unverified`，不能据此声称导入丢失或要求自动重试。当前不实现通用SVG语义等价器。无法证明完整保留即显式报告未验证，留待专用测试文档人工检查。

创建/挂树/导入/回读/刷新任一步失败均可能留下新空层/部分标注。HTTP400 payload含 `id`（能读时）、`failed`、`status=partial_or_unverified`、`rolled_back=false`、`read_layers_required=true`；不自动删层、回滚或重试。完全通过严格回读仅返回 `status=structural_readback_only`、实际层ID/type/parent/顶层shape数及points映射，`krita_editing_verified=false`。桥超时仍不取消已经运行的回调。

`prepare` 只打印6次 `/layer/add` 和1次 `/layer/vector` 的人工请求序列，绝不发送。既有PNG接口本身没有目标文档守卫，不能将离线序列当原子apply；获授权后也须每步人工确认专用活动文档、核对图层树，失败停止并回读，不盲目重试。它不新建、保存或接管用户作品。

## 官方依据与真实未知

本实现消费派发前的官方来源静态核验，并只读补查了[6.0 Document.h](https://raw.githubusercontent.com/KDE/krita/krita/6.0/libs/libkis/Document.h)与下列Document.cpp，确认双轴getter的pixels per inch单位及标量resolution的局限；没有用本机运行替代未知。公开归档API不是本机6.0.4绑定保证，6.0分支也是可变分支：

- [VectorLayer API](https://api.kde.org/legacy/krita/html/classVectorLayer.html)、[6.0 Document.cpp](https://raw.githubusercontent.com/KDE/krita/krita/6.0/libs/libkis/Document.cpp)：专用创建器与泛型Node包装器区别。
- [6.0 VectorLayer.cpp](https://raw.githubusercontent.com/KDE/krita/krita/6.0/libs/libkis/VectorLayer.cpp)、[Shape.h](https://raw.githubusercontent.com/KDE/krita/krita/6.0/libs/libkis/Shape.h)：字符串导入、空list、优先活动文档资源、points与文本导出默认参数。
- [6.0 Node.cpp](https://raw.githubusercontent.com/KDE/krita/krita/6.0/libs/libkis/Node.cpp)、[SvgParser.cpp](https://raw.githubusercontent.com/KDE/krita/krita/6.0/libs/flake/svg/SvgParser.cpp)：挂树与SVG解析静态依据。
- [矢量层手册](https://docs.krita.org/en/reference_manual/layers_and_masks/vector_layers.html)、[文字工具](https://docs.krita.org/en/reference_manual/tools/text.html)、[KRA格式](https://docs.krita.org/en/general_concepts/file_formats/file_kra.html)：能力说明，不是这次输入/本机保存重开的验收。

## 实际离线映射与 B 门槛

[布局测试](<../../tests/test_layout.py>) 覆盖六槽、同cell/名义尺度、人工锚点/参考线、300DPI points、XML转义、PNG准入/CRC/资源、透明度/隐藏RGB、原图及SVG源保留、链接/FIFO/别名/覆盖/源改变、篡改/不完整bundle、确定性及帮助不执行模块/联网/创建缓存。

[矢量测试](<../../tests/test_vector.py>) 覆盖受控SVG攻击/元素数/字符/数值边界、144/300DPI映射、不支持API/目标/颜色类型拒绝、root/group/above与旧树保护、空list/部分导入/抛错/挂树/回读/刷新失败、活动文档变化、最小above/props扩展、内存HTTP handler与既有bridge分派。测试替身不证明真实Krita/Qt行为。

B门槛全部未验，必须另获授权后使用专用测试文档：

1. 明确运行插件来源、本机createVectorLayer/绑定/xRes与yRes能力；测试root和嵌套组、稳定活动目标与错误目标拒绝。
2. 用户授权的六张真实图、共同尺度和人工头顶锚点，实际300DPI量测paint/文字/线条落点、透明度与RGBA通道；非方形DPI首版不支持。
3. 在Krita GUI真正改中文/拉丁文字内容、字体/字号/基线，移动参考线与指向线端点；确认文字未变path/栅格。记录字体替代，不以SVG源/形状数量/视觉相似替代。
4. 另存专用 `.kra`、关闭仅该测试文档、重开后逐层核对类型/透明度，重新改字与线；旧会话UUID不作为安全续接凭据。
5. 检查用户原层/原作品从未改变。只有完成真实六图案例与可编辑保存重开，才讨论issue关闭。
