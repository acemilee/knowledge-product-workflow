---
name: kp-diagram-designer
description: 将知识教程中的教学关系转化为受控 D2/SVG 或宿主生图插图，并输出 PDF/PNG 双格式资产。
user-invocable: true
metadata:
  version: "1.4.2"
---

# KP Diagram Designer

## v1.4.1 首次执行

首次消费当前diagram-template及真实渲染约束，不从旧图猜group/配色/标签字段。正文使用语义图名，图号由出版层生成。图在同小节可浮动/由后续文字回填，图与图题保持关系；高图导致前页大白时重新设计布局或受控排版，不缩字到可读性下限以下，也不凭“块完整”签通过。

## 政策2任务与正文位置

优先读共享契约政策2节。消费当前内容计划的create图任务，不以主要asset_decision决定是否有图；diagram-template --task-id绑定身份并给当前paragraph_anchors，after_anchor/before_anchor由作者受控细化到草稿计划。普通步骤默认列表，但解释一次调用背后隐含阶段的process_overview可以准入：政策2/风格2、hidden_mechanism或synthesis、三至六节点冻结链，使用有限overview规格，不伪造分支。图按读者需要的位置插入，不一律小节末尾；未登记、过期、相同视角重复或缺锚点停止。图型/后端与批准任务相同，未知费用保持unavailable，每任务累计两次不因换ID/颜色/位置清空。

## 用途与触发条件

当读者需要理解流程、分层架构、对比关系、时序或生命周期时使用。它交付可嵌入 PDF 的 PDF/PNG 图形资产，不交付 `.vsdx`，也不把字符画当作插图。

## 前置工件及状态

- 当前章节合约已由批准蓝图派生。
- 当前章节源稿位于该书稿工作区内。受控图片引用由 `kp insert-diagram-reference` 从已填写的语义输入创建，不由模型手写。
- 本机必须有 `diagram_browser.available: true`，用于离线转换受控 PDF/PNG；D2 图型另需 `d2.available: true`，固定 SVG 图型无需 D2。缺少所需工具时停止并报告，不下载、不联网、不回退 ASCII 图。

## 允许读取与写入

- 读取：当前章节源稿、`kp diagram-schema` 输出、当前图表模板及其上下文。
- 写入：仅当前工作区内的图表语义输入 JSON。内部 `runs/diagrams/` 和 `assets/figures/` 由 CLI 写入。

## v1.1 编排工作单

先阅读 `references/orchestration-contract.md`。这是条件 Skill：仅在当前章节需要解释流程、架构、对比、时序或生命周期关系时由章节作者租约内调用。它只产出版本绑定的图表语义输入和受控 D2/SVG 资产，不直接改写正文、合约、质量报告或交付物；正文引用仍由作者通过 `kp insert-diagram-reference` 受控写入。最终图文位置由作者按小节教学顺序确定；调整已有引用位置后重跑同源稿的编辑证明与图资产，再做成页复核。

## 必要步骤与条件分支

1. 先确定教学任务、精确标签、关系、证据边界和正文位置，再按图型排版。D2 承担 flow/sequence/lifecycle；v6 的 layered_architecture/comparison/nested_structure/card_matrix/stage_flow 使用受控 SVG 模板。固定 SVG 图型不接受 node.group，分层关系须在节点及合法连接中表达；不为渲染丢失关系或更改已批准图型。不要把普通列表硬画成图，先用 `kp diagram-schema` 查看约束。
2. 先运行 `kp diagram-template <章节号> --workspace <workspace> --chapter-source <chapter-source.md> --diagram-id <diagram-id>`，只填写模板中的语义字段：图表 ID、类型、方向、图注、读者结论、节点、分组和边。图注只能是无编号的语义标题，禁止写“图 1”或“Figure 1”；节点最多 14 个、每个节点文字最多 72 字符、连线标签最多 56 字符。优先使用能与正文共同排版的紧凑关系图；不要为普通步骤列表生成过长的纵向流程图。四个及以上节点的右向 `flow` 图由 CLI 自动排为两行，关系含义应写入节点和 `reader_conclusion`，不要依赖边标签承载关键教学信息。节点标签使用完整中文，不使用随机配色、装饰元素或内部 Claim。
3. 使用已填写的输入运行 `kp insert-diagram-reference <章节号> <diagram-id> --workspace <workspace> --from <chapter-source.md> --diagram-input <diagram-input.json>`。CLI 写入最终图注和唯一 PDF 引用，模型不得手写该 Markdown 行。
4. 语义/图注/引用冻结后，运行`kp submit-diagram`生成D2/SVG双格式资产；宿主生图按下节预约/提交/风格检查。CLI绑定当前源稿/合约/主题/资产，标明真实renderer。资产就绪后作者最终定稿、结构预检，对同源执行developmental-check和humanize-check再提交章节。宿主文字/箭头错误或风格漂移必须阻断，不能因好看放行。
5. 图表、正文或合约变化后按影响范围重建：语义/风格不变的合格图只重绑，不再生成；图变则重做受影响资产/风格证明，随后最终两份正文证明及章节合规。最终PDF另有当前版本的成页/人工门，不能把原图或无看图能力当作通过。

## 禁止事项

- 不输出 `┌ ┐ └ ┘ │ ─` 等读者可见 ASCII 字符画，代码示例和明确的终端输出除外。
- 不在图注、替代文本或正文手写图号，也不以长文本、超密节点或缩小字体规避可读性合同。
- 不插入远程图片、SVG、绝对路径、工作区外资产、原始 TeX/HTML 或无图注图片。
- 不填写或推导 SHA-256、章节号、Schema 版本、主题、资产路径、内部 Spec、清单或 D2 源。
- 不直接运行 D2、不以 PNG/SVG 代替受控 PDF 引用、不搜索源码、安装目录或其他工作区来恢复失败。
- 不把内部 Spec、D2 源、运行路径、哈希或调试输出复制到读者正文。

## 确定性验收命令

`kp submit-diagram` 必须成功生成 `assets/figures/<id>.pdf` 和 `.png`；随后 `kp submit-chapter` 与 `kp validate` 必须接受当前图表绑定。

## 失败恢复

出现失败时只读取当前工作区 `runs/diagnostics/submit-diagram-chapter-XX.json`、模板、上下文和指定章节源稿。按诊断中的 `next_command` 恢复；不得尝试字段名、手算哈希、手工移动资产或跨目录搜索。

## 人工审批点

图表改变技术结论、架构边界或数据含义时，先请章节作者和用户确认；图表仅能解释已批准正文，不能凭空添加结论。

## 用户交互边界

只向章节作者和根 Skill 提供图表结论与修改影响。不得让用户处理 D2、资产路径、模板、哈希或图表提交命令。

## 下游交接条件

最终资产交章节合规与出版者，不再改签名源稿。第四步是在成书页核验图字、箭头、图注、裁切和缩放；图字过小优先拆图，不靠高分辨率掩盖。只改品牌色时由出版更新重渲染图样式，保持正文和章节证明。

## v1.3.1 受控宿主生图

先读`kp illustration-schema`。母版与有限三图族规格在本包assets/illustrations；CLI也随正式wheel携带同hash资源。使用CLI返回的当前工作区参考，不依赖开发者work/tmp路径。架构图用分组卡片，层级图用一致腰线纵向层，流程图用有限条件/分支/回路；完整Excalidraw不冒充正式支持，API/MCP生图接入不在本工作流范围。

消费共享契约v1.3.2宿主路由：仅根Skill已核实Codex订阅＋GPT＋真实原生工具资格且Brief确认AI方案时填写host-image；其他宿主填写vector，不调用外部生图服务。采用host-image后仍可把精确数值/属性交原生表格或vector。每图显式backend，不靠模板默认值覆盖批准方案；政策2按批准任务N/2N或已有消费C＋R控制，每任务两次；仅旧政策1沿用6槽×2次。

1. 沿用diagram-template/insert-diagram-reference，填写backend=host-image和admission：当前内容计划的reader_job、视觉增益、family/information_form、证据边界、计划图幅、required与替代形式。任务字段一致，hierarchy方向必须down。精确属性用原生表格，普通线性操作用列表。确认当前冻结出版风格版本和真实参考能力；风格2首次两参考、修复三输入，process_overview只支持政策2/风格2；无能力就明确选择已支持形式，不虚构同等质量。
2. 同次冻结`kp freeze-illustration-budget --by <已确认身份>`默认精简预算；用`kp illustration-preflight <n> --from <输入> --chapter-source <源稿> --supports-reference`取得单次凭证、最小可见图文提示词和style参考。精确指定模型时必须匹配宿主实际回执；原生工具未知则记录unavailable，不宣称指定image-2.5。目标尺寸/quality只是提示词目标，实际像素另验。
3. 缓存失败不得复用为合格资产。新调用前`kp record-illustration-call <id> --state dispatched`，调用当前宿主生图工具仅一次，复制实际返回PNG到本工作区；再以completed和--image记录，费用可得才填actual，未知保持unavailable。缓存命中不发送；原生工具不得隐式重试，订阅额度/限流错误立即停止本轮生图。长请求每60秒以内反馈，300秒总等待无结果则停止本地等待，记录unknown保留预约，先查是否返回/取消结果，不盲重发。
4. `kp submit-illustration <n> --from <语义> --chapter-source <源稿> --image <实际PNG> --call-id <id>`派生PDF/PNG与不可伪造绑定，renderer明确host-image；PDF包装不变成矢量，不伪写字体文件或字号。失败候选和成功源图保留，新版未通过前不能覆盖其调用证据。
5. 交视觉审阅者用illustration-style-template/预算/record-illustration-style-check检查当前图。wrong字、漏标签、错边、未知装饰/字体漂移/品牌漂移都不能放行。明确失败可在原槽位用--repair-reason一次纠正；候选是edit_target、母版是style，标签/边不变。次数用尽阻断并返回选择，不用换ID/移章/换模型/换色清空计数。

资产就绪后作者最终定稿、生成两份同源证明、提交章节。已提交正文仅纯展示修复可由出版者委派，不能取得正文写权限；内容/标签/关系变更先amend-chapter。预算发现与恢复用illustration-budget-status及当前受控诊断，不能搜其他项目或手算hash。

## v1.3.2 配色与封面

出版预览同次确认风格2与辅助色策略：warm-focus/balanced/cool-structure，品牌强调保持用户主色；同概念跨图同色，不能随机改字形/图族。vector保持本图型画幅；host-image采用中性母版＋当前配色卡，首次两参考、纠正另带edit_target共三输入，preflight须声明reference_capacity至少3；型号/费用未知如实记录。

图级展示输入包含presentation的node/group颜色和标准概念映射，不接收自由CSS/任意HEX；全书概念改色通过出版更新illustration_category_colors确认，受影响图用update-diagram-presentation取得候选。候选检查使用illustration-style-template --candidate-call-id及观察输入candidate_call_id，failed不能覆盖当前；检查后activate-illustration-candidate切换，uncertain只允许受控试排并保留最终人工待决。新命令不释放作者租约，源稿变化仍先amend-chapter。调用completed只代表返回文件，不等同风格通过。

新封面选择cover_layout_revision=2；中文梗概必需，英文/最多四项学习重点/内容说明可选。Brief阶段提炼梗概，蓝图批准后再补学习重点，终稿核对后通过出版更新锁定；不在渲染时补写，不新增每图审批。封面日期仅年月、无语言栏/样稿徽标/开发提示，个人署名仅在已确认copyright_line出现一次；正文页脚独立。旧书缺字段布局1不静默升级。

旧政策1预算及历史保留；新政策2图数由批准教学任务推导，不设每章/全书图数配额；无消费书N个实际host-image制作任务最多2N次，已有消费修订按C＋R。每任务最多两次、串行，失败/超时/换色不重置。资产/成页/跨图核验共同保证可交付结果，无效量测转人工；不能保证随机生成每次成功。当前母版仍为候选时，明确只在隔离验收使用，正式发布需实际用户确认。

风格2资产量测revision2按实际PNG宽与冻结图幅折算可见笔画pt，低于9pt阻断；必须观察/量测最小说明和连线，不能只量较大节点。多行框和不可可靠测量项保留uncertain，成页另核。AI生图发送次数耗尽时停止该生图分支，不能换ID、绕过门或默认切换后端；这不限制文本或vector核验。

## v1.3.4 修正0.7：受控素材与确定性文字候选

纯vector路线保持。已在Brief明确选择host-image且当前任务为三至五阶段相邻stage_flow/right时，可用material_composition=stage_flow，让原生工具只生产无字、无箭头的素材，文字/关系/配色由现SVG层确定生成。先查diagram-schema与illustration-preflight-schema；不支持图族明确拒绝，不换task重置C+R。

发送前preferred字体、实际字体资源与两行标签容量会检查，失败无预约/发送。原件completed后用record-illustration-call的material-origin及source-evidence记录声明和原工具/会话回执；文件哈希仅绑定所提供证据，不能独立证明AI来源。synthetic仅作本地样品，绝不冒称实际AI。

submit-illustration从原任务、原completed调用、原PNG派生host-image+svg候选，保留原件、失败历史与全部消费。新候选走九项风格/成页检查；原件、来源、字体、图义或色义变化后重新核当前绑定。确定性文字/关系失败先修SVG，不能为它消耗AI纠正。只有素材确有缺陷且原任务还有批准次数才按现C+R纠正。

activate-illustration-candidate对混合候选要求当前真实本人响应与review-input指纹、confirm-mixed-candidate和by；全部风格项通过才可激活。不能将测试夹具响应、synthetic本地通过或编辑者自述当本人样品批准。源码已接入不等于实际AI发送/正式生产质量通过。
