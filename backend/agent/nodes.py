import logging

from backend.agent.baseline import format_diffs
from backend.agent.actions import EvaluateResponse
from backend.agent.llm import StructuredOutputError, invoke_structured
from backend.agent.prompts import (
    BUDGET_EXHAUSTED_PROMPT,
    CHALLENGE_PROMPT,
    DIFF_ONLY_FORCED_PROMPT,
    EVALUATE_SYSTEM_PROMPT,
)
from backend.agent.state import AgentState
from backend.github.client import GitHubAPIError, GitHubClient
from backend.github.models import FileDiff
from backend.models.review import InvestigationStep, ReviewVerdict
from backend.services.review_events import emit_budget, emit_review_event

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

# Re-requesting an already-seen path costs no budget, so it needs its own cap or
# the evaluate -> evaluate edge can spin forever.
MAX_REDUNDANT_REQUESTS = 2


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


def _format_confirmed_paths(diffs: list[FileDiff]) -> str:
    paths = [
        diff.filename for diff in diffs if diff.status != "removed"
    ]
    if not paths:
        return ""
    listed = "\n".join(f"- {path}" for path in paths)
    return (
        "\n\n## Paths confirmed in this PR (safe to fetch)\n\n"
        f"{listed}\n"
    )


def _unfetchable_path_reason(path: str) -> str | None:
    if any(ch in path for ch in "*?[]"):
        return "GitHub cannot fetch glob patterns. Use an exact file path"
    normalized = path.replace("\\", "/").lower()
    for segment in (
        "/build/",
        "/target/",
        "/dist/",
        "/node_modules/",
        "/test-results/",
        "/out/",
    ):
        if segment in normalized:
            return "Build output paths are not stored in git"
    return None


def _case_correct_path(path: str, diffs: list[FileDiff]) -> str | None:
    """If the basename matches a changed file ignoring case, return the real path."""
    basename = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    matches = [
        diff.filename
        for diff in diffs
        if diff.filename.rsplit("/", 1)[-1].lower() == basename
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _reject_bad_fetch_request(
    state: AgentState, file_path: str
) -> dict | None:
    """Block invalid paths without spending investigation budget."""
    unavailable = state.get("unavailable_files") or {}
    if file_path in state["fetched_files"] or file_path in unavailable:
        return None

    reason = _unfetchable_path_reason(file_path)
    if reason is None:
        corrected = _case_correct_path(file_path, state["diffs"])
        if corrected and corrected != file_path:
            logger.info(
                "> evaluate -> case-corrected | %r -> %r", file_path, corrected
            )
            return {
                "pending_file_request": corrected,
                "pending_reason": f"Case-corrected path for {file_path}",
                "feedback_note": None,
            }
        return None

    logger.info("> evaluate -> rejected path | %r (%s)", file_path, reason)
    confirmed = ", ".join(
        diff.filename for diff in state["diffs"] if diff.status != "removed"
    )
    return {
        "unavailable_files": {**unavailable, file_path: reason},
        "pending_file_request": None,
        "pending_reason": None,
        "feedback_note": (
            f"Note: '{file_path}' cannot be fetched ({reason}). "
            f"Request an exact source path, or produce a verdict from the diff. "
            f"Paths confirmed in this PR: {confirmed or 'none'}."
        ),
    }


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
        f"Investigation budget remaining: {remaining} of {state['max_investigations']}\n"
        f"{_format_confirmed_paths(state['diffs'])}"
        f"\n## Changed files ({len(state['diffs'])})\n\n{diff_text}"
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


async def _forced_final_verdict(state: AgentState, user_prompt: str) -> dict:
    """Squeeze a best-effort verdict out of the model when the loop must stop now."""
    if not state["fetched_files"]:
        system_prompt = f"{EVALUATE_SYSTEM_PROMPT}\n\n{DIFF_ONLY_FORCED_PROMPT}"
    else:
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
            summary=response.summary
            or "Unable to complete full investigation within budget.",
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


async def _emit_investigate(file_path: str, reason: str | None) -> None:
    text = reason or "Requesting another file for context."
    await emit_review_event({"type": "thought", "text": text})
    await emit_review_event(
        {"type": "tool_call", "file": file_path, "reason": reason or text}
    )


async def _emit_tool_result(file_path: str, *, success: bool, note: str) -> None:
    await emit_review_event(
        {"type": "tool_result", "file": file_path, "success": success, "note": note}
    )


async def evaluate_node(state: AgentState) -> dict:
    budget_remaining = state["max_investigations"] - state["investigation_count"]
    user_prompt = build_evaluate_prompt(state)
    await emit_budget(state["investigation_count"], state["max_investigations"])
    await emit_review_event(
        {
            "type": "thought",
            "text": (
                "Analyzing the diff and deciding whether to investigate further "
                f"({budget_remaining} investigation(s) left)."
            ),
        }
    )
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
        await emit_review_event(
            {"type": "thought", "text": "Investigation budget exhausted. Writing final verdict."}
        )
        return await _forced_final_verdict(state, user_prompt)

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
        await emit_review_event(
            {
                "type": "thought",
                "text": (
                    f"Verdict ready: {len(verdict.issues)} issue(s), "
                    f"{verdict.confidence} confidence."
                ),
            }
        )
        return {
            "verdict": verdict,
            "pending_file_request": None,
            "pending_reason": None,
            "feedback_note": None,
        }

    file_path = response.file_path or ""
    rejected = _reject_bad_fetch_request(state, file_path)
    if rejected is not None:
        if rejected.get("pending_file_request"):
            await _emit_investigate(
                rejected["pending_file_request"],
                rejected.get("pending_reason"),
            )
        elif file_path in (rejected.get("unavailable_files") or {}):
            note = rejected["unavailable_files"][file_path]
            await _emit_tool_result(file_path, success=False, note=note)
            await emit_review_event(
                {
                    "type": "thought",
                    "text": rejected.get("feedback_note") or f"Cannot fetch {file_path}.",
                }
            )
        return rejected

    unavailable = state.get("unavailable_files") or {}
    if file_path in state["fetched_files"] or file_path in unavailable:
        already = "already fetched" if file_path in state["fetched_files"] else "known missing"
        redundant_count = state.get("redundant_request_count", 0) + 1

        if redundant_count > MAX_REDUNDANT_REQUESTS:
            logger.warning(
                "> evaluate | %d redundant request(s), forcing final verdict",
                redundant_count,
            )
            await emit_review_event(
                {
                    "type": "thought",
                    "text": "Repeated unavailable paths. Wrapping up with a partial verdict.",
                }
            )
            result = await _forced_final_verdict(state, user_prompt)
            result["redundant_request_count"] = redundant_count
            return result

        logger.info(
            "> evaluate -> dedup | %r %s, re-evaluating (%d/%d redundant)",
            file_path,
            already,
            redundant_count,
            MAX_REDUNDANT_REQUESTS,
        )
        await emit_review_event(
            {
                "type": "thought",
                "text": f"'{file_path}' is {already}. Choosing a different next step.",
            }
        )
        return {
            "pending_file_request": None,
            "pending_reason": None,
            "redundant_request_count": redundant_count,
            "feedback_note": (
                f"Note: You requested '{file_path}', which is {already}. Do not request "
                "it again. Request a file you have not seen yet, or produce a verdict now "
                "using the context you already have."
            ),
        }

    logger.info(
        "> evaluate -> investigate | file=%s reason=%s", file_path, response.reason
    )
    await _emit_investigate(file_path, response.reason)
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
    investigation_num = state["investigation_count"] + 1
    logger.info(
        "> fetch_file | investigation %d/%d: %s @ %s",
        investigation_num,
        state["max_investigations"],
        path,
        metadata.head_sha[:7],
    )
    await emit_budget(state["investigation_count"], state["max_investigations"])
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
        await _emit_tool_result(path, success=False, note=exc.message)
        new_count = state["investigation_count"] + 1
        await emit_budget(new_count, state["max_investigations"])
        return {
            "unavailable_files": {**(state.get("unavailable_files") or {}), path: exc.message},
            "investigation_count": new_count,
            "pending_file_request": None,
            "pending_reason": None,
            "feedback_note": (
                f"Note: '{path}' could not be fetched ({exc.message}). It does not exist at "
                "this commit, so it may be a standard library or third-party symbol, or the "
                "path may be wrong. Request a different file or produce a verdict."
            ),
        }

    logger.info("> fetch_file done | %s (%d chars)", path, len(content))
    await _emit_tool_result(
        path, success=True, note=f"Loaded {len(content):,} characters"
    )
    new_count = state["investigation_count"] + 1
    await emit_budget(new_count, state["max_investigations"])
    trail = list(state["investigation_trail"]) + [
        InvestigationStep(file_path=path, reason=reason)
    ]

    return {
        "fetched_files": {**state["fetched_files"], path: content},
        "investigation_count": new_count,
        "investigation_trail": trail,
        "pending_file_request": None,
        "pending_reason": None,
        "feedback_note": None,
    }
