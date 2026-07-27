from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.models.review import ReviewIssue, ReviewVerdict


class EvaluateResponse(BaseModel):
    action: Literal["investigate", "verdict"]
    file_path: str | None = None
    reason: str | None = None
    summary: str | None = None
    issues: list[ReviewIssue] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] | None = None
    partial_investigation: bool = False

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
