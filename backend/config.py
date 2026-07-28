import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None


GROQ_API_KEY: str | None = _optional_env("GROQ_API_KEY")
GITHUB_TOKEN: str | None = _optional_env("GITHUB_TOKEN")
MAX_INVESTIGATIONS: int = int(os.getenv("MAX_INVESTIGATIONS", "5"))
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
