from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

NULL_STRINGS = {"null", "none", "nil", ""}
TRUE_STRINGS = {"true", "yes", "1"}
SEVERITY_ALIASES = {
    "high": "error",
    "critical": "error",
    "major": "error",
    "medium": "warning",
    "moderate": "warning",
    "low": "suggestion",
    "minor": "suggestion",
    "info": "suggestion",
}


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
        normalized = dict(data)
        if "line" in normalized:
            normalized["line"] = coerce_null(normalized["line"])
        if isinstance(normalized.get("severity"), str):
            severity = normalized["severity"].strip().lower()
            normalized["severity"] = SEVERITY_ALIASES.get(severity, severity)
        return normalized


class InvestigationStep(BaseModel):
    file_path: str
    reason: str


class ReviewInsights(BaseModel):
    """Executive summary buckets in plain language, distinct from file-level issues."""

    whats_good: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)


class ReviewVerdict(BaseModel):
    summary: str
    issues: list[ReviewIssue] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]
    confidence_score: int | None = None
    confidence_rationale: str | None = None
    confidence_tips: list[str] = Field(default_factory=list)
    pr_readiness: Literal["high", "medium", "low"] | None = None
    pr_readiness_score: int | None = None
    pr_readiness_rationale: str | None = None
    pr_readiness_tips: list[str] = Field(default_factory=list)
    partial_investigation: bool = False
    investigation_trail: list[InvestigationStep] = Field(default_factory=list)
    insights: ReviewInsights = Field(default_factory=ReviewInsights)

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "partial_investigation" not in data:
            return data
        return {
            **data,
            "partial_investigation": coerce_bool(data["partial_investigation"]),
        }
