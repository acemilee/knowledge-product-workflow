---
name: kp-author
description: Create chapter contracts, sample chapter, and subsequent drafts.
---

# 正文创作工作流

1. 确认 `kp compile-contracts --all --workspace <workspace>` 已从当前批准蓝图派生全部章节合约；不得使用已废弃的 `kp contract`。
2. 使用 `kp draft 1 --workspace <workspace>` 创建第 1 章空白正文输入；章节标题只能来自当前合约。
3. 每段叙述性正文前必须有描述内容主题的具体 Markdown 标题；不得使用“正文”“来源”“来源台账”等泛化或内部标题。
4. 使用 `kp approve-sample --workspace <workspace> --by <editor>` 获得人工样章批准。
5. 仅在样章批准后使用 `kp draft <number> --workspace <workspace>` 创建后续章节草稿。
