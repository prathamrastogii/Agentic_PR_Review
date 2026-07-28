from unittest.mock import AsyncMock, patch

import pytest

from backend.agent.actions import EvaluateResponse
from backend.agent.graph import build_review_graph, route_after_evaluate, run_agent_review
from backend.agent.nodes import (
    _pick_investigation_target,
    changed_line_count,
    evaluate_node,
    fetch_file_node,
)
from backend.agent.state import AgentState
from backend.github.client import GitHubAPIError, GitHubClient
from backend.github.models import FileDiff, PRMetadata
from backend.models.review import InvestigationStep, ReviewIssue, ReviewVerdict


def _metadata() -> PRMetadata:
    return PRMetadata(
        owner="octo",
        repo="repo",
        pr_number=1,
        title="Test PR",
        body=None,
        base_ref="main",
        head_ref="feature",
        head_sha="abc123",
        html_url="https://github.com/octo/repo/pull/1",
    )


def _initial_state(**overrides) -> AgentState:
    state: AgentState = {
        "pr_metadata": _metadata(),
        "llm_config": None,
        "diffs": [
            FileDiff(
                filename="app.py",
                status="modified",
                patch="@@ -1 +1 @@\n-from helpers import foo\n+from helpers import bar",
            )
        ],
        "fetched_files": {},
        "unavailable_files": {},
        "investigation_count": 0,
        "max_investigations": 2,
        "investigation_trail": [],
        "pending_file_request": None,
        "pending_reason": None,
        "verdict": None,
        "feedback_note": None,
    }
    state.update(overrides)
    return state


class TestRouteAfterEvaluate:
    def test_end_when_verdict_set(self):
        verdict = ReviewVerdict(summary="ok", confidence="high")
        state = _initial_state(verdict=verdict)
        assert route_after_evaluate(state) == "end"

    def test_fetch_when_pending_and_budget(self):
        state = _initial_state(
            pending_file_request="helpers.py",
            pending_reason="need foo impl",
        )
        assert route_after_evaluate(state) == "fetch_file"

    def test_routes_to_evaluate_when_budget_exhausted(self):
        state = _initial_state(
            investigation_count=2,
            pending_file_request="helpers.py",
            pending_reason="need foo impl",
        )
        assert route_after_evaluate(state) == "evaluate"


@pytest.mark.asyncio
async def test_evaluate_node_verdict():
    state = _initial_state()
    mock_response = EvaluateResponse(
        action="verdict",
        summary="All good",
        confidence="high",
    )
    with patch(
        "backend.agent.nodes.invoke_structured",
        new=AsyncMock(return_value=mock_response),
    ):
        result = await evaluate_node(state)

    assert result["verdict"].summary == "All good"
    assert result["verdict"].confidence == "high"
    assert result["pending_file_request"] is None


@pytest.mark.asyncio
async def test_evaluate_node_investigate():
    state = _initial_state()
    mock_response = EvaluateResponse(
        action="investigate",
        file_path="helpers.py",
        reason="Need implementation of bar()",
    )
    with patch(
        "backend.agent.nodes.invoke_structured",
        new=AsyncMock(return_value=mock_response),
    ):
        result = await evaluate_node(state)

    assert result["pending_file_request"] == "helpers.py"
    assert result["pending_reason"] == "Need implementation of bar()"
    assert "verdict" not in result


@pytest.mark.asyncio
async def test_evaluate_node_dedup_skips_budget():
    state = _initial_state(
        fetched_files={"helpers.py": "def bar(): pass"},
    )
    investigate_dup = EvaluateResponse(
        action="investigate",
        file_path="helpers.py",
        reason="Need implementation",
    )
    verdict_response = EvaluateResponse(
        action="verdict",
        summary="Done",
        confidence="high",
    )
    with patch(
        "backend.agent.nodes.invoke_structured",
        new=AsyncMock(side_effect=[investigate_dup, verdict_response]),
    ):
        result = await evaluate_node(state)
        assert result["feedback_note"] is not None

        state.update(result)
        result2 = await evaluate_node(state)
        assert result2["verdict"].summary == "Done"


@pytest.mark.asyncio
async def test_evaluate_node_budget_exhausted_forces_partial():
    state = _initial_state(investigation_count=2)
    mock_response = EvaluateResponse(
        action="investigate",
        file_path="helpers.py",
        reason="still need more",
    )
    with patch(
        "backend.agent.nodes.invoke_structured",
        new=AsyncMock(return_value=mock_response),
    ):
        result = await evaluate_node(state)

    assert result["verdict"] is not None
    assert result["verdict"].partial_investigation is True
    assert result["verdict"].confidence in ("medium", "low")


@pytest.mark.asyncio
async def test_fetch_file_node():
    state = _initial_state(
        pending_file_request="helpers.py",
        pending_reason="Need bar()",
    )
    client = GitHubClient(token="test")
    with patch.object(
        client,
        "get_file_content",
        new=AsyncMock(return_value="def bar(): return 1"),
    ):
        result = await fetch_file_node(state, client)

    assert result["fetched_files"]["helpers.py"] == "def bar(): return 1"
    assert result["investigation_count"] == 1
    assert len(result["investigation_trail"]) == 1
    assert result["investigation_trail"][0].file_path == "helpers.py"


@pytest.mark.asyncio
async def test_full_graph_investigate_then_verdict():
    investigate = EvaluateResponse(
        action="investigate",
        file_path="helpers.py",
        reason="Need bar implementation",
    )
    verdict_response = EvaluateResponse(
        action="verdict",
        summary="Found issue in bar usage",
        confidence="high",
        issues=[
            ReviewIssue(
                file="app.py",
                severity="warning",
                category="correctness",
                message="bar() may return wrong type",
            )
        ],
    )
    client = GitHubClient(token="test")
    with (
        patch(
            "backend.agent.nodes.invoke_structured",
            new=AsyncMock(side_effect=[investigate, verdict_response]),
        ),
        patch.object(
            client,
            "get_file_content",
            new=AsyncMock(return_value="def bar(): return None"),
        ),
    ):
        graph = build_review_graph(client)
        result = await graph.ainvoke(_initial_state(), config={"recursion_limit": 25})

    assert result["verdict"] is not None
    assert result["verdict"].summary == "Found issue in bar usage"
    assert len(result["investigation_trail"]) == 1
    assert result["investigation_trail"][0].file_path == "helpers.py"
    assert result["investigation_count"] == 1


@pytest.mark.asyncio
async def test_run_agent_review_integration():
    metadata = _metadata()
    files = [
        FileDiff(
            filename="app.py",
            status="modified",
            patch="+from utils import helper",
        )
    ]
    client = GitHubClient(token="test")

    investigate = EvaluateResponse(
        action="investigate",
        file_path="utils.py",
        reason="Need helper definition",
    )
    verdict_response = EvaluateResponse(
        action="verdict",
        summary="Review complete",
        confidence="medium",
    )

    with (
        patch(
            "backend.agent.nodes.invoke_structured",
            new=AsyncMock(side_effect=[investigate, verdict_response]),
        ),
        patch.object(
            client,
            "get_file_content",
            new=AsyncMock(return_value="def helper(): pass"),
        ),
    ):
        verdict = await run_agent_review(metadata, files, client, max_investigations=3)

    assert verdict.summary == "Review complete"
    assert len(verdict.investigation_trail) == 1
    assert verdict.investigation_trail[0].file_path == "utils.py"


class TestChangedLineCount:
    def test_prefers_reported_changes(self):
        diffs = [FileDiff(filename="a.py", status="modified", changes=40)]
        assert changed_line_count(diffs) == 40

    def test_falls_back_to_counting_patch_lines(self):
        diffs = [
            FileDiff(
                filename="a.py",
                status="modified",
                patch="@@ -1,2 +1,2 @@\n-old line\n+new line\n context",
            )
        ]
        assert changed_line_count(diffs) == 2

    def test_ignores_file_headers(self):
        diffs = [
            FileDiff(
                filename="a.py",
                status="modified",
                patch="--- a/a.py\n+++ b/a.py\n-old\n+new",
            )
        ]
        assert changed_line_count(diffs) == 2


def _substantial_state(**overrides):
    """State whose diff is large enough that a no-investigation verdict is suspect."""
    return _initial_state(
        diffs=[FileDiff(filename="app.py", status="modified", patch="@@ x @@", changes=80)],
        **overrides,
    )


@pytest.mark.asyncio
async def test_challenge_can_turn_verdict_into_investigation():
    unearned = EvaluateResponse(action="verdict", summary="Looks fine", confidence="high")
    after_challenge = EvaluateResponse(
        action="investigate",
        file_path="packages/core/wall.ts",
        reason="verify mitering contract",
    )
    mock = AsyncMock(side_effect=[unearned, after_challenge])

    with patch("backend.agent.nodes.invoke_structured", new=mock):
        result = await evaluate_node(_substantial_state())

    assert mock.await_count == 2
    assert result["pending_file_request"] == "packages/core/wall.ts"
    assert "verdict" not in result


@pytest.mark.asyncio
async def test_challenge_accepts_reaffirmed_verdict():
    unearned = EvaluateResponse(action="verdict", summary="Looks fine", confidence="high")
    reaffirmed = EvaluateResponse(
        action="verdict", summary="Self-contained change", confidence="high"
    )
    mock = AsyncMock(side_effect=[unearned, reaffirmed])

    with patch("backend.agent.nodes.invoke_structured", new=mock):
        result = await evaluate_node(_substantial_state())

    assert mock.await_count == 2
    assert result["verdict"].summary == "Self-contained change"


@pytest.mark.asyncio
async def test_no_challenge_for_trivial_diff():
    verdict = EvaluateResponse(action="verdict", summary="Typo fix", confidence="high")
    mock = AsyncMock(return_value=verdict)

    with patch("backend.agent.nodes.invoke_structured", new=mock):
        result = await evaluate_node(
            _initial_state(
                diffs=[
                    FileDiff(
                        filename="README.md",
                        status="modified",
                        patch="@@ -1 +1 @@\n-teh\n+the",
                    )
                ]
            )
        )

    assert mock.await_count == 1
    assert result["verdict"].confidence == "high"


@pytest.mark.asyncio
async def test_no_challenge_when_confidence_not_high():
    verdict = EvaluateResponse(
        action="verdict", summary="Some doubts", confidence="medium"
    )
    mock = AsyncMock(return_value=verdict)

    with patch("backend.agent.nodes.invoke_structured", new=mock):
        result = await evaluate_node(_substantial_state())

    assert mock.await_count == 1
    assert result["verdict"].confidence == "medium"


@pytest.mark.asyncio
async def test_missing_file_does_not_abort_the_review():
    """A guessed path that does not exist should cost budget, not kill the run."""
    state = _initial_state(
        pending_file_request="core/src/main/java/.../Duration.java",
        pending_reason="verify Duration.parse()",
    )
    client = GitHubClient(token="test")
    with patch.object(
        client,
        "get_file_content",
        new=AsyncMock(side_effect=GitHubAPIError(404, "Not Found")),
    ):
        result = await fetch_file_node(state, client)

    assert "core/src/main/java/.../Duration.java" in result["unavailable_files"]
    assert result["investigation_count"] == 1
    assert result["feedback_note"] is not None
    assert "investigation_trail" not in result


@pytest.mark.asyncio
async def test_auth_and_rate_limit_errors_still_propagate():
    state = _initial_state(
        pending_file_request="helpers.py", pending_reason="need it"
    )
    client = GitHubClient(token="test")
    with patch.object(
        client,
        "get_file_content",
        new=AsyncMock(side_effect=GitHubAPIError(403, "rate limit exceeded")),
    ):
        with pytest.raises(GitHubAPIError):
            await fetch_file_node(state, client)


@pytest.mark.asyncio
async def test_known_missing_path_is_not_refetched():
    state = _initial_state(unavailable_files={"nope.py": "Not Found"})
    repeat = EvaluateResponse(
        action="investigate", file_path="nope.py", reason="try again"
    )
    with patch("backend.agent.nodes.invoke_structured", new=AsyncMock(return_value=repeat)):
        result = await evaluate_node(state)

    assert result["pending_file_request"] is None
    assert "known missing" in result["feedback_note"]


@pytest.mark.asyncio
async def test_graph_recovers_from_missing_file_and_still_reviews():
    first_guess = EvaluateResponse(
        action="investigate", file_path="java/time/Duration.java", reason="verify parse()"
    )
    give_up = EvaluateResponse(
        action="verdict",
        summary="Duration is a JDK class; change looks correct",
        confidence="medium",
    )
    client = GitHubClient(token="test")
    with (
        patch(
            "backend.agent.nodes.invoke_structured",
            new=AsyncMock(side_effect=[first_guess, give_up]),
        ),
        patch.object(
            client,
            "get_file_content",
            new=AsyncMock(side_effect=GitHubAPIError(404, "Not Found")),
        ),
    ):
        graph = build_review_graph(client)
        result = await graph.ainvoke(_initial_state(), config={"recursion_limit": 25})

    assert result["verdict"].confidence == "medium"
    assert result["investigation_trail"] == []
    assert "java/time/Duration.java" in result["unavailable_files"]


@pytest.mark.asyncio
async def test_no_challenge_after_an_investigation():
    verdict = EvaluateResponse(action="verdict", summary="Checked it", confidence="high")
    mock = AsyncMock(return_value=verdict)

    with patch("backend.agent.nodes.invoke_structured", new=mock):
        result = await evaluate_node(
            _substantial_state(
                investigation_count=1,
                fetched_files={"wall.ts": "export const x = 1"},
            )
        )

    assert mock.await_count == 1
    assert result["verdict"].summary == "Checked it"


def _multi_file_state(**overrides):
    return _initial_state(
        diffs=[
            FileDiff(
                filename="core/src/main/java/com/example/DateTimeUtils.java",
                status="modified",
                patch="@@ x @@",
                changes=40,
            ),
            FileDiff(
                filename="core/src/test/java/com/example/DateTimeUtilsTest.java",
                status="modified",
                patch="@@ x @@",
                changes=30,
            ),
            FileDiff(
                filename="ui-next/src/pages/agent/Skills.tsx",
                status="modified",
                patch="@@ x @@",
                changes=25,
            ),
        ],
        **overrides,
    )


@pytest.mark.asyncio
async def test_multi_file_verdict_is_forced_to_investigate():
    stubborn = EvaluateResponse(action="verdict", summary="All good", confidence="high")
    mock = AsyncMock(side_effect=[stubborn, stubborn])

    with patch("backend.agent.nodes.invoke_structured", new=mock):
        result = await evaluate_node(_multi_file_state())

    assert mock.await_count == 2
    assert (
        result["pending_file_request"]
        == "core/src/main/java/com/example/DateTimeUtils.java"
    )
    assert "Mandatory first investigation" in result["pending_reason"]


def test_pick_investigation_target_prefers_non_test_file():
    diffs = [
        FileDiff(filename="core/src/test/FooTest.java", status="modified", changes=50),
        FileDiff(filename="core/src/main/Foo.java", status="modified", changes=20),
    ]
    assert _pick_investigation_target(diffs) == "core/src/main/Foo.java"
