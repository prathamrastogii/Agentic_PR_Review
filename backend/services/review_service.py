import logging
import time
from typing import Literal

from backend.agent.baseline import review_pr_baseline
from backend.agent.graph import run_agent_review
from backend.config import MAX_INVESTIGATIONS
from backend.github.client import GitHubClient, parse_pr_url
from backend.models.review import ReviewVerdict

logger = logging.getLogger(__name__)


async def run_review(
    pr_url: str,
    mode: Literal["agent", "baseline"] = "agent",
    *,
    github_client: GitHubClient | None = None,
    max_investigations: int | None = None,
) -> ReviewVerdict:
    started = time.perf_counter()
    logger.info("=== Review started | mode=%s url=%s", mode, pr_url)

    parsed = parse_pr_url(pr_url)
    logger.info(
        "Step 1/4 parse_url | %s/%s#%s", parsed.owner, parsed.repo, parsed.pr_number
    )

    client = github_client or GitHubClient()
    owns_client = github_client is None

    try:
        logger.info("Step 2/4 fetch_metadata | requesting PR details from GitHub")
        metadata = await client.get_pr_metadata(
            parsed.owner, parsed.repo, parsed.pr_number
        )
        logger.info(
            "Step 2/4 fetch_metadata done | title=%r head=%s (%s -> %s)",
            metadata.title,
            metadata.head_sha[:7],
            metadata.head_ref,
            metadata.base_ref,
        )

        logger.info("Step 3/4 fetch_diffs | requesting changed files from GitHub")
        files = await client.get_pr_files(parsed.owner, parsed.repo, parsed.pr_number)
        logger.info(
            "Step 3/4 fetch_diffs done | %d changed file(s): %s",
            len(files),
            ", ".join(f.filename for f in files[:5])
            + (" ..." if len(files) > 5 else ""),
        )

        if mode == "baseline":
            logger.info("Step 4/4 baseline_review | single LLM call, no tools")
            verdict = await review_pr_baseline(metadata, files)
        else:
            budget = (
                max_investigations
                if max_investigations is not None
                else MAX_INVESTIGATIONS
            )
            logger.info("Step 4/4 agent_review | investigation budget=%d", budget)
            verdict = await run_agent_review(metadata, files, client, budget)

        logger.info(
            "=== Review finished | mode=%s issues=%d confidence=%s partial=%s "
            "investigations=%d elapsed=%.1fs",
            mode,
            len(verdict.issues),
            verdict.confidence,
            verdict.partial_investigation,
            len(verdict.investigation_trail),
            time.perf_counter() - started,
        )
        return verdict
    except Exception as exc:
        logger.error(
            "=== Review failed after %.1fs | %s: %s",
            time.perf_counter() - started,
            type(exc).__name__,
            exc,
        )
        raise
    finally:
        if owns_client:
            await client.close()
