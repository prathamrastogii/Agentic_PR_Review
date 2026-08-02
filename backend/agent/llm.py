import json
import logging
import re
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from backend.agent.providers import LLMConfig, build_chat_model, resolve_llm_config

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
MAX_RETRIES = 2

PARSE_ERRORS = (ValidationError, ValueError, json.JSONDecodeError, KeyError)

JSON_REPLY_INSTRUCTION = (
    "\n\nRespond with ONLY a single JSON object matching the schema below. "
    "No markdown fences, no extra text. "
    'Use lowercase action values "investigate" or "verdict". '
    "Booleans must be true/false (not strings). Use null for empty optional fields."
)


class StructuredOutputError(ValueError):
    """The model could not produce output matching the requested schema."""

    def __init__(self, message: str, *, raw_text: str | None = None):
        self.raw_text = raw_text
        super().__init__(message)


class LLMRateLimitError(Exception):
    """Upstream LLM quota exhausted (HTTP 429 or vendor-specific equivalent)."""

    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(message)


def is_rate_limit_error(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    return type(exc).__name__ in {"RateLimitError", "ResourceExhausted"}


def get_llm(llm_config: LLMConfig | None = None) -> BaseChatModel:
    return build_chat_model(llm_config or resolve_llm_config())


def extract_json(text: str) -> str:
    fence_match = JSON_FENCE_PATTERN.search(text)
    if fence_match:
        return fence_match.group(1).strip()
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return stripped
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        return brace_match.group(0)
    raise ValueError("No JSON object found in model response")


def parse_structured_response(text: str, model: type[T]) -> T:
    raw_json = extract_json(text)
    data = json.loads(raw_json)
    return model.model_validate(data)


def failed_tool_call_payload(exc: Exception) -> str | None:
    """Return the model's raw generation when Groq rejects a malformed tool call."""
    body: Any = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict) or error.get("code") != "tool_use_failed":
        return None
    generation = error.get("failed_generation")
    return generation if isinstance(generation, str) else None


def _schema_prompt(system_prompt: str, model: type[BaseModel]) -> str:
    schema = json.dumps(model.model_json_schema(), indent=2)
    return f"{system_prompt}{JSON_REPLY_INSTRUCTION}\n\nSchema:\n{schema}"


async def _invoke_json_mode(
    system_prompt: str,
    user_prompt: str,
    model: type[T],
    llm_config: LLMConfig,
) -> T:
    """Plain JSON generation, used for Gemini, which mis-names tool calls."""
    llm = get_llm(llm_config)
    messages = [
        SystemMessage(content=_schema_prompt(system_prompt, model)),
        HumanMessage(content=user_prompt),
    ]
    last_error: Exception | None = None
    last_raw_text: str | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await llm.ainvoke(messages)
            content = (
                response.content
                if isinstance(response.content, str)
                else str(response.content)
            )
            last_raw_text = content
            logger.debug("JSON mode response (attempt %d): %s", attempt + 1, content[:500])
            result = parse_structured_response(content, model)
            logger.info("  llm ok | attempt=%d schema=%s (json mode)", attempt + 1, model.__name__)
            return result
        except PARSE_ERRORS as exc:
            last_error = exc
            logger.warning("  llm json attempt %d failed: %s", attempt + 1, exc)
            messages.append(
                HumanMessage(
                    content="Your previous response was not valid JSON for the schema. "
                    "Reply with ONLY a corrected JSON object."
                )
            )
        except Exception as exc:
            if is_rate_limit_error(exc):
                raise LLMRateLimitError(llm_config.provider, str(exc)) from exc
            raise

    raise StructuredOutputError(
        f"Failed to parse structured response after retries: {last_error}",
        raw_text=last_raw_text,
    )


async def _invoke_tool_mode(
    system_prompt: str,
    user_prompt: str,
    model: type[T],
    llm_config: LLMConfig,
) -> T:
    """Native structured output via tool binding, reliable on Groq."""
    llm = get_llm(llm_config)
    structured_llm = llm.with_structured_output(model)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    last_error: Exception | None = None
    last_raw_text: str | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            result = await structured_llm.ainvoke(messages)
            logger.info("  llm ok | attempt=%d schema=%s", attempt + 1, model.__name__)
            if isinstance(result, model):
                return result
            return model.model_validate(result)
        except Exception as exc:
            rejected_generation = failed_tool_call_payload(exc)
            if rejected_generation is not None:
                last_raw_text = rejected_generation
                logger.warning(
                    "  llm attempt %d | Groq rejected the tool call, salvaging payload: %s",
                    attempt + 1,
                    rejected_generation[:300],
                )
                try:
                    salvaged = parse_structured_response(rejected_generation, model)
                except PARSE_ERRORS as parse_exc:
                    last_error = parse_exc
                    logger.warning("  llm attempt %d | salvage failed: %s", attempt + 1, parse_exc)
                else:
                    logger.info(
                        "  llm ok | attempt=%d recovered from rejected tool call", attempt + 1
                    )
                    return salvaged
            elif isinstance(exc, PARSE_ERRORS):
                last_error = exc
                logger.warning("  llm attempt %d | invalid structured output: %s", attempt + 1, exc)
            elif is_rate_limit_error(exc):
                raise LLMRateLimitError(llm_config.provider, str(exc)) from exc
            else:
                raise

    logger.info("  llm fallback | retrying with raw JSON parsing")
    try:
        return await _invoke_json_mode(system_prompt, user_prompt, model, llm_config)
    except StructuredOutputError as exc:
        if exc.raw_text is None and last_raw_text is not None:
            exc.raw_text = last_raw_text
        raise


def salvage_evaluate_response(raw_text: str) -> "EvaluateResponse | None":
    """Best-effort parse when strict structured output fails."""
    from backend.agent.actions import EvaluateResponse
    from backend.models.review import ReviewInsights, ReviewIssue

    try:
        data = json.loads(extract_json(raw_text))
    except (ValueError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    try:
        response = EvaluateResponse.model_validate(data)
    except ValidationError:
        response = None
    else:
        if response.action == "verdict":
            return response

    insights_raw = data.get("insights")
    insights = ReviewInsights()
    if isinstance(insights_raw, dict):
        try:
            insights = ReviewInsights.model_validate(insights_raw)
        except ValidationError:
            pass

    issues: list[ReviewIssue] = []
    issues_raw = data.get("issues")
    if isinstance(issues_raw, list):
        for item in issues_raw:
            if not isinstance(item, dict):
                continue
            try:
                issues.append(ReviewIssue.model_validate(item))
            except ValidationError:
                message = item.get("message")
                if message:
                    issues.append(
                        ReviewIssue(
                            file=str(item.get("file") or "unknown"),
                            severity="warning",
                            category="correctness",
                            message=str(message),
                        )
                    )

    has_insights = bool(insights.whats_good or insights.risks or insights.improvements)
    summary = data.get("summary")
    if not summary and not issues and not has_insights:
        return None

    confidence = data.get("confidence")
    if confidence not in ("high", "medium", "low"):
        confidence = "low"

    return EvaluateResponse(
        action="verdict",
        summary=summary or "Partial review based on incomplete model output.",
        confidence=confidence,
        issues=issues,
        insights=insights,
        partial_investigation=True,
    )


async def invoke_structured(
    system_prompt: str,
    user_prompt: str,
    model: type[T],
    llm_config: LLMConfig | None = None,
) -> T:
    llm_config = llm_config or resolve_llm_config()

    logger.info(
        "  llm call | provider=%s model=%s schema=%s",
        llm_config.provider,
        llm_config.model,
        model.__name__,
    )

    if llm_config.provider == "google":
        return await _invoke_json_mode(system_prompt, user_prompt, model, llm_config)

    return await _invoke_tool_mode(system_prompt, user_prompt, model, llm_config)
