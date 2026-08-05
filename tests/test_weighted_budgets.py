import pytest
from pydantic import ValidationError

from knowledge_product_workflow.detail_constraints import (
    ChapterSpec,
    DetailConstraints,
    WordBudget,
    allocate_budgets,
    parse_word_budget,
)


def test_range_budget_uses_midpoint_target() -> None:
    assert parse_word_budget("30000-50000") == WordBudget(
        minimum=30000,
        target=40000,
        maximum=50000,
    )


def test_weighted_targets_preserve_each_range_total() -> None:
    constraints = DetailConstraints(word_min=30000, word_target=40000, word_max=50000)
    chapters = [
        ChapterSpec(
            chapter_number=1,
            weight=1,
            rationale="建立共同概念基础",
            required_units=["concept_explanation"],
        ),
        ChapterSpec(
            chapter_number=2,
            weight=3,
            rationale="承担主要实践训练",
            required_units=["worked_case"],
        ),
    ]

    budgets = allocate_budgets(chapters, constraints)

    assert [budget.word_min for budget in budgets] == [7500, 22500]
    assert [budget.word_target for budget in budgets] == [10000, 30000]
    assert [budget.word_max for budget in budgets] == [12500, 37500]
    assert sum(budget.word_target or 0 for budget in budgets) == 40000


def test_reversed_word_range_is_rejected() -> None:
    with pytest.raises(ValidationError, match="最小值"):
        DetailConstraints(word_min=50000, word_target=40000, word_max=30000)
