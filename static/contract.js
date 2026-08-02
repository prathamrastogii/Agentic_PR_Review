/**
 * Shared API/UI contract for review streams and verdict payloads.
 * Keep in sync with backend/ui_contract.py (enforced by tests/test_ui_contract.py).
 */

export const UI_CONTRACT_VERSION = 1;

export const ISSUE_SEVERITIES = ["error", "warning", "suggestion"];
export const ISSUE_CATEGORIES = ["correctness", "style", "security", "performance"];
export const CONFIDENCE_LEVELS = ["high", "medium", "low"];

export const SEVERITY_LABEL = { error: "High", warning: "Medium", suggestion: "Low" };
export const SEVERITY_ORDER = { error: 0, warning: 1, suggestion: 2 };

export const CONFIDENCE_LEVEL_HIGH = 72;
export const CONFIDENCE_LEVEL_MEDIUM = 48;
export const CONFIDENCE_TIPS_THRESHOLD = 80;
export const READINESS_TIPS_THRESHOLD = 55;

export const STREAM_EVENT_TYPES = new Set([
  "ping",
  "done",
  "status",
  "thought",
  "pr_metadata",
  "budget",
  "tool_call",
  "tool_result",
  "error",
  "verdict",
]);

export const PR_METADATA_FIELDS = [
  "owner",
  "repo",
  "pr_number",
  "title",
  "html_url",
  "head_ref",
  "base_ref",
  "head_sha",
  "changed_files",
  "additions",
  "deletions",
];

export const VERDICT_REQUIRED_FIELDS = [
  "summary",
  "confidence",
  "issues",
  "insights",
  "investigation_trail",
  "partial_investigation",
];

export const INSIGHT_FIELDS = ["whats_good", "risks", "improvements"];

export class ContractError extends Error {
  constructor(message) {
    super(message);
    this.name = "ContractError";
  }
}

export function scoreToLevel(score, fallbackLevel = "low") {
  const resolved =
    score ??
    (fallbackLevel === "high"
      ? CONFIDENCE_LEVEL_HIGH
      : fallbackLevel === "medium"
        ? CONFIDENCE_LEVEL_MEDIUM
        : 25);
  if (resolved >= CONFIDENCE_LEVEL_HIGH) return "high";
  if (resolved >= CONFIDENCE_LEVEL_MEDIUM) return "medium";
  return "low";
}

export function validateStreamEvent(event) {
  if (!event || typeof event !== "object") {
    throw new ContractError("Stream event must be an object.");
  }
  if (!STREAM_EVENT_TYPES.has(event.type)) {
    throw new ContractError(`Unknown stream event type: ${event.type}`);
  }
  if (event.type === "error" && !event.detail) {
    throw new ContractError("Error stream events must include detail.");
  }
  if (event.type === "verdict" && !event.data) {
    throw new ContractError("Verdict stream events must include data.");
  }
  if (event.type === "pr_metadata" && !event.data) {
    throw new ContractError("pr_metadata stream events must include data.");
  }
  return event;
}

export function validateVerdict(verdict) {
  if (!verdict || typeof verdict !== "object") {
    throw new ContractError("Verdict must be an object.");
  }

  for (const field of VERDICT_REQUIRED_FIELDS) {
    if (!(field in verdict)) {
      throw new ContractError(`Verdict missing required field: ${field}`);
    }
  }

  if (!CONFIDENCE_LEVELS.includes(verdict.confidence)) {
    throw new ContractError(`Unknown confidence level: ${verdict.confidence}`);
  }

  if (!Array.isArray(verdict.issues)) {
    throw new ContractError("Verdict issues must be an array.");
  }

  for (const [index, issue] of verdict.issues.entries()) {
    if (!issue || typeof issue !== "object") {
      throw new ContractError(`Issue at index ${index} must be an object.`);
    }
    if (!ISSUE_SEVERITIES.includes(issue.severity)) {
      throw new ContractError(`Unknown issue severity: ${issue.severity}`);
    }
    if (!ISSUE_CATEGORIES.includes(issue.category)) {
      throw new ContractError(`Unknown issue category: ${issue.category}`);
    }
    if (!issue.file || !issue.message) {
      throw new ContractError(`Issue at index ${index} must include file and message.`);
    }
  }

  const insights = verdict.insights ?? {};
  for (const field of INSIGHT_FIELDS) {
    const value = insights[field];
    if (value != null && !Array.isArray(value)) {
      throw new ContractError(`insights.${field} must be an array when present.`);
    }
  }

  if (!Array.isArray(verdict.investigation_trail)) {
    throw new ContractError("investigation_trail must be an array.");
  }
  for (const [index, step] of verdict.investigation_trail.entries()) {
    if (!step?.file_path || !step?.reason) {
      throw new ContractError(
        `investigation_trail[${index}] must include file_path and reason.`
      );
    }
  }

  return verdict;
}

export function validatePrMetadata(metadata) {
  if (!metadata || typeof metadata !== "object") return metadata;
  for (const field of PR_METADATA_FIELDS) {
    if (!(field in metadata)) {
      throw new ContractError(`PR metadata missing field: ${field}`);
    }
  }
  return metadata;
}
