import logging

from backend.agent.baseline import format_diffs
from backend.agent.actions import EvaluateResponse
from backend.agent.llm import StructuredOutputError, invoke_structured
from backend.agent.prompts import (
    BUDGET_EXHAUSTED_PROMPT,
    CHALLENGE_PROMPT,
    EVALUATE_SYSTEM_PROMPT,
)
from backend.agent.state import AgentState
from backend.github.client import GitHubAPIError, GitHubClient
from backend.github.models import FileDiff
from backend.models.review import InvestigationStep, ReviewVerdict

logger = logging.getLogger(__name__)

MAX_CHARS_FETCHED_FILE = 12000

# Diffs at or below this many changed lines are treated as trivial enough that a
# no-investigation verdict is credible.
TRIVIAL_DIFF_LINES = 10
# Multi-file PRs must open at least one file before a verdict is accepted.
MANDATORY_INVESTIGATION_MIN_FILES = 2

# A guessed path can legitimately not exist (standard library symbols, wrong
# directory). Those are the model's mistakes to recover from, unlike auth
# failures or rate limits, which must surface to the caller.
RECOVERABLE_FETCH_STATUSES = frozenset({400, 404, 422})


def _format_fetched_files(fetched_files: dict[str, str]) -> str:
    if not fetched_files:
        return "No additional files fetched yet."
    parts: list[str] = []
    for path, content in fetched_files.items():
        if len(content) > MAX_CHARS_FETCHED_FILE:
            content = content[:MAX_CHARS_FETCHED_FILE] + "\n... [file truncated]"
        parts.append(f"### {path}\n```\n{content}\n```")
    return "\n\n".join(parts)


def _format_unavailable(unavailable_files: dict[str, str]) -> str:
    if not unavailable_files:
        return ""
    lines = "\n".join(
        f"- {path}: {error}" for path, error in unavailable_files.items()
    )
    return (
        "\n\n## Paths that do not exist at this commit\n\n"
        f"{lines}\n\n"
        "Do not request these again. They may be standard library or third-party "
        "symbols, or the path may be wrong."
    )


def _format_trail(trail: list[InvestigationStep]) -> str:
    if not trail:
        return ""
    steps = "\n".join(
        f"{i}. {step.file_path} — {step.reason}" for i, step in enumerate(trail, 1)
    )
    return f"\n\n## Files you already investigated\n\n{steps}"


def changed_line_count(diffs: list[FileDiff]) -> int:
    """Size of the change in lines, falling back to counting patch lines."""
    reported = sum(diff.changes for diff in diffs)
    if reported:
        return reported
    return sum(
        1
        for diff in diffs
        if diff.patch
        for line in diff.patch.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )


def build_evaluate_prompt(state: AgentState) -> str:
    metadata = state["pr_metadata"]
    diff_text, truncated = format_diffs(state["diffs"])
    truncation_note = (
        "\n\nNote: Some diff content was truncated due to size limits."
        if truncated
        else ""
    )
    body_section = f"\nDescription:\n{metadata.body}" if metadata.body else ""
    remaining = state["max_investigations"] - state["investigation_count"]
    fetched_section = _format_fetched_files(state["fetched_files"])
    feedback_note = state.get("feedback_note")
    feedback_section = f"\n\n{feedback_note}" if feedback_note else ""

    return (
        f"Pull Request: {metadata.title}\n"
        f"Repository: {metadata.owner}/{metadata.repo}\n"
        f"Branch: {metadata.head_ref} → {metadata.base_ref}\n"
        f"URL: {metadata.html_url}"
        f"{body_section}\n\n"
        f"Investigation budget remaining: {remaining} of {state['max_investigations']}\n\n"
        f"## Changed files ({len(state['diffs'])})\n\n{diff_text}"
        f"{truncation_note}"
        f"{_format_trail(state['investigation_trail'])}\n\n"
        f"## Fetched files\n\n{fetched_section}"
        f"{_format_unavailable(state.get('unavailable_files') or {})}"
        f"{feedback_section}"
    )


def _fallback_verdict(state: AgentState) -> dict:
    """Honest low-confidence verdict for when the model never returns valid output.

    Preserves the investigation trail so completed work is not thrown away.
    """
    verdict = ReviewVerdict(
        summary=(
            "Review incomplete: the model did not return a valid structured verdict "
            "after several attempts. Any files listed in the investigation trail were "
            "fetched successfully, but no reliable findings could be produced."
        ),
        issues=[],
        confidence="low",
        partial_investigation=True,
        investigation_trail=list(state["investigation_trail"]),
    )
    return {
        "verdict": verdict,
        "pending_file_request": None,
        "pending_reason": None,
        "feedback_note": None,
    }


def _is_test_file(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    name = normalized.rsplit("/", 1)[-1]
    return (
        "/test/" in normalized
        or "/tests/" in normalized
        or normalized.startswith("test/")
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("test.java")
        or ".test." in name
        or ".spec." in name
    )


def _pick_investigation_target(diffs: list[FileDiff]) -> str:
    """Choose the most informative non-test file when the model skips investigation."""
    candidates = [
        diff
        for diff in diffs
        if diff.status != "removed" and not _is_test_file(diff.filename)
    ] or [diff for diff in diffs if diff.status != "removed"] or list(diffs)
    return max(candidates, key=lambda diff: (diff.changes, diff.filename)).filename


def _should_challenge(state: AgentState, response: EvaluateResponse) -> bool:
    """True when a verdict is suspicious without any file reads."""
    if response.action != "verdict" or state["investigation_count"] != 0:
        return False
    if len(state["diffs"]) >= MANDATORY_INVESTIGATION_MIN_FILES:
        return True
    return (
        response.confidence == "high"
        and changed_line_count(state["diffs"]) > TRIVIAL_DIFF_LINES
    )


def _enforce_mandatory_investigation(
    state: AgentState, response: EvaluateResponse
) -> EvaluateResponse:
    """Code-level backstop when the model still skips investigation on multi-file PRs."""
    if (
        response.action == "verdict"
        and state["investigation_count"] == 0
        and len(state["diffs"]) >= MANDATORY_INVESTIGATION_MIN_FILES
    ):
        target = _pick_investigation_target(state["diffs"])
        logger.warning(
            "> evaluate | forcing investigation of %s (%d files changed, 0 files read)",
            target,
            len(state["diffs"]),
        )
        return EvaluateResponse(
            action="investigate",
            file_path=target,
            reason=(
                "Mandatory first investigation: this PR changes multiple files and no "
                "out-of-diff context has been loaded yet."
            ),
        )
    return response


async def evaluate_node(state: AgentState) -> dict:
    budget_remaining = state["max_investigations"] - state["investigation_count"]
    user_prompt = build_evaluate_prompt(state)
    logger.info(
        "> evaluate | budget_remaining=%d fetched_files=%d prompt_chars=%d",
        budget_remaining,
        len(state["fetched_files"]),
        len(user_prompt),
    )

    if budget_remaining <= 0 and state["verdict"] is None:
        logger.warning(
            "> evaluate | budget exhausted, forcing best-effort final verdict"
        )
        system_prompt = f"{EVALUATE_SYSTEM_PROMPT}\n\n{BUDGET_EXHAUSTED_PROMPT}"
        try:
            response = await invoke_structured(
                system_prompt, user_prompt, EvaluateResponse, state.get("llm_config")
            )
        except StructuredOutputError as exc:
            logger.error("> evaluate | final verdict unusable, falling back: %s", exc)
            return _fallback_verdict(state)
        if response.action != "verdict":
            response = EvaluateResponse(
                action="verdict",
                summary=response.summary or "Unable to complete full investigation within budget.",
                issues=response.issues,
                confidence="low",
                partial_investigation=True,
            )
        verdict = response.to_verdict(state["investigation_trail"])
        verdict.partial_investigation = True
        if verdict.confidence == "high":
            verdict.confidence = "medium"
        logger.info(
            "> evaluate -> partial verdict | issues=%d confidence=%s",
            len(verdict.issues),
            verdict.confidence,
        )
        return {
            "verdict": verdict,
            "pending_file_request": None,
            "pending_reason": None,
            "feedback_note": None,
        }

    try:
        response = await invoke_structured(
            EVALUATE_SYSTEM_PROMPT, user_prompt, EvaluateResponse, state.get("llm_config")
        )
    except StructuredOutputError as exc:
        logger.error("> evaluate | no usable response, falling back: %s", exc)
        return _fallback_verdict(state)

    if _should_challenge(state, response):
        logger.info(
            "> evaluate | challenging unearned high confidence (%d changed lines, 0 files read)",
            changed_line_count(state["diffs"]),
        )
        try:
            response = await invoke_structured(
                f"{EVALUATE_SYSTEM_PROMPT}\n\n{CHALLENGE_PROMPT}",
                user_prompt,
                EvaluateResponse,
                state.get("llm_config"),
            )
        except StructuredOutputError as exc:
            logger.warning(
                "> evaluate | challenge unusable, keeping original verdict: %s", exc
            )
        else:
            logger.info("> evaluate | after challenge -> %s", response.action)

    response = _enforce_mandatory_investigation(state, response)

    if response.action == "verdict":
        verdict = response.to_verdict(state["investigation_trail"])
        logger.info(
            "> evaluate -> verdict | issues=%d confidence=%s",
            len(verdict.issues),
            verdict.confidence,
        )
        return {
            "verdict": verdict,
            "pending_file_request": None,
            "pending_reason": None,
            "feedback_note": None,
        }

    file_path = response.file_path or ""
    unavailable = state.get("unavailable_files") or {}
    if file_path in state["fetched_files"] or file_path in unavailable:
        already = "already fetched" if file_path in state["fetched_files"] else "known missing"
        logger.info(
            "> evaluate -> dedup | %r %s, re-evaluating without cost", file_path, already
        )
        return {
            "pending_file_request": None,
            "pending_reason": None,
            "feedback_note": (
                f"Note: You requested '{file_path}', which is {already}. "
                "Produce a verdict or request a different file."
            ),
        }

    logger.info(
        "> evaluate -> investigate | file=%s reason=%s", file_path, response.reason
    )
    return {
        "pending_file_request": file_path,
        "pending_reason": response.reason,
        "feedback_note": None,
    }


async def fetch_file_node(state: AgentState, github_client: GitHubClient) -> dict:
    path = state["pending_file_request"]
    reason = state["pending_reason"]
    if not path or not reason:
        logger.warning("> fetch_file | skipped, no pending file request in state")
        return {}

    metadata = state["pr_metadata"]
    logger.info(
        "> fetch_file | investigation %d/%d: %s @ %s",
        state["investigation_count"] + 1,
        state["max_investigations"],
        path,
        metadata.head_sha[:7],
    )
    try:
        content = await github_client.get_file_content(
            metadata.owner, metadata.repo, path, metadata.head_sha
        )
    except GitHubAPIError as exc:
        if exc.status_code not in RECOVERABLE_FETCH_STATUSES:
            raise
        logger.warning(
            "> fetch_file unavailable | %s (%d: %s), letting the agent try again",
            path,
            exc.status_code,
            exc.message,
        )
        return {
            "unavailable_files": {**(state.get("unavailable_files") or {}), path: exc.message},
            "investigation_count": state["investigation_count"] + 1,
            "pending_file_request": None,
            "pending_reason": None,
            "feedback_note": (
                f"Note: '{path}' could not be fetched ({exc.message}). It does not exist at "
                "this commit, so it may be a standard library or third-party symbol, or the "
                "path may be wrong. Request a different file or produce a verdict."
            ),
        }

    logger.info("> fetch_file done | %s (%d chars)", path, len(content))
    trail = list(state["investigation_trail"]) + [
        InvestigationStep(file_path=path, reason=reason)
    ]

    return {
        "fetched_files": {**state["fetched_files"], path: content},
        "investigation_count": state["investigation_count"] + 1,
        "investigation_trail": trail,
        "pending_file_request": None,
        "pending_reason": None,
        "feedback_note": None,
    }
