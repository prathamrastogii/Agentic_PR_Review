import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.agent.llm import LLMRateLimitError, LLMTimeoutError, StructuredOutputError
from backend.agent.providers import available_providers, build_chat_model, resolve_llm_config
from backend.config import GITHUB_TOKEN
from backend.github.client import GitHubAPIError, github_error_response
from backend.logging_config import setup_logging
from backend.models.api import LLMTestRequest, ReviewRequest
from backend.models.review import ReviewVerdict
from backend.services.review_service import run_review

setup_logging()
logger = logging.getLogger(__name__)

if not GITHUB_TOKEN:
    logger.warning(
        "GITHUB_TOKEN is not set. GitHub API calls are unauthenticated (~60 requests/hour). "
        "Add GITHUB_TOKEN to .env for 5,000 requests/hour."
    )

app = FastAPI(title="PR Review Agent", version="0.1.0")


def _resolve_github_token(request: ReviewRequest) -> str | None:
    if request.github_token:
        return request.github_token.get_secret_value()
    return GITHUB_TOKEN


def _github_token_configured(request: ReviewRequest) -> bool:
    return bool(_resolve_github_token(request))


def _review_error_payload(exc: Exception, *, token_configured: bool) -> tuple[int, str]:
    if isinstance(exc, LLMRateLimitError):
        return 429, "LLM rate limit reached on all configured providers. Please retry later."
    if isinstance(exc, LLMTimeoutError):
        return 504, str(exc)
    if isinstance(exc, StructuredOutputError):
        return 500, "The model failed to produce a valid review."
    if isinstance(exc, ValueError):
        return 400, str(exc)
    if isinstance(exc, GitHubAPIError):
        return github_error_response(exc, token_configured=token_configured)
    if isinstance(exc, RuntimeError):
        return 500, str(exc)
    if getattr(exc, "status_code", None) == 429:
        return 429, "LLM rate limit reached. Please retry later."
    return 500, f"Unexpected error: {type(exc).__name__}"


def _format_sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _review_event_stream(request: ReviewRequest) -> AsyncIterator[str]:
    queue: asyncio.Queue[tuple[str, dict | None]] = asyncio.Queue()
    settings = request.llm
    heartbeat_seconds = 3.0

    async def emit(event: dict) -> None:
        await queue.put(("event", event))

    token_configured = _github_token_configured(request)
    github_token = _resolve_github_token(request)

    async def worker() -> None:
        try:
            llm_config = resolve_llm_config(
                provider=settings.provider if settings else None,
                model=settings.model if settings else None,
                api_key=settings.api_key if settings else None,
            )
            await run_review(
                request.pr_url,
                request.mode,
                llm_config=llm_config,
                github_token=github_token,
                emit=emit,
            )
        except Exception as exc:
            status, detail = _review_error_payload(exc, token_configured=token_configured)
            logger.exception("Review stream failed | %s", detail)
            await emit({"type": "error", "status": status, "detail": detail})
        finally:
            await queue.put(("done", None))

    async def heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(heartbeat_seconds)
                await queue.put(("heartbeat", None))
        except asyncio.CancelledError:
            return

    yield _format_sse({"type": "ping"})
    task = asyncio.create_task(worker())
    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "done":
                yield _format_sse({"type": "done"})
                break
            if kind == "heartbeat":
                yield _format_sse({"type": "ping"})
                continue
            try:
                yield _format_sse(payload)
            except (TypeError, ValueError) as exc:
                event_type = payload.get("type") if isinstance(payload, dict) else "unknown"
                logger.exception("Failed to encode stream event | type=%s", event_type)
                yield _format_sse(
                    {
                        "type": "error",
                        "status": 500,
                        "detail": f"Could not serialize stream event: {exc}",
                    }
                )
                break
            if isinstance(payload, dict) and payload.get("type") == "verdict":
                logger.info("Review stream | verdict event sent")
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        else:
            with contextlib.suppress(Exception):
                await task


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/providers")
async def list_providers():
    """Vendors the UI can offer. Contains no key material."""
    return {"providers": available_providers()}


@app.post("/api/configure-llm/test")
async def test_llm_connection(request: LLMTestRequest):
    """Minimal provider ping: validates key and model without storing credentials."""
    from langchain_core.messages import HumanMessage

    from backend.agent.llm import LLMTimeoutError, _ainvoke_with_timeout

    try:
        llm_config = resolve_llm_config(
            provider=request.provider,
            model=request.model,
            api_key=request.api_key,
        )
        llm = build_chat_model(llm_config)
        await _ainvoke_with_timeout(
            llm.ainvoke([HumanMessage(content="Reply with exactly: OK")]),
            provider=llm_config.provider,
        )
        return {"ok": True, "provider": llm_config.provider, "model": llm_config.model}
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("LLM connection test failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Could not reach the provider. Check your key, model, and network.",
        ) from exc


@app.post("/api/review", response_model=ReviewVerdict)
async def create_review(request: ReviewRequest) -> ReviewVerdict:
    settings = request.llm
    logger.info(
        "POST /api/review | mode=%s provider=%s url=%s",
        request.mode,
        (settings.provider if settings else None) or "default",
        request.pr_url,
    )
    try:
        llm_config = resolve_llm_config(
            provider=settings.provider if settings else None,
            model=settings.model if settings else None,
            api_key=settings.api_key if settings else None,
        )
        return await run_review(
            request.pr_url,
            request.mode,
            llm_config=llm_config,
            github_token=_resolve_github_token(request),
        )
    except LLMRateLimitError as exc:
        logger.warning("Responding 429 | all providers rate-limited: %s", exc)
        raise HTTPException(
            status_code=429,
            detail="LLM rate limit reached on all configured providers. Please retry later.",
        ) from exc
    except LLMTimeoutError as exc:
        logger.warning("Responding 504 | LLM timeout: %s", exc)
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except StructuredOutputError as exc:
        logger.error("Responding 500 | model output unusable: %s", exc)
        raise HTTPException(
            status_code=500, detail="The model failed to produce a valid review."
        ) from exc
    except ValueError as exc:
        logger.warning("Responding 400 | %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GitHubAPIError as exc:
        status, detail = github_error_response(
            exc, token_configured=_github_token_configured(request)
        )
        if status == 404:
            logger.warning("Responding 404 | %s", detail)
        elif status == 429:
            logger.warning("Responding 429 | GitHub rate limit: %s", detail)
        else:
            logger.error("Responding %s | GitHub %s: %s", status, exc.status_code, detail)
        raise HTTPException(status_code=status, detail=detail) from exc
    except RuntimeError as exc:
        logger.error("Responding 500 | %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        if getattr(exc, "status_code", None) == 429:
            logger.warning("Responding 429 | upstream rate limit: %s", exc)
            raise HTTPException(
                status_code=429,
                detail="LLM rate limit reached. Please retry later.",
            ) from exc
        logger.exception("Responding 500 | unexpected error during review")
        raise HTTPException(
            status_code=500, detail=f"Unexpected error: {type(exc).__name__}"
        ) from exc


@app.post("/api/review/stream")
async def create_review_stream(request: ReviewRequest) -> StreamingResponse:
    settings = request.llm
    logger.info(
        "POST /api/review/stream | mode=%s provider=%s url=%s",
        request.mode,
        (settings.provider if settings else None) or "default",
        request.pr_url,
    )
    return StreamingResponse(
        _review_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
