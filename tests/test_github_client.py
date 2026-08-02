import base64
import pytest
import respx
from httpx import Response

from backend.github.client import (
    GitHubAPIError,
    GitHubClient,
    github_error_response,
    parse_pr_url,
)
from backend.github.models import FileDiff, PRMetadata


class TestParsePRUrl:
    def test_https_url(self):
        parsed = parse_pr_url("https://github.com/octocat/Hello-World/pull/1347")
        assert parsed.owner == "octocat"
        assert parsed.repo == "Hello-World"
        assert parsed.pr_number == 1347

    def test_url_without_scheme(self):
        parsed = parse_pr_url("github.com/foo/bar/pull/1")
        assert parsed.owner == "foo"
        assert parsed.repo == "bar"
        assert parsed.pr_number == 1

    def test_url_with_www(self):
        parsed = parse_pr_url("https://www.github.com/foo/bar/pull/42")
        assert parsed.pr_number == 42

    def test_url_with_trailing_slash(self):
        parsed = parse_pr_url("https://github.com/foo/bar/pull/5/")
        assert parsed.pr_number == 5

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Invalid GitHub PR URL"):
            parse_pr_url("https://gitlab.com/foo/bar/merge_requests/1")

    def test_missing_pr_number_raises(self):
        with pytest.raises(ValueError):
            parse_pr_url("https://github.com/foo/bar")


class TestGithubErrorResponse:
    def test_404_without_token_mentions_private_repo(self):
        exc = GitHubAPIError(404, "Not Found")
        _, detail = github_error_response(exc, token_configured=False)
        assert "GitHub token" in detail
        assert "private" in detail.lower()

    def test_404_with_token_mentions_access(self):
        exc = GitHubAPIError(404, "Not Found")
        _, detail = github_error_response(exc, token_configured=True)
        assert "read access" in detail.lower()


@pytest.fixture
def client():
    return GitHubClient(token="test-token")


@respx.mock
@pytest.mark.asyncio
async def test_get_pr_metadata(client):
    respx.get("https://api.github.com/repos/octo/repo/pulls/1").mock(
        return_value=Response(
            200,
            json={
                "title": "Fix bug",
                "body": "Fixes #1",
                "html_url": "https://github.com/octo/repo/pull/1",
                "base": {"ref": "main"},
                "head": {"ref": "fix", "sha": "abc123"},
            },
        )
    )
    metadata = await client.get_pr_metadata("octo", "repo", 1)
    await client.close()

    assert metadata == PRMetadata(
        owner="octo",
        repo="repo",
        pr_number=1,
        title="Fix bug",
        body="Fixes #1",
        base_ref="main",
        head_ref="fix",
        head_sha="abc123",
        html_url="https://github.com/octo/repo/pull/1",
    )


@respx.mock
@pytest.mark.asyncio
async def test_get_pr_files(client):
    respx.get("https://api.github.com/repos/octo/repo/pulls/1/files").mock(
        return_value=Response(
            200,
            json=[
                {
                    "filename": "src/main.py",
                    "status": "modified",
                    "patch": "@@ -1 +1 @@\n-old\n+new",
                    "additions": 1,
                    "deletions": 1,
                    "changes": 2,
                },
                {
                    "filename": "README.md",
                    "status": "added",
                    "patch": "@@ -0,0 +1 @@\n+hello",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                },
            ],
        )
    )
    files = await client.get_pr_files("octo", "repo", 1)
    await client.close()

    assert len(files) == 2
    assert files[0] == FileDiff(
        filename="src/main.py",
        status="modified",
        patch="@@ -1 +1 @@\n-old\n+new",
        additions=1,
        deletions=1,
        changes=2,
    )


@respx.mock
@pytest.mark.asyncio
async def test_get_file_content(client):
    content = "def hello(): pass\n"
    encoded = base64.b64encode(content.encode()).decode()
    respx.get(
        "https://api.github.com/repos/octo/repo/contents/src%2Fmain.py",
        params={"ref": "abc123"},
    ).mock(
        return_value=Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "content": encoded,
            },
        )
    )
    result = await client.get_file_content("octo", "repo", "src/main.py", "abc123")
    await client.close()

    assert result == content


@respx.mock
@pytest.mark.asyncio
async def test_get_pr_metadata_not_found(client):
    respx.get("https://api.github.com/repos/octo/repo/pulls/999").mock(
        return_value=Response(404, json={"message": "Not Found"})
    )
    with pytest.raises(GitHubAPIError) as exc_info:
        await client.get_pr_metadata("octo", "repo", 999)
    await client.close()

    assert exc_info.value.status_code == 404


@respx.mock
@pytest.mark.asyncio
async def test_get_file_content_directory_raises(client):
    respx.get(
        "https://api.github.com/repos/octo/repo/contents/src",
        params={"ref": "abc123"},
    ).mock(return_value=Response(200, json=[{"type": "file", "name": "main.py"}]))
    with pytest.raises(GitHubAPIError, match="directory"):
        await client.get_file_content("octo", "repo", "src", "abc123")
    await client.close()


@pytest.mark.asyncio
async def test_live_public_pr_fetch():
    """Integration test against a real public PR (no token required)."""
    client = GitHubClient(token=None)
    try:
        parsed = parse_pr_url("https://github.com/octocat/Hello-World/pull/1")
        metadata = await client.get_pr_metadata(
            parsed.owner, parsed.repo, parsed.pr_number
        )
        files = await client.get_pr_files(parsed.owner, parsed.repo, parsed.pr_number)

        assert metadata.title
        assert metadata.head_sha
        assert len(files) >= 1

        file_with_patch = next((f for f in files if f.patch), None)
        if file_with_patch:
            content = await client.get_file_content(
                parsed.owner,
                parsed.repo,
                file_with_patch.filename,
                metadata.head_sha,
            )
            assert len(content) > 0
    finally:
        await client.close()
