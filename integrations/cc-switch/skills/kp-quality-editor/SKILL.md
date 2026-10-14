---
name: kp-quality-editor
description: 执行确定性内容质量校验、汇总可执行修订项，并阻断不合格书稿；审稿批次仅用于用户主动请求的诊断。
user-invocable: true
metadata:
  version: "1.3.4"
---

# KP Quality Editor

## 用途与触发条件

在章节或全书完成后执行。它负责确定性质量门：重复、占位符、内部标记泄漏、读者可见结构、Claim、来源与交付约束。本 Skill 不悄悄改写作者正文；独立审稿是否为交付前提只由 Brief 的 `review_policy` 决定。

## 前置工件及状态

- 已存在全部章节草稿、章节合约、来源台账及必要的质量输入。

## 允许读取与写入

- 读取：`planning/`、`research/`、`chapters/`、现有质量报告。
- 写入：仅质量报告和修订请求工件；不得直接改写章节正文。

## v1.1 编排工作单

先阅读 `references/orchestration-contract.md`。在 `assurance` 工单内只运行确定性章节/全书/交付质量门并提出唯一恢复动作，不获得正文、Brief 或蓝图写入权。PDF 预览缺失时先交给发布者运行 `kp repair-pdf-preview`；预览有效但交付质量报告无效、过期或未通过时才运行当前版本的 `validate-delivery`。只有 `independent_review_required` 且能力快照为可执行时才创建一次全书审稿批次；未通过预检时必须在批量正文前阻断，不能靠同会话角色切换补救。质量结论、审稿批次和修订请求由根编排器消费，不自行发布交付物。

## 必要步骤与条件分支

1. 运行确定性质量门：字数、重复、占位符、内部标记、泛化标题、来源泄漏、Markdown 结构和 Python 代码块语法；代码检查不执行导入、网络或副作用。
2. 遇到未声明的“真实 JD”“典型权重”、市场/薪资数字或绝对化结论时，要求在章节合约声明 Claim、映射正文标题并登记来源，或降级为情境案例。
3. 每章进入提交前，先检查章节合约和模板上下文中的只读 `expected_section_outline`。它是编号、标题、层级和顺序的唯一真源；运行 `kp chapter-compliance-template <章节号> --workspace <workspace> --from <章节源稿>` 后，作者只填写学习目标、必需教学单元和 Claim 到当前正文标题的语义锚点，再运行 `kp submit-chapter`。不得要求作者填写或手工修补章节号、标题树、正文 SHA、合约 SHA 或内部 Schema。
4. 章节未通过时读取当前工作区 `runs/diagnostics/submit-chapter-chapter-XX.json`、模板、上下文和指定源稿，按“文件、行号、实际标题、期望标题、唯一恢复命令”汇总；`HEADING_*` blocker 未消除前不允许审稿、汇编或发布。
5. `deterministic_only` 下可按用户要求运行审稿诊断；`independent_review_required` 下必须运行 `kp submit-review-batch --workspace <workspace> --all`，并验证真实宿主子任务回执。`host_skill` 模式不得标记为独立审稿通过。
6. v5 章节还必须检查内容计划、发展性编辑报告和工作表结构是否绑定当前正文；表/图/工作表承诺缺失时阻断，解释密度等主观问题仅给出 warning。
7. PDF 含受控图表或 v5 出版档案时，运行交付校验必须检查重复图注、当前 PDF 预览集、视觉复核绑定和受控图表占高契约。确定性检查无法证明图中文字、裁切和版式时，返回“需要人工视觉复核”，不得标记可发布或让发布 Skill 绕过。

## 禁止事项

- 不把宿主同会话角色切换伪装为独立 API 或真实子任务审稿，也不写入读者书稿。
- 不将来源台账或审稿标记泄漏到读者书稿。
- 不以“模型认为没问题”替代确定性质量门。
- 不手写、猜测或逆向章节合规内部 JSON、哈希、合约版本或 `unit_mappings`/`units` 等字段；不搜索源码、安装目录或其他工作区来恢复提交失败。

## 确定性验收命令

`kp validate --workspace <workspace>`、`kp validate-delivery --workspace <workspace>`；仅在 Brief 要求独立审稿时补充 `kp submit-review-batch --workspace <workspace> --all` 和 `kp review-check --workspace <workspace>`。

## 人工审批点

高风险事实冲突、版权/许可不明、连续审稿失败或范围变更必须升级给用户。

## 用户交互边界

只向根 Skill 提供产品级风险、影响范围和修订建议。不得向用户展示质量规则编号、报告路径、审稿工单、哈希或技术恢复命令。

## 下游交接条件

只有无 blocker 的质量报告才能交给 `kp-publisher`。

## v1.3.1 共同交接增量

消费冻结reader_layout_revision及插图风格版本。布局2的普通章有自然导读，参考/附录不机械追加；圆点表示并列，数字表示步骤，目录层级和正文事实边界保持。资产、引用和正文最终就绪后生成发展性/humanize证明；任意正文修改重跑两份，不复用旧稿报告。

宿主图资产风格证明在章节提交前；实际PDF后的成页证明在最终视觉报告中。缺失/过期/明确失败阻断，主观uncertain留人工而不伪写算法通过。最终用户回执只绑定当前PDF，测试用户回执不能当真实批准。模板/安装资源来自规范包，不读取本次指定书稿之外的开发者work/tmp或客户PDF。旧政策1保留章内1/全书6历史；政策2按批准图任务和C＋R控制发送，不按图数配额。每任务两次、串行，超额/能力缺失停止并返回选择。
