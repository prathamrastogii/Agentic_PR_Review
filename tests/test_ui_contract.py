"""Ensure backend payloads stay compatible with the static UI contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.github.models import FileDiff, PRMetadata
from backend.main import app
from backend.models.review import InvestigationStep, ReviewInsights, ReviewIssue, ReviewVerdict
from backend.services.verdict_enrichment import enrich_verdict
from backend.ui_contract import (
    CONFIDENCE_LEVELS,
    ISSUE_CATEGORIES,
    ISSUE_SEVERITIES,
    PR_METADATA_FIELDS,
    STREAM_EVENT_TYPES,
    UI_CONTRACT_VERSION,
    UI_THRESHOLDS,
    VERDICT_REQUIRED_FIELDS,
    ui_contract_dict,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_JS = ROOT / "static" / "contract.js"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


def _js_number(name: str) -> int:
    match = re.search(rf"export const {name} = (\d+);", CONTRACT_JS.read_text())
    assert match, f"{name} not found in static/contract.js"
    return int(match.group(1))


def _js_array(name: str) -> list[str]:
    text = CONTRACT_JS.read_text()
    match = re.search(rf"export const {name} = \[(.*?)\];", text, re.S)
    assert match, f"{name} not found in static/contract.js"
    return re.findall(r'"([^"]+)"', match.group(1))


def _sample_metadata() -> PRMetadata:
    return PRMetadata(
        owner="acme",
        repo="app",
        pr_number=42,
        title="Add review confidence scoring",
        body="Implements readiness and review confidence metrics.",
        base_ref="main",
        head_ref="feat/review-confidence",
        head_sha="abc1234",
        html_url="https://github.com/acme/app/pull/42",
    )


def _sample_files() -> list[FileDiff]:
    return [
        FileDiff(filename="backend/ui_contract.py", status="added", patch="+1", changes=40),
        FileDiff(filename="static/contract.js", status="added", patch="+1", changes=80),
    ]


def _sample_verdict() -> ReviewVerdict:
    return ReviewVerdict(
        summary="Adds shared UI contract and validation.",
        confidence="high",
        issues=[
            ReviewIssue(
                file="static/app.js",
                line=12,
                severity="warning",
                category="correctness",
                message="Validate verdict before rendering.",
            )
        ],
        insights=ReviewInsights(
            whats_good=["Shared contract between API and UI"],
            risks=["Contract drift if files are not updated together"],
            improvements=["Add more stream event tests"],
        ),
        investigation_trail=[
            InvestigationStep(
                file_path="backend/ui_contract.py",
                reason="Verify exported fields",
            )
        ],
    )


def test_ui_contract_dict_contains_expected_groups():
    contract = ui_contract_dict()
    assert contract["version"] == UI_CONTRACT_VERSION
    assert set(contract["issue_severities"]) == set(ISSUE_SEVERITIES)
    assert set(contract["stream_event_types"]) == set(STREAM_EVENT_TYPES)
    assert set(contract["pr_metadata_fields"]) == set(PR_METADATA_FIELDS)
    assert set(contract["verdict_required_fields"]) == set(VERDICT_REQUIRED_FIELDS)


def test_static_contract_js_thresholds_match_backend():
    assert _js_number("CONFIDENCE_LEVEL_HIGH") == UI_THRESHOLDS["confidence_level_high"]
    assert _js_number("CONFIDENCE_LEVEL_MEDIUM") == UI_THRESHOLDS["confidence_level_medium"]
    assert _js_number("CONFIDENCE_TIPS_THRESHOLD") == UI_THRESHOLDS["confidence_tips_threshold"]
    assert _js_number("READINESS_TIPS_THRESHOLD") == UI_THRESHOLDS["readiness_tips_threshold"]


def test_static_contract_js_enums_match_backend():
    assert _js_array("ISSUE_SEVERITIES") == list(ISSUE_SEVERITIES)
    assert _js_array("ISSUE_CATEGORIES") == list(ISSUE_CATEGORIES)
    assert _js_array("CONFIDENCE_LEVELS") == list(CONFIDENCE_LEVELS)


def test_static_contract_js_exports_render_helpers():
    text = CONTRACT_JS.read_text()
    for symbol in (
        "SEVERITY_ORDER",
        "SEVERITY_LABEL",
        "validateVerdict",
        "validateStreamEvent",
        "validatePrMetadata",
        "scoreToLevel",
    ):
        assert f"export const {symbol}" in text or f"export function {symbol}" in text


@pytest.mark.asyncio
async def test_static_contract_js_is_served(client):
    response = await client.get("/contract.js")
    assert response.status_code == 200
    assert "validateVerdict" in response.text
    assert "SEVERITY_ORDER" in response.text


def test_enriched_verdict_json_matches_ui_contract():
    enriched = enrich_verdict(
        _sample_metadata(),
        _sample_verdict(),
        _sample_files(),
        mode="agent",
    )
    payload = enriched.model_dump(mode="json")

    for field in VERDICT_REQUIRED_FIELDS:
        assert field in payload, f"Missing verdict field required by UI: {field}"

    assert payload["confidence"] in CONFIDENCE_LEVELS
    assert isinstance(payload["issues"], list)
    for issue in payload["issues"]:
        assert issue["severity"] in ISSUE_SEVERITIES
        assert issue["category"] in ISSUE_CATEGORIES
        assert issue["file"]
        assert issue["message"]

    insights = payload["insights"]
    assert isinstance(insights["whats_good"], list)
    assert isinstance(insights["risks"], list)
    assert isinstance(insights["improvements"], list)

    for step in payload["investigation_trail"]:
        assert step["file_path"]
        assert step["reason"]

    # Must round-trip through JSON like the SSE stream does.
    json.dumps({"type": "verdict", "data": payload})


def test_stream_event_types_used_by_backend_are_in_contract():
    emitted_types = {
        "status",
        "thought",
        "pr_metadata",
        "budget",
        "tool_call",
        "tool_result",
        "error",
        "verdict",
        "ping",
        "done",
    }
    assert emitted_types <= STREAM_EVENT_TYPES


def test_pr_metadata_event_shape_matches_ui_contract():
    metadata = _sample_metadata()
    event_data = {
        "owner": metadata.owner,
        "repo": metadata.repo,
        "pr_number": metadata.pr_number,
        "title": metadata.title,
        "html_url": metadata.html_url,
        "head_ref": metadata.head_ref,
        "base_ref": metadata.base_ref,
        "head_sha": metadata.head_sha,
        "changed_files": 2,
        "additions": 120,
        "deletions": 4,
    }
    for field in PR_METADATA_FIELDS:
        assert field in event_data
