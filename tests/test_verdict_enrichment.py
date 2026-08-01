from backend.github.models import FileDiff, PRMetadata
from backend.models.review import ReviewInsights, ReviewIssue, ReviewVerdict
from backend.services.verdict_enrichment import (
    CONFIDENCE_TIPS_THRESHOLD,
    build_confidence_tips,
    compute_confidence_score,
    enrich_verdict,
    infer_pr_aim,
    sync_insights_to_issues,
)


def _metadata(**overrides) -> PRMetadata:
    defaults = {
        "owner": "acme",
        "repo": "app",
        "pr_number": 42,
        "title": "Convert failedWorkflow input to a Map",
        "body": None,
        "base_ref": "main",
        "head_ref": "fix/failed-workflow-input-map",
        "head_sha": "abc1234",
        "html_url": "https://github.com/acme/app/pull/42",
    }
    defaults.update(overrides)
    return PRMetadata(**defaults)


def _files() -> list[FileDiff]:
    return [
        FileDiff(
            filename="core/src/main/java/Foo.java",
            status="modified",
            patch="+1",
            changes=12,
        ),
        FileDiff(
            filename="core/src/test/java/FooTest.java",
            status="modified",
            patch="+1",
            changes=8,
        ),
    ]


def test_sync_insights_to_issues_backfills_risks_and_improvements():
    verdict = ReviewVerdict(
        summary="Needs work",
        confidence="medium",
        insights=ReviewInsights(
            risks=["Possible null handling bug in Foo.java"],
            improvements=["Add edge-case test coverage"],
        ),
    )

    enriched = sync_insights_to_issues(verdict, _files())

    assert len(enriched.issues) == 2
    assert enriched.issues[0].severity == "error"
    assert enriched.issues[0].file == "core/src/main/java/Foo.java"
    assert enriched.issues[1].severity == "suggestion"


def test_sync_insights_to_issues_keeps_existing_issues():
    verdict = ReviewVerdict(
        summary="ok",
        confidence="high",
        issues=[
            ReviewIssue(
                file="a.py",
                severity="error",
                category="correctness",
                message="bug",
            )
        ],
        insights=ReviewInsights(risks=["Should not duplicate"]),
    )

    enriched = sync_insights_to_issues(verdict, _files())
    assert len(enriched.issues) == 1


def test_infer_pr_aim_uses_description_when_present():
    metadata = _metadata(
        body="This PR converts failedWorkflow input to a Map so nested references resolve."
    )
    aim, clarity = infer_pr_aim(metadata, _files())

    assert "Map" in aim
    assert clarity >= 10


def test_infer_pr_aim_falls_back_to_branch_and_code():
    metadata = _metadata(body=None)
    aim, clarity = infer_pr_aim(metadata, _files())

    assert "Title:" in aim
    assert "bug fix" in aim.lower() or "Inferred from code" in aim
    assert clarity >= 6


def test_compute_confidence_score_higher_with_description():
    verdict = ReviewVerdict(summary="ok", confidence="high")
    with_desc = compute_confidence_score(
        _metadata(body="Detailed explanation of the workflow input change."),
        verdict,
        _files(),
    )[0]
    without_desc = compute_confidence_score(_metadata(body=None), verdict, _files())[0]

    assert with_desc > without_desc


def test_enrich_verdict_sets_score_and_issues():
    verdict = ReviewVerdict(
        summary="Looks risky",
        confidence="high",
        insights=ReviewInsights(risks=["Security issue in auth path"]),
    )

    enriched = enrich_verdict(_metadata(body="Fix auth handling for nested maps."), verdict, _files())

    assert enriched.issues
    assert enriched.confidence_score is not None
    assert enriched.confidence_rationale
    assert enriched.confidence in ("high", "medium", "low")


def test_review_issue_normalizes_high_severity_alias():
    issue = ReviewIssue(
        file="a.py",
        severity="high",
        category="correctness",
        message="bug",
    )
    assert issue.severity == "error"


def test_build_confidence_tips_empty_when_score_at_threshold():
    verdict = ReviewVerdict(summary="ok", confidence="high")
    tips = build_confidence_tips(_metadata(), verdict, _files(), CONFIDENCE_TIPS_THRESHOLD)
    assert tips == []


def test_build_confidence_tips_when_score_below_threshold():
    verdict = ReviewVerdict(
        summary="ok",
        confidence="medium",
        partial_investigation=True,
    )
    tips = build_confidence_tips(_metadata(body=None), verdict, _files(), 65)

    assert tips
    assert any("description" in tip.lower() for tip in tips)
    assert any("investigation" in tip.lower() for tip in tips)


def test_enrich_verdict_includes_tips_when_score_low():
    verdict = ReviewVerdict(summary="ok", confidence="low")
    enriched = enrich_verdict(_metadata(body=None), verdict, _files())

    assert enriched.confidence_score is not None
    if enriched.confidence_score < CONFIDENCE_TIPS_THRESHOLD:
        assert enriched.confidence_tips
