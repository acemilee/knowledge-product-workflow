"""Two-copy technical protection is not a semantic-equivalence certificate."""
import json
from hashlib import sha256

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.humanize import (
    HumanizeReportError,
    compare_protected_parts,
    protected_parts,
    record_humanize_check,
    require_current_humanize_report,
    scan_chinese_style,
)
from knowledge_product_workflow.quality import Severity
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from tests.test_structure_review_v133 import _fixture


@pytest.mark.parametrize("before,after", [
    ("参数`timeout=8`。", "参数`timeout=80`。"),
    ("使用``a`b``。", "使用``a`c``。"),
    ("~~~cpp\nint a = 2;\n~~~\n", "~~~cpp\nint a = 3;\n~~~\n"),
    ("````md\n```x\n````\n", "````md\n```y\n````\n"),
    ("[原文](https://example.com/a)", "[原文](https://example.com/b)"),
    ("| 输入 | -8.7 秒 |\n", "| 输入 | 8.7 秒 |\n"),
    ("# 标题 {#task}\n", "# 标题 {#other}\n"),
    ("::: {.kp-note}\n条件说明。\n:::\n", "条件说明。\n"),
    ("$x=8$", "$x=80$"),
    ("---\ntitle: a\n---\n正文", "---\ntitle: b\n---\n正文"),
    ("1. 先检查。\n2. 再提交。", "2. 先检查。\n1. 再提交。"),
    ("| 超时8秒 |\n", "| 超时80秒 |\n"),
    ("    timeout=8\n", "    timeout=80\n"),
    ("`timeout=\n8`", "`timeout=\n80`"),
    ("保存到 docs/a.md", "保存到 pages/a.md"),
    ("- 执行：\n\n    ~~~cpp\n    int timeout = 8;\n    ~~~\n", "- 执行：\n\n    ~~~cpp\n    int timeout = 80;\n    ~~~\n"),
])
def test_changed_technical_parts_block(before, after):
    findings = compare_protected_parts(before, after, "source.md")
    assert findings and all(f.severity is Severity.BLOCKER for f in findings)


def test_table_step_and_link_wording_can_be_edited_without_freezing_all_prose():
    before = "[来源](https://example.com/a)\n| 描述 | 8 秒 |\n1. 先检查配置。\n2. 再提交结果。\n"
    after = "[参考](https://example.com/a)\n| 用途 | 8 秒 |\n1. 检查配置。\n2. 提交结果。\n"
    assert not compare_protected_parts(before, after, "source.md")
    assert protected_parts(before) == protected_parts(after)


def test_style_findings_are_advisory_and_code_is_not_scanned():
    prose = "这里「引用」与单—插入语。" + "不是删去条件，而是解释条件。" * 4
    assert scan_chinese_style(prose, "source.md")
    assert all(f.severity is Severity.WARNING for f in scan_chinese_style(prose, "source.md"))
    code = "`「值」—`\n``「值」—`x``\n~~~text\n「值」—\n~~~\n````text\n「值」—\n```\n````\n"
    assert not scan_chinese_style(code, "source.md")


@pytest.mark.parametrize("before,after", [
    ("解释输入/输出关系。", "解释输入与输出关系。"),
    ("见 https://example.com/a，随后解释。", "见 https://example.com/a，接着解释。"),
    ("::: {.kp-note}\n条件。\n:::\n", "::: {.kp-note}\n\n条件说明。\n\n:::\n"),
])
def test_protection_does_not_freeze_ordinary_prose_or_spacing(before, after):
    assert not compare_protected_parts(before, after, "source.md")


def test_indented_code_literal_is_not_a_component_and_list_prose_is_editable():
    assert not scan_chinese_style("    ::: {.kp-note}\n", "source.md")
    before = "1. 先检查。\n\n    当前条件决定下一步。\n"
    after = "1. 检查条件。\n\n    依据当前条件决定下一步。\n"
    assert not compare_protected_parts(before, after, "source.md")


@pytest.mark.parametrize("malformed", ["```python\nprint(1)\n", "::: {.kp-note}\n条件。\n", "条件。\n:::\n"])
def test_illegal_boundaries_are_hard_failures(malformed):
    assert any(f.severity is Severity.BLOCKER for f in scan_chinese_style(malformed, "source.md"))


def test_cli_warnings_pass_hard_changes_fail_and_current_report_reuses(tmp_path):
    workspace, source, body = _fixture(tmp_path)
    source.write_text(body + "\n参数`timeout=8`，这里「引用」。\n", encoding="utf-8")
    before = workspace.root / "runs/before.md"
    before.write_bytes(source.read_bytes())
    args = ["humanize-check", "1", "--workspace", str(workspace.root), "--from", str(source), "--before", str(before)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "阻断项=0" in result.output and "警告项=" in result.output
    report_path, _ = record_humanize_check(workspace, 1, source, before_path=before)
    report_bytes = report_path.read_bytes()
    events = (workspace.root / "runs/events.jsonl").read_bytes()
    assert CliRunner().invoke(app, args).exit_code == 0
    assert report_path.read_bytes() == report_bytes
    assert (workspace.root / "runs/events.jsonl").read_bytes() == events
    source.write_text(source.read_text(encoding="utf-8").replace("timeout=8", "timeout=80"), encoding="utf-8")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1 and "阻断项=1" in result.output
    contract, _ = require_current_chapter_contract(workspace, 1)
    with pytest.raises(HumanizeReportError, match="阻断"):
        require_current_humanize_report(workspace, 1, source.read_text(encoding="utf-8"), contract)


@pytest.mark.parametrize("change", ["rule-missing", "rule-old", "protected-digest"])
def test_old_or_missing_rule_and_tampered_protection_are_stale(tmp_path, change):
    workspace, source, body = _fixture(tmp_path)
    path, _ = record_humanize_check(workspace, 1, source)
    value = json.loads(path.read_text(encoding="utf-8"))
    if change == "rule-missing":
        value.pop("rule_set")
    elif change == "rule-old":
        value["rule_set"] = "kp-chinese-humanize-editor-v1"
    else:
        value["protected_fragments_sha256"] = sha256(b"wrong").hexdigest()
    path.write_text(json.dumps(value), encoding="utf-8")
    contract, _ = require_current_chapter_contract(workspace, 1)
    with pytest.raises(HumanizeReportError, match="过期"):
        require_current_humanize_report(workspace, 1, body, contract)
    record_humanize_check(workspace, 1, source)
    assert require_current_humanize_report(workspace, 1, body, contract)


def test_before_and_source_must_belong_to_current_book(tmp_path):
    workspace, source, _ = _fixture(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("外部文件", encoding="utf-8")
    for current, before in ((source, outside), (outside, None), (source, workspace.root / "runs/missing.md")):
        with pytest.raises((HumanizeReportError, FileNotFoundError)):
            record_humanize_check(workspace, 1, current, before_path=before)
