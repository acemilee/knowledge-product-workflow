import json

import pytest

from knowledge_product_workflow.chapter_compliance import submit_chapter
from knowledge_product_workflow.humanize import record_humanize_check
from knowledge_product_workflow.user_decisions import (
    UserDecisionTarget,
    UserResponseDecision,
    record_user_decision,
    request_user_decision,
    sample_approval_basis,
    sample_approval_is_current,
)
from knowledge_product_workflow.workflows.authoring import (
    amend_chapter_draft,
    approve_sample_chapter,
    create_chapter_draft,
)
from tests.test_change_impact_v140 import workspace_fixture
from tests.test_chapter_compliance import _body, _mapping


def sample(tmp_path):
    w = workspace_fixture(tmp_path)
    create_chapter_draft(w, 1)
    source = w.root / "runs/body.md"
    source.write_text(_body().replace("基础判断", "独立教学单元1"), encoding="utf-8")
    mapping = w.root / "runs/mapping.json"
    mapping.write_text(json.dumps(_mapping(), ensure_ascii=False), encoding="utf-8")
    record_humanize_check(w, 1, source)
    submit_chapter(w, 1, source, mapping)
    request_user_decision(w, UserDecisionTarget.SAMPLE)
    record_user_decision(w, UserDecisionTarget.SAMPLE, UserResponseDecision.APPROVE,
        approved_by="测试用户", response="继续完成全书")
    approve_sample_chapter(w, "测试用户")
    return w


def test_metadata_reopening_preserves_approved_reader_object(tmp_path):
    w = sample(tmp_path)
    marker = w.root / "chapters/chapter-01-sample.approved"
    before, basis = marker.read_bytes(), sample_approval_basis(w)
    amend_chapter_draft(w, 1, "重开元数据但未修改读者内容")
    assert marker.read_bytes() == before
    assert sample_approval_basis(w) == basis
    assert sample_approval_is_current(w)


def test_changed_body_or_changed_obligation_never_retains_approval(tmp_path):
    w = sample(tmp_path)
    chapter = w.root / "chapters/chapter-01.md"
    chapter.write_text(chapter.read_text(encoding="utf-8") + "\n新的事实。\n", encoding="utf-8")
    assert not sample_approval_is_current(w)
    with pytest.raises(ValueError, match="样章"):
        create_chapter_draft(w, 2)


def test_changed_obligation_with_identical_body_still_requires_approval(tmp_path):
    from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
    from tests.test_proof_refresh_v140 import apply
    w = sample(tmp_path)
    body = (w.root / "chapters/chapter-01.md").read_bytes()
    _, _, blueprint = require_current_submitted_blueprint(w)
    raw = blueprint.model_dump(mode="json")
    raw["chapters"][0]["scope"] += "，新增正式适用条件"
    apply(w, raw)
    assert (w.root / "chapters/chapter-01.md").read_bytes() == body
    assert not sample_approval_is_current(w)
