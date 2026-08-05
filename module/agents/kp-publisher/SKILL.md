---
name: kp-publisher
description: Build final Markdown and PDF delivery artifacts.
---

# 发布者

以 Markdown 为唯一规范源。仅在质量报告通过后发布。PDF 必须通过工作流生成的 `delivery/book.qmd`、固定封面和固定中文样式文件由 Quarto 渲染；不得让模型自由修改 PDF 样式。随后创建 `delivery/delivery-manifest.yaml`，其中只校验最终交付的 Markdown 和 PDF 文件。
