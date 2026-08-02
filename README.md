# AI-Powered PR Review Agent

An agentic code review system that investigates pull requests with a capped investigation budget, fetching additional files when the diff alone isn't enough context.

## Prerequisites

- Python 3.13
- [Groq API key](https://console.groq.com/) (free tier)
- GitHub token (optional for public PRs; required for private repos)

## Setup

```bash
cd proj-1
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: add GROQ_API_KEY before Phase 2; GITHUB_TOKEN optional for public PRs
```

## Run locally

```bash
uvicorn backend.main:app --reload
```

Health check: `curl http://localhost:8000/health`

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Phase 2+ | Groq API key for Llama 3.3 70B |
| `GITHUB_TOKEN` | Optional | PAT for private PRs and higher GitHub rate limits |
| `MAX_INVESTIGATIONS` | Optional | Agent tool-call budget (default: 5) |
| `GROQ_MODEL` | Optional | Groq model id (default: `llama-3.3-70b-versatile`) |
