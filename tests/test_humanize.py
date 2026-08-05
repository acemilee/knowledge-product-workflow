from pathlib import Path

from knowledge_product_workflow.humanize import protected_fragments_sha256, scan_chinese_style


def test_chinese_humanize_rules_block_decorative_ai_style_but_ignore_code(tmp_path: Path) -> None:
    text = """# 标题

这是「直角引号」和 **·** 装饰。这里使用单—破折号。

```text
「代码内引号」
单—破折号
```
"""

    codes = {finding.code for finding in scan_chinese_style(text, "chapter.md")}

    assert {
        "CHINESE_STYLE_CORNER_QUOTES",
        "CHINESE_STYLE_DECORATIVE_MIDDOT",
        "CHINESE_STYLE_SINGLE_DASH",
    } <= codes


def test_protected_fragment_hash_covers_code_links_and_table_numbers() -> None:
    original = """[来源](https://example.com/a)

| 指标 | 数值 |
| --- | --- |
| 延迟 | 8.7 秒 |

```python
print("stable")
```
"""
    changed = original.replace("8.7", "9.2")

    assert protected_fragments_sha256(original) != protected_fragments_sha256(changed)
