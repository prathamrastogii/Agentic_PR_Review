from backend.models.review import ReviewInsights, ReviewVerdict


def test_review_verdict_defaults_insights():
    verdict = ReviewVerdict(summary="ok", confidence="high")
    assert verdict.insights.whats_good == []
    assert verdict.insights.risks == []
    assert verdict.insights.improvements == []


def test_review_verdict_serializes_insights():
    verdict = ReviewVerdict(
        summary="ok",
        confidence="medium",
        insights=ReviewInsights(
            whats_good=["Clean diff"],
            risks=["Possible race"],
            improvements=["Add logging"],
        ),
    )
    data = verdict.model_dump(mode="json")
    assert data["insights"]["whats_good"] == ["Clean diff"]
    assert data["insights"]["risks"] == ["Possible race"]
