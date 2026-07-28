import logging
from typing import Literal

from langgraph.graph import END, StateGraph

from backend.agent.nodes import evaluate_node, fetch_file_node
from backend.agent.state import AgentState
from backend.github.client import GitHubClient

logger = logging.getLogger(__name__)


def _decide_route(state: AgentState) -> Literal["fetch_file", "evaluate", "end"]:
    if state.get("verdict") is not None:
        return "end"
    if state.get("feedback_note"):
        return "evaluate"
    pending = state.get("pending_file_request")
    if pending and state["investigation_count"] < state["max_investigations"]:
        return "fetch_file"
    if state["investigation_count"] >= state["max_investigations"]:
        return "evaluate"
    return "end"


def route_after_evaluate(state: AgentState) -> Literal["fetch_file", "evaluate", "end"]:
    decision = _decide_route(state)
    logger.info(
        "  route | next=%s (investigations %d/%d)",
        decision,
        state["investigation_count"],
        state["max_investigations"],
    )
    return decision


def build_review_graph(github_client: GitHubClient):
    async def fetch_file(state: AgentState) -> dict:
        return await fetch_file_node(state, github_client)

    graph = StateGraph(AgentState)
    graph.add_node("evaluate", evaluate_node)
    graph.add_node("fetch_file", fetch_file)
    graph.set_entry_point("evaluate")
    graph.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {"fetch_file": "fetch_file", "evaluate": "evaluate", "end": END},
    )
    graph.add_edge("fetch_file", "evaluate")
    return graph.compile()


async def run_agent_review(
    metadata,
    files: list,
    github_client: GitHubClient,
    max_investigations: int,
    llm_config=None,
) -> "ReviewVerdict":
    from backend.config import MAX_INVESTIGATIONS
    from backend.models.review import ReviewVerdict

    budget = max_investigations or MAX_INVESTIGATIONS
    initial_state: AgentState = {
        "pr_metadata": metadata,
        "llm_config": llm_config,
        "diffs": files,
        "fetched_files": {},
        "unavailable_files": {},
        "investigation_count": 0,
        "max_investigations": budget,
        "investigation_trail": [],
        "pending_file_request": None,
        "pending_reason": None,
        "verdict": None,
        "feedback_note": None,
    }

    logger.info(
        "Agent loop starting | %d diff(s), max_investigations=%d", len(files), budget
    )
    graph = build_review_graph(github_client)
    result = await graph.ainvoke(initial_state, config={"recursion_limit": 25})

    verdict = result.get("verdict")
    if verdict is None:
        logger.error("Agent loop ended with no verdict in state")
        raise RuntimeError("Agent finished without producing a verdict")

    unavailable = result.get("unavailable_files") or {}
    logger.info(
        "Agent loop complete | %d investigation(s) used: %s%s",
        result["investigation_count"],
        ", ".join(step.file_path for step in verdict.investigation_trail) or "none",
        f" | {len(unavailable)} unavailable path(s): {', '.join(unavailable)}"
        if unavailable
        else "",
    )
    return verdict
