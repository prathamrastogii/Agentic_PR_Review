"""Run a baseline (single-shot) PR review from the command line."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agent.baseline import review_pr_baseline
from backend.github.client import GitHubClient, parse_pr_url


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/baseline_review.py <pr_url>")
        sys.exit(1)

    pr_url = sys.argv[1]
    parsed = parse_pr_url(pr_url)
    client = GitHubClient()

    try:
        metadata = await client.get_pr_metadata(
            parsed.owner, parsed.repo, parsed.pr_number
        )
        files = await client.get_pr_files(parsed.owner, parsed.repo, parsed.pr_number)
        verdict = await review_pr_baseline(metadata, files)
        print(json.dumps(verdict.model_dump(), indent=2))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
