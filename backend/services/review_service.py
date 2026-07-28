import logging
import time
from typing import Literal

from backend.agent.baseline import review_pr_baseline
from backend.agent.graph import run_agent_review
from backend.agent.llm import LLMRateLimitError
from backend.agent.providers import LLMConfig, resolve_fallback_config, resolve_llm_config
from backend.config import MAX_INVESTIGATIONS
from backend.github.client import GitHubClient, parse_pr_url
from backend.models.review import ReviewVerdict

logger = logging.getLogger(__name__)


async def _execute_review(
    pr_url: str,
    mode: Literal["agent", "baseline"],
    *,
    github_client: GitHubClient,
    max_investigations: int | None,
    llm_config: LLMConfig,
) -> ReviewVerdict:
    started = time.perf_counter()
    logger.info(
        "=== Review started | mode=%s provider=%s model=%s url=%s",
        mode,
        llm_config.provider,
        llm_config.model,
        pr_url,
    )

    parsed = parse_pr_url(pr_url)
    logger.info(
        "Step 1/4 parse_url | %s/%s#%s", parsed.owner, parsed.repo, parsed.pr_number
    )

    logger.info("Step 2/4 fetch_metadata | requesting PR details from GitHub")
    metadata = await github_client.get_pr_metadata(
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
    files = await github_client.get_pr_files(parsed.owner, parsed.repo, parsed.pr_number)
    logger.info(
        "Step 3/4 fetch_diffs done | %d changed file(s): %s",
        len(files),
        ", ".join(f.filename for f in files[:5])
        + (" ..." if len(files) > 5 else ""),
    )

    if mode == "baseline":
        logger.info("Step 4/4 baseline_review | single LLM call, no tools")
        verdict = await review_pr_baseline(metadata, files, llm_config)
    else:
        budget = (
            max_investigations if max_investigations is not None else MAX_INVESTIGATIONS
        )
        logger.info("Step 4/4 agent_review | investigation budget=%d", budget)
        verdict = await run_agent_review(metadata, files, github_client, budget, llm_config)

    logger.info(
        "=== Review finished | mode=%s provider=%s issues=%d confidence=%s partial=%s "
        "investigations=%d elapsed=%.1fs",
        mode,
        llm_config.provider,
        len(verdict.issues),
        verdict.confidence,
        verdict.partial_investigation,
        len(verdict.investigation_trail),
        time.perf_counter() - started,
    )
    return verdict


async def run_review(
    pr_url: str,
    mode: Literal["agent", "baseline"] = "agent",
    *,
    github_client: GitHubClient | None = None,
    max_investigations: int | None = None,
    llm_config: LLMConfig | None = None,
    allow_fallback: bool = True,
) -> ReviewVerdict:
    llm_config = llm_config or resolve_llm_config()
    client = github_client or GitHubClient()
    owns_client = github_client is None

    try:
        return await _execute_review(
            pr_url,
            mode,
            github_client=client,
            max_investigations=max_investigations,
            llm_config=llm_config,
        )
    except LLMRateLimitError as exc:
        if not allow_fallback:
            raise

        fallback = resolve_fallback_config(llm_config)
        if fallback is None:
            logger.error(
                "Provider %s rate-limited and no fallback is configured", exc.provider
            )
            raise

        logger.warning(
            "Provider %s rate-limited mid-review — restarting from scratch with %s/%s",
            exc.provider,
            fallback.provider,
            fallback.model,
        )
        return await run_review(
            pr_url,
            mode,
            github_client=client,
            max_investigations=max_investigations,
            llm_config=fallback,
            allow_fallback=False,
        )
    except Exception as exc:
        logger.error("=== Review failed | %s: %s", type(exc).__name__, exc)
        raise
    finally:
        if owns_client:
            await client.close()
