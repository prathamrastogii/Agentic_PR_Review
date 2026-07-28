from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agent.actions import EvaluateResponse
from backend.agent.llm import (
    StructuredOutputError,
    failed_tool_call_payload,
    invoke_structured,
)
from backend.models.review import ReviewVerdict


class FakeGroqBadRequest(Exception):
    """Stands in for groq.BadRequestError, which carries the payload on `.body`."""

    def __init__(self, failed_generation: str):
        super().__init__("Error code: 400 - tool call validation failed")
        self.body = {
            "error": {
                "message": "tool call validation failed: parameters for tool "
                "EvaluateResponse did not match schema",
                "type": "invalid_request_error",
                "code": "tool_use_failed",
                "failed_generation": failed_generation,
            }
        }


REJECTED_GENERATION = (
    '<function=EvaluateResponse>{"action": "verdict", "confidence": "low", '
    '"partial_investigation": "true", "summary": "Unable to complete full '
    'investigation within budget.", "issues": [], "reason": "null", '
    '"file_path": "null"}</function>'
)


def fake_llm(structured_side_effect=None, raw_side_effect=None):
    """`with_structured_output` is sync, so the outer mock must not be async."""
    structured_llm = AsyncMock()
    structured_llm.ainvoke.side_effect = structured_side_effect
    llm = MagicMock()
    llm.with_structured_output.return_value = structured_llm
    llm.ainvoke = AsyncMock(side_effect=raw_side_effect)
    return llm, structured_llm


class TestFailedToolCallPayload:
    def test_extracts_generation_from_groq_error(self):
        exc = FakeGroqBadRequest(REJECTED_GENERATION)
        assert failed_tool_call_payload(exc) == REJECTED_GENERATION

    def test_ignores_unrelated_exception(self):
        assert failed_tool_call_payload(ValueError("boom")) is None

    def test_ignores_other_api_errors(self):
        exc = Exception("rate limited")
        exc.body = {"error": {"code": "rate_limit_exceeded"}}
        assert failed_tool_call_payload(exc) is None


class TestStringCoercion:
    def test_evaluate_response_coerces_string_bool_and_nulls(self):
        response = EvaluateResponse.model_validate(
            {
                "action": "verdict",
                "confidence": "low",
                "partial_investigation": "true",
                "summary": "Budget exhausted.",
                "issues": [],
                "reason": "null",
                "file_path": "null",
            }
        )
        assert response.partial_investigation is True
        assert response.file_path is None
        assert response.reason is None

    def test_verdict_coerces_string_bool(self):
        verdict = ReviewVerdict.model_validate(
            {
                "summary": "ok",
                "confidence": "high",
                "partial_investigation": "false",
                "issues": [],
            }
        )
        assert verdict.partial_investigation is False

    def test_issue_coerces_null_line(self):
        verdict = ReviewVerdict.model_validate(
            {
                "summary": "ok",
                "confidence": "high",
                "issues": [
                    {
                        "file": "app.py",
                        "line": "null",
                        "severity": "warning",
                        "category": "style",
                        "message": "nit",
                    }
                ],
            }
        )
        assert verdict.issues[0].line is None


@pytest.mark.asyncio
async def test_rejected_tool_call_is_salvaged():
    """The exact Groq 400 that previously escaped all retries and returned a 500."""
    llm, structured_llm = fake_llm(FakeGroqBadRequest(REJECTED_GENERATION))

    with patch("backend.agent.llm.get_llm", return_value=llm):
        result = await invoke_structured("system", "user", EvaluateResponse)

    assert result.action == "verdict"
    assert result.partial_investigation is True
    assert result.confidence == "low"
    assert structured_llm.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_unsalvageable_rejection_falls_back_to_raw_then_raises():
    llm, structured_llm = fake_llm(
        structured_side_effect=FakeGroqBadRequest("not json at all"),
        raw_side_effect=ValueError("still broken"),
    )

    with patch("backend.agent.llm.get_llm", return_value=llm):
        with pytest.raises(StructuredOutputError):
            await invoke_structured("system", "user", EvaluateResponse)

    assert structured_llm.ainvoke.await_count == 3
    assert llm.ainvoke.await_count == 3


@pytest.mark.asyncio
async def test_non_parse_errors_are_not_retried():
    llm, structured_llm = fake_llm(ConnectionError("network down"))

    with patch("backend.agent.llm.get_llm", return_value=llm):
        with pytest.raises(ConnectionError):
            await invoke_structured("system", "user", EvaluateResponse)

    assert structured_llm.ainvoke.await_count == 1
