---
name: kp-publication-designer
description: 为知识产品生成受控出版设计预览，并将已批准的设计 token 交给固定 PDF 渲染链路。
user-invocable: false
metadata:
  version: "1.3.4"
  mode: host-driven
---

# KP Publication Designer

## 用途与触发条件

在产品 Brief 已形成、第一次向用户展示 Brief 前调用一次。它根据主题、读者、阅读场景和产品承诺生成受控出版设计预览，不直接生成 PDF、TeX、CSS、QMD 或正文。

## 前置工件及状态

- 当前书稿工作区已初始化，且存在可审阅的 Brief 预览或结构化 Brief 输入。
- 只读取根编排器当前工作单、Brief 摘要和受控出版配置；不得读取其他书稿、客户文件或外部样本。

## 允许读取与写入

- 读取：当前工作区的 `planning/`、Brief 摘要与当前工作单。
- 写入：仅当前工作区 `runs/` 中的出版设计语义预览输入；`planning/publication-profile.yaml` 只能由 CLI 在用户确认后物化。

## 必要步骤与条件分支

1. 只选择受控 `design_family`：`editorial-guide`、`field-playbook` 或 `technical-manual`。
2. 用简短语义字段说明 `reading_context`、`product_promise` 和 `visual_direction`；不得复制外部作品的品牌、字体、封面或文案。
3. 从用户提示词提取 `brand_color_intent`；默认橙色基调，明确紫色基调或合法 HEX 时保留原意，含糊色先给候选。design_family 只选阅读密度，不能改色相。Brief 预览展示原意、实际色块及标注“设计示意”的标题/目录/框/表/图样例；用户批准 Brief 即批准设计。颜色须满足正文/纸色、正文/浅底、强调色/纸色、强调色/浅底对比度，不提交 TeX/CSS/HTML。
4. 根 Skill 将设计预览与 Brief 一次展示；用户批准 Brief 即同时批准当前设计。用户要求仅修改出版设计时，走受控出版更新，不重做蓝图、章节、来源或审稿。
5. 每本书默认只生成一次设计预览。输入版本未变化时复用结果，不重复调用宿主模型；输入版本和有效缓存由控制面核对，不设文本token额度。

按共享契约的v1.3.2宿主路由生成预览：未确认默认illustration_backend_preference=vector；只有根Skill已核实Codex订阅/GPT/原生工具资格且用户确认AI方案，才受控物化host-image。新政策2在蓝图同次说明批准教学任务推导的首次N/最多2N（已有消费修订C＋R）；仅旧政策1沿用6槽×2次。复用既有审批，不创建API Key或MCP配置。

## 禁止事项

- 不直接编辑 `publication-profile.yaml`、`book.qmd`、TeX 模板、Lua 过滤器、Markdown 正文或交付清单。
- 不把统一紫色、报告风、卡片堆叠或外部 PDF 样式当作默认设计答案。
- 不新增用户审批点，不宣称自动设计已替代用户的品牌、审美或商业判断。

## 确定性验收命令

`kp update-publication --workspace <workspace> --from <workspace>/runs/publication-update.yaml` 必须生成可审阅预览；确认后 `kp validate-delivery --workspace <workspace>` 必须绑定当前出版元数据与样式资产。

## 下游交接条件

将已批准的设计 token 交给 `kp-publisher` 受控渲染；章节作者只使用内容块语义，不直接选择 TeX 视觉实现。

## v1.3.1 有限阅读布局与图族语言

布局2默认字体：中文正文PingFang SC、中文标题及强调PingFang SC Semibold、英文正文Inter、代码及等宽标识JetBrains Mono。Brief出版预览说明字体角色并运行doctor核查；缺默认字体须明确提示并停止默认渲染，不能静默改微软雅黑。仅用户明确选择font_policy=fallback才走既有兼容字体。旧布局1不静默换字体，位图插图的字形也不能冒充已使用这些字体文件。保留代码↪折行提示，本版不施工删除该符号。

reader_layout_revision与illustration_style_revision分开冻结。新书布局2包含自然章导读、H2双淡线、品牌浅代码框和品牌列表；A4/12pt/13mm左右及18mm上下、目录层级/编号保持。共同字体角色/母版hash/辅助色板/有限图族随Brief同次预览确认，不另起每图决定。当前唯一style_id为kp-editorial-illustration，revision1。

旧书布局1继续旧固定资源；升级2先列出缺导读章节，在原布局amend并提交后才确认升级。允许明确只升级插图风格而保持布局1，但整书受约束旧D2/SVG教学图也须核验，不能新旧混搭逃P0。品牌/图型纯展示改动不改正文/合约/编辑证明；全图AI换色占原槽位次数与费用，无额度不得混入旧色图。数据图/截图保留真实语义，不强制手绘化。

## v1.3.2 配色与封面

出版预览同次确认风格2与辅助色策略：warm-focus/balanced/cool-structure，品牌强调保持用户主色；同概念跨图同色，不能随机改字形/图族。vector保持本图型画幅；host-image采用中性母版＋当前配色卡，首次两参考、纠正另带edit_target共三输入，preflight须声明reference_capacity至少3；型号/费用未知如实记录。

图级展示输入包含presentation的node/group颜色和标准概念映射，不接收自由CSS/任意HEX；全书概念改色通过出版更新illustration_category_colors确认，受影响图用update-diagram-presentation取得候选。候选检查使用illustration-style-template --candidate-call-id及观察输入candidate_call_id，failed不能覆盖当前；检查后activate-illustration-candidate切换，uncertain只允许受控试排并保留最终人工待决。新命令不释放作者租约，源稿变化仍先amend-chapter。调用completed只代表返回文件，不等同风格通过。

新封面选择cover_layout_revision=2；中文梗概必需，英文/最多四项学习重点/内容说明可选。Brief阶段提炼梗概，蓝图批准后再补学习重点，终稿核对后通过出版更新锁定；不在渲染时补写，不新增每图审批。封面日期仅年月、无语言栏/样稿徽标/开发提示，个人署名仅在已确认copyright_line出现一次；正文页脚独立。旧书缺字段布局1不静默升级。

旧政策1预算及历史保留；新政策2图数由批准教学任务推导，不设每章/全书图数配额；无消费书N个实际host-image制作任务最多2N次，已有消费修订按C＋R。每任务最多两次、串行，失败/超时/换色不重置。资产/成页/跨图核验共同保证可交付结果，无效量测转人工；不能保证随机生成每次成功。当前母版仍为候选时，明确只在隔离验收使用，正式发布需实际用户确认。

封面2默认版权为©年份署名；通过cover_title_lines预览语义断行并保留正式题目、英文词间空格。字号、纸张、字体角色保持既有系统，长题不得靠缩至正文尺度规避。


## v1.3.3 跨主题质量与成本增补

主线、示例/行动、例外、来源有稳定视觉分工。表格按比较/查询/结果用途选择对齐和宽度，不机械满宽或随机变样；来源低于核心结论强调。
