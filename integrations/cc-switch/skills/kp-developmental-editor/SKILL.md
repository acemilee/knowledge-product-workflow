---
name: kp-developmental-editor
description: 在章节初稿和中文终稿之间执行版本绑定的发展性编辑，检查读者任务、内容节奏与可复用工件。
user-invocable: false
metadata:
  version: "1.2.0"
  mode: host-driven
---

# KP Developmental Editor

## 用途与触发条件

在章节初稿、内容块计划和章节合约均已就绪后调用。它检查读者任务、内容块兑现、解释密度和工作表结构；不使用 AI 检测分数，不直接改写作者正文。

## 前置工件及状态

- 当前章节由 `kp-chapter-writer` 持有正文租约。
- 当前工作区存在已提交的 `chapter-XX-content-plan.yaml`、章节合约和章节源稿。
- 旧 v4 出版设计档案不强制执行本 Skill；v5 工作区必须拥有当前报告。

## 允许读取与写入

- 读取：当前章节源稿、章节合约、内容块计划和受控图表/练习建议。
- 写入：仅 `runs/editorial/chapter-XX-developmental-report.json`；不得改写 `chapters/chapter-XX.md`、合约、来源或质量报告。

## 必要步骤与条件分支

1. 运行 `kp developmental-check <章节号> --workspace <workspace> --from <章节源稿>`。
2. 检查每个小节是否兑现内容计划中的 reader job、opening mode、artifact 和 asset decision。
3. 将内容计划缺失、版本不一致、承诺的图表/表格/工作表缺失、工作表最小结构不足列为 blocker。
4. 将连续长解释段、重点滞后或强调密度偏高列为具体 warning 与编辑建议；不得把主观审美判断伪装成 blocker。
5. 作者修订正文、章节合约或内容计划后，旧报告自动过期，必须重跑。报告通过后才交给中文终稿编辑，再进入章节合规提交。
6. 默认 lean 模式只审样章和确定性门标记的章节；standard/premium 的额外审查必须先取得 token 预算授权，不能读取整本书。

## 禁止事项

- 不虚构第一人称经验、数据、来源、案例或读者反馈。
- 不把同一章节全文重复发送给多个编辑角色；使用内容计划、结构索引和变更片段。
- 不直接提交章节、创建审稿批次或向用户展示内部报告、哈希、命令和工单。

## 确定性验收命令

当前 v5 章节的 `kp submit-chapter` 必须接受版本绑定的发展性编辑报告；`kp validate` 与 `kp validate-delivery` 必须在正文、合约或内容计划变化后使旧报告失效。

## 下游交接条件

将当前报告和可操作修订建议交回 `kp-chapter-writer`；作者完成修订后交给 `kp-chinese-humanize-editor` 与 `kp-quality-editor`。
