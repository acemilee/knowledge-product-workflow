from knowledge_product_workflow.quality import QualityFinding, Severity, evaluate_publishability


def test_blocker_finding_prevents_publishability() -> None:
    findings = [
        QualityFinding(
            code="MISSING_CLAIM_SOURCE",
            severity=Severity.BLOCKER,
            location="claim-002",
            message="Claim has no registered source",
        )
    ]

    assert evaluate_publishability(findings) is False


def test_warnings_do_not_prevent_publishability() -> None:
    findings = [
        QualityFinding(
            code="STYLE_WARNING",
            severity=Severity.WARNING,
            location="chapter-01.md",
            message="Sentence could be shorter",
        )
    ]

    assert evaluate_publishability(findings) is True
