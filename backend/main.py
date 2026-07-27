from fastapi import FastAPI, HTTPException

from backend.github.client import GitHubAPIError
from backend.models.api import ReviewRequest
from backend.models.review import ReviewVerdict
from backend.services.review_service import run_review

app = FastAPI(title="PR Review Agent", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/review", response_model=ReviewVerdict)
async def create_review(request: ReviewRequest) -> ReviewVerdict:
    try:
        return await run_review(request.pr_url, request.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GitHubAPIError as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        raise HTTPException(status_code=502, detail=exc.message) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
