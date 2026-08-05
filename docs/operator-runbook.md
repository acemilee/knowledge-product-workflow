# 操作手册

安装 Python 3.13 与 `uv` 后，创建开发环境：

```powershell
uv python install 3.13
uv sync --group dev
```

如需使用已有解释器，可将其路径传给 `uv venv --python <Python-3.13-路径>`；不要将某台机器的绝对路径写入项目文件。

启动本地书稿工作区：

```powershell
uv run kp init ai-agent-foundations --root output
uv run kp brief "AI Agent 应用开发" --workspace output/ai-agent-foundations
uv run kp status --workspace output/ai-agent-foundations
```

`kp brief` 只逐项询问目标受众与受众级别。页数、字数、语言、版式与交付格式由宿主模型建议，也可在结构化输入或具名参数中覆盖；未提供页数或字数不会阻断流程。页数可填写精确值（如 `20`）、区间（如 `10-20`）、下限（如 `至少10页`）或不设上限（如 `不限`）；除非明确写“正文”，它表示包含封面、目录、正文、附录和版权页的最终 PDF 总页数。字数可填写单点值（如 `40000`）或区间（如 `30000-50000`）。精确/区间页数会先由课程架构 Skill 给出教学篇幅建议；明显超过建议上限时，用户只需自然语言选择“采用建议”“保留并扩展”或说明新的页数/范围，系统不允许用同义重复凑页。下限和不限页数不会被伪造为机械的章节平均页数目标。核对汇总后输入“是”或“确认”；输入其他内容不会创建 Brief 文件。

审批身份默认在后台保存为“用户”，仅用于审计，不再作为用户需要回答的字段。根 Skill 必须先展示当前 Brief、课程结构或样章版本，再将用户“批准”“可以”“继续”“继续生成全书”等自然语言回复记录为版本绑定决定；“修改：……”或具体修改意见进入受控修订。用户不需要知道或执行任何批准命令。

在非交互式终端或 CI 中，必须显式指定目标受众与受众级别；页数和字数可以留空。该入口先生成 `runs/brief-preview.json`，只有显式 `--confirm` 才写入正式 Brief：

```powershell
uv run kp brief "AI Agent 应用开发" --workspace output/ai-agent-foundations `
  --no-interactive --target-words 30000-50000 --audience-level 专家 --delivery-format 两者 --confirm
```

也可使用字段可乱序、拒绝未知字段的 JSON/YAML。宿主必须把输入写入 `<workspace>/runs/brief-input.json`，并使用绝对路径：`kp brief "<主题>" --workspace <workspace> --from <workspace-absolute>/runs/brief-input.json`。命令位置参数已提供主题时，JSON 可省略 `topic`；如果两处都提供，必须完全一致。`target_pages` 和 `target_words` 均支持精确值或 `最小-最大` 区间。输入文件中的 `topic`、`language`、`layout_profile`、`audience_level`、`delivery_format` 支持中文显示值；宿主以相同输入加 `--confirm` 物化可审阅 Brief，再向用户展示并收集一次自然语言决定。`--confirm` 是后台物化动作，不是要求用户学习的确认命令。交互式 `kp brief` 检测到重定向 stdin 会立即失败，不能用多行管道“喂答案”。

任何中断后的恢复都先执行：

```powershell
uv run kp status --json --workspace output/ai-agent-foundations
uv run kp route --workspace output/ai-agent-foundations --write-work-order
```

`kp status` 只读取已落盘的 Brief、蓝图、章节、审稿报告、质量报告、交付物和运行事件；JSON 会输出阶段、阻断项、有效交付约束 `effective_constraints`、建议 Skill 和可执行的 `next_actions`，不会猜测模型是否完成工作。宿主只能执行 `next_actions`，不得自行拼装审批、渲染或恢复命令。`kp route` 不调用模型；加入 `--write-work-order` 后才会在 `runs/work-orders/` 生成受限工单并记录事件。

### v1.1 宏编排入口

普通用户由 `/kp-create`、`/kp-continue` 与 `/kp-deliver` 驱动，不需要执行本手册后续的逐章恢复命令。新工作区在 `kp init` 时已进入 `managed` 编排模式并创建初始工单，不会被误判为 legacy。根 Skill 先读取 `kp status --json` 的 `macro_phase`、`user_summary`、`required_human_decision`、`next_actions` 与 `current_work_order`，再创建或复用版本绑定工单：

```powershell
uv run kp orchestration-plan --workspace output/ai-agent-foundations --json
```

工单位于 `runs/orchestration/work-orders/`，记录输入清单、允许读写范围、章节目标、过期时间和完成证明；用户不需要读取或编辑它。正文阶段同一章节只有一个作者租约，练习、图表和中文终稿仅在该租约内协作。专业 Skill 完成当前动作且状态已经迁移后，根 Skill 使用 `kp complete-orchestration-work-order` 写入证明；出现输入变更、租约冲突或过期时回到 `kp status --json`，不手工修补 JSON。

当 `status --json` 包含 `user_decision` 且状态为 `request_required` 或 `stale` 时，根 Skill 先展示完整当前产物，再在后台登记展示版本。用户自然回复“批准”“继续”或“修改：……”后，根 Skill 记录决定并调用对应受控转换。旧决定的 SHA、版本或依赖发生变化时自动失效；根 Skill 重新展示最新版本，不得把身份配置、用户沉默、合规通过或历史回复当成确认。

真实旧工作区不会被自动迁移。先运行只读预览，向用户说明只新增控制记录且不重写正文、审批、质量报告或交付物；获得一次确认后才加入 `--adopt-legacy`：

```powershell
uv run kp orchestration-migration-preview --workspace output/ai-agent-foundations --json
uv run kp orchestration-plan --workspace output/ai-agent-foundations --adopt-legacy --json
```

`independent_review_required` 在 Brief 审批前必须通过真实宿主子任务能力预检，且质量阶段仍验证真实回执。没有能力时选择 `deterministic_only` 或更换宿主；不能以同会话角色切换、手填 JSON 或假回执降级质量门。该预检是不可绕过的前置门禁：失败时不会创建蓝图、章节、审稿批次或交付物。

### 受控 Brief 与蓝图修订

已批准工件不能直接编辑、删除或覆盖。用户修改交付格式、页数、语言或版式等 Brief 交付约束时，宿主生成修订预览、展示给用户并等待一次自然语言“继续/修改”决定：

```powershell
uv run kp amend-brief --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/brief-amendment.yaml
uv run kp approve-brief-amendment --workspace output/ai-agent-foundations
```

包装修订会归档旧 Brief，保留当前蓝图、章节与来源绑定；当 `delivery_format: both` 且 `delivery/book.pdf` 缺失时，`kp status --json` 会给出 `PDF_REQUIRED_AND_MISSING` 和 `kp render-pdf`。主题、目标受众、语言、页数/字数预算等内容型修订会使蓝图进入待修订状态，此时填写新蓝图后运行：

```powershell
uv run kp amend-outline --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/curriculum-blueprint.revision.yaml
```

该命令会归档旧蓝图和旧章节合约、写入待审批的新蓝图；用户批准新蓝图后再运行 `kp compile-contracts --all`。不得删除 `planning/` 工件、直接改 SHA 或通过重建工作区绕过该流程。

## 更新出版信息

封面标签、页脚标签、版权行、副标题、发布日期和版本号不属于 Brief。它们可以在书稿完成后独立修改，且封面标签与页脚标签没有绑定关系。

普通用户只需向宿主说明目标和影响范围，例如：

```text
封面标签使用“星云学院”，页脚使用“仅供内部学习”，版权行使用“版权所有 2026 星云学院”，版本升级为 patch；不要修改正文。
```

宿主先根据一次自然语言请求写入当前工作区的出版信息输入文件并展示预览；用户回复“确认”后，使用同一输入加 `--confirm`。该命令只重建 Markdown/QMD/PDF/manifest 等派生交付物，不会重走蓝图、章节、来源、审稿或审批：

```powershell
uv run kp update-publication --workspace output/ai-agent-foundations `
  --from output/ai-agent-foundations/runs/publication-update.yaml
# 用户确认后：
uv run kp update-publication --workspace output/ai-agent-foundations `
  --from output/ai-agent-foundations/runs/publication-update.yaml --confirm
```

输入仅允许 `subtitle`、`cover_label`、`footer_label`、`copyright_line`、`edition_label`、`publication_date` 和必填的 `version_bump`。`version_bump` 必须为 `patch`、`minor`、`major` 或 `none`；SemVer 版本分别递增为 `v1.0.1`、`v1.1.0`、`v2.0.0` 或保持不变。新书默认封面标签/页脚标签为“知识产品工作流”、版权行为“版权所有 {年份} 知识产品工作流”、版本为 `v1.0.0`。`edition_label` 与 `version_bump` 不能同时指定。

运维或自动化宿主必须使用版本绑定的输入文件，不应让用户直接编辑 `planning/publication-profile.yaml`。例如：

```yaml
cover_label: 星云学院
footer_label: 仅供内部学习
copyright_line: 版权所有 2026 星云学院
version_bump: patch
```

`version_bump: patch` 仅将书稿版本从 `v1.0.0` 更新为 `v1.0.1`；它不等同于 KP 软件版本。书稿正文、蓝图或教学结构发生实质变化时，应先走对应的内容修订流程，再更新出版信息。

需要排查 PDF 环境时，先运行只读诊断而不是让宿主模型猜测环境：

```powershell
uv run kp doctor
```

宿主进行联网研究时必须设置最长 8 秒的超时。发生超时后立即向用户报告，并记录脱敏事件：

```powershell
uv run kp record-network-timeout --workspace output/ai-agent-foundations --endpoint-category official-documentation --timeout-seconds 8
```

该事件仅保存端点类别和超时秒数，不保存 URL、提示词或请求正文。

已批准的 `planning/product-brief.md` 是详细程度、交付和审稿策略的唯一来源。书稿语言可为中文（`zh-CN`）或英文（`en-US`）；未显式修改时，版式、受众和格式默认分别为 A4 标准版式、进阶、Markdown，审稿策略为 `deterministic_only`。只有明确设置 `review_policy: independent_review_required` 时，真实宿主父子任务回执才成为交付门。

以下命令属于根 Skill/运维后台动作，普通用户不执行也不需要看到。根 Skill 只有在当前版本已展示且收到自然语言继续决定后才调用它们：

```powershell
uv run kp approve output/ai-agent-foundations/planning/product-brief.md --workspace output/ai-agent-foundations
uv run kp outline --workspace output/ai-agent-foundations
uv run kp outline-schema
uv run kp submit-outline --workspace output/ai-agent-foundations --from output/ai-agent-foundations/planning/curriculum-blueprint.template.yaml
uv run kp approve output/ai-agent-foundations/planning/curriculum-blueprint.md --workspace output/ai-agent-foundations
uv run kp compile-contracts --all --workspace output/ai-agent-foundations
```

`kp outline` 只生成绑定当前 Brief 的空白 YAML，不生成通用课程内容；`kp outline-schema` 公开每种 `chapter_archetype` 可用、默认和禁止的教学单元，并要求每章填写 `section_outline`。该树是读者可见的编号标题真源：同级从 1 连续递增、子项使用父级前缀，正文必须按该树的编号、标题、层级和深度优先顺序逐项呈现。由 `kp-curriculum-architect` 仅依据当前工作区模板和该 Schema 填写主题化章节、体裁、教学单元、Claim、权重与小节树后才能提交。`required_units` 缺省时由 CLI 按体裁填入默认值；不需要搜索源码、二进制、网络或其他工作区猜测枚举。创建章节合约前必须先批准课程蓝图。存在字数目标时，蓝图会保存每章经审批的 `weight`、`word_min`、`word_target`、`word_max`、`rationale` 和 `required_units`；预算以教学权重分配，不会平均摊给每章。批准后，章节合约会原样继承这些字段。高风险事实必须先在合约中声明 Claim，再登记来源台账。

会改变受众、定位、章节权重、范围或结论的前提必须写入内部假设台账。`evidence_needed` 会阻断交付；获得来源后以同一标识追加 `verified` 并关联 Claim ID；无法证实时以 `reframed_as_case` 降级为读者可见的情境案例，不能再表述为真实、典型或多数情况：

```powershell
uv run kp assumption interview-weight --workspace output/ai-agent-foundations `
  --statement "系统设计在目标岗位中占较高考核权重" `
  --impact curriculum_weight --disposition evidence_needed --rationale "决定章节权重"
```

默认 `deterministic_only` 不需要审稿批次。若 Brief 明确要求独立审稿，才一次性冻结全部章节：

```powershell
uv run kp submit-review-batch --workspace output/ai-agent-foundations --all
uv run kp review-check --workspace output/ai-agent-foundations
```

该命令先在 `runs/review-batches/<batch-id>/` 冻结全部正文、合约和最小证据，再创建每章任务。控制面生成 SHA-256、章节版本、批次 ID、任务 token 与工作单，审稿输入不得手填这些字段。正文或合约变化时，只有受影响章节进入下一批次；用户不需要删除旧文件、复制哈希或逐章恢复。

在 `deterministic_only` 下，审稿可作为诊断，且 manifest 明确写入“未执行独立审稿”。在 `independent_review_required` 下，`kp submit-review-batch --all` 只接受真实宿主子任务回执；`host_skill` 模式的角色切换没有技术可证独立性，不能满足交付门。读者交付物始终不展示审稿记录。

### 章节合规与图表的受控提交

模型不得填写 SHA-256、章节号、内部 Schema、资产路径或内部清单。所有输入和源稿均必须位于当前书稿工作区。图表先由作者确定其教学目的，再执行受控顺序：

```powershell
uv run kp diagram-schema
uv run kp diagram-template 1 --workspace output/ai-agent-foundations --chapter-source output/ai-agent-foundations/runs/chapter-01-source.md --diagram-id agent-layers
# 只填写 runs/templates/chapter-01-agent-layers.diagram.template.json 的语义字段。
uv run kp insert-diagram-reference 1 agent-layers --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/chapter-01-source.md --diagram-input output/ai-agent-foundations/runs/chapter-01-agent-layers.diagram.input.json
uv run kp humanize-check 1 --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/chapter-01-source.md
uv run kp submit-diagram 1 --workspace output/ai-agent-foundations --chapter-source output/ai-agent-foundations/runs/chapter-01-source.md --from output/ai-agent-foundations/runs/chapter-01-agent-layers.diagram.input.json
```

`kp insert-diagram-reference` 从已填写图表输入中幂等写入 `![图注](assets/figures/<diagram-id>.pdf)`，同 ID 的 PNG、SVG、绝对路径、重复引用或工作区外源稿都会阻断。终稿编辑必须发生在引用写入后、图表资产渲染前；`kp submit-diagram` 不改写源稿，只预检最终源稿、当前合约、引用、节点、边、D2 和本机浏览器，随后以临时目录生成并原子替换内部 D2、SVG、读者 PDF/PNG、Spec 与清单。D2 是唯一图结构和 SVG 生成器；Chrome、Edge 或 Chromium 只在禁用后台网络与更新的无头模式下将已生成 SVG 离线转换为 PDF/PNG，以避开 D2 内置 Playwright 的运行时下载。最终书稿仍仅由 Quarto/XeLaTeX 渲染。这样编辑报告与图表清单绑定同一最终正文，避免过期循环。正文、合约或图表语义变化后需从图表模板命令重跑。

终稿编辑通过后，再生成章节合规模板：

```powershell
uv run kp humanize-check 1 --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/chapter-01-source.md
uv run kp chapter-compliance-schema
uv run kp chapter-compliance-template 1 --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/chapter-01-source.md
# 只填写 runs/templates/chapter-01-compliance.template.json 中到当前正文标题的语义锚点。
uv run kp submit-chapter 1 --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/chapter-01-source.md --compliance output/ai-agent-foundations/runs/chapter-01-compliance.input.json
```

模板旁的 `*.context.json` 列出当前标题候选、只读 `expected_section_outline`、合约要求的学习目标、教学单元和 Claim，供宿主选择，不属于提交输入。`HEADING_NUMBER_GAP`、`HEADING_NUMBER_OUT_OF_ORDER` 与 `HEADING_OUTLINE_*` 会阻断提交；诊断包含行号、实际标题、期望标题和唯一恢复命令。旧版完整 `ChapterComplianceSubmission` 或 `DiagramSpec` JSON 会返回迁移错误和 `runs/diagnostics/` 中的 UTF-8 诊断。旧蓝图缺少小节树时，先运行 `kp outline-migration-draft --workspace <工作区>` 生成只读候选，用户确认后写入 v2 蓝图输入并执行 `kp amend-outline`；不得直接提交候选、删除蓝图或从正文反写蓝图。恢复时只读取该诊断、当前模板、上下文与指定源稿，并执行诊断给出的下一条 `kp` 命令；不得手算哈希、搜索源码或安装目录、直接执行 D2，或以 PNG/SVG 绕过受控资产链路。

写完并批准样章后，执行校验、汇编和交付：

```powershell
uv run kp validate --workspace output/ai-agent-foundations
uv run kp assemble --workspace output/ai-agent-foundations
uv run kp render-pdf --workspace output/ai-agent-foundations # Brief 要求 PDF 时
uv run kp validate-delivery --workspace output/ai-agent-foundations
uv run kp publish --workspace output/ai-agent-foundations
```

`delivery/qa-report.json` 会记录所有可操作的质量 finding，并以 schema、报告 ID、校验阶段和输入 SHA-256 绑定当前版本；每次报告均归档在 `runs/quality-reports/`。`BOOK_WORD_RANGE_VIOLATION` 会在全书低于 `word_min` 或高于 `word_max` 时阻断；单章偏离其区间只产生 `CHAPTER_BUDGET_RANGE_WARNING`。`DUPLICATE_CONTENT_DETECTED`、`UNRESOLVED_TEMPLATE_DETECTED`、`INTERNAL_TRACE_LEAKED`、`UNDECLARED_HIGH_RISK_CLAIM`、`CHAPTER_COMPLIANCE_STALE` 和 `DIAGRAM_ASSET_UNBOUND` 分别阻止重复正文、模板残留、内部泄漏、无声明的高风险事实、过期章节证明和过期图表。报告出现 `blocked`、`invalid` 或 `stale` 时，执行 `kp status --json` 给出的 `kp validate` 或 `kp validate-delivery` 重跑；不得删除报告。读者书稿不展示来源台账、来源 ID、批次、哈希或任务路径；存在任意 blocker 时不能发布。

选择 PDF 或“两者”交付格式时，需单独安装 Quarto 和 XeLaTeX。`kp doctor` 与渲染器共用同一解析器，依次检查 `KP_QUARTO_PATH`、当前进程 PATH、持久化用户 PATH、用户默认安装目录、用户/机器级 Windows 注册表、系统默认安装目录及 x86 兼容目录，并以不超过 8 秒的本地 `quarto --version` 验证候选项。因此即使 Claude Code 或 Codex 在配置 PATH 前已启动，也能识别用户级 Quarto。工作流会根据规范 `delivery/book.md` 生成 `delivery/book.qmd`，并同时写入固定的 `knowledge-book-cover.tex` 与 `knowledge-book-style.tex`。`a4-standard` 使用 A4、`extreport` 文档类、默认 12pt 或显式 14pt、左右 16mm 与上下 18mm 页边距、封面、独立目录页、微软雅黑 CJK 字体、无标题横线的页眉页码、固定段落行距和自动折行代码样式；渲染后以生成的 `book.tex` 验证实际文档类与基准字号。渲染器会显式关闭 Quarto 的自动 TeX 包安装，避免在工作流中隐式发起网络下载；缺少包时应由运维人员先完成本机 TeX 环境安装。随后渲染并校验实际 PDF 页数：

```powershell
uv run kp render-pdf --workspace output/ai-agent-foundations
uv run kp validate-delivery --workspace output/ai-agent-foundations
uv run kp publish --workspace output/ai-agent-foundations
```

仅交付 Markdown 时不需要 Quarto 或 `book.pdf`。交付 PDF 或两种格式时，必须先生成 `delivery/book.pdf`、通过质量报告、固定 PDF 源契约和可导航的 PDF 书签，`kp publish` 才会创建 `delivery/delivery-manifest.yaml`。工作区在打开时会规范为绝对路径，渲染器显式固定工作目录，只接受 `delivery/book.qmd -> delivery/book.pdf`；若出现 `delivery/output/` 或 `delivery/delivery/` 嵌套落点，查看 `runs/pdf-render.log` 并从上游重跑，不得移动文件。用户或模型设定了页数预算时，PDF 必须达到该区间下限；精确页数等价于下限和上限相同。`kp assemble` 会冻结 `delivery/book-input-manifest.json`；发布重新校验 Brief、蓝图、合约、章节合规、编辑报告、图表、书稿、QMD、模板、QA 与 PDF 的哈希。清单只记录工作区相对 POSIX 路径，例如 `delivery/book.md`；任一文件被手改或清单为旧 schema 时，运行 `kp publish` 重建，禁止手工编辑。交付给客户的只有清单中列出的 `book.md` 和/或 `book.pdf`；QMD、TeX、审稿报告和台账不对外发送。

### PDF 视觉系统 v4

首次汇编还会创建 `planning/publication-profile.yaml`，并为当前渲染写入 `delivery/publication-metadata.yaml`、封面、目录、基础样式、读者组件和过滤器资产；这些文件的哈希会进入交付清单。章节源可使用 `.kp-source`、`.kp-key-takeaway`、`.kp-practice` 和 `.kp-note` fenced div，但只能使用完整围栏，不能出现裸 `{.kp-*}`。`book.md` 会降级为不含内部类名的通用 Markdown，`book.qmd` 使用合法 Div，渲染后 PDF 文本还会被检查是否泄漏内部标记。来源卡片在 PDF 中必须保留可点击外链。插图仅允许工作区 `assets/` 中的 PDF、PNG、JPG/JPEG，直接 SVG 必须先转换。完整写法与人工视觉验收见 [PDF 视觉验收](pdf-visual-acceptance.md)。
