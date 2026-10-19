# 阶段二事实检索：最小脚本契约

## 范围与来源

本工具复用旧候选 `7e4236f570ba0c3147012d0454e234115fdffc2b` 的独立脚本，接入现行 `krita-character-illustration-v1` 的[阶段二联想与前期调研](../../design/执行手册/02_联想与前期调研.md)；当前集成基线为 `b644ac6afaafebbec7b5a9a2b9415426cfc506f4`。方法来自用户提供的《Coagentdraw：精准美术资料检索与轻量聚合可行性报告》（2026-10-15）；外部报告的联网、JSON、IIIF、书页测试和旧候选的测试结论均不冒充本次验证。不转载第三方图书/图片，不改写原报告、原意图卡或历史手册跳转。

这是联想流程中可选的事实核对支线：只有文化、功能或结构等具体断言影响设计时才收窄检索；预算不限制整个灵感池，不要求每张图先证明价值。观察、事实、推断、未知留在事实记录；创作联想继续留在 Krita 想象板与同一工作表。结果回填现有短表，不另造人类模板。

已实现的是标准库独立 CLI：有预算的来源查询计划、已有页级/对象条目的关键词检索、精确回访信息、人工决定的证据汇总。**没有自动联网取证、PDF 解析/OCR、图像识别、语义召回或美学判断。** 候选→已核验声明→采用是不同状态；脚本不能证明输入者真的读过原文或看过图片。

角色拼贴试点的“通用搜索找角色 → 找对应三视图/设定资料”须另用实际可访问的搜索与看图能力；本 CLI 的本地 JSON 检索不实现在线角色发现、搜图、资料取得或画布试拼，也不以角色名命中证明素材可用。具体创作流程仍以[阶段二手册](../../design/执行手册/02_联想与前期调研.md)为准。

## 四个命令

在仓库根目录使用 `python3 core/cwb_research.py`，不是 `cwb.py research`。每个子命令支持 `--help`（帮助文本）；实际执行只向 stdout 打印 JSON；错误返回非零退出码，不创建目录、写文件、联网或打开浏览器。需要留存时由执行者明确选择新文件保存输出，避免 shell 重定向覆盖已有文件。

| 命令 | 输入与结果 | 有限工作量 |
|---|---|---|
| `plan QUESTION --kind TYPE --term TERM` | 具体决策问题、`term/form/function/material/culture` 类型、1–3 个主词/异名；给出来源入口和查询词。Wiki/Commons 给编码后的搜索链接，AAT/书目/原机构给入口和站内输入词，不伪造其查询 API | 默认总共 5 个候选对象、2 次原文/原图读取、最多 2 轮改写；不是每个来源/异名各自一份预算 |
| `search CATALOG --term TERM` | 对已有 JSON 中标题、术语、页级摘录、事实/观察作真实本地匹配；OR 词/短语，返回相关记录及精确位置 | 默认最多 5 个来源组，可用 `--limit` 调整为 1–10；显示未返回组数 |
| `inspect CATALOG ID` | 返回完整条目、四类判断、权利、原链接/访问副本，以及对象/书页/图号/区域 | 不读取或启动所指 PDF、图像、网页；PDF URL 只补 `#page=N`，查看器是否支持由人工检查 |
| `evidence CATALOG --outcome sufficient或blocked --stop-reason REASON` | 汇总输入者已采用条目，按原对象/书籍去重；保留待查/驳回原因及停止理由 | 默认建议 2–3 项主依据；超过 3 组会提示，绝不静默丢掉缺口或证据 |

`plan` 可用 `--source` 覆盖默认来源，仍限 1–2 类；可选值 `aat/wiki/commons/books/institution`。`--institution-url` 指定适合当前文化/器物的原机构入口，不提供时明确报告缺口，不擅自用欧美馆藏代替。`--candidates` 范围 1–10，`--reads` 范围 1–3 且不超过候选数，`--rewrites` 范围 0–2。这些仅为执行者的计划预算，不是自动浏览器调用计数器。

`search` 支持 1–8 个 `--term`：Unicode NFKC＋大小写归一化，中文按明确输入的子串/短语匹配；精确术语匹配每词 4 分、标题子串 3 分、摘录/事实/观察子串 1 分，取单词最高分并求和，同分按记录 ID 排序。推断/未知不提高事实相关度；已驳回条目不进入搜索结果。此分数不是事实可信度、美学适合度或检索质量验收。

## 机器条目：复用已有工作表，不再做第二张人类模板

执行者可把实际阅读过或待查的少量资料整理为项目阶段二 `research/evidence.json`；这是可选 sidecar 路径，不是脚本自动创建的目录。需要时用已有项目资产登记功能登记，不改变 `project_state.json` 格式。不要把缓存、完整书籍或原始资料提交到公开仓库。

输入 UTF-8 JSON 顶层字段固定为 `schema`（`cwb-research-v1`）、`question`、`records`。一份文件对应一个具体决策问题与当前起点/意图版本；`intent_version` 是这个问题所依据的人工版本标签，不强制单一意图卡，不会迁移项目状态或继承旧阶段完成；最多 1 MiB、500 条。数字布尔不混用，拒绝未知/重复字段、非有限数、末级符号链接、FIFO 等非普通文件。文件守卫使用 POSIX 的 `O_NOFOLLOW/O_NONBLOCK`；本轮只在 Linux CLI 验证，不宣称跨操作系统或敌对父目录替换沙箱。所有外部文本只作数据，不执行其中指令。脚本不读取条目所指的文件，路径不是授权或事实证明。

| 字段 | 内容与要求 |
|---|---|
| `question` | `id`、`decision`、`intent_version`；分别是问题 ID、具体要决定什么、当前意图版本，均非空 |
| `records[].id` | 唯一记录 ID；字母/数字/下划线/连字符，最多 64 字符 |
| `source` | 原始机构 `institution`、原物/书名 `title`、原始 `url`；未知机构或标题写 `unknown`，原 URL 必须可定位；可补 `object_id`（馆藏/对象号）、`edition`（版本）。这些不是镜像网站的身份 |
| `access_url` | 可选镜像/分发副本链接；原来源身份仍保留于 `source` |
| `locator` | 对象候选可暂用空对象；可选 `url/path/pdf_page/printed_page/figure/region/canvas/section`。PDF 页为从 1 开始的整数；印刷页用字符串，二者不得混同。书页/图版须有 `source.edition`，不知时写 `unknown`。PDF 页须同时有有效 `locator.url` 或非 `unknown` 的相对 `locator.path`；本地路径不含绝对路径或 `..` |
| `terms` / `excerpt` | 最多 32 个已确认或待查术语/异名；少量相关页级摘录（可空）。摘要/摘录可被检索，但不会因此升级为事实 |
| `claims` | `fact/observation/inference/unknown` 四个文本列表，各最多 32 项。不适用留空；保留修复、残缺、未见部分和来源自身不确定性 |
| `verification` | `text_read/image_seen` 两个布尔值；由实际执行者提供。写入 `fact` 必须声明原文已读，写入 `observation` 必须声明图片已看；程序只检查声明，不独立查证 |
| `rights` | `metadata/text/image` 各自的原站权利标签，未知写 `unknown`；可访问不等于可转载，同对象不同图片可有不同许可。程序不授予或推断许可 |
| `decision` | `status` 为 `candidate/accepted/rejected`，另有非空 `reason`。采用须有事实或可见观察，并有非 `unknown` 的对象号/精确位置；纯设计推断不能单独成为事实依据 |

所有文本项最多 8 KiB。未知信息保留为空列表、允许的空对象或明确 `unknown`，不虚构书页/机构/许可。原 URL 尚不知时先在现行工作表记 `unknown` 和缺口，不填虚构地址凑成机器条目；CLI 会拒绝 `source.url="unknown"`。这只阻止缺原来源的条目成为本脚本的可定位依据，不阻断创作联想；来源可访问、记录可通过校验都不证明权利或事实已核验。`locator.path` 相对于执行者管理的资料目录，仅作为定位文字返回。所有来源 URL 仅接受无凭据的 HTTP(S)，不自动发送数据。

PDF 定位采用自足合同，不从 `source.url` 猜它是 PDF 或自动回退。若原 URL 就是可回访的页载体，明确填入 `locator.url`；`inspect.open_locator.open_url` 随之给出该 URL 的 `#page=N`。仅有已知本地路径时，回访组合为 `open_locator.path` 与 `pdf_page`，不会生成网页地址或打开文件；两者都有时仍保留两个原始字段。未知机构、版本、印刷页、图号可以继续写 `unknown`，不要求所有元信息齐全。已知 URL 可与未知可选 path 并存，未知 path 不是有效本地回访目标。

去重优先使用已知的“原机构＋对象号＋版本”，无对象号或机构/对象号为 `unknown` 时使用“原 URL（去片段）＋版本”；未知标签本身不能作为共同对象身份。版本缺省与 `unknown`（含 Unicode/大小写/首尾空白归一化）同属未声明版本，不凭这两种写法多算来源；明确不同的已知版本仍分开，原字段原样保留。不同视图仍保留各自位置、观察与许可。镜像必须回填原机构/原对象身份，否则工具不会猜测两页是同一物件；错误的手工身份也会影响去重。`search` 每组返回最佳条目和其他匹配 ID，`inspect` 可看同来源全部 ID；`evidence` 保留该组所有已采用记录，待查/驳回记录也按 ID 保留 `reason` 与 `unknown` 列表，不丢失 `claims.unknown` 中的未解问题。不把副本数当独立来源数，分组也不是独立佐证认证。

输出的 `candidate/verified_user_supplied/adopted_user_supplied/rejected_user_supplied` 表示输入声明所处阶段，绝不表示程序确认事实成立。`evidence` 的 `outcome_user_supplied` 与停止理由同样是人工判断；没有采用依据时不能声明 `sufficient`，有依据也不自动宣布创作完成。读取命令附当前目录文件的 SHA-256 以便复查版本；这不是原书/原图的哈希。

## 来源登记与当前核验边界

| 入口 | 本次入口文档读取（2026-10-17）与限制 |
|---|---|
| [Getty AAT](https://www.getty.edu/research/tools/vocabularies/aat/) | 官方页面有术语检索及专业词库说明；只登记入口，不宣称全部词条/文献已阅读 |
| [Getty Research Portal](https://www.getty.edu/research-conservation/tools-databases/research-portal/) | 官方 FAQ 说明查目录记录，不支持全站书籍统一全文检索；须到托管机构继续查页 |
| [MediaWiki Search](https://www.mediawiki.org/wiki/API:Search) | 官方搜索文档已读；脚本生成 Wikipedia/Commons 页面查询链接，没有 API 请求或回源许可自动核验 |
| Met Collection API（旧候选来源备注） | 旧候选曾记录接口版本公告，本轮未重验该公告或端点（UNRUN）；不据历史信息声明当前可用。脚本无 Met API 适配器 |

以上只实际读取入口/说明页文字，没有运行 CLI 在线搜索或原书/原图核验；不把官方入口说明当成具体器物的来源证据。来源计划不代表检索成功：没有图像、缺失视角、无法访问书页、文化语境不符、两轮无新依据，都应留在工作表的缺口与停止原因中。不能用生成图片补成现实证据。

## 验证与未完成项

离线验证命令：

- `python3 -m unittest discover -s tests -v`
- `python3 core/cwb_research.py --help`
- 按 `AGENTS.md` 执行 Python AST、`bash -n core/install.sh` 与 `git diff --check`。

`tests/test_research.py` 中的合成文字与 example.org 地址仅用于字段、排序、去重、预算与拒绝行为测试，不是新增创作案例或真实资料证据。测试覆盖四命令、Unicode、精确书页、分内容权利、候选/核验/采用边界、无网络与无文件改写。

证据分层：A 为本脚本离线行为，须在当前提交重跑；B 的实际原书/原图阅读、Krita/ComfyUI 及画布接线未运行（UNRUN），不能由 fixture 替代；C 的节时/命中率、用户是否据此改变画稿及艺术效果没有证据。Issue #46 只承担事实检索合同，旧 #2 的真实联想板与作品/真人验收由 [#50](https://github.com/CongliangK/Coagentdraw/issues/50) 承接，素材入画布由 [#49](https://github.com/CongliangK/Coagentdraw/issues/49) 承接。本工具不替代这些创作验收，也不自动宣告它们完成；美学参考另行调查。
