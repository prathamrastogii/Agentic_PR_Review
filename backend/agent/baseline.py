import logging

from backend.github.models import FileDiff, PRMetadata
from backend.models.review import ReviewVerdict

logger = logging.getLogger(__name__)

MAX_CHARS_PER_FILE = 8000
MAX_TOTAL_DIFF_CHARS = 50000


def format_diffs(files: list[FileDiff]) -> tuple[str, bool]:
    """Format file diffs for the prompt. Returns (text, was_truncated)."""
    parts: list[str] = []
    total_chars = 0
    truncated = False

    for file_diff in files:
        header = f"### {file_diff.filename} ({file_diff.status})"
        if file_diff.patch:
            patch = file_diff.patch
            if len(patch) > MAX_CHARS_PER_FILE:
                patch = patch[:MAX_CHARS_PER_FILE] + "\n... [patch truncated]"
                truncated = True
            block = f"{header}\n```diff\n{patch}\n```"
        else:
            block = f"{header}\n(no patch — file may be binary or too large)"

        if total_chars + len(block) > MAX_TOTAL_DIFF_CHARS:
            parts.append("... [remaining files omitted due to size limit]")
            truncated = True
            break

        parts.append(block)
        total_chars += len(block)

    return "\n\n".join(parts), truncated


def build_baseline_prompt(metadata: PRMetadata, files: list[FileDiff]) -> str:
    diff_text, truncated = format_diffs(files)
    truncation_note = (
        "\n\nNote: Some diff content was truncated due to size limits. "
        "Flag uncertainty where truncated context may hide issues."
        if truncated
        else ""
    )
    body_section = f"\nDescription:\n{metadata.body}" if metadata.body else ""

    return (
        f"Pull Request: {metadata.title}\n"
        f"Repository: {metadata.owner}/{metadata.repo}\n"
        f"Branch: {metadata.head_ref} → {metadata.base_ref}\n"
        f"URL: {metadata.html_url}"
        f"{body_section}\n\n"
        f"## Changed files ({len(files)})\n\n{diff_text}"
        f"{truncation_note}"
    )


async def review_pr_baseline(
    metadata: PRMetadata,
    files: list[FileDiff],
    llm_config=None,
) -> ReviewVerdict:
    from backend.agent.llm import invoke_structured
    from backend.agent.prompts import BASELINE_SYSTEM_PROMPT

    user_prompt = build_baseline_prompt(metadata, files)
    logger.info(
        "> baseline | %d diff(s), prompt_chars=%d", len(files), len(user_prompt)
    )
    verdict = await invoke_structured(
        BASELINE_SYSTEM_PROMPT, user_prompt, ReviewVerdict, llm_config
    )
    verdict.investigation_trail = []
    verdict.partial_investigation = False
    logger.info(
        "> baseline done | issues=%d confidence=%s",
        len(verdict.issues),
        verdict.confidence,
    )
    return verdict
