import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from backend.agent.baseline import review_pr_baseline
from backend.agent.graph import run_agent_review
from backend.agent.llm import LLMRateLimitError
from backend.agent.providers import LLMConfig, resolve_fallback_config, resolve_llm_config
from backend.config import MAX_INVESTIGATIONS
from backend.github.client import GitHubClient, parse_pr_url
from backend.models.review import ReviewVerdict
from backend.services.verdict_enrichment import enrich_verdict
from backend.services.review_events import emit_review_event, review_event_scope

logger = logging.getLogger(__name__)

EmitFn = Callable[[dict[str, Any]], Awaitable[None]]


async def _execute_review(
    pr_url: str,
    mode: Literal["agent", "baseline"],
    *,
    github_client: GitHubClient,
    max_investigations: int | None,
    llm_config: LLMConfig,
    emit: EmitFn | None = None,
) -> ReviewVerdict:
    async with review_event_scope(emit):
        started = time.perf_counter()
        logger.info(
            "=== Review started | mode=%s provider=%s model=%s url=%s",
            mode,
            llm_config.provider,
            llm_config.model,
            pr_url,
        )
        await emit_review_event({"type": "status", "text": "Parsing pull request URL…"})

        parsed = parse_pr_url(pr_url)
        logger.info(
            "Step 1/4 parse_url | %s/%s#%s", parsed.owner, parsed.repo, parsed.pr_number
        )

        await emit_review_event({"type": "status", "text": "Fetching PR metadata from GitHub…"})
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
        await emit_review_event(
            {
                "type": "thought",
                "text": (
                    f"Reviewing “{metadata.title}” ({metadata.head_ref} → {metadata.base_ref})."
                ),
            }
        )

        await emit_review_event({"type": "status", "text": "Loading changed files and diffs…"})
        logger.info("Step 3/4 fetch_diffs | requesting changed files from GitHub")
        files = await github_client.get_pr_files(parsed.owner, parsed.repo, parsed.pr_number)
        logger.info(
            "Step 3/4 fetch_diffs done | %d changed file(s): %s",
            len(files),
            ", ".join(f.filename for f in files[:5])
            + (" ..." if len(files) > 5 else ""),
        )
        await emit_review_event(
            {
                "type": "thought",
                "text": f"{len(files)} changed file(s) in this PR.",
            }
        )
        await emit_review_event(
            {
                "type": "pr_metadata",
                "data": {
                    "owner": metadata.owner,
                    "repo": metadata.repo,
                    "pr_number": metadata.pr_number,
                    "title": metadata.title,
                    "html_url": metadata.html_url,
                    "head_ref": metadata.head_ref,
                    "base_ref": metadata.base_ref,
                    "head_sha": metadata.head_sha,
                    "changed_files": len(files),
                    "additions": sum(f.additions for f in files),
                    "deletions": sum(f.deletions for f in files),
                },
            }
        )

        if mode == "baseline":
            await emit_review_event(
                {"type": "status", "text": "Running baseline review (single LLM pass)…"}
            )
            logger.info("Step 4/4 baseline_review | single LLM call, no tools")
            verdict = await review_pr_baseline(metadata, files, llm_config)
        else:
            budget = (
                max_investigations if max_investigations is not None else MAX_INVESTIGATIONS
            )
            await emit_review_event(
                {
                    "type": "status",
                    "text": f"Starting agent review (up to {budget} investigations)…",
                }
            )
            await emit_review_event({"type": "budget", "used": 0, "max": budget})
            logger.info("Step 4/4 agent_review | investigation budget=%d", budget)
            verdict = await run_agent_review(
                metadata, files, github_client, budget, llm_config
            )

        try:
            verdict = enrich_verdict(metadata, verdict, files, mode=mode)
        except Exception as exc:
            logger.exception("Verdict enrichment failed")
            raise RuntimeError(f"Could not finalize review scores: {exc}") from exc

        logger.info(
            "=== Review finished | mode=%s provider=%s issues=%d confidence=%s score=%s partial=%s "
            "investigations=%d elapsed=%.1fs",
            mode,
            llm_config.provider,
            len(verdict.issues),
            verdict.confidence,
            verdict.confidence_score,
            verdict.partial_investigation,
            len(verdict.investigation_trail),
            time.perf_counter() - started,
        )
        await emit_review_event(
            {"type": "verdict", "data": verdict.model_dump(mode="json")}
        )
        logger.info("Review stream | verdict queued for client")
        return verdict


async def run_review(
    pr_url: str,
    mode: Literal["agent", "baseline"] = "agent",
    *,
    github_client: GitHubClient | None = None,
    github_token: str | None = None,
    max_investigations: int | None = None,
    llm_config: LLMConfig | None = None,
    allow_fallback: bool = True,
    emit: EmitFn | None = None,
) -> ReviewVerdict:
    llm_config = llm_config or resolve_llm_config()
    client = github_client or GitHubClient(token=github_token)
    owns_client = github_client is None

    try:
        return await _execute_review(
            pr_url,
            mode,
            github_client=client,
            max_investigations=max_investigations,
            llm_config=llm_config,
            emit=emit,
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
            "Provider %s rate-limited mid-review, restarting from scratch with %s/%s",
            exc.provider,
            fallback.provider,
            fallback.model,
        )
        await emit_review_event(
            {
                "type": "thought",
                "text": (
                    f"Rate limit on {exc.provider}. Restarting with "
                    f"{fallback.provider}/{fallback.model}."
                ),
            }
        )
        return await run_review(
            pr_url,
            mode,
            github_client=client,
            github_token=github_token,
            max_investigations=max_investigations,
            llm_config=fallback,
            allow_fallback=False,
            emit=emit,
        )
    except Exception as exc:
        logger.error("=== Review failed | %s: %s", type(exc).__name__, exc)
        raise
    finally:
        if owns_client:
            await client.close()
