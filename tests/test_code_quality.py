from knowledge_product_workflow.code_quality import validate_code_blocks


def test_python_code_block_syntax_is_checked_without_execution() -> None:
    findings = validate_code_blocks("```python\ndef run(:\n    pass\n```\n", "chapters/chapter-01.md")

    assert {finding.code for finding in findings} == {"PYTHON_CODE_SYNTAX_INVALID"}


def test_non_python_code_and_valid_python_are_not_executed_or_blocked() -> None:
    findings = validate_code_blocks(
        "```python\nimport os\nprint('safe syntax only')\n```\n\n```bash\nrm -rf ignored\n```\n",
        "chapters/chapter-01.md",
    )

    assert findings == []
