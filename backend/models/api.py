from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class LLMSettings(BaseModel):
    """Caller's LLM choice. Anything omitted falls back to server configuration.

    `api_key` is a SecretStr so it is masked in reprs and logs, and it is never
    stored or echoed back in a response.
    """

    provider: str | None = None
    model: str | None = None
    api_key: SecretStr | None = None


class ReviewRequest(BaseModel):
    pr_url: str = Field(..., min_length=1)
    mode: Literal["agent", "baseline"] = "agent"
    llm: LLMSettings | None = None
