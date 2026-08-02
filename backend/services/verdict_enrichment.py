"""Post-process review verdicts: backfill issues and compute contextual confidence."""

from __future__ import annotations

import re
from typing import Literal

from backend.github.diff_format import diff_prompt_coverage
from backend.github.models import FileDiff, PRMetadata
from backend.models.review import ReviewIssue, ReviewVerdict

MODEL_BASE_SCORE = {"high": 62, "medium": 48, "low": 32}

# Review confidence: combined score and level bands
CONFIDENCE_RICHNESS_WEIGHT = 0.35
CONFIDENCE_CALIBRATION_WEIGHT = 0.65
CONFIDENCE_SCORE_FLOOR = 8
CONFIDENCE_SCORE_CEILING = 96
CONFIDENCE_LEVEL_HIGH = 72
CONFIDENCE_LEVEL_MEDIUM = 48
CONFIDENCE_TIPS_THRESHOLD = 80
PARTIAL_RUN_HIGH_RICHNESS_CAP = 70

# Review confidence: context richness
RICHNESS_BASE = 38
RICHNESS_MIN = 10
RICHNESS_MAX = 95
RICHNESS_DESCRIPTION_BONUS = 14
RICHNESS_NO_DESCRIPTION_MULTI_FILE_PENALTY = 3
RICHNESS_SINGLE_FILE_BONUS = 28
RICHNESS_FULL_DIFF_VISIBILITY_BONUS_BASE = 12
RICHNESS_FULL_DIFF_VISIBILITY_BONUS_CAP = 20
RICHNESS_FULL_DIFF_VISIBILITY_FILE_DIVISOR = 4
RICHNESS_PARTIAL_DIFF_VISIBILITY_BONUS = 10
RICHNESS_LOW_DIFF_VISIBILITY_PENALTY = 8
RICHNESS_WIDE_PR_LOW_VISIBILITY_PENALTY = 6
RICHNESS_WIDE_PR_SCOPE_PENALTY = 2
RICHNESS_LARGE_DIFF_TRUNCATED_PENALTY = 10
RICHNESS_LARGE_DIFF_MILD_PENALTY = 3
RICHNESS_TRAIL_BONUS_PER_FILE = 4
RICHNESS_TRAIL_BONUS_CAP = 14
RICHNESS_BASELINE_MODE_BONUS = 6
RICHNESS_PARTIAL_RUN_PENALTY = 16
RICHNESS_NO_TRAIL_MULTI_FILE_PENALTY = 12
RICHNESS_SPARSE_TRAIL_PENALTY = 8
RICHNESS_HIGH_VISIBILITY_SPARSE_TRAIL_PENALTY = 3
RICHNESS_DIFF_TRUNCATED_PENALTY = 6
RICHNESS_MISSING_PATCH_PENALTY_PER_FILE = 5
RICHNESS_MISSING_PATCH_PENALTY_CAP = 10
LARGE_DIFF_LINE_THRESHOLD = 300

DIFF_VISIBILITY_HIGH = 0.95
DIFF_VISIBILITY_GOOD = 0.75
DIFF_VISIBILITY_PARTIAL = 0.85
DIFF_VISIBILITY_STRONG = 0.9

MULTI_FILE_MIN = 3
WIDE_PR_MIN_FILES = 6
LARGE_PR_MIN_FILES = 8
VERY_LARGE_PR_MIN_FILES = 10
SPARSE_TRAIL_MAX = 2

# Review confidence: calibration adjustments
CALIBRATION_MIN = 18
CALIBRATION_MAX = 95
CALIBRATION_HIGH_ON_THIN_CONTEXT_PENALTY = 20
CALIBRATION_HIGH_ON_MULTI_FILE_PENALTY = 10
CALIBRATION_LOW_ON_RICH_CONTEXT_BONUS = 8
CALIBRATION_PARTIAL_HIGH_PENALTY = 14
CALIBRATION_PARTIAL_OTHER_PENALTY = 6
CALIBRATION_SINGLE_FILE_BLOCKING_BONUS = 14
CALIBRATION_ISSUES_RICH_CONTEXT_BONUS = 8
CALIBRATION_ISSUES_MULTI_FILE_BONUS = 6
CALIBRATION_ISSUES_SINGLE_FILE_BONUS = 8
CALIBRATION_ISSUES_LOW_CONFIDENCE_BONUS = 4
CALIBRATION_DIFF_ONLY_HIGH_NO_ISSUES_PENALTY = 12
CALIBRATION_DIFF_ONLY_HIGH_NO_ISSUES_MILD_PENALTY = 6

RICHNESS_THIN_CONTEXT = 55
RICHNESS_MULTI_FILE_ISSUE_CONTEXT = 50
RICHNESS_MODERATE_CONTEXT = 60
RICHNESS_GOOD_CONTEXT = 68
RICHNESS_STRONG_CONTEXT = 72
RICHNESS_SINGLE_FILE_ISSUE_CONTEXT = 52
CALIBRATION_WIDE_PR_MIN_FILES = 4

# PR readiness: issue penalties and level bands
READINESS_START = 100
READINESS_SCORE_FLOOR = 5
READINESS_SCORE_CEILING = 98
READINESS_CLEAN_BONUS_CAP = 98
READINESS_CLEAN_BONUS = 2
READINESS_LEVEL_HIGH = 75
READINESS_LEVEL_MEDIUM = 45
READINESS_TIPS_THRESHOLD = 55
READINESS_TIPS_LOW_SCORE = 45
READINESS_ERROR_PENALTY = 28
READINESS_WARNING_PENALTY = 10
READINESS_SUGGESTION_PENALTY = 3
READINESS_UNCOVERED_RISK_PENALTY_PER = 8
READINESS_UNCOVERED_RISK_PENALTY_CAP = 16
READINESS_SUSPICIOUS_DIFF_MULTIPLIER = 1.5
READINESS_SUSPICIOUS_FEATURE_PENALTY = 12
READINESS_NO_DESCRIPTION_PENALTY = 6
READINESS_NO_TESTS_MULTI_SRC_PENALTY = 8
READINESS_MIN_SRC_FILES_FOR_TEST_EXPECTATION = 2

# Suspicious diff heuristics
SUSPICIOUS_PLACEHOLDER_PENALTY = 12
SUSPICIOUS_MASS_DELETE_PENALTY = 10
SUSPICIOUS_HEAVY_DELETE_PENALTY = 8
SUSPICIOUS_SHRUNK_PATCH_PENALTY = 8
SUSPICIOUS_MASS_DELETE_MIN_LINES = 30
SUSPICIOUS_MASS_DELETE_MAX_ADDITIONS = 3
SUSPICIOUS_HEAVY_DELETE_MIN_LINES = 10
SUSPICIOUS_HEAVY_DELETE_MAX_ADDITIONS = 1
SUSPICIOUS_SHRUNK_PATCH_MIN_CHANGES = 40
SUSPICIOUS_SHRUNK_PATCH_MAX_CHARS = 120
SUSPICIOUS_DIFF_PENALTY_CAP = 24

# PR aim / intent clarity (0-20)
AIM_CLARITY_DESCRIPTION_BASE = 12
AIM_CLARITY_DESCRIPTION_LENGTH_BONUS_CAP = 8
AIM_CLARITY_DESCRIPTION_CHARS_PER_POINT = 40
AIM_CLARITY_NO_DESCRIPTION_BASE = 6
AIM_CLARITY_LONG_TITLE_BONUS = 3
AIM_CLARITY_LONG_TITLE_MIN_CHARS = 20
AIM_CLARITY_BRANCH_HINT_BONUS = 4
AIM_CLARITY_CODE_HINT_BONUS = 4
AIM_CLARITY_TITLE_BRANCH_OVERLAP_BONUS = 3
AIM_CLARITY_MAX = 20
AIM_SUMMARY_MAX_CHARS = 180
AIM_RATIONALE_MAX_CHARS = 90
READINESS_AIM_MAX_CHARS = 100
TITLE_BRANCH_OVERLAP_MIN_TOKEN_LEN = 5

CONFIDENCE_TIPS_MAX = 5
READINESS_TIPS_MAX = 5

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

    score = round(
        richness * CONFIDENCE_RICHNESS_WEIGHT + calibration * CONFIDENCE_CALIBRATION_WEIGHT
    )
    score = max(CONFIDENCE_SCORE_FLOOR, min(CONFIDENCE_SCORE_CEILING, score))

    if score >= CONFIDENCE_LEVEL_HIGH:
        level: Literal["high", "medium", "low"] = "high"
    elif score >= CONFIDENCE_LEVEL_MEDIUM:
        level = "medium"
    else:
        level = "low"

    if (
        verdict.partial_investigation
        and level == "high"
        and richness < PARTIAL_RUN_HIGH_RICHNESS_CAP
    ):
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
    score = float(RICHNESS_BASE)
    file_count = len(files)
    total_changes = sum(item.changes for item in files)
    trail_len = len(verdict.investigation_trail)
    truncated, files_in_prompt, _ = diff_prompt_coverage(files)
    patch_ratio, with_patch, _ = _diff_patch_coverage(files)
    prompt_ratio = files_in_prompt / file_count if file_count else 1.0
    diff_visibility = min(patch_ratio, prompt_ratio)
    parts: list[str] = ["PR diff"]

    if _has_description(metadata):
        score += RICHNESS_DESCRIPTION_BONUS
        parts.append("description")
    elif file_count > 1:
        score -= RICHNESS_NO_DESCRIPTION_MULTI_FILE_PENALTY

    if file_count == 1:
        score += RICHNESS_SINGLE_FILE_BONUS
        parts.append("single-file change (diff likely complete)")
    elif diff_visibility >= DIFF_VISIBILITY_HIGH:
        score += min(
            RICHNESS_FULL_DIFF_VISIBILITY_BONUS_CAP,
            RICHNESS_FULL_DIFF_VISIBILITY_BONUS_BASE
            + file_count // RICHNESS_FULL_DIFF_VISIBILITY_FILE_DIVISOR,
        )
        parts.append(f"diff visible for {files_in_prompt}/{file_count} changed files")
    elif diff_visibility >= DIFF_VISIBILITY_GOOD:
        score += RICHNESS_PARTIAL_DIFF_VISIBILITY_BONUS
        parts.append(f"diff visible for {files_in_prompt}/{file_count} changed files")
    else:
        score -= RICHNESS_LOW_DIFF_VISIBILITY_PENALTY
        parts.append(
            f"limited diff visibility ({with_patch} patches, {files_in_prompt} in prompt)"
        )

    if file_count >= VERY_LARGE_PR_MIN_FILES and diff_visibility < DIFF_VISIBILITY_PARTIAL:
        score -= RICHNESS_WIDE_PR_LOW_VISIBILITY_PENALTY
    elif file_count >= VERY_LARGE_PR_MIN_FILES:
        score -= RICHNESS_WIDE_PR_SCOPE_PENALTY

    if total_changes > LARGE_DIFF_LINE_THRESHOLD:
        if truncated or diff_visibility < DIFF_VISIBILITY_STRONG:
            score -= RICHNESS_LARGE_DIFF_TRUNCATED_PENALTY
            parts.append("large diff with truncated or partial visibility")
        else:
            score -= RICHNESS_LARGE_DIFF_MILD_PENALTY
            parts.append("large diff")

    if trail_len:
        score += min(RICHNESS_TRAIL_BONUS_CAP, trail_len * RICHNESS_TRAIL_BONUS_PER_FILE)
        parts.append(f"{trail_len} file(s) read beyond diff")

    if mode == "baseline":
        score += RICHNESS_BASELINE_MODE_BONUS
        parts.append("baseline mode (diff-only by design)")

    if verdict.partial_investigation:
        score -= RICHNESS_PARTIAL_RUN_PENALTY
        parts.append("partial run")

    if (
        mode == "agent"
        and file_count >= MULTI_FILE_MIN
        and trail_len == 0
        and diff_visibility < DIFF_VISIBILITY_PARTIAL
    ):
        score -= RICHNESS_NO_TRAIL_MULTI_FILE_PENALTY
        parts.append("no files beyond diff on a multi-file PR")
    elif (
        mode == "agent"
        and file_count >= WIDE_PR_MIN_FILES
        and trail_len < SPARSE_TRAIL_MAX
        and diff_visibility < DIFF_VISIBILITY_PARTIAL
    ):
        score -= RICHNESS_SPARSE_TRAIL_PENALTY
    elif (
        mode == "agent"
        and file_count >= LARGE_PR_MIN_FILES
        and trail_len < SPARSE_TRAIL_MAX
        and diff_visibility >= DIFF_VISIBILITY_STRONG
    ):
        score -= RICHNESS_HIGH_VISIBILITY_SPARSE_TRAIL_PENALTY

    if truncated:
        score -= RICHNESS_DIFF_TRUNCATED_PENALTY
        parts.append("diff truncated at size limit")

    missing_patches = file_count - with_patch
    if missing_patches:
        score -= min(
            RICHNESS_MISSING_PATCH_PENALTY_CAP,
            missing_patches * RICHNESS_MISSING_PATCH_PENALTY_PER_FILE,
        )
        parts.append("some file patches unavailable")

    richness = max(RICHNESS_MIN, min(RICHNESS_MAX, round(score)))
    summary = ", ".join(parts)
    return richness, summary


def _diff_patch_coverage(files: list[FileDiff]) -> tuple[float, int, int]:
    with_patch = sum(1 for item in files if item.patch and item.patch.strip())
    total = len(files)
    ratio = with_patch / total if total else 1.0
    return ratio, with_patch, total


def _diff_visibility(files: list[FileDiff]) -> float:
    truncated, files_in_prompt, file_count = diff_prompt_coverage(files)
    patch_ratio, _, _ = _diff_patch_coverage(files)
    prompt_ratio = files_in_prompt / file_count if file_count else 1.0
    visibility = min(patch_ratio, prompt_ratio)
    if truncated and file_count:
        visibility = min(visibility, files_in_prompt / file_count)
    return visibility


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
    diff_visibility = _diff_visibility(files)

    if verdict.confidence == "high" and richness < RICHNESS_THIN_CONTEXT:
        score -= CALIBRATION_HIGH_ON_THIN_CONTEXT_PENALTY
    elif (
        verdict.confidence == "high"
        and richness < RICHNESS_GOOD_CONTEXT
        and file_count > 1
        and diff_visibility < DIFF_VISIBILITY_PARTIAL
    ):
        score -= CALIBRATION_HIGH_ON_MULTI_FILE_PENALTY

    if verdict.confidence == "low" and richness >= RICHNESS_STRONG_CONTEXT:
        score += CALIBRATION_LOW_ON_RICH_CONTEXT_BONUS

    if verdict.partial_investigation:
        if verdict.confidence == "high":
            score -= CALIBRATION_PARTIAL_HIGH_PENALTY
        else:
            score -= CALIBRATION_PARTIAL_OTHER_PENALTY

    if verdict.issues:
        has_blocking = any(issue.severity == "error" for issue in verdict.issues)
        if file_count == 1 and has_blocking and verdict.confidence == "high":
            score += CALIBRATION_SINGLE_FILE_BLOCKING_BONUS
        elif richness >= RICHNESS_MODERATE_CONTEXT and verdict.confidence in ("high", "medium"):
            score += CALIBRATION_ISSUES_RICH_CONTEXT_BONUS
        elif (
            richness >= RICHNESS_MULTI_FILE_ISSUE_CONTEXT
            and file_count > 1
            and diff_visibility >= DIFF_VISIBILITY_STRONG
            and verdict.confidence in ("high", "medium")
        ):
            score += CALIBRATION_ISSUES_MULTI_FILE_BONUS
        elif (
            richness >= RICHNESS_SINGLE_FILE_ISSUE_CONTEXT
            and file_count == 1
            and verdict.confidence in ("high", "medium")
        ):
            score += CALIBRATION_ISSUES_SINGLE_FILE_BONUS
        elif verdict.confidence == "low":
            score += CALIBRATION_ISSUES_LOW_CONFIDENCE_BONUS

    trail_len = len(verdict.investigation_trail)
    if (
        mode == "agent"
        and file_count >= CALIBRATION_WIDE_PR_MIN_FILES
        and trail_len == 0
        and verdict.confidence == "high"
        and not verdict.issues
    ):
        score -= (
            CALIBRATION_DIFF_ONLY_HIGH_NO_ISSUES_PENALTY
            if diff_visibility < DIFF_VISIBILITY_STRONG
            else CALIBRATION_DIFF_ONLY_HIGH_NO_ISSUES_MILD_PENALTY
        )

    return max(CALIBRATION_MIN, min(CALIBRATION_MAX, score))


def compute_pr_readiness_score(
    metadata: PRMetadata,
    verdict: ReviewVerdict,
    files: list[FileDiff],
) -> tuple[int, str, Literal["high", "medium", "low"]]:
    """Score how safe the PR looks to merge given findings, aim, and diff shape."""
    aim, _ = infer_pr_aim(metadata, files)
    score = float(READINESS_START)

    errors = sum(1 for issue in verdict.issues if issue.severity == "error")
    warnings = sum(1 for issue in verdict.issues if issue.severity == "warning")
    suggestions = sum(1 for issue in verdict.issues if issue.severity == "suggestion")

    score -= errors * READINESS_ERROR_PENALTY
    score -= warnings * READINESS_WARNING_PENALTY
    score -= suggestions * READINESS_SUGGESTION_PENALTY

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
    score -= min(
        READINESS_UNCOVERED_RISK_PENALTY_CAP,
        len(extra_risks) * READINESS_UNCOVERED_RISK_PENALTY_PER,
    )

    suspicious_penalty = _suspicious_diff_penalty(files)
    score -= suspicious_penalty * READINESS_SUSPICIOUS_DIFF_MULTIPLIER

    if suspicious_penalty and _looks_like_feature_pr(metadata):
        score -= READINESS_SUSPICIOUS_FEATURE_PENALTY

    if not _has_description(metadata):
        score -= READINESS_NO_DESCRIPTION_PENALTY

    src_files = [
        item
        for item in files
        if item.status != "removed" and not _is_test_file(item.filename)
    ]
    test_files = [item for item in files if _is_test_file(item.filename)]
    if len(src_files) >= READINESS_MIN_SRC_FILES_FOR_TEST_EXPECTATION and not test_files:
        score -= READINESS_NO_TESTS_MULTI_SRC_PENALTY

    if (
        not verdict.issues
        and not verdict.insights.risks
        and suspicious_penalty == 0
    ):
        score = min(READINESS_CLEAN_BONUS_CAP, score + READINESS_CLEAN_BONUS)

    score = max(READINESS_SCORE_FLOOR, min(READINESS_SCORE_CEILING, round(score)))

    if score >= READINESS_LEVEL_HIGH:
        level: Literal["high", "medium", "low"] = "high"
    elif score >= READINESS_LEVEL_MEDIUM:
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

    if verdict.insights.improvements and score < READINESS_TIPS_LOW_SCORE:
        tips.append(
            "Address improvement items called out in the review, or document why they "
            "can wait until a follow-up PR."
        )

    if not tips:
        tips.append(
            "Review flagged risks in the summary and issues list before merging."
        )

    return tips[:READINESS_TIPS_MAX]


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
    diff_visibility = _diff_visibility(files)

    if mode == "agent" and file_count >= MULTI_FILE_MIN and trail_len == 0:
        if diff_visibility < DIFF_VISIBILITY_PARTIAL:
            tips.append(
                "Only the PR diff was available for a multi-file change. Subtle cross-file "
                "issues may be missing from this review."
            )
        elif file_count >= LARGE_PR_MIN_FILES:
            tips.append(
                "Most changes are visible in the diff, but cross-file interactions outside "
                "those hunks were not verified."
            )
    elif (
        mode == "agent"
        and file_count >= WIDE_PR_MIN_FILES
        and trail_len < SPARSE_TRAIL_MAX
        and diff_visibility < DIFF_VISIBILITY_PARTIAL
    ):
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

    truncated, _, _ = diff_prompt_coverage(files)
    if truncated or (
        total_changes > LARGE_DIFF_LINE_THRESHOLD
        and diff_visibility < DIFF_VISIBILITY_STRONG
    ):
        if mode == "agent":
            if trail_len == 0:
                tips.append(
                    "The diff hit the size limit and the agent did not read any files "
                    "beyond it. Re-run and let the agent use its investigation budget on "
                    "key callees, or split this PR."
                )
            else:
                tips.append(
                    "The diff hit the size limit before all file content could be included. "
                    f"The agent only read {trail_len} supporting file(s). Split the PR or "
                    "raise the investigation budget for large cross-file changes."
                )
        else:
            tips.append(
                "The diff is large relative to the context provided. Consider a smaller PR "
                "or use agent mode so related files can be read beyond the truncated diff."
            )
    elif (
        total_changes > LARGE_DIFF_LINE_THRESHOLD
        and richness < RICHNESS_MODERATE_CONTEXT
        and mode == "baseline"
    ):
        tips.append(
            "The diff is large relative to the context provided. Use agent mode so related "
            "files can be read beyond the diff alone."
        )

    if sum(1 for item in files if not item.patch) > 0:
        tips.append(
            "Some changed files had no inline patch in the review context. Those files "
            "were not fully visible to the model."
        )

    if verdict.confidence == "high" and richness < RICHNESS_THIN_CONTEXT:
        tips.append(
            "The model's raw output claimed high confidence, but available context was "
            "thin after scoring. Treat non-obvious findings cautiously."
        )

    if not tips:
        tips.append(
            "Available context was limited for this PR shape. Manually verify findings "
            "that depend on code outside the diff."
        )

    return tips[:CONFIDENCE_TIPS_MAX]


def infer_pr_aim(metadata: PRMetadata, files: list[FileDiff]) -> tuple[str, int]:
    """Return a short aim summary and intent-clarity score (0–20)."""
    if _has_description(metadata):
        body = metadata.body.strip()
        clarity = AIM_CLARITY_DESCRIPTION_BASE + min(
            AIM_CLARITY_DESCRIPTION_LENGTH_BONUS_CAP,
            len(body) // AIM_CLARITY_DESCRIPTION_CHARS_PER_POINT,
        )
        first_line = body.splitlines()[0].strip()
        aim = first_line[:AIM_SUMMARY_MAX_CHARS] + (
            "…" if len(first_line) > AIM_SUMMARY_MAX_CHARS else ""
        )
        return aim, clarity

    parts: list[str] = [f"Title: {metadata.title.strip()}"]
    branch_hint = _branch_intent_hint(metadata.head_ref)
    if branch_hint:
        parts.append(branch_hint)
    code_hint = _code_intent_hint(files)
    if code_hint:
        parts.append(code_hint)

    clarity = AIM_CLARITY_NO_DESCRIPTION_BASE
    if len(metadata.title.strip()) > AIM_CLARITY_LONG_TITLE_MIN_CHARS:
        clarity += AIM_CLARITY_LONG_TITLE_BONUS
    if branch_hint:
        clarity += AIM_CLARITY_BRANCH_HINT_BONUS
    if code_hint:
        clarity += AIM_CLARITY_CODE_HINT_BONUS
    if _title_branch_overlap(metadata.title, metadata.head_ref):
        clarity += AIM_CLARITY_TITLE_BRANCH_OVERLAP_BONUS

    return ". ".join(parts), min(AIM_CLARITY_MAX, clarity)


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
        len(overlap) == 1 and len(next(iter(overlap))) >= TITLE_BRANCH_OVERLAP_MIN_TOKEN_LEN
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
        f'Aim context: "{aim[:AIM_RATIONALE_MAX_CHARS]}{"…" if len(aim) > AIM_RATIONALE_MAX_CHARS else ""}".{partial}'
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
        f'for its aim ("{aim[:READINESS_AIM_MAX_CHARS]}{"…" if len(aim) > READINESS_AIM_MAX_CHARS else ""}"): {detail}.'
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
            penalty += SUSPICIOUS_PLACEHOLDER_PENALTY

        if (
            deletions >= SUSPICIOUS_MASS_DELETE_MIN_LINES
            and additions <= SUSPICIOUS_MASS_DELETE_MAX_ADDITIONS
        ):
            penalty += SUSPICIOUS_MASS_DELETE_PENALTY
        elif (
            deletions >= SUSPICIOUS_HEAVY_DELETE_MIN_LINES
            and additions <= SUSPICIOUS_HEAVY_DELETE_MAX_ADDITIONS
        ):
            penalty += SUSPICIOUS_HEAVY_DELETE_PENALTY

        if (
            changes >= SUSPICIOUS_SHRUNK_PATCH_MIN_CHANGES
            and len(patch.strip()) < SUSPICIOUS_SHRUNK_PATCH_MAX_CHARS
        ):
            penalty += SUSPICIOUS_SHRUNK_PATCH_PENALTY

    return min(penalty, SUSPICIOUS_DIFF_PENALTY_CAP)


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
