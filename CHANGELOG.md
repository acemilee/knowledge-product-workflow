# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的格式，并使用[语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.5] - 2026-08-05

### Fixed

- 修复托管自动运行在交付质量报告无效、过期或未通过后无法创建 `validate-delivery` 恢复工单的死锁。
- 修复四节点及以上右向 D2 流程图在 A4 PDF 中因横向缩放导致文字过小的问题。

### Changed

- 新书稿的默认封面标签、页脚标签和版权行改为中性项目品牌“知识产品工作流”；现有书稿的出版配置不会被重写。
- 开源仓库补充 Apache-2.0 许可、治理文档、CI、Dependabot 与可移植安装说明。
