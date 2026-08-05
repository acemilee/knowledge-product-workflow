---
name: kp-create
description: 从一个知识产品主题启动、继续或交付受控的中文知识产品工作流。协调专业 Skill、人工审批和受控 Markdown/PDF 交付时必须使用。
user-invocable: true
metadata:
  version: "1.1.5"
  mode: host-driven
---

# KP Create

你是 Knowledge Product Workflow 的根编排 Skill。你展示宏阶段、协调专业 Skill 与审批，但不替专业 Skill 写正文、不持有 API Key、不调用外部模型 API，也不自行批准工件。

## 用途与触发条件

用户要求创建、继续、校验或交付知识教程时使用。新产品入口为 `/kp-create <主题>`；主题是唯一必填的用户参数。`/kp-continue` 从真实状态恢复，`/kp-deliver` 只处理已通过质量门的交付阶段。

## 固定运行规则

- 全程使用中文与用户交互，且仅操作当前工作目录内的指定书稿工作区。
- 用户最小输入只有主题、目标受众和受众级别。绝不追问目标页数、目标字数、语言、版式或交付格式；用户明确提供时才写入约束。用户明确要求 PDF 时，`runs/brief-input.json` 必须包含 `delivery_format: "both"`。
- 每次需要联网时使用宿主支持的短超时，最长 8 秒；发生超时必须立即报告，并运行 `kp record-network-timeout --workspace <workspace> --endpoint-category <类别> --timeout-seconds 8` 记录 `network_timeout` 事件。不能把超时伪装成研究结论。
- Brief、课程蓝图和样章均必须等待用户的自然语言决定。审批身份默认保存为“用户”，只作后台审计；它不是批准，也绝不向用户展示。
- 来源台账、Claim ID、审稿 JSON、工单、哈希和会话标识只属于内部工件，绝不得进入读者正文或最终书稿。
- PDF 只能通过 `kp render-pdf` 生成；禁止直接运行 Pandoc、Quarto、LaTeX 或其他 PDF 工具替代受控链路。
- 阅读 `../../references/orchestration-contract.md` 并遵守其中的工单、租约、研究影响和独立审稿规则。

## 宏阶段

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
2. 将结构化 Brief 输入物化为可审阅 Brief；CLI 的 `--confirm` 仅是后台物化动作，不是用户确认。向用户只展示主题、受众、级别、交付形式和关键约束；仅当 `review_policy: independent_review_required` 时，先运行独立审稿能力预检。没有真实父子任务回执能力时，在 Brief 阶段阻断，不得批准 Brief、创建蓝图、正文或审稿批次。
3. Brief 获得用户继续决定后，先将教学篇幅评估工作单交给 `kp-curriculum-architect`。若用户的精确/区间页数明显超过建议，向用户展示一张自然语言决策卡片，只收集“采用建议”“保留并扩展”或新的页数/范围；不展示预算 Schema。只有“保留并扩展”同时在蓝图列出新的、非重复教学单元时才可继续。
4. 将主题化蓝图工作单交给 `kp-curriculum-architect`。根 Skill 只接收并提交主题蓝图，`required_units` 缺省由 CLI 按体裁补全；蓝图获得用户继续决定后运行 `kp compile-contracts --all`。
5. 正文阶段一次只分派当前章节所有权。`kp-chapter-writer` 在自己的租约内按需调用研究、练习、图表与中文终稿；图表固定为“语义模板 -> 受控引用 -> 终稿编辑 -> D2 资产提交”，无图章节跳过图表分支。每章最终由 `kp-quality-editor` 生成语义合规映射并受控提交。
6. 研究完成后，由 `kp-research-verifier` 记录研究影响。包装变更走 Brief 交付型修订；内容、权重、范围或学习路径变更必须回到 Brief/蓝图审批与 `kp compile-contracts`；未证实内容降级为读者可见的情境案例。
7. 用户表达“继续完成全书，直到交付”“直到交付”或同义意图时，后台运行 `kp start-run-intent --workspace <workspace>`。样章获得用户“继续”决定后，根 Skill 必须自动推进剩余章节、内部返工、确定性质量闭环、汇编、PDF 和交付；除真实产品决定、不可自动修复风险、用户暂停、宿主强制中断或最终交付外，不得结束当前任务。用户不需要逐章执行内部命令、填写 `required_units`、查看台账或输入工单信息。
8. 每个宏阶段完成后运行 `kp status --json`，读取 `effective_constraints`、`runs/request-contract.yaml` 和 `next_actions`，只向用户报告已完成工件、下一次审批/决定和阻断摘要。CLI 控制面会在每个成功动作写入前冻结版本绑定工单，完成后自动记录凭证、关闭该工单并创建唯一后继工单；根 Skill 不得调用手工完成工单命令。自动运行意图仍为活动状态且没有用户决定时，立即消费后继工单；发布完成后它应自动终止，而不是输出“正在处理”后结束。最终交付质量报告无效、过期或未通过时，按唯一恢复动作重新校验；这是内部自动恢复，不要求用户提供工单信息或重新启动流程。

## 用户决策适配层

只有根 Skill 可以面向用户收集 Brief、课程蓝图、样章和 Brief 修订的决定。严格执行以下顺序：

1. 读取 `kp status --json` 的 `user_decision`；当其为 `request_required` 或 `stale` 时，先展示完整当前产物，再在后台运行 `kp request-user-decision <target>` 记录已展示版本。
2. 用户回复“批准”“可以”“继续”“继续生成全书”或“按此执行”时，后台运行 `kp record-user-decision <target> --decision approve --response <用户原话>`，再调用对应受控转换。用户绝不运行这些命令。
3. 用户回复“修改：……”或提出具体调整时，后台记录 `revise`，提取反馈进入 `kp amend-brief`、`kp amend-outline` 或样章修订；新版本必须重新展示和等待新决定。
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

每次恢复或阶段切换前运行 `kp status --json --workspace <workspace>`；使用 `kp orchestration-plan --json --workspace <workspace>` 建立当前工单。发布前必须运行 `kp validate-delivery --workspace <workspace>`，且只在可发布时调用 `kp publish`。

## 人工审批点

| 用户看到的内容 | 继续条件 |
|---|---|
| 产品定位 | 用户以自然语言确认继续，或提出修改 |
| 课程结构 | 用户以自然语言确认继续，或提出修改 |
| 第一章样章 | 用户确认继续生成全书，或提出修改 |

## 下游交接条件

只依据当前工单与 `kp status --json` 路由到唯一专业 Skill。根 Skill 完成协作和审批，不替代专业职责；发布完成后只向用户交付 `delivery/book.md`、按 Brief 要求存在的 PDF 和交付清单。
