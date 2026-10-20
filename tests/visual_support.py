"""Bind synthetic test observations; never infer assessments or user approval."""
from knowledge_product_workflow.visual_audit import current_visual_audit


def bind_fixture_observations(workspace, observations):
    report = current_visual_audit(workspace)
    assert report is not None
    for item in observations:
        item.observation_input_sha256 = report.observation_input_sha256
        item.page_image_sha256 = report.pages[item.page_number - 1].high_resolution_image_sha256
    return observations


def approve_fixture_pdf(workspace, monkeypatch, scopes, response):
    """Synthetic scope protocol only; upstream content is outside this unit test."""
    from knowledge_product_workflow.delivery_preflight import run_delivery_preflight
    from knowledge_product_workflow.user_decisions import (
        UserDecisionTarget,
        UserResponseDecision,
        record_user_decision,
        request_user_decision,
    )
    with monkeypatch.context() as patch:
        patch.setattr('knowledge_product_workflow.workflows.validation.collect_delivery_findings', lambda _: [])
        run_delivery_preflight(workspace)
    request_user_decision(workspace, UserDecisionTarget.PDF, scopes=scopes)
    record_user_decision(workspace, UserDecisionTarget.PDF, UserResponseDecision.APPROVE,
        approved_by='软件范围测试夹具，非用户批准', response=response, confirmed_scope=scopes)
