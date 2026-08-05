# KP v1.1 编排契约

这份契约供 `kp-create` 和 10 个专业 Skill 共同使用。它约束内部协作，不能展示给知识产品读者，也不能作为正文内容。

## 受控调用

1. 根编排 Skill 先运行 `kp status --json --workspace <workspace>`，只消费其中的 `macro_phase`、`user_summary`、`required_human_decision`、`user_decision`、`next_actions` 与 `current_work_order`。
2. 没有当前工单时，根编排 Skill 运行 `kp orchestration-plan --json --workspace <workspace>`；它会创建或复用 `runs/orchestration/work-orders/` 中版本绑定、带过期时间的工单。
3. 专业 Skill 开始前只读取根编排器分配的当前工单。工单规定唯一目标 Skill、允许读写范围、输入清单、章节目标、允许命令与完成条件。输入工件已变化、工单过期、目标章节不匹配或正文租约被占用时，立即停止，重新运行 `kp status --json`，不得自行修复、猜测或重派下游工作。
4. 一项受控动作开始前，CLI 控制面冻结并校验唯一当前工单；动作成功后，它以该冻结工单写入输出哈希和事件绑定的完成凭证，关闭该工单并创建唯一后继工单。最终发布会同时终结运行意图。根编排 Skill 和专业 Skill 均不得调用手工完成工单命令或填写完成证明；若输入冲突，控制面只保留可恢复证据并重新路由。
5. 用户只看到宏阶段、可审阅工件、“继续/修改”决定、交付结果和可理解的阻断摘要；不得显示工单 ID、SHA、租约、任务令牌、Claim ID、来源台账、审批身份、内部命令或内部审稿 JSON。

## 用户决定

1. Brief、课程结构、样章和 Brief 修订均由根 Skill 在展示当前完整版本后发起一次内部 `user_decision` 请求。请求绑定目标工件路径、版本和 SHA-256。
2. 用户的自然语言“批准”“可以”“继续”“继续生成全书”或“按此执行”由根 Skill 解析为 `approve`；“修改：……”或具体调整解析为 `revise`。含义不明时只追问继续还是修改，绝不自动推进。
3. 根 Skill 在后台记录决定并调用对应受控转换。用户不运行 `request-user-decision`、`record-user-decision`、`approve`、`approve-sample` 或其他控制面命令。已有“继续直到交付”运行意图时，成功批准会自动恢复该意图并消费后继工单；修订与澄清不会恢复。
4. 目标工件、依赖或工作单输入变化时，旧决定必须失效。根 Skill 重新展示新版本并等待新决定；不得把审批身份、历史回复、用户沉默或确定性检查通过当成确认。
5. 旧工作区被用户确认采纳后，既有已批准 Brief、蓝图和样章标记可作为 `legacy_import` 审计记录导入；不得强迫用户重走历史审批。

## 正文所有权

- 同一章节同一时刻只有一个 `kp-chapter-writer` 正文租约。练习、图表和中文终稿均是该作者工作单内的条件动作，不可独立抢占或并行改写章节正文。
- 研究 Skill 只写证据与假设边界；它完成后必须用 `kp record-research-impact` 记录 `no_product_change`、`packaging_change`、`content_change` 或 `unverified`。只有 `content_change` 回到 Brief/蓝图审批与合约重编译。
- 质量编辑只校验、提出修订请求和写入受控合规/质量工件；不得改写章节散文。发布 Skill 只写派生交付物；不得改写正文。

## 独立审稿

`independent_review_required` 只能在正文批量生产前由宿主适配器提供真实父子任务能力并冻结能力快照。没有可验证的回执能力时，根编排 Skill 在 Brief 审批前明确阻断，让用户选择合适宿主或 `deterministic_only`；不得以同会话角色切换或人工填写字段伪造通过。

## 失败恢复

- 优先重新运行 `kp status --json --workspace <workspace>`，按唯一 `next_actions` 选择恢复点。
- 不删除、重命名或覆盖已批准 Brief、蓝图、章节合约、质量报告、审稿批次或交付清单。
- 需要放弃当前动作时，仅使用 `kp cancel-orchestration-work-order`；它只释放自己拥有的租约，不删除书稿工件。
- 交付校验与发布前，控制面只读对账工单、运行意图、事件和现存工件。证据不足的历史悬挂工单会阻断自动交付，不能被猜测或伪造成已完成；但交付质量报告无效、过期或未通过时，新恢复工单必须显式授权唯一的 `validate-delivery` 动作，不能创建空动作工单。
