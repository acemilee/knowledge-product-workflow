"""命名建议不是词性硬门；蓝图/正文共用同一确定性候选定位。"""
from knowledge_product_workflow import content_quality
from knowledge_product_workflow.workflows.curriculum import curriculum_outline_schema


def test_schema_discloses_topic_heading_and_separate_reader_actions():
    guidance = curriculum_outline_schema()["heading_navigation"]
    assert "主题" in guidance["title_rule"]
    assert "reader_job" in guidance["action_rule"]
    assert guidance["automatic_semantic_approval"] is False


def test_task_sentence_and_long_labels_are_advisory_candidates():
    diagnostic = content_quality.heading_navigation_diagnostics
    rows = [{"number":"1", "level":1,"title":"先定义模型能力，再选择测评标准","parent_number":None},
            {"number":"1.1", "level":2,"title":"这是一个包含许多重复步骤而且无法直接指出中心主题的特别长标题文字","parent_number":"1"}]
    found = diagnostic(rows, language="zh-CN")
    assert {r["number"] for r in found} == {"1", "1.1"}
    assert all(r["severity"] == "warning" for r in found)
    assert rows[0]["title"] == "先定义模型能力，再选择测评标准"


def test_clear_topic_and_short_question_are_not_hard_rejections():
    diagnostic = content_quality.heading_navigation_diagnostics
    rows = [{"number":str(i),"level":1,"title":title,"parent_number":None}
            for i,title in enumerate(["LoRA领域微调","什么是AI Agent","工具调用与权限","Retrieval-Augmented Generation"],1)]
    assert not [r for r in diagnostic(rows, language="zh-CN") if r.get("severity") == "blocker"]


def test_code_payload_titles_do_not_trigger_navigation_scanner():
    text = "# 1. 分词与解码\n\n```text\n## 1.1 先做这个，再完成其他事情\n```\n"
    found = content_quality.scan_markdown_content(text, "chapter.md")
    assert not [r for r in found if r.code.startswith("HEADING_NAVIGATION")]
