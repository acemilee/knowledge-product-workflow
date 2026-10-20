# 工作流工件

每个 `kp init` 书稿工作区包含以下目录与内部工件：

- `planning/`：产品 Brief、课程蓝图、出版 profile 和篇幅评估。
- `research/`：追加式来源、假设与主张分类台账。
- `chapters/`：章节合约、草稿、内容计划、编辑/合规证明和样章审批。
- `runs/approval-profile.json`：当前工作区确认的审批身份，后续决定默认复用。
- `delivery/`: 质量报告、规范 Markdown、派生 QMD、PDF 和交付清单。PDF 交付时还会生成固定封面和样式 TeX 文件，它们只作为可再现渲染输入，不属于客户交付物。
- `runs/`：追加式 `events.jsonl` 审计、编排工单/租约、token 预算、诊断与候选源稿。
- `runs/pdf-preview/`：当前 PDF 的完整预览与哈希清单；独立预览修复不改 PDF。
- `runs/visual-audit-report.json`、`runs/visual-audit-pages/`：v6 逐页量测、抽样截图、观察和修复归属；人工复核独立绑定当前版本。
- `runs/diagrams/`、`assets/figures/`：受控语义输入、图源、Spec/清单和 PDF/PNG 双格式资产。

Markdown 工件带 YAML frontmatter。持久化工件应通过相应审批、修订或派生命令更新；不能手写哈希、工单完成证明或最终交付清单。
