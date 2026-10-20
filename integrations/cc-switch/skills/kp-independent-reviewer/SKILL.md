---
name: kp-independent-reviewer
description: 审阅不可变章节快照、按教学与面试 Rubric 评分，并通过受控命令提交版本绑定的审稿结果。
user-invocable: false
metadata:
  version: "1.4.2"
  mode: host-skill
  review-evidence: snapshot-bound
---

# KP Independent Reviewer

## v1.4.0 任务边界与等待

每任务／候选版本使用新上下文，按结构化handoff与当前manifest接续，不重放历史全聊。完整当前章、合约和必要依赖／比较单元必须齐备；材料不足只补缺失范围，不猜测。领域计算交给本地程序；原进程只有一个等待owner，不另设LLM协调者轮询／sleep／转发心跳。用operation-status核原回执，断流不重发；真实宿主无推理守候接线仍pending，按根Skill降级规则反馈。细节见references/orchestration-contract.md的单一等待章节。

## 用途与触发条件

由 `kp-quality-editor` 在冻结批次准备完成后调用。只有宿主创建的、可返回父子任务回执的 `Quality review` 工作线程能满足 `independent_review_required`。没有该能力时，本 Skill 仅可作为宿主审稿诊断，普通用户和正文作者会话不得把结论当作独立审稿通过依据。

## 前置工件及状态

- 审稿批次状态为 `review_tasks_pending`，且本任务有控制面生成的工作单。
- 审阅章节必须已通过当前 `section_outline` 的确定性编号、标题、层级和顺序门禁；审稿 Skill 不评分、不豁免也不修补 `HEADING_*` blocker。
- 只读取本任务对应的快照目录中的 `chapter.md`、`contract.md` 和 `evidence/`；不读取实时 `chapters/` 目录。
- 工单提供 task token、批次 ID、章节号和输入清单哈希。它们不写入审稿结果 JSON。

## 允许读取与写入

- 读取：当前批次的指定快照、工单、Brief、蓝图和证据快照。
- 写入：仅本任务私有的临时 `review-result.json`。
- 最终输出必须通过 `kp submit-review-result` 写入受保护的批次目录；不得直接写正文、合约、来源台账、交付物或审稿存档。

## v1.1 编排工作单

先阅读 `references/orchestration-contract.md`。本 Skill 只接受 `kp-quality-editor` 创建的冻结批次任务工单；该工单是根编排工单在审稿阶段的受控子边界。它不获取正文租约、不修改章节、不创建下一批任务，也不在能力快照缺失或未验证时以“独立审稿”身份运行。

## 必要步骤与条件分支

1. 验证工单中的批次 ID、任务 token 和输入清单哈希；只审阅工作单对应的冻结快照。
2. 按事实与来源、教学递进、面试可操作性、案例具体性、表达去重性分别评分 1--5，并给出每项必需教学单元的读者可见位置。
3. 有 blocker、任一评分低于 3 或平均分低于 3.5 时填写 `status: revise`、问题和可执行修订请求；否则填写 `status: passed`。
4. 结果 JSON 只包含状态、模型/版本、64 位 `prompt_hash`、审稿者身份/会话、评分、教学单元证据、问题和修订请求。不得填写哈希、章节版本、工单 ID、batch/task ID 或父任务 ID。
5. 运行 `kp submit-review-result --workspace <workspace> --batch <batch-id> --task-token <token> --input review-result.json`，再运行 `kp review-check --workspace <workspace>`。

## 禁止事项

- 不得在作者身份或作者会话中执行；需要以审稿 Skill 或宿主 Quality review 工作线程的独立审稿身份运行。
- 不得读取可变实时章节，不得手工复制、伪造或覆盖控制面绑定字段。
- 不得把 Claim ID、来源台账、工单、哈希、任务 ID 或审稿结论泄漏到读者正文或最终书稿。
- 宿主未提供任务回执时，必须将执行方式记录为 `host_skill`，不得声称获得了宿主签发的父子任务回执，也不得满足 `independent_review_required`。

## 确定性验收命令

`kp review-check --workspace <workspace>`；在 `independent_review_required` 下，只有全部章节都有当前版本、真实任务回执、通过评分和教学单元证据且无 blocker，才交给全书质量校验。

## 人工审批点

发现高风险事实、版权/许可不明、事实冲突、连续审稿失败或范围变更时，保留 `revise` 结论并升级给用户，不得自行批准。

## 用户交互边界

仅向 `kp-quality-editor` 和根 Skill 返回冻结快照的审稿结论。不得直接与用户交互，也不得暴露任务令牌、工单、快照路径、哈希或内部评分 JSON。

## 下游交接条件

受控结果通过后由 `kp-quality-editor` 执行全书确定性质量校验；正文/合约变化时，只交回 `kp-chapter-writer` 修订受影响章节，再重新提交整个待审集合。


## v1.4.0 生产协作边界

候选和观察绑定同一版本，只读完整当前章与必要比较单元；不改正文，不能自填宿主回执；旧版/晚到/混版本拒绝，diagnostic不能成为独立通过。 所有委派共用总池；正式接收由控制面串行执行，先核当前证明及版本。
