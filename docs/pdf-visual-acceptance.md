# PDF 视觉验收

KP 的 PDF 使用固定 `knowledge-product-pdf-v4` 样式，而不是由宿主模型自由设计。读者交付只包含 `delivery/book.md` 和按 Brief 要求生成的 `delivery/book.pdf`；QMD、TeX、过滤器、台账、审稿记录和运行日志均为内部工件。

## 固定视觉系统

- 封面展示主题、副标题、目标受众、版本、冻结发布日期、封面标签和版权行；页脚标签单独配置，不与封面标签绑定。
- 目录独立起页，显示“目录 / Table of Contents”，保留至多两级标题、页码和引导线。
- 标题、页码、页脚、来源卡片、提示框、表格和代码块都由固定模板渲染。
- A4 正文默认 12pt，也支持显式选择 14pt；使用 `extreport` 使字号真正生效。左右页边距 16mm、上下页边距 18mm，代码块按正文同步放大并强制长行折行。
- PDF 书签由 Markdown 标题生成；使用来源卡片时，PDF 必须保留可点击的 HTTPS 外部链接。
- 首次汇编创建 `planning/publication-profile.yaml`，默认封面标签和页脚标签为“知识产品工作流”、版权行为“版权所有 {年份} 知识产品工作流”、版本为 `v1.0.0`。每次渲染会从该配置派生 `delivery/publication-metadata.yaml`；配置更新必须经受控预览和一次确认，旧 PDF/manifest 会归档。

## 可自定义的出版信息

用户可以独立修改封面标签、页脚标签、版权行、副标题、发布日期和书稿版本。推荐直接向宿主提出自然语言请求，例如：

```text
把封面标签改成“星云学院”，把页脚改成“仅供内部学习”，书稿版本升级一个 patch；不修改课程和正文。
```

宿主必须先展示预览并等待一次确认，再调用受控出版更新。确认后仅重建规范 Markdown、Quarto 源稿、PDF 和交付清单；不得让用户重走 Brief、蓝图、样章、章节、来源或审稿流程。封面标签与页脚标签可以不同，用户也可以保持其中任一字段不变。禁止直接编辑 TeX、Lua、QMD、交付清单或 `publication-profile.yaml` 来改变视觉效果。

## 允许的正文组件

```markdown
::: {.kp-source}
来源：[机构或文档标题](https://example.com/documentation)
:::

::: {.kp-key-takeaway}
关键结论：用可验证的工件交接阶段结果。
:::

::: {.kp-practice}
练习：为自己的项目列出一个可验证的交付条件。
:::
```

裸 `{.kp-note}`、`{.kp-key-takeaway}` 等字符串不是合法组件，禁止写入章节。汇编后的 `book.md` 不保留 `.kp-*` 或 `:::`；只有 `book.qmd` 保留合法 fenced div 供 PDF 过滤器使用。

普通 Markdown 标题、表格和带语言标签的 fenced code block 均受支持。不要在读者书稿中写原始 TeX、HTML、来源台账、Claim ID、审稿批次、哈希或运行路径。

## 插图规则

- 将读者图片放在书稿工作区 `assets/`，正文使用相对路径，例如 `![流程图](assets/workflow.png)`。
- 仅允许 PDF、PNG、JPG/JPEG。SVG 可保留为创作源，但必须先转换为 PDF 或 PNG 后再进入正文。
- 禁止远程图片、`file:` URI、绝对路径、`..` 路径逃逸和不存在的资源。
- 插图必须服务于概念、流程、对比或案例讲解，不能仅用于填充页数。

## 发布前检查

1. 运行 `kp assemble`，确认生成 `book.md`、`book.qmd`、出版元数据和 v4 样式资产。
2. 运行 `kp render-pdf` 与 `kp validate-delivery`，确认 PDF 可读、页数达标、书签存在，且来源卡片对应 PDF 链接存在。渲染日志写入内部 `runs/pdf-render.log`；出现 `Overfull \\hbox` 或 `Overfull \\vbox` 时渲染会直接失败。
3. 人工查看封面、目录和正文样张：文字不重叠、表格与代码不越界、图片不截断、页码和页脚稳定、无异常空白页。
4. 运行 `kp publish` 后，只将清单中列出的最终 Markdown/PDF 交付给客户。
