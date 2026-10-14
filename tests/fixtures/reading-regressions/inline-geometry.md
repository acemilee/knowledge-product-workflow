# 1. 行内标识与数据保真

## 1.1 短标识的端部留白

保存`task-notes`目录，查看`skills`、`/skills/`与`AGENTS.md`。下降部与下划线：`g_p_y`。

相邻标识`input.jsonl``rules.json`；括号（`name`）与逗号、句号：`description`。

## 1.2 行末与长数据

这段正文用来让路径处于当前行的剩余空间中，要求正文顺序不变、路径完整且不伸向右边缘：`notes-to-actions/SKILL.md`。

保留数据：`{"source_id":"S1","paragraph_id":"P1","text":"camera checkout requires approval"}`。

极长无自然断点：`ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ`。

公式继续使用原数学角色：$w_1x_1+w_2x_2$。

## 1.3 单元格开头的代码基线

| 标识 | 同行普通文字 | 输出用途 |
|---|---|---|
| `TASK_CELL` | PLAIN_CELL 同行的首行应与代码标识对齐，不因单元格内容换行产生虚假的首行空白 | 保留一段足够宽的说明，使表格使用真实出版物中的段落列，同时检查多行文本的首行视觉轴 |
