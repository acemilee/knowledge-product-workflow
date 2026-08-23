---
name: kp-publisher
description: 受控汇编、渲染、验证并发布知识产品 Markdown/PDF，保留可审计交付清单。
user-invocable: true
metadata:
  version: "1.2.0"
---

# KP Publisher

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

先阅读 `../../references/orchestration-contract.md`。只能在根编排器分配的 `delivery` 工单内汇编、渲染、校验或发布，且只执行 `kp status --json` 返回的当前 `next_actions`。CLI 会在动作前冻结当前工单，并在派生物生成后写入完成凭证、创建后继或终结运行意图；不得手工补写工单。输入清单、质量报告或出版配置变化时重新路由；不得通过删除报告、重建工作区或跳过 PDF 门禁处理过期派生物。发布成功后只向用户交付读者文件。

## 必要步骤与条件分支

1. 运行 `kp assemble` 生成唯一规范 Markdown：`delivery/book.md`，并冻结 `delivery/book-input-manifest.json`；它从 `planning/publication-profile.yaml` 派生当前 `delivery/publication-metadata.yaml`、封面、目录、样式、组件与过滤器资产。
2. 读取 `kp status --json` 的 `effective_constraints`。只有 `next_actions` 包含 `PDF_REQUIRED_AND_MISSING` 时运行其给出的 `kp render-pdf` 参数；`delivery_format: both` 表示 PDF 是必需交付物。渲染器只接受 `delivery/book.qmd` 并只接纳 `delivery/book.pdf`；出现 TeX 溢出或输出路径契约失败时，查看 `runs/pdf-render.log` 后从上游修订，绝不移动嵌套 PDF。
3. 运行 `kp validate-delivery`；它会重新验证输入图谱、来源、章节合规、内容计划/发展性编辑、图表、书稿、PDF 和视觉模板哈希。v5 PDF 会生成当前字节绑定的页面预览集；含图或 v5 PDF 会额外检查重复图注、预览清单和当前视觉复核绑定。自动检查无法证明可读性时，根 Skill 必须收集一次当前版本的人工视觉复核，PDF、预览或图表变化后旧复核自动失效。质量报告绑定被校验输入，并归档至 `runs/quality-reports/`；报告处于 blocked、invalid 或 stale 时，按 `kp status` 推荐命令重跑校验，绝不删除 `qa-report.json`。托管运行中，该恢复校验由版本绑定工单明确授权；不得把恢复失败伪装成新的用户审批或要求用户重启工作流。
4. 仅在全部质量门通过后运行 `kp publish` 生成 `delivery-manifest.yaml`。清单路径固定为工作区相对 POSIX 路径，例如 `delivery/book.md`；旧清单或过期清单只能重新运行 `kp publish` 重建。
5. 向用户报告最终书稿路径，不展示内部来源台账、Claim ID 或审稿 JSON。

### 出版信息快速更新

当用户只要求修改封面标签、页脚标签、版权行、副标题、发布日期或书稿版本时，不能调用 `kp amend-brief`，也不能让用户重走蓝图、章节、审稿或来源流程：

1. 从用户的一次自然语言请求提取允许字段，写入当前工作区 `runs/publication-update.yaml`。唯一允许字段是 `subtitle`、`cover_label`、`footer_label`、`copyright_line`、`edition_label`、`publication_date` 和必填 `version_bump`；版本递增只能取 `patch`、`minor`、`major` 或 `none`。
2. 运行 `kp update-publication --workspace <workspace> --from <workspace>/runs/publication-update.yaml`，向用户展示预览；不要自行编辑 `publication-profile.yaml`。
3. 用户回复“确认”后，使用相同参数追加 `--confirm`。CLI 会受控归档旧 PDF/manifest、重建 QMD/PDF/交付质量报告和 manifest；没有已汇编书稿时只保存配置，后续正常发布自动使用它。
4. 默认封面标签和页脚标签为“知识产品工作流”，默认版权行为“版权所有 {年份} 知识产品工作流”，初始书稿版本为 `v1.0.0`。书稿版本与 KP 软件版本无关。

## 禁止事项

- 禁止直接调用 Pandoc、Quarto、LaTeX 或手写 PDF 替代 `kp render-pdf`。
- 禁止以任意命名的 PDF 替代 `delivery/book.pdf`。
- 禁止在质量报告失败时创建 manifest。
- 禁止手工改写 `book-input-manifest.json`、`publication-profile.yaml`、`publication-metadata.yaml`、`book.qmd`、TeX 模板或 Lua 过滤器以绕过固定视觉契约。
- 禁止删除质量报告、手工移动嵌套 PDF 或编辑 `delivery-manifest.yaml` 来绕过状态恢复。
- 禁止把未核实的默认值当作当前 Brief 约束；不得因 PDF 命令在错误阶段被拒绝就建议重建工作区。

## 确定性验收命令

`kp validate-delivery --workspace <workspace>` 必须 publishable；PDF 需满足封面、目录、书签、经 XeLaTeX 实际验证的 12pt/14pt A4 版式、当前出版配置派生元数据、无 TeX 溢出日志、无裸 `.kp-*` 内部标记、图表视觉合同和来源契约。书稿使用 `.kp-source` 时，PDF 必须包含可点击的外部链接；插图只接受 `assets/` 中已转换的 PDF、PNG、JPG/JPEG，SVG 不可直接渲染。

## 人工审批点

发布前向用户报告交付清单；若用户要求额外格式或商业发布范围，先确认后再扩展。

## 用户交互边界

只向根 Skill 返回读者交付物和产品级交付摘要。不得让用户查看渲染日志、质量报告、命令、工作区路径或控制面状态。

## 下游交接条件

最终交付为 `delivery/book.md`、按 Brief 要求存在的 `delivery/book.pdf` 与 `delivery/delivery-manifest.yaml`。
