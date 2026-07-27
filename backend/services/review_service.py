from typing import Literal

from backend.agent.baseline import review_pr_baseline
from backend.agent.graph import run_agent_review
from backend.config import MAX_INVESTIGATIONS
from backend.github.client import GitHubClient, parse_pr_url
from backend.models.review import ReviewVerdict


async def run_review(
    pr_url: str,
    mode: Literal["agent", "baseline"] = "agent",
    *,
    github_client: GitHubClient | None = None,
    max_investigations: int | None = None,
) -> ReviewVerdict:
    parsed = parse_pr_url(pr_url)
    client = github_client or GitHubClient()
    owns_client = github_client is None

    try:
        metadata = await client.get_pr_metadata(
            parsed.owner, parsed.repo, parsed.pr_number
        )
        files = await client.get_pr_files(parsed.owner, parsed.repo, parsed.pr_number)

        if mode == "baseline":
            return await review_pr_baseline(metadata, files)

        budget = max_investigations if max_investigations is not None else MAX_INVESTIGATIONS
        return await run_agent_review(metadata, files, client, budget)
    finally:
        if owns_client:
            await client.close()
