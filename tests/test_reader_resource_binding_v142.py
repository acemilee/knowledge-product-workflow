"""精确片段、引用容器与资源边界的独立单元检查；不执行代码。"""
import pytest

from knowledge_product_workflow.content_plan import ContentPlanInput
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.reader_artifacts import (
    ReaderResourceError,
    resolve_reader_resources,
)
from knowledge_product_workflow.workflows.product_brief import (
    BriefSemanticFields,
    create_product_brief,
)
from knowledge_product_workflow.workspace import Workspace


@pytest.fixture
def resource_case(tmp_path, monkeypatch):
    workspace = Workspace.create(tmp_path, "guide")
    create_product_brief(workspace, "方法课", DetailConstraints(), target_audience="初学者",
                         semantics=BriefSemanticFields(teaching_focus="method_practice"))
    source = workspace.root / "assets/examples/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("def answer(rows):\n    return [r for r in rows if r['current']]\n", encoding="utf-8")
    plan = ContentPlanInput(sections=[{"section_id": "1.1", "reader_job": "读懂现行资料筛选，再迁移到新资料。",
        "opening_mode": "procedure", "block_sequence": ["example"], "artifact": "资料过滤程序",
        "artifact_kind": "code", "asset_decision": "none", "asset_rationale": "只展示核心筛选。",
        "code_delivery": {"mode": "excerpt_with_resource", "resource_path": "assets/examples/main.py",
            "reason": "完整运行器在资源，正文只讲过滤条件。", "excerpts": [
                {"body_fence_index": 2, "start_line": 2, "end_line": 2}]}}])
    monkeypatch.setattr("knowledge_product_workflow.reader_artifacts.require_current_content_plan", lambda *_: plan)
    body = "## 1.1 过滤现行资料\n\n```text\n## 9.9 这是提示内容，不是真小节\n```\n\n完整程序：assets/examples/main.py\n\n```python\n    return [r for r in rows if r['current']]\n```\n"
    return workspace, source, body


def test_exact_nonstandalone_excerpt_ignores_headings_inside_prompt(resource_case):
    workspace, _, body = resource_case
    bound = resolve_reader_resources(workspace, body, chapter_number=1)
    assert len(bound) == 1 and len(bound[0]["excerpt_spans"]) == 1


def test_same_excerpt_in_reader_book_and_quote_container(resource_case):
    workspace, _, body = resource_case
    body = body.replace("```python\n    return", "> ```python\n>     return").replace("r['current']]\n```", "r['current']]\n> ```")
    assert len(resolve_reader_resources(workspace, "# 1. 方法课\n\n" + body)) == 1


def test_changed_resource_does_not_silently_rebind_old_excerpt(resource_case):
    workspace, source, body = resource_case
    source.write_text("def answer(rows):\n    return []\n", encoding="utf-8")
    with pytest.raises(ReaderResourceError, match="不一致"):
        resolve_reader_resources(workspace, body, chapter_number=1)


def test_missing_resource_and_missing_reader_entry_fail(resource_case):
    workspace, source, body = resource_case
    with pytest.raises(ReaderResourceError, match="路径"):
        resolve_reader_resources(workspace, body.replace("assets/examples/main.py", "隐藏了文件"), chapter_number=1)
    source.unlink()
    with pytest.raises(ReaderResourceError, match="缺失"):
        resolve_reader_resources(workspace, body, chapter_number=1)


def test_excerpt_syntax_exception_is_identical_for_chapter_and_reader_book(resource_case):
    from knowledge_product_workflow.code_quality import validate_code_blocks
    workspace, _, body = resource_case
    assert not validate_code_blocks(body, "chapters/chapter-01.md", workspace=workspace)
    assert not validate_code_blocks("# 1. 方法课\n\n" + body, "delivery/book.md", workspace=workspace)
    assert any(f.code == "PYTHON_CODE_SYNTAX_INVALID" for f in validate_code_blocks(body, "unbound.md"))
    for location, text in [("chapters/chapter-01.md", body), ("delivery/book.md", "# 1. 方法课\n\n" + body)]:
        bad = text.replace("if r['current']", "if not r['current']")
        assert any(f.code == "READER_RESOURCE_INVALID" for f in validate_code_blocks(bad, location, workspace=workspace))


def test_actual_chapter_and_delivery_validation_calls_both_use_resource_mapping(resource_case):
    from knowledge_product_workflow.chapter_compliance import validate_chapter_reader_content
    from knowledge_product_workflow.workflows.validation import validate_reader_content
    workspace, _, body = resource_case
    path = workspace.root / "chapters/chapter-01.md"
    book = workspace.root / "delivery/book.md"
    book.write_text("# 1. 方法课\n\n" + body, encoding="utf-8")
    codes = {"READER_RESOURCE_INVALID", "PYTHON_CODE_SYNTAX_INVALID"}
    assert not any(f.code in codes for f in validate_chapter_reader_content(workspace, path, body))
    assert not any(f.code in codes for f in validate_reader_content(workspace, include_delivery_book=True))
    bad = body.replace("if r['current']", "if not r['current']")
    book.write_text("# 1. 方法课\n\n" + bad, encoding="utf-8")
    assert any(f.code == "READER_RESOURCE_INVALID" for f in validate_chapter_reader_content(workspace, path, bad))
    assert any(f.code == "READER_RESOURCE_INVALID" for f in validate_reader_content(workspace, include_delivery_book=True))


def test_other_language_resources_can_be_read_without_claiming_python_execution(resource_case, monkeypatch):
    from knowledge_product_workflow import reader_artifacts
    workspace, source, body = resource_case
    plan = reader_artifacts.require_current_content_plan(workspace, 1)
    plan.sections[0].code_delivery.resource_path = "assets/examples/main.js"
    source.with_suffix(".js").write_text("function answer(rows) {\n  return rows.filter(r => r.current);\n}\n", encoding="utf-8")
    body = body.replace("main.py", "main.js").replace("```python", "```javascript").replace(
        "    return [r for r in rows if r['current']]", "  return rows.filter(r => r.current);")
    bound = resolve_reader_resources(workspace, body, chapter_number=1)
    assert bound[0]["syntax_check"] == "not_verified" and not bound[0]["execution_verified"]
    from knowledge_product_workflow.example_checks import example_template
    source = workspace.root / "runs/source.md"
    source.write_text(body, encoding="utf-8")
    template = example_template(workspace, source)
    assert not template["resources"][0]["execution_supported"]
    assert template["resources"][0]["needs_execution"] is None
    assert "仅支持Python" in template["resources"][0]["unsupported_execution_action"]


def test_complete_resource_execution_proof_is_not_a_fragment_or_prose_proof(resource_case):
    import json

    from knowledge_product_workflow.example_checks import example_template, run_python_resource
    workspace, _, body = resource_case
    source = workspace.root / "runs/source.md"
    source.write_text(body, encoding="utf-8")
    template = example_template(workspace, source)
    assert template["examples"] == [] and template["resources"][0]["needs_execution"]
    with pytest.raises(ValueError, match="明确授权"):
        run_python_resource(workspace, source, "assets/examples/main.py")
    proof = run_python_resource(workspace, source, "assets/examples/main.py", approved=True)
    assert json.loads(proof.read_text(encoding="utf-8"))["status"] == "passed"
    assert example_template(workspace, source)["resources"][0]["binding_state"] == "current"
    source.write_text(body + "\n补充操作说明，完整程序未改。\n", encoding="utf-8")
    assert not example_template(workspace, source)["resources"][0]["needs_execution"]
    (proof.parent / "stdout.bin").write_bytes(b"forged output")
    assert example_template(workspace, source)["resources"][0]["needs_execution"]


def test_bundle_binds_declared_closure_and_refuses_missing_old_or_replaced_zip(resource_case):
    import io
    import zipfile

    from knowledge_product_workflow.example_checks import run_python_resource
    from knowledge_product_workflow.publishing import (
        _reader_bundle_outputs,
        reader_resource_bundle_findings,
    )
    workspace, _, body = resource_case
    for name, text in [("README.md", "在examples目录运行main.py。"),
                       ("THIRD_PARTY_NOTICES.md", "本例为虚构教学资料，无第三方代码。")]:
        (workspace.root / "assets" / name).write_text(text, encoding="utf-8")
    source = workspace.root / "runs/source.md"
    source.write_text(body, encoding="utf-8")
    assert reader_resource_bundle_findings(workspace, body)

    for path, raw in _reader_bundle_outputs(workspace, body).items():
        path.write_bytes(raw)
    assert reader_resource_bundle_findings(workspace, body)  # 有包不等于实跑过。
    run_python_resource(workspace, source, "assets/examples/main.py", approved=True)
    assert not reader_resource_bundle_findings(workspace, body)
    archive_path = workspace.root / "delivery/practice-resources.zip"
    with zipfile.ZipFile(archive_path) as archive:
        original = {name: archive.read(name) for name in archive.namelist()}
    assert set(original) == {"README.md", "assets/examples/main.py", "assets/README.md", "assets/THIRD_PARTY_NOTICES.md"}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, raw in original.items():
            archive.writestr(name, b"print('old code')" if name.endswith("main.py") else raw)
    archive_path.write_bytes(stream.getvalue())
    assert reader_resource_bundle_findings(workspace, body)


def test_single_unit_review_does_not_require_or_hash_unrelated_resource_sections(resource_case):
    from knowledge_product_workflow import reader_artifacts
    workspace, _, body = resource_case
    plan = reader_artifacts.require_current_content_plan(workspace, 1)
    other = plan.sections[0].model_copy(deep=True)
    other.section_id = "1.2"
    other.code_delivery.resource_path = "assets/examples/not_read.py"
    plan.sections.append(other)
    assert len(resolve_reader_resources(workspace, body, chapter_number=1, partial_scope=True)) == 1
    with pytest.raises(ReaderResourceError, match="小节缺失"):
        resolve_reader_resources(workspace, body, chapter_number=1)


@pytest.mark.parametrize("mutation", ["preview", "copied_input", "source_input", "copied_program"])
def test_resource_proof_requires_raw_output_and_all_current_declared_inputs(resource_case, mutation):
    import json
    import os

    from knowledge_product_workflow import reader_artifacts
    from knowledge_product_workflow.example_checks import example_template, run_python_resource
    workspace, _, body = resource_case
    plan = reader_artifacts.require_current_content_plan(workspace, 1)
    relative = "assets/examples/data.json"
    plan.sections[0].code_delivery.input_files = [relative]
    data_path = workspace.root / relative
    data_path.write_text('{"current":true}\n', encoding="utf-8")
    source = workspace.root / "runs/source.md"
    source.write_text(body, encoding="utf-8")
    proof = run_python_resource(workspace, source, "assets/examples/main.py", approved=True)
    assert not example_template(workspace, source)["resources"][0]["needs_execution"]
    if mutation == "preview":
        data = json.loads(proof.read_text(encoding="utf-8"))
        data["stdout"] = "伪造的运行输出，不对应stdout.bin"
        proof.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    else:
        path = (data_path if mutation == "source_input" else proof.parent / relative
                if mutation == "copied_input" else proof.parent / "assets/examples/main.py")
        info = path.stat()
        path.write_bytes(path.read_bytes().replace(b"true", b"null") if path.suffix == ".json" else b"pass\n")
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert example_template(workspace, source)["resources"][0]["needs_execution"]


@pytest.mark.parametrize("extra", ["../escaped.txt", "/absolute.txt", "undeclared.txt", "assets/examples/main.py"])
def test_bundle_refuses_extra_escaped_absolute_and_duplicate_members(resource_case, extra):
    import zipfile

    from knowledge_product_workflow.example_checks import run_python_resource
    from knowledge_product_workflow.publishing import (
        _reader_bundle_outputs,
        reader_resource_bundle_findings,
    )
    workspace, _, body = resource_case
    for name in ("README.md", "THIRD_PARTY_NOTICES.md"):
        (workspace.root / "assets" / name).write_text("真实测试说明，无第三方材料。", encoding="utf-8")
    source = workspace.root / "runs/source.md"
    source.write_text(body, encoding="utf-8")
    run_python_resource(workspace, source, "assets/examples/main.py", approved=True)
    for path, data in _reader_bundle_outputs(workspace, body).items():
        path.write_bytes(data)
    assert not reader_resource_bundle_findings(workspace, body)
    with zipfile.ZipFile(workspace.root / "delivery/practice-resources.zip", "a") as archive:
        if extra == "assets/examples/main.py":
            with pytest.warns(UserWarning, match="Duplicate"):
                archive.writestr(extra, "extra")
        else:
            archive.writestr(extra, "extra")
    assert reader_resource_bundle_findings(workspace, body)


def test_asset_junction_cannot_read_another_workspace(resource_case):
    from knowledge_product_workflow import reader_artifacts
    from tests.test_verification_context_v140 import _directory_link
    workspace, _, body = resource_case
    outside = workspace.root.parent / "other-owned-test-workspace"
    outside.mkdir()
    (outside / "main.py").write_text("def answer(rows):\n    return [r for r in rows if r['current']]\n", encoding="utf-8")
    link = workspace.root / "assets/linked"
    _directory_link(link, outside)
    plan = reader_artifacts.require_current_content_plan(workspace, 1)
    plan.sections[0].code_delivery.resource_path = "assets/linked/main.py"
    with pytest.raises(ReaderResourceError, match="越出"):
        resolve_reader_resources(workspace, body.replace("assets/examples/main.py", "assets/linked/main.py"), chapter_number=1)


def test_unsupported_resource_execution_stays_unknown_and_cannot_publish_as_verified(resource_case):
    from knowledge_product_workflow import reader_artifacts
    from knowledge_product_workflow.publishing import (
        _reader_bundle_outputs,
        reader_resource_bundle_findings,
    )
    workspace, source, body = resource_case
    plan = reader_artifacts.require_current_content_plan(workspace, 1)
    plan.sections[0].code_delivery.resource_path = "assets/examples/main.js"
    source.with_suffix(".js").write_text("function answer(rows) {\n  return rows.filter(r => r.current);\n}\n", encoding="utf-8")
    body = body.replace("main.py", "main.js").replace("```python", "```javascript").replace(
        "    return [r for r in rows if r['current']]", "  return rows.filter(r => r.current);")
    for name in ("README.md", "THIRD_PARTY_NOTICES.md"):
        (workspace.root / "assets" / name).write_text("自有资源，未提供非Python执行证明。", encoding="utf-8")
    for path, data in _reader_bundle_outputs(workspace, body).items():
        path.write_bytes(data)
    findings = reader_resource_bundle_findings(workspace, body)
    assert findings and "实跑" in findings[0].message
