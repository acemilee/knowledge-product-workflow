---
description: 从当前知识产品工作区的真实状态继续工作流。
---

调用 `kp-create` Skill 的恢复流程。先运行 `kp status --json --workspace <workspace>`，读取宏阶段、用户摘要、`user_decision`、下一次决定和 `next_actions`；需要时由根 Skill 运行 `kp orchestration-plan --json --workspace <workspace>` 创建或复用当前工单。若需要用户决定，先展示当前完整版本，再在后台登记并消费用户的自然语言“继续/修改”决定。不得猜测阶段、展示内部工单或跳过门禁。
