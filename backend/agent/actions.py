from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from backend.models.review import (
    ReviewIssue,
    ReviewVerdict,
    coerce_bool,
    coerce_null,
)

OPTIONAL_STRING_FIELDS = ("file_path", "reason", "summary", "confidence")


class EvaluateResponse(BaseModel):
    action: Literal["investigate", "verdict"]
    file_path: str | None = None
    reason: str | None = None
    summary: str | None = None
    issues: list[ReviewIssue] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] | None = None
    partial_investigation: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        for field in OPTIONAL_STRING_FIELDS:
            if field in normalized:
                normalized[field] = coerce_null(normalized[field])
        if "partial_investigation" in normalized:
            normalized["partial_investigation"] = coerce_bool(
                normalized["partial_investigation"]
            )
        return normalized

    @model_validator(mode="after")
    def validate_action_fields(self) -> "EvaluateResponse":
        if self.action == "investigate":
            if not self.file_path or not self.reason:
                raise ValueError("investigate action requires file_path and reason")
        elif self.action == "verdict":
            if not self.summary or not self.confidence:
                raise ValueError("verdict action requires summary and confidence")
        return self

    def to_verdict(self, trail: list) -> ReviewVerdict:
        return ReviewVerdict(
            summary=self.summary or "",
            issues=self.issues,
            confidence=self.confidence or "low",
            partial_investigation=self.partial_investigation,
            investigation_trail=list(trail),
        )
