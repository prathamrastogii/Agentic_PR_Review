from backend.github.models import FileDiff, PRMetadata
from backend.models.review import ReviewInsights, ReviewIssue, ReviewVerdict
from backend.services.verdict_enrichment import (
    CONFIDENCE_TIPS_THRESHOLD,
    READINESS_TIPS_THRESHOLD,
    build_confidence_tips,
    build_pr_readiness_tips,
    compute_confidence_score,
    compute_pr_readiness_score,
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


def test_infer_pr_aim_skips_boilerplate_description_heading():
    metadata = _metadata(
        body="## Description\n\nEnsure subtasks inherit planned status from parent tasks."
    )
    aim, _ = infer_pr_aim(metadata, _files())

    assert aim.startswith("Ensure subtasks")
    assert "## Description" not in aim


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
    assert any("description" in tip.lower() or "context" in tip.lower() for tip in tips)
    assert any("investigation" in tip.lower() or "ended" in tip.lower() for tip in tips)


def test_obvious_single_file_issue_scores_high_review_confidence():
    """A correct review of an obvious single-file problem should score high."""
    metadata = _metadata(
        title="updated movies player for live streaming",
        body=None,
        head_ref="feat/live-stream",
    )
    files = [
        FileDiff(
            filename="src/pages/Home.jsx",
            status="modified",
            patch=(
                "@@ -1,120 +1 @@\n"
                "-import React from 'react';\n"
                "+total work done\n"
            ),
            additions=1,
            deletions=120,
            changes=121,
        )
    ]
    verdict = ReviewVerdict(
        summary="The Home page was replaced with placeholder text.",
        confidence="high",
        issues=[
            ReviewIssue(
                file="src/pages/Home.jsx",
                line=1,
                severity="error",
                category="correctness",
                message="The entire Home page component was deleted and replaced with placeholder text.",
            )
        ],
        insights=ReviewInsights(
            risks=["Deleting the entire page implementation breaks the application."],
        ),
    )

    enriched = enrich_verdict(metadata, verdict, files, mode="agent")

    assert enriched.confidence == "high"
    assert enriched.confidence_score is not None
    assert enriched.confidence_score >= 72
    assert enriched.pr_readiness == "low"
    assert enriched.pr_readiness_score is not None
    assert enriched.pr_readiness_score < 45


def test_multi_file_complete_diff_scores_reasonable_review_confidence():
    """When every changed file is visible in the diff, trust should not crater."""
    metadata = _metadata(body="Adds review confidence scoring across backend and UI.")
    files = [
        FileDiff(
            filename=f"src/module{i}.py",
            status="modified",
            patch=f"@@\n+change {i}\n" * 5,
            changes=20,
        )
        for i in range(21)
    ]
    from backend.models.review import InvestigationStep

    verdict = ReviewVerdict(
        summary="Coherent feature PR with scoring split and UI updates.",
        confidence="high",
        investigation_trail=[
            InvestigationStep(
                file_path="backend/services/verdict_enrichment.py",
                reason="core scoring logic",
            )
        ],
        issues=[
            ReviewIssue(
                file="static/app.js",
                severity="suggestion",
                category="style",
                message="Consider extracting chart helpers.",
            )
        ],
    )

    score, _, level = compute_confidence_score(metadata, verdict, files, mode="agent")

    assert level in ("medium", "high")
    assert score >= 48


def test_multi_file_diff_only_with_high_model_claim_scores_low_review_confidence():
    metadata = _metadata(body=None)
    files = [
        FileDiff(filename=f"src/module{i}.py", status="modified", patch="+x" * 20, changes=40)
        for i in range(6)
    ]
    verdict = ReviewVerdict(
        summary="Looks fine.",
        confidence="high",
        issues=[],
    )

    score, _, level = compute_confidence_score(metadata, verdict, files, mode="agent")

    assert level in ("low", "medium")
    assert score < 72


def test_enrich_verdict_caps_partial_investigation_at_medium():
    from backend.models.review import InvestigationStep

    trail = [
        InvestigationStep(file_path="core/src/main/java/Foo.java", reason="check"),
        InvestigationStep(file_path="core/src/test/java/FooTest.java", reason="tests"),
    ]
    verdict = ReviewVerdict(
        summary="partial review with findings",
        confidence="medium",
        partial_investigation=True,
        investigation_trail=trail,
        issues=[
            ReviewIssue(
                file="core/src/main/java/Foo.java",
                severity="warning",
                category="correctness",
                message="possible bug",
            ),
            ReviewIssue(
                file="core/src/test/java/FooTest.java",
                severity="warning",
                category="correctness",
                message="missing edge case",
            ),
            ReviewIssue(
                file="core/src/main/java/Foo.java",
                severity="suggestion",
                category="style",
                message="cleanup",
            ),
        ],
    )
    enriched = enrich_verdict(
        _metadata(
            body=(
                "This PR converts failedWorkflow input to a Map so nested references resolve. "
                * 3
            )
        ),
        verdict,
        _files(),
    )

    assert enriched.confidence != "high"
    assert enriched.confidence_score is not None
    assert enriched.confidence_score < 72


def test_enrich_verdict_includes_tips_when_score_low():
    verdict = ReviewVerdict(summary="ok", confidence="low")
    enriched = enrich_verdict(_metadata(body=None), verdict, _files())

    assert enriched.confidence_score is not None
    if enriched.confidence_score < CONFIDENCE_TIPS_THRESHOLD:
        assert enriched.confidence_tips


def test_clean_pr_scores_high_readiness():
    metadata = _metadata(body="Adds input validation and unit tests for the workflow map change.")
    verdict = ReviewVerdict(
        summary="Focused validation improvement with tests.",
        confidence="high",
        issues=[],
        insights=ReviewInsights(whats_good=["Clear validation logic", "Tests updated"]),
    )
    enriched = enrich_verdict(metadata, verdict, _files(), mode="agent")

    assert enriched.pr_readiness_score is not None
    assert enriched.pr_readiness == "high"
    assert enriched.pr_readiness_score >= 75


def test_agent_mode_tips_do_not_suggest_switching_to_agent_mode():
    """Large truncated PRs in agent mode should not tell the user to use agent mode."""
    files = [
        FileDiff(
            filename=f"src/module{i}.py",
            status="modified",
            patch="@@\n" + "+line\n" * 400,
            changes=400,
        )
        for i in range(21)
    ]
    from backend.models.review import InvestigationStep

    verdict = ReviewVerdict(
        summary="Large feature PR.",
        confidence="high",
        investigation_trail=[
            InvestigationStep(
                file_path="backend/services/verdict_enrichment.py",
                reason="core logic",
            )
        ],
    )
    tips = build_confidence_tips(_metadata(body=None), verdict, files, 40, mode="agent")

    assert tips
    joined = " ".join(tips).lower()
    assert "use agent mode" not in joined
    assert "re-running with agent mode" not in joined
    assert "investigation budget" in joined or "supporting file" in joined
