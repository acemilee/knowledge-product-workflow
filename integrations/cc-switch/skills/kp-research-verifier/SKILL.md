---
name: kp-research-verifier
description: 为高风险事实建立内部来源与假设边界，阻止无证据主张进入知识产品正文。
user-invocable: true
metadata:
  version: "1.3.4"
---

# KP Research Verifier

## v1.3.4 O16 前提与证据范围

研究判断须定位当前输入、操作、实际产出和适用范围。缺失输入保留unknown，不能从材料未含答案推出计算分数；代码实跑只证明当前脚本及stdout，不自动证明其未执行的环节。先找反例和不满足前提，再记录证据决定。关键命题交发展性模板按assertion_audit_protocol审核；图标题宣称的分支/关系需与当前节点和边一致。当前来源绑定不等于结论语义已经机器证明。

## 用途与触发条件

在蓝图批准后、正文写作前或发现高风险事实主张时使用。它管理内部证据，不向读者展示来源台账。

## 前置工件及状态

- 已批准课程蓝图。
- 要核验的事实主张、数字、日期、公司、市场/薪资结论或“真实/多数/典型”表述清单。

## 允许读取与写入

- 读取：`planning/`、本阶段明确允许的公开来源、当前 `research/` 工件。
- 写入：仅 `research/source-ledger.jsonl`、`research/assumption-ledger.jsonl` 与研究摘要工件。

## v1.1 编排工作单

先阅读 `references/orchestration-contract.md`。只能在根编排器分配的 `production` 工单内核验当前合约声明的事实，不拥有 Brief、蓝图或正文的写入权。完成核验后必须使用 `kp record-research-impact` 记录影响：`no_product_change` 才可继续，`packaging_change` 走交付型 Brief 修订，`content_change` 回到 Brief/蓝图审批，`unverified` 记录未核验边界，只有显式 classify-claim 后才可作为情境案例；不得将来源台账或 Claim ID 交给读者。

## 必要步骤与条件分支

1. 判断外部信息是否会改变受众、课程定位、章节权重、结论或 Go/No-Go；仅在会改变时才联网。
2. 所有网络请求使用最长 8 秒超时；超时立即报告并执行 `kp record-network-timeout --workspace <workspace> --endpoint-category <类别> --timeout-seconds 8`，主张保持 `evidence_needed`。
3. 将会影响受众、定位、章节权重、范围或结论的前提写入假设台账：`kp assumption <id> --workspace <workspace> --statement <陈述> --impact <范围> --disposition evidence_needed --rationale <理由>`。
4. 将可核验证据写入来源台账，并标记许可、时间、适用范围和支持的 Claim；证实假设时，以相同 ID 追加 `verified` 记录并关联 Claim ID。
5. 没有证据的外部事实不能保留事实口吻；确实改为教学设定时以相同 ID 追加 `reframed_as_case`，后台记录分类与理由，正文无需输出官方/作者身份的分界声明。
6. 普通教程的来源核验默认留在内部。读者需要打开文档完成任务或用户明确要求引用时，向写作 Skill 交接来源标题与 HTTPS URL；不自动生成章末“依据”卡片，台账 ID、摘录和内部备注不得进入正文。

## 禁止事项

- 不把来源台账、URL、Claim ID 或内部备注写入章节正文。
- 不以泛搜替代验证计划。
- 不把未核验案例称为“真实”。

## 确定性验收命令

`kp validate --workspace <workspace>` 自动从当前章节合约收集 Claim，必须通过所有要求的 Claim 和假设台账门。

## 人工审批点

高风险、版权不明或证据冲突的结论必须报告用户；不得自行选择发布。

## 用户交互边界

只向根 Skill 报告结论影响和可理解的风险摘要。不得将来源台账、Claim ID、网络超时记录、内部路径或恢复命令交给用户。

## 下游交接条件

分类契约见 `kp claim-classification-schema`。未分类为 factual；作者建议或教学情境用 `kp classify-claim --workspace <workspace> --from <输入>` 在后台绑定 Claim、实际教学内容句、标题和理由；不绑定“本节规则为作者构造”等来源身份套话，不要求正文出现分类标签。建议中夹带效果数字/平台事实仍需独立 factual 来源；来源搜索失败不得自动降级。只纠正分类且内容契约未变时局部刷新，不要求重批整份蓝图。

为 `kp-chapter-writer` 提供已验证事实与明确标注的假设边界。
