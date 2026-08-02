from unittest.mock import AsyncMock, patch

import pytest

from backend.agent.llm import LLMRateLimitError, is_rate_limit_error
from backend.agent.providers import LLMConfig, resolve_fallback_config
from backend.github.models import FileDiff, PRMetadata
from backend.models.review import ReviewVerdict
from backend.services.review_service import run_review


class RateLimitError(Exception):
    status_code = 429


class TestIsRateLimitError:
    def test_status_code_429(self):
        exc = Exception("quota")
        exc.status_code = 429
        assert is_rate_limit_error(exc)

    def test_groq_rate_limit_class_name(self):
        assert is_rate_limit_error(RateLimitError("tokens per day"))

    def test_other_errors_are_not_rate_limits(self):
        assert not is_rate_limit_error(ValueError("bad json"))


class TestResolveFallbackConfig:
    def test_google_falls_back_to_groq_by_default(self):
        google = LLMConfig(provider="google", model="m", api_key="k")
        with (
            patch("backend.config.LLM_FALLBACK_PROVIDER", None),
            patch("backend.config.GROQ_API_KEY", "groq-key"),
            patch("backend.config.GROQ_MODEL", "llama-3.3-70b-versatile"),
        ):
            fallback = resolve_fallback_config(google)

        assert fallback is not None
        assert fallback.provider == "groq"
        assert fallback.model == "llama-3.3-70b-versatile"

    def test_groq_falls_back_to_google_when_configured(self):
        groq = LLMConfig(provider="groq", model="m", api_key="k")
        with (
            patch("backend.config.LLM_FALLBACK_PROVIDER", "google"),
            patch("backend.config.GOOGLE_API_KEY", "google-key"),
            patch("backend.config.GOOGLE_MODEL", "gemini-3.5-flash-lite"),
        ):
            fallback = resolve_fallback_config(groq)

        assert fallback is not None
        assert fallback.provider == "google"
        assert fallback.model == "gemini-3.5-flash-lite"

    def test_no_fallback_when_already_on_groq(self):
        groq = LLMConfig(provider="groq", model="m", api_key="k")
        with patch("backend.config.LLM_FALLBACK_PROVIDER", None):
            assert resolve_fallback_config(groq) is None

    def test_no_fallback_when_already_on_google_with_explicit_fallback(self):
        google = LLMConfig(provider="google", model="m", api_key="k")
        with patch("backend.config.LLM_FALLBACK_PROVIDER", "google"):
            assert resolve_fallback_config(google) is None

    def test_returns_none_when_fallback_not_configured(self):
        groq = LLMConfig(provider="groq", model="m", api_key="k")
        with (
            patch("backend.config.LLM_FALLBACK_PROVIDER", "google"),
            patch("backend.config.GOOGLE_API_KEY", None),
        ):
            assert resolve_fallback_config(groq) is None


@pytest.mark.asyncio
async def test_run_review_restarts_with_gemini_when_groq_rate_limited():
    metadata = PRMetadata(
        owner="octo",
        repo="repo",
        pr_number=1,
        title="Test",
        body=None,
        base_ref="main",
        head_ref="feat",
        head_sha="abc123",
        html_url="https://github.com/octo/repo/pull/1",
    )
    files = [FileDiff(filename="app.py", status="modified", patch="+x")]
    verdict = ReviewVerdict(summary="Done on Gemini", confidence="medium")

    client = AsyncMock()
    client.get_pr_metadata.return_value = metadata
    client.get_pr_files.return_value = files
    client.close = AsyncMock()

    groq = LLMConfig(provider="groq", model="llama", api_key="g")
    gemini = LLMConfig(provider="google", model="gemini-3.5-flash-lite", api_key="gem")

    execute = AsyncMock(
        side_effect=[LLMRateLimitError("groq", "TPD exceeded"), verdict]
    )

    with (
        patch("backend.services.review_service._execute_review", new=execute),
        patch(
            "backend.services.review_service.resolve_fallback_config",
            return_value=gemini,
        ),
    ):
        result = await run_review(
            "https://github.com/octo/repo/pull/1",
            github_client=client,
            llm_config=groq,
        )

    assert result.summary == "Done on Gemini"
    assert execute.await_count == 2
    assert execute.await_args_list[0].kwargs["llm_config"].provider == "groq"
    assert execute.await_args_list[1].kwargs["llm_config"].provider == "google"


@pytest.mark.asyncio
async def test_run_review_propagates_when_fallback_also_rate_limited():
    client = AsyncMock()
    client.close = AsyncMock()
    groq = LLMConfig(provider="groq", model="llama", api_key="g")
    gemini = LLMConfig(provider="google", model="gemini", api_key="gem")

    execute = AsyncMock(
        side_effect=[
            LLMRateLimitError("groq", "TPD exceeded"),
            LLMRateLimitError("google", "quota exceeded"),
        ]
    )

    with (
        patch("backend.services.review_service._execute_review", new=execute),
        patch(
            "backend.services.review_service.resolve_fallback_config",
            return_value=gemini,
        ),
    ):
        with pytest.raises(LLMRateLimitError) as raised:
            await run_review(
                "https://github.com/octo/repo/pull/1",
                github_client=client,
                llm_config=groq,
            )
        assert raised.value.provider == "google"

    assert execute.await_count == 2
