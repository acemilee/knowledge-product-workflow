"""独立读者文档的声明、可移植链接与无副作用前检。"""
import pytest

from knowledge_product_workflow import reader_artifacts
from knowledge_product_workflow.content_plan import ContentPlanInput, ContentPlanSection
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.workflows.product_brief import (
    BriefSemanticFields,
    create_product_brief,
)
from knowledge_product_workflow.workspace import Workspace


@pytest.fixture
def documents(tmp_path, monkeypatch):
    w = Workspace.create(tmp_path, "documents")
    create_product_brief(w, "资料使用", DetailConstraints(), target_audience="需要实践的读者",
                         semantics=BriefSemanticFields(teaching_focus="method_practice"))
    plan = ContentPlanInput(sections=[{"section_id":"1.1","reader_job":"复制模板并记录实际实验中的输入与输出",
        "opening_mode":"reference","block_sequence":["explanation"],"artifact":"none",
        "asset_decision":"none","asset_rationale":"用文件入口即可说明实践记录的用途",
        "reader_files":["assets/templates/实验 A.md"]}])
    monkeypatch.setattr("knowledge_product_workflow.reader_artifacts.require_current_content_plan", lambda *_: plan)
    (w.root/"assets/templates").mkdir(parents=True)
    (w.root/"assets/templates/实验 A.md").write_text("# 实验记录\n\n待填写的输入与结果。\n",encoding="utf-8")
    body = "# 1. 资料使用\n\n## 1.1 实验记录\n\n[模板](assets/templates/%E5%AE%9E%E9%AA%8C%20A.md#实验记录)\n"
    return w, body


def test_reader_files_are_optional_and_cannot_smuggle_programs():
    raw = {"section_id":"1.1","reader_job":"根据实际资料进行核对并记录判断理由",
        "opening_mode":"claim","block_sequence":["explanation"],"artifact":"none",
        "asset_decision":"none","asset_rationale":"文字说明即可"}
    assert "reader_files" not in ContentPlanSection.model_validate(raw).model_dump()
    with pytest.raises(ValueError):
        ContentPlanSection.model_validate({**raw,"reader_files":["assets/run.py"]})
    with pytest.raises(ValueError):
        ContentPlanSection.model_validate({**raw,"reader_files":["assets/../private.txt"]})


def test_preflight_reports_both_required_documents_once(documents):
    w, body = documents
    check = reader_artifacts.reader_resource_preflight(w, body)
    assert check["ready"] is False
    assert {r["path"] for r in check["findings"]} >= {"assets/README.md","assets/THIRD_PARTY_NOTICES.md"}
    assert not (w.root/"delivery/book.md").exists()


def test_documents_and_book_links_rebase_without_rewriting_code(documents):
    w, body = documents
    (w.root/"assets/README.md").write_text("[记录](templates/实验%20A.md#实验记录)\n",encoding="utf-8")
    (w.root/"assets/THIRD_PARTY_NOTICES.md").write_text("自编练习模板。",encoding="utf-8")
    body += "\n```text\n[x](assets/missing.md)\n```\n"
    check = reader_artifacts.reader_resource_preflight(w, body)
    assert check["ready"], check["findings"]
    text = reader_artifacts.rebase_reader_file_links(w, body)
    assert "../assets/templates/" in text
    assert "[x](assets/missing.md)" in text


def test_reference_links_and_unsafe_encoded_targets(documents):
    w, body = documents
    (w.root/"assets/README.md").write_text("完整记录模板。",encoding="utf-8")
    (w.root/"assets/THIRD_PARTY_NOTICES.md").write_text("自编。",encoding="utf-8")
    ref = "# 1. 资料使用\n\n## 1.1 实验记录\n\n[表单][entry]\n\n[entry]: <assets/templates/实验%20A.md>\n"
    assert reader_artifacts.reader_resource_preflight(w, ref)["ready"]
    assert "../assets/templates/" in reader_artifacts.rebase_reader_file_links(w, ref)
    bad = body + "\n[逃逸](assets/%2e%2e/private.txt)\n"
    assert not reader_artifacts.reader_resource_preflight(w, bad)["ready"]


def test_actual_document_zip_is_portable_and_rejects_changed_closure(documents, tmp_path):
    import io
    import zipfile

    from knowledge_product_workflow.publishing import (
        _reader_bundle_outputs,
        reader_resource_bundle_findings,
    )
    w, body = documents
    (w.root/"assets/README.md").write_text("[记录](templates/实验%20A.md#实验记录)\n", encoding="utf-8")
    (w.root/"assets/THIRD_PARTY_NOTICES.md").write_text("自编记录模板，无第三方素材。", encoding="utf-8")
    body = reader_artifacts.rebase_reader_file_links(w, body)
    outputs = _reader_bundle_outputs(w, body)
    for p, data in outputs.items():
        p.write_bytes(data)
    assert not reader_resource_bundle_findings(w, body)
    for destination in (tmp_path/"delivery-only", tmp_path/"moved-delivery"):
        (destination/"delivery").mkdir(parents=True)
        (destination/"delivery/book.md").write_text(body, encoding="utf-8")
        with zipfile.ZipFile(io.BytesIO(outputs[w.root/"delivery/practice-resources.zip"])) as archive:
            archive.extractall(destination)
        for source in [destination/"delivery/book.md", destination/"assets/README.md"]:
            for link in reader_artifacts.markdown_file_links(source.read_text(encoding="utf-8")):
                from urllib.parse import unquote
                target = source.parent/unquote(link["target"].split("#")[0])
                assert target.resolve().is_relative_to(destination.resolve())
                assert target.is_file()
        assert not (destination/"chapters").exists()
    (w.root/"assets/templates/实验 A.md").write_text("变更的读者模板", encoding="utf-8")
    assert reader_resource_bundle_findings(w, body)


@pytest.mark.parametrize("target", ["assets/%252e%252e/private.txt", "C:/secret.txt", "//host/share", "assets/../x.txt", "assets/templates/实验%20A.md#不存在"])
def test_preflight_never_accepts_unsafe_or_missing_anchors(documents, target):
    w, body = documents
    (w.root/"assets/README.md").write_text("模板入口", encoding="utf-8")
    (w.root/"assets/THIRD_PARTY_NOTICES.md").write_text("自编。", encoding="utf-8")
    assert not reader_artifacts.reader_resource_preflight(w, body+f"\n[x](<{target}>)\n")["ready"]


def test_escaped_parentheses_and_inline_code_are_not_confused(documents):
    w, body = documents
    p = w.root/"assets/templates/实验 A.md"
    new = p.with_name("实验(A).md")
    new.write_bytes(p.read_bytes())
    plan = reader_artifacts.require_current_content_plan(w, 1)
    plan.sections[0].reader_files.append("assets/templates/实验(A).md")
    (w.root/"assets/README.md").write_text("模板入口", encoding="utf-8")
    (w.root/"assets/THIRD_PARTY_NOTICES.md").write_text("自编。", encoding="utf-8")
    text = body + "\n[x](assets/templates/实验\\(A\\).md)\n`[fake](assets/missing.md)`\n"
    assert reader_artifacts.reader_resource_preflight(w, text)["ready"]


def test_unadopted_legacy_book_is_not_silently_migrated(tmp_path):
    w = Workspace.create(tmp_path, "legacy-links")
    create_product_brief(w, "旧书", DetailConstraints())
    body = "[旧入口](notes/legacy.md)\n"
    result = reader_artifacts.reader_resource_preflight(w, body)
    assert result["ready"] and result["checked_files"] == [] and "not_adopted" in result["legacy_policy"]
    assert reader_artifacts.rebase_reader_file_links(w, body) == body


@pytest.mark.parametrize("payload", [
    "sections:\n- section_id: '1.1'\n  reader_files: [assets/template.txt]\n",
    "sections: {reader_files: [assets/template.txt]}\n",
])
def test_unadopted_new_declaration_is_rejected_instead_of_silently_omitted(tmp_path, monkeypatch, payload):
    w = Workspace.create(tmp_path, "unadopted-new-files")
    create_product_brief(w, "待确认的资料用途", DetailConstraints())
    (w.root/"chapters/chapter-01-content-plan.yaml").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(reader_artifacts, "require_current_content_plan", lambda *_: (_ for _ in ()).throw(AssertionError("廉价声明诊断不应深扫证明")))
    result = reader_artifacts.reader_resource_preflight(w, "# 1. 资料\n\n## 1.1 入口\n")
    assert not result["ready"]
    assert any(term in result["findings"][0]["message"] for term in ["teaching_focus", "声明结构"])


def test_unclosed_local_destination_does_not_count_as_a_verified_link(documents):
    w, body = documents
    (w.root/"assets/README.md").write_text("记录模板入口", encoding="utf-8")
    (w.root/"assets/THIRD_PARTY_NOTICES.md").write_text("自编模板。", encoding="utf-8")
    assert not reader_artifacts.reader_resource_preflight(w, body.rstrip()[:-1])["ready"]


def test_invalid_text_encoding_does_not_hide_other_independent_missing_files(documents):
    w, body = documents
    plan = reader_artifacts.require_current_content_plan(w, 1)
    plan.sections[0].reader_files.append("assets/bad.txt")
    (w.root/"assets/bad.txt").write_bytes(bytes([255]))
    result = reader_artifacts.reader_resource_preflight(w, body)
    assert not result["ready"]
    assert {item["path"] for item in result["findings"]} >= {
        "assets/bad.txt", "assets/README.md", "assets/THIRD_PARTY_NOTICES.md"}
