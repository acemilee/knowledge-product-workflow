from knowledge_product_workflow.detail_constraints import (
    DetailConstraints,
    PageConstraintMode,
    PageScope,
    allocate_budgets,
    count_words,
    parse_page_budget,
)


def test_constraints_allow_the_model_to_decide_length() -> None:
    constraints = DetailConstraints()

    assert constraints.target_pages is None
    assert constraints.word_budget is None


def test_page_budget_accepts_an_interval_and_preserves_legacy_target() -> None:
    budget = parse_page_budget("10-20")
    constraints = DetailConstraints(
        target_pages=budget.target,
        page_min=budget.minimum,
        page_target=budget.target,
        page_max=budget.maximum,
    )

    assert (budget.minimum, budget.target, budget.maximum) == (10, 15, 20)
    assert constraints.target_pages == 15
    assert constraints.page_budget is not None
    assert (constraints.page_budget.minimum, constraints.page_budget.maximum) == (10, 20)


def test_count_words_handles_chinese_links_and_code_comments() -> None:
    text = "Agent 应用开发 with Python 3\n[docs](https://example.com/long-url)\n```python\n# 注释 comment\nvalue = 3\n```"

    assert count_words(text) == 12


def test_budget_allocation_preserves_the_exact_total() -> None:
    constraints = DetailConstraints(target_words=10, target_pages=4)

    budgets = allocate_budgets([1, 2, 3], constraints)

    assert sum(budget.target_words or 0 for budget in budgets) == 10
    assert sum(budget.target_pages or 0 for budget in budgets) == 4
    assert [budget.target_words for budget in budgets] == [4, 3, 3]


def test_page_constraints_support_minimum_unbounded_and_body_only_without_forcing_targets() -> None:
    minimum = parse_page_budget("至少10页")
    unlimited = parse_page_budget("页数无上限")
    body_only = parse_page_budget("除去封面与目录外 15 页")

    assert minimum.mode is PageConstraintMode.MINIMUM
    assert minimum.minimum == 10 and minimum.target is None and minimum.scope is PageScope.DELIVERY_TOTAL
    assert unlimited.mode is PageConstraintMode.UNBOUNDED
    assert body_only.mode is PageConstraintMode.EXACT and body_only.scope is PageScope.BODY_ONLY

    minimum_budgets = allocate_budgets([1, 2], DetailConstraints(page_constraint=minimum))
    unlimited_budgets = allocate_budgets([1, 2], DetailConstraints(page_constraint=unlimited))
    assert [budget.target_pages for budget in minimum_budgets] == [None, None]
    assert [budget.target_pages for budget in unlimited_budgets] == [None, None]


def test_page_constraint_normalizes_lower_bound_synonyms_without_an_implied_target() -> None:
    for value in ("至少10页", "10页及以上", "10页以上", "10页起", "10+", "5+页", "至少10页，目标为10页"):
        constraint = parse_page_budget(value)
        assert constraint.mode is PageConstraintMode.MINIMUM
        assert constraint.minimum == (5 if value == "5+页" else 10)
        assert constraint.target is None
        assert constraint.maximum is None
