from typing import TypedDict

from backend.agent.providers import LLMConfig
from backend.github.models import FileDiff, PRMetadata
from backend.models.review import InvestigationStep, ReviewVerdict


class AgentState(TypedDict):
    pr_metadata: PRMetadata
    llm_config: LLMConfig | None
    diffs: list[FileDiff]
    fetched_files: dict[str, str]
    unavailable_files: dict[str, str]
    investigation_count: int
    max_investigations: int
    investigation_trail: list[InvestigationStep]
    pending_file_request: str | None
    pending_reason: str | None
    verdict: ReviewVerdict | None
    feedback_note: str | None
