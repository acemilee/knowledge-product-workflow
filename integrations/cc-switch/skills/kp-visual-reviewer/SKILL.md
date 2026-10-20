---
name: kp-visual-reviewer
description: 核验知识产品最终 PDF 的全页指标与清晰抽样页面，记录页码、缺陷和修复归属。
user-invocable: false
metadata:
  version: "1.4.2"
---

# KP Visual Reviewer

## v1.4.1 首次执行

首次用pdf-observation-template取得当前页图、摘要、evidence_revision及evidence_schema。概览/四宫格只定位；新证据政策下passed须实际细看清晰页，填写view_kind=clear_page、evidence_anchor和legibility/clipping/layout三项checked_dimensions。原始像素尺寸与摘要正确并不能证明模型看懂；看不清保留uncertain，不得用提取文字/像素密度脚本默认签passed。

page仅用于本页可见判断；前后页/续表/图文位置用context，context_page_sha256须绑定本页及相关相邻页。context_required_pages非空时必须查两页并说明flow_cause；确认object_pushback不能passed。章末偶发大面积空白可容忍；图或表挤页导致前页大白必须修，不以“保持完整”自动放行。35%只作候选筛查，不能当因果定论。

只补pending或实际受影响页，合法reusable_pages保留；新政策需旧书显式adopt-reading-policy --confirm，不静默改旧已发布记录。完整有效模型观察＋本人终审，或本人明确完整检查当前PDF两路径仍合法；抽样/机器量测不冒全书覆盖。

## 用途与触发条件

v6 PDF 与完整预览已生成后，由根编排器分派逐页视觉核验。沿用当前宿主看图能力，不创建额外模型或独立审稿任务。

消费当前共享契约：语义/图/当前PDF输入和合格证明未变时复用，不换动作名称重复看图。同项有限修复耗尽后返回具体待审页/缺陷，不循环观察/validate；可用现有失败证据交用户判断，不新增生图或代签通过。实际图像token未知保留unavailable；文本观察不设额度、不预约、不因旧token账本阻断。

## 前置工件及状态

先读 `references/orchestration-contract.md`、当前工单及 `kp status --json`。PDF、出版档案和页面清单必须绑定同一当前版本。

## 允许读取与写入

读取当前 PDF、出版色板、图表清单、全部页面指标及清晰代表/异常页。仅通过受控命令写 `runs/visual-audit-report.json` 和对应清晰页图；不拥有正文、图源、排版模板或人工回执的写入权。

## 必要步骤与条件分支

第一次看图前读取 `kp pdf-observation-template --workspace <workspace>` 的增量清单：reusable_pages保持原观察，只为pending_pages查看清晰图并填写默认observations；各批仅提交本批非空数组，控制面合并旧观察。failed_pages先按归属修上游，uncertain_pages交具体判断，输入未变不重填；needs_clear_images／unavailable_pages保留manual_required，不盲目重跑audit。needs_submission=false时不调用空记录接口，也不据此宣布全书通过；最终仍须全页覆盖、自动指标和本人审批。

1. 运行 `kp audit-pdf-visual --workspace <workspace>`，全页量测纸张、可提取字号、正文占高，默认生成全部当前页面的160 DPI清晰图。显式`--sample-limit N`只供初查；封面、末页及可定位异常页必选，不被普通预算截断。读取report的sampled_pages与原始量测，低清缩略图只定位，不能宣布细字可读。
2. 实际查看当前清晰页面后记录真实观察；相同PDF／预览／profile／图源及页图像素复用有效观察，规则刷新不强迫重看同一实际页，failed原样保留；缺图只补缺项。最终全书须全部清晰页实际passed，或本人对完整当前PDF明确manual-coverage原话确认；抽样、量测和未看图不能冒全书通过。无看图能力保留人工待核，不要求文本额度预约。
3. 实际打开报告列出的清晰图片。按页面类型检查：封面/目录层级；正文字号、行距与连续密度；合理留白；图字/箭头/图注；代码换行；五列以上表格的最窄列、表头和行高；品牌色一致性；裁切与孤标题。不能仅看文本层写 passed。
4. 将已查看页写为 JSON 数组，每项含 `page_number`、`assessment`（passed/failed/uncertain）、`note`、`responsible_skill`，通过 `kp record-pdf-visual-observations --workspace <workspace> --from <输入>` 绑定当前报告。note 必须说明具体观察，failed/uncertain 说明页码、痛点和修复动作；未查看页保留未验证。
5. 没有实际看图能力时，不提交假观察，返回 `manual_required` 和待判断页。根 Skill 展示当前 PDF，由用户直接检查完成主观项；自动 blocker 必须修复。

## 禁止事项

不按像素多就判细字清晰，不将源图字体大小冒充成书字号，不以模型观察替代用户出版确认，不修改正文或生成人工通过证明。无法可靠量化的拥挤、色彩协调和栅格图字只写观察或待人工项。

## 确定性验收命令

观察scope=page只用于该页可见字号、裁切和布局；跨页闭合、内容关系用context。旧缺省context不跨PDF复用。系统仅在同页高清图、相关上下文、版式规则、图源及DPI一致时派生page观察，并给inherited_pages/newly_observed_pages/pending_pages；不手填reuse_origin。每份新PDF的客观指标、链接/书签仍重新校验，整PDF本人批准不继承；观察更新后先重新delivery-preflight再展示。

`kp audit-pdf-visual`与当前观察提交后，根先做delivery-preflight并处理自动问题，再展示准确版本请求用户确认，随后record-pdf-visual-review、validate-delivery、publish。预检失败先修复，不提前消耗本人确认；整份PDF批准始终绑定当前版本。

## 下游交接条件

报告交给质量编辑；版式问题交出版者、图问题交图表设计者、内容密度问题交作者。修改后重新渲染、量测和复核，不能沿用旧报告。

## v1.3.1 资产与成页双层风格门

章节提交前查看当前图原图和批准母版，用`kp illustration-style-template <id>`取得九项观察模板和review_input；实际查看后运行record-illustration-style-check。逐项核对labels、relations、typography、role_scale、palette、density、decorations、geometry、reference_leakage；准确图题也属于labels，不能只核节点。template不是通过报告。

固定同角色字高/线宽/图标语言，比较同图幅；CJK文本区域可用text_regions提供当前图片内[x,y,w,h]，须只框一行文字、排除图标与边框。CLI读实际暗像素计算字高；它不能证明字体文件身份。Latin/量测不可靠明确留待决，不能把估测伪写pixel_measurement。可靠偏离25%阻断、15%复核；字形/装饰改变仍看图，不对不同主题做整图相似度。

PDF生成后沿原全页指标+清晰抽样执行page-style-check（包含在audit-pdf-visual中），绑定PDF/预览/profile/全部图证明，横向核验同类图、A4图字、图文组合和多列表格。未知主观项只允许取得成页证据，最终人工门一次解决；确定性blocker/明确失败必须修复。纯展示变更保持正文证明，图/规则/出版参数变化使对应视觉证据失效。

观察必须绑定控制面输出的当前资产／成页输入；图片、PDF、profile或内容变化后重新核对。不得以旧图、报告文本或占位说明代替实际看图。

## v1.3.2 配色与封面

出版预览同次确认风格2与辅助色策略：warm-focus/balanced/cool-structure，品牌强调保持用户主色；同概念跨图同色，不能随机改字形/图族。vector保持本图型画幅；host-image采用中性母版＋当前配色卡，首次两参考、纠正另带edit_target共三输入，preflight须声明reference_capacity至少3；型号/费用未知如实记录。

图级展示输入包含presentation的node/group颜色和标准概念映射，不接收自由CSS/任意HEX；全书概念改色通过出版更新illustration_category_colors确认，受影响图用update-diagram-presentation取得候选。候选检查使用illustration-style-template --candidate-call-id及观察输入candidate_call_id，failed不能覆盖当前；检查后activate-illustration-candidate切换，uncertain只允许受控试排并保留最终人工待决。新命令不释放作者租约，源稿变化仍先amend-chapter。调用completed只代表返回文件，不等同风格通过。

新封面选择cover_layout_revision=2；中文梗概必需，英文/最多四项学习重点/内容说明可选。Brief阶段提炼梗概，蓝图批准后再补学习重点，终稿核对后通过出版更新锁定；不在渲染时补写，不新增每图审批。封面日期仅年月、无语言栏/样稿徽标/开发提示，个人署名仅在已确认copyright_line出现一次；正文页脚独立。旧书缺字段布局1不静默升级。

旧政策1预算及历史保留；新政策2图数由批准教学任务推导，不设每章/全书图数配额；无消费书N个实际host-image制作任务最多2N次，已有消费修订按C＋R。每任务最多两次、串行，失败/超时/换色不重置。资产/成页/跨图核验共同保证可交付结果，无效量测转人工；不能保证随机生成每次成功。当前母版仍为候选时，明确只在隔离验收使用，正式发布需实际用户确认。

风格2仍用候选母版时，根须将母版与当前PDF同次展示，得到用户明确认可后才用record-pdf-visual-review --confirm-style-anchor绑定当前母版hash；不修改图像hash或代签用户判断。旧文本预算账本仅保留历史，不再作为执行或质量依赖。

风格2资产量测revision2按实际PNG宽与冻结图幅折算可见笔画pt，低于9pt阻断；必须观察/量测最小说明和连线，不能只量较大节点。多行框和不可可靠测量项保留uncertain，成页另核。AI生图发送次数耗尽时停止该生图分支，不能换ID、绕过门或默认切换后端；这不限制文本或vector核验。


## v1.3.3 跨主题质量与成本增补

实际看清晰页检查引入—图/例子—解释—核对组合、表格视觉轴和来源层次；语义通过不代替成页判断。有限修复次数耗尽不跳过必要观察或将failed改passed。

布局2的typography_measurement_revision=1报告分别记录CJK/Latin/InlineMath基础字级；确认原字体家族、实际缩放/角色例外和中英数字/公式外侧间距。已有/缺失空格应同样清楚，不出现双重累计；代码框外侧间距独立保持。不能以名义12pt、可辨认或整页中位数忽略角色偏离；可靠字号漂移阻断，无法可靠定位的图内/嵌套片段留待核。普通教程无固定来源身份卡，来源核查在后台。观察只绑定当前报告输入，旧角色检查缺项不得复用为新通过。

## v1.3.4 修正0.7：最小充分教学与实际核验

先读取真实图片工具能力。当前audit报告/清晰页后用只读pdf-observation-template取得可观察页与Schema；实际看过才填observations数组，不把模板外壳提交为观察，不用文字提取冒视觉。短教程应覆盖全页；如当前报告缺页先走受控补预览/重建抽样绑定，不能手填截图SHA。

重点核对数学与Code角色、表格列间字形及色框外留白、标识符断行、图字字号、图与相邻任务、长代码续段、总结闭合。PDF_POSSIBLE_ORPHAN_CLOSURE与稀疏指标仅定位：是否新增任务、是否只是两行总结、如何调整须有当前页证据；有意义留白可通过，不能统一用留白适中替代检查。模型自述看过与实际read_image调用分开，缺能力的项uncertain/manual_required。最终本人批准仍由本人，不由本Skill代签。

## v1.3.4 真实对照后的阅读增量与成页检查

实际查看封面梗概是否出现一两字孤尾，续文页眉是否指向本页还未开始的节，普通段落是否出现孤行或明显的词内跨页，代码续标题是否有充分对比。新技术教程默认章名页眉；旧profile或显式section保留，不能凭默认值猜当前PDF行为。

短尾页、重复收束和图/代码与说明脱离，都要结合前后页判断信息增量、阅读定位与可操作性；无裁切不等于passed。合理留白可以通过，必要长因果段可以通过，不设占高/段长/图数配额。失败须指明当前页及归属：普通分页/对比交出版角色，重复解释/操作闭合交写审角色；不能以主题不同忽略可重复版式缺陷，也不能绕最终本人批准。
