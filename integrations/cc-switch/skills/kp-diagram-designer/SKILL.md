---
name: kp-diagram-designer
description: 将知识教程中的流程、架构、对比、时序和生命周期关系转化为受控 D2 图表资产并嵌入 PDF。
user-invocable: true
metadata:
  version: "1.1.6"
---

# KP Diagram Designer

## 用途与触发条件

当读者需要理解流程、分层架构、对比关系、时序或生命周期时使用。它交付可嵌入 PDF 的 PDF/PNG 图形资产，不交付 `.vsdx`，也不把字符画当作插图。

## 前置工件及状态

- 当前章节合约已由批准蓝图派生。
- 当前章节源稿位于该书稿工作区内。受控图片引用由 `kp insert-diagram-reference` 从已填写的语义输入创建，不由模型手写。
- 本机 `kp doctor` 显示 `d2.available: true` 且 `diagram_browser.available: true`；D2 只生成 SVG，本机 Chrome/Edge/Chromium 离线转换受控 PDF/PNG。缺失任一工具时停止并报告，不下载、不联网、不回退 ASCII 图。

## 允许读取与写入

- 读取：当前章节源稿、`kp diagram-schema` 输出、当前图表模板及其上下文。
- 写入：仅当前工作区内的图表语义输入 JSON。内部 `runs/diagrams/` 和 `assets/figures/` 由 CLI 写入。

## v1.1 编排工作单

先阅读 `../../references/orchestration-contract.md`。这是条件 Skill：仅在当前章节需要解释流程、架构、对比、时序或生命周期关系时由章节作者租约内调用。它只产出版本绑定的图表语义输入和受控 D2 资产，不直接改写正文、合约、质量报告或交付物；正文引用仍由作者通过 `kp insert-diagram-reference` 受控写入。

## 必要步骤与条件分支

1. 先提取读者真正需要的关系，选择 `flow`、`layered_architecture`、`comparison`、`sequence` 或 `lifecycle`，不要把普通列表硬画成图。
2. 先运行 `kp diagram-template <章节号> --workspace <workspace> --chapter-source <chapter-source.md> --diagram-id <diagram-id>`，只填写模板中的语义字段：图表 ID、类型、方向、图注、读者结论、节点、分组和边。图注只能是无编号的语义标题，禁止写“图 1”或“Figure 1”；节点最多 14 个、每个节点文字最多 72 字符、连线标签最多 56 字符。优先使用能与正文共同排版的紧凑关系图；不要为普通步骤列表生成过长的纵向流程图。四个及以上节点的右向 `flow` 图由 CLI 自动排为两行，关系含义应写入节点和 `reader_conclusion`，不要依赖边标签承载关键教学信息。节点标签使用完整中文，不使用随机配色、装饰元素或内部 Claim。
3. 使用已填写的输入运行 `kp insert-diagram-reference <章节号> <diagram-id> --workspace <workspace> --from <chapter-source.md> --diagram-input <diagram-input.json>`。CLI 写入最终图注和唯一 PDF 引用，模型不得手写该 Markdown 行。
4. 先对包含最终图注的源稿运行 `kp humanize-check`；终稿编辑必须保留受控图片引用。随后运行 `kp submit-diagram <章节号> --workspace <workspace> --from <diagram-input.json> --chapter-source <chapter-source.md>`。CLI 只读取最终源稿，自行生成内部 D2/SVG 和读者 PDF/PNG，并绑定当前正文、当前合约和资产哈希。
5. 图表、正文或合约变化后，从图表模板重新执行该固定顺序，再执行章节合规模板与 `kp submit-chapter`。先写最终图注、再终稿编辑、最后渲染资产，避免编辑报告与图表清单相互判定过期。最终 PDF 含图时，根 Skill 还必须完成当前 PDF 的版本绑定视觉复核；不能因宿主没有视觉能力而宣称通过。

## 禁止事项

- 不输出 `┌ ┐ └ ┘ │ ─` 等读者可见 ASCII 字符画，代码示例和明确的终端输出除外。
- 不在图注、替代文本或正文手写图号，也不以长文本、超密节点或缩小字体规避可读性合同。
- 不插入远程图片、SVG、绝对路径、工作区外资产、原始 TeX/HTML 或无图注图片。
- 不填写或推导 SHA-256、章节号、Schema 版本、主题、资产路径、内部 Spec、清单或 D2 源。
- 不直接运行 D2、不以 PNG/SVG 代替受控 PDF 引用、不搜索源码、安装目录或其他工作区来恢复失败。
- 不把内部 Spec、D2 源、运行路径、哈希或调试输出复制到读者正文。

## 确定性验收命令

`kp submit-diagram` 必须成功生成 `assets/figures/<id>.pdf` 和 `.png`；随后 `kp submit-chapter` 与 `kp validate` 必须接受当前图表绑定。

## 失败恢复

出现失败时只读取当前工作区 `runs/diagnostics/submit-diagram-chapter-XX.json`、模板、上下文和指定章节源稿。按诊断中的 `next_command` 恢复；不得尝试字段名、手算哈希、手工移动资产或跨目录搜索。

## 人工审批点

图表改变技术结论、架构边界或数据含义时，先请章节作者和用户确认；图表仅能解释已批准正文，不能凭空添加结论。

## 用户交互边界

只向章节作者和根 Skill 提供图表结论与修改影响。不得让用户处理 D2、资产路径、模板、哈希或图表提交命令。

## 下游交接条件

当前图表资产和图注交给 `kp-chinese-humanize-editor` 作最终文本检查，再交给章节合规与 `kp-publisher` 渲染。
