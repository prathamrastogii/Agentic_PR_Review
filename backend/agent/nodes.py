from backend.agent.baseline import format_diffs
from backend.agent.actions import EvaluateResponse
from backend.agent.llm import invoke_structured
from backend.agent.prompts import BUDGET_EXHAUSTED_PROMPT, EVALUATE_SYSTEM_PROMPT
from backend.agent.state import AgentState
from backend.github.client import GitHubClient
from backend.models.review import InvestigationStep, ReviewVerdict

MAX_CHARS_FETCHED_FILE = 12000


def _format_fetched_files(fetched_files: dict[str, str]) -> str:
    if not fetched_files:
        return "No additional files fetched yet."
    parts: list[str] = []
    for path, content in fetched_files.items():
        if len(content) > MAX_CHARS_FETCHED_FILE:
            content = content[:MAX_CHARS_FETCHED_FILE] + "\n... [file truncated]"
        parts.append(f"### {path}\n```\n{content}\n```")
    return "\n\n".join(parts)


def build_evaluate_prompt(state: AgentState) -> str:
    metadata = state["pr_metadata"]
    diff_text, truncated = format_diffs(state["diffs"])
    truncation_note = (
        "\n\nNote: Some diff content was truncated due to size limits."
        if truncated
        else ""
    )
    body_section = f"\nDescription:\n{metadata.body}" if metadata.body else ""
    remaining = state["max_investigations"] - state["investigation_count"]
    fetched_section = _format_fetched_files(state["fetched_files"])
    dedup_note = state.get("dedup_note")
    dedup_section = f"\n\n{dedup_note}" if dedup_note else ""

    return (
        f"Pull Request: {metadata.title}\n"
        f"Repository: {metadata.owner}/{metadata.repo}\n"
        f"Branch: {metadata.head_ref} → {metadata.base_ref}\n"
        f"URL: {metadata.html_url}"
        f"{body_section}\n\n"
        f"Investigation budget remaining: {remaining} of {state['max_investigations']}\n\n"
        f"## Changed files ({len(state['diffs'])})\n\n{diff_text}"
        f"{truncation_note}\n\n"
        f"## Fetched files\n\n{fetched_section}"
        f"{dedup_section}"
    )


async def evaluate_node(state: AgentState) -> dict:
    budget_remaining = state["max_investigations"] - state["investigation_count"]
    user_prompt = build_evaluate_prompt(state)

    if budget_remaining <= 0 and state["verdict"] is None:
        system_prompt = f"{EVALUATE_SYSTEM_PROMPT}\n\n{BUDGET_EXHAUSTED_PROMPT}"
        response = await invoke_structured(system_prompt, user_prompt, EvaluateResponse)
        if response.action != "verdict":
            response = EvaluateResponse(
                action="verdict",
                summary=response.summary or "Unable to complete full investigation within budget.",
                issues=response.issues,
                confidence="low",
                partial_investigation=True,
            )
        verdict = response.to_verdict(state["investigation_trail"])
        verdict.partial_investigation = True
        if verdict.confidence == "high":
            verdict.confidence = "medium"
        return {
            "verdict": verdict,
            "pending_file_request": None,
            "pending_reason": None,
            "dedup_note": None,
        }

    response = await invoke_structured(EVALUATE_SYSTEM_PROMPT, user_prompt, EvaluateResponse)

    if response.action == "verdict":
        verdict = response.to_verdict(state["investigation_trail"])
        return {
            "verdict": verdict,
            "pending_file_request": None,
            "pending_reason": None,
            "dedup_note": None,
        }

    file_path = response.file_path or ""
    if file_path in state["fetched_files"]:
        return {
            "pending_file_request": None,
            "pending_reason": None,
            "dedup_note": (
                f"Note: You requested '{file_path}' but it is already in the fetched files section. "
                "Produce a verdict or request a different file."
            ),
        }

    return {
        "pending_file_request": file_path,
        "pending_reason": response.reason,
        "dedup_note": None,
    }


async def fetch_file_node(state: AgentState, github_client: GitHubClient) -> dict:
    path = state["pending_file_request"]
    reason = state["pending_reason"]
    if not path or not reason:
        return {}

    metadata = state["pr_metadata"]
    content = await github_client.get_file_content(
        metadata.owner, metadata.repo, path, metadata.head_sha
    )
    trail = list(state["investigation_trail"]) + [
        InvestigationStep(file_path=path, reason=reason)
    ]

    return {
        "fetched_files": {**state["fetched_files"], path: content},
        "investigation_count": state["investigation_count"] + 1,
        "investigation_trail": trail,
        "pending_file_request": None,
        "pending_reason": None,
        "dedup_note": None,
    }
