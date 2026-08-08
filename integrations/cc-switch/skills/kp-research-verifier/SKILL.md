---
name: kp-research-verifier
description: 为高风险事实建立内部来源与假设边界，阻止无证据主张进入知识产品正文。
user-invocable: true
metadata:
  version: "1.1.6"
---

# KP Research Verifier

## 用途与触发条件

在蓝图批准后、正文写作前或发现高风险事实主张时使用。它管理内部证据，不向读者展示来源台账。

## 前置工件及状态

- 已批准课程蓝图。
- 要核验的事实主张、数字、日期、公司、市场/薪资结论或“真实/多数/典型”表述清单。

## 允许读取与写入

- 读取：`planning/`、本阶段明确允许的公开来源、当前 `research/` 工件。
- 写入：仅 `research/source-ledger.jsonl`、`research/assumption-ledger.jsonl` 与研究摘要工件。

## v1.1 编排工作单

先阅读 `../../references/orchestration-contract.md`。只能在根编排器分配的 `production` 工单内核验当前合约声明的事实，不拥有 Brief、蓝图或正文的写入权。完成核验后必须使用 `kp record-research-impact` 记录影响：`no_product_change` 才可继续，`packaging_change` 走交付型 Brief 修订，`content_change` 回到 Brief/蓝图审批，`unverified` 降级为情境案例；不得将来源台账或 Claim ID 交给读者。

## 必要步骤与条件分支

1. 判断外部信息是否会改变受众、课程定位、章节权重、结论或 Go/No-Go；仅在会改变时才联网。
2. 所有网络请求使用最长 8 秒超时；超时立即报告并执行 `kp record-network-timeout --workspace <workspace> --endpoint-category <类别> --timeout-seconds 8`，主张保持 `evidence_needed`。
3. 将会影响受众、定位、章节权重、范围或结论的前提写入假设台账：`kp assumption <id> --workspace <workspace> --statement <陈述> --impact <范围> --disposition evidence_needed --rationale <理由>`。
4. 将可核验证据写入来源台账，并标记许可、时间、适用范围和支持的 Claim；证实假设时，以相同 ID 追加 `verified` 记录并关联 Claim ID。
5. 没有证据的内容必须以相同 ID 追加 `reframed_as_case`，并改成清楚标注的情境案例或作者建议。
6. 需要读者可见引用时，仅向写作 Skill 交接来源标题与 HTTPS URL，供 `.kp-source` 卡片使用；台账 ID、摘录和内部备注不得进入正文。

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

为 `kp-chapter-writer` 提供已验证事实与明确标注的假设边界。
