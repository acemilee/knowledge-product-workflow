"""F00共同合成夹具；不读生产材料，测试批准／观察不能用于真实交付。"""

import copy
import json

import yaml

from knowledge_product_workflow.chapter_compliance import recheck_submitted_chapter, submit_chapter
from knowledge_product_workflow.claim_classification import (
    ClaimClassificationInput,
    append_claim_classification,
)
from knowledge_product_workflow.claims import current_claim_contract_bindings
from knowledge_product_workflow.developmental import (
    StructureReviewInput,
    create_structure_review_template,
    record_developmental_check,
    record_structure_review,
)
from knowledge_product_workflow.humanize import record_humanize_check
from knowledge_product_workflow.source_ledger import SourceLedgerEntry, append_source_entry
from knowledge_product_workflow.user_decisions import (
    UserDecisionTarget,
    UserResponseDecision,
    record_user_decision,
    request_user_decision,
)
from knowledge_product_workflow.workflows.authoring import (
    approve_sample_chapter,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
from tests.test_change_impact_v140 import workspace_fixture
from tests.test_chapter_compliance import _body
from tests.test_illustration_policy import plan
from tests.test_proof_refresh_v140 import apply
from tests.test_structure_review_v133 import simulated_review

CLAIM = "claim-perf-local"
STATEMENT = "本合成夹具只处理固定输入并提供可核对输出。"
RATIONALE = "本节以文字判断顺序表达，无须图示关系"


def inputs(w, number, count):
    raw = plan(
        2, [{"section_id": f"{number}.1", "decision": "none", "alternative_rationale": RATIONALE}]
    )
    raw["sections"][0]["section_id"] = f"{number}.1"
    p = w.root / f"runs/perf-plan-{number}.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8", newline="\n")
    body = _body().replace("1.", f"{number}.").replace("基础判断", f"章节{number}")
    body += "\n可查属性表\n\n| 输入 | 核验 |\n| --- | --- |\n| 证据不足 | 记录待核条件 |\n"
    if number == count:
        body += "\n" + STATEMENT + "\n"
    source = w.root / f"runs/perf-body-{number}.md"
    source.write_text(body, encoding="utf-8", newline="\n")
    mapping = w.root / f"runs/perf-mapping-{number}.json"
    mapping.write_text(
        json.dumps(
            {
                "learning_objective_anchors": {"建立可执行的判断框架": f"{number}.1 决策框架"},
                "unit_anchors": {"decision_framework": f"{number}.1 决策框架"},
                "claim_locations": {CLAIM: f"{number}.1 决策框架"} if number == count else {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p, source, mapping


def proofs(w, number, source, *, compare=True):
    report = simulated_review(w, source, number=number)
    if compare:
        template = create_structure_review_template(w, number, source)
        observations = json.loads(report.read_text(encoding="utf-8"))["observations"]
        others = [
            f"{n}.1"
            for n in range(1, number)
            if (w.root / f"chapters/chapter-{n:02d}.md").is_file()
        ]
        if len(others) < 2:
            others = [
                f"{n}.1"
                for n in range(1, 100)
                if n != number and (w.root / f"chapters/chapter-{n:02d}.md").is_file()
            ]
        for o in observations:
            o["compared_unit_ids"] = others[:13]
        record_structure_review(
            w,
            number,
            source,
            StructureReviewInput(template_id=template["template_id"], observations=observations),
        )
    record_developmental_check(w, number, source)
    record_humanize_check(w, number, source)


def make_fixture(parent, count, *, blueprint_only=False):
    w = workspace_fixture(parent, count)
    raw = require_current_submitted_blueprint(w)[2].model_dump(mode="json")
    raw.update(production_policy_revision=1, shared_context={}, illustration_policy_revision=2)
    first = copy.deepcopy(raw["chapters"][0])
    for n in range(1, count + 1):
        raw["chapters"][n - 1] = {
            **copy.deepcopy(first),
            "chapter_number": n,
            "title": f"章节{n}",
            "scope": f"第{n}项决策。"
            + ("固定输入、可复核输出和明确的失败边界构成教学练习的条件。" * 45),
            "rationale": f"独立核验第{n}个教学任务的输入、决策与迁移条件",
            "claim_ids": [CLAIM] if n == count else [],
            "production_dependencies": [],
            "production_exports": [],
            "illustration_policy_revision": 2,
            "section_outline": [{"number": f"{n}.1", "title": "决策框架", "children": []}],
            "illustration_tasks": [
                {"section_id": f"{n}.1", "decision": "none", "alternative_rationale": RATIONALE}
            ],
        }
    apply(w, raw)
    append_claim_classification(
        w,
        ClaimClassificationInput(
            claim_id=CLAIM,
            kind="factual",
            statement=STATEMENT,
            heading_anchor=f"{count}.1 决策框架",
            reason="自有合成观测材料，不是现实外部事实",
        ),
    )
    binding = current_claim_contract_bindings(w)
    for n in range(3):
        append_source_entry(
            w.root / "research/source-ledger.jsonl",
            SourceLedgerEntry(
                source_id=f"perf-owned-{n}",
                url="https://example.test/owned-fixture",
                accessed_at="2026-10-16T00:00:00Z",
                excerpt=STATEMENT,
                claim_ids=[CLAIM],
                claim_contract_sha256=binding,
                license_note="自有合成材料；未联网",
            ),
        )
    if blueprint_only:
        return w
    from knowledge_product_workflow.content_plan import submit_content_plan

    for n in range(1, count):
        create_chapter_draft(w, n)
        p, source, mapping = inputs(w, n, count)
        submit_content_plan(w, n, p)
        proofs(w, n, source, compare=False)
        submit_chapter(w, n, source, mapping)
        if n == 1:
            request_user_decision(w, UserDecisionTarget.SAMPLE)
            record_user_decision(
                w,
                UserDecisionTarget.SAMPLE,
                UserResponseDecision.APPROVE,
                approved_by="测试用户",
                response="人工夹具批准样章继续",
            )
            approve_sample_chapter(w, "测试用户")
    # Establish real comparison bindings only once all compared units exist.
    for n in range(1, count):
        _, source, mapping = inputs(w, n, count)
        proofs(w, n, source, compare=True)
        recheck_submitted_chapter(w, n, mapping)
    inputs(w, count, count)
    return w
