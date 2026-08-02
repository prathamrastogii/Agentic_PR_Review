# AI-Powered PR Review Agent

An agentic pull request reviewer that reads GitHub diffs, investigates related files when context is missing, and returns a structured verdict with summary, insights, issues, and confidence scores.

The web UI streams the agent loop live (SSE) so you can watch investigations happen in real time, not just read a final answer.

## Features

- **Agent mode** — LangGraph loop with a capped investigation budget; fetches out-of-diff files when the model needs more context
- **Baseline mode** — single LLM pass on the diff only (faster, fewer GitHub API calls)
- **Live thinking feed** — SSE stream of status, thoughts, tool calls, and tool results during a review
- **Structured output** — summary, file-level issues, investigation trail, and executive insight cards (`whats_good`, `risks`, `improvements`)
- **Scoring** — contextual review confidence and PR readiness scores with improvement tips when scores are low
- **Multi-provider LLM** — Groq (default free tier) and Google Gemini on the server; optional BYO key/model from the UI
- **Provider fallback** — if Groq rate-limits mid-review, the run restarts on Gemini when configured
- **Desktop UI** — welcome → connect LLM → review workspace, dark mode, review history, export to Markdown

## How it works

```
PR URL → GitHub API (metadata + diffs)
       → Agent or baseline review (Groq / Gemini)
       → Verdict enrichment (issues backfill, confidence + readiness scores)
       → JSON verdict (+ SSE events during streaming)
```

**Agent loop:** at each step the model chooses `investigate` (fetch one file) or `verdict` (final review). Python enforces budget limits, blocks bad paths (globs, build artifacts), deduplicates repeated requests, and can force investigation on multi-file PRs before accepting a high-confidence verdict.

## Prerequisites

- Python 3.13
- At least one LLM API key (Groq and/or Google Gemini)
- **GitHub token strongly recommended** — public PRs work without one, but unauthenticated GitHub API access is limited to ~60 requests/hour per IP. Agent reviews use several calls each (metadata, diffs, file fetches).

## Setup

```bash
git clone <repo-url>
cd Agentic_PR_Review
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your keys (see below)
```

## Run locally

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload
```

Open **http://127.0.0.1:8000** in your browser.

Health check:

```bash
curl http://localhost:8000/health
```

## Using the UI

1. **Setup** — choose **Free tier** (server Groq key) or **Use your own LLM**
2. **Connect** — for BYO: pick a vendor/model, enter API key, test connection, continue
3. **Review** — paste a GitHub PR URL, pick **Agent** or **Baseline**, run review

Results include PR context, confidence/readiness metrics, summary, insight cards, investigation trail, and filterable issues. Use **Export .md** or **Copy summary** from the actions bar.

Session state (LLM choice, theme, recent reviews) is stored in `sessionStorage` only. API keys entered in the UI are sent per request and are not saved on the server.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/providers` | Server-configured LLM providers (no secrets) |
| `POST` | `/api/configure-llm/test` | Validate a BYO provider/key/model |
| `POST` | `/api/review` | Run review, return `ReviewVerdict` JSON |
| `POST` | `/api/review/stream` | Same as above, but streams SSE events then verdict |

**Review request body:**

```json
{
  "pr_url": "https://github.com/owner/repo/pull/123",
  "mode": "agent",
  "llm": {
    "provider": "google",
    "model": "gemini-3.5-flash-lite",
    "api_key": "optional-per-request-key"
  },
  "github_token": "optional-per-request-github-pat"
}
```

**Stream event types:** `status`, `thought`, `pr_metadata`, `budget`, `tool_call`, `tool_result`, `verdict`, `error`, `done`

**Example (non-streaming):**

```bash
curl -X POST http://localhost:8000/api/review \
  -H "Content-Type: application/json" \
  -d '{"pr_url":"https://github.com/owner/repo/pull/1","mode":"agent"}'
```

## CLI

```bash
python scripts/review.py https://github.com/owner/repo/pull/123
python scripts/review.py <pr_url> --mode baseline
python scripts/review.py <pr_url> --provider google --model gemini-3.5-flash-lite
python scripts/review.py <pr_url> --max-investigations 8
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | For free tier | Groq API key ([console.groq.com](https://console.groq.com/keys)) |
| `GOOGLE_API_KEY` | For Gemini / fallback | Google AI key ([aistudio.google.com](https://aistudio.google.com/apikey)) |
| `LLM_PROVIDER` | Optional | Default provider: `groq` or `google` (default: `groq`) |
| `LLM_FALLBACK_PROVIDER` | Optional | Fallback when primary is rate-limited (e.g. `google`) |
| `GROQ_MODEL` | Optional | Default Groq model (default: `llama-3.3-70b-versatile`) |
| `GOOGLE_MODEL` | Optional | Default Gemini model (default: `gemini-3.5-flash-lite`) |
| `GOOGLE_THINKING_BUDGET` | Optional | Gemini thinking budget; omit unless you know you need it |
| `GITHUB_TOKEN` | Recommended | GitHub PAT for higher rate limits and private repos (no scopes needed for public repos) |
| `MAX_INVESTIGATIONS` | Optional | Agent file-fetch budget per review (default: `5`) |
| `LOG_LEVEL` | Optional | Logging level (default: `INFO`) |

See `.env.example` for a starter template.

## Project structure

```
backend/
  agent/          LangGraph loop, prompts, LLM providers
  github/         GitHub API client
  models/         Pydantic schemas (ReviewVerdict, API requests)
  services/       review_service, streaming events, verdict enrichment
  main.py         FastAPI app + static file mount
static/           SPA UI (HTML/CSS/JS modules)
scripts/review.py CLI entry point
tests/            pytest suite
```

## Testing

```bash
source .venv/bin/activate
pytest tests/ -q
```

Skip the live GitHub integration test (needs network, no mock):

```bash
pytest tests/ -q --deselect tests/test_github_client.py::test_live_public_pr_fetch
```

## Limitations

- **GitHub rate limits** — without `GITHUB_TOKEN`, expect ~60 API calls/hour. Heavy agent use will hit this quickly.
- **Server LLM providers** — the backend currently implements **Groq** and **Google Gemini** only. The UI vendor catalog lists other providers for future use; BYO for those vendors requires adding a provider in `backend/agent/providers.py`.
- **Path guessing** — the agent must guess repository-relative file paths; wrong guesses consume budget but no longer abort the whole review.
- **Model variability** — the model may put findings in insight bullets without structured `issues`; the server backfills issues from insights when possible.
- **No persistence** — reviews are not stored in a database; history lives in browser `sessionStorage` only.
- **Not deployed** — no production hosting config yet (`render.yaml` / Phase 6 still TODO).

## Roadmap

| Item | Status |
|------|--------|
| Core agent + API + UI | Done |
| SSE streaming + insights + scoring | Done |
| Deploy (e.g. Render) | Not started |
| Validation matrix (agent vs baseline on real PRs) | Not started |

## License

Add your license here.
