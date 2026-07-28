from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.github.client import GitHubAPIError
from backend.main import app
from backend.models.review import ReviewVerdict


def _sample_verdict(**overrides) -> ReviewVerdict:
    defaults = {
        "summary": "Looks good",
        "issues": [],
        "confidence": "high",
        "partial_investigation": False,
        "investigation_trail": [],
    }
    defaults.update(overrides)
    return ReviewVerdict(**defaults)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_index_html_served(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "PR review agent" in response.text


@pytest.mark.asyncio
async def test_static_assets_served(client):
    response = await client.get("/style.css")
    assert response.status_code == 200
    assert "EDE0CE" in response.text or "#ede0ce" in response.text.lower()


@pytest.mark.asyncio
@patch("backend.main.run_review", new_callable=AsyncMock)
async def test_review_agent_mode(mock_run_review, client):
    mock_run_review.return_value = _sample_verdict(
        investigation_trail=[{"file_path": "helpers.py", "reason": "check callee"}]
    )

    response = await client.post(
        "/api/review",
        json={
            "pr_url": "https://github.com/octo/repo/pull/1",
            "mode": "agent",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == "Looks good"
    assert len(payload["investigation_trail"]) == 1
    args, kwargs = mock_run_review.await_args
    assert args == ("https://github.com/octo/repo/pull/1", "agent")
    assert kwargs["llm_config"].provider == "groq"


@pytest.mark.asyncio
@patch("backend.main.run_review", new_callable=AsyncMock)
async def test_review_baseline_mode_default(mock_run_review, client):
    mock_run_review.return_value = _sample_verdict()

    response = await client.post(
        "/api/review",
        json={"pr_url": "https://github.com/octo/repo/pull/1"},
    )

    assert response.status_code == 200
    args, _ = mock_run_review.await_args
    assert args == ("https://github.com/octo/repo/pull/1", "agent")


@pytest.mark.asyncio
@patch("backend.main.run_review", new_callable=AsyncMock)
async def test_review_invalid_url_returns_400(mock_run_review, client):
    mock_run_review.side_effect = ValueError("Invalid GitHub PR URL")

    response = await client.post(
        "/api/review",
        json={"pr_url": "not-a-url", "mode": "baseline"},
    )

    assert response.status_code == 400
    assert "Invalid GitHub PR URL" in response.json()["detail"]


@pytest.mark.asyncio
@patch("backend.main.run_review", new_callable=AsyncMock)
async def test_review_pr_not_found_returns_404(mock_run_review, client):
    mock_run_review.side_effect = GitHubAPIError(404, "Not Found")

    response = await client.post(
        "/api/review",
        json={"pr_url": "https://github.com/octo/repo/pull/999"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"


@pytest.mark.asyncio
@patch("backend.main.run_review", new_callable=AsyncMock)
async def test_review_github_error_returns_502(mock_run_review, client):
    mock_run_review.side_effect = GitHubAPIError(403, "rate limit exceeded")

    response = await client.post(
        "/api/review",
        json={"pr_url": "https://github.com/octo/repo/pull/1"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "rate limit exceeded"


@pytest.mark.asyncio
@patch("backend.main.run_review", new_callable=AsyncMock)
async def test_llm_rate_limit_returns_429(mock_run_review, client):
    rate_limited = Exception("Error code: 429 - tokens per day exceeded")
    rate_limited.status_code = 429
    mock_run_review.side_effect = rate_limited

    response = await client.post(
        "/api/review",
        json={"pr_url": "https://github.com/octo/repo/pull/1"},
    )

    assert response.status_code == 429
    assert "rate limit" in response.json()["detail"].lower()


@pytest.mark.asyncio
@patch("backend.main.run_review", new_callable=AsyncMock)
async def test_unexpected_error_returns_500(mock_run_review, client):
    mock_run_review.side_effect = TypeError("something odd")

    response = await client.post(
        "/api/review",
        json={"pr_url": "https://github.com/octo/repo/pull/1"},
    )

    assert response.status_code == 500
    assert "TypeError" in response.json()["detail"]


@pytest.mark.asyncio
@patch("backend.main.run_review", new_callable=AsyncMock)
async def test_review_agent_failure_returns_500(mock_run_review, client):
    mock_run_review.side_effect = RuntimeError("Agent finished without producing a verdict")

    response = await client.post(
        "/api/review",
        json={"pr_url": "https://github.com/octo/repo/pull/1"},
    )

    assert response.status_code == 500
    assert "verdict" in response.json()["detail"]
