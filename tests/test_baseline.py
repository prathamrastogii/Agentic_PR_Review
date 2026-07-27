import json

import pytest
from pydantic import ValidationError

from backend.agent.baseline import build_baseline_prompt, format_diffs
from backend.agent.llm import extract_json, parse_structured_response
from backend.github.models import FileDiff, PRMetadata
from backend.models.review import ReviewIssue, ReviewVerdict


class TestJsonParsing:
    def test_extract_json_from_fence(self):
        text = 'Here is the review:\n```json\n{"summary": "ok", "confidence": "high"}\n```'
        raw = extract_json(text)
        data = json.loads(raw)
        assert data["summary"] == "ok"

    def test_extract_json_raw(self):
        text = '{"summary": "ok", "issues": [], "confidence": "low"}'
        raw = extract_json(text)
        assert json.loads(raw)["confidence"] == "low"

    def test_parse_structured_response(self):
        text = '{"summary": "Looks good", "issues": [], "confidence": "high"}'
        verdict = parse_structured_response(text, ReviewVerdict)
        assert verdict.summary == "Looks good"
        assert verdict.confidence == "high"

    def test_parse_invalid_raises(self):
        with pytest.raises((ValidationError, ValueError, json.JSONDecodeError)):
            parse_structured_response("not json at all", ReviewVerdict)


class TestFormatDiffs:
    def test_format_basic_diff(self):
        files = [
            FileDiff(
                filename="main.py",
                status="modified",
                patch="@@ -1 +1 @@\n-old\n+new",
            )
        ]
        text, truncated = format_diffs(files)
        assert "main.py" in text
        assert "+new" in text
        assert truncated is False

    def test_truncates_large_patch(self):
        files = [
            FileDiff(
                filename="big.py",
                status="modified",
                patch="x" * 10000,
            )
        ]
        text, truncated = format_diffs(files)
        assert truncated is True
        assert "[patch truncated]" in text


class TestBuildBaselinePrompt:
    def test_includes_metadata(self):
        metadata = PRMetadata(
            owner="octo",
            repo="repo",
            pr_number=1,
            title="Fix bug",
            body="Fixes #1",
            base_ref="main",
            head_ref="fix",
            head_sha="abc",
            html_url="https://github.com/octo/repo/pull/1",
        )
        files = [FileDiff(filename="a.py", status="modified", patch="+fix")]
        prompt = build_baseline_prompt(metadata, files)
        assert "Fix bug" in prompt
        assert "octo/repo" in prompt
        assert "a.py" in prompt
