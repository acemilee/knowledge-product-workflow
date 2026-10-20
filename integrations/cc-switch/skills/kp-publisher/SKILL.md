---
name: kp-publisher
description: 受控汇编、渲染、验证并发布知识产品 Markdown/PDF，保留可审计交付清单。
user-invocable: true
metadata:
  version: "1.4.2"
---

# KP Publisher

## v1.4.1 首次执行

update-publication完成必要PDF派生后止于当前观察/预检。先处理自动问题与必要观察，再request-user-decision pdf明确展示所求scope；取得真实答复后record-user-decision pdf用实际scope和原话记录，再record-pdf-visual-review。一般确认不扩成content或manual_full_coverage，缺scope先按诊断修正，不重跑全书试错。

未变输入只validate-delivery一次，再publish复用完整current交付QA；缺失、陈旧、旧规则或未通过时先校验，不指望publish隐式重算。成功后消费同一status --json结果，不连续查plain/JSON/help重复深扫。PowerShell每步检查原生LASTEXITCODE，前一步非零立即停止依赖链，保存完整错误。

章末偶发留白允许；图表挤页造成前页大白须修排版。图可同小节浮动/文字回填，长表合法续页、表头与行完整，不缩字或补文字填页。需要续作时用recovery-view核旧操作，close-failed-operation必须带实际reason且证据充分，不自动结案、不把发布完成当历史操作全终结。

## v1.3.4 O17 篇幅与阅读闭合

当前PDF生成后分别核preferred目标偏离与hard边界，不用“无blocker”宣称工程篇幅通过。按实际页定位章末薄页、单行代码尾、练习条件与对象分离；薄页只作为复核线索，补内容必须有新的教学增量。模板已对代码有限尾部和短引入/引用对象做组合保护，长块仍可续页。局部修订后核原代码复制字节、字体角色和全页；沿受控render-review-preview及正式渲染处理，不改派生PDF或压字填页。

布局2教学图默认等比例限高145mm，长行内代码必须保留数据空格。仅当前规范包的渲染资源与既有派生资源实际不同，允许通过update-publication保留既定可见配置来修复；健康无变化仍拒绝。不以调整字号/版本触发无关更新，不改已冻结正文。资产本身硬失败或调用用尽时保留未通过，不能以资源刷新代替资产门。

## 用途与触发条件

在所有章节和质量门通过后执行。它只从标准工作区工件产生最终交付；是否需要独立审稿由 Brief 的 `review_policy` 决定。

## 前置工件及状态

- 章节、来源核验、章节合规、中文终稿编辑、图表资产与确定性质量报告均无 blocker。
- `review_policy: independent_review_required` 时，必须存在真实宿主回执绑定的通过审稿；`deterministic_only` 则不要求审稿。
- 已批准交付格式和固定 A4 中文版式。

## 允许读取与写入

- 读取：已批准章节、Brief、蓝图、质量报告与 PDF 模板。
- 写入：`delivery/`、`planning/publication-profile.yaml`、`planning/history/` 与 `runs/` 中该操作生成的预览/归档记录。

## v1.1 编排工作单

先阅读 `references/orchestration-contract.md`。只能在根编排器分配的 `delivery` 工单内汇编、渲染、校验或发布，且只执行 `kp status --json` 返回的当前 `next_actions`。CLI 会在动作前冻结当前工单，并在派生物生成后写入完成凭证、创建后继或终结运行意图；不得手工补写工单。输入清单、质量报告或出版配置变化时重新路由；不得通过删除报告、重建工作区或跳过 PDF 门禁处理过期派生物。发布成功后只向用户交付读者文件。

## 必要步骤与条件分支

视觉审核先交只读pdf-observation-template的增量清单：有效reusable_pages保留，只安排pending_pages的新观察；failed_pages先修上游，uncertain_pages交判断，缺清晰图保留manual_required。全部已有观察时不再提交空数组，也不重置原passed；full_coverage_ready只描述全页观察覆盖，不替代自动指标或本人最终批准。audit回执报告实际已有／待办数量，不把缓存复用冒充新看图。

1. 运行 `kp assemble` 生成唯一规范 Markdown：`delivery/book.md`，并冻结 `delivery/book-input-manifest.json`；它从 `planning/publication-profile.yaml` 派生当前 `delivery/publication-metadata.yaml`、封面、目录、样式、组件与过滤器资产。
2. 读取 `kp status --json` 的 `effective_constraints`。只有 `next_actions` 授权 `render-pdf` 时运行其给出的命令；`delivery_format: both` 表示 PDF 是必需交付物。渲染器只接受 `delivery/book.qmd` 并只接纳 `delivery/book.pdf`；出现 TeX 溢出或输出路径契约失败时，查看 `runs/pdf-render.log` 后从上游修订，绝不移动嵌套 PDF。
3. PDF 已存在但状态为 `pdf_preview_repair_pending` 时，执行 `kp repair-pdf-preview --workspace <workspace>`。它只重建当前 PDF 的页面预览；不得反复运行 `validate-delivery`，也不得修改出版信息来触发重渲染。
4. v6 先交给视觉审阅者运行 `kp audit-pdf-visual`，默认全页清晰图，逐页实际核正文及相邻阅读闭合；显式抽样只供初查，不能作最终全书验收。最终须全页实际passed，或本人明确确认完整当前PDF并用既有manual-coverage原话路径记录；视觉报告不能代替人工确认。根Skill展示当前PDF与完整预览，获得本人的当前版本判断后才record-pdf-visual-review，不由测试审批代签。PDF、预览或报告变化后只刷新失效绑定。
5. 在本人确认前运行`kp delivery-preflight --workspace <workspace> --json`，解决自动阻断项，展示当前版与待人工项；预检不覆盖qa-report也不授发布权限。确认后运行`kp validate-delivery`，读取当前报告publishable再publish。它重新核输入图谱/证明/书稿/PDF/视觉绑定；没有输入变化时只做批准和最终收尾，不再编译。blocked/invalid/stale沿真实状态恢复，不删除qa-report。

每步原生命令失败立即停下游，不把分号链最后exit0当整体成功。assemble可预先指定`--operation-id op-<唯一ID>`；回执绑定真实tx后，文件占用/断流先operation-status，再recover-operation续同一事务；不得重发汇编/正文，不以清空锁恢复。字体missing与timeout/compile_failed/probe_unavailable分别诊断，未知不自动改字体或反复跑doctor/编译。
6. 仅在全部质量门通过后运行 `kp publish` 生成 `delivery-manifest.yaml`。清单路径固定为工作区相对 POSIX 路径，例如 `delivery/book.md`；旧清单或过期清单只能重新运行 `kp publish` 重建。
7. 向用户报告最终书稿路径，不展示内部来源台账、Claim ID 或审稿 JSON。

### 出版信息快速更新

当用户只要求修改封面标签、页脚标签、版权行、副标题、发布日期或书稿版本时，不能调用 `kp amend-brief`，也不能让用户重走蓝图、章节、审稿或来源流程：

1. 从用户的一次自然语言请求提取允许字段，写入当前工作区 `runs/publication-update.yaml`。出版信息字段包括 `subtitle`、`cover_label`、`footer_label`、`copyright_line`、`edition_label`、`publication_date`；品牌/布局调整消费出版设计确认，完整字段以 `kp publication-schema` 为准，包括有限的 `reader_layout_revision`、`illustration_style_revision`。必填 `version_bump` 只能取 `patch`、`minor`、`major` 或 `none`；不手改档案绕过预检与受控确认。
2. 运行 `kp update-publication --workspace <workspace> --from <workspace>/runs/publication-update.yaml`，向用户展示预览；不要自行编辑 `publication-profile.yaml`。
3. 用户回复“确认”后，使用相同参数追加 `--confirm`。CLI 会受控归档旧 PDF/manifest、重建 QMD/PDF/交付质量报告和 manifest；没有已汇编书稿时只保存配置，后续正常发布自动使用它。
4. 默认署名和页脚标签为“知识产品工作流”，新封面2版权行为“© {年份} {已确认署名}”，封面仅版权处显示署名。旧布局及用户明确版权文字保留原意。初始书稿版本为 `v1.0.0`，与KP软件版本无关。

## 禁止事项

- 禁止直接调用 Pandoc、Quarto、LaTeX 或手写 PDF 替代 `kp render-pdf`。
- 禁止以任意命名的 PDF 替代 `delivery/book.pdf`。
- 禁止在质量报告失败时创建 manifest。
- 禁止手工改写 `book-input-manifest.json`、`publication-profile.yaml`、`publication-metadata.yaml`、`book.qmd`、TeX 模板或 Lua 过滤器以绕过固定视觉契约。
- 禁止删除质量报告、手工移动嵌套 PDF 或编辑 `delivery-manifest.yaml` 来绕过状态恢复。
- 禁止把未核实的默认值当作当前 Brief 约束；不得因 PDF 命令在错误阶段被拒绝就建议重建工作区。

## 确定性验收命令

`kp validate-delivery --workspace <workspace>` 必须 publishable；PDF 需满足封面、目录、书签、经 XeLaTeX 实际验证的 12pt/14pt A4 版式、当前出版配置派生元数据、无 TeX 溢出日志、无裸 `.kp-*` 内部标记、图表视觉合同和来源契约。书稿使用 `.kp-source` 时，PDF 必须包含可点击的外部链接；插图只接受 `assets/` 中已转换的 PDF、PNG、JPG/JPEG，SVG 不可直接渲染。

`PDF_REQUIRED_AND_MISSING` 或 `PDF_INVALID_AND_REBUILD_REQUIRED` 才走受控整书渲染；`PDF_PREVIEW_REPAIR_REQUIRED` 只修复预览。`PDF_VISUAL_AUDIT_REQUIRED` 先补逐页报告，`CURRENT_PDF_HUMAN_REVIEW_REQUIRED` 由根 Skill 收集当前版本判断。品牌色可通过 `brand_color_intent`（橙/紫/合法 HEX）更新，与图、提示框、表格和目录同色板。新书使用 v6 模板，旧 v4/v5 不静默变版。

## 人工审批点

发布前向用户报告交付清单；若用户要求额外格式或商业发布范围，先确认后再扩展。

## 用户交互边界

只向根 Skill 返回读者交付物和产品级交付摘要。不得让用户查看渲染日志、质量报告、命令、工作区路径或控制面状态。

## 下游交接条件

最终交付为 `delivery/book.md`、按 Brief 要求存在的 `delivery/book.pdf` 与 `delivery/delivery-manifest.yaml`。

## v1.3.1 共同交接增量

消费冻结reader_layout_revision及插图风格版本。布局2的普通章有自然导读，参考/附录不机械追加；圆点表示并列，数字表示步骤，目录层级和正文事实边界保持。资产、引用和正文最终就绪后生成发展性/humanize证明；任意正文修改重跑两份，不复用旧稿报告。

宿主图资产风格证明在章节提交前；实际PDF后的成页证明在最终视觉报告中。缺失/过期/明确失败阻断，主观uncertain留人工而不伪写算法通过。最终用户回执只绑定当前PDF，测试用户回执不能当真实批准。模板/安装资源来自规范包，不读取本次指定书稿之外的开发者work/tmp或客户PDF。旧政策1保留章内1/全书6历史；政策2按批准图任务和C＋R控制发送，不按图数配额。每任务两次、串行，超额/能力缺失停止并返回选择。

## v1.3.2 配色与封面

出版预览同次确认风格2与辅助色策略：warm-focus/balanced/cool-structure，品牌强调保持用户主色；同概念跨图同色，不能随机改字形/图族。vector保持本图型画幅；host-image采用中性母版＋当前配色卡，首次两参考、纠正另带edit_target共三输入，preflight须声明reference_capacity至少3；型号/费用未知如实记录。

图级展示输入包含presentation的node/group颜色和标准概念映射，不接收自由CSS/任意HEX；全书概念改色通过出版更新illustration_category_colors确认，受影响图用update-diagram-presentation取得候选。候选检查使用illustration-style-template --candidate-call-id及观察输入candidate_call_id，failed不能覆盖当前；检查后activate-illustration-candidate切换，uncertain只允许受控试排并保留最终人工待决。新命令不释放作者租约，源稿变化仍先amend-chapter。调用completed只代表返回文件，不等同风格通过。

新封面选择cover_layout_revision=2；中文梗概必需，英文/最多四项学习重点/内容说明可选。Brief阶段提炼梗概，蓝图批准后再补学习重点，终稿核对后通过出版更新锁定；不在渲染时补写，不新增每图审批。封面日期仅年月、无语言栏/样稿徽标/开发提示，个人署名仅在已确认copyright_line出现一次；正文页脚独立。旧书缺字段布局1不静默升级。

旧政策1预算及历史保留；新政策2图数由批准教学任务推导，不设每章/全书图数配额；无消费书N个实际host-image制作任务最多2N次，已有消费修订按C＋R。每任务最多两次、串行，失败/超时/换色不重置。资产/成页/跨图核验共同保证可交付结果，无效量测转人工；不能保证随机生成每次成功。当前母版仍为候选时，明确只在隔离验收使用，正式发布需实际用户确认。

风格2仍用候选母版时，根须将母版与当前PDF同次展示，得到用户明确认可后才用record-pdf-visual-review --confirm-style-anchor绑定当前母版hash；不修改图像hash或代签用户判断。旧文本预算账本仅保留历史，不再作为执行或质量依赖。

诊断预览是失败资产的独立出口：全部正文当前就绪后用render-review-preview；其文件与清单不可当正式交付。


## v1.4.2 r0.3 受控执行要点

昂贵汇编/出片前集中运行reader-resource-preflight，无ZIP时也可核预期闭包；缺README/NOTICES/模板或坏链接一次修齐。ready不是发布批准，原未知事务仍先恢复。book.md本地读者文件链统一../assets/，配套ZIP只含已声明资源及说明/许可；按ZIP根README将book.md置delivery/，独立解压与整体移动后仍可达。发布不补包，实跑只证明默认入口。新书默认空右页脚，封面版权按出版日期年份署“葬爱西兰花”；旧配置保留，显式出版更新才改变，不修改安装模板为单书热修。MD、纸面目录和书签须核文字/层级/正文目标页，计数不等于导航通过。
