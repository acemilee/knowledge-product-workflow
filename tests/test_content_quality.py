from knowledge_product_workflow.content_quality import scan_markdown_content
from knowledge_product_workflow.workflows.validation import run_validation
from knowledge_product_workflow.workspace import Workspace

_REPEATED_PARAGRAPH = (
    "候选人在回答系统设计题时，应先确认业务目标、用户规模、延迟约束和可靠性目标，"
    "再说明 Agent 编排、工具调用、检索质量、可观测性和降级策略之间的取舍，最后用具体指标验证方案。"
    "回答还应说明失败场景、监控告警、人工接管条件，以及如何根据线上反馈持续改进系统。"
)


def test_exact_and_near_duplicate_long_prose_are_blocked() -> None:
    exact = scan_markdown_content(
        f"{_REPEATED_PARAGRAPH}\n\n{_REPEATED_PARAGRAPH}", "chapters/chapter-03.md"
    )
    near = scan_markdown_content(
        f"{_REPEATED_PARAGRAPH}\n\n{_REPEATED_PARAGRAPH.replace('候选人', '面试者', 1)}",
        "chapters/chapter-03.md",
    )

    assert any(finding.code == "DUPLICATE_CONTENT_DETECTED" for finding in exact)
    assert any(finding.code == "DUPLICATE_CONTENT_DETECTED" for finding in near)


def test_duplicate_heading_template_and_trace_leak_are_blocked() -> None:
    markdown = """## 实战步骤

正文。

## 实战步骤

{{待替换内容}}

结论（claim-ch01-0001）请见 source-ledger.jsonl。
"""

    codes = {finding.code for finding in scan_markdown_content(markdown, "delivery/book.md")}

    assert {
        "DUPLICATE_HEADING_DETECTED",
        "UNRESOLVED_TEMPLATE_DETECTED",
        "INTERNAL_TRACE_LEAKED",
    } <= codes


def test_reader_facing_internal_headings_and_source_ledger_are_blocked() -> None:
    markdown = """## 正文

这里不应使用泛化的正文标题。

## 来源

来源台账：nist-ai-600-1
"""

    codes = {finding.code for finding in scan_markdown_content(markdown, "delivery/book.md")}

    assert "FORBIDDEN_READER_HEADING" in codes
    assert "INTERNAL_SOURCE_LEAKED" in codes


def test_inline_markdown_heading_is_blocked_but_normal_nist_explanation_is_allowed() -> None:
    malformed = scan_markdown_content(
        "先完成本节练习，再复盘答案。 ## 练习\n", "chapters/chapter-01.md"
    )
    normal_explanation = scan_markdown_content(
        "NIST AI 600-1 是一份讨论人工智能风险管理术语的公开文件。",
        "chapters/chapter-01.md",
    )

    assert any(finding.code == "MALFORMED_MARKDOWN_STRUCTURE" for finding in malformed)
    assert not any(finding.code == "INTERNAL_SOURCE_LEAKED" for finding in normal_explanation)


def test_same_subheading_in_different_chapters_is_not_a_duplicate() -> None:
    markdown = """# 第一章

## 学习目标

第一章内容。

# 第二章

## 学习目标

第二章内容。
"""

    assert not any(
        finding.code == "DUPLICATE_HEADING_DETECTED"
        for finding in scan_markdown_content(markdown, "delivery/book.md")
    )


def test_code_and_link_urls_do_not_trigger_content_markers() -> None:
    markdown = """[参考资料](https://example.com/claim-ch01-0001?todo=1)

```python
TODO = "claim-ch01-0001"
```
"""

    assert scan_markdown_content(markdown, "chapters/chapter-01.md") == []


def test_horizontal_rules_are_blocked_while_claim_binding_is_checked_in_workflow() -> None:
    markdown = """## 岗位判断

大多数公司会将系统设计分配为典型权重 40%。

---

真实 JD 显示所有候选人都要在 15-30 秒内完成回答。
"""

    codes = {finding.code for finding in scan_markdown_content(markdown, "delivery/book.md")}

    assert "HORIZONTAL_RULE_DETECTED" in codes
    assert "UNDECLARED_HIGH_RISK_CLAIM" not in codes


def test_explicit_case_label_is_excluded_from_high_risk_claim_detection() -> None:
    markdown = """## 模拟题

情境案例：以下是虚构的面试节奏示例，不代表真实 JD 或多数公司的流程。
"""

    assert not any(
        finding.code == "UNDECLARED_HIGH_RISK_CLAIM"
        for finding in scan_markdown_content(markdown, "chapters/chapter-01.md")
    )


def test_invalid_percentage_total_and_unbounded_resume_metrics_are_blocked() -> None:
    markdown = """## 复习时间分配

系统设计占比 54% + 项目复盘占比 30% + 模拟面试占比 20% = 100%。

## 简历示例

将“把检索准确率提升 35%”直接写入项目经历。
"""

    codes = {finding.code for finding in scan_markdown_content(markdown, "delivery/book.md")}

    assert {"PERCENTAGE_TOTAL_INVALID", "RESUME_EVIDENCE_BOUNDARY_MISSING"} <= codes


def test_verifiable_resume_boundary_allows_quantified_example() -> None:
    markdown = """## 简历示例

将“把检索准确率提升 35%”写入项目经历之前，必须确认该数字来自自己的真实、可核验经历；不得编造指标、规模或结果。
"""

    assert not any(
        finding.code == "RESUME_EVIDENCE_BOUNDARY_MISSING"
        for finding in scan_markdown_content(markdown, "chapters/chapter-01.md")
    )


def test_validation_aggregates_content_blockers(tmp_path) -> None:
    workspace = Workspace.create(tmp_path, "guide")
    (workspace.root / "chapters" / "chapter-01.md").write_text(
        "---\nbook_id: guide\nartifact_type: chapter\nstatus: in_production\nversion: 1\ndepends_on: []\napproved_by: null\n---\n"
        f"{_REPEATED_PARAGRAPH}\n\n{_REPEATED_PARAGRAPH}\n\n结论（claim-ch01-0001）",
        encoding="utf-8",
    )

    _, report = run_validation(workspace, set())

    assert {"DUPLICATE_CONTENT_DETECTED", "INTERNAL_TRACE_LEAKED"} <= {
        finding.code for finding in report.findings
    }
