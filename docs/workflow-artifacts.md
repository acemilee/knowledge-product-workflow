# Workflow Artifacts

Each `kp init` workspace contains these directories:

- `planning/`: product brief and curriculum blueprint.
- `research/`: append-only `source-ledger.jsonl` evidence records.
- `chapters/`: chapter contracts, chapter drafts, and sample approval marker.
- `runs/approval-profile.json`: the one approval identity confirmed for this workspace; later approval events reuse it by default.
- `delivery/`: 质量报告、规范 Markdown、派生 QMD、PDF 和交付清单。PDF 交付时还会生成固定封面和样式 TeX 文件，它们只作为可再现渲染输入，不属于客户交付物。
- `runs/`: append-only `events.jsonl` audit log.

Markdown artifacts include YAML frontmatter. Do not edit a persisted artifact in place without recording the corresponding approval or workflow event.
