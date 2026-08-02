"""Format PR file diffs for LLM prompts and measure prompt coverage."""

from backend.github.models import FileDiff

MAX_CHARS_PER_FILE = 8000
MAX_TOTAL_DIFF_CHARS = 50000


def _build_diff_blocks(files: list[FileDiff]) -> tuple[list[str], bool, int]:
    """Build per-file diff blocks. Returns (blocks, truncated, files_included)."""
    parts: list[str] = []
    total_chars = 0
    truncated = False
    included = 0

    for file_diff in files:
        header = f"### {file_diff.filename} ({file_diff.status})"
        if file_diff.patch:
            patch = file_diff.patch
            if len(patch) > MAX_CHARS_PER_FILE:
                patch = patch[:MAX_CHARS_PER_FILE] + "\n... [patch truncated]"
                truncated = True
            block = f"{header}\n```diff\n{patch}\n```"
        else:
            block = f"{header}\n(no patch; file may be binary or too large)"

        if total_chars + len(block) > MAX_TOTAL_DIFF_CHARS:
            parts.append("... [remaining files omitted due to size limit]")
            truncated = True
            break

        parts.append(block)
        total_chars += len(block)
        included += 1

    return parts, truncated, included


def diff_prompt_coverage(files: list[FileDiff]) -> tuple[bool, int, int]:
    """Return whether the prompt diff was truncated and how many files fit."""
    _, truncated, included = _build_diff_blocks(files)
    return truncated, included, len(files)


def format_diffs(files: list[FileDiff]) -> tuple[str, bool]:
    """Format file diffs for the prompt. Returns (text, was_truncated)."""
    parts, truncated, _ = _build_diff_blocks(files)
    return "\n\n".join(parts), truncated
