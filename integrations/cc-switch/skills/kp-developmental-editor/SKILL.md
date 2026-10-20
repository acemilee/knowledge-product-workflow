---
name: kp-developmental-editor
description: 对章节定稿执行版本绑定的发展性检查，核验读者任务、内容节奏与可复用工件。
user-invocable: false
metadata:
  version: "1.4.2"
  mode: host-driven
---

# KP Developmental Editor

## v1.4.1 首次执行

首次读取developmental-review-template中的teaching_evidence_protocol、teaching_check_schema、required_unit_ids与reusable_unit_ids。新证据政策下每项teaching_check的evidence_quote引用当前unit原文，note说明这个维度的具体判断/适用边界；引用定位不等于机器证明结论，不能复制六条通用理由或附unit_id假装逐项审核。

只重新判断受影响必审项；未变且合法的记录用reuse_unit_ids，不全章重录、不反复读其他章。真实uncertain保留，最终必审uncertain未解决不得发布。旧书仅在明确采用adopt-reading-policy --confirm后迁移，不撤销旧已发布历史；审核政策仍按Brief，不强制额外独立Agent。

## v1.4.0 任务边界与等待

每任务／候选版本使用新上下文，按结构化handoff与当前manifest接续，不重放历史全聊。完整当前章、合约和必要依赖／比较单元必须齐备；材料不足只补缺失范围，不猜测。领域计算交给本地程序；原进程只有一个等待owner，不另设LLM协调者轮询／sleep／转发心跳。用operation-status核原回执，断流不重发；真实宿主无推理守候接线仍pending，按根Skill降级规则反馈。细节见references/orchestration-contract.md的单一等待章节。

恢复或收到审核过期时，先读当前developmental-review-template的review_context_delta与comparison_context_delta：按具体章节/小节/导出项及旧新摘要定位变化，再按reusable_unit_ids复用观察、补审未获复用的单元。affected_unit_ids只描述依赖摘要变化，不覆盖正文/相邻单元变化、实际案例比较变化或原revise；诊断不授通过资格。history_unavailable/invalid_history表示旧明细不足，沿用原严格复核门并只补必要材料，不猜旧值、不修改正文来刷新证明；changes_truncated=true时先核change_count并取原证明明细，不把首64项当完整范围。

## 成页前阅读节奏核查（v1.3.4本机单轮修正）

不能因某节出现一张表、一幅图或一个代码块就判定阅读节奏通过。实际检查其前后的解释段串：逐段指出新增的概念、推理、例子或读者动作；无法指出增量、重复表格/图中已有信息、把几个平行职责写成连续散文时，返回具体段落和修订理由，由作者选择更清晰的组织方式。保持事实、必要推导与学习成果，不按固定字数／图表数量删稿或填充。章内审核只证明源稿阅读单元；标题与首个练习/读者块分离、表格列距与图内断词必须在成页复核另行判定，不能由本回执覆盖。

## 政策2图任务审核

正文/图生产前按共享契约审当前章节内容计划的illustration_tasks：具体读图问题、组织增益、替代评估、独立视角和图文时机。required/useful/none与create/reference/omit分开；普通短步骤、参数查询和装饰不硬画，已有图用文字回看，不重画。核对批准蓝图task_id及范围，反馈具体修订项；任务审核不生成或替代最终源稿的发展性证明。输入未变复用审核，不为同一任务反复调用。

## v1.4.2 正文与配套实现的分层审核

Brief已确认teaching_focus时，在原六项教学检查的representation_fit/actionability/reading_hierarchy中一并核代码呈现和阅读节奏，不新开例行审稿回合。逐段判断保留的实现是否帮助当前读者任务；方法课应能靠正文的资料、完整提示、输出/反例和练习理解方法，完整运行器可外置。实现课必要长实现可保留，但应有读码任务、关键机制解释与运行核对点。

实际检查重点/例外/下一步是否易定位，提示与输入输出是否相邻，解释、表格、图和练习是否互补；不为丰富层次强行卡片化或切碎长因果推导。超过40程序逻辑行只定位，须具体说明教学价值或外置建议；不靠拆围栏、截图或藏附录通过。配套资源存在不证明教学兑现，短片段也不能冒充可独立运行完整程序；资源变更依当前绑定只刷新受影响单元。

## 用途与触发条件

在章节初稿、内容块计划和章节合约均已就绪后调用。它检查读者任务、内容块兑现、解释密度和工作表结构；不使用 AI 检测分数，不直接改写作者正文。

## 前置工件及状态

- 当前章节由 `kp-chapter-writer` 持有正文租约。
- 当前工作区存在已提交的 `chapter-XX-content-plan.yaml`、章节合约和章节源稿。
- 旧 v4 出版设计档案不强制执行本 Skill；v5 工作区必须拥有当前报告。

## 允许读取与写入

- 读取：当前完整章节源稿、合约、内容计划、当前审核模板/阅读索引、实际执行证据以及必要比较单元。
- 写入：当前审核观察输入JSON；最终结构/发展性报告由受控命令写入 `runs/editorial/`，不得手写证明；不得改写 `chapters/chapter-XX.md`、合约、来源或质量报告。

## 必要步骤与条件分支

新工作区结构政策2：先运行`kp developmental-review-template <章节号> --workspace <workspace> --from <源稿>`。实际审样章各节、标记单元与成果示例/成功判断单元；模板给出位置、template_id与review_input。实际审阅当前输入，再用`kp record-developmental-review <章节号> --workspace <workspace> --chapter-source <源稿> --from <观察JSON>`提交template_id与observations。passed说明信息关系及保留理由；revise给具体重组建议并阻断；uncertain当次写明具体单元与缺少的证据，作为最终补审待办。不能提交模板占位理由或由指标批量签passed。正文、合约、计划变化后当前证明失效。

必审uncertain允许中间制作及独立后章继续；最终`validate`、`validate-delivery`与发布门必须解决。按原模板差分只补必要证据与单元，实际重审确定后提交新观察并刷新同稿的发展性报告；正文未变时保持原稿，不为更新证明重写章节。acceptance_ready只表示中间流程状态，不能代替最终质量通过。

模板reusable_unit_ids表示控制面已经核对单元及必要上下文的旧观察；选择后填reuse_unit_ids，observations只写本次实际新审单元，两者恰好覆盖必审范围。全部复用不启动新模型；部分新审必须实际查看当前单元。控制面归档旧证明并重建当前绑定，不手填hash、不把uncertain改passed、不复用revise。旧工作区由根先预览adopt-reading-policy，用户明确采用后--confirm；不静默采用。

并列条件/性质/验收点用圆点，依赖步骤用数字，共同维度比较用表格，因果推导保留解释段。用户需要编写或调用时保留完整示例、结果与核对点。字符密度仅定位warning，不强制段落/列表/图数量，不以空提示框清零。先结构编辑、再中文定稿，最后对同稿生成两份证明。代码/事实/数字/标识/来源受保护。

1. 运行 `kp developmental-check <章节号> --workspace <workspace> --from <章节源稿>`。
2. 检查每个小节是否兑现内容计划中的 reader job、opening mode、artifact 和 asset decision。解释长文实际核对跨段因果、条件和回指，不能用删除连接词、拆短句或增加列表来替代教学关系；编辑前后不应丢失限定、否定和独立信息。

   修正0.8：非工件解释可artifact=none；已有产物承诺即使仅explanation也必审。连续文字的决策／推导须实际核备选、条件、取舍与结论或具体推理，非空候选检测不算完成证明。旧报告required标记不决定当前必审集合；只补实际新增／失效观察，未变观察可局部复用。
3. 将内容计划缺失、版本不一致、承诺的图表/表格/工作表缺失、工作表最小结构不足列为 blocker。
4. 将连续长解释段、重点滞后或强调密度偏高列为具体 warning 与编辑建议；不得把主观审美判断伪装成 blocker。
   当多个段落分别解释职责/字段/条件，或已有表图后仍连续解释时，实际指出各段的信息增量和读者任务；必要时交作者形成更容易核对的表达。重复检查必须引用具体两处及其共同信息，不能仅填“必要解释”或“层次清晰”签passed。段落是否连贯要看推导依赖，不以字符阈值或块数量清零替代判断。成页审核补查引入与代码/表/图的实际位置、图内完整ID及可读字号；源稿相邻、字体家族存在不等于最终角色/字级合格。
   核对普通教程是否机械重复“依据”卡、官方/作者身份声明或案例身份标签；除明确引用需求外交作者移除，不能因缺少这些声明判正文不合格。后台内容绑定与事实核验继续检查。
5. 早期建议交给作者用于编辑；最终通过证明必须在中文定稿后与 humanize 报告绑定同一源稿。运行 `kp check-chapter --json` 消费证明路径及刷新动作；当前有效报告复用，正文、合约或内容计划变化时只重跑失效项。例子实跑证据按example-check-template的当前代码payload状态复用，散文变更不等于代码过期；用户显式重跑仍实际执行。v5/v6均保持当前版本绑定。
6. 同次阅读当前完整章节，重点记录样章各节、标记单元与产出/成功判断单元的实际观察；不因未标记就忽略错误。必要审核由质量需要决定，不设文本额度、预警或审核次数门。读取内容计划、块索引和必要上下文，不将整书反复发送给多个编辑。

## 禁止事项

新提交会拒收本次已证的通用占位理由，例如“某单元的某检查与本节目标一致、按整体结构未见冲突”，以及对所有命题统一声称“存在模板所以成立”。脚本只整理已完成的逐项判断；实际核对该单元原文、给定输入、推导/输出和反例，缺前提写uncertain或revise。全项确实通过可以全passed，但每项保留其具体依据；旧有效观察不因这项新提交规则自动撤销，原最终新鲜性和质量门保持。

- 不虚构第一人称经验、数据、来源、案例或读者反馈。
- 不把同一章节全文重复发送给多个编辑角色；使用内容计划、结构索引和变更片段。
- 不直接提交章节、创建审稿批次或向用户展示内部报告、哈希、命令和工单。

## 确定性验收命令

当前 v5 章节的 `kp submit-chapter` 必须接受版本绑定的发展性编辑与实际结构审核报告；`kp validate` 与 `kp validate-delivery` 必须在正文、合约或内容计划变化后使旧报告失效。

## 下游交接条件

将当前报告和可操作修订建议交回 `kp-chapter-writer`；作者完成修订后交给 `kp-chinese-humanize-editor` 与 `kp-quality-editor`。

## v1.3.1 共同交接增量

消费冻结reader_layout_revision及插图风格版本。布局2的普通章有自然导读，参考/附录不机械追加；圆点表示并列，数字表示步骤，目录层级和正文事实边界保持。资产、引用和正文最终就绪后生成发展性/humanize证明；任意正文修改重跑两份，不复用旧稿报告。

宿主图资产风格证明在章节提交前；实际PDF后的成页证明在最终视觉报告中。缺失/过期/明确失败阻断，主观uncertain留人工而不伪写算法通过。最终用户回执只绑定当前PDF，测试用户回执不能当真实批准。模板/安装资源来自规范包，不读取本次指定书稿之外的开发者work/tmp或客户PDF。旧政策1保留章内1/全书6历史；政策2按批准图任务和C＋R控制发送，不按图数配额。每任务两次、串行，超额/能力缺失停止并返回选择。


## v1.3.3 跨主题质量与成本增补

旧档案消费Rubric2，新CLI书稿消费Rubric3，使用developmental-review-schema核对teaching_checks六项格式。观察指出源稿位置、信息增量、形式适配、互补、重复价值、可用性、层次；实际比较的单元填compared_unit_ids。不得以密度变小、块变多或模板存在签通过；Rubric/案例依赖变化后只刷新受影响观察。

## v1.3.4 修正0.7：最小充分教学与实际核验

读取当前模板的binding.rubric_revision；Rubric3在六项teaching_checks之外消费assertion_prompts与assertion_support_context，为每个当前关键命题填写assertion_checks：模板assertion_id、逐字quote、支撑ref、缺失前提、supported/unsupported/uncertain、必要反例与具体理由。引文/引用缺项由CLI拒绝；supported必须有当前支撑且无缺失前提，unsupported总体revise，uncertain不能总体passed。字段完整只证明回执一致，实际语义判断仍由本Skill承担；旧Rubric2按原政策不静默补签。

逐项核对推理前提：未给查询向量不得推出新分数；只见错误答案不能断定唯一失败环节；向量正比例缩放性质不自动成为文本变长后嵌入方向性质；相关分数相近不代表没有直接证据；零向量余弦未定义，与声明工程回退值不同；加权平均须保留分母/评分尺度；状态顺序须满足当前前提。必要的因果说明可无图，图表重复若没有新增条件、任务或核对作用须revise，不因形式多而passed。

审核原稿与相邻单元实际增量、操作闭合和最终示例执行证据；具体位置对应问题，不用泛化美观备注。无独立上下文能力如实self_review/unspecified，不能伪装正式独立审稿。局部修改只刷新真实依赖，显式比较对象改变仍要重审，不因作者声称纯格式变化免审。

## v1.3.4 真实对照后的阅读增量与成页检查

审核相邻单元和章末收束的信息增量。重复既有约定的段落若没有新增条件、因果、反例或读者操作，应在redundancy_value/form_adaptation等当前teaching_checks中记录具体位置并要求修订；不能因为事实正确或长度较短签passed。相同内容在新条件下用于独立操作、因果推导或核对时可保留，并写清新增用途。条件、反例与需要它们的动作应能对应，不能把边界总述当成新增教学单元。

章节证明仍审核源稿语义；页尾两段、无增量总结或解释与工件分离的问题，要把明确建议交给作者及成页审核。不要以低密度、块数量增加或无裁切冒充阅读节奏改善。

## v1.3.4 当前正文前提引用的边界

新托管模板的assertion_audit_protocol.required为true时，每项填写input_status、inputs、operation、observed_output、scope_limit、counterexample_status、evidence_kind。先定位输入并寻找反例：缺输入/发现反例/仍有未知时不能supported。actual_execution只引用当前成功实跑，observed_output逐字复制stdout的完整行或连续完整行，不能用数值子串匹配；scope_limit明确此脚本实际完成的步骤，局部检索不能证明生成或完整业务系统。diagram_semantics读取模板diagram:<id>的真实节点/边/标题，required_relations填写主张必须成立的[source,target]对，CLI校对边是否存在；方向或分支不符先revise。正文前提可支撑有边界的独立推导，但结论原话不是自己的前提；概率/普遍规则要检验适用条件和反例，单个成功例不证明普遍成立。字段校验只约束一致性，命题语义及图标题与实际关系仍须本Skill实际审核。

当前模板若列出body_unit:<id>，它只定位这一版正文中的作者情景前提与推导。实际阅读该单元，在命题note中写明使用的给定条件、独立演算或反例；不能仅引用原结论证明原结论。contract/content_plan只有其实际内容覆盖前提时才可引用，不因索引存在就签supported。正文单位不证明外部事实真实，也不证明程序已经执行；外部事实沿研究核验，实际运行主张读取当前实跑记录。缺失必要前提仍uncertain/unsupported，不能用更换ref名称绕过。


## v1.4.2 r0.3 受控执行要点

使用现有repetition_value/reading_hierarchy核boundary_repetition_candidates：比较对象/适用范围/新增条件/独立阅读必要性。建议最多8组，每组当前及最多2处前文、每片段160字；同义改写可能漏检。候选不是完整比较材料，实际compared_unit_ids须在冻结前绑定必要原文；缺项明确返回并受控创建新候选，不在冻结后读取活书或把短摘录冒充已审全章。程序默认入口证据不覆盖真实设备/其他模式，先在现有审阅做有界正反与未运行核对。
