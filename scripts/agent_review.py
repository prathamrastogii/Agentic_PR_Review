"""Run an agentic PR review from the command line."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.logging_config import setup_logging
from backend.services.review_service import run_review


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/agent_review.py <pr_url>")
        sys.exit(1)

    setup_logging()
    verdict = await run_review(sys.argv[1], mode="agent")
    print(json.dumps(verdict.model_dump(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
