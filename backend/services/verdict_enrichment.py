"""Post-process review verdicts: backfill issues and compute contextual confidence."""

from __future__ import annotations

import re
from typing import Literal

from backend.github.models import FileDiff, PRMetadata
from backend.models.review import ReviewIssue, ReviewVerdict

MODEL_BASE_SCORE = {"high": 62, "medium": 48, "low": 32}
CONFIDENCE_TIPS_THRESHOLD = 80
READINESS_TIPS_THRESHOLD = 55

ERROR_KEYWORDS = re.compile(
    r"\b(bug|crash|security|vulnerab|exploit|data loss|incorrect|wrong|break|"
    r"null pointer|npe|race condition|deadlock)\b",
    re.IGNORECASE,
)

PLACEHOLDER_PATCH = re.compile(
    r"\b(total work done|todo|fixme|placeholder|lorem ipsum|wip|coming soon)\b",
    re.IGNORECASE,
)

BRANCH_PREFIX_HINTS = (
    ("fix/", "Branch suggests a bug fix"),
    ("fix-", "Branch suggests a bug fix"),
    ("feat/", "Branch suggests a new feature"),
    ("feature/", "Branch suggests a new feature"),
    ("refactor/", "Branch suggests refactoring"),
    ("chore/", "Branch suggests maintenance"),
    ("docs/", "Branch suggests documentation changes"),
)


def enrich_verdict(
    metadata: PRMetadata,
    verdict: ReviewVerdict,
    files: list[FileDiff],
    *,
    mode: Literal["agent", "baseline"] = "agent",
) -> ReviewVerdict:
    """Normalize issues and attach a contextual confidence score."""
    verdict = sync_insights_to_issues(verdict, files)
    score, rationale, level = compute_confidence_score(metadata, verdict, files, mode=mode)
    tips = build_confidence_tips(metadata, verdict, files, score, mode=mode)
    readiness_score, readiness_rationale, readiness_level = compute_pr_readiness_score(
        metadata, verdict, files
    )
    readiness_tips = build_pr_readiness_tips(
        metadata, verdict, files, readiness_score
    )
    return verdict.model_copy(
        update={
            "confidence_score": score,
            "confidence_rationale": rationale,
            "confidence": level,
            "confidence_tips": tips,
            "pr_readiness_score": readiness_score,
            "pr_readiness_rationale": readiness_rationale,
            "pr_readiness": readiness_level,
            "pr_readiness_tips": readiness_tips,
        }
    )


def sync_insights_to_issues(
    verdict: ReviewVerdict,
    files: list[FileDiff],
) -> ReviewVerdict:
    """Backfill structured issues when the model only populated insight bullets."""
    if verdict.issues:
        return verdict

    default_file = _primary_changed_file(files)
    if not default_file:
        return verdict

    issues: list[ReviewIssue] = []
    for text in verdict.insights.risks:
        issues.append(
            ReviewIssue(
                file=_guess_file_from_text(text, files) or default_file,
                severity="error" if ERROR_KEYWORDS.search(text) else "warning",
                category=_guess_category(text),
                message=text,
            )
        )
    for text in verdict.insights.improvements:
        issues.append(
            ReviewIssue(
                file=_guess_file_from_text(text, files) or default_file,
                severity="suggestion",
                category="style",
                message=text,
            )
        )

    if not issues:
        return verdict

    return verdict.model_copy(update={"issues": issues})


def compute_confidence_score(
    metadata: PRMetadata,
    verdict: ReviewVerdict,
    files: list[FileDiff],
    *,
    mode: Literal["agent", "baseline"] = "agent",
) -> tuple[int, str, Literal["high", "medium", "low"]]:
    """Score how trustworthy the review is given the context the model had."""
    aim, _ = infer_pr_aim(metadata, files)
    richness, context_summary = _review_context_richness(
        metadata, verdict, files, mode=mode
    )
    calibration = _review_calibration_score(verdict, richness, files, mode=mode)

    score = round(richness * 0.35 + calibration * 0.65)
    score = max(8, min(96, score))

    if score >= 72:
        level: Literal["high", "medium", "low"] = "high"
    elif score >= 48:
        level = "medium"
    else:
        level = "low"

    if verdict.partial_investigation and level == "high" and richness < 70:
        level = "medium"

    rationale = _confidence_rationale(
        metadata, aim, verdict, score, context_summary, richness, calibration
    )
    return score, rationale, level


def _review_context_richness(
    metadata: PRMetadata,
    verdict: ReviewVerdict,
    files: list[FileDiff],
    *,
    mode: Literal["agent", "baseline"],
) -> tuple[int, str]:
    """Estimate how much evidence was available to the reviewer (0-100)."""
    score = 38.0
    file_count = len(files)
    total_changes = sum(item.changes for item in files)
    trail_len = len(verdict.investigation_trail)
    parts: list[str] = ["PR diff"]

    if _has_description(metadata):
        score += 14
        parts.append("description")
    elif file_count > 1:
        score -= 3

    if file_count == 1:
        score += 28
        parts.append("single-file change (diff likely complete)")
    elif file_count == 2 and total_changes < 120:
        score += 12
    elif file_count >= 5:
        score -= 10
        parts.append(f"{file_count} changed files")

    if total_changes > 300:
        score -= 12
        parts.append("large diff")

    if trail_len:
        score += min(24, trail_len * 8)
        parts.append(f"{trail_len} supporting file(s)")

    if mode == "baseline":
        score += 6
        parts.append("baseline mode (diff-only by design)")

    if verdict.partial_investigation:
        score -= 16
        parts.append("partial run")

    if mode == "agent" and file_count >= 3 and trail_len == 0:
        score -= 12
        parts.append("no files beyond diff on a multi-file PR")
    elif mode == "agent" and file_count >= 6 and trail_len < 2:
        score -= 8

    missing_patches = sum(1 for item in files if not item.patch)
    if missing_patches:
        score -= min(10, missing_patches * 5)
        parts.append("some file patches unavailable")

    richness = max(10, min(95, round(score)))
    summary = ", ".join(parts)
    return richness, summary


def _review_calibration_score(
    verdict: ReviewVerdict,
    richness: int,
    files: list[FileDiff],
    *,
    mode: Literal["agent", "baseline"],
) -> float:
    """Adjust model-stated confidence to match available evidence."""
    score = float(MODEL_BASE_SCORE.get(verdict.confidence, MODEL_BASE_SCORE["low"]))
    file_count = len(files)

    if verdict.confidence == "high" and richness < 55:
        score -= 20
    elif verdict.confidence == "high" and richness < 68 and file_count > 1:
        score -= 10

    if verdict.confidence == "low" and richness >= 72:
        score += 8

    if verdict.partial_investigation:
        if verdict.confidence == "high":
            score -= 14
        else:
            score -= 6

    if verdict.issues:
        has_blocking = any(issue.severity == "error" for issue in verdict.issues)
        if file_count == 1 and has_blocking and verdict.confidence == "high":
            score += 14
        elif richness >= 60 and verdict.confidence in ("high", "medium"):
            score += 8
        elif richness >= 52 and file_count == 1 and verdict.confidence in ("high", "medium"):
            score += 8
        elif verdict.confidence == "low":
            score += 4

    trail_len = len(verdict.investigation_trail)
    if (
        mode == "agent"
        and file_count >= 4
        and trail_len == 0
        and verdict.confidence == "high"
        and not verdict.issues
    ):
        score -= 12

    return max(18, min(95, score))


def compute_pr_readiness_score(
    metadata: PRMetadata,
    verdict: ReviewVerdict,
    files: list[FileDiff],
) -> tuple[int, str, Literal["high", "medium", "low"]]:
    """Score how safe the PR looks to merge given findings, aim, and diff shape."""
    aim, _ = infer_pr_aim(metadata, files)
    score = 100.0

    errors = sum(1 for issue in verdict.issues if issue.severity == "error")
    warnings = sum(1 for issue in verdict.issues if issue.severity == "warning")
    suggestions = sum(1 for issue in verdict.issues if issue.severity == "suggestion")

    score -= errors * 28
    score -= warnings * 10
    score -= suggestions * 3

    covered_risk_messages = {
        issue.message.strip().lower()
        for issue in verdict.issues
        if issue.severity in ("error", "warning")
    }
    extra_risks = [
        risk
        for risk in verdict.insights.risks
        if risk.strip().lower() not in covered_risk_messages
    ]
    score -= min(16, len(extra_risks) * 8)

    suspicious_penalty = _suspicious_diff_penalty(files)
    score -= suspicious_penalty * 1.5

    if suspicious_penalty and _looks_like_feature_pr(metadata):
        score -= 12

    if not _has_description(metadata):
        score -= 6

    src_files = [
        item
        for item in files
        if item.status != "removed" and not _is_test_file(item.filename)
    ]
    test_files = [item for item in files if _is_test_file(item.filename)]
    if len(src_files) >= 2 and not test_files:
        score -= 8

    if (
        not verdict.issues
        and not verdict.insights.risks
        and suspicious_penalty == 0
    ):
        score = min(98, score + 2)

    score = max(5, min(98, round(score)))

    if score >= 75:
        level: Literal["high", "medium", "low"] = "high"
    elif score >= 45:
        level = "medium"
    else:
        level = "low"

    rationale = _pr_readiness_rationale(metadata, aim, verdict, files, score)
    return score, rationale, level


def build_pr_readiness_tips(
    metadata: PRMetadata,
    verdict: ReviewVerdict,
    files: list[FileDiff],
    score: int,
) -> list[str]:
    """Actionable merge guidance when PR readiness is below the display threshold."""
    if score >= READINESS_TIPS_THRESHOLD:
        return []

    tips: list[str] = []

    errors = [issue for issue in verdict.issues if issue.severity == "error"]
    if errors:
        tips.append(
            f"Resolve {len(errors)} high-severity issue(s) before merging."
        )

    if _suspicious_diff_penalty(files):
        tips.append(
            "The diff looks destructive or placeholder-like. Confirm the intended "
            "implementation is present before merge."
        )

    if _looks_like_feature_pr(metadata) and not _test_files_for_pr(files):
        tips.append(
            "This looks like a feature change without test updates. Add or link tests "
            "that cover the new behavior."
        )

    if not _has_description(metadata):
        tips.append(
            "Add a PR description explaining what changed, why, and how you verified it."
        )

    if verdict.insights.improvements and score < 45:
        tips.append(
            "Address improvement items called out in the review, or document why they "
            "can wait until a follow-up PR."
        )

    if not tips:
        tips.append(
            "Review flagged risks in the summary and issues list before merging."
        )

    return tips[:5]


def build_confidence_tips(
    metadata: PRMetadata,
    verdict: ReviewVerdict,
    files: list[FileDiff],
    score: int,
    *,
    mode: Literal["agent", "baseline"] = "agent",
) -> list[str]:
    """Tips when review trust is low relative to available context."""
    if score >= CONFIDENCE_TIPS_THRESHOLD:
        return []

    tips: list[str] = []
    richness, _ = _review_context_richness(metadata, verdict, files, mode=mode)
    trail_len = len(verdict.investigation_trail)
    file_count = len(files)
    total_changes = sum(item.changes for item in files)

    if mode == "agent" and file_count >= 3 and trail_len == 0:
        tips.append(
            "Only the PR diff was available for a multi-file change. Subtle cross-file "
            "issues may be missing from this review."
        )
    elif mode == "agent" and file_count >= 6 and trail_len < 2:
        tips.append(
            f"Limited supporting context ({trail_len} file(s) read across {file_count} "
            "changed files). Re-run after fetching key callees if the change is risky."
        )

    if not _has_description(metadata):
        tips.append(
            "No PR description was available to the reviewer. Intent and test notes were "
            "inferred from the title and diff only."
        )

    if verdict.partial_investigation:
        tips.append(
            "The review ended before the agent finished investigating. Findings may only "
            "cover what was visible at that point."
        )

    if total_changes > 300 and richness < 60:
        tips.append(
            "The diff is large relative to the context provided. Consider a smaller PR or "
            "re-running with agent mode so related files can be read."
        )

    if sum(1 for item in files if not item.patch) > 0:
        tips.append(
            "Some changed files had no inline patch in the review context. Those files "
            "were not fully visible to the model."
        )

    if verdict.confidence == "high" and richness < 55:
        tips.append(
            "The model claimed high confidence despite thin context. Treat non-obvious "
            "findings cautiously."
        )

    if not tips:
        tips.append(
            "Available context was limited for this PR shape. Manually verify findings "
            "that depend on code outside the diff."
        )

    return tips[:5]


def infer_pr_aim(metadata: PRMetadata, files: list[FileDiff]) -> tuple[str, int]:
    """Return a short aim summary and intent-clarity score (0–20)."""
    if _has_description(metadata):
        body = metadata.body.strip()
        clarity = 12 + min(8, len(body) // 40)
        first_line = body.splitlines()[0].strip()
        aim = first_line[:180] + ("…" if len(first_line) > 180 else "")
        return aim, clarity

    parts: list[str] = [f"Title: {metadata.title.strip()}"]
    branch_hint = _branch_intent_hint(metadata.head_ref)
    if branch_hint:
        parts.append(branch_hint)
    code_hint = _code_intent_hint(files)
    if code_hint:
        parts.append(code_hint)

    clarity = 6
    if len(metadata.title.strip()) > 20:
        clarity += 3
    if branch_hint:
        clarity += 4
    if code_hint:
        clarity += 4
    if _title_branch_overlap(metadata.title, metadata.head_ref):
        clarity += 3

    return ". ".join(parts), min(20, clarity)


def _has_description(metadata: PRMetadata) -> bool:
    return bool(metadata.body and metadata.body.strip())


def _branch_intent_hint(branch: str) -> str | None:
    normalized = branch.lower().replace("_", "-")
    for prefix, hint in BRANCH_PREFIX_HINTS:
        if normalized.startswith(prefix) or f"/{prefix}" in normalized:
            return hint
    if "refactor" in normalized:
        return "Branch suggests refactoring"
    if "deps" in normalized or "bump" in normalized:
        return "Branch suggests dependency updates"
    return None


def _code_intent_hint(files: list[FileDiff]) -> str | None:
    if not files:
        return None

    test_count = sum(1 for item in files if _is_test_file(item.filename))
    src_count = len(files) - test_count
    if test_count and src_count:
        return (
            f"Inferred from code: {src_count} source file(s) changed with "
            f"{test_count} test file(s)"
        )
    if test_count and not src_count:
        return "Inferred from code: test-only changes"

    doc_exts = {"md", "rst", "txt"}
    exts = {
        item.filename.rsplit(".", 1)[-1].lower()
        for item in files
        if "." in item.filename
    }
    if exts and exts <= doc_exts:
        return "Inferred from code: documentation-only changes"

    return f"Inferred from code: changes across {len(files)} file(s)"


def _title_branch_overlap(title: str, branch: str) -> bool:
    title_tokens = _tokenize(title)
    branch_tokens = _tokenize(branch.replace("/", " ").replace("-", " ").replace("_", " "))
    if not title_tokens or not branch_tokens:
        return False
    overlap = title_tokens & branch_tokens
    return len(overlap) >= 2 or (
        len(overlap) == 1 and len(next(iter(overlap))) >= 5
    )


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in {"the", "and", "for", "with", "from"}
    }


def _confidence_rationale(
    metadata: PRMetadata,
    aim: str,
    verdict: ReviewVerdict,
    score: int,
    context_summary: str,
    richness: int,
    calibration: float,
) -> str:
    partial = " Review ended early." if verdict.partial_investigation else ""
    return (
        f"{score}% review confidence: trust in this review given available context "
        f"({context_summary}; richness {richness}%, calibration {round(calibration)}%). "
        f'Aim context: "{aim[:90]}{"…" if len(aim) > 90 else ""}".{partial}'
    )


def _pr_readiness_rationale(
    metadata: PRMetadata,
    aim: str,
    verdict: ReviewVerdict,
    files: list[FileDiff],
    score: int,
) -> str:
    errors = sum(1 for issue in verdict.issues if issue.severity == "error")
    warnings = sum(1 for issue in verdict.issues if issue.severity == "warning")
    parts: list[str] = []
    if errors or warnings:
        parts.append(
            f"{errors} blocking and {warnings} moderate issue(s) flagged"
        )
    else:
        parts.append("no blocking issues flagged")

    if _suspicious_diff_penalty(files):
        parts.append("suspicious diff patterns detected")
    elif _looks_like_feature_pr(metadata):
        parts.append("change intent inferred from title/branch")

    detail = ", ".join(parts)
    return (
        f"{score}% PR readiness based on whether the change looks safe to merge "
        f'for its aim ("{aim[:100]}{"…" if len(aim) > 100 else ""}"): {detail}.'
    )


def _looks_like_feature_pr(metadata: PRMetadata) -> bool:
    hint = _branch_intent_hint(metadata.head_ref)
    if hint and "feature" in hint.lower():
        return True
    title = metadata.title.lower()
    return any(
        token in title
        for token in ("feat", "feature", "add ", "implement", "support", "introduce")
    )


def _test_files_for_pr(files: list[FileDiff]) -> list[FileDiff]:
    return [item for item in files if _is_test_file(item.filename)]


def _suspicious_diff_penalty(files: list[FileDiff]) -> int:
    """Penalize diffs that look like placeholders or mass deletions."""
    penalty = 0
    for item in files:
        deletions = item.deletions or 0
        additions = item.additions or 0
        changes = item.changes or (deletions + additions)
        patch = item.patch or ""

        if PLACEHOLDER_PATCH.search(patch):
            penalty += 12

        if deletions >= 30 and additions <= 3:
            penalty += 10
        elif deletions >= 10 and additions <= 1:
            penalty += 8

        if changes >= 40 and len(patch.strip()) < 120:
            penalty += 8

    return min(penalty, 24)


def _primary_changed_file(files: list[FileDiff]) -> str | None:
    candidates = [
        item
        for item in files
        if item.status != "removed" and not _is_test_file(item.filename)
    ] or [item for item in files if item.status != "removed"] or list(files)
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.changes, item.filename)).filename


def _guess_file_from_text(text: str, files: list[FileDiff]) -> str | None:
    lowered = text.lower()
    matches = [
        item.filename
        for item in files
        if item.filename.lower() in lowered
        or item.filename.rsplit("/", 1)[-1].lower() in lowered
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        return max(matches, key=len)
    return None


def _guess_category(text: str) -> Literal["correctness", "style", "security", "performance"]:
    lowered = text.lower()
    if any(word in lowered for word in ("security", "vulnerab", "auth", "injection", "xss")):
        return "security"
    if any(word in lowered for word in ("performance", "slow", "latency", "memory", "cpu")):
        return "performance"
    if any(word in lowered for word in ("style", "readability", "naming", "format")):
        return "style"
    return "correctness"


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
