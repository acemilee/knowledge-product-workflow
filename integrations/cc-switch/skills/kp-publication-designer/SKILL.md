---
name: kp-publication-designer
description: 为知识产品生成受控出版设计预览，并将已批准的设计 token 交给固定 PDF 渲染链路。
user-invocable: false
metadata:
  version: "1.2.0"
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
3. 不提交原始颜色、LaTeX、CSS、HTML、路径或模板片段。若需要颜色或排版参数，只从 CLI 的受控枚举/token 选择。
4. 根 Skill 将设计预览与 Brief 一次展示；用户批准 Brief 即同时批准当前设计。用户要求仅修改出版设计时，走受控出版更新，不重做蓝图、章节、来源或审稿。
5. 每本书默认只生成一次设计预览。输入版本未变化时复用结果，不重复调用宿主模型；模式、预算和缓存由 token 成本控制面记录。

## 禁止事项

- 不直接编辑 `publication-profile.yaml`、`book.qmd`、TeX 模板、Lua 过滤器、Markdown 正文或交付清单。
- 不把统一紫色、报告风、卡片堆叠或外部 PDF 样式当作默认设计答案。
- 不新增用户审批点，不宣称自动设计已替代用户的品牌、审美或商业判断。

## 确定性验收命令

`kp update-publication --workspace <workspace> --from <workspace>/runs/publication-update.yaml` 必须生成可审阅预览；确认后 `kp validate-delivery --workspace <workspace>` 必须绑定当前出版元数据与样式资产。

## 下游交接条件

将已批准的设计 token 交给 `kp-publisher` 受控渲染；章节作者只使用内容块语义，不直接选择 TeX 视觉实现。
