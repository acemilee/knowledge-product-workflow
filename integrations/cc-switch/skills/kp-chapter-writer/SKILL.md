---
name: kp-chapter-writer
description: 依据已批准章节合约和证据边界撰写读者可见的知识产品章节。
user-invocable: true
metadata:
  version: "1.1.6"
---

# KP Chapter Writer

## 用途与触发条件

在 Brief、课程蓝图、章节合约和必要证据均已满足后，撰写一个指定章节或样章。

## 前置工件及状态

- 已批准 Brief 和课程蓝图。
- 对应 `chapters/chapter-XX-contract.md` 存在。
- 高风险事实已在当前章节合约中声明 Claim 并有来源台账，或被明确降级为情境案例。

## 允许读取与写入

- 读取：该章节合约、必要研究摘要、已批准样章和相邻章节摘要。
- 写入：仅对应的 `chapters/chapter-XX.md` 或样章草稿路径。

## v1.1 编排工作单

先阅读 `../../references/orchestration-contract.md`。只能在根编排器分配、且章节号匹配的 `production` 工单和正文租约内写当前章节；输入哈希变化、工单过期、跨章节或租约冲突时立即停止。练习、图表和中文终稿均是本章租约内的条件协作：作者负责把已验证建议整合进当前正文，但不让协作 Skill 独立拥有正文。章节提交后由根编排器写完成证明并重新路由，作者不得自行派发下一章。

## 必要步骤与条件分支

1. 按章节预算、体裁、必需/可选/禁止教学单元、读者级别和合约中的 `section_outline` 组织内容；全部编号标题必须逐项使用合约中的编号、标题、层级和深度优先顺序，不能自行增加、跳过、改名、重排或用非编号标题替代。
2. 只写当前体裁需要的论证、案例、操作或参考内容。概念章不自动补练习，参考/附录章不自动补学习目标或小结，面试书只在蓝图配额内写完整示范回答。
3. 对量化示例明确说明是虚构情境或读者可核验的本人经历；简历例子必须提醒读者不能编造数据。
4. 样章必须在批量写作前交由用户审批。
5. 仅使用普通 Markdown 标题、表格、带语言标识的代码块，以及 `.kp-source`、`.kp-key-takeaway`、`.kp-practice`、`.kp-note` 读者组件。组件必须使用完整 fenced div（`::: {.kp-note}` 到对应的 `:::`）；绝不能单独输出 `{.kp-note}` 等裸标记。来源卡片必须写读者可见的标题和 HTTPS 链接。
6. 函数、类、参数、变量、CLI 命令、文件路径、配置键和值、代码标识符必须用单反引号标注为行内代码，例如 `get_orders(user_id)`、`--workspace`、`runs/events.jsonl`。普通概念、完整句子和宣传性短语不得滥用反引号；长代码仍使用带语言标识的代码块。
7. 插图仅引用当前书稿 `assets/` 中由受控工作流生成的 PDF、PNG、JPG/JPEG；SVG 必须先转换，且不得为凑页数添加无教学意义的图片。流程、架构、对比、时序或生命周期关系先交给 `kp-diagram-designer`，使用“图表模板 -> 受控引用 -> 终稿编辑 -> 提交图表”的 D2 链路，不保留 ASCII 字符画。
8. 图表引用写入后，将最终章节源稿交给 `kp-chinese-humanize-editor`，运行 `kp humanize-check`；随后提交图表资产（有图表时），再由 `kp-quality-editor` 运行章节合规模板并提交章节。合规模板上下文中的 `expected_section_outline` 是只读真源，作者不得填写编号或标题结构字段；正文作者不得计算或填写正文/合约哈希。

## 篇幅预算

- 正文按章节合约的 `word_min`/`word_target`/`word_max` 写作，先达到目标字数，再校验收束；不得为凑字数复制段落或堆叠同义内容。
- Brief 只声明页数目标、没有字数额门时，CLI 会按页数估算章节预算（`kp submit-outline` 会提示"已按页数目标估算章节预算"）；此时以估算预算为写作锚点，避免交付阶段页数不达标返工。
- 预算与页数目标明显冲突（例如合约预算写不满目标页数）时，先回 `kp-curriculum-architect` 修订篇幅评估或蓝图，不得自行放宽预算、改小节结构或增删合约内容。

## 禁止事项

- 不输出 `## 正文`、`来源台账`、Claim ID、内部说明或占位符。
- 不复制模板段落、机械重复总结或用字数填充。
- 不自行审核或批准自己的章节。
- 不写原始 TeX、HTML、远程图片、绝对图片路径或工作区外资产引用。
- 不手工插入图表 PDF 引用，不手写 DiagramSpec、章节合规内部证明、SHA 或 Schema 版本。

## 确定性验收命令

`kp humanize-check <章节号> --workspace <workspace> --from <chapter.md>`、`kp chapter-compliance-template <章节号> --workspace <workspace> --from <chapter.md>`、`kp submit-chapter <章节号> --workspace <workspace> --from <chapter.md> --compliance <mapping.json>` 与 `kp status --json --workspace <workspace>` 必须无章节内容阻断项。

## 人工审批点

首个样章必须等待用户批准。若 Brief 要求独立审稿，交给 `kp-quality-editor` 创建一次批量审稿；否则不创建阻塞性的审稿工单。

## 用户交互边界

只向根 Skill 交付完整样章、合规状态和修订摘要。不得向用户显示 `approve-sample`、审批身份、章节工单、哈希或命令；根 Skill 负责将样章呈现为“继续生成全书/修改”的产品决定。

## 下游交接条件

章节正文、合约、合规映射、终稿编辑报告、图表资产与证据边界交给 `kp-practice-designer` 和 `kp-quality-editor`。审稿需要时，控制面冻结作者上下文，禁止由审稿人猜测或伪造。
