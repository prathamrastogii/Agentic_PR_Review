from typing import Literal

from langgraph.graph import END, StateGraph

from backend.agent.nodes import evaluate_node, fetch_file_node
from backend.agent.state import AgentState
from backend.github.client import GitHubClient


def route_after_evaluate(state: AgentState) -> Literal["fetch_file", "evaluate", "end"]:
    if state.get("verdict") is not None:
        return "end"
    if state.get("dedup_note"):
        return "evaluate"
    pending = state.get("pending_file_request")
    if pending and state["investigation_count"] < state["max_investigations"]:
        return "fetch_file"
    if state["investigation_count"] >= state["max_investigations"]:
        return "evaluate"
    return "end"


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
) -> "ReviewVerdict":
    from backend.config import MAX_INVESTIGATIONS
    from backend.models.review import ReviewVerdict

    budget = max_investigations or MAX_INVESTIGATIONS
    initial_state: AgentState = {
        "pr_metadata": metadata,
        "diffs": files,
        "fetched_files": {},
        "investigation_count": 0,
        "max_investigations": budget,
        "investigation_trail": [],
        "pending_file_request": None,
        "pending_reason": None,
        "verdict": None,
        "dedup_note": None,
    }

    graph = build_review_graph(github_client)
    result = await graph.ainvoke(initial_state, config={"recursion_limit": 25})
    verdict = result.get("verdict")
    if verdict is None:
        raise RuntimeError("Agent finished without producing a verdict")
    return verdict
