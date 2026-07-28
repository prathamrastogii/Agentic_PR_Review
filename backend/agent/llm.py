import json
import logging
import re
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, ValidationError

from backend.config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
MAX_RETRIES = 2

PARSE_ERRORS = (ValidationError, ValueError, json.JSONDecodeError)


class StructuredOutputError(ValueError):
    """The model could not produce output matching the requested schema."""


def get_llm() -> ChatGroq:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is not set. Add it to your .env file.")
    return ChatGroq(model=GROQ_MODEL, api_key=GROQ_API_KEY, temperature=0)


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
    """Return the model's raw generation when Groq rejects a malformed tool call.

    Groq validates tool arguments server-side and answers with HTTP 400
    (`tool_use_failed`) instead of a parseable response. The generation itself is
    included in the error body and is usually salvageable after coercion.
    """
    body: Any = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict) or error.get("code") != "tool_use_failed":
        return None
    generation = error.get("failed_generation")
    return generation if isinstance(generation, str) else None


async def invoke_structured(
    system_prompt: str,
    user_prompt: str,
    model: type[T],
) -> T:
    llm = get_llm()
    structured_llm = llm.with_structured_output(model)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    logger.info("  llm call | model=%s schema=%s", GROQ_MODEL, model.__name__)
    last_error: Exception | None = None
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
                    logger.info("  llm ok | attempt=%d recovered from rejected tool call", attempt + 1)
                    return salvaged
            elif isinstance(exc, PARSE_ERRORS):
                last_error = exc
                logger.warning("  llm attempt %d | invalid structured output: %s", attempt + 1, exc)
            else:
                raise

    logger.info("  llm fallback | retrying with raw JSON parsing")
    raw_llm = get_llm()
    messages.append(
        HumanMessage(
            content="Your previous response was not valid JSON matching the required schema. "
            "Respond with ONLY a valid JSON object, no markdown fences or extra text. "
            "Use real JSON types: booleans must be true/false and empty values must be null, "
            "never the strings \"true\" or \"null\"."
        )
    )
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await raw_llm.ainvoke(messages)
            content = response.content if isinstance(response.content, str) else str(response.content)
            logger.debug("Raw model response (retry %d): %s", attempt + 1, content[:500])
            return parse_structured_response(content, model)
        except PARSE_ERRORS as exc:
            last_error = exc
            logger.warning("  llm raw parse attempt %d failed: %s", attempt + 1, exc)

    logger.error("All structured output attempts failed for schema %s", model.__name__)
    raise StructuredOutputError(
        f"Failed to parse structured response after retries: {last_error}"
    )
