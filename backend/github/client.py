import base64
import re
from urllib.parse import quote

import httpx

from backend.config import GITHUB_TOKEN
from backend.github.models import FileDiff, ParsedPRUrl, PRMetadata

GITHUB_API_BASE = "https://api.github.com"
PR_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<pr_number>\d+)",
    re.IGNORECASE,
)


class GitHubAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"GitHub API error {status_code}: {message}")


def github_error_response(exc: GitHubAPIError, *, token_configured: bool) -> tuple[int, str]:
    """Map GitHub API failures to HTTP status and a user-facing message."""
    if exc.status_code == 404:
        return 404, exc.message
    if exc.status_code == 403 and "rate limit" in exc.message.lower():
        if not token_configured:
            return (
                429,
                "GitHub API rate limit exceeded. Add a GitHub token in the review form "
                "or GITHUB_TOKEN to your .env file "
                "(create a fine-grained or classic token at github.com/settings/tokens; "
                "no scopes needed for public repos). Unauthenticated access is capped at ~60 requests/hour.",
            )
        return (
            429,
            "GitHub API rate limit exceeded for the configured token. "
            "Wait for the hourly reset or switch to another token.",
        )
    return 502, exc.message


def parse_pr_url(url: str) -> ParsedPRUrl:
    url = url.strip()
    match = PR_URL_PATTERN.search(url)
    if not match:
        raise ValueError(
            "Invalid GitHub PR URL. Expected format: https://github.com/{owner}/{repo}/pull/{number}"
        )
    return ParsedPRUrl(
        owner=match.group("owner"),
        repo=match.group("repo"),
        pr_number=int(match.group("pr_number")),
    )


class GitHubClient:
    def __init__(self, token: str | None = None):
        self._token = token or GITHUB_TOKEN
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.AsyncClient(
                base_url=GITHUB_API_BASE,
                headers=headers,
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, path: str, params: dict | None = None) -> dict | list:
        client = await self._get_client()
        response = await client.get(path, params=params)
        if response.status_code >= 400:
            message = response.text
            try:
                payload = response.json()
                message = payload.get("message", message)
            except ValueError:
                pass
            raise GitHubAPIError(response.status_code, message)
        return response.json()

    async def get_pr_metadata(
        self, owner: str, repo: str, pr_number: int
    ) -> PRMetadata:
        data = await self._request(f"/repos/{owner}/{repo}/pulls/{pr_number}")
        return PRMetadata(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            title=data["title"],
            body=data.get("body"),
            base_ref=data["base"]["ref"],
            head_ref=data["head"]["ref"],
            head_sha=data["head"]["sha"],
            html_url=data["html_url"],
        )

    async def get_pr_files(
        self, owner: str, repo: str, pr_number: int
    ) -> list[FileDiff]:
        data = await self._request(f"/repos/{owner}/{repo}/pulls/{pr_number}/files")
        return [
            FileDiff(
                filename=item["filename"],
                status=item["status"],
                patch=item.get("patch"),
                previous_filename=item.get("previous_filename"),
                additions=item.get("additions", 0),
                deletions=item.get("deletions", 0),
                changes=item.get("changes", 0),
            )
            for item in data
        ]

    async def get_file_content(
        self, owner: str, repo: str, path: str, ref: str
    ) -> str:
        encoded_path = quote(path, safe="")
        data = await self._request(
            f"/repos/{owner}/{repo}/contents/{encoded_path}",
            params={"ref": ref},
        )
        if isinstance(data, list):
            raise GitHubAPIError(
                400,
                f"Path '{path}' refers to a directory, not a file",
            )
        if data.get("type") != "file":
            raise GitHubAPIError(
                400,
                f"Path '{path}' is not a file (type={data.get('type')})",
            )
        if "content" not in data:
            raise GitHubAPIError(
                422,
                f"File '{path}' is too large or not available inline from GitHub API",
            )
        encoding = data.get("encoding", "base64")
        if encoding != "base64":
            raise GitHubAPIError(
                422,
                f"Unsupported content encoding for '{path}': {encoding}",
            )
        raw = base64.b64decode(data["content"])
        return raw.decode("utf-8")
