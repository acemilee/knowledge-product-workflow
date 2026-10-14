---
name: kp-curriculum-architect
description: 从已批准的知识产品 Brief 生成并提交主题化结构化课程蓝图，再批量派生当前章节合约。
user-invocable: true
metadata:
  version: "1.3.4"
---

# KP Curriculum Architect

## 政策2图任务与图像调用摘要

新outline模板使用illustration_policy_revision=2。每小节评估图的读者问题及必要/有益/不画决定，不分每章图数配额；独立制作任务有task_id/section_id/视角/增益/替代理由/图型/后端/placement，none只填不用理由。有益图未选制作及回看引用不计生图N。与课程蓝图同次展示图任务和首次N/无消费书最多2N、修订C＋R；批准后compile-contracts派生，正文细化不越过批准范围。时序等不支持的host图按Brief分工用vector，不强制AI化。

## 用途与触发条件

在 Brief 已批准后，设计学习路径、章节权重、必需教学单元与章节边界。

## 前置工件及状态

- `planning/product-brief.md` 必须存在且为已批准状态。
- 必须读取 Brief 中可用的字数与页数约束；两者未设置时，按教学权重和必需单元设计结构，不得因没有数字预算阻断课程蓝图。

## 允许读取与写入

- 读取：当前工作区中的 `planning/product-brief.md`、`planning/curriculum-blueprint.template.yaml` 与 `kp outline-schema` 的受控输出。
- 写入：`planning/curriculum-blueprint.template.yaml`、`planning/curriculum-blueprint.md` 和批准后生成的 `chapters/chapter-XX-contract.md`。

## v1.1 编排工作单

先阅读 `references/orchestration-contract.md`。只能在根编排器分配的 `architecture` 工单内生成主题化蓝图或从已批准蓝图派生章节合约。工作单输入与当前 Brief 绑定；Brief 的内容型修订、蓝图过期或 `section_outline` 变化时停止并按状态重新路由。不得自行批准蓝图、拥有 Brief 写入权，或从章节正文反写课程结构。

## 必要步骤与条件分支

1. 先运行 `kp length-recommendation-template --workspace <workspace>`，根据主题、受众、级别、案例/练习/代码/图表密度填写并提交教学篇幅建议。建议必须解释依据，不能为迎合用户页数制造同义小节。
2. 当用户的精确或区间页数超过建议上限时，在建议中声明每项额外的、可辨识的教学单元、案例、项目、模板或附录。用户未决定“保留并扩展”前不生成蓝图；下限或不限页数不生成机械章节页数目标。
3. 定义从基础理解到可迁移能力的学习路径，而非按主题罗列名词。
4. 为每章声明完整 `section_outline`：从 `<章节号>.1` 连续编号，标题必须是读者将看到的正式标题；子项必须挂在正确父级下。它是批准后冻结的读者结构，正文作者不得自行增删、跳号、改名或重排。
5. 为每章声明正数 `weight`、教学理由、`chapter_archetype`、必需/可选/禁止教学单元、学习目标、范围和 Claim 清单；预算由 CLI 按权重分配，不得平均摊分。
6. 体裁只可使用 `concept`、`case_study`、`workshop`、`strategy`、`reference`、`appendix`。根据教学目的选择体裁，不得给每章套同一种“目标、问答、追问、误区、练习、小结”模具。
7. 面试类产品必须声明 `product_mode: interview` 与全书/每章 `interview_answer_policy`。只有有限高频题可设 `complete_answer_demo`；其余内容使用答题框架、证据槽位或练习，不能量产标准话术。
8. 先运行 `kp outline --workspace <workspace>`，再运行 `kp outline-schema`。只读取当前工作区生成的模板和该公开 Schema，在模板中填写主题化蓝图后运行 `kp submit-outline --workspace <workspace> --from <template.yaml>`。`required_units` 可省略，CLI 会按 `chapter_archetype` 填充默认值；只有确需偏离默认教学单元时才显式填写公开 Schema 中的值。提交成功后将生成的 `planning/curriculum-blueprint.md` 交给用户审批。
9. 用户批准蓝图后，运行 `kp compile-contracts --all --workspace <workspace>`。不得逐章手填标题或自行创建合约。
10. 若 `kp status --json` 返回 `BLUEPRINT_CONTENT_BINDING_STALE`，只可填写当前 Brief 对应的蓝图输入并运行 `kp amend-outline --workspace <workspace> --from <blueprint.yaml>`。该命令会归档旧蓝图和旧合约；提交后必须重新等待用户批准，再运行 `kp compile-contracts --all`。若旧蓝图缺少 `section_outline`，先运行 `kp outline-migration-draft --workspace <workspace>` 取得只读候选。经用户一次确认后，将确认树填入 v2 蓝图输入并运行同一 `kp amend-outline` 命令；不得直接提交候选、不得删除蓝图，也不得从正文自动覆盖蓝图。

## 禁止事项

- 不把未验证的市场判断写入蓝图。
- 不使用统一章节字数掩盖重点差异。
- 不写正文或自行批准蓝图。
- 不搜索用户目录、其他工作区、二进制、源码或网络来猜测字段枚举；字段含义仅以 `kp outline-schema` 为准。

## 确定性验收命令

`kp submit-length-recommendation --workspace <workspace> --from <recommendation.yaml>` 和 `kp submit-outline --workspace <workspace> --from <template.yaml>` 必须成功；批准后运行 `kp compile-contracts --all --workspace <workspace>`，再以 `kp status --json --workspace <workspace>` 确认当前状态。

## 人工审批点

必须等待用户批准 `planning/curriculum-blueprint.md`。

## 用户交互边界

只向根 Skill 交付可审阅课程结构和修改摘要。不得让用户填写 `required_units`、运行提交命令或查看蓝图 Schema；根 Skill 负责呈现“继续撰写样章/修改”的自然语言决定。

## 下游交接条件

蓝图批准并完成合约派生后，交给 `kp-research-verifier` 和 `kp-chapter-writer`。

## v1.3.2 配色与封面

出版预览同次确认风格2与辅助色策略：warm-focus/balanced/cool-structure，品牌强调保持用户主色；同概念跨图同色，不能随机改字形/图族。vector保持本图型画幅；host-image采用中性母版＋当前配色卡，首次两参考、纠正另带edit_target共三输入，preflight须声明reference_capacity至少3；型号/费用未知如实记录。

图级展示输入包含presentation的node/group颜色和标准概念映射，不接收自由CSS/任意HEX；全书概念改色通过出版更新illustration_category_colors确认，受影响图用update-diagram-presentation取得候选。候选检查使用illustration-style-template --candidate-call-id及观察输入candidate_call_id，failed不能覆盖当前；检查后activate-illustration-candidate切换，uncertain只允许受控试排并保留最终人工待决。新命令不释放作者租约，源稿变化仍先amend-chapter。调用completed只代表返回文件，不等同风格通过。

新封面选择cover_layout_revision=2；中文梗概必需，英文/最多四项学习重点/内容说明可选。Brief阶段提炼梗概，蓝图批准后再补学习重点，终稿核对后通过出版更新锁定；不在渲染时补写，不新增每图审批。封面日期仅年月、无语言栏/样稿徽标/开发提示，个人署名仅在已确认copyright_line出现一次；正文页脚独立。旧书缺字段布局1不静默升级。

旧政策1预算及历史保留；新政策2图数由批准教学任务推导，不设每章/全书图数配额；无消费书N个实际host-image制作任务最多2N次，已有消费修订按C＋R。每任务最多两次、串行，失败/超时/换色不重置。资产/成页/跨图核验共同保证可交付结果，无效量测转人工；不能保证随机生成每次成功。当前母版仍为候选时，明确只在隔离验收使用，正式发布需实际用户确认。


## v1.3.3 跨主题质量与成本增补

以读者起点、章节独立贡献和学习成果设计路径，明确复用前文与新增任务；按主题选择概念、操作、决策或推导的组织方式。先估算必要内容、编辑和成页成本，不能按剩余token删教学承诺。

修正0.8：required_units明确包含input_output_artifact或template时，内容计划必须承诺并兑现可复用工件，说明图不能替代它；概念、案例与策略章可全artifact=none，不强造图表／列表。蓝图既有显式学习成果及工件承诺不自动删除；确需改变仍走原修订批准。

## v1.3.4 修正0.7：最小充分教学与实际核验

从后台reader_request对照学习成果、范围排除项、固定语料/数值与页数口径。篇幅评估计入封面目录、完整代码、表、公式、图的真实空间；目标区间与硬上限分别保留。按最小充分教学单元组织，每节有新增判断/动作，重复同一职责的散文、图、表不能各算一个独立教学增量。章数与表达形式由教学关系决定，不设段长、图数或形式配额；必要长推导和无图短因果解释可以合格。
