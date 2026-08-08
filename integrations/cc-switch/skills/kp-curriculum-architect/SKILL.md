---
name: kp-curriculum-architect
description: 从已批准的知识产品 Brief 生成并提交主题化结构化课程蓝图，再批量派生当前章节合约。
user-invocable: true
metadata:
  version: "1.1.6"
---

# KP Curriculum Architect

## 用途与触发条件

在 Brief 已批准后，设计学习路径、章节权重、必需教学单元与章节边界。

## 前置工件及状态

- `planning/product-brief.md` 必须存在且为已批准状态。
- 必须读取 Brief 中可用的字数与页数约束；两者未设置时，按教学权重和必需单元设计结构，不得因没有数字预算阻断课程蓝图。

## 允许读取与写入

- 读取：当前工作区中的 `planning/product-brief.md`、`planning/curriculum-blueprint.template.yaml` 与 `kp outline-schema` 的受控输出。
- 写入：`planning/curriculum-blueprint.template.yaml`、`planning/curriculum-blueprint.md` 和批准后生成的 `chapters/chapter-XX-contract.md`。

## v1.1 编排工作单

先阅读 `../../references/orchestration-contract.md`。只能在根编排器分配的 `architecture` 工单内生成主题化蓝图或从已批准蓝图派生章节合约。工作单输入与当前 Brief 绑定；Brief 的内容型修订、蓝图过期或 `section_outline` 变化时停止并按状态重新路由。不得自行批准蓝图、拥有 Brief 写入权，或从章节正文反写课程结构。

## 必要步骤与条件分支

1. 先运行 `kp length-recommendation-template --workspace <workspace>`，根据主题、受众、级别、案例/练习/代码/图表密度填写并提交教学篇幅建议。建议必须解释依据，不能为迎合用户页数制造同义小节。
2. 当用户的精确或区间页数超过建议上限时，在建议中声明每项额外的、可辨识的教学单元、案例、项目、模板或附录。用户未决定“保留并扩展”前不生成蓝图；下限或不限页数不生成机械章节页数目标。
3. 定义从基础理解到可迁移能力的学习路径，而非按主题罗列名词。
4. 为每章声明完整 `section_outline`：从 `<章节号>.1` 连续编号，标题必须是读者将看到的正式标题；子项必须挂在正确父级下。它是批准后冻结的读者结构，正文作者不得自行增删、跳号、改名或重排。
5. 为每章声明正数 `weight`、教学理由、`chapter_archetype`、必需/可选/禁止教学单元、学习目标、范围和 Claim 清单；预算由 CLI 按权重分配，不得平均摊分。
6. 体裁只可使用 `concept`、`case_study`、`workshop`、`strategy`、`reference`、`appendix`。根据教学目的选择体裁，不得给每章套同一种“目标、问答、追问、误区、练习、小结”模具。
7. 面试类产品必须声明 `product_mode: interview` 与全书/每章 `interview_answer_policy`。只有有限高频题可设 `complete_answer_demo`；其余内容使用答题框架、证据槽位或练习，不能量产标准话术。
8. 先运行 `kp outline --workspace <workspace>`，再运行 `kp outline-schema`。只读取当前工作区生成的模板和该公开 Schema，在模板中填写主题化蓝图后运行 `kp submit-outline --workspace <workspace> --from <template.yaml>`。`required_units` 可省略，CLI 会按 `chapter_archetype` 填充默认值；只有确需偏离默认教学单元时才显式填写公开 Schema 中的值。提交成功后将生成的 `planning/curriculum-blueprint.md` 交给用户审批。
9. 用户批准蓝图后，运行 `kp compile-contracts --all --workspace <workspace>`。不得逐章手填标题或自行创建合约。
10. 若 `kp status --json` 返回 `BLUEPRINT_CONTENT_BINDING_STALE`，只可填写当前 Brief 对应的蓝图输入并运行 `kp amend-outline --workspace <workspace> --from <blueprint.yaml>`。该命令会归档旧蓝图和旧合约；提交后必须重新等待用户批准，再运行 `kp compile-contracts --all`。若旧蓝图缺少 `section_outline`，先运行 `kp outline-migration-draft --workspace <workspace>` 取得只读候选。经用户一次确认后，将确认树填入 v2 蓝图输入并运行同一 `kp amend-outline` 命令；不得直接提交候选、不得删除蓝图，也不得从正文自动覆盖蓝图。

## 禁止事项

- 不把未验证的市场判断写入蓝图。
- 不使用统一章节字数掩盖重点差异。
- 不写正文或自行批准蓝图。
- 不搜索用户目录、其他工作区、二进制、源码或网络来猜测字段枚举；字段含义仅以 `kp outline-schema` 为准。

## 确定性验收命令

`kp submit-length-recommendation --workspace <workspace> --from <recommendation.yaml>` 和 `kp submit-outline --workspace <workspace> --from <template.yaml>` 必须成功；批准后运行 `kp compile-contracts --all --workspace <workspace>`，再以 `kp status --json --workspace <workspace>` 确认当前状态。

## 人工审批点

必须等待用户批准 `planning/curriculum-blueprint.md`。

## 用户交互边界

只向根 Skill 交付可审阅课程结构和修改摘要。不得让用户填写 `required_units`、运行提交命令或查看蓝图 Schema；根 Skill 负责呈现“继续撰写样章/修改”的自然语言决定。

## 下游交接条件

蓝图批准并完成合约派生后，交给 `kp-research-verifier` 和 `kp-chapter-writer`。
