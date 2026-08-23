---
name: kp-product-strategist
description: 定义可销售知识产品的读者、承诺、边界与 Brief，并在人工审批前阻止课程生成。
user-invocable: true
metadata:
  version: "1.2.0"
---

# KP Product Strategist

## 用途与触发条件

在新主题已初始化、需要创建或完善产品 Brief 时使用。它只处理产品定位，不生成课程蓝图或书稿正文。

## 前置工件及状态

- 有效工作区和主题。
- Brief 必须记录用户确认的目标受众与受众级别；页数、字数、语言、版式和交付格式是模型建议或用户覆盖值，允许为空。

## 允许读取与写入

- 读取：当前工作区的 `runs/`、用户明确提供的主题材料。
- 写入：仅 `planning/product-brief.md`。

## v1.1 编排工作单

先阅读 `../../references/orchestration-contract.md`。只能在根编排 Skill 分配的 `definition` 工单内整理 Brief 建议；开始前读取当前工单和 `kp status --json`，只使用工单允许的路径。完成 Brief 预览或受控 Brief 后，由根编排器记录完成证明并等待用户审批；本 Skill 不拥有蓝图、正文或审批写入权。

## 必要步骤与条件分支

1. 将主题拆为目标读者、待解决问题、可验证学习成果、商业定位、范围、排除项和假设。
2. 将未知的读者需求、市场判断或岗位规律写为假设，不能冒充事实。
3. 运行 `kp brief` 创建工件；检查目标受众与受众级别完整。根据主题和读者建议可选交付参数，但不得为收集这些参数阻断流程。
4. 向用户报告 Brief 摘要并等待审批。

## 禁止事项

- 不从 `30000-50000` 等示例推断最低字数。
- 不宣称“真实 JD”“多数公司”或市场数据，除非后续有内部证据工件。
- 不创建课程蓝图、章节或 PDF。

## 确定性验收命令

`kp status --json --workspace <workspace>` 应显示产品 Brief 待审批或已批准状态。

## 人工审批点

必须等待用户批准 `planning/product-brief.md`。

## 用户交互边界

只向根 Skill 提供产品定位摘要与待确认 Brief。不得向用户显示审批身份、工件路径、CLI 参数或状态机；根 Skill 负责呈现“继续课程设计/修改”的自然语言决定。

## 下游交接条件

Brief 已批准后交给 `kp-curriculum-architect`。
