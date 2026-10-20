"""受控章节到Markdown/资源包交付；人工批准/语义审核仅为明确软件夹具。"""

import json
from pathlib import Path

import pytest
import yaml

from knowledge_product_workflow.chapter_compliance import submit_chapter
from knowledge_product_workflow.content_plan import submit_content_plan
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.developmental import record_developmental_check
from knowledge_product_workflow.example_checks import run_python_resource
from knowledge_product_workflow.humanize import record_humanize_check
from knowledge_product_workflow.publishing import assemble_book, create_delivery_manifest
from knowledge_product_workflow.workflows.authoring import (
    compile_chapter_contracts,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.product_brief import (
    BriefSemanticFields,
    create_product_brief,
)
from knowledge_product_workflow.workflows.validation import run_delivery_validation
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import prepare_test_chapter_inputs, submit_test_blueprint
from tests.test_publication_updates import _approve
from tests.test_structure_review_v133 import simulated_review


def _resource_book(tmp_path: Path):
    w = Workspace.create(tmp_path, "resource-delivery")
    brief = create_product_brief(w, "资料判断方法", DetailConstraints(delivery_format="markdown"),
        target_audience="需要学习资料判断的工程师", semantics=BriefSemanticFields(teaching_focus="method_practice"))
    _approve(brief)
    outline = submit_test_blueprint(w, brief, titles=("基础判断",))
    _approve(outline)
    compile_chapter_contracts(w)
    create_chapter_draft(w, 1)
    program = w.root / "assets/examples/main.py"
    program.parent.mkdir(parents=True)
    program.write_text('def pick(rows):\n    return [r for r in rows if r["state"] == "current"]\n\n'
                       'if __name__ == "__main__":\n    print(pick([{"state":"current"}]))\n', encoding="utf-8")
    (w.root / "assets/README.md").write_text("进入assets/examples，使用Python运行main.py。", encoding="utf-8")
    (w.root / "assets/THIRD_PARTY_NOTICES.md").write_text("自有虚构示例，无第三方程序。", encoding="utf-8")
    plan = {"sections": [{"section_id": "1.1", "reader_job": "按资料状态作取舍，保留不确定项。",
        "opening_mode": "decision", "block_sequence": ["decision", "example"], "artifact": "资料筛选例子",
        "artifact_kind": "code", "asset_decision": "none", "asset_rationale": "短片段解释判断条件。",
        "code_delivery": {"mode": "excerpt_with_resource", "resource_path": "assets/examples/main.py",
            "reason": "正文解释资料判断，完整运行器供复核。", "excerpts": [
                {"body_fence_index": 1, "start_line": 2, "end_line": 2}]}}]}
    plan_path = w.root / "runs/plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan, allow_unicode=True), encoding="utf-8")
    submit_content_plan(w, 1, plan_path)
    source, mapping = prepare_test_chapter_inputs(w, tmp_path, 1)
    with source.open("a", encoding="utf-8") as handle:
        handle.write('\n以下片段只说明过滤条件；完整例子：[资料筛选程序](../assets/examples/main.py)。\n\n'
                     '```python\n    return [r for r in rows if r["state"] == "current"]\n```\n')
    execution = run_python_resource(w, source, "assets/examples/main.py", approved=True)
    simulated_review(w, source)
    _, report = record_developmental_check(w, 1, source)
    assert report is not None and not [f for f in report.findings if f.severity.value == "blocker"]
    record_humanize_check(w, 1, source)
    submit_chapter(w, 1, source, mapping)
    (w.root / "chapters/chapter-01-sample.approved").write_text("approved_by: 用户\n", encoding="utf-8")
    book = assemble_book(w)
    return w, book, execution


def test_complete_resource_book_creates_current_qa_and_bound_bundle_manifest(tmp_path):
    w, book, execution = _resource_book(tmp_path)
    _, qa = run_delivery_validation(w)
    assert qa.publishable, [(f.code, f.message) for f in qa.findings]
    manifest = create_delivery_manifest(w, book, None)
    artifacts = {item["path"] for item in yaml.safe_load(manifest.read_text(encoding="utf-8"))["artifacts"]}
    assert {"delivery/book.md", "delivery/practice-resources.zip", "delivery/practice-resources-manifest.json"} <= artifacts
    inputs = json.loads((w.root / "delivery/book-input-manifest.json").read_text(encoding="utf-8"))
    assert {"assets/examples/main.py", "delivery/practice-resources.zip"} <= {item["path"] for item in inputs["inputs"]}
    evidence = json.loads(execution.read_text(encoding="utf-8"))
    assert evidence["status"] == "passed" and "current" in evidence["stdout"]
    assert 'return [r for r in rows' in book.read_text(encoding="utf-8")


def test_bundle_change_after_qa_cannot_be_published_or_silently_rebuilt(tmp_path):
    w, book, _ = _resource_book(tmp_path)
    _, qa = run_delivery_validation(w)
    assert qa.publishable
    archive = w.root / "delivery/practice-resources.zip"
    archive.write_bytes(b"replaced after QA")
    with pytest.raises(ValueError, match="过期|资源|ZIP|当前"):
        create_delivery_manifest(w, book, None)
    assert archive.read_bytes() == b"replaced after QA"
    assert not (w.root / "delivery/delivery-manifest.yaml").exists()
