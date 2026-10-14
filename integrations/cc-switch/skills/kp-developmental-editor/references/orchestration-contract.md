# KP v1.3.4 编排契约

## v1.3.4 修正0.8 中文编辑

作者租约内的中文编辑先保信息／确定程度，再满足当前合约与用户文体、作者声音，最后改风格。编辑Skill携带固定Humanizer-zh参考及MIT许可，不依赖全局安装。保留有用途的顺序、因果、转折、条件和回指，不逐句拆短或把长解释改成提纲；通顺段落允许不修改。实际编辑独占保留工作区内前稿，用humanize-check的--before对比技术保护；风格命中只是warning，扫描零阻断不是语义已验证。结构审核须核实际段落关系和信息保真。

## v1.3.4 O15 当前证明复用

章节提交前消费check-chapter的当前证明及refresh_actions。developmental_report/humanize_report当前有效时复用原证明，仅刷新缺失、过期或阻断的项；真实正文、合约和图义变化仍使旧证明失效。例子先消费example-check-template的binding_state/needs_execution/evidence_path：current表示代码payload及原脚本、stdout/stderr已核验，散文变化不要求再执行。source_sha256只记录原取证全文，不是当前代码过期判据。失败/timeout证明保留，不签passed；用户明确要求重跑仍需实际执行授权。所有复用决定仅属于后台，不写进读者正文。

这份契约供根编排 Skill 与 manifest 声明的全部专业 Skill 使用。它约束内部协作，不能展示给知识产品读者，也不能作为正文内容。各 Skill 包携带此文件的同哈希镜像，安装单个目录也必须能读取。

## 受控调用

1. 根编排 Skill 先运行 `kp status --json --workspace <workspace>`，只消费其中的 `macro_phase`、`user_summary`、`required_human_decision`、`user_decision`、`next_actions` 与 `current_work_order`。
2. 没有当前工单时，根编排 Skill 运行 `kp orchestration-plan --json --workspace <workspace>`；它会创建或复用 `runs/orchestration/work-orders/` 中版本绑定、带过期时间的工单。
3. 专业 Skill 开始前只读取根编排器分配的当前工单。工单规定唯一目标 Skill、允许读写范围、输入清单、章节目标、允许命令与完成条件。输入工件已变化、工单过期、目标章节不匹配或正文租约被占用时，立即停止，重新运行 `kp status --json`，不得自行修复、猜测或重派下游工作。
4. 一项受控动作开始前，CLI 冻结并校验唯一当前工单。章节 `draft` 只准备骨架并记录进展，保持本章工单与租约；最终 `submit-chapter` 才关闭作者工单并推进下一章。其他主动作成功后写输出哈希和事件凭证、创建后继；最终发布终结运行意图。任何 Skill 均不得手工完成工单或伪造证明。
5. 用户只看到宏阶段、可审阅工件、“继续/修改”决定、交付结果和可理解的阻断摘要；不得显示工单 ID、SHA、租约、任务令牌、Claim ID、来源台账、审批身份、内部命令或内部审稿 JSON。

## 用户决定

1. Brief、课程结构、样章和 Brief 修订均由根 Skill 在展示当前完整版本后发起一次内部 `user_decision` 请求。请求绑定目标工件路径、版本和 SHA-256。
2. 用户的自然语言“批准”“可以”“继续”“继续生成全书”或“按此执行”由根 Skill 解析为 `approve`；“修改：……”或具体调整解析为 `revise`。含义不明时只追问继续还是修改，绝不自动推进。
3. 根 Skill 在后台记录决定并调用对应受控转换。用户不运行 `request-user-decision`、`record-user-decision`、`approve`、`approve-sample` 或其他控制面命令。已有“继续直到交付”运行意图时，成功批准会自动恢复该意图并消费后继工单；修订与澄清不会恢复。
4. 目标工件、依赖或工作单输入变化时，旧决定必须失效。根 Skill 重新展示新版本并等待新决定；不得把审批身份、历史回复、用户沉默或确定性检查通过当成确认。
5. 旧工作区被用户确认采纳后，既有已批准 Brief、蓝图和样章标记可作为 `legacy_import` 审计记录导入；不得强迫用户重走历史审批。

## 正文所有权

- 同一章节同一时刻只有一个作者租约；`draft` 不释放它。样章批准后逐章完成，不要求先建齐全部草稿。内容计划、练习、图表与文字编辑在本章权限内协作，不能并发改同一正文。
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
- PDF 有效而预览缺失/过期时运行 `repair-pdf-preview`；PDF 损坏才重渲染。v6 依次执行 `audit-pdf-visual`、看清晰代表/异常页（具备看图能力时 `record-pdf-visual-observations`）、展示当前 PDF、获得用户明确判断、`record-pdf-visual-review`、`validate-delivery`、`publish`。报告与人工回执分别绑定版本，二者不能互相冒充；模型无看图能力时保留待人工项，用户直接检查当前 PDF 可完成主观复核，自动 blocker 仍须修复。
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

课程按读者要完成的判断/动作组织，而不是按名词堆章节。各节reader_job说明新增贡献；贯通案例再次出现必须增加条件、完成新操作或支持独立核对。每块选最短而完整的表达：共同维度用表，关系用图，依赖步骤用编号，并列用圆点，因果用连贯段落。不能把同一结论在故事、散文、图、表中依次复述；不能以段落长度/图数/列表比例代替实际质量。

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
