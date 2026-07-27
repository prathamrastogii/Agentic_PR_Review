from typing import Literal

from pydantic import BaseModel, Field


class ReviewIssue(BaseModel):
    file: str
    line: int | None = None
    severity: Literal["error", "warning", "suggestion"]
    category: Literal["correctness", "style", "security", "performance"]
    message: str


class InvestigationStep(BaseModel):
    file_path: str
    reason: str


class ReviewVerdict(BaseModel):
    summary: str
    issues: list[ReviewIssue] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]
    partial_investigation: bool = False
    investigation_trail: list[InvestigationStep] = Field(default_factory=list)
