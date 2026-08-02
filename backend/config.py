import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None


def _optional_int_env(name: str) -> int | None:
    value = _optional_env(name)
    return int(value) if value else None


GITHUB_TOKEN: str | None = _optional_env("GITHUB_TOKEN")

LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "google").strip().lower()
LLM_FALLBACK_PROVIDER: str | None = _optional_env("LLM_FALLBACK_PROVIDER")
LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "180"))

GROQ_API_KEY: str | None = _optional_env("GROQ_API_KEY")
GROQ_MODEL: str | None = _optional_env("GROQ_MODEL")

GOOGLE_API_KEY: str | None = _optional_env("GOOGLE_API_KEY")
GOOGLE_MODEL: str | None = _optional_env("GOOGLE_MODEL")
GOOGLE_THINKING_BUDGET: int | None = _optional_int_env("GOOGLE_THINKING_BUDGET")

MAX_INVESTIGATIONS: int = int(os.getenv("MAX_INVESTIGATIONS", "5"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
