# 操作手册

## v1.4.2 读者任务与代码呈现

Brief 先确认 `teaching_focus`：`method_practice`、`implementation` 或 `mixed`。方法类教程保留完整提示、输入输出、失败对照和迁移练习；完整运行器可放配套资源，正文只保留讲清机制所需的片段。实现类教程仍可保留必要完整代码，不为所有题材设统一行数硬上限。旧书需经原 Brief 变更与确认流程显式采用，升级不自动改正文。

内容计划中的 `artifact_kind: prompt` 不要求附程序；`artifact_kind: code` 在新政策下须说明 `code_delivery`，选择 `inline_complete`、`excerpt_with_resource` 或 `resource_only`。资源路径限定本书 assets，片段按正文围栏序号与完整程序行号精确绑定；缺包、错绑、陈旧实跑证据不能发布。配套 ZIP 和成员清单随汇编事务生成，进入交付 QA 与最终交付清单；publish 不隐式补包。当前自动实跑只支持Python；其他语言资源可读但实跑未知时仍拒绝完整资源交付，不能把打包成功当已验证。

已有作者和编辑同时检查教学层次、阅读节奏、必要代码与完整练习，不增加固定审核角色或模型回合。程序、提示、数据和命令分开统计，聚合统计不因拆围栏变少；统计只定位复核，不能证明教学质量。提示与相关输入输出相邻，练习附参考与迁移，不强制卡片或图表配额。

本地 PDF 编译默认 300 秒；仅 `render-pdf --compile-timeout` 可显式设 1—600 秒，网络仍为 30 秒，失败不自动延长或重试。横向文字扫描只列页码、坐标与原文候选，必须看当前清晰页确认。视觉读视图仅限单次命令，完整字节与写前新鲜性核验保留；新规则的报告须重新量测，历史记录保留。

`kp outline-schema --json` 直接给实际蓝图输入模型，其中 `shared_context.cases` 为案例 ID 到对象的映射；`kp commands --all --command pdf-observation-template --json` 给该命令的精确参数，模板命令本身已输出 JSON，不再加 `--json`。

## v1.4.1 r0.5 操作变化

首次章节计划使用`content-plan-template <章号> --json`，一次取真实提交Schema，原文本输出兼容。新书证据政策1要求教学检查附当前单元原文引用与具体判断；视觉区分概览/细查、page/context，新Schema以当前模板为准，不用脚本默认passed。旧书显式`adopt-reading-policy --confirm`才迁移，已发布历史不自动撤销。

章末偶发空白可容忍；图表挤页致前页大白需修，同小节可浮动回填、长表可合法续页。35%只是跨页核查候选，不是密度自动质量分。

PDF提问前先request-user-decision明确scope，回复后record-user-decision记录实际scope再记录复核，不能把一般批准当本人全页覆盖。终检一次，publish复用完整current QA；报告缺失/陈旧/旧规则时先validate-delivery，不在publish隐式重算。需要续作时recovery-view读取operation_diagnostics并沿原op/tx处理；close-failed-operation须reason和完整证据，不自动结案。PowerShell前步非零立即停止依赖链，复用一次status --json结果，避免重复深扫。

## v1.4.1 出版前检与失败短路

源稿质量通过后汇编、渲染、量测并实际检查页面，再执行`kp delivery-preflight --workspace <书稿> --json`。`ready_for_human_review=true`仅表示可以展示该版请求本人判断；修改观察/正文/版式后重新前检。本人确认后记录当前PDF复核，再validate-delivery，读取当前状态确认进入publish_pending或delivery_manifest_stale才publish。未改输入时不再次编译；最终源稿、报告、批准或输入绑定变化仍拒绝。

当前 PDF 人审先执行 `request-user-decision pdf` 展示绑定当前 PDF 和范围的问题，再以 `record-user-decision pdf --decision approve --response <用户原话>` 记录真实答复。默认只请求/确认 `visual`；宿主已全页观察时走此路径。若用户自行完整检查，必须在问题和答复中显式确认 `--scope visual --scope manual_full_coverage`；内容批准另加 `--scope content`，不能把一般“确认交付”扩为完整覆盖或内容批准。之后 `record-pdf-visual-review` 的确认项只能取已确认范围。当前失败观察、机器阻断及输入变化仍拒绝，两条合法覆盖路径在预检后均可完成，不因决定回执本身自废预检。旧未发布书使用本接口显式采纳，已发布历史不回溯改签。

`pdf-observation-template` 返回的每项含当前 `observation_input_sha256` 和 `page_image_sha256`；只填已实际看过的当前 pending 页，不移绑旧观察。出版更新应用必要派生后停在 `pdf_visual_audit`，继续量测、观察和预检，不隐式终检或发布。正文用语义图名，图号由出版层生成；导航引用不当作跨章结果消费，真实消费仍绑定来源。

PowerShell原生命令必须显式短路；ErrorActionPreference不能替代LASTEXITCODE。以下函数只执行命令和检查本次退出码，不生成审核结论：

```powershell
function Invoke-Kp {
    param([string[]]$KpArguments)
    & kp @KpArguments
    $kpExit = $LASTEXITCODE
    if ($kpExit -ne 0) { throw "KP动作失败，exit=$kpExit；先核原回执，不运行依赖动作。" }
}
```

例如先`Invoke-Kp -KpArguments @('delivery-preflight','--workspace',$workspace,'--json')`并读取其准备状态；不要把assemble/render/audit无条件串成分号链。validate与validate-delivery的退出0表示报告生成，不等于publishable；最终发布继续由真实质量、输入新鲜性和本人批准共同约束。

汇编可指定`--operation-id op-<唯一标识>`，一次原操作绑定一个实际tx。失败后只查询原operation-status/recover-operation；op和tx不是可互换标识。文件占用由本人关闭阅读器，混合态不能发布，不自动关闭软件、清理journal或重发正文。没有wait-owner只表示无可释放等待者，不能消除原未知。持续意图未登记时按recovery-view恢复，不把draft可用冒充自动生产已恢复。

新失败回执只有已保存完整 `precheck` 证据、尚未触及任何域事务时，可用 `close-failed-operation --operation-id <原op> --reason <原因>` 结案为 `closed_failed`；它不是成功、不修正文、不清理未知事务。无充分证据的旧失败、未知或未链接 journal 必须保留待核，不批量结案。回执书稿身份取 Brief 的 book_id，目录改名不改变书稿身份，仍核工作区绝对路径。

字体探针一次检查四字体，保留available/missing/timeout/compile_failed/probe_unavailable；未知不是缺失，也不授予改字体或重跑全链的权利。publication_stage_started/finished记录探针、解析、编译、预览各自耗时；returned仅是调用返回，不能当质量通过。完整真实工具链效果以独立实测为准。

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

也可使用字段可乱序、拒绝未知字段的 JSON/YAML。宿主必须把输入写入 `<workspace>/runs/brief-input.json`，并使用绝对路径：`kp brief "<主题>" --workspace <workspace> --from <workspace-absolute>/runs/brief-input.json`。命令位置参数已提供主题时，JSON 可省略 `topic`；如果两处都提供，必须完全一致。`target_pages` 和 `target_words` 均支持精确值或 `最小-最大` 区间。输入文件中的 `topic`、`language`、`layout_profile`、`delivery_format` 支持中文显示值，`audience_level` 支持固定中文别名映射（入门/初学者/初级/初中级→beginner，进阶/中级/中高级→intermediate，专家/高级→expert，详见 `detail_constraints.py` 的 `_CONSTRAINT_VALUE_ALIASES`）；宿主以相同输入加 `--confirm` 物化可审阅 Brief，再向用户展示并收集一次自然语言决定。`--confirm` 是后台物化动作，不是要求用户学习的确认命令。交互式 `kp brief` 检测到重定向 stdin 会立即失败，不能用多行管道“喂答案”。

任何中断后的恢复都先执行：

```powershell
uv run kp status --json --workspace output/ai-agent-foundations
uv run kp route --workspace output/ai-agent-foundations --write-work-order
```

`kp status` 只读取已落盘的 Brief、蓝图、章节、审稿报告、质量报告、交付物和运行事件；JSON 会输出阶段、阻断项、有效交付约束 `effective_constraints`、建议 Skill 和可执行的 `next_actions`，不会猜测模型是否完成工作。宿主只能执行 `next_actions`，不得自行拼装审批、渲染或恢复命令。`kp route` 不调用模型；加入 `--write-work-order` 后才会在 `runs/work-orders/` 生成受限工单并记录事件。

### v1.1 宏编排入口

普通用户由 `/kp-create`、`/kp-continue` 与 `/kp-deliver` 驱动，不需要执行本手册后续的逐章恢复命令。新工作区在 `kp init` 时已进入 `managed` 编排模式并创建初始工单，不会被误判为 legacy。根 Skill 先读取 `kp status --json` 的 `macro_phase`、`user_summary`、`required_human_decision`、`next_actions`、`current_work_order`、`pending_claim_ids` 与 `readonly_diagnostics`，只在缺少合法当前工单时创建或复用版本绑定工单：

```powershell
uv run kp orchestration-plan --workspace output/ai-agent-foundations --json
```

工单位于 `runs/orchestration/work-orders/`，记录输入清单、允许读写范围、章节目标、过期时间和完成证明；用户不需要读取或编辑它。正文阶段同一章节只有一个作者租约，练习、图表和中文终稿仅在该租约内协作。标准主动作成功由CLI自动完成原工单并创建合法后继，根消费成功回执／当前状态，不再另调手动完成命令。draft保持租约、只读查询不收尾，辅助更新按原授权刷新；正文检查点关闭正文工单但保留图待办。`kp complete-orchestration-work-order`仅保留明确获授权、满足原终态证明的人工恢复兼容入口。输入变更、租约冲突或过期时回到 `kp status --json`，不手工修补 JSON。

### v1.3.5 登记与复用顺序

研究第一次登记前读取pending_claim_ids，逐声明核验，再按来源身份／版本、支持范围及许可分组；同一证据组一次research重复指定--claim，当前有效覆盖不重登。候选源稿已有时先消费readonly_diagnostics的check-chapter --json，复用current/reusable证明，只刷新refresh_actions必要项，结构blocker先修正文；缺稿先draft。视觉模板只为pending_pages生成输入，reusable_pages保留；failed先修上游，uncertain交判断，无清晰图保留manual_required，空模板不签通过。只读查询和命令发现都不增加写权限。

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

默认 `deterministic_only` 不额外创建整章独立审稿子任务；必需的结构观察与技术检查保持。若 Brief 明确要求独立审稿，并行路径每章候选就绪即送审，不等待全书；原串行／显式诊断可使用以下全章批次入口：

```powershell
uv run kp submit-review-batch --workspace output/ai-agent-foundations --all
uv run kp review-check --workspace output/ai-agent-foundations
```

该命令先在 `runs/review-batches/<batch-id>/` 冻结全部正文、合约和最小证据，再创建每章任务。控制面生成 SHA-256、章节版本、批次 ID、任务 token 与工作单，审稿输入不得手填这些字段。正文或合约变化时，只有受影响章节进入下一批次；用户不需要删除旧文件、复制哈希或逐章恢复。

在 `deterministic_only` 下，审稿可作为诊断，且 manifest 明确写入“未执行独立审稿”。在 `independent_review_required` 下，`kp submit-review-batch --all` 只接受真实宿主子任务回执；`host_skill` 模式的角色切换没有技术可证独立性，不能满足交付门。读者交付物始终不展示审稿记录。

### 章节合规与图表的受控提交

v1.2 的新书在 Brief 出版设计预览确认后会启用内容计划与发展性编辑。它们不增加用户审批点：章节作者在自己的租约内按“内容计划 -> 初稿 -> 图表/练习 -> 发展性编辑 -> 中文终稿 -> 合规提交”推进。内容计划必须覆盖已批准 `section_outline`，并只在确实降低理解成本时要求表、图或工作表；发展性编辑报告与正文、合约、内容计划绑定，任一变化后必须重做。普通用户不执行以下内部命令：

```powershell
uv run kp content-plan-template 1 --workspace output/ai-agent-foundations
uv run kp submit-content-plan 1 --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/content-plan.yaml
uv run kp developmental-check 1 --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/chapter-01-source.md
```

文本token预算门已于2026-10-07按用户决定取消；无全书／章节／动作额度、预警线、预算预约或追加预算审批。写稿和审核按批准教学目标负责内容质量，实际缺项与用户审批继续阻断交付。旧文本账本保留历史，AI生图调用次数规则保持独立。

模型不得填写 SHA-256、章节号、内部 Schema、资产路径或内部清单。所有输入和源稿均必须位于当前书稿工作区。图表先由作者确定其教学目的，再执行受控顺序：

```powershell
uv run kp diagram-schema
uv run kp diagram-template 1 --workspace output/ai-agent-foundations --chapter-source output/ai-agent-foundations/runs/chapter-01-source.md --diagram-id agent-layers
# 只填写 runs/templates/chapter-01-agent-layers.diagram.template.json 的语义字段。
uv run kp insert-diagram-reference 1 agent-layers --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/chapter-01-source.md --diagram-input output/ai-agent-foundations/runs/chapter-01-agent-layers.diagram.input.json
uv run kp humanize-check 1 --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/chapter-01-source.md
uv run kp submit-diagram 1 --workspace output/ai-agent-foundations --chapter-source output/ai-agent-foundations/runs/chapter-01-source.md --from output/ai-agent-foundations/runs/chapter-01-agent-layers.diagram.input.json
```

`kp insert-diagram-reference` 从已填写图表输入中幂等写入 `![图注](assets/figures/<diagram-id>.pdf)`，同 ID 的 PNG、SVG、绝对路径、重复引用或工作区外源稿都会阻断。终稿编辑必须发生在引用写入后、图表资产渲染前；`kp submit-diagram` 不改写源稿，只预检最终源稿、当前合约、引用、节点、边、D2 和本机浏览器，随后以临时目录生成并原子替换内部 D2、SVG、读者 PDF/PNG、Spec 与清单。流程/时序/生命周期由 D2 生成；v6 的五种出版图型由固定 SVG 模板生成；Chrome、Edge 或 Chromium 只在禁用后台网络与更新的无头模式下将已生成 SVG 离线转换为 PDF/PNG，以避开 D2 内置 Playwright 的运行时下载。最终书稿仍仅由 Quarto/XeLaTeX 渲染。这样编辑报告与图表清单绑定同一最终正文，避免过期循环。正文、合约或图表语义变化后需从图表模板命令重跑。

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

选择 PDF 或“两者”交付格式时，需单独安装 Quarto 和 XeLaTeX。`kp doctor` 与渲染器共用同一解析器，依次检查 `KP_QUARTO_PATH`、当前进程 PATH、持久化用户 PATH、用户默认安装目录、用户/机器级 Windows 注册表、系统默认安装目录及 x86 兼容目录，并以不超过 8 秒的本地 `quarto --version` 验证候选项。因此即使 Claude Code 或 Codex 在配置 PATH 前已启动，也能识别用户级 Quarto。工作流会根据规范 `delivery/book.md` 生成 `delivery/book.qmd`，并同时写入固定的 `knowledge-book-cover.tex` 与 `knowledge-book-style.tex`。`a4-standard` 的旧版 v4/v5 使用 A4、`extreport` 文档类、默认 12pt 或显式 14pt、左右 16mm 与上下 18mm 页边距、封面、独立目录页、微软雅黑 CJK 字体、无标题横线的页眉页码、固定段落行距和自动折行代码样式；渲染后以生成的 `book.tex` 验证实际文档类与基准字号。渲染器会显式关闭 Quarto 的自动 TeX 包安装，避免在工作流中隐式发起网络下载；缺少包时应由运维人员先完成本机 TeX 环境安装。随后渲染并校验实际 PDF 页数：

```powershell
uv run kp render-pdf --workspace output/ai-agent-foundations
uv run kp audit-pdf-visual --workspace output/ai-agent-foundations # v6：预算授权后查看样张、记录观察
uv run kp record-pdf-visual-observations --workspace output/ai-agent-foundations --from output/ai-agent-foundations/runs/visual-observations.json
uv run kp record-pdf-visual-review --workspace output/ai-agent-foundations --by 用户
uv run kp validate-delivery --workspace output/ai-agent-foundations
uv run kp publish --workspace output/ai-agent-foundations
```

渲染后先查看当前 PDF 及 `runs/pdf-preview/preview-manifest.json` 对应的页面图片；只有用户确认这一版本的可读性后，宿主才记录视觉复核。若 PDF 已存在而预览清单或图片缺失，`kp status --json` 会给出 `kp repair-pdf-preview --workspace <workspace>`：先执行该动作，再展示修复后的页面并记录复核。`validate-delivery` 只检查当前证据，不生成预览；PDF 或预览改变后需重新判断，不能重复校验来代替修复。

仅交付 Markdown 时不需要 Quarto 或 `book.pdf`。交付 PDF 或两种格式时，必须先生成 `delivery/book.pdf`、通过质量报告、固定 PDF 源契约和可导航的 PDF 书签，`kp publish` 才会创建 `delivery/delivery-manifest.yaml`。工作区在打开时会规范为绝对路径，渲染器显式固定工作目录，只接受 `delivery/book.qmd -> delivery/book.pdf`；若出现 `delivery/output/` 或 `delivery/delivery/` 嵌套落点，查看 `runs/pdf-render.log` 并从上游重跑，不得移动文件。用户或模型设定了页数预算时，PDF 必须达到该区间下限；精确页数等价于下限和上限相同。`kp assemble` 会冻结 `delivery/book-input-manifest.json`；发布重新校验 Brief、蓝图、合约、章节合规、编辑报告、图表、书稿、QMD、模板、QA 与 PDF 的哈希。清单只记录工作区相对 POSIX 路径，例如 `delivery/book.md`；任一文件被手改或清单为旧 schema 时，运行 `kp publish` 重建，禁止手工编辑。交付给客户的只有清单中列出的 `book.md` 和/或 `book.pdf`；QMD、TeX、审稿报告和台账不对外发送。

### PDF 视觉系统 v4

首次汇编还会创建 `planning/publication-profile.yaml`，并为当前渲染写入 `delivery/publication-metadata.yaml`、封面、目录、基础样式、读者组件和过滤器资产；这些文件的哈希会进入交付清单。章节源可使用 `.kp-source`、`.kp-key-takeaway`、`.kp-practice` 和 `.kp-note` fenced div，但只能使用完整围栏，不能出现裸 `{.kp-*}`。`book.md` 会降级为不含内部类名的通用 Markdown，`book.qmd` 使用合法 Div，渲染后 PDF 文本还会被检查是否泄漏内部标记。来源卡片在 PDF 中必须保留可点击外链。插图仅允许工作区 `assets/` 中的 PDF、PNG、JPG/JPEG，直接 SVG 必须先转换。完整写法与人工视觉验收见 [PDF 视觉验收](pdf-visual-acceptance.md)。

## v1.3 控制面与恢复

`kp commands --all --json` 公开全部命令、参数、必填字段和用途；`kp outline-schema`、`kp content-plan-schema`、`kp claim-classification-schema` 提供结构与语义示例。

作者在同一章节工单/租约内运行 draft、写作、协作和提交；draft 不结束租约。样章确认后顺序完成下一章，无需创建全部空草稿。正式章节写入串行，独立只读核验和候选生成可以并行。输入只绑定实际章节依赖；确实变化时 `kp orchestration-plan --refresh --reason <原因>` 自动退休旧工单并路由。`kp cancel-orchestration-work-order` 的完整参数以命令索引为准；不要等待超时或手写完成证明。

最终正文和图语义确定后，依次执行 developmental-check、humanize-check、submit-diagram、`kp check-chapter <章号> --from <源稿> --json`、submit-chapter。编辑若改变正文，两个编辑报告与对应图资产均需刷新。预检区分缺失、过期和待生成资产，并提前检查工作表的使用说明、输入、输出示例、复盘。

普通文本中的不同总体百分比不求和。同总体分布用 `.kp-percentage-distribution`，声明“总体：”及表格“占比”列；校验按显示精度容差计算。作者建议/情境必须通过 classify-claim 显式绑定语义与标题，并在正文标记；未分类或分类失效默认 factual，搜索失败不自动降级。

| 状态/错误 | 恢复动作 |
| --- | --- |
| PDF 缺失或损坏 | render-pdf |
| PDF 存在而预览缺失/过期 | repair-pdf-preview |
| 视觉报告缺失/过期 | audit-pdf-visual，查看当前样张并记录观察 |
| 视觉 blocker/失败观察 | 按责任 Skill 修订章节或受控出版 token，再渲染复核 |
| 当前视觉判断缺失 | 根 Skill 展示当前版本，收集用户判断后 record-pdf-visual-review |
| 章节报告过期 | check-chapter 列明刷新清单，重跑当前证明 |

新书 v6 默认品牌色橙色，Brief 可以声明紫色或合法 HEX；旧 v4/v5 不迁移。Windows 中文工作区的真实 Quarto 运行使用临时 ASCII 驱动器别名映射同一书稿路径，结束后释放；不把书稿移到项目外，也不隐式安装依赖。完整视觉指标和人工判断边界见 PDF 视觉验收。


## v1.4.0 隔离并行生产基础

新书请求自适应、旧书保持串行；`kp production-plan --workspace <书稿> --json`在写作前列公共约定、navigation/interface/result依赖、就绪与阻断。4/5/6为全部子代理共享候选总池；多章候选、滚动送审、私有证明、幂等和单写者提交保留原质量/人工门。当前真实宿主资格仍pending，实际原生生产保持串行，不能宣称已取得生产4~6资格或整体效率通过。`production-start --diagnostic`和模拟仅私有诊断，不满足生产或发布门；不自动安装、同步、迁移、重启或发布。


## v1.4.0 原生接入工程入口（尚未取得生产资格）

2026-10-16：开发、安装和真实资格分别留证，安装版本以实际identity为准。真实模型小样与容量／效率试验需要用户明确授权；已有本轮授权时沿其范围执行，不重复请求同一许可。资格不足时status／production-plan继续显示pending、有效serial。既有Brief默认deterministic_only，桥接入口不改变审稿策略。

`kp commands --all --json --command production-host-connect`列零模型连接诊断；需当前书稿workspace、已核验Codex二进制路径和SHA。`production-host-replay --workspace <隔离书稿> --input <书稿内事件JSON数组> --parent <实际父ID>`仅回放归属／未知槽位，永不授予资格。

`production-host-session --workspace <隔离书稿> --binary <已核验codex.exe> --expected-sha256 <实际SHA> --model <明确模型>`建立单个长存活JSONL控制进程，默认`--model-turn-limit 0`。stdin一行一个动作，stdout一行一个`ok/result`或`ok/error/retry=false`；保持同一个进程，不能把每行动作拆成独立CLI。零模型示例：

2026-10-17 · 限定资格入口：实际权限测试须成对提供`--qualification-shell <已核验pwsh.exe>`与`--qualification-python <现有python.exe>`。程序在书稿内唯一私有目录创建自己持有的loopback执行服务，注册只读材料/程序运行时、禁网的命名权限；不运行系统沙盒安装，不自动登录或发模型请求。实际shell/目录/连通回执不符即停止。未配置该入口的qualification在模型派发前拒绝，不再让模型反复寻找不存在的工具。Python受控入口为`production_host.NativeQualificationHost`，须在初始化后register、由同一bridge显式绑定；字典、保存的JSON或其他连接不能授予执行范围。

运行时可读根不能包含资格保护区。原生二进制在启动任何服务前再次核对SHA；规范路径在进入和使用期间复核，私有目录改绑即停止。运行中不为每次派单重复读取整个二进制。

资格子线程先用`positive_probe_commands`证明指定材料可读，再按原脚本分别观察保护区读、写与网络拒绝，通过实际`functions.exec`内的`tools.exec_command`调用。原生命令动作须精确绑定，不因普通失败、缺文件或连接拒绝签权限通过；本地化拒绝按已知PermissionError/操作系统错误码核验。普通作者/语义/审稿turn显式清空执行环境并恢复readOnly，不能混批或在运行turn中切换资格模式。停止/断线保留未知槽位；服务退出即失去资格活性。单子组件通过、固定本地响应或配置值均不解锁正式4～6并行。

```jsonl
{"op":"status"}
{"op":"close"}
```

动作：create-parent（仅建受限元数据会话）；qualification/count、collect-qualification/timeout（未来获授权才可运行的固定read/write/network拒绝小样）；admit（仅接收该进程真实原生证据，配置／模型自述／回放均不授予）；start/parallelism（正式资格不足拒绝，diagnostic=true仅私有诊断）；resume/run_id；dispatch；collect/timeout；freeze/task_id；commit/task_id/context/compliance；recover/commit_id；status；close。context与compliance须为当前运行私有路径。父路由器用turn/start或turn/steer滚动接单；所有子角色共用总池，未启动回执、未知和未释放仍保留槽位。

作者派单前须存在当前内容计划，输入仅批准公共约定、本章与显式依赖；同来源多Claim按证据组登记。不向子节点提供共享写权限。收到作者稿后freeze运行原技术校验，依据missing只补实际结构观察与必要证明，审核使用同一候选全文及Rubric。collect可先返回已完成且绑定正确的单章产物；执行额度已归还后才正式接收／补位，不等同批全完。初次样章仍走原串行本人审批；批准样章不自动重写。

`--model-turn-limit`仅限制桥接发出的turn/start/steer请求数，不是底层HTTP次数、token或精确费用上限。私有CODEX_HOME不复制账户凭据；r0.8开发认证路由已在隔离实例实测，真实权限资格仍未通过；旧安装快照不自动包含开发修正。禁止盲试、提权或扩大权限。未知回执后不重发；request-audit.jsonl和host-events.jsonl及native-dispatch文件留在当前书稿runs/production，恢复先查实际终态。JSON不能恢复资格；新进程保留旧任务槽位，待真实会话重新核实。停止本地进程不等于远端子线程已释放。thread/shellCommand不继承线程沙箱，控制RPC白名单不包含它。


### 原生资格与低开销顺序（v1.4.0 r0.8）

1. 在唯一私有实例初始化并核对内存认证、元数据RPC及受限范围；ephemeral线程只查询`thread/read(includeTurns=false)`，所需接口通过零模型预检后再打开官方登录。`--parallelism`同时约束本实例请求线程数与工作流共享池；配置回执只是请求值，真实容量仍看运行区间。
2. 已授权试验中，先启用上述限定资格入口并`create-parent`（`auth-start`也会先完成必要预检），完成一次官方内存登录；正读成功后，固定read/write/network资格探针各只尝试一次原生受限调用。普通作者／审稿继续只用内联材料。工具缺失、拒绝或未知如实记录，不把模型说“无权限”当权限拒绝回执。
3. 显式`qualification/count`派出第一批，单程序owner用`collect-qualification/timeout`收原回执。单次观察窗口到期是pending，沿原任务继续观察；EOF／真实流错／总时限结束才保留unknown并停，不重新派单或重新登录试同一请求。
4. 第一批有效结果及执行容量归还后，再次`qualification/count`派同父池第二批；两批均取得实际running重叠及补位证据后才`admit`。4／5／6计全部同时运行子代理，父另计；累计创建数、未释放计数和配置值都不能签并发。
5. V1沿原close/shutdown回执；V2由同turn原生终态、任务绑定与同活连接的同父子idle元数据共同核执行额度归还，驻留对象仍可能保留。已完成结果可先落私有工件，release_pending不阻止保全结果，但阻止超池派单及正式接收。父控制面先发齐就绪批次再等待，结果按原生子事件收取，父最终只返回task_id与异常摘要。

### 隔离实例认证（v1.4.0，2026-10-15）

2026-10-17 · 原生独立审稿的模型、版本、输入摘要及身份由控制端填写，子代理只返回内容判断，不猜来源。实际子模型只取匹配父子归属的原生started事件；未报告时明确记`host-not-reported`。原生`prompt_hash`绑定本次父控制turn实际派发文本，审核记录另存`host_provenance.prompt_hash_scope=native_parent_dispatch_input`及`child_prompt_observed=false`，不声称看到了子代理完整提示词。来源同时绑定任务、版本、输入清单、派发波及父子turn；缺少本进程绑定则在接收落盘前拒绝。程序直接派发通道继续绑定其实际子输入，两种摘要范围不能混比；内容评分、教学单元与独立性门保持。

production-host-session新增auth-status、auth-start（mode为browser或device）、auth-poll（timeout至多30秒）、auth-cancel。在同一个控制进程内先查看auth-status；缺认证时auth-start返回官方登录挑战，前端仅向账号持有人展示/打开，不把挑战URL、一次性设备码或认证材料写入日志。由账号持有人在官方页面完成登录/授权，auth-poll匹配本次loginId完成事件后再次读实际账户状态；认证成功不等于并行资格通过。获得真实账户回执后才继续原受限父会话与六子通道自检。

宿主必须原生确认cli_auth_credentials_store=ephemeral；认证仅在该进程内存，不回落auth.json/keyring。子进程CODEX_HOME、CODEX_SQLITE_HOME及tmp/logs都在测试工作区，移除继承的OpenAI/Codex认证环境变量，不读取或复制生产auth，不退出/强制刷新生产账号。这里只支持官方ChatGPT browser/device登录，拒绝API key/外部token注入和未知登录重发；auth-cancel仅取消本进程自己的登录，notFound不冒已取消。结束进程销毁内存认证；下一新进程需要重新授权。未知权限/终态保持原硬门。
# v1.4.0 修正恢复入口（2026-10-15）

结果生产章重新接收不代表消费者旧证明仍有效：即使不显式引用章节编号，也按蓝图已声明的使用小节绑定真实结果意义。补齐受影响复核后，用 `kp recheck-chapter <章节号> --workspace <workspace> --compliance <当前映射>` 保持正文／版本更新证明；正文若不同于原提交快照会拒绝，不能借此跳过修订。独立审稿策略仍另检查，旧报告保留。

先运行 `kp recovery-view --workspace <workspace> --json` 核对原事务、保存稿和未知任务，再读status与当前工单。仅恢复原operation_id；不要重发未知动作、重建intent或从第一章重做。合作暂停不能保证宿主UI按钮已终止外部任务。

蓝图修订前用 `kp preview-change-impact --workspace <workspace> --from <候选蓝图> --json`；按实际变更领域复核。正文未变时用 `kp refresh-chapter-proofs <章节号> --workspace <workspace> --impact-id <impact_id>` 保留不可变证明链；无关章节不amend。结果依赖阻断消费者，独立后章可继续，最终门清算所有deferred_repairs。

旧书默认policy1，只能显式预览 `kp adopt-binding-policy --workspace <workspace>` 后由用户决定是否加 `--confirm`。旧书保持串行，旧依据归档，缺证明重新核验，已撤销批准不恢复。本命令不同于安装／部署；当前生产稿不得用于测试。新CLI书稿默认policy2，宿主资格pending时仍串行。


## 2026-10-16 · v1.4.0效率修正（工程行为已验证，生产资格待验）

- 2026-10-17 · `validate`、`assemble`、`validate-delivery`、`publish`在同一命令内复用刚完成授权的写前状态来验证当前工单，保留输入清单、生命周期与允许命令检查。创建/重路由工单或受控写入改变阶段后重新计算；领域输出生成后仍计算新的状态并沿原收尾与后继路径推进。各公共状态读取及其他动作不跨调用复用，不跳过质量或审批门。
- 本地CLI提交使用原进程／父工具句柄，由一个等待owner负责；不能额外创建只会sleep、询问进度或转发“仍在运行”的LLM协调者。提交可预先指定`--operation-id op-<唯一标识>`，加`--operation-events`在stderr取得早期ID与阶段JSONL；正常stdout兼容原格式。
- 等待先查`kp operation-status --workspace <workspace> --operation-id <原ID> --json`，或由一个程序执行`kp wait-operation --workspace <workspace> --operation-id <原ID> --timeout 60`。它只读固定回执／原事务，不用深status替代心跳；一个动作不多层轮询。正文提交与控制面收尾分别记录；断流／超时先核原进程与回执，domain完成只`recover-operation`补原事务／控制面，禁止重发submit-chapter或再次执行示例。unknown不猜结束、不释放槽位。
- 原生通道在同一认证与ledger进程使用有界`run_until_attention`；默认只收原回执，`authorized_actions`明确授权dispatch/freeze/commit后才可自动推进；commit还要同版本task_id/context/compliance，不能合成语义证明／用户批准。保持共享总池，失败／EOF／deadline保留未知，不重试未知派单。暂停不派新任务。
- U11真实宿主的无推理守候／可见反馈接线仍pending。程序本地heartbeat≤30秒不算真实进展；未经实际宿主证明不能宣称“零推理守候已接入”。当前降级为单父代理有界等待，按宿主可见能力满足≤60秒反馈，必要模型回合如实计入开销。
- 每个任务／候选版本创建新角色上下文，依据结构化handoff的task/version/manifest、完成项、待办、相关输入和unknown继续；不默认累积全书聊天，不为报进度反复读章。作者读批准公共设定、本章合约／计划和显式依赖。编辑／审稿读完整冻结当前章与全部必需比较单元；缺材料明确请求缺失范围，禁止猜测、截断必要原文或把摘要当原文。当前完整章及必要引用不能为节约Token被省略。
- 同一证据组先逐声明核验，再一次research重复`--claim`关联多项；当前Schema版本首次发现并复用，内容计划依本章template填写，禁止失败提交探索枚举。证明更新用refresh-chapter-proofs／recheck-chapter，按真实依赖局部复核，独立后续章继续；最终质量门仍必须收齐债务。

原因：同动作重复深检与路由、全书嵌套解析、仅按阶段计进展、领域已提交却缺控制回执、多层模型等待共同放大耗时。代码分别由verification_context、局部索引／ledger视图、finish_controlled_action、operation_receipts和run_until_attention承担；journal仍是领域事实，回执不授新权。真实宿主资格、U11反馈接线与生产效率结论分别记录，离线协议不能替代。

### v1.4.0 r0.8 诊断程序派单接缝

开发中的 `production_delegated_host.DelegatedInlineBridge` 是显式诊断Python入口，组合既有原生客户端和 `NativeProductionSession`；当前 `production-host-session` CLI仍使用原spawn桥接，不能凭此文档假定CLI或已安装Skill已切换。它不签生产grant，不接受admit/原spawn资格，禁止canonical提交及其恢复。

同一个私有连接先核受限范围/内存认证和本实例agents禁用配置，再 `create-parent` 建立fresh控制线程、`start`指定diagnostic=true建立原ProductionRun、`create-control`先持久记录再启动控制turn。`dispatch`复用原调度/精确envelope，同锁写claim后才直接发fresh worker；作者/语义核验/独立审稿共用4/5/6候选总池。结果必须经过原freeze/evaluate及版本绑定审核，不使用第二套质量门。

`collect`在程序内等待总观察窗，quiet一秒不唤醒父模型；有实际结果及时返回以继续冻结/补位，多个live idle查询共用剩余预算。持久terminal仅记录结果，执行额度由当前bridge活证据归还；EOF/新turn撤回已加载Run的释放权。`replay-results`只重放不可变私有结果对应的本地域步骤，不RPC、不重发、不恢复执行额度或权限。unknown继续保槽，不拿停止本地进程当远端释放。

2026-10-17 · 收件与质量处置分开：合法审稿回执要求修订时，`run_until_attention`可以返回`task_attention_required`，这不表示连接已关闭，也不表示其他已派任务失败。先停止后续派单、冻结及提交；若唯一owner、原连接/Run和输入归属仍有效，且未暂停/停止、原总预算尚余，可在同进程继续显式`collect`，只保存本次已派集合的有效回执。每次观察窗取原剩余总预算与30秒的较小值；quiet仅程序等待，不新增LLM协调者。不能通过新建CLI/重新登录来收同一未知任务，也不能为了收齐延长原实验预算。收齐后仍返回原修订原因，进入显式修订决策，不将诊断签为通过或自动补发修稿。

收到unknown、版本/依赖失效、跨集合/重复或无效回执、真实EOF/安全边界错误，或用户暂停/停止/总截止时，停止继续收件；已保存产物保留，未证明归还的槽位仍未知。结果落盘与执行容量归还分别检查；即使收齐正文，也须原活连接的终态及idle证据确认额度归还，不能由计数推断。禁止在通用异常`finally`中无条件收尾。退出前保存已收、未收及原失败原因；旧实验因提前退出产生的unknown不能由新实例或离线回放改签。

成本核对时可显式请求 `{"op":"status","include_usage":true}`。`usage_observation`只汇总本连接已消费、与本地绑定线程及当前已知turn匹配的原生`tokenUsage.total`，按控制／作者／语义／审稿等角色给观察值与覆盖数；普通status保持原输出，不循环查询成本或唤醒模型等用量。重复累计通知不相加，也不将last或推理Token重复计入。缺失、旧turn、不兼容输入与部分覆盖均明确标注；没有数据时为null，真实报告0才为0，缓存写入未报告也不猜0。`all_bound_threads_observed`仅表示已绑定线程的快照覆盖，不表示完整运行、实时账号用量、费用、额度或生产资格；`model_turns_requested`只计本客户端顶层turn/start及turn/steer，不是内部所有模型请求数。该查询不做RPC、不排空事件、不读认证或账号；需先由原唯一owner正常collect消费事件，不能为更新报表额外制造推理回合。

已有work原型的两波六重叠只是可行性证据，不是本接缝真实容量/权限资格。开发候选仍须实际wheel及空上下文自检，然后另做冻结真实suite；本接缝不部署、不改变普通用户的审稿默认策略。

审稿子任务只提交评价内容。诊断程序桥接按真实请求填`model`（请求模型名）、`model_version`（宿主未报告时明确为`host-not-reported`）、`prompt_hash`（本次turn输入文本UTF-8字节SHA-256，非不可见系统前缀的哈希）及线程身份；模型不计算或猜测这些字段，回传伪造值也不会成为审稿来源。原spawn的输入契约保持不变，技术来源元数据齐全不代表内容审稿通过。

诊断运行可显式`start(diagnostic=true, diagnostic_review_policy=independent_review_required)`追加独立审稿，或在Python的create_production_run传相同关键字。它不改Brief的发布策略、不声称真实宿主已具备生产独立审稿资格；正式运行或以此字段取消审稿均在创建前拒绝。默认不传时仍继承Brief。这样可在诚实未签生产资格的环境准备附加诊断审稿，全部候选仍受原质量门及禁止canonical发布约束。
