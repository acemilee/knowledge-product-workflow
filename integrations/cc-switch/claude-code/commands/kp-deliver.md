---
description: 校验并交付当前知识产品。
---

调用 `kp-create` Skill 的 `delivery` 宏阶段，再由 `kp-publisher` 执行受控 `kp assemble`、`kp render-pdf`、`kp validate-delivery` 与 `kp publish` 链路；只执行当前 `next_actions`，质量失败时停止并以产品级摘要报告。不得向用户显示底层命令、路径、工单或质量报告细节。
