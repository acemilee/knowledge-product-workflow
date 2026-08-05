---
name: kp-diagram-designer
description: Render controlled D2 diagram assets for knowledge-product chapters.
---

# 图表设计

将流程、分层、对比、时序和生命周期关系提取为 DiagramSpec，并通过 `kp submit-diagram --from <spec.json> --chapter-source <source.md>` 离线生成 PDF/PNG 图表资产。正文只能引用 `assets/figures/<id>.pdf`，不得保留 ASCII 字符画、SVG、远程图片、内部 D2 源或哈希。图表改动后重新执行中文终稿检查和章节提交。
