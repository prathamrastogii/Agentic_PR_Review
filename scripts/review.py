"""Run a PR review from the command line.

Examples:
    python scripts/review.py <pr_url>
    python scripts/review.py <pr_url> --mode baseline
    python scripts/review.py <pr_url> --provider google
    python scripts/review.py <pr_url> --provider google --model gemini-3.6-flash
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agent.providers import PROVIDERS, resolve_llm_config
from backend.logging_config import setup_logging
from backend.services.review_service import run_review


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review a GitHub pull request.")
    parser.add_argument("pr_url", help="https://github.com/{owner}/{repo}/pull/{n}")
    parser.add_argument("--mode", choices=("agent", "baseline"), default="agent")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default=None)
    parser.add_argument("--model", default=None, help="Overrides the provider default")
    parser.add_argument("--max-investigations", type=int, default=None)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    setup_logging()

    llm_config = resolve_llm_config(provider=args.provider, model=args.model)
    verdict = await run_review(
        args.pr_url,
        args.mode,
        max_investigations=args.max_investigations,
        llm_config=llm_config,
    )
    print(json.dumps(verdict.model_dump(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
