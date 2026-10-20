"""Reuse deterministic work while every public admission and write stays fresh."""

import json
import os

import pytest

from knowledge_product_workflow import chapter_compliance as compliance
from knowledge_product_workflow.quality import Severity
from knowledge_product_workflow.verification_context import (
    SnapshotChangedError,
    verification_scope,
    write_text,
)
from tests.test_deferred_repairs_v140 import serial_book


def prepared(tmp_path):
    workspace = serial_book(tmp_path, with_content_profile=True)
    path = workspace.root / "chapters/chapter-01.md"
    assert not any(f.severity is Severity.BLOCKER
                   for f in compliance.validate_current_chapter_compliance(workspace, path))
    return workspace, path


def test_same_input_reuses_calculation_with_independent_return_values(tmp_path, monkeypatch):
    workspace, path = prepared(tmp_path)
    original = compliance.validate_chapter_submission
    calls = []

    def observed(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(compliance, "validate_chapter_submission", observed)
    with verification_scope(workspace):
        first = compliance.validate_current_chapter_compliance(workspace, path)
        expected = list(first)
        first.clear()
        first.append(None)
        assert compliance.validate_current_chapter_compliance(workspace, path) == expected
        assert calls == [1]


def test_unrelated_owned_write_preserves_current_input_calculation(tmp_path, monkeypatch):
    workspace, path = prepared(tmp_path)
    original = compliance.validate_chapter_submission
    calls = []

    def observed(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(compliance, "validate_chapter_submission", observed)
    with verification_scope(workspace):
        compliance.validate_current_chapter_compliance(workspace, path)
        write_text(workspace.root / "runs/unrelated-controlled.txt", "phase", encoding="utf-8")
        compliance.validate_current_chapter_compliance(workspace, path)
        assert calls == [1]


def test_compliance_calculation_is_not_reused_across_public_actions(tmp_path, monkeypatch):
    workspace, path = prepared(tmp_path)
    original = compliance.validate_chapter_submission
    calls = []

    def observed(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(compliance, "validate_chapter_submission", observed)
    compliance.validate_current_chapter_compliance(workspace, path)
    compliance.validate_current_chapter_compliance(workspace, path)
    assert calls == [1, 1]


def test_new_public_transaction_admission_rejects_cached_compliance(tmp_path):
    workspace, path = prepared(tmp_path)
    journal = workspace.root / "runs/orchestration/transactions/tx-new-admission.json"
    with verification_scope(workspace):
        assert not any(f.severity is Severity.BLOCKER
                       for f in compliance.validate_current_chapter_compliance(workspace, path))
        write_text(journal, json.dumps({"operation_id": "tx-new-admission",
            "book_id": workspace.root.name, "kind": "chapter_submission",
            "scope_chapters": [1], "outputs": {}, "state": "prepared"}), encoding="utf-8")
        findings = compliance.validate_current_chapter_compliance(workspace, path)
        assert any(f.code == "CHAPTER_COMPLIANCE_STALE" and f.severity is Severity.BLOCKER
                   for f in findings)


def test_controlled_compliance_proof_change_is_not_reused(tmp_path):
    workspace, path = prepared(tmp_path)
    record_path = compliance.compliance_path(workspace, 1)
    with verification_scope(workspace):
        compliance.validate_current_chapter_compliance(workspace, path)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["chapter_body_sha256"] = "0" * 64
        write_text(record_path, json.dumps(record), encoding="utf-8")
        assert any(f.code == "CHAPTER_COMPLIANCE_STALE"
                   for f in compliance.validate_current_chapter_compliance(workspace, path))


def test_same_mtime_external_body_change_cannot_publish_cached_result(tmp_path):
    workspace, path = prepared(tmp_path)
    output = workspace.root / "runs/reuse-must-not-publish.txt"
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        compliance.validate_current_chapter_compliance(workspace, path)
        info, before = path.stat(), path.read_bytes()
        changed = before.replace(b"#", b"!", 1)
        assert changed != before
        path.write_bytes(changed)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        compliance.validate_current_chapter_compliance(workspace, path)
        write_text(output, "must reject", encoding="utf-8")
    assert not output.exists()


def test_interview_quota_observes_new_compliance_directory_member(tmp_path):
    workspace, _ = prepared(tmp_path)
    output = workspace.root / "runs/quota-must-not-publish.txt"
    directory = workspace.root / "runs/chapter-compliance"
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        compliance._existing_complete_answer_count(workspace, 1)
        (directory / "chapter-98.json").write_text("{}", encoding="utf-8")
        write_text(output, "must reject", encoding="utf-8")
    assert not output.exists()


@pytest.mark.parametrize("kind", ["refresh", "plan_history"])
def test_equivalence_fallback_observes_new_directory_member(tmp_path, kind):
    from knowledge_product_workflow.change_impact import (
        plan_binding_matches,
        proof_contract_matches,
    )

    workspace, _ = prepared(tmp_path)
    if kind == "refresh":
        directory = workspace.root / "runs/orchestration/proof-refresh"
        path = directory / "tx-observed.json"
        query = lambda: proof_contract_matches(workspace, 1, "a" * 64, "content_plan",
                                              compliance.compliance_path(workspace, 1))
    else:
        directory = workspace.root / "runs/content-plan-history"
        path = directory / f"chapter-01-{'a' * 64}.yaml"
        query = lambda: plan_binding_matches(workspace, 1, "a" * 64)
    directory.mkdir(parents=True, exist_ok=True)
    output = workspace.root / "runs/history-must-not-publish.txt"
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        assert query() is False
        path.write_text("{}", encoding="utf-8")
        write_text(output, "must reject changed member set", encoding="utf-8")
    assert not output.exists()


def style_fixture(tmp_path):
    import hashlib
    import shutil

    import yaml

    from knowledge_product_workflow.frontmatter import (
        parse_markdown_artifact,
        render_markdown_artifact,
    )
    from knowledge_product_workflow.illustrations import record_asset_style
    from tests.test_illustration_style import review, style_workspace

    workspace = serial_book(tmp_path / "book")
    seed = style_workspace(tmp_path / "style-seed")
    profile = yaml.safe_load((seed.root / "planning/publication-profile.yaml").read_text(encoding="utf-8"))
    profile.update(profile_id="knowledge-product-pdf-v4", schema_version=1, content_contract_enabled=False)
    (workspace.root / "planning/publication-profile.yaml").write_text(
        yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    for relative in ("assets/figures/fixture.png", "runs/diagrams/fixture.json"):
        destination = workspace.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed.root / relative, destination)
    proof = record_asset_style(workspace, review())
    path = workspace.root / "chapters/chapter-01.md"
    metadata, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    body = body.rstrip() + "\n\n资产核验路径：assets/figures/fixture.png\n"
    path.write_text(render_markdown_artifact(metadata, body), encoding="utf-8")
    record_path = compliance.compliance_path(workspace, 1)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["chapter_body_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return workspace, path, proof


@pytest.mark.parametrize("target", ["proof", "png"])
def test_external_style_inputs_cannot_publish_cached_pass(tmp_path, target):
    workspace, path, proof = style_fixture(tmp_path)
    output = workspace.root / "runs/style-must-not-publish.txt"
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        assert compliance.validate_current_chapter_compliance(workspace, path) == []
        changed = proof if target == "proof" else workspace.root / "assets/figures/fixture.png"
        info = changed.stat()
        changed.write_bytes(changed.read_bytes() + b" ")
        os.utime(changed, ns=(info.st_atime_ns, info.st_mtime_ns))
        compliance.validate_current_chapter_compliance(workspace, path)
        write_text(output, "must reject stale asset inputs", encoding="utf-8")
    assert not output.exists()


def test_style_seal_events_remain_live_inside_compliance_reuse(tmp_path):
    workspace, path, _ = style_fixture(tmp_path)
    with verification_scope(workspace):
        assert compliance.validate_current_chapter_compliance(workspace, path) == []
        events = workspace.root / "runs/events.jsonl"
        rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
        rows = [row for row in rows if row.get("event_type") != "illustration_style_checked"]
        events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        findings = compliance.validate_current_chapter_compliance(workspace, path)
        assert any(f.code == "ILLUSTRATION_STYLE_PROOF_REQUIRED" for f in findings)


def test_absent_palette_input_is_in_the_read_set(tmp_path):
    from knowledge_product_workflow.illustration_palette import load_category_colors

    workspace, _ = prepared(tmp_path)
    path = workspace.root / "planning/illustration-plan.json"
    assert not path.exists()
    output = workspace.root / "runs/palette-must-not-publish.txt"
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        assert load_category_colors(workspace) == {}
        path.write_text("{}", encoding="utf-8")
        write_text(output, "must reject newly created palette", encoding="utf-8")
    assert not output.exists()


def test_composition_with_external_font_is_not_memoized(tmp_path):
    from types import SimpleNamespace

    from knowledge_product_workflow.diagrams import DiagramInput
    from knowledge_product_workflow.experimental_composition import composition_input_digest
    from tests.test_illustration_palette import presentation, profile

    root = tmp_path / "book"
    root.mkdir()
    material = root / "material.png"
    material.write_bytes(b"synthetic material hash input")
    font = tmp_path / "external-font.bin"
    font.write_bytes(b"font-version-one")
    css = f'@font-face {{src:url("{font.as_uri()}")}}'
    colors = presentation()
    colors.node_colors["b"] = "sage"
    semantic = DiagramInput(diagram_id="digest", diagram_type="flow", caption="摘要依赖",
        reader_conclusion="保持素材和字体的版本绑定",
        nodes=[{"node_id": "a", "label": "输入"}, {"node_id": "b", "label": "输出"}],
        presentation=colors)
    with verification_scope(SimpleNamespace(root=root)) as context:
        compute = lambda: composition_input_digest(material, semantic, profile(), {}, css)
        first = context.memo("composition", compute)
        font.write_bytes(b"font-version-two")
        second = context.memo("composition", compute)
        assert first != second
