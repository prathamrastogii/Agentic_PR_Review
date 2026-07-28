import {
  appState,
  applyCustomLlmFromForm,
  resetCustomLlmVerification,
  selectFreeTier,
} from "./state.js";

const SEVERITY_ORDER = { error: 0, warning: 1, suggestion: 2 };
const SEVERITY_LABEL = { error: "High", warning: "Medium", suggestion: "Low" };

const VIEW_HINTS = {
  welcome: "Choose how to run reviews",
  config: "Connect your LLM",
  dashboard: "Desktop review workspace",
};

const views = {
  welcome: document.getElementById("view-welcome"),
  config: document.getElementById("view-config"),
  dashboard: document.getElementById("view-dashboard"),
};

const titleBarHint = document.getElementById("title-bar-hint");
const homeLink = document.getElementById("home-link");

const chooseFreeTier = document.getElementById("choose-free-tier");
const chooseOwnLlm = document.getElementById("choose-own-llm");

const configForm = document.getElementById("config-form");
const configBack = document.getElementById("config-back");
const configProvider = document.getElementById("config-provider");
const configModel = document.getElementById("config-model");
const configApiKey = document.getElementById("config-api-key");
const configBaseUrl = document.getElementById("config-base-url");
const baseUrlField = document.getElementById("base-url-field");
const testConnectionBtn = document.getElementById("test-connection-btn");
const connectionResult = document.getElementById("connection-result");
const configContinue = document.getElementById("config-continue");

const llmIndicatorDot = document.getElementById("llm-indicator-dot");
const llmIndicatorText = document.getElementById("llm-indicator-text");
const changeLlmPath = document.getElementById("change-llm-path");

const form = document.getElementById("review-form");
const prUrlInput = document.getElementById("pr-url");
const submitBtn = document.getElementById("submit-btn");
const statusEl = document.getElementById("status");
const workspaceEmpty = document.getElementById("workspace-empty");
const resultsEl = document.getElementById("results");
const partialBanner = document.getElementById("partial-banner");
const confidenceBadge = document.getElementById("confidence-badge");
const summaryText = document.getElementById("summary-text");
const modeNote = document.getElementById("mode-note");
const trailList = document.getElementById("trail-list");
const trailEmpty = document.getElementById("trail-empty");
const issuesList = document.getElementById("issues-list");
const issuesEmpty = document.getElementById("issues-empty");

function navigateTo(viewName) {
  for (const [name, el] of Object.entries(views)) {
    el.hidden = name !== viewName;
  }
  titleBarHint.textContent = VIEW_HINTS[viewName] || "";
  if (viewName === "dashboard") {
    updateLlmIndicator();
  }
}

function updateLlmIndicator() {
  if (appState.llmPath === "free") {
    llmIndicatorDot.className = "llm-indicator__dot llm-indicator__dot--free";
    llmIndicatorText.textContent = "Free tier";
    return;
  }

  if (appState.llmPath === "custom") {
    llmIndicatorDot.className = "llm-indicator__dot llm-indicator__dot--custom";
    const { provider, model } = appState.customLlm;
    const label = provider ? provider.charAt(0).toUpperCase() + provider.slice(1) : "Custom";
    llmIndicatorText.textContent = model ? `Custom LLM: ${model}` : `Custom LLM: ${label}`;
  }
}

function syncReviewModeFromForm() {
  appState.reviewMode = form.mode.value;
}

function restoreReviewModeToForm() {
  const radio = form.querySelector(`input[name="mode"][value="${appState.reviewMode}"]`);
  if (radio) radio.checked = true;
}

function setConnectionResult(kind, message) {
  connectionResult.hidden = false;
  connectionResult.textContent = message;
  connectionResult.className = `connection-result connection-result--${kind}`;
}

function clearConnectionResult() {
  connectionResult.hidden = true;
  connectionResult.textContent = "";
  connectionResult.className = "connection-result";
}

function onConfigProviderChange() {
  const selected = configProvider.selectedOptions[0];
  const isCustom = selected?.value === "custom";
  baseUrlField.hidden = !isCustom;

  if (selected?.dataset.defaultModel && !configModel.value) {
    configModel.placeholder = selected.dataset.defaultModel;
  }

  resetCustomLlmVerification();
  configContinue.disabled = true;
  clearConnectionResult();
}

function onConfigFieldChange() {
  resetCustomLlmVerification();
  configContinue.disabled = true;
  clearConnectionResult();
}

/**
 * PLACEHOLDER: replace with a real backend call (e.g. POST /api/llm/test)
 * when connection verification is implemented server-side.
 */
async function testConnection() {
  const apiKey = configApiKey.value.trim();
  const provider = configProvider.value;

  if (!provider) {
    setConnectionResult("error", "Select a provider before testing.");
    return;
  }

  if (!apiKey) {
    setConnectionResult("error", "Couldn't verify this key — enter an API key first.");
    return;
  }

  if (provider === "custom" && !configBaseUrl.value.trim()) {
    setConnectionResult("error", "Endpoint unreachable — enter a base URL for custom endpoints.");
    return;
  }

  testConnectionBtn.disabled = true;
  setConnectionResult("loading", "Testing connection…");

  await new Promise((resolve) => setTimeout(resolve, 450));

  appState.customLlm.connectionVerified = true;
  setConnectionResult("success", "Connection looks good");
  configContinue.disabled = false;
  testConnectionBtn.disabled = false;
}

function setStatus(message, type = "loading") {
  statusEl.hidden = !message;
  statusEl.textContent = message;
  statusEl.className = `status is-${type}`;
}

function hideResults() {
  resultsEl.hidden = true;
  workspaceEmpty.classList.remove("is-hidden");
}

function showResults() {
  resultsEl.hidden = false;
  workspaceEmpty.classList.add("is-hidden");
}

function buildRequestBody() {
  const body = {
    pr_url: prUrlInput.value.trim(),
    mode: appState.reviewMode,
  };

  if (appState.llmPath === "custom") {
    const { provider, model, apiKey } = appState.customLlm;
    body.llm = {};
    if (provider && provider !== "custom") body.llm.provider = provider;
    if (provider === "groq") body.llm.provider = "groq";
    if (provider === "google") body.llm.provider = "google";
    if (model) body.llm.model = model;
    if (apiKey) body.llm.api_key = apiKey;
  }

  return body;
}

function renderTrail(trail, mode) {
  trailList.innerHTML = "";

  if (trail.length === 0) {
    trailEmpty.hidden = false;
    trailEmpty.textContent =
      mode === "baseline"
        ? "Baseline mode does not fetch files — single LLM pass on the diff only."
        : "No files were investigated. The agent judged from the diff alone.";
    return;
  }

  trailEmpty.hidden = true;
  for (const step of trail) {
    const li = document.createElement("li");
    li.className = "trail-item";
    li.innerHTML = `
      <span class="trail-file">${escapeHtml(step.file_path)}</span>
      <p class="trail-reason">${escapeHtml(step.reason)}</p>
    `;
    trailList.appendChild(li);
  }
}

function renderIssues(issues) {
  issuesList.innerHTML = "";

  if (!issues.length) {
    issuesEmpty.hidden = false;
    return;
  }

  issuesEmpty.hidden = true;
  const sorted = [...issues].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
  );

  for (const issue of sorted) {
    const card = document.createElement("article");
    card.className = "issue-card";
    const line = issue.line != null ? `:${issue.line}` : "";
    card.innerHTML = `
      <div class="issue-header">
        <span class="severity-dot severity-dot--${issue.severity}" title="${SEVERITY_LABEL[issue.severity] || issue.severity}"></span>
        <span class="issue-location">${escapeHtml(issue.file)}${line}</span>
        <span class="issue-category">${escapeHtml(issue.category)}</span>
      </div>
      <p class="issue-message">${escapeHtml(issue.message)}</p>
    `;
    issuesList.appendChild(card);
  }
}

function renderVerdict(verdict, mode) {
  confidenceBadge.textContent = `${verdict.confidence} confidence`;
  summaryText.textContent = verdict.summary;
  modeNote.textContent =
    mode === "agent"
      ? `${verdict.investigation_trail.length} file(s) investigated · ${verdict.issues.length} issue(s) found`
      : `Baseline review · ${verdict.issues.length} issue(s) found`;

  partialBanner.hidden = !verdict.partial_investigation;
  renderTrail(verdict.investigation_trail || [], mode);
  renderIssues(verdict.issues || []);
  showResults();
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

chooseFreeTier.addEventListener("click", () => {
  selectFreeTier();
  restoreReviewModeToForm();
  navigateTo("dashboard");
});

chooseOwnLlm.addEventListener("click", () => {
  navigateTo("config");
});

homeLink.addEventListener("click", () => {
  navigateTo("welcome");
});

configBack.addEventListener("click", (event) => {
  event.preventDefault();
  navigateTo("welcome");
});

changeLlmPath.addEventListener("click", () => {
  navigateTo("welcome");
});

configProvider.addEventListener("change", onConfigProviderChange);
configModel.addEventListener("input", onConfigFieldChange);
configApiKey.addEventListener("input", onConfigFieldChange);
configBaseUrl.addEventListener("input", onConfigFieldChange);

testConnectionBtn.addEventListener("click", () => {
  testConnection();
});

configForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!appState.customLlm.connectionVerified) return;

  applyCustomLlmFromForm({
    provider: configProvider.value,
    model: configModel.value.trim(),
    apiKey: configApiKey.value.trim(),
    baseUrl: configBaseUrl.value.trim(),
  });

  restoreReviewModeToForm();
  navigateTo("dashboard");
});

form.addEventListener("change", (event) => {
  if (event.target.name === "mode") {
    syncReviewModeFromForm();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  syncReviewModeFromForm();
  hideResults();
  workspaceEmpty.classList.add("is-hidden");
  submitBtn.disabled = true;
  setStatus("Reviewing… fetching the PR and running the agent. This may take up to a minute.", "loading");

  try {
    const res = await fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildRequestBody()),
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      const detail = data.detail || `Request failed (${res.status})`;
      setStatus(detail, "error");
      return;
    }

    setStatus("");
    renderVerdict(data, appState.reviewMode);
  } catch {
    setStatus("Could not reach the server. Is it running?", "error");
  } finally {
    submitBtn.disabled = false;
  }
});

navigateTo("welcome");
