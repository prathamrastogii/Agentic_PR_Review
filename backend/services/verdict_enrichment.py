"""Post-process review verdicts: backfill issues and compute contextual confidence."""

from __future__ import annotations

import re
from typing import Literal

from backend.github.models import FileDiff, PRMetadata
from backend.models.review import ReviewIssue, ReviewVerdict

MODEL_BASE_SCORE = {"high": 62, "medium": 48, "low": 32}
CONFIDENCE_TIPS_THRESHOLD = 80

ERROR_KEYWORDS = re.compile(
    r"\b(bug|crash|security|vulnerab|exploit|data loss|incorrect|wrong|break|"
    r"null pointer|npe|race condition|deadlock)\b",
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
) -> ReviewVerdict:
    """Normalize issues and attach a contextual confidence score."""
    verdict = sync_insights_to_issues(verdict, files)
    score, rationale, level = compute_confidence_score(metadata, verdict, files)
    tips = build_confidence_tips(metadata, verdict, files, score)
    return verdict.model_copy(
        update={
            "confidence_score": score,
            "confidence_rationale": rationale,
            "confidence": level,
            "confidence_tips": tips,
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
) -> tuple[int, str, Literal["high", "medium", "low"]]:
    """Score how well the review aligns with PR intent and investigation depth."""
    aim, aim_clarity = infer_pr_aim(metadata, files)
    score = float(MODEL_BASE_SCORE.get(verdict.confidence, MODEL_BASE_SCORE["low"]))

    score += aim_clarity * 0.9

    if _has_description(metadata):
        score += 8

    if _title_branch_overlap(metadata.title, metadata.head_ref):
        score += 3

    trail_len = len(verdict.investigation_trail)
    if trail_len:
        score += min(10, trail_len * 2)
    elif len(files) > 2:
        score -= 8

    if verdict.partial_investigation:
        score -= 14

    if verdict.issues:
        score += min(6, len(verdict.issues) * 2)

    score = max(8, min(96, round(score)))
    level: Literal["high", "medium", "low"]
    if score >= 72:
        level = "high"
    elif score >= 48:
        level = "medium"
    else:
        level = "low"

    rationale = _confidence_rationale(metadata, aim, verdict, score)
    return score, rationale, level


def build_confidence_tips(
    metadata: PRMetadata,
    verdict: ReviewVerdict,
    files: list[FileDiff],
    score: int,
) -> list[str]:
    """Actionable tips when review confidence is below the display threshold."""
    if score >= CONFIDENCE_TIPS_THRESHOLD:
        return []

    tips: list[str] = []

    if not _has_description(metadata):
        tips.append(
            "Add a PR description with context, motivation, and how you tested the change."
        )

    if verdict.partial_investigation:
        tips.append(
            "Investigation stopped early. Re-run the review or manually verify files "
            "the agent could not fetch."
        )

    trail_len = len(verdict.investigation_trail)
    file_count = len(files)
    if file_count > 2 and trail_len == 0:
        tips.append(
            "This PR changes multiple files but no supporting files were investigated. "
            "Use agent mode and let it read callees and related modules."
        )
    elif file_count > 5 and trail_len < 2:
        tips.append(
            f"Only {trail_len} file(s) were investigated across {file_count} changed files. "
            "Investigate key imports or callers for higher confidence."
        )

    if not _title_branch_overlap(metadata.title, metadata.head_ref):
        tips.append(
            "Align the branch name and PR title so intent is obvious without reading the diff."
        )

    if len(metadata.title.strip()) < 25:
        tips.append(
            "Use a more specific PR title that states what changed and the expected outcome."
        )

    total_changes = sum(item.changes for item in files)
    if total_changes > 300:
        tips.append(
            "Large diffs are harder to review confidently. Split follow-up work into "
            "smaller PRs when feasible."
        )

    if not tips:
        tips.append(
            "Provide more PR context (description, linked issue, test notes) before merging."
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
) -> str:
    if _has_description(metadata):
        source = "the PR description"
    else:
        source = "the title, branch name, and changed files"

    trail_len = len(verdict.investigation_trail)
    if trail_len:
        depth = f"{trail_len} file(s) read beyond the diff"
    else:
        depth = "diff-only context"

    partial = " Partial investigation." if verdict.partial_investigation else ""
    return (
        f"{score}% confidence based on aim inferred from {source} "
        f'("{aim[:100]}{"…" if len(aim) > 100 else ""}") and review depth ({depth}).'
        f"{partial}"
    )


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
