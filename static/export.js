const SEVERITY_LABEL = { error: "High", warning: "Medium", suggestion: "Low" };

export function verdictToMarkdown({ verdict, prTitle, prUrl, mode, prMetadata }) {
  const lines = [`# ${prTitle || "PR Review"}`, ""];

  if (prUrl) lines.push(`**PR:** ${prUrl}`);
  if (prMetadata?.head_ref && prMetadata?.base_ref) {
    lines.push(`**Branches:** \`${prMetadata.head_ref}\` → \`${prMetadata.base_ref}\``);
  }
  lines.push(`**Mode:** ${mode === "agent" ? "Agent" : "Baseline"}`);
  if (verdict.pr_readiness_score != null) {
    lines.push(
      `**PR readiness:** ${verdict.pr_readiness_score}% (${verdict.pr_readiness || "unknown"})`
    );
    if (verdict.pr_readiness_rationale) {
      lines.push(`**PR readiness note:** ${verdict.pr_readiness_rationale}`);
    }
    if (verdict.pr_readiness_score < 55 && verdict.pr_readiness_tips?.length) {
      lines.push("", "**How to improve PR readiness:**");
      for (const tip of verdict.pr_readiness_tips) lines.push(`- ${tip}`);
    }
  }
  if (verdict.confidence_score != null) {
    lines.push(`**Review confidence:** ${verdict.confidence_score}% (${verdict.confidence})`);
    if (verdict.confidence_rationale) {
      lines.push(`**Review confidence note:** ${verdict.confidence_rationale}`);
    }
    if (verdict.confidence_score < 80 && verdict.confidence_tips?.length) {
      lines.push("", "**Review confidence caveats:**");
      for (const tip of verdict.confidence_tips) lines.push(`- ${tip}`);
    }
  } else if (verdict.pr_readiness_score == null) {
    lines.push(`**Confidence:** ${verdict.confidence}`);
  }
  lines.push("");

  lines.push("## Summary", "", verdict.summary, "");

  const insights = verdict.insights || {};
  if (insights.whats_good?.length) {
    lines.push("## Positive impacts");
    for (const item of insights.whats_good) lines.push(`- ${item}`);
    lines.push("");
  }
  if (insights.risks?.length) {
    lines.push("## Potential breaking changes");
    for (const item of insights.risks) lines.push(`- ${item}`);
    lines.push("");
  }
  if (insights.improvements?.length) {
    lines.push("## Improvement opportunities");
    for (const item of insights.improvements) lines.push(`- ${item}`);
    lines.push("");
  }

  if (verdict.investigation_trail?.length) {
    lines.push("## Investigation trail");
    for (const step of verdict.investigation_trail) {
      lines.push(`- \`${step.file_path}\`: ${step.reason}`);
    }
    lines.push("");
  }

  if (verdict.issues?.length) {
    lines.push("## Issues");
    for (const issue of verdict.issues) {
      const line = issue.line != null ? `:${issue.line}` : "";
      const sev = SEVERITY_LABEL[issue.severity] || issue.severity;
      lines.push(
        `### [${sev}] \`${issue.file}${line}\` (${issue.category})`,
        "",
        issue.message,
        ""
      );
    }
  }

  return lines.join("\n");
}

export function downloadMarkdown(filename, content) {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
