import logging

from backend.github.diff_format import format_diffs
from backend.github.models import FileDiff, PRMetadata
from backend.models.review import ReviewVerdict

logger = logging.getLogger(__name__)


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
