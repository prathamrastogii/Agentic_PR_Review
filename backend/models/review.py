from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

NULL_STRINGS = {"null", "none", "nil", ""}
TRUE_STRINGS = {"true", "yes", "1"}


def coerce_bool(value: Any) -> Any:
    """Accept the stringified booleans Llama models often emit."""
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in TRUE_STRINGS:
            return True
        if lowered in NULL_STRINGS or lowered in {"false", "no", "0"}:
            return False
    return value


def coerce_null(value: Any) -> Any:
    """Turn the literal string "null" into a real None."""
    if isinstance(value, str) and value.strip().lower() in NULL_STRINGS:
        return None
    return value


class ReviewIssue(BaseModel):
    file: str
    line: int | None = None
    severity: Literal["error", "warning", "suggestion"]
    category: Literal["correctness", "style", "security", "performance"]
    message: str

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "line" in data:
            return {**data, "line": coerce_null(data["line"])}
        return data


class InvestigationStep(BaseModel):
    file_path: str
    reason: str


class ReviewVerdict(BaseModel):
    summary: str
    issues: list[ReviewIssue] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]
    partial_investigation: bool = False
    investigation_trail: list[InvestigationStep] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "partial_investigation" not in data:
            return data
        return {
            **data,
            "partial_investigation": coerce_bool(data["partial_investigation"]),
        }
