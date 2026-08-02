"""Shared API/UI contract for review streams and verdict payloads.

Keep in sync with static/contract.js. tests/test_ui_contract.py enforces parity.
"""

from __future__ import annotations

from backend.services.verdict_enrichment import (
    CONFIDENCE_LEVEL_HIGH,
    CONFIDENCE_LEVEL_MEDIUM,
    CONFIDENCE_TIPS_THRESHOLD,
    READINESS_TIPS_THRESHOLD,
)

UI_CONTRACT_VERSION = 1

ISSUE_SEVERITIES = ("error", "warning", "suggestion")
ISSUE_CATEGORIES = ("correctness", "style", "security", "performance")
CONFIDENCE_LEVELS = ("high", "medium", "low")

STREAM_EVENT_TYPES = frozenset(
    {
        "ping",
        "done",
        "status",
        "thought",
        "pr_metadata",
        "budget",
        "tool_call",
        "tool_result",
        "error",
        "verdict",
    }
)

PR_METADATA_FIELDS = (
    "owner",
    "repo",
    "pr_number",
    "title",
    "html_url",
    "head_ref",
    "base_ref",
    "head_sha",
    "changed_files",
    "additions",
    "deletions",
)

VERDICT_REQUIRED_FIELDS = (
    "summary",
    "confidence",
    "issues",
    "insights",
    "investigation_trail",
    "partial_investigation",
)

VERDICT_OPTIONAL_FIELDS = (
    "confidence_score",
    "confidence_rationale",
    "confidence_tips",
    "pr_readiness",
    "pr_readiness_score",
    "pr_readiness_rationale",
    "pr_readiness_tips",
)

INSIGHT_FIELDS = ("whats_good", "risks", "improvements")

ISSUE_REQUIRED_FIELDS = ("file", "severity", "category", "message")

INVESTIGATION_STEP_FIELDS = ("file_path", "reason")

# Values mirrored in static/contract.js for the dashboard.
UI_THRESHOLDS = {
    "confidence_level_high": CONFIDENCE_LEVEL_HIGH,
    "confidence_level_medium": CONFIDENCE_LEVEL_MEDIUM,
    "confidence_tips_threshold": CONFIDENCE_TIPS_THRESHOLD,
    "readiness_tips_threshold": READINESS_TIPS_THRESHOLD,
}


def ui_contract_dict() -> dict:
    """Machine-readable contract for tests and optional API exposure."""
    return {
        "version": UI_CONTRACT_VERSION,
        "issue_severities": list(ISSUE_SEVERITIES),
        "issue_categories": list(ISSUE_CATEGORIES),
        "confidence_levels": list(CONFIDENCE_LEVELS),
        "stream_event_types": sorted(STREAM_EVENT_TYPES),
        "pr_metadata_fields": list(PR_METADATA_FIELDS),
        "verdict_required_fields": list(VERDICT_REQUIRED_FIELDS),
        "verdict_optional_fields": list(VERDICT_OPTIONAL_FIELDS),
        "insight_fields": list(INSIGHT_FIELDS),
        "issue_required_fields": list(ISSUE_REQUIRED_FIELDS),
        "investigation_step_fields": list(INVESTIGATION_STEP_FIELDS),
        "thresholds": dict(UI_THRESHOLDS),
    }
