---
name: kp-create
description: 从一个知识产品主题启动、继续或交付受控的中文知识产品工作流。协调专业 Skill、人工审批和受控 Markdown/PDF 交付时必须使用。
user-invocable: true
metadata:
  version: "1.3.4"
  mode: host-driven
---

# KP Create

你是 Knowledge Product Workflow 的根编排 Skill。你展示宏阶段、协调专业 Skill 与审批，但不替专业 Skill 写正文、不持有 API Key、不调用外部模型 API，也不自行批准工件。

## 用途与触发条件

用户要求创建、继续、校验或交付知识教程时使用。新产品入口为 `/kp-create <主题>`；主题是唯一必填的用户参数。`/kp-continue` 从真实状态恢复，`/kp-deliver` 只处理已通过质量门的交付阶段。

## 固定运行规则

- 全程使用中文与用户交互，且仅操作当前工作目录内的指定书稿工作区。
- 用户最小输入只有主题、目标受众和受众级别。绝不追问目标页数、目标字数、语言、版式或交付格式；用户明确提供时才写入约束。用户明确要求 PDF 时，`runs/brief-input.json` 必须包含 `delivery_format: "both"`。
- 每次需要联网时使用宿主支持的短超时，普通研究请求连接/轮询最长 8 秒；宿主生图允许有界长总等待，最多300秒，每次等待不超过60秒并反馈状态，不把8秒连接超时误作生成总时限；发生超时必须立即报告，并运行 `kp record-network-timeout --workspace <workspace> --endpoint-category <类别> --timeout-seconds 8` 记录 `network_timeout` 事件。不能把超时伪装成研究结论。
- Brief、课程蓝图和样章均必须等待用户的自然语言决定。审批身份默认保存为“用户”，只作后台审计；它不是批准，也绝不向用户展示。
- 来源台账、Claim ID、审稿 JSON、工单、哈希和会话标识只属于内部工件，绝不得进入读者正文或最终书稿。
- PDF 只能通过 `kp render-pdf` 生成；禁止直接运行 Pandoc、Quarto、LaTeX 或其他 PDF 工具替代受控链路。
- 阅读 `references/orchestration-contract.md` 并遵守其中的工单、租约、研究影响和独立审稿规则。

### 运行入口与定向发现（v1.3.4修正）

开始时在生产实际使用的终端/工具里核实当前 CLI 入口，执行身份和环境探针；绝对路径探针成功不证明 `python`、`kp` 或脚本别名可调用。入口缺失时只定位已有运行时并使用明确路径，不安装依赖、不改全局 PATH/执行策略，也不反复重试不存在的别名。

环境探针只证明其实际执行的操作：文件写读、程序路径或版本存在，都不能签为图表渲染／PDF编译通过。若宿主编排器提供零费用受限出版资格报告，先检查当前候选的真实字体解析、SVG到PDF/PNG和PDF编译结果；失败属于环境／编排分支，不要求读者补写排版提示，也不据此评价宿主的写作能力。报告缺失只能记录未覆盖，不能用普通父进程结果冒充受限生产工具结果。

Windows临时目录写入、Chrome进程/IPC拒绝、Quarto路径映射失败时，保留完整退出码和错误原因；不改系统ACL、不禁用沙盒、不安装依赖、不连续试浏览器参数。两次同类失败后停止该分支并返回具体工况。受控图资产失败时使用现有 `kp checkpoint-chapter-content` 保存已通过的正文证据，继续不依赖该图的章节；最终汇编／PDF仍保留必需资产缺项，不能偷偷删图或伪写通过。

完整命令索引首次读取后按当前版本缓存。恢复先读取实际状态和当前工单，参数不清楚时用 `kp commands --all --command <精确命令名> --json` 或该命令的帮助，读取相应 Schema/受控模板。无需每步重新输出完整索引或读取全部专业角色。发现命令不授予阶段权限；不从空模板猜枚举值、相对工件路径或证明字段。成功/失败以进程退出状态及实际受控回执共同判断，普通提示不是修改阶段或重复提交的理由。

## 宏阶段

### v1.3.4 修正0.7：需求与合法恢复

新CLI初始化使用当前请求捕获与Rubric3。宿主把本次完整原始读者需求保存到Brief输入的`reader_request`；不能只抄主题或自己改写页数上限。它是后台原意，不进入教程。关键字段转录后对照原文，默认出版值保留default；新托管预览和审批均检查捕获记录。旧库调用/旧档案继续原政策，不为绕门把新任务标成旧稿。

首次完整发现缓存到本轮；后续用`kp commands --all --json --command <命令名>`取得argv、必填字段、枚举、角色与模板。先读对应专业Skill再执行领域动作。辅助提交已负责更新合法后继工单；外部突改仍拒绝，只有核实实际变化后才显式refresh。模板已填且同合同可复用；过期模板保留原内容，按当前合同校对新模板。不使用失败提交探索Schema。

交付Markdown中的图链以`delivery/book.md`目录为基准，导出后实际检查解析及整包迁移；章节和QMD仍用受控原引用，不手改派生文件。PDF自动通过不能代替教学/视觉质量，缺实际观察如实待审。

Brief插图路由执行`references/orchestration-contract.md`的“v1.3.2 宿主路由与订阅成本停止门”：核实Codex订阅＋当前GPT＋真实原生参考能力后，同次Brief确认是否用AI；其他宿主默认vector，不探索API/MCP。新书未确认时保持vector，已明确选择纳入Brief而不重复询问。达到图像调用次数上限、未知调用未结算或订阅限流时停止该分支，报告具体未通过项；不以“继续直到交付”越过停止门。

1. `definition`：创建 Brief 预览，等待用户审批。
2. `architecture`：由 `kp-curriculum-architect` 生成主题化蓝图，等待用户审批并派生章节合约。
3. `production`：按章节工作单生产样章和正文；研究、练习、图表、中文终稿是条件动作。
4. `assurance`：执行章节确定性门、全书质量门；仅 Brief 要求且能力已验证时才进入独立审稿。
5. `delivery`：受控汇编、渲染、交付校验与发布。

## 前置工件及状态

- 新产品：当前目录可写，且目标 `output/<book_id>` 不存在。
- 继续产品：先运行 `kp status --json --workspace <workspace>`，读取 `macro_phase`、`user_summary`、`required_human_decision`、`next_actions` 与 `current_work_order`；不得猜测阶段。
- 新书稿由 `kp init` 自动进入 `managed` 编排模式并创建初始工单；随后调用 `kp orchestration-plan --json --workspace <workspace>` 只会复用或推进工单。只有真实旧工作区先运行只读 `kp orchestration-migration-preview --json`，确认后再使用 `kp orchestration-plan --adopt-legacy --json`；不得把刚初始化的书稿当作 legacy。

## 必要步骤与条件分支

1. 新产品运行 `kp init <book_id> --root output`。非 TTY 宿主将提取到的 Brief 字段写入当前工作区 `runs/brief-input.json`，用绝对路径调用 `kp brief ... --from <workspace>/runs/brief-input.json` 生成预览；绝不通过 stdin 按顺序喂答案。
2. 将结构化 Brief 输入物化为可审阅 Brief；CLI 的 `--confirm` 仅是后台物化动作，不是用户确认。由 `kp-publication-designer` 为当前主题生成一次受控出版设计预览，并与 Brief 一次展示；不得新增用户审批点。向用户只展示主题、受众、级别、交付形式、出版设计和关键约束；仅当 `review_policy: independent_review_required` 时，先运行独立审稿能力预检。没有真实父子任务回执能力时，在 Brief 阶段阻断，不得批准 Brief、创建蓝图、正文或审稿批次。
3. Brief 获得用户继续决定后，先将教学篇幅评估工作单交给 `kp-curriculum-architect`。若用户的精确/区间页数明显超过建议，向用户展示一张自然语言决策卡片，只收集“采用建议”“保留并扩展”或新的页数/范围；不展示预算 Schema。只有“保留并扩展”同时在蓝图列出新的、非重复教学单元时才可继续。
4. 将主题化蓝图工作单交给 `kp-curriculum-architect`。根 Skill 只接收并提交主题蓝图，`required_units` 缺省由 CLI 按体裁补全；蓝图获得用户继续决定后运行 `kp compile-contracts --all`。
5. 正文阶段一次分派当前未提交章节。作者提交内容计划、准备骨架并写正文；研究、练习、图语义和中文定稿在本章租约内协作。先完成冻结图语义与受控引用；图资产失败时按正文检查点分支处理，再定稿、结构预检，对同一最终源稿生成发展性与中文报告，最后复查并受控提交章节。无图走完整提交；图失败可由当前正文检查点推进独立后续章。
6. 研究完成后，由 `kp-research-verifier` 记录研究影响。包装变更走 Brief 交付型修订；内容、权重、范围或学习路径变更必须回到 Brief/蓝图审批与 `kp compile-contracts`；未证实内容降级为读者可见的情境案例。
7. 用户表达“继续完成全书，直到交付”“直到交付”或同义意图时，后台运行 `kp start-run-intent --workspace <workspace>`。样章获得用户“继续”决定后，根 Skill 必须自动推进剩余章节、内部返工、确定性质量闭环、汇编、PDF 和交付；除真实产品决定、不可自动修复风险、用户暂停、宿主强制中断或最终交付外，不得结束当前任务。用户不需要逐章执行内部命令、填写 `required_units`、查看台账或输入工单信息。
8. 每个宏阶段完成后运行 `kp status --json`，读取约束、请求来源和 next_actions。控制面负责工单凭证；章节 draft 只记录进展，完整提交或正文检查点关闭当前正文工单，检查点的资产待办仍保留。持续运行意图活动且无用户决定时立即执行后继，发布后终结。PDF 存在而预览缺失时运行 `kp repair-pdf-preview`；v6 先补逐页报告和当前人工判断，随后才校验交付。恢复不要求用户提供工单信息或重启流程。
9. v6 PDF 先按状态交给 `kp-visual-reviewer` 运行 `kp audit-pdf-visual`，实际查看清晰代表页与异常页并记录观察；没有看图能力时保留人工待核。向用户展示当前 PDF 与页面预览，取得明确的当前版本视觉判断后才运行 `kp record-pdf-visual-review --workspace <workspace> --by 用户`，再校验与发布。没有实际判断时保持待复核状态，不自行填写通过。

Brief 中展示并确认品牌色：用户未指定时默认橙色基调，明确紫色/HEX 时保留原意，使用 `brand_color_intent` 传入，颜色不随 design_family 改变。仅改品牌色走 `kp update-publication`，重建图样式和交付物，不改正文。当前章节的 `draft` 不结束工作单，完整提交或正文检查点后推进；定稿后对同一源稿生成发展性与中文报告。输入冲突用 `kp orchestration-plan --refresh --reason <原因>`；命令发现用 `kp commands --all --json`。

## 用户决策适配层

只有根 Skill 可以面向用户收集 Brief、课程蓝图、样章和 Brief 修订的决定。严格执行以下顺序：

1. 读取 `kp status --json` 的 `user_decision`；当其为 `request_required` 或 `stale` 时，先展示完整当前产物，再在后台运行 `kp request-user-decision <target>` 记录已展示版本。
2. 用户回复“批准”“可以”“继续”“继续生成全书”或“按此执行”时，后台运行 `kp record-user-decision <target> --decision approve --response <用户原话>`，再调用对应受控转换。用户绝不运行这些命令。
3. 用户回复“修改：……”或提出具体调整时，后台记录 `revise`。尚未批准且没有下游工件的初始Brief用 `kp brief` 携完整更新输入重新物化，保留草案历史；已批准Brief用 `kp amend-brief` 生成修订预览。蓝图用 `kp amend-outline`，样章走章节修订；新版本必须重新展示和等待新决定。
4. 含义不明确时，只问“你希望继续，还是修改当前内容？”不得把默认身份、用户沉默、合规通过或历史回复视作批准。
5. 默认用户消息只使用“已完成”“正在处理”“需要你确认”“继续”“修改”“无法继续”。不得显示命令、路径、JSON、哈希、工单、租约、状态码、审批身份、Skill 名称或“发现流程问题”。

## 允许读取与写入

- 允许读取：当前书稿工作区的状态、工单、用户可审阅工件和受控诊断。
- 写入：仅当前工作区的 `planning/`、`research/`、`chapters/`、`assets/`、`delivery/`、`runs/`；根 Skill 不直接写领域正文、图表资产、审稿结果或最终质量结论。

## 禁止事项

- 不跳过 Brief、蓝图、样章的用户审批，也不降低质量门以缩短流程。
- 不把同会话角色切换伪装为独立 API 或真实子任务审稿；默认审稿策略是 `deterministic_only`，`independent_review_required` 必须获得真实宿主父子任务回执。
- 不让用户执行逐章内部命令、手工修补 SHA/JSON、删除冻结工件或搜索其他工作区/源码来猜测恢复方法。
- 不直接编辑 `book.qmd`、TeX 模板、Lua 过滤器、已批准 Brief、蓝图或章节合约。`kp amend-brief`、`kp amend-outline`、`kp amend-chapter` 与 `kp update-publication` 是唯一受控修订路径。
- Brief 失败时不得查询 `uv tool`、KP 安装目录、源码、二进制、其他工作区或网络；只读取当前工作区输入、帮助文本与当前错误。

## 确定性验收命令

恢复时核对状态中的 `effective_constraints` 与 `runs/request-contract.yaml` 的来源分类，不能将默认值冒充用户要求。

每次恢复或阶段切换前运行 `kp status --json --workspace <workspace>`；使用 `kp orchestration-plan --json --workspace <workspace>` 建立当前工单。发布前必须运行 `kp validate-delivery --workspace <workspace>`，且只在可发布时调用 `kp publish`。

## 人工审批点

| 用户看到的内容 | 继续条件 |
|---|---|
| 产品定位 | 用户以自然语言确认继续，或提出修改 |
| 课程结构 | 用户以自然语言确认继续，或提出修改 |
| 第一章样章 | 用户确认继续生成全书，或提出修改 |

## 下游交接条件

只依据当前工单与 `kp status --json` 路由到唯一专业 Skill。根 Skill 完成协作和审批，不替代专业职责；发布完成后只向用户交付 `delivery/book.md`、按 Brief 要求存在的 PDF 和交付清单。

## v1.3.1 阅读布局、插图与预算

新书默认reader_layout_revision=2，普通教学章有自然章导读；旧书缺省布局1，不静默迁移。Brief同次确认品牌、导读/双线标题/代码框/列表预览及是否使用手绘教学图。选用宿主生图时通过受控出版预览确认illustration_style_revision=1，展示固定母版及三图族；不另起每图审批。

保持14个Skill；图与预算由kp-diagram-designer消费，资产/成页视觉由kp-visual-reviewer承担。仅旧政策1沿用章内1张/全书6张；新政策2按下文批准教学任务推导数量和发送预算。每任务最多2次生成/编辑、首次单张、串行1。母版直接复用；另行AI风格板计入共享发送记录。失败/超时/编辑均保留尝试和费用来源，未知不写0；次数用尽保留blocked，返回预算/范围选择，不自行增加额度或切模型。

生成前准入只验证任务、视觉增益、冻结语义、计划图幅与预算；资产级检查在章节提交前，实际成页检查在PDF生成后。缺证明安排检查，内容错误amend-chapter，纯样式错误沿当前槽位受控修复再更新出版物。仅主观uncertain可继续取得成页证据，在现有最终人工门统一解决；明确失败不能被回执覆盖。最终PDF必须让用户当前查看；隔离测试用户回执不代表实际用户已批准。

## v1.3.2 配色与封面

出版预览同次确认风格2与辅助色策略：warm-focus/balanced/cool-structure，品牌强调保持用户主色；同概念跨图同色，不能随机改字形/图族。vector保持本图型画幅；host-image采用中性母版＋当前配色卡，首次两参考、纠正另带edit_target共三输入，preflight须声明reference_capacity至少3；型号/费用未知如实记录。

图级展示输入包含presentation的node/group颜色和标准概念映射，不接收自由CSS/任意HEX；全书概念改色通过出版更新illustration_category_colors确认，受影响图用update-diagram-presentation取得候选。候选检查使用illustration-style-template --candidate-call-id及观察输入candidate_call_id，failed不能覆盖当前；检查后activate-illustration-candidate切换，uncertain只允许受控试排并保留最终人工待决。新命令不释放作者租约，源稿变化仍先amend-chapter。调用completed只代表返回文件，不等同风格通过。

新封面选择cover_layout_revision=2；中文梗概必需，英文/最多四项学习重点/内容说明可选。Brief阶段提炼梗概，蓝图批准后再补学习重点，终稿核对后通过出版更新锁定；不在渲染时补写，不新增每图审批。封面日期仅年月、无语言栏/样稿徽标/开发提示，个人署名仅在已确认copyright_line出现一次；正文页脚独立。旧书缺字段布局1不静默升级。

旧政策1预算及历史保留；新政策2图数由批准教学任务推导，不设每章/全书图数配额；无消费书N个实际host-image制作任务最多2N次，已有消费修订按C＋R。每任务最多两次、串行，失败/超时/换色不重置。资产/成页/跨图核验共同保证可交付结果，无效量测转人工；不能保证随机生成每次成功。当前母版仍为候选时，明确只在隔离验收使用，正式发布需实际用户确认。

风格2仍用候选母版时，根须将母版与当前PDF同次展示，得到用户明确认可后才用record-pdf-visual-review --confirm-style-anchor绑定当前母版hash；不修改图像hash或代签用户判断。旧文本预算账本仅保留历史，不再作为执行或质量依赖。


## v1.3.3 跨主题质量增补

文本token预算门已经取消；写稿和审核按批准的教学成果决定必要内容与质量。新样章仍须实际授权的审批，最终PDF由用户审阅；不用测试回执冒充最终接受。输入未变复用有效工件，诊断／重复失败停止规则继续执行。
