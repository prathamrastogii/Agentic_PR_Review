from unittest.mock import AsyncMock, patch

import pytest

from backend.agent.actions import EvaluateResponse
from backend.agent.graph import build_review_graph, route_after_evaluate, run_agent_review
from backend.agent.nodes import evaluate_node, fetch_file_node
from backend.agent.state import AgentState
from backend.github.client import GitHubClient
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
        "diffs": [
            FileDiff(
                filename="app.py",
                status="modified",
                patch="@@ -1 +1 @@\n-from helpers import foo\n+from helpers import bar",
            )
        ],
        "fetched_files": {},
        "investigation_count": 0,
        "max_investigations": 2,
        "investigation_trail": [],
        "pending_file_request": None,
        "pending_reason": None,
        "verdict": None,
        "dedup_note": None,
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
        assert result["dedup_note"] is not None

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
