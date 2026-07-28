import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from backend.agent.llm import LLMRateLimitError, StructuredOutputError
from backend.agent.providers import available_providers, resolve_llm_config
from backend.github.client import GitHubAPIError
from backend.logging_config import setup_logging
from backend.models.api import ReviewRequest
from backend.models.review import ReviewVerdict
from backend.services.review_service import run_review

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="PR Review Agent", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/providers")
async def list_providers():
    """Vendors the UI can offer. Contains no key material."""
    return {"providers": available_providers()}


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
        return await run_review(request.pr_url, request.mode, llm_config=llm_config)
    except LLMRateLimitError as exc:
        logger.warning("Responding 429 | all providers rate-limited: %s", exc)
        raise HTTPException(
            status_code=429,
            detail="LLM rate limit reached on all configured providers. Please retry later.",
        ) from exc
    except StructuredOutputError as exc:
        logger.error("Responding 500 | model output unusable: %s", exc)
        raise HTTPException(
            status_code=500, detail="The model failed to produce a valid review."
        ) from exc
    except ValueError as exc:
        logger.warning("Responding 400 | %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GitHubAPIError as exc:
        if exc.status_code == 404:
            logger.warning("Responding 404 | %s", exc.message)
            raise HTTPException(status_code=404, detail=exc.message) from exc
        logger.error("Responding 502 | GitHub %s: %s", exc.status_code, exc.message)
        raise HTTPException(status_code=502, detail=exc.message) from exc
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


static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
