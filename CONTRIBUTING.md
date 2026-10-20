# 贡献指南

感谢你考虑为 Knowledge Product Workflow 贡献代码或文档。

## 开始前

1. 先在 Issue 中说明缺陷、改进目标或设计取舍；涉及状态机、质量门、PDF 契约或 Skill 编排的改动，先附最小复现与预期行为。
2. 不要在 Issue、提交、测试夹具或截图中包含客户书稿、密钥、个人信息、绝对本机路径或受版权保护的未授权内容。
3. 项目内的规范 `kp-*` Skill 包是唯一可部署的真源；`module/` 仅用于旧模块索引兼容。

## 本地环境

```powershell
uv python install 3.13
uv sync --group dev
uv run pytest -q
uv run ruff check src tests
uv run python .github/scripts/check_ruff_baseline.py
uv lock --check
```

PDF 与 D2 图表属于可选的本机集成依赖。若改动涉及 PDF、Quarto、XeLaTeX、D2 或浏览器转换链路，请运行 `uv run kp doctor` 并在 PR 中说明验证边界。

原生测试分别标记为 `requires_poppler`、`requires_pdf_tools`、`requires_svg_tools`、`requires_d2`。依赖或合法字体缺失会明确跳过并说明未执行；不是原生通过。`uv run pytest -q -ra`显示全部跳过原因；准备好工具与授权字体后，同一命令会实际执行这些断言。synthetic导出替身不得宣称真实字体或真实产品批准。

全局Ruff目前有19项固定历史问题。CI执行上述基线检查脚本，严格要求零新增，按文件、规则、消息和原语句保留重复计数；不会自动接纳新基线。直接运行Ruff仍显示历史问题，不代表全局零问题。

## 提交要求

- 保持改动小而聚焦；不要顺带格式化无关文件。
- 对行为变化添加或更新 `tests/` 下的可重复运行测试。测试源码是发布资产，不是应排除的运行痕迹。
- 运行上述测试、Ruff 和锁文件检查；PR 描述应记录已执行的验证与未覆盖的风险。
- 用户可见文案默认使用中文，不暴露工单、哈希、内部路径、审批身份或 Schema 等控制面细节。
- 生成书稿、PDF、截图、`tmp/`、`output/`、`runs/`、本地工具安装目录和客户资料不得提交。

## Pull Request

请使用仓库模板，并说明：问题、解决方式、测试、兼容性影响和需要人工复核的项目。维护者可能要求将重大状态机或交付契约改动拆分为更小的 PR。
