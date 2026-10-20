# Knowledge Product Workflow

[![CI](https://github.com/acemilee/knowledge-product-workflow/actions/workflows/ci.yml/badge.svg)](https://github.com/acemilee/knowledge-product-workflow/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

面向中文知识产品的本地、文件驱动工作流。它将宿主 Agent 的自然语言协作与确定性 CLI 质量门分开：宿主负责理解需求、编排专业 Skill 与展示用户决策；`kp` CLI 负责工作区、版本绑定工件、质量校验和可审计交付。

当前公开版本为 **[v1.4.2](https://github.com/acemilee/knowledge-product-workflow/releases/tag/v1.4.2)**，对应已部署并完成该轮工程验收的 r0.2 快照。`r0.2` 是施工计划修订号，软件包版本仍为 `1.4.2`。发布内容包括公开核心和14个公开 Skill；不含闭源参考创作扩展，也不含尚未完成验收的 r0.3 修改。具体范围、验证和限制见[发布说明](docs/releases/v1.4.2.md)。

本版继承批量来源登记、局部证据恢复、只读预检、视觉观察复用和提交恢复能力。默认正文生产仍按串行推进；真实宿主4～6并发资格和大规模性能目标尚未完成验收，发布本版不会解锁正式并行。默认产品命令不保存 API Key、不发起模型调用；可选的 Codex App Server 控制会话默认模型请求额度为0，只有显式设置额度并派单才可能消耗模型。开发、安装和对外发布分别执行，源码构建不自动更新宿主 Skill；软件发布也不代替每份书稿的用户批准。

1.4.2 在 Brief 中区分方法实践、实现和混合教程：方法课保留完整提示、输入输出、失败对照和迁移练习，完整程序可交有版本与实跑绑定的配套资源包；实现课仍保留必要代码。作者与编辑在原流程内检查阅读层次与节奏，不增加固定协调角色。本地编译默认300秒、显式上限600秒，长路径和代码断行保持内容；横向扫描只列疑点，最终仍须看图。详见[操作手册](docs/operator-runbook.md)。

交付前先执行`delivery-preflight`处理自动阻断，再展示准确版本请求本人确认。汇编失败沿原operation/transaction恢复，字体未知不静默替换，单页观察可受控派生而整份PDF批准不继承。1.4.1已实现范围及真实出片/性能待验状态见[更新记录](CHANGELOG.md)。

插图在Brief阶段确定：Codex订阅认证、当前GPT模型及原生生图/参考能力均可核实时，同次确认是否采用AI插图；其他Agent（如DeepSeek harness）默认vector。未确认不自动生图，不接生图API/MCP。新书按批准教学任务规划图数，每个实际AI制作任务最多2次且串行；首次N、无消费书上限2N，修订时保留历史按C＋R；达到额度、未知调用未结算或订阅限流时停止。原生图像token不可得时如实记录未知，不把调用次数当作实际token账单。

新版提供逐章租约与工单自动退休、章节只读预检、独立预览修复、公开命令索引，以及 v6 出版设计和逐页视觉审阅。新书在 Brief 确认品牌色，默认橙色，也支持紫色或经对比度校验的 HEX 色；目录、编号、提示框、表格和受控插图共用色板。旧 v4/v5 书稿保持原版式。

`kp-diagram-designer` 将语义输入转换为 D2 或固定 SVG 图型，并输出 PDF/PNG；新增 `kp-visual-reviewer` 量测实际页面、查看高分辨率样张并记录修订意见。模型观察和用户最终视觉判断分别留证，模型不能代替用户批准。命令参数可通过 `kp commands --all --json` 查询。

v1.3.1的图准入、预算、宿主接入、双层风格门和恢复顺序见[受控插图与阅读布局](docs/controlled-illustrations.md)。橙色流程图验收按用户决定保留未通过，不能据版本标签声称全部风格矩阵通过。字体需在部署机器上准备并通过`kp doctor`探测，字体文件不随软件包分发。

## 适用场景

- 为中文读者制作教程、训练营材料、面试指南或内部知识手册。
- 交付 Markdown，或使用固定版式生成 A4 PDF。
- 在 Codex、Claude Code 或其他支持加载本地 Skill 的宿主中，以自然语言推进工作流。

## 不是什么

- 不是模型 API 网关，也不替宿主选择或调用大模型。
- 不是任意 Markdown 到 PDF 的通用排版器；PDF 采用受控的 Quarto/XeLaTeX 样式契约。
- 不能替代领域专家对事实、版权、合规或最终客户交付的人工判断。

## 工作流

```mermaid
flowchart LR
  A[产品定位] --> B[课程设计]
  B --> C[样章确认]
  C --> D[内容生产]
  D --> E[质量闭环]
  E --> F[Markdown / PDF 交付]
```

用户只需要在三个产品级节点确认或提出修改：产品 Brief（包含出版设计预览）、课程蓝图和第一章样章。其余内容计划、发展性编辑、研究、练习、图表、中文终稿、结构校验和出版动作由根 Skill 在受控工作流中调度。

## 环境要求

| 能力 | 必需组件 | 何时需要 | 官方安装入口 |
| --- | --- | --- | --- |
| 基础 CLI | Python `3.13`、[uv](https://docs.astral.sh/uv/) | 始终需要 | [Python for Windows](https://www.python.org/downloads/windows/)；[uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/) |
| Agent 协作 | 支持本地 Skill 的宿主，例如 Codex 或 Claude Code | 使用 `kp-create` 等入口时需要 | 按所用宿主的本地 Skill 安装说明启用 |
| PDF 交付 | [Quarto](https://quarto.org/)、XeLaTeX 与中文字体 | `delivery_format: pdf` 或 `both` | [Quarto 安装说明](https://quarto.org/docs/get-started/)；[TeX Live for Windows](https://tug.org/texlive/windows.html) |
| 受控图表 | [D2](https://d2lang.com/) 与本机 Chromium、Chrome 或 Edge | 书稿包含流程、架构、时序等图表时 | [D2 安装说明](https://d2lang.com/tour/install/)；Windows 通常已包含 Edge |

只交付 Markdown、且不需要受控图表时，仅安装 Python 3.13 和 uv 即可。Windows 是当前经过完整验证的平台；新书默认中文正文PingFang SC、标题/强调PingFang SC Semibold、英文Inter、代码JetBrains Mono，需在本机合法准备并通过doctor核验。旧阅读布局1保留微软雅黑；缺新默认字体时仅经用户选择fallback才使用兼容字体。其他平台的 CLI 路径可以运行，但尚未作为正式支持矩阵承诺。

### Windows 中文输出乱码

Python 3.13 在 Windows 上默认按 ANSI 代码页（GBK）编码标准输出，而 PowerShell 7 终端按 UTF-8 解码，会造成中文路径与消息乱码，遇到 GBK 无法表示的字符（如数学符号）还会直接报 `UnicodeEncodeError`。这不是项目缺陷，建议一次性设置（永久生效，新终端无需重复配置）：

```powershell
setx PYTHONUTF8 1
```

之后新开的终端与 Agent 会话不再乱码。`tests/conftest.py` 已为 pytest 输出与测试内子进程做 UTF-8 兜底。

安装系统依赖后运行 `uv run kp doctor`。它会检查 Python 环境、Quarto、XeLaTeX、中文字体、D2 和图表浏览器，且不会下载、更新或修改任何系统工具。第三方工具不随本仓库打包，应由用户从上述官方入口自行安装。

## 快速开始

从源码运行：

```powershell
git clone https://github.com/acemilee/knowledge-product-workflow.git
cd knowledge-product-workflow
uv python install 3.13
uv sync --group dev
uv run kp doctor
```

创建一个隔离书稿工作区：

```powershell
uv run kp init ai-agent-interview --root output
uv run kp brief "AI Agent 应用开发岗位面试资料" --workspace output/ai-agent-interview
uv run kp status --workspace output/ai-agent-interview
```

在已启用本地 Skill 的宿主中，使用根入口即可：

```text
/kp-create AI Agent 应用开发岗位面试资料
```

在 Codex 中，使用客户端提供的 Skill 调用方式触发 `kp-create`；若客户端不提供斜杠入口，直接点名该 Skill。普通用户不需要运行章节、审稿、哈希或工单命令。

本地 Skill 的安装方式由宿主决定；工作区、恢复和运维说明见[操作手册](docs/operator-runbook.md)。

## 质量与交付

- Brief、蓝图、章节、图表、质量报告与交付物均做版本绑定，源文件变化会使派生物和旧审批自动失效。
- `kp submit-chapter` 检查章节编号、顺序、标题树、教学单元、内部标记泄漏和章节合约一致性。
- 中文终稿检查与确定性质量门用于减少重复、占位符、来源台账、Claim ID 与 `.kp-*` 标记泄漏到读者交付物。
- 出版设计使用受控 profile：主题化色彩、阅读密度、页眉/章节开场和组件强调度均由白名单 token 映射到固定模板，不允许模型注入 TeX/CSS。
- PDF 固定走 `Markdown -> Quarto/Pandoc -> XeLaTeX -> PDF`，并校验封面、目录、书签、字号、页数、图注、图表资产和版本绑定预览集。
文本token预算门已于2026-10-07按用户决定取消；无全书／章节／动作额度、预警线、预算预约或追加预算审批。写稿和审核按批准教学目标负责内容质量，实际缺项与用户审批继续阻断交付。旧文本账本保留历史，AI生图调用次数规则保持独立。
- 默认审稿策略是确定性检查。只有宿主提供可验证的父子任务回执时，`independent_review_required` 才会作为真正独立审稿启用；否则会在批量写作前友好阻断。

交付给客户的只有 `delivery/book.md` 和按需生成的 `delivery/book.pdf`。运行日志、来源台账、QMD、TeX、审稿报告和内部清单不属于客户交付物。

### 自定义封面和页脚

封面标签、页脚标签、版权行、副标题、发布日期和书稿版本是独立的出版信息，不属于 Brief 或正文。用户可直接对宿主说：

```text
将封面标签改为“星云学院”，页脚改为“仅供内部学习”，版权行改为“版权所有 2026 星云学院”，版本升级为 patch；只更新出版信息，不修改正文。
```

宿主会展示更新后的出版信息预览；用户确认后，工作流只重建 `book.md`、`book.qmd`、PDF 和交付清单，不会重新生成蓝图、章节、来源、审稿或样章。封面和页脚可设置为不同内容。字段、版本递增规则和运维入口见[操作手册](docs/operator-runbook.md#更新出版信息)。

## 项目结构

```text
src/knowledge_product_workflow/  Python CLI、状态机、质量门和 PDF 渲染逻辑
integrations/                    宿主集成资产；普通用户无需直接操作
docs/                            操作、工件与 PDF 验收文档
tests/                           可重复运行的单元、CLI 与回归测试
module/                          旧模块目录兼容索引，不是可部署 Skill 源
```

项目内的规范 `kp-*` Skill 包是唯一真源。不要直接安装或修改 `module/` 中的历史角色说明。

## 开发与验证

`tests/test_pdf_long_reading_v134.py` 是最终隔离包的长书验收：显式设置 `KP_TEST_CANDIDATE_RECORD` 指向含 `site_root`、`wheel_sha256` 的不可变候选记录，并使用该包运行时执行；未提供时跳过，表示该次运行未覆盖最终包验收。普通源码回归不依赖本机被忽略的 `work/` 工程目录。

```powershell
uv sync --group dev
uv run pytest -q
uv run ruff check src tests
uv lock --check
```

新功能必须同时提交可重复运行的测试源码。不要提交 `.venv/`、`tmp/`、`output/`、本地书稿、渲染 PDF、运行日志、密钥或客户资料。

### Skill 自检

验证 Skill 提示词与控制面的一致性，不需要再在空上下文中人工模拟宿主：

```powershell
uv run pytest tests/test_skill_contract.py -v       # SKILL.md/手册 ↔ CLI 命令与选项契约
uv run pytest tests/test_skill_self_check.py -v -s  # 宿主模拟黄金路径 + P0 恢复闭环（需 Quarto/XeLaTeX/D2）
```

`test_skill_contract.py` 随默认测试套件运行，零外部依赖，自动抓提示词引用了不存在的命令、拼错的选项或过时的 Skill 元数据。`test_skill_self_check.py` 在隔离工作区用真实 CLI 完成从 Brief 到交付的完整流程，并验证交付质量报告过期后的恢复工单闭环；需要本机 Quarto、XeLaTeX、中文字体（Windows 默认微软雅黑）、D2 与 Chrome/Edge（`kp doctor` 全绿）才运行，环境缺失时自动跳过，可设置 `KP_SELF_CHECK_REPORT_DIR` 输出自检报告。

### 成本模式

文本token预算门已于2026-10-07按用户决定取消；无全书／章节／动作额度、预警线、预算预约或追加预算审批。写稿和审核按批准教学目标负责内容质量，实际缺项与用户审批继续阻断交付。旧文本账本保留历史，AI生图调用次数规则保持独立。

## 文档

- [Skill 架构与职责](docs/skills.md)
- [操作手册](docs/operator-runbook.md)
- [工作区工件说明](docs/workflow-artifacts.md)
- [PDF 视觉验收契约](docs/pdf-visual-acceptance.md)
- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)
- [变更日志](CHANGELOG.md)

## 隐私与安全

KP 本身不会保存或传输 API Key。由宿主 Agent 或其模型提供商处理的提示词、书稿内容和联网行为，受该宿主与服务商的配置及隐私政策约束。不要把客户机密、真实凭据或个人信息写入 Git 仓库、示例、Issue 或公开日志。

## 许可证

本项目采用 [Apache License 2.0](LICENSE)。


v1.3.2候选新增有限辅助色板、概念颜色记忆、中性母版及配色参考、图级候选更新与新版信息封面。当前455项自动回归通过，但真实双后端与简短输入PDF视觉验收仍有未通过项；本版本是工程快照，不代表已部署或全部P0关闭。旧版本与失败预算保留。


v1.3.3为上一开发候选：增加具体Brief、实际结构审核、逻辑阅读块分页、失败资产隔离和总额内收尾预留。该版新书默认教学Rubric2，用六项具体观察检查信息增量、表达适配、内容互补、重复价值、可用性与层次；旧书升级后旧观察失效，实际比较单元变化只刷新相关证据。正式发布仍需要当前PDF的真实用户视觉判断；旧host-image风格未通过项不随版本号自动关闭。公开接口使用`kp brief-schema`、`kp developmental-review-template`、`kp developmental-review-schema`、`kp checkpoint-chapter-content`和`kp render-review-preview`。

文本token预算门已于2026-10-07按用户决定取消；无全书／章节／动作额度、预警线、预算预约或追加预算审批。写稿和审核按批准教学目标负责内容质量，实际缺项与用户审批继续阻断交付。旧文本账本保留历史，AI生图调用次数规则保持独立。

混排采用固定字体角色与0.25em外部间距：正文中英、数字及行内公式的实际基准字级一致，公式仍用Latin Modern Math，代码仍用JetBrains Mono。Code、URL与公式内部数据不改。普通教程不显示固定依据／来源身份卡，后台核验继续有效；无关Claim台账更新不触发全书重审，真实引用和相关证据仍绑定版本。


## v1.4.0 隔离并行生产基础

新书请求自适应、旧书保持串行；`kp production-plan --workspace <书稿> --json`在写作前列公共约定、navigation/interface/result依赖、就绪与阻断。4/5/6为全部子代理共享候选总池；多章候选、滚动送审、私有证明、幂等和单写者提交保留原质量/人工门。当前真实宿主资格仍pending，实际原生生产保持串行，不能宣称已支持真实4~6或效率通过。`production-start --diagnostic`和模拟仅私有诊断，不满足生产或发布门；不自动安装、同步、迁移、重启或发布。
