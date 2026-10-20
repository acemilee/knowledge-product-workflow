---
name: kp-product-strategist
description: 定义可销售知识产品的读者、承诺、边界与 Brief，并在人工审批前阻止课程生成。
user-invocable: true
metadata:
  version: "1.4.2"
---

# KP Product Strategist

## v1.4.2 教学取向同次Brief确认

根据目标读者真正要完成的任务提出teaching_focus=method_practice/implementation/mixed，连同正文与配套资源分工在原Brief同次确认，不按题材关键词擅自锁模式、不新增审批轮次。方法类读者不应先学无关文件I/O/CLI框架；实现类承诺保留必要完整代码。未明确采用该字段的旧书保持原契约。

## 用途与触发条件

在新主题已初始化、需要创建或完善产品 Brief 时使用。它只处理产品定位，不生成课程蓝图或书稿正文。

## 前置工件及状态

- 有效工作区和主题。
- Brief 必须记录用户确认的目标受众与受众级别；页数、字数、语言、版式和交付格式是模型建议或用户覆盖值，允许为空。

## 允许读取与写入

- 读取：当前工作区的 `runs/`、用户明确提供的主题材料。
- 写入：仅 `planning/product-brief.md`。

## v1.1 编排工作单

先阅读 `references/orchestration-contract.md`。只能在根编排 Skill 分配的 `definition` 工单内整理 Brief 建议；开始前读取当前工单和 `kp status --json`，只使用工单允许的路径。完成 Brief 预览或受控 Brief 后，由根编排器记录完成证明并等待用户审批；本 Skill 不拥有蓝图、正文或审批写入权。

## 必要步骤与条件分支

1. 将主题拆为目标读者、待解决问题、可验证学习成果、商业定位、范围、排除项和假设。
2. 将未知的读者需求、市场判断或岗位规律写为假设，不能冒充事实。
3. 运行 `kp brief` 创建工件；检查目标受众与受众级别完整。根据主题和读者建议可选交付参数，但不得为收集这些参数阻断流程。
4. 向用户报告 Brief 摘要并等待审批。

将根Skill核实的宿主资格与插图建议纳入同次Brief：仅Codex订阅＋GPT＋实际原生工具可用时确认host-image/vector；其他宿主写明vector，无API/MCP配置问题。具体预算和停止规则消费`references/orchestration-contract.md`的v1.3.2节；不用“默认建议”代替用户批准。

页数口径写入Brief：`target_pages: "正文 1-2"` 表示正文范围，`"1-2"` 表示交付总页数。正文范围不包含封面和目录；实际渲染后分别统计，不拿总页数代替正文页数。初始草案收到修改意见时，用完整更新输入重跑 `kp brief`；尚未批准的Brief不能走 `amend-brief`。

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

## v1.3.1 共同交接增量

消费冻结reader_layout_revision及插图风格版本。布局2的普通章有自然导读，参考/附录不机械追加；圆点表示并列，数字表示步骤，目录层级和正文事实边界保持。资产、引用和正文最终就绪后生成发展性/humanize证明；任意正文修改重跑两份，不复用旧稿报告。

宿主图资产风格证明在章节提交前；实际PDF后的成页证明在最终视觉报告中。缺失/过期/明确失败阻断，主观uncertain留人工而不伪写算法通过。最终用户回执只绑定当前PDF，测试用户回执不能当真实批准。模板/安装资源来自规范包，不读取本次指定书稿之外的开发者work/tmp或客户PDF。旧政策1保留章内1/全书6历史；政策2按批准图任务和C＋R控制发送，不按图数配额。每任务两次、串行，超额/能力缺失停止并返回选择。

## v1.3.2 配色与封面

出版预览同次确认风格2与辅助色策略：warm-focus/balanced/cool-structure，品牌强调保持用户主色；同概念跨图同色，不能随机改字形/图族。vector保持本图型画幅；host-image采用中性母版＋当前配色卡，首次两参考、纠正另带edit_target共三输入，preflight须声明reference_capacity至少3；型号/费用未知如实记录。

图级展示输入包含presentation的node/group颜色和标准概念映射，不接收自由CSS/任意HEX；全书概念改色通过出版更新illustration_category_colors确认，受影响图用update-diagram-presentation取得候选。候选检查使用illustration-style-template --candidate-call-id及观察输入candidate_call_id，failed不能覆盖当前；检查后activate-illustration-candidate切换，uncertain只允许受控试排并保留最终人工待决。新命令不释放作者租约，源稿变化仍先amend-chapter。调用completed只代表返回文件，不等同风格通过。

新封面选择cover_layout_revision=2；中文梗概必需，英文/最多四项学习重点/内容说明可选。Brief阶段提炼梗概，蓝图批准后再补学习重点，终稿核对后通过出版更新锁定；不在渲染时补写，不新增每图审批。封面日期仅年月、无语言栏/样稿徽标/开发提示，个人署名仅在已确认copyright_line出现一次；正文页脚独立。旧书缺字段布局1不静默升级。

旧政策1预算及历史保留；新政策2图数由批准教学任务推导，不设每章/全书图数配额；无消费书N个实际host-image制作任务最多2N次，已有消费修订按C＋R。每任务最多两次、串行，失败/超时/换色不重置。资产/成页/跨图核验共同保证可交付结果，无效量测转人工；不能保证随机生成每次成功。当前母版仍为候选时，明确只在隔离验收使用，正式发布需实际用户确认。

简短输入中的具体学习成果、定位与范围写入公开Brief语义字段，保留user_explicit/model_recommendation/default来源。禁止用通用模板覆盖实际成果。Brief的presentation可承载同次确认的封面/品牌/后端信息，批准后不再补字段造成立即过期。
