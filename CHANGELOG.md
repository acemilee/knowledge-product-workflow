# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的格式，并使用[语义化版本](https://semver.org/lang/zh-CN/)。

## [1.2.0] - 2026-08-23

### Added

- 新增 `kp-publication-designer`、出版设计档案 v2 和受控 v5 PDF token 映射；历史 v4 profile 保持兼容。
- 新增章节内容块计划、`kp-developmental-editor`、决策/步骤/工作表组件及版本绑定发展性编辑报告。
- 新增 token 预算账本、缓存键、调用重试限制和 hidden CLI 控制面；默认 lean 模式防止新增 Skill 无边界消耗 token。
- 新增 PDF 预览集、预览哈希绑定与人工视觉复核前提；新增 Python 代码块语法检查。
- Skill 包扩展为 13 个 Skill，并校验共享资源 SHA-256、相对引用和暂存安装布局。

## [1.1.6] - 2026-08-08

### Fixed

- `kp publish` 在最终交付质量门含内容性 blocker（如 `PDF_PAGE_COUNT_NOT_MET`）时，报错列出真实 blocker 与修复动作，不再循环提示重跑 `kp validate-delivery`。
- `audience_level` 中文别名补齐（初级/初中级/中高级/高级）；非法值报错改为中文并列出合法别名，不再暴露英文 pydantic 枚举错误。
- Brief 只声明页数目标、未设置字数额门时，按页数估算章节 word 预算并提示，避免正文写完才发现页数不达标、拖到交付阶段返工。
- 重复运行 `kp outline` 的报错补充 `kp amend-outline` 修订路径。
- `kp status` 的 `blueprint_missing` 阶段会反映教学篇幅评估已提交，不再让宿主误判前置条件未满足。

### Changed

- 课程蓝图模板与图表 context 前置提交前绑定规则（主题一致性、篇幅评估前置、`scope_expansion_units`、word 预算），减少宿主提交报错返工。
- `kp status --json` 与 `kp orchestration-plan --json` 输出中的 workspace 绝对路径替换为 `<workspace>` 占位符，降低控制面载荷噪音。
- `kp-chapter-writer` 明确按章节合约 `word_min/word_target/word_max` 写作的篇幅预算要求。

## [1.1.5] - 2026-08-05

### Fixed

- 修复托管自动运行在交付质量报告无效、过期或未通过后无法创建 `validate-delivery` 恢复工单的死锁。
- 修复四节点及以上右向 D2 流程图在 A4 PDF 中因横向缩放导致文字过小的问题。

### Changed

- 新书稿的默认封面标签、页脚标签和版权行改为中性项目品牌“知识产品工作流”；现有书稿的出版配置不会被重写。
- 开源仓库补充 Apache-2.0 许可、治理文档、CI、Dependabot 与可移植安装说明。
