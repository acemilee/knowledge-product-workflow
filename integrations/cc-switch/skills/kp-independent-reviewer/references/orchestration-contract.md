# KP v1.4.1 编排契约

## v1.4.1 r0.5 有效证据与交付

新书review_evidence_revision=1；旧书不静默迁移，明确adopt-reading-policy --confirm后启用。六项教学检查按当前unit填写evidence_quote和具体note；不得用六条通用理由/附ID代真实判断。合法未变单元沿reuse_unit_ids，默认审稿策略不变。新视觉模板evidence_revision=1要求passed声明clear_page细查、内容锚点及legibility/clipping/layout三维；概览或机器量测只定位，不能默认passed。page仅该页，跨页结论用context绑定本页及相邻清晰PNG。旧已发布历史保留，完整人工覆盖路径仍需实际明确原话，不扩大一般批准。

章末偶发大白可容忍。图表挤页致前页大白不可接受，35%末端留白仅筛查；context_required_pages指定页对必须实际核查，确认object_pushback不能签passed。同小节浮动/文字回填与合法续表可用，禁止缩字、丢行或填充文字凑版。

首次内容计划用content-plan-template <章号> --json一次取本章输入Schema/模板/上下文，不猜枚举或提交内部字段。提交错误保留完整输出，依赖命令逐项检查原生退出码；失败即短路，两次同类失败停止盲试。任务内保存所需Schema，不逐项重复读取或重录未变证明。

PDF批准先完成观察和delivery-preflight，在提问前request-user-decision pdf展示准确scope；用户真实回复后record-user-decision pdf显式填实际scope，再record-pdf-visual-review。缺决定/scope先按便宜诊断修，不盲目重算。未变输入只validate-delivery一次，publish复用完整current交付QA；陈旧、缺失、旧规则或失败须明确先校验，不能发布隐式修复。最终清单仍核全部输入、权限与unknown/混合事务门。

继续旧任务时查recovery-view；operation_diagnostics给原op/tx/封印/下一命令。close-failed-operation --operation-id <原op> --reason <实际原因>仍须核充分证据，不自动结案、不把后来成功当无写入证明。已证precheck失败未结案本轮不另加发布硬门；可发布不等于can_resume。

## v1.4.1 出版准备与恢复

先完成源稿质量、汇编、PDF/预览和实际视觉观察，再delivery-preflight；只有精确的PDF_DIAGRAM_MANUAL_VISUAL_REVIEW_REQUIRED与PDF_FULL_VISUAL_COVERAGE_REQUIRED列为本人待决，其他blocker先修。前检独立于qa-report，不授发布权；所有PDF入口（含update-publication）按同一顺序，出版信息已应用不等于已发布。

### 当前PDF与批准范围

- 用pdf-observation-template的当前路径看图，填写增量observations数组；保留observation_input_sha256/page_image_sha256，不用目录时间找最新。仅实际看过才填，缺能力说明范围；摘要一致不证明语义真实。current不重录，历史page派生由程序产生，不手抄旧观察。failed先修，uncertain明确判断。
- delivery-preflight已就绪后，展示准确PDF和实际观察范围（已核X/Y页、待核页）。模型全页实际passed后仍需本人终审；另一合法路径是用户本人明确已完整核对当前全部页面。普通“确认发布”不自动成为完整覆盖声明。
- 展示问题前登记request-user-decision pdf --scope visual，实际同时请求内容则加--scope content；只有用户选择本人完整检查路径时才加--scope manual_full_coverage，并明确“本人已直接核对全部Y页”。候选母版需同次展示并单列--scope style_anchor。CLI输出的问题保留当前PDF/预览/报告绑定，不记录过期决定。
- 得到真实回复后record-user-decision pdf --decision approve --response <真实回复>，重复--scope列出实际确认且已请求的范围。未收到明确覆盖就只记visual，不把脚本布尔值当用户事实。然后record-pdf-visual-review按对应决定使用--confirm-content/--confirm-manual-coverage/--confirm-style-anchor；不能替用户补选。旧未发布书通过该当前PDF请求显式采纳新范围门；已发布历史保留，不自动改旧回执。
- 无failed/机器blocker且当前人审有效后，validate-delivery并核有效性/publishable，再publish。确认后同稿不改章/图、不重编译/重audit；外部变化仍拒并回到受影响步骤。update-publication生成新PDF后止于观察/预检，不用旧批准隐式生成清单。

### 写作、证明与失败

作者写作前消费本章模板context和writer_requirements；正文用语义图名，不手写本书图号，图注已有同规则。外部原著编号明确范围，代码示例保留。内容计划按section_input_schema检查artifact/asset/block组合，图稿按当前rendering_constraints的色系/辅助色/group/三行限制，先廉价校验再跑绘图。

发展性模板给出required_unit_ids、reusable_unit_ids和真实teaching_check_schema。只对必审且未获复用单元作新判断；合法复用用reuse_unit_ids直接提交，current不重新record/check，不用临时合并器复制不获复用的旧通过。真实后章消费变化仍复核，导航不绑定无关正文。

本地提交只有原句柄/单owner；失败停依赖链。PowerShell每个原生命令后检查$LASTEXITCODE，非零立即停止，不用最后一个命令的成功覆盖前错；报告exit0不等于质量通过。无tx/PID退出不推断无副作用。新操作有持久前置失败证据时按operation-status下一动作close-failed-operation --operation-id <原ID> --reason <原因>受控结案；closed_failed表示失败，不是完成。旧缺证/unknown/未链接journal保持待核，有tx只recover原tx，不用外部goal绕过恢复门。工作区目录名不同于书稿ID时仍核当前workspace与原事务范围。

宿主有真实新角色能力时每角色用冻结输入新上下文；能力未知或仅单上下文则明确降级为自审和历史累积限制，保持已批准review_policy。Skill不能清空宿主历史，不增设LLM等待协调者，不把自审称独立审稿。

## v1.4.0：单一等待与最小充分交接

- 本地CLI提交使用原进程／父工具句柄，由一个等待owner负责；不能额外创建只会sleep、询问进度或转发“仍在运行”的LLM协调者。提交可预先指定`--operation-id op-<唯一标识>`，加`--operation-events`在stderr取得早期ID与阶段JSONL；正常stdout兼容原格式。
- 等待先查`kp operation-status --workspace <workspace> --operation-id <原ID> --json`，或由一个程序执行`kp wait-operation --workspace <workspace> --operation-id <原ID> --timeout 60`。它只读固定回执／原事务，不用深status替代心跳；一个动作不多层轮询。正文提交与控制面收尾分别记录；断流／超时先核原进程与回执，domain完成只`recover-operation`补原事务／控制面，禁止重发submit-chapter或再次执行示例。unknown不猜结束、不释放槽位。
- 原生通道在同一认证与ledger进程使用有界`run_until_attention`；默认只收原回执，`authorized_actions`明确授权dispatch/freeze/commit后才可自动推进；commit还要同版本task_id/context/compliance，不能合成语义证明／用户批准。保持共享总池，失败／EOF／deadline保留未知，不重试未知派单。暂停不派新任务。
- U11真实宿主的无推理守候／可见反馈接线仍pending。程序本地heartbeat≤30秒不算真实进展；未经实际宿主证明不能宣称“零推理守候已接入”。当前降级为单父代理有界等待，按宿主可见能力满足≤60秒反馈，必要模型回合如实计入开销。
- 每个任务／候选版本创建新角色上下文，依据结构化handoff的task/version/manifest、完成项、待办、相关输入和unknown继续；不默认累积全书聊天，不为报进度反复读章。作者读批准公共设定、本章合约／计划和显式依赖。编辑／审稿读完整冻结当前章与全部必需比较单元；缺材料明确请求缺失范围，禁止猜测、截断必要原文或把摘要当原文。当前完整章及必要引用不能为节约Token被省略。
- 同一证据组先逐声明核验，再一次research重复`--claim`关联多项；当前Schema版本首次发现并复用，内容计划依本章template填写，禁止失败提交探索枚举。证明更新用refresh-chapter-proofs／recheck-chapter，按真实依赖局部复核，独立后续章继续；最终质量门仍必须收齐债务。


## v1.3.4 修正0.8 中文编辑

作者租约内的中文编辑先保信息／确定程度，再满足当前合约与用户文体、作者声音，最后改风格。编辑Skill携带固定Humanizer-zh参考及MIT许可，不依赖全局安装。保留有用途的顺序、因果、转折、条件和回指，不逐句拆短或把长解释改成提纲；通顺段落允许不修改。实际编辑独占保留工作区内前稿，用humanize-check的--before对比技术保护；风格命中只是warning，扫描零阻断不是语义已验证。结构审核须核实际段落关系和信息保真。

## v1.3.5 登记前分组

研究先消费status的pending_claim_ids，只处理缺失／旧绑定项；逐声明核验证据后，按同一来源身份／版本、支持范围和许可分组，一组一次research，例如 `kp research <source-id> <url> --workspace <workspace> --claim <claim-1> --claim <claim-2> --excerpt <共同摘录> --license-note <许可>`。同URL不足以合并，原有效来源／历史行保留，所有登记串行。sources绑定是版本证据，不是语义真值；字段为空也不跳来源／假设门。

## 当前证明复用与只读预检（v1.3.5）

根消费readonly_diagnostics；候选源稿已存在时，替换当前实际路径后先运行check-chapter --json，再依refresh_actions安排必要动作；draft_missing／源稿不存在时先走合法draft。developmental_report/humanize_report当前有效时复用原证明，仅刷新缺失、过期或阻断的项；结构blocker先修正文，真实正文、合约和图义变化仍使旧证明失效。诊断独立于next_actions，不扩写权限、不写台账、不收尾；不为每次只读查询重派工单。例子先消费example-check-template的binding_state/needs_execution/evidence_path：current表示代码payload及原脚本、stdout/stderr已核验，散文变化不要求再执行。source_sha256只记录原取证全文，不是当前代码过期判据。失败/timeout证明保留，不签passed；用户明确要求重跑仍需实际执行授权。所有复用决定仅属于后台，不写进读者正文。

这份契约供根编排 Skill 与 manifest 声明的全部专业 Skill 使用。它约束内部协作，不能展示给知识产品读者，也不能作为正文内容。各 Skill 包携带此文件的同哈希镜像，安装单个目录也必须能读取。

## 受控调用

1. 根编排 Skill 先运行 `kp status --json --workspace <workspace>`，消费其中的 `macro_phase`、`user_summary`、`required_human_decision`、`user_decision`、`next_actions`、`current_work_order`、`pending_claim_ids` 与 `readonly_diagnostics`；诊断与写动作权限分开处理。
2. 没有当前工单时，根编排 Skill 运行 `kp orchestration-plan --json --workspace <workspace>`；它会创建或复用 `runs/orchestration/work-orders/` 中版本绑定、带过期时间的工单。
3. 专业 Skill 开始前只读取根编排器分配的当前工单。工单规定唯一目标 Skill、允许读写范围、输入清单、章节目标、允许命令与完成条件。输入工件已变化、工单过期、目标章节不匹配或正文租约被占用时，立即停止，重新运行 `kp status --json`，不得自行修复、猜测或重派下游工作。
4. 一项受控动作开始前，CLI冻结并校验唯一当前工单。标准主动作成功由CLI自动完成原工单、写输出哈希／事件凭证并创建后继；根消费回执／状态，不再手动完成原单。章节draft只记录进展、保持工单和租约；submit-chapter／正文检查点按原实现关闭正文工单，检查点资产待办仍保留；辅助更新只刷新相应授权，只读查询不收尾。最终发布终结运行意图。专业Skill不手工完成／伪造证明；complete-orchestration-work-order保留明确获授权、满足原终态证明的人工恢复兼容路径。
5. 用户只看到宏阶段、可审阅工件、“继续/修改”决定、交付结果和可理解的阻断摘要；不得显示工单 ID、SHA、租约、任务令牌、Claim ID、来源台账、审批身份、内部命令或内部审稿 JSON。

## 用户决定

1. Brief、课程结构、样章和 Brief 修订均由根 Skill 在展示当前完整版本后发起一次内部 `user_decision` 请求。请求绑定目标工件路径、版本和 SHA-256。
2. 用户的自然语言“批准”“可以”“继续”“继续生成全书”或“按此执行”由根 Skill 解析为 `approve`；“修改：……”或具体调整解析为 `revise`。含义不明时只追问继续还是修改，绝不自动推进。
3. 根 Skill 在后台记录决定并调用对应受控转换。用户不运行 `request-user-decision`、`record-user-decision`、`approve`、`approve-sample` 或其他控制面命令。已有“继续直到交付”运行意图时，成功批准会自动恢复该意图并消费后继工单；修订与澄清不会恢复。
4. 目标工件、依赖或工作单输入变化时，旧决定必须失效。根 Skill 重新展示新版本并等待新决定；不得把审批身份、历史回复、用户沉默或确定性检查通过当成确认。
5. 旧工作区被用户确认采纳后，既有已批准 Brief、蓝图和样章标记可作为 `legacy_import` 审计记录导入；不得强迫用户重走历史审批。

## 正文所有权

- 同一章节同一时刻只有一个作者租约；`draft` 不释放它。serial及宿主资格pending时，样章批准后逐章完成，不要求先建齐全部草稿；adaptive资格通过后按批准依赖和总池滚动准备候选。内容计划、练习、图表与文字编辑在本章权限内协作，不能并发改同一正文。
- 最终顺序：内容计划 → 正文与图语义 → 受控引用/图注 → 中文定稿 → `check-chapter` 结构预检 → 同一源稿 `developmental-check` 与 `humanize-check` → 受控图资产 → 最终检查/合规映射 → `submit-chapter`。两个检查都不改正文；修改后刷新全部过期证明。只在当前合约明确要求input_output_artifact/template时强制工件，其他解释可全none；旧计划的显式产物仍兑现，用 `content-plan-schema` 查看真实契约。
- 默认品牌色为橙色；用户明确指定紫色或合法 HEX 时写入 `brand_color_intent`，与 Brief 同次展示确认。阅读版式类别不能擅自改变色相。v6 独立模板消费同一档案；旧 v4/v5 只有受控出版更新确认后才迁移。
- 出版设计是 Brief 的附属预览，由 `kp-publication-designer` 一书一次生成并与 Brief 同次确认。它只能写受控 token；`kp-publisher` 是唯一消费 token 并写入 QMD/TeX/Lua 派生物的 Skill。
- 文本模型动作不设 token 总额、章节、单次或收尾预留额度，不要求预算预约或用量回执。输入未变化时复用有效工件；质量、版本绑定、审批与诊断停止规则仍执行。
- 研究 Skill 只写内部证据与边界。未分类 Claim 默认 factual；建议与教学情境用 `classify-claim` 绑定实际教学内容句、标题和合约，正文不展示官方规范与作者规则的身份区分，不强制“作者建议／情境案例”标签。普通教程默认不输出章末“定义依据／检查依据／判定依据”等卡片；任务需要打开文档或用户明确要求引用时才提供链接。来源搜索失败不能自动降级事实。纯分类纠正只刷新受影响证明；实际改结论或产品契约仍走审批。研究完成后用 `record-research-impact` 记录影响，只有内容变化回到 Brief/蓝图审批。
- 质量编辑只校验、提出修订请求和写入受控合规/质量工件；不得改写章节散文。发布 Skill 只写派生交付物；不得改写正文。

## 独立审稿

`independent_review_required` 只能在正文批量生产前由宿主适配器提供真实父子任务能力并冻结能力快照。没有可验证的回执能力时，根编排 Skill 在 Brief 审批前明确阻断，让用户选择合适宿主或 `deterministic_only`；不得以同会话角色切换或人工填写字段伪造通过。

## 失败恢复

- 优先重新运行 `kp status --json --workspace <workspace>`，按唯一 `next_actions` 选择恢复点。
- 环境发现不等于操作资格：读写／版本探针不能证明受限工具可完成字体解析、SVG到PDF/PNG和PDF编译。真实工况的固定零费用出版探针与教程质量验收分开；缺失／失败记录明确，不从普通父进程推断受限进程通过。Windows临时目录私有DACL、浏览器进程／IPC和Quarto路径映射失败保持诊断，不放宽系统权限或沙盒；两次同类失败停止对应分支，图失败按正文检查点继续合法独立章节，不冒签完整交付。
- 不删除、重命名或覆盖已批准 Brief、蓝图、章节合约、质量报告、审稿批次或交付清单。
- 输入变化后运行 `kp orchestration-plan --refresh --reason <原因>`，失效旧工单立即退休；相同输入幂等复用。放弃动作使用 `kp cancel-orchestration-work-order --work-order-id <ID> --reason <原因>`，只释放该工单所有的租约，不删除正文，不等待自然过期。
- 首次完整命令及必需参数从 `kp commands --all --json` 获取并按当前版本缓存；恢复读取实际状态/工单，再用 `kp commands --all --command <精确命令名> --json` 查当前必要参数及相应Schema/模板，不反复输出全索引或穷举命令名。定向发现不授予阶段权限。实际生产shell先核实入口，绝对路径探针不证明别名/ps1可用；不得为入口错误放宽系统策略或安装依赖。正常提示与失败分开，进程退出状态还须对应实际版本回执。
- PDF 有效而预览缺失/过期时运行 `repair-pdf-preview`；PDF 损坏才重渲染。v6 依次执行 `audit-pdf-visual`、看清晰代表/异常页（具备看图能力时 `record-pdf-visual-observations`）、在提问前`request-user-decision pdf`明确scope展示当前 PDF、获得用户明确判断、`record-user-decision pdf`记录实际scope、`record-pdf-visual-review`、`validate-delivery`、`publish`。报告与人工回执分别绑定版本，二者不能互相冒充；模型无看图能力时保留待人工项，用户直接检查当前 PDF 可完成主观复核，自动 blocker 仍须修复。
- 交付校验与发布前，控制面只读对账工单、运行意图、事件和现存工件。证据不足的历史悬挂工单会阻断自动交付，不能被猜测或伪造成已完成；但交付质量报告无效、过期或未通过时，新恢复工单必须显式授权唯一的 `validate-delivery` 动作，不能创建空动作工单。


## v1.3.1 阅读与受控插图

本版布局2默认字体为PingFang SC中文正文、PingFang SC Semibold中文标题/强调、Inter英文、JetBrains Mono代码/等宽标识。doctor使用实际XeLaTeX/fontspec探测，缺默认字体阻断，不静默回退；用户明确确认font_policy=fallback才用兼容字体。布局1保留旧字体。代码↪折行提示按用户决定保留，不在本版施工删除或新增整段复制安全阻断。AI位图文字不冒充已嵌入上述字体文件。

出版更新见 `kp publication-schema`。章节写作以当前合约及冻结内容计划为必要输入；结构审核提交当前template_id，资产／成页观察绑定控制面返回的当前输入指纹。文本预算接口已经移除。

页数可用 `target_pages: "正文 1-2"` 指定正文口径，排除封面/目录；不带正文前缀是交付总页数。初始Brief修订重跑完整 `brief` 输入；蓝图草案收到修改或其Brief绑定过期时，`amend-outline` 保留历史并重绑当前Brief，不先批准旧蓝图。沿用CLI生成模板的绑定字段即可，禁止宿主手改hash。

新书reader_layout_revision=2，旧档案缺省1；illustration_style_revision=1表示kp-editorial-illustration固定母版/角色/三图族，并与阅读布局独立。资产提交与asset-style-check先于最终两份正文证明和章节提交，page-style-check在PDF生成后并入全页报告。主观待决可取得成页证据，在最终人工门统一解决，不增每图审批；明确失败不可用回执覆盖。

旧政策1兼容预算章内1槽、全书6槽、每槽最多2调用、串行1；共享AI风格板占全书槽，复用已认可母版不新增费用。授权一次消费，失败/超时/编辑照记，未知费用不当0；改ID、移章、换色/模型/后端不重置历史。预约未发送可cancelled；unknown先核对响应/取消结果，不盲重发。研究连接/轮询8秒，生图总等待明确300秒、每次等待至多60秒并反馈，超时记录与保留预约。

资产原图按冻结标签/箭头/角色/品牌检查，实际PNG字高量测不代表字体文件身份。原生工具未暴露model/quality/size时记录真实能力和像素，不宣称已指定型号。输入只发送可见caption、节点标签、合法边和风格；不把heading_anchor、工单、内部hash送作图中文字。

新图/规则/色板使相关风格及成页证据失效。正文/关系变化走amend-chapter；纯展示在原槽位修复，再受控出版更新，正文/合约/编辑证明不变。pdf_visual_repair_pending必须给合法具体动作，不能循环validate自愈。没有额度或真实工具能力保持未通过，不能静默drop必需图或改通过。

## v1.3.2 宿主路由与订阅成本停止门

definition阶段由根Skill核对当前宿主、认证方式、当前模型及原生工具能力。只有Codex订阅认证、当前GPT模型、原生生图及所需参考能力均可确认时，才在Brief与出版预览同次建议host-image并取得用户决定；风格2还须支持首次两参考、纠正三输入。起始提示词已有明确选择时纳入Brief，不重复追问。Pro身份或GPT名称本身不是工具能力证明，CLI也不能独立辨识宿主订阅。

其他Agent（含DeepSeek harness）、API认证、非GPT、资格未知或无相应原生工具时采用vector，在Brief说明。明确但不受支持的host-image请求须解释限制，等用户接受vector或换宿主；不能静默改写明确请求。工作流不接入生图API Key、外部付费API或生图MCP，不主动提出这些配置。新书未确认配置保持vector，用户确认AI方案后由出版预览受控写入illustration_backend_preference=host-image；图任务明确填写批准backend，风格2预检在预约前检查该选择。用户明确vector始终尊重，精确数值/属性采用确定性图或表格。

旧政策1章1槽、书6槽、每槽2次、串行1，即每书最多12次实际生成/编辑发送；复用静态母版不占新调用，共享新风格准备占槽。每次新发送先预约、记录dispatched、仅调用一次，凭证不可重复消费；缓存命中不得发送。第二次只修正明确缺陷或执行已确认展示更新，不因轻微审美差异重试。失败/超时/unknown保留占额，unknown未核实前阻断其他预约。订阅额度/限流错误停止当前生图，不以轮询重发、换模型、换图ID/颜色、移章、复制工作区或另开会话恢复配额。

所有文本模型动作均不设token额度门；内容范围与质量由写稿／审核Skill按批准教学任务处理。相同输入与当前合格证明复用，仅重审受影响工件。禁止重复validate自愈或无新证据盲重试；按AGENTS诊断时间盒和实际工具错误停止对应分支。

AI生图工具仍须遵守图像调用凭证、次数、未知回执与订阅限流规则；文本预算取消不改变该独立链路。工具不暴露实际图像token时记unavailable，不宣称账户实耗或锁定型号。


## v1.3.2 政策2：教学需求、位置与成本

本节取代新书的固定图数配额，旧政策1/历史预算不重置。新outline模板显式illustration_policy_revision=2，旧输入缺字段仍1。每个普通小节明确required/useful/none及理由；有具体读图问题、组织增益、替代形式评估、独立视角且能正确可读实现才准入，不为填空白或每章配图。必需图未满足不能改成none躲预算；有益图制作选择单列。已有合格图可文字指引回看，不复制另一章清单/证明。

先Brief确认Codex订阅/GPT/原生参考能力与路由及成本模式，再在蓝图同次批准图任务/成本摘要。章内illustration_tasks各有稳定task_id、section_id、decision、production、trigger、read_question、viewpoint、visual_gain、alternative_rationale、图型/后端/placement；none只填不用理由，无task_id。任务经compile-contracts派生。内容计划消费相同身份，主要asset_decision可为table/worksheet，独立图任务仍合法；多个diagram/explanation实例按计划顺序。作者细化后才冻结标签/边及生成，不提前用预期N发图。

图按orientation前置地图、synthesis后置综合、viewpoint_change互补视角出现，位置跟随语义。diagram-template --task-id给出当前paragraph_anchors；从返回上下文原样选择after_anchor或before_anchor写入当前租约内的内容计划，草稿更新用submit-content-plan --replace-draft。不手算hash、页码或偏移，不向用户展示技术字段。锚点缺失/已变先重新取当前模板，受控修订计划；重复插入不复制/倒序。无图可合格，多图须独立任务和实际9pt门；连续致密内容先调整分组/原生组件，不硬画装饰图。

新书N只数production=create且backend=host-image的批准任务；引用/不制作/vector不计入。freeze-illustration-budget从已批准蓝图派生授权，首次N/最多2N，每任务两次、串行。已有消费修订按C＋R，C包括停用/失败/unknown历史，R是当前批准任务剩余尝试之和，停用不退款；N=0旧书保留账本，未发送才可取消预约。计划hash过期、未登记任务、同义重画、超额、unknown或订阅限流停止，不换ID/颜色/工作区或validate无限重试。语义设计及资产／成页观察不再走文本token预检；真实输入与观察仍须绑定。

机制总览process_overview仅政策2/风格2、hidden_mechanism或synthesis任务、三至六阶段冻结链可用；有限overview卡片语法，不造条件菱形/分支/步骤或新AI图族。普通短操作仍列表。时序等不支持的host图按Brief说明用vector。

当前实际host-image引用才需要对应候选母版确认；纯vector/N=0不因style revision 2或目录旧失败PNG被拦，仍做正常成页核验。批准元数据不改变图像、消费历史或新政策视觉缓存；旧签名兼容读取。全部资产/引用就绪后再做同一最终源稿的两份章节证明；任务审核不替代最终检查。最终用户看当前PDF，不用模拟测试回执代签。


## v1.3.3 阅读结构、正文检查点与质量核验

用户简短输入不附加排版提示。宿主将具体learning_outcomes、product_positioning、scope_inclusions/exclusions写入正式Brief及来源分类。出版信息可在Brief的presentation中同次预览；cover_title_lines只控制展示断行，必须保留正式题目和英文词间空格；封面2默认©年份署名、日期年月、署名仅一次。

结构审核使用developmental-review-template与record-developmental-review。读取模板review_input与template_id，提交必审单元的实际observations；不进行文本预算预约或结算；代码、事实、数字、标识和来源受保护。样章各节、密度/枚举标记、产出示例/验收单元必审；passed写具体依据、revise阻断、uncertain留读者审阅。解释段可保留因果；圆点用于并列，数字用于依赖步骤，共同维度比较用表格。密度是warning，不能用装饰/拆句清零或批量自动签passed。正文/合约/计划变化后重新绑定当前审核和developmental/humanize证明。

旧托管工作区用adopt-reading-policy取得只读预览，用户明确采用后才加--confirm；需要已确认v6布局2，不静默换布局或重写旧书。政策1旧审核不能代替新政策必要证明，重复采用无额外事件。

审核复用只选模板reusable_unit_ids并提交reuse_unit_ids，不能填新hash。控制面核对合约/计划、单元、章导读、相邻/显式引用单元、引用章节、研究台账和图语义；任一相关变化不复用。已过或uncertain观察可原样继承，revise不能复用；旧版无上下文证据先重新审核。observations与reuse_unit_ids合计须恰好覆盖必审单元。全部必审复用时无需新模型动作；有新观察必须实际审阅当前输入，不要求额度预约。旧证明归档、新绑定由控制面写入，uncertain不会变passed。

单图失败只停该资产分支。先有当前图语义与合法引用，再完成正文/事实/编辑证明和合规映射，用checkpoint-chapter-content记录正文就绪、待办图并释放该正文租约。这不是chapter_submitted，也不退款/重置图次数；样章仍需产品决定。后续独立章继续，全部正文就绪后render-review-preview在runs/review-preview中渲染完整诊断PDF，待办位置明确标示、footer写诊断预览；不覆盖delivery、不生成发布清单、不采用错误图内容。正式submit/validate-delivery/publish仍拒绝缺项或明确失败；修图或改后端走原批准路径。

补齐资产后按真实状态重新取得有效当前章节工单，用原submit-chapter完整提交；不使用已释放租约。样章正文未变则保留已记录审批；正文改变时归档审批标记、失效旧决定并重新等待用户，补图本身不能生成新批准。

host-image首次references为style/palette，纠正为style/palette/edit_target。有序references是发送路径、角色、SHA和提示词的真源，edit_target已在清单中，不再重复追加。真实调用能力/次数按原政策2；未确认外部API或MCP时不探索；AI生图调用次数仍按原规则。

新schema2文本账本在lean60000内默认预留12000给最终视觉与一次有限修复；前段动作不得侵占，改action名称不解锁。只有控制面确认全部正文就绪并进入收尾阶段，才能使用预留；课程/内容范围变更重新匹配范围。旧schema1消费和0预留读取兼容，不隐式重写。实际账户未暴露usage仍注明未知。

普通行内标识有品牌浅底及安全断行，斜杠等自然断点优先；不能在RawInline输出段落环境。独立长数据块在块级处理。使用实际字体/宽度估算标题与示例、短列表、末段与来源卡的阅读组合；不能全局缩字、收行距、填装饰或强制占高。最终仍实际看清晰页并取得当前PDF的用户判断，测试审批不是正式接受。

## v1.3.3 教学组织Rubric2与任务成本

课程先说明各章独立贡献；作者的reader_job表达新增理解、判断或动作，block_sequence按信息关系选择，不给所有主题套相同的小节模具。artifact仍只表示现行Schema中的读者资产，none不写普通学习成果。编程可用代码，策划可用规则/流程，定量可用推导/算例；必要连续因果解释保留。重复案例须增加条件、难度、视角或支持独立操作，否则简短回指。教学分类、预期/实测与来源留在后台，正文不承担依据身份声明义务。

reading_structure_revision保持2，reading_review_rubric_revision旧缺值按1、新书默认2。旧书采用走adopt-reading-policy只读预览再确认，已批准修订的技术恢复不重复询问未变的产品方案。Rubric变化使相应旧观察失效，不能把缺六项的历史报告复用为新通过。

用developmental-review-template取得当前模板、book_reading_index与teaching_rubric；需要格式时读developmental-review-schema。每个必审单元teaching_checks恰好有learning_increment、representation_fit、complementarity、repetition_value、actionability、reading_hierarchy，各含assessment和具体note。任一revise则总体revise，否则有uncertain则总体uncertain；not_applicable要说明原因，信息增量与层次始终适用。实际比较的其他单元填compared_unit_ids，由控制面保存当前依赖；只读索引和必要片段，不重复投送全书。只签实际观察，不发送占位模板，不用段落长度/组件数量代替教学判断。

图/例子与引入、解释和核对点形成读者可连续理解的组合；短结果表与正文保持稳定视觉轴，普通教程无固定依据卡。完整文件、推导和数据受保护，实际分页与色框仍看清晰页。写作先结构编辑、再中文定稿，当前报告与实际源稿绑定。

混排由Skill默认负责：普通中文与英文/数字及行内公式之间有统一外侧留白；作者自然使用半角空格，PDF对缺失兜底，已有空格不叠加。源码/安装候选锁定中文、英文及数学基础缩放，正文12 TeX pt约11.955 PDF pt，代码沿既有字级。保留原字体家族；不以名义12pt或全页中位数证明正确，不按主题随机调比例。AST只归一化外部文本边界，Code/CodeBlock/URL目标/路径及Math payload不变。普通行内公式基础字级与正文协调，分式/上下标的自然结构高度不是漂移。

成页报告typography_measurement_revision=1才覆盖布局2新增角色检查；旧缺省0可读但不冒充当前证明。正文角色字级可靠偏离声明目标时阻断，已知零留白/重复累计必须修复。嵌套/图内字体无法可靠量测时保留人工待核，不能放宽门或误判合法脚注、代码、上下标。修订后只更新受影响的语义/成页证明，所有模型观察继续绑定当前输入；ActualText用于原生字符保真，不以隐藏图层替代正文修订。

质量标准固定。按批准学习成果安排必要写作、六项结构审核、终稿、成页观察及证据驱动的局部修复；不以token额度删减内容或跳过审核。有效缓存、局部变更与本机确定性工具用于避免重复劳动。

2026-10-07用户明确取消文本token预算门：新书不创建token-budget.json，旧账本仅保留历史，不再参与审核、质量指纹或交付判定；相关预算命令移除。图像次数账本继续独立执行。

修复次数不等于合格。连续两次同类失败先停止重复动作并诊断，有新证据与合法工单才局部修复。实际质量缺项保留未通过；本机诊断／渲染／回归仍按授权、超时及用户限定轮次执行。


## v1.3.4 当前执行规则（旧书兼容不等于新书默认）

新书PublicationProfile schema4默认封面2、阅读布局2、插图风格2、preferred字体、12pt和compact段距。先由产品/出版Skill把具体主题的梗概与主线写入Brief同次预览；不能回落封面1或用泛泛的“专业知识教程”代替梗概。品牌只来自当前明确输入，未指定不推断私人姓名。封面年月、无语言栏/样稿提示，署名单次；正文页脚独立。旧已批准档案不静默升级。

根在初始Brief的reader_request保存本次原始读者需求，fixed_inputs保存用户给定原文/数值/前提。目标区间和硬上限分开：例如target_pages="目标6-8页，最多10页"默认总页数；不得转成正文6-8或硬最大8。首选偏差如实报告，硬超限阻断。未给字数不制造字数硬门；篇幅估计计入封面、目录、代码、表图，不用机械平均页数填内容。

课程按读者要完成的判断/动作组织，而不以无依赖的术语清单代替教学路径（目录标题仍优先主题短语，任务放目标/reader_job/导读）。各节reader_job说明新增贡献；贯通案例再次出现必须增加条件、完成新操作或支持独立核对。每块选最短而完整的表达：共同维度用表，关系用图，依赖步骤用编号，并列用圆点，因果用连贯段落。不能把同一结论在故事、散文、图、表中依次复述；不能以段落长度/图数/列表比例代替实际质量。

作者写完整可核对的教学单元：按当前任务提供必要条件、做法／示例和判断结果，不给概念解释硬套实践模具。自然导读按规模写必要信息，不按字数填满、不重复首段。draft骨架已有冻结标题和未完成标记，不能携占位提交。代码、推导或决策过程可用artifact_kind声明读者产物，asset_decision=none；连续文字可承载具体推理／备选条件取舍结论，但有散文只算候选存在，不算完成证明，明确产物进入六项实际必审。C/C++／JavaScript非空闭合围栏可表示程序存在，不代表实跑／实机通过。普通解释用artifact=none，不为合规硬加图表或列表。

真实事件、实际输出和计算数值必须有对应来源、执行或完整成立条件；缺输入保持未知，不能补造结果。人工教学数据不冒充模型输出/质量评测。后台事实分类/来源继续，普通教材不输出固定“检查依据/判定依据”或官方/作者身份卡；教学任务本身需要的文档ID/出处保留。

审稿人实际读取当前全章、内容计划、执行证据与book_reading_index，并按需打开被比较单元。先找具体问题，再给保留/修订结论，不以六项理由写满签通过。fact_check_prompts只是定位；自动结构/语法通过不等于语义正确。能真实委派就用作者外上下文，reviewer_identity/review_context_kind如实记录；这不冒充正式independent_review_required的宿主适配器证明。没有能力则明确辅助自评局限，最终内容人工待核并入同一次最终审阅，不新增逐节审批。

完整的、已审阅且本次获授权的Python小例子可以先example-check-template，再run-example-check --approve-execution实跑；不是安全沙箱，不自动执行不可信文档。stdout/退出码/当前代码绑定由CLI生成，不手填执行证明。输入未变复用有效证据；代码或条件变化重新核验。语法检查始终不自动执行代码。

导航只绑定章节契约，真实内容引用绑定具体单元/结果；小数、版本、URL和代码不建立章引用。新模板列dependency_context/reusable_unit_ids，只补真实受影响观察；真实来源、结果、相邻或比较变化仍失效。旧章仅上下文待核可排队，不阻断独立后章生产；所有待办在最终门仍必须解决，不拿图专用checkpoint绕其他错误。

图任务按准入规则确定，不按每章/全书图数。新制作任务声明relation_kind及图型，方向由任务冻结；短线性交接用stage_flow，真正分支依赖用flow，层级/比较/时序采用对应族，不把所有关系强塞三张卡。vector在schema4可由控制面物化有限配色，显式展示输入仍严格校验；同概念同色、跨关系使用受控色板，不随机HEX。首次政策2引用按placement/anchor_hint解析语义位置，不要求尚未存在的正文hash；旧明确锚点保留，过期位置用insert-diagram-reference --rebind-position，不重画同义图。具体错误先读诊断/Schema，不猜字段。

短教程10页以内清晰全页核验。没有清晰页图时仍做可做的自动量测、保留manual_required，不伪造模型看图。最终用户实际直接核对可用confirm-manual-coverage，内容确认用confirm-content，并保存当前用户原话；明确failed/自动blocker不能覆盖，旧视觉回执不自动成为内容批准。测试审批只覆盖明确授权的Brief/蓝图/样章，不能模拟最终本人判断。

预览只消费preview-manifest.json的当前世代路径/gallery_path，不glob历史PNG。各阶段和交付前用run-snapshot更新后台报告；verify-current拒绝过时快照。effect_state=unknown时不盲重发，先读取当前阶段、回执和工件；验证门已证实完成可直接续合法后继。identity区分源码/安装/资源与自报宿主模型；一个新符号不等于全包一致，未知不写verified。只使用已核实本次入口，不自动安装或替换全局工具。

文本模型不设token总额、章节、动作或收尾额度，也不以审核次数配额替代。图像N/2N、C+R、每任务最多两次、串行、unknown/限流停止继续保持；混合实验不能重置旧调用。阶段/10分钟保存检查点，反馈间隔不超过60秒，同类两失败转15分钟证据诊断；失败只阻断依赖，合法独立分支继续。当前指定书稿即使位于tmp也可按工单操作；禁止读取指定工作区之外的开发work/tmp、旧客户稿或源码来猜接口。

## v1.3.4 修正0.7补充契约

新CLI书稿捕获完整reader_request并启用Rubric3；旧档案/库调用不静默升级。关键命题观察按当前模板quote/ref绑定；supported、unsupported、uncertain仍是实际审稿判断，回执字段不等于语义证明。辅助领域提交更新合法工单，未知突改先核实；命令发现返回argv/角色/枚举/模板，模板重复不覆盖填过内容。

数学表达不得误放Code；长程序与标识符保持原payload，不任意拆字母或缩字号。写稿负责最小充分单元、信息增量及实际操作闭合；图设计负责关系和有限图幅，出版负责表格/续页/闭合，视觉负责实际清晰页观察。稀疏只定位，不设填充率、段长或图数配额。普通教学不增加固定来源身份卡，来源后台保留。

交付book.md相对图链以文档目录解析，整个受控包迁移后仍可解析，代码教学中的图片语法不改。观察模板只给当前可看的页，未实际读取的页不得passed。真实模型/宿主/工具与委派独立性记录事实；无图像或独立上下文能力不假装具有。Loop生产者只有一个冻结初始输入，评估者只读观察，内容建议只进入下一候选的Skill/代码，不干预当前生产。

修正0.7混合候选仍在原host-image预算路线：有限stage_flow素材+确定性SVG，经原调度、completed原件/来源、候选、风格、当前本人认可与激活。原素材/失败/unknown/C+R不重置；字体/标签发送前拒绝。synthetic本地探针与真实AI来源分开，未有实际调用/本人认可仍pending，不用vector工程通过代替。

## v1.3.4 真实对照后的阅读增量与成页检查

写审角色按读者动作和信息增量决定表达与篇幅：既有约定不在每章重复总述，新条件、必要因果和独立操作所需的提醒保留；章末无增量复述精炼或归入对应工件，不强制总结。成页角色实际核对封面孤尾、普通分页、续段导航、代码续题对比与工件闭合，无裁切不等于阅读质量合格。不设段长、图数或填充率配额，不取消事实/数学/证明门。新technical_manual布局2默认chapter页眉，旧档案/布局1及明确section选择保留；任何最终效果以当前PDF为准。

## v1.3.4 当前正文前提引用的边界

当前模板若列出body_unit:<id>，它只定位这一版正文中的作者情景前提与推导。实际阅读该单元，在命题note中写明使用的给定条件、独立演算或反例；不能仅引用原结论证明原结论。contract/content_plan只有其实际内容覆盖前提时才可引用，不因索引存在就签supported。正文单位不证明外部事实真实，也不证明程序已经执行；外部事实沿研究核验，实际运行主张读取当前实跑记录。缺失必要前提仍uncertain/unsupported，不能用更换ref名称绕过。


## v1.4.0 写作前依赖与并行候选

新书默认adaptive、旧记录保持serial；先读production_overview和`kp production-plan --json`。样章先串行并保留原用户批准，之后仅安排依赖就绪章。蓝图production_policy_revision=1时逐章明确production_dependencies/production_exports及shared_context；None表示未论证，[]表示明确无依赖。navigation不等待正文，interface使用批准定义，result等待已受控接受且证明当前的结果。缺失、矛盾、结果循环在写正文前处理；不能写完后才首次规划。正文新增上一章/下一章/前文/后文引用时报告并修订受影响依赖，不假装自动扫描覆盖全部语义。

4/5/6是全部同时工作子代理总池，父代理另计；研究、练习、文字、图义、审稿、语义核验及孙代理均占同一池，本地程序校验不占。候选完成一章即可入校验和审稿，审稿积压优先审稿，等稿时提高作者占比；未知任务先查回执，完成不等于槽位释放。保留同章唯一作者、冻结版本、幂等接收及单写者共享台账/ID/预算。

先消费本章、批准公共设定、实际引用小节及相关Claim，禁止每个任务默认重复全聊天/全书；审稿必须读当前完整章及必要比较单元。候选输入、内容计划、例子、图、结构/发展性/中文报告及索引按task+version私有绑定，不回落canonical旧通过报告。只刷新真实变化部分；3.3仅影响4.2时保留4.1/4.3。上游正文未变但来源/执行/审核证明失效仍撤销结果就绪；无关组继续。必要事实核验、全文阅读和全书一致性判断不可削减为省token。

当前原生宿主资格pending，生产实际降为原serial；不得以手填production_verified、fake64、诊断/模拟4~6签真实并行或独立审稿。`production-start --diagnostic`及离线结果只用于明确请求的私有工程诊断，不派模型、不写正式章、不满足发布门。真实启用需完整可信父子回执、只读/无环境权限与槽位生命周期资格，未知保持停用；不会自动改宿主配置。开发/候选通过绝不自动安装、全局同步、生产迁移、重启、Git或发布。

候选命令顺序（父控制面，隔离诊断）：production-start --diagnostic → production-next → production-result（作者返回正文）→ production-freeze → production-evaluate；只补返回的missing/stale项，production-structure-result提交真实结构观察后复验，同版本有效证明复用。批准Brief为independent_review_required时才额外进行整章独立审稿，用production-review-result，原Rubric与必需教学单元保持；deterministic_only在必需语义／技术检查齐备后以作者候选进入acceptance_ready，不默认加独立reviewer。diagnostic没有模拟签收生产的权限；production-result里的passed不能代替整章审稿与真实宿主回执。原生桥接的真实权限和终态资格未通过前，不把这组命令作为真实书稿并行路径。

受控返修用production-revise --run <id> --chapter <n> --reason <实际原因>；旧候选／晚结果保留，未知宿主线程不因改版本释放。接收入口production-commit，正式资格及完整候选审稿不足均拒绝；中断查production-recover，硬崩溃控制锁由production-recover-lock核PID／进程创建身份，仅死进程可释放。共享请求用production-shared-request排队、production-shared-next取得原领域命令，沿当前工单和原Schema串行执行，再production-shared-complete核实际工件；来源按证据组重复--claim，不手填passed作回执，公共定义修订仍走原蓝图本人批准。容量0暂停派发，unknown／未释放线程仍计数。已有stdio／事件账本／长存活控制面及本地协议验证，不等于真实接线／资格已经通过；当前正式原生分支保持关闭。


2026-10-15 · 原生桥接工程新增production-host-connect（零模型）、production-host-replay（仅诊断）与production-host-session（长存活JSONL，默认model-turn-limit=0）。详情及合法动作见operator-runbook的“v1.4.0 原生接入工程入口”。真实试验有明确授权和额度才可启动qualification；已有本次授权沿其范围执行，不重复索要许可；开发不自动改变已安装环境。pending沿既有serial继续；初次样章及样章修订仍走原本人批准链，已批准样章不得自动重写。未知／未释放槽位与原回执保留，真实输入固定后再发任务；完整章节回收即滚动推进，不等同批其他章。命令存在与本地协议通过均不能签真实资格或效率通过。
# v1.4.0 修正：精确失效与可恢复生产

合作暂停后的CLI统一拒绝新的辅助写命令（包括报告、模板和编排工单），只允许明确只读查询、原事务恢复／取消及显式继续。关键内容计划、文字／结构／发展性报告和图资产库入口也检查同一暂停边界。暂停前已拥有的事务可按原operation_id恢复，未知原生任务仍保槽；不能借“恢复”新派任务。不把合作暂停保证扩展为未实测的UI硬暂停。

上游已接收结果的正文／图义／成立条件变化时，已提交消费者也必须复核，即使正文没有章节编号引用。语义证明绑定实际消费的AcceptedExport；逐小节只刷新真实使用方。content_plan和中文文字报告无需仅因上游结果变化重做。补齐必需语义／技术报告后，使用 `kp recheck-chapter <章节号> --workspace <workspace> --compliance <当前映射>` 仅更新合规证明；它核原提交快照、保留旧报告、支持幂等恢复，不修改正文／版本、不替代独立审稿。正文确有变化时拒绝此入口，仍走正文修订。

新CLI书稿的binding_policy_revision=2；旧库调用／旧书缺省1，禁止为了绕门手改state。旧书先用 `kp adopt-binding-policy --workspace <workspace>` 只读预览，只有用户明确迁移命令后才加 `--confirm`；拒绝热迁移，保留旧报告／审批归档，不自动恢复已撤销样章批准。迁移缺领域依据的旧证明重新复核，不能静默转为policy2通过。

首次生产前把章节依赖分为navigation、interface、result并批准；None是未知，空列表必须来自实际独立性判断。先明确共享术语／案例与结果出口，再写正文。结果依赖等待当前已提交且必要证明有效的AcceptedExport；串行普通提交也能登记出口，不伪造原生资格。4／5／6按所有子代理总数计算，未获真实宿主资格保持串行。

蓝图修改前运行 `kp preview-change-impact --workspace <workspace> --from <候选蓝图> --json`；向用户说明变了什么、为何重核、哪些不动。实际修订产生的impact_id才可用于 `kp refresh-chapter-proofs <章节号> --workspace <workspace> --impact-id <impact_id>`。不要通过amend正文来更新证明；只补报告指出的失效领域。原证明、来源台账、原生manifest和用户回复原件保留，不手填哈希、等价标记或批准。

同章一个图任务改变，先核图义及实际结果消费者，不能笼统重审全书。正文未改且批准义务未变的样章保留批准；实际正文、义务或图义改变须回人工门。受影响章节进入deferred_repairs；不依赖它的就绪后章可继续。最终汇编、PDF及发布仍须全部债务／未决事务清零，PDF生成后保留视觉与本人批准门。

暂停、断流、模型切换或上下文压缩后，第一步 `kp recovery-view --workspace <workspace> --json`，再读当前status／工单与最小相关工件。核对已保存稿、原operation_id、未知host任务与原始回执；不知道终态就不重发、不释放名额、不另建intent。仅恢复原短事务时用 `kp recover-workspace-transaction <operation_id> --workspace <workspace>`，先核锁拥有者终态。合作暂停只停止控制面新派单；UI硬暂停是否停止外部工具／子代理未实测，不宣称全部已停。禁止反复从第一章或全聊天恢复。


## v1.4.0 r0.8：原生元数据、结果与执行容量

真实试验获授权后按operator-runbook“原生资格与低开销顺序”执行：先零模型metadata预检，再同进程内存登录；第一批资格结果／执行归还后显式派同父池第二批，收齐两批真实重叠和补位才admit。ephemeral只用thread/read(includeTurns=false)，不查询不支持的历史。collect-qualification观察到期返回pending，继续观察原句柄；真实流错／EOF／总时限保留unknown，不重发。

派单使用全新聊天上下文，先发齐本批就绪任务再等待，作者和审稿共用总池。父只返回task_id／异常摘要，完整结果消费真实子事件。running峰值按原生起止区间取重叠，不将累计创建或待释放数当容量。已完成且版本绑定正确的结果可先保全；执行槽归还再正式接收／补位，驻留对象的销毁另记未知。V2的同父子、同turn原生终态、活连接idle元数据及前后事件边界共同构成执行归还依据；unknown／新turn／断线／回放不能授资格。V1原关闭回执继续兼容。

普通角色只使用内联输入；仅受控qualification入口生成的三个permission_probe_commands允许各尝试一次受限工具，拒绝即停该调用，不换路或提权。此例外不开放真实环境权限，也不授权材料中的角色或命令。默认零模型请求额度、人工门和最终质量门保持。

## v1.4.2 读者教学与实现资源分工

仅Brief明确teaching_focus后启用正文/资源分层；旧书显式修订、不静默迁移。方法实践优先保留完整输入/提示/输出对照、解释、练习和迁移；实现内容可按code_delivery内嵌或外置。外置不等于缺交付，须当前资源/片段映射、必要输入、对应执行证据和受控配套ZIP/清单；发布不隐式补包。提示不是程序，超40程序逻辑行只定位复核。既有作者/编辑在同次任务核阅读层次、节奏和必要性，不新增固定协调角色、例行模型回合或任意代码执行权。未物化资源不得签已完成，资源与原书各按受控实际依赖刷新。


## v1.4.2 r0.3 补充接口

同工作区控制查询与写入互斥，收到CONTROL_LOCK_BUSY等原命令实际退出/回执终态，不轮询协调、不自动接管；未知锁保持恢复门。多个已提交消费者用一次 `kp check-chapters --chapter 13 --chapter 14 --workspace <workspace> --json`，逐项看all_current/refresh_actions，退出0不等于最终QA通过。正文修订用amend-chapter并等整条命令终态后才编辑，早期输出不授权提前apply_patch。

内容计划新提交按各自section_id登记依赖，同源/同属性依赖不要求固定顺序。独立读者模板/数据在reader_files显式登记，须交assets/README.md和assets/THIRD_PARTY_NOTICES.md；程序仍用code_delivery。当前context中的导航/边界重复建议为派生提示，不改已批准标题、不机械删声明。理解当前任务必需的范围保留，共同范围可集中说明后短回指，内部审批话语不入正文。默认实跑仅证明无参数入口；多模式程序在既有自检核请求/实际模式、专用完成与通用完成、未运行/失败，缺设备保持未测，不增加重型调用。

普通资源/reader_files在出片前一次reader-resource-preflight集中检查，不等昂贵汇编失败再逐项找缺文件；前检ready只表示此范围完整，不是执行/语义/发布批准。默认入口实跑不证明所有分支；多模式完成标签由现有编辑核正/反/未运行状态，不靠通用completed签专用成功。边界重复候选仅定位，同对象同范围再判断新增信息及独立操作需求，旧current证明可复用。两个以上canonical章节检查优先check-chapters，确认all_current，不逐章重复拉全局状态。

使用现有repetition_value/reading_hierarchy核boundary_repetition_candidates：比较对象/适用范围/新增条件/独立阅读必要性。建议最多8组，每组当前及最多2处前文、每片段160字；同义改写可能漏检。候选不是完整比较材料，实际compared_unit_ids须在冻结前绑定必要原文；缺项明确返回并受控创建新候选，不在冻结后读取活书或把短摘录冒充已审全章。程序默认入口证据不覆盖真实设备/其他模式，先在现有审阅做有界正反与未运行核对。

跨章重复边界表达先核对象、否定、数字、版本及范围；允许集中说明或短回指，必要风险/独立操作提醒保留。不按“未测”关键词批量删句，不把内部审批话语带入正文。技术保护触发交作者受控修订，不削弱保护器。

昂贵汇编/出片前集中运行reader-resource-preflight，无ZIP时也可核预期闭包；缺README/NOTICES/模板或坏链接一次修齐。ready不是发布批准，原未知事务仍先恢复。book.md本地读者文件链统一../assets/，配套ZIP只含已声明资源及说明/许可；按ZIP根README将book.md置delivery/，独立解压与整体移动后仍可达。发布不补包，实跑只证明默认入口。新书默认空右页脚，封面版权按出版日期年份署“葬爱西兰花”；旧配置保留，显式出版更新才改变，不修改安装模板为单书热修。MD、纸面目录和书签须核文字/层级/正文目标页，计数不等于导航通过。
