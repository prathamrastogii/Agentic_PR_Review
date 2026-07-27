import json
import logging
import re
from typing import TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, ValidationError

from backend.config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
MAX_RETRIES = 2


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

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            result = await structured_llm.ainvoke(messages)
            if isinstance(result, model):
                return result
            return model.model_validate(result)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning("Structured output attempt %d failed: %s", attempt + 1, exc)

    raw_llm = get_llm()
    messages.append(
        HumanMessage(
            content="Your previous response was not valid JSON matching the required schema. "
            "Respond with ONLY a valid JSON object, no markdown fences or extra text."
        )
    )
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await raw_llm.ainvoke(messages)
            content = response.content if isinstance(response.content, str) else str(response.content)
            logger.debug("Raw model response (retry %d): %s", attempt + 1, content[:500])
            return parse_structured_response(content, model)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning("Raw parse attempt %d failed: %s", attempt + 1, exc)

    logger.error("All structured output attempts failed")
    raise ValueError(f"Failed to parse structured response after retries: {last_error}")
