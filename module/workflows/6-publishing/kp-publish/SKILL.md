---
name: kp-publish
description: Assemble Markdown and create an auditable delivery manifest.
---

# 发布工作流

1. 执行 `kp assemble --workspace <workspace>`。
2. 用 Quarto 将工作流生成的规范源渲染为 `delivery/book.pdf`；不得手动替换 `book.qmd`、封面或样式文件。
3. 执行 `kp validate-delivery --workspace <workspace>`，确认读者内容和 PDF 样式契约均通过。
4. 执行 `kp publish --workspace <workspace>`。
5. 仅交付 `delivery/delivery-manifest.yaml` 列出的最终文件。
