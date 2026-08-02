import {
  LLM_VENDORS,
  getModelLabel,
  getVendorById,
  getVendorInitial,
  resolveBackendProvider,
} from "./vendors.js";
import { downloadMarkdown, verdictToMarkdown } from "./export.js";
import {
  appState,
  applyCustomLlmFromForm,
  resetCustomLlmVerification,
  restoreSessionToState,
  selectFreeTier,
} from "./state.js";
import {
  loadReviewHistory,
  loadSession,
  loadTheme,
  saveReviewHistory,
  saveSession,
  saveTheme,
} from "./session.js";
import { showToast } from "./toast.js";
import { ReviewStreamError, consumeReviewStream } from "./stream.js";

const CONFIDENCE_TIPS_THRESHOLD = 80;
const READINESS_TIPS_THRESHOLD = 55;
const SEVERITY_LABEL = { error: "High", warning: "Medium", suggestion: "Low" };

const TIMELINE_PHASES = [
  { id: "parse", match: /parsing pull request/i, label: "Parse URL" },
  { id: "metadata", match: /fetching pr metadata/i, label: "Fetch metadata" },
  { id: "diffs", match: /loading changed files/i, label: "Load diffs" },
  { id: "review", match: /starting agent|baseline review/i, label: "Run review" },
  { id: "verdict", match: /verdict ready/i, label: "Verdict" },
];

const views = {
  welcome: document.getElementById("view-welcome"),
  config: document.getElementById("view-config"),
  dashboard: document.getElementById("view-dashboard"),
};

const stepper = document.getElementById("stepper");
const homeLink = document.getElementById("home-link");
const userProfile = document.getElementById("user-profile");
const profileMenu = document.getElementById("profile-menu");
const profileTier = document.getElementById("profile-tier");
const menuChangeLlm = document.getElementById("menu-change-llm");
const themeToggle = document.getElementById("theme-toggle");

const chooseFreeTier = document.getElementById("choose-free-tier");
const chooseOwnLlm = document.getElementById("choose-own-llm");

const configForm = document.getElementById("config-form");
const configBack = document.getElementById("config-back");
const vendorPicker = document.getElementById("vendor-picker");
const modelSelectWrap = document.getElementById("model-select-wrap");
const modelCustomWrap = document.getElementById("model-custom-wrap");
const credentialsWrap = document.getElementById("credentials-wrap");
const configModel = document.getElementById("config-model");
const configModelCustom = document.getElementById("config-model-custom");
const configApiKey = document.getElementById("config-api-key");
const configBaseUrl = document.getElementById("config-base-url");
const baseUrlField = document.getElementById("base-url-field");
const toggleApiKey = document.getElementById("toggle-api-key");
const testConnectionBtn = document.getElementById("test-connection-btn");
const connectionResult = document.getElementById("connection-result");
const configContinue = document.getElementById("config-continue");

const changeLlmPath = document.getElementById("change-llm-path");
const form = document.getElementById("review-form");
const prUrlInput = document.getElementById("pr-url");
const githubTokenInput = document.getElementById("github-token");
const toggleGithubToken = document.getElementById("toggle-github-token");
const submitBtn = document.getElementById("submit-btn");
const submitBtnLabel = document.getElementById("submit-btn-label");
const statusEl = document.getElementById("status");

const reviewTimeline = document.getElementById("review-timeline");
const timelineList = document.getElementById("timeline-list");
const thinkingPanel = document.getElementById("thinking-panel");
const thinkingFeed = document.getElementById("thinking-feed");
const thinkingToggle = document.getElementById("thinking-toggle");
const thinkingCollapsedHint = document.getElementById("thinking-collapsed-hint");
const revisitStreamBtn = document.getElementById("revisit-stream-btn");
const actionsRevisitStream = document.getElementById("actions-revisit-stream");
const budgetIndicator = document.getElementById("budget-indicator");
const streamLostBanner = document.getElementById("stream-lost-banner");
const workspaceEmpty = document.getElementById("workspace-empty");
const resultsEl = document.getElementById("results");
const partialBanner = document.getElementById("partial-banner");
const prTitleEl = document.getElementById("pr-title");
const prMetaChips = document.getElementById("pr-meta-chips");
const prGithubLink = document.getElementById("pr-github-link");
const confidenceChart = document.getElementById("confidence-chart");
const confidenceChartFill = document.getElementById("confidence-chart-fill");
const confidenceValue = document.getElementById("confidence-value");
const confidenceCaption = document.getElementById("confidence-caption");
const confidenceTips = document.getElementById("confidence-tips");
const readinessChart = document.getElementById("readiness-chart");
const readinessChartFill = document.getElementById("readiness-chart-fill");
const readinessValue = document.getElementById("readiness-value");
const readinessCaption = document.getElementById("readiness-caption");
const readinessTips = document.getElementById("readiness-tips");
const contextRepo = document.getElementById("context-repo");
const contextBranches = document.getElementById("context-branches");
const contextBadges = document.getElementById("context-badges");
const progressValue = document.getElementById("progress-value");
const progressCaption = document.getElementById("progress-caption");
const statsGrid = document.getElementById("stats-grid");
const summaryText = document.getElementById("summary-text");
const modeNote = document.getElementById("mode-note");
const trailList = document.getElementById("trail-list");
const trailEmpty = document.getElementById("trail-empty");
const issuesList = document.getElementById("issues-list");
const issuesEmpty = document.getElementById("issues-empty");
const issueFilters = document.getElementById("issue-filters");
const copySummaryBtn = document.getElementById("copy-summary-btn");
const exportReviewBtn = document.getElementById("export-review-btn");
const newReviewBtn = document.getElementById("new-review-btn");
const reviewHistorySection = document.getElementById("review-history-section");
const reviewHistoryList = document.getElementById("review-history-list");

const INSIGHT_VISIBLE_MAX = 4;
const insightLists = {
  good: document.getElementById("insight-good-list"),
  risk: document.getElementById("insight-risk-list"),
  improve: document.getElementById("insight-improve-list"),
};
const insightMore = {
  good: document.getElementById("insight-good-more"),
  risk: document.getElementById("insight-risk-more"),
  improve: document.getElementById("insight-improve-more"),
};

let selectedVendorId = "";
let capturedPrMetadata = null;
let thinkingStepCount = 0;
let thinkingPanelReviewComplete = false;
let currentVerdict = null;
let currentPrUrl = "";
let currentReviewMode = "agent";
let activeIssueFilter = "all";
let timelineState = {};

function persistSession() {
  saveSession(appState, { prUrl: prUrlInput?.value?.trim() || "" });
}

function navigateTo(viewName, { animate = true } = {}) {
  for (const [name, el] of Object.entries(views)) {
    const show = name === viewName;
    el.hidden = !show;
    if (show && animate) {
      el.classList.remove("view--enter");
      void el.offsetWidth;
      el.classList.add("view--enter");
    }
  }
  updateStepper(viewName);
  updateProfileTier();
  closeProfileMenu();
  persistSession();
}

function updateStepper(activeView) {
  const order = { welcome: 1, config: 2, dashboard: 3 };
  const activeIndex = order[activeView] || 1;

  for (const btn of stepper.querySelectorAll(".stepper__step")) {
    const step = btn.dataset.step;
    const index = Number(btn.dataset.stepIndex);
    btn.classList.toggle("stepper__step--active", step === activeView);
    btn.classList.toggle("stepper__step--done", index < activeIndex);

    const marker = btn.querySelector(".stepper__marker");
    if (index < activeIndex) {
      marker.textContent = "✓";
    } else {
      marker.textContent = String(index);
    }

    const canNavigate =
      (step === "welcome") ||
      (step === "config" && appState.llmPath === "custom") ||
      (step === "dashboard" && appState.llmPath != null);

    btn.disabled = !canNavigate || step === activeView;
  }
}

function updateProfileTier() {
  if (appState.llmPath === "free") {
    profileTier.textContent = "Free tier";
    profileTier.className = "user-profile__tier user-profile__tier--free";
    return;
  }
  if (appState.llmPath === "custom") {
    const { modelLabel, vendorLabel } = appState.customLlm;
    profileTier.textContent = modelLabel || vendorLabel || "Custom";
    profileTier.className = "user-profile__tier user-profile__tier--custom";
    return;
  }
  profileTier.textContent = "Not configured";
  profileTier.className = "user-profile__tier";
}

function openProfileMenu() {
  profileMenu.hidden = false;
  userProfile.setAttribute("aria-expanded", "true");
}

function closeProfileMenu() {
  profileMenu.hidden = true;
  userProfile.setAttribute("aria-expanded", "false");
}

function toggleProfileMenu(event) {
  event.stopPropagation();
  if (profileMenu.hidden) {
    const rect = userProfile.getBoundingClientRect();
    profileMenu.style.top = `${rect.bottom + 4}px`;
    profileMenu.style.left = `${rect.left}px`;
    openProfileMenu();
  } else {
    closeProfileMenu();
  }
}

function applyTheme(theme) {
  const isDark = theme === "dark";
  document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");
  saveTheme(theme);
  themeToggle.setAttribute("aria-label", isDark ? "Switch to light mode" : "Switch to dark mode");
  themeToggle.setAttribute("title", isDark ? "Light mode" : "Dark mode");
  themeToggle.classList.toggle("theme-toggle--dark", isDark);
}

function toggleTheme() {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  applyTheme(next);
  showToast(`${next === "dark" ? "Dark" : "Light"} mode enabled`, { type: "info" });
}

function formatPrTitle(prUrl, metadata) {
  const title = metadata?.title;
  const num = metadata?.pr_number || parsePrFromUrl(prUrl)?.number;
  if (num && title) return `PR #${num} (${title})`;
  if (num) return `PR #${num}`;
  return title || "Pull request review";
}

function parsePrFromUrl(url) {
  const match = url.trim().match(/github\.com\/([^/]+)\/([^/]+)\/pull\/(\d+)/i);
  if (!match) return null;
  return { owner: match[1], repo: match[2], number: match[3] };
}

function githubFileUrl(metadata, filePath, line) {
  if (!metadata?.owner || !metadata?.head_sha) return null;
  const base = `https://github.com/${metadata.owner}/${metadata.repo}/blob/${metadata.head_sha}/${filePath}`;
  return line != null ? `${base}#L${line}` : base;
}

function githubPrUrl(metadata, prUrl) {
  return metadata?.html_url || prUrl || "#";
}

function initTimeline() {
  timelineState = {};
  for (const phase of TIMELINE_PHASES) {
    timelineState[phase.id] = "pending";
  }
  timelineList.innerHTML = "";
  for (const phase of TIMELINE_PHASES) {
    const li = document.createElement("li");
    li.className = "timeline-item";
    li.dataset.phase = phase.id;
    li.innerHTML = `<span class="timeline-item__dot"></span>${phase.label}`;
    timelineList.appendChild(li);
  }
  reviewTimeline.hidden = false;
  renderTimeline();
}

function setTimelinePhase(statusText) {
  for (const phase of TIMELINE_PHASES) {
    if (phase.match.test(statusText)) {
      timelineState[phase.id] = "active";
      for (const p of TIMELINE_PHASES) {
        if (p.id === phase.id) break;
        timelineState[p.id] = "done";
      }
      break;
    }
  }
  if (/verdict ready/i.test(statusText)) {
    timelineState.verdict = "done";
  }
  renderTimeline();
}

function renderTimeline() {
  for (const li of timelineList.querySelectorAll(".timeline-item")) {
    const state = timelineState[li.dataset.phase] || "pending";
    li.classList.toggle("timeline-item--active", state === "active");
    li.classList.toggle("timeline-item--done", state === "done");
  }
}

function hideTimeline() {
  reviewTimeline.hidden = true;
}

function initVendorPicker() {
  vendorPicker.innerHTML = "";
  for (const vendor of LLM_VENDORS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "vendor-card";
    btn.dataset.vendorId = vendor.id;
    btn.setAttribute("role", "radio");
    btn.setAttribute("aria-checked", "false");
    btn.innerHTML = `
      <span class="vendor-card__badge">${escapeHtml(getVendorInitial(vendor.label))}</span>
      <span class="vendor-card__label">${escapeHtml(vendor.label)}</span>
      <span class="vendor-card__hint">${escapeHtml(vendor.hint || "")}</span>
      <span class="vendor-card__check" aria-hidden="true"></span>
    `;
    btn.addEventListener("click", () => selectVendor(vendor.id));
    vendorPicker.appendChild(btn);
  }
}

function highlightSelectedVendor() {
  for (const btn of vendorPicker.querySelectorAll(".vendor-card")) {
    const isSelected = btn.dataset.vendorId === selectedVendorId;
    btn.classList.toggle("vendor-card--selected", isSelected);
    btn.setAttribute("aria-checked", isSelected ? "true" : "false");
    const check = btn.querySelector(".vendor-card__check");
    if (check) check.textContent = isSelected ? "✓" : "";
  }
}

function updateConfigFormVisibility() {
  const vendor = getActiveVendor();
  const hasVendor = Boolean(vendor);
  const isCustom = Boolean(vendor?.customEndpoint);
  const { modelId } = readModelSelection();

  modelSelectWrap.hidden = !hasVendor || isCustom;
  modelCustomWrap.hidden = !hasVendor || !isCustom;
  baseUrlField.hidden = !isCustom;
  credentialsWrap.hidden = !hasVendor || (!isCustom && !modelId) || (isCustom && !modelId);
}

function populateModelDropdown(vendor) {
  configModel.innerHTML = "";
  if (!vendor || vendor.customEndpoint) return;

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Select a model…";
  configModel.appendChild(placeholder);

  for (const model of vendor.models) {
    const opt = document.createElement("option");
    opt.value = model.id;
    opt.textContent = model.label;
    configModel.appendChild(opt);
  }
}

function selectVendor(vendorId, { modelId = "", skipReset = false } = {}) {
  selectedVendorId = vendorId;
  const vendor = getVendorById(vendorId);
  highlightSelectedVendor();
  populateModelDropdown(vendor);

  const isCustom = Boolean(vendor?.customEndpoint);
  if (isCustom) {
    configModelCustom.value = modelId;
  } else if (vendor) {
    configModel.value = modelId || (vendor.models.length === 1 ? vendor.models[0].id : "");
  }

  if (!skipReset) {
    resetCustomLlmVerification();
    configContinue.disabled = true;
    clearConnectionResult();
  }
  updateConfigFormVisibility();
}

function getActiveVendor() {
  return getVendorById(selectedVendorId);
}

function readModelSelection() {
  const vendor = getActiveVendor();
  if (!vendor) return { modelId: "", modelLabel: "" };
  if (vendor.customEndpoint) {
    const modelId = configModelCustom.value.trim();
    return { modelId, modelLabel: modelId };
  }
  const modelId = configModel.value;
  return { modelId, modelLabel: getModelLabel(vendor, modelId) };
}

function restoreConfigFormFromState() {
  const saved = appState.customLlm;
  if (!saved.vendorId) {
    selectedVendorId = "";
    highlightSelectedVendor();
    credentialsWrap.hidden = true;
    modelSelectWrap.hidden = true;
    configApiKey.value = "";
    configBaseUrl.value = "";
    configContinue.disabled = true;
    clearConnectionResult();
    return;
  }
  selectVendor(saved.vendorId, { modelId: saved.modelId, skipReset: true });
  configApiKey.value = saved.apiKey;
  configBaseUrl.value = saved.baseUrl;
  configContinue.disabled = !saved.connectionVerified;
  if (saved.connectionVerified) {
    setConnectionResult("success", "Connection looks good");
  }
  updateConfigFormVisibility();
}

function setConnectionResult(kind, message) {
  connectionResult.hidden = false;
  connectionResult.textContent = message;
  connectionResult.className = `connection-result connection-result--${kind}`;
  if (kind === "success") showToast(message, { type: "success" });
  if (kind === "error") showToast(message, { type: "error" });
}

function clearConnectionResult() {
  connectionResult.hidden = true;
  connectionResult.textContent = "";
  connectionResult.className = "connection-result";
}

function onConfigFieldChange() {
  resetCustomLlmVerification();
  configContinue.disabled = true;
  clearConnectionResult();
  updateConfigFormVisibility();
}

async function testConnection() {
  const vendor = getActiveVendor();
  const { modelId } = readModelSelection();
  const apiKey = configApiKey.value.trim();
  const provider = resolveBackendProvider(vendor);

  if (!vendor) return setConnectionResult("error", "Select a vendor before testing.");
  if (!apiKey) return setConnectionResult("error", "Enter an API key first.");
  if (vendor.customEndpoint && !configBaseUrl.value.trim()) {
    return setConnectionResult("error", "Enter a base URL for custom endpoints.");
  }
  if (!modelId) return setConnectionResult("error", "Select or enter a model first.");

  testConnectionBtn.disabled = true;
  setConnectionResult("loading", "Testing connection…");

  try {
    const res = await fetch("/api/configure-llm/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model: modelId, api_key: apiKey }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Connection failed");

    appState.customLlm.connectionVerified = true;
    setConnectionResult("success", `Connected to ${data.provider}/${data.model}`);
    configContinue.disabled = false;
  } catch (err) {
    setConnectionResult("error", err.message || "Could not verify connection.");
  } finally {
    testConnectionBtn.disabled = false;
  }
}

function setStatus(message, type = "loading") {
  statusEl.hidden = !message;
  statusEl.textContent = message;
  statusEl.className = `status is-${type}`;
}

function hideResults() {
  resultsEl.hidden = true;
  resultsEl.classList.remove("results--revealed");
  workspaceEmpty.classList.remove("is-hidden");
}

function showResults() {
  resultsEl.hidden = false;
  resultsEl.classList.add("results--revealed");
  workspaceEmpty.classList.add("is-hidden");
}

function resetThinkingPanel() {
  thinkingFeed.innerHTML = "";
  thinkingPanel.hidden = true;
  thinkingPanel.classList.remove("thinking-panel--collapsed");
  thinkingStepCount = 0;
  thinkingPanelReviewComplete = false;
  budgetIndicator.hidden = true;
  streamLostBanner.hidden = true;
  updateThinkingStreamControls();
}

function showThinkingPanel() {
  thinkingPanel.hidden = false;
  thinkingPanel.classList.remove("thinking-panel--collapsed");
  updateThinkingStreamControls();
}

function collapseThinkingPanel(stepCount) {
  thinkingStepCount = stepCount;
  thinkingPanelReviewComplete = true;
  thinkingPanel.classList.add("thinking-panel--collapsed");
  updateThinkingStreamControls();
}

function expandThinkingPanel({ scrollIntoView = false } = {}) {
  thinkingPanel.hidden = false;
  thinkingPanel.classList.remove("thinking-panel--collapsed");
  updateThinkingStreamControls();
  thinkingFeed.scrollTop = thinkingFeed.scrollHeight;
  if (scrollIntoView) thinkingPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function updateThinkingStreamControls() {
  const hasStream = thinkingFeed.children.length > 0;
  const collapsed = thinkingPanel.classList.contains("thinking-panel--collapsed");
  const showControls = thinkingPanelReviewComplete && hasStream && !thinkingPanel.hidden;

  thinkingToggle.hidden = !showControls;
  thinkingCollapsedHint.hidden = !(showControls && collapsed);
  revisitStreamBtn.hidden = !showControls;
  actionsRevisitStream.hidden = !showControls;

  if (showControls) {
    thinkingCollapsedHint.textContent = `${thinkingStepCount} step(s) recorded. Expand to read the full response.`;
    thinkingToggle.textContent = collapsed ? "View response" : "Hide response";
    thinkingToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }
}

function appendThinkingEntry(kind, bodyHtml) {
  const entry = document.createElement("article");
  entry.className = `thinking-entry thinking-entry--${kind} thinking-entry--pulse`;
  entry.innerHTML = `<span class="thinking-entry__icon" aria-hidden="true"></span><div class="thinking-entry__body">${bodyHtml}</div>`;
  const icon = entry.querySelector(".thinking-entry__icon");
  const icons = { thought: "·", status: "·", "tool-call": "→", "tool-result--ok": "✓", "tool-result--fail": "✕" };
  icon.textContent = icons[kind] || "·";
  thinkingFeed.appendChild(entry);
  thinkingFeed.scrollTop = thinkingFeed.scrollHeight;
  updateThinkingStreamControls();
}

function updateBudgetIndicator(used, max) {
  if (max == null) return;
  budgetIndicator.hidden = false;
  budgetIndicator.textContent = used === 0 ? `Up to ${max} investigations` : `Investigation ${used} of ${max}`;
}

function handleStreamEvent(event) {
  switch (event.type) {
    case "status":
      setTimelinePhase(event.text || "");
      appendThinkingEntry("status", escapeHtml(event.text || ""));
      break;
    case "thought":
      appendThinkingEntry("thought", escapeHtml(event.text || ""));
      if (/verdict ready/i.test(event.text || "")) setTimelinePhase(event.text);
      break;
    case "pr_metadata":
      capturedPrMetadata = event.data;
      break;
    case "budget":
      updateBudgetIndicator(event.used ?? 0, event.max);
      break;
    case "tool_call": {
      const parts = [`Opening <span class="thinking-entry__file">${escapeHtml(event.file || "")}</span>`];
      if (event.reason) parts.push(`<span class="thinking-entry__note">${escapeHtml(event.reason)}</span>`);
      appendThinkingEntry("tool-call", parts.join(""));
      break;
    }
    case "tool_result":
      appendThinkingEntry(
        event.success ? "tool-result--ok" : "tool-result--fail",
        `<span class="thinking-entry__file">${escapeHtml(event.file || "")}</span><span class="thinking-entry__note">${escapeHtml(event.note || "")}</span>`
      );
      break;
    case "error":
      throw new ReviewStreamError(event.detail || "Review failed.", event.status || 500);
    default:
      break;
  }
}

async function runReviewWithStream(body) {
  let verdict = null;
  let stepCount = 0;

  const response = await fetch("/api/review/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const sawDone = await consumeReviewStream(response, (event) => {
    if (event.type === "verdict") {
      verdict = event.data;
      return;
    }
    if (event.type !== "done") stepCount += 1;
    handleStreamEvent(event);
  });

  return { verdict, sawDone, stepCount };
}

function buildRequestBody() {
  const body = { pr_url: prUrlInput.value.trim(), mode: appState.reviewMode };
  const githubToken = githubTokenInput.value.trim();
  if (githubToken) {
    body.github_token = githubToken;
  }
  if (appState.llmPath === "custom") {
    const { vendorId, modelId, apiKey } = appState.customLlm;
    if (!apiKey) {
      throw new ReviewStreamError("Re-enter your API key in LLM setup before reviewing.", 400);
    }
    const vendor = getVendorById(vendorId);
    const provider = resolveBackendProvider(vendor);
    if (provider && modelId) {
      body.llm = { provider, model: modelId, api_key: apiKey };
    }
  }
  return body;
}

function fileIcon(path) {
  const ext = (path.split(".").pop() || "").toLowerCase();
  const map = { py: "🐍", js: "📜", ts: "📘", json: "📋", md: "📝", yml: "⚙️", yaml: "⚙️" };
  return map[ext] || "📄";
}

function renderTrail(trail, mode) {
  trailList.innerHTML = "";
  if (!trail.length) {
    trailEmpty.hidden = false;
    trailEmpty.textContent =
      mode === "baseline"
        ? "Baseline mode does not fetch files. Single LLM pass on the diff only."
        : "No files were investigated. The agent judged from the diff alone.";
    return;
  }
  trailEmpty.hidden = true;
  for (const step of trail) {
    const li = document.createElement("li");
    li.className = "trail-item";
    const url = githubFileUrl(capturedPrMetadata, step.file_path);
    const fileHtml = url
      ? `<a class="trail-file trail-file--link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="trail-file-icon">${fileIcon(step.file_path)}</span>${escapeHtml(step.file_path)}</a>`
      : `<span class="trail-file"><span class="trail-file-icon">${fileIcon(step.file_path)}</span>${escapeHtml(step.file_path)}</span>`;
    li.innerHTML = `${fileHtml}<p class="trail-reason">${escapeHtml(step.reason)}</p>`;
    trailList.appendChild(li);
  }
}

function renderInsightBucket(key, items) {
  const listEl = insightLists[key];
  const moreEl = insightMore[key];
  listEl.innerHTML = "";
  moreEl.hidden = true;
  const bullets = items || [];
  if (!bullets.length) {
    listEl.className = "insight-list insight-list--empty";
    const li = document.createElement("li");
    li.textContent = "Nothing noted.";
    listEl.appendChild(li);
    return;
  }
  listEl.className = "insight-list";
  for (const text of bullets.slice(0, INSIGHT_VISIBLE_MAX)) {
    const li = document.createElement("li");
    li.textContent = text;
    listEl.appendChild(li);
  }
  const remaining = bullets.length - INSIGHT_VISIBLE_MAX;
  if (remaining > 0) {
    moreEl.hidden = false;
    moreEl.innerHTML = `+${remaining} more in the <a href="#issues-section">detailed issues</a> below.`;
  }
}

function renderInsights(insights) {
  const data = insights || {};
  renderInsightBucket("good", data.whats_good);
  renderInsightBucket("risk", data.risks);
  renderInsightBucket("improve", data.improvements);
}

function renderIssues(issues) {
  issuesList.innerHTML = "";
  const filtered =
    activeIssueFilter === "all" ? issues : issues.filter((i) => i.severity === activeIssueFilter);

  if (!filtered.length) {
    issuesEmpty.hidden = false;
    issuesEmpty.textContent =
      issues.length && activeIssueFilter !== "all"
        ? "No issues at this severity level."
        : "No issues flagged in this review.";
    return;
  }
  issuesEmpty.hidden = true;

  const sorted = [...filtered].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
  );

  for (const issue of sorted) {
    const card = document.createElement("article");
    card.className = "issue-card";
    const line = issue.line != null ? `:${issue.line}` : "";
    const fileUrl = githubFileUrl(capturedPrMetadata, issue.file, issue.line);
    const locationHtml = fileUrl
      ? `<a class="issue-location issue-location--link" href="${escapeHtml(fileUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(issue.file)}${line}</a>`
      : `<span class="issue-location">${escapeHtml(issue.file)}${line}</span>`;

    card.innerHTML = `
      <div class="issue-header">
        <span class="severity-dot severity-dot--${issue.severity}" title="${SEVERITY_LABEL[issue.severity]}"></span>
        ${locationHtml}
        <span class="issue-category">${escapeHtml(issue.category)}</span>
      </div>
      <p class="issue-message">${escapeHtml(issue.message)}</p>
      <div class="issue-card__actions">
        <button type="button" class="issue-copy-btn">Copy</button>
      </div>
    `;
    card.querySelector(".issue-copy-btn")?.addEventListener("click", async () => {
      const text = `${issue.file}${line}: ${issue.message}`;
      await navigator.clipboard.writeText(text);
      showToast("Issue copied", { type: "success", duration: 2000 });
    });
    issuesList.appendChild(card);
  }
}

function renderMetricChart({
  chartEl,
  fillEl,
  valueEl,
  captionEl,
  tipsEl,
  score,
  fallbackLevel,
  rationale,
  tips,
  tipsThreshold,
  fallbackCaption,
}) {
  const resolvedScore =
    score ??
    (fallbackLevel === "high" ? 72 : fallbackLevel === "medium" ? 48 : 25);
  const level =
    resolvedScore >= 72 ? "high" : resolvedScore >= 48 ? "medium" : "low";
  chartEl.className = `metric-chart ${chartEl.id} metric-chart--${level}`;
  fillEl.setAttribute("stroke-dasharray", `${resolvedScore}, 100`);
  valueEl.textContent = `${resolvedScore}%`;
  captionEl.textContent = rationale || fallbackCaption || `${resolvedScore}%`;

  if (!tipsEl) return;
  if (resolvedScore < tipsThreshold && tips?.length) {
    tipsEl.hidden = false;
    tipsEl.innerHTML = tips.map((tip) => `<li>${escapeHtml(tip)}</li>`).join("");
  } else {
    tipsEl.hidden = true;
    tipsEl.innerHTML = "";
  }
}

function renderReadinessChart(verdict) {
  renderMetricChart({
    chartEl: readinessChart,
    fillEl: readinessChartFill,
    valueEl: readinessValue,
    captionEl: readinessCaption,
    tipsEl: readinessTips,
    score: verdict.pr_readiness_score,
    fallbackLevel: verdict.pr_readiness || verdict.confidence,
    rationale: verdict.pr_readiness_rationale,
    tips: verdict.pr_readiness_tips,
    tipsThreshold: READINESS_TIPS_THRESHOLD,
    fallbackCaption: "PR readiness",
  });
}

function renderConfidenceChart(verdict) {
  renderMetricChart({
    chartEl: confidenceChart,
    fillEl: confidenceChartFill,
    valueEl: confidenceValue,
    captionEl: confidenceCaption,
    tipsEl: confidenceTips,
    score: verdict.confidence_score,
    fallbackLevel: verdict.confidence,
    rationale: verdict.confidence_rationale,
    tips: verdict.confidence_tips,
    tipsThreshold: CONFIDENCE_TIPS_THRESHOLD,
    fallbackCaption: `${verdict.confidence_score ?? ""}% review confidence`.trim(),
  });
}

function renderStats(issues) {
  const counts = { error: 0, warning: 0, suggestion: 0 };
  for (const issue of issues || []) {
    if (issue.severity in counts) counts[issue.severity] += 1;
  }
  statsGrid.innerHTML = "";
  for (const [severity, label] of [
    ["error", "High"],
    ["warning", "Medium"],
    ["suggestion", "Low"],
  ]) {
    const item = document.createElement("div");
    item.className = `stat-item stat-item--${severity}`;
    item.innerHTML = `<span class="stat-item__count">${counts[severity]}</span><span class="stat-item__label">${label}</span>`;
    statsGrid.appendChild(item);
  }
}

function renderContext(metadata, mode) {
  if (!metadata) {
    contextRepo.textContent = "-";
    contextBranches.textContent = "";
    contextBadges.innerHTML = "";
    return;
  }
  contextRepo.textContent = `${metadata.owner}/${metadata.repo}`;
  contextBranches.textContent = `${metadata.head_ref} → ${metadata.base_ref}`;
  const chips = [];
  if (metadata.changed_files != null) chips.push(`${metadata.changed_files} files`);
  if (metadata.additions != null) chips.push(`+${metadata.additions}`);
  if (metadata.deletions != null) chips.push(`-${metadata.deletions}`);
  contextBadges.innerHTML = chips.map((c) => `<span class="meta-chip">${escapeHtml(c)}</span>`).join("");
  contextBadges.innerHTML += `<span class="meta-chip">${mode === "agent" ? "Agent" : "Baseline"}</span>`;
  if (appState.llmPath === "free") {
    contextBadges.innerHTML += `<span class="meta-chip">Free tier</span>`;
  } else if (appState.customLlm.modelLabel) {
    contextBadges.innerHTML += `<span class="meta-chip">${escapeHtml(appState.customLlm.modelLabel)}</span>`;
  }
}

function renderPrHeader(prUrl) {
  const title = formatPrTitle(prUrl, capturedPrMetadata);
  prTitleEl.textContent = title;
  prMetaChips.innerHTML = "";
  if (capturedPrMetadata?.head_ref) {
    prMetaChips.innerHTML = `<span class="meta-chip">${escapeHtml(capturedPrMetadata.head_ref)} → ${escapeHtml(capturedPrMetadata.base_ref)}</span>`;
  }
  const ghUrl = githubPrUrl(capturedPrMetadata, prUrl);
  prGithubLink.href = ghUrl;
  prGithubLink.hidden = !ghUrl || ghUrl === "#";
}

function renderProgress(verdict, mode) {
  const trailLen = verdict.investigation_trail?.length ?? 0;
  const issueLen = verdict.issues?.length ?? 0;
  if (mode === "baseline") {
    progressValue.textContent = "Baseline";
    progressCaption.textContent = "Single LLM pass on the diff only";
  } else if (trailLen === 0) {
    progressValue.textContent = "Diff only";
    progressCaption.textContent = "No extra files investigated";
  } else {
    progressValue.textContent = `${trailLen} file${trailLen === 1 ? "" : "s"}`;
    progressCaption.textContent = verdict.partial_investigation
      ? "Investigation stopped early"
      : "Files read beyond the diff";
  }
  modeNote.textContent =
    mode === "agent"
      ? `${trailLen} file(s) investigated · ${issueLen} issue(s) found`
      : `Baseline review · ${issueLen} issue(s) found`;
}

function renderVerdict(verdict, mode, prUrl, { fromHistory = false } = {}) {
  currentVerdict = verdict;
  currentPrUrl = prUrl;
  currentReviewMode = mode;
  activeIssueFilter = "all";
  for (const btn of issueFilters.querySelectorAll(".issue-filter")) {
    btn.classList.toggle("issue-filter--active", btn.dataset.filter === "all");
    btn.setAttribute("aria-selected", btn.dataset.filter === "all" ? "true" : "false");
  }

  renderPrHeader(prUrl);
  renderContext(capturedPrMetadata, mode);
  renderReadinessChart(verdict);
  renderConfidenceChart(verdict);
  renderProgress(verdict, mode);
  renderStats(verdict.issues);
  summaryText.textContent = verdict.summary;
  partialBanner.hidden = !verdict.partial_investigation;
  renderInsights(verdict.insights);
  renderTrail(verdict.investigation_trail || [], mode);
  renderIssues(verdict.issues || []);
  showResults();
  hideTimeline();

  if (!fromHistory) {
    saveReviewHistory({
      id: Date.now(),
      prUrl,
      prTitle: formatPrTitle(prUrl, capturedPrMetadata),
      confidence: verdict.confidence,
      issueCount: verdict.issues?.length ?? 0,
      mode,
      metadata: capturedPrMetadata,
      verdict,
      timestamp: new Date().toISOString(),
    });
    renderReviewHistory();
    showToast("Review complete", { type: "success" });
  }
}

function renderReviewHistory() {
  const history = loadReviewHistory();
  reviewHistorySection.hidden = !history.length;
  reviewHistoryList.innerHTML = "";
  for (const item of history) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "review-history__item";
    btn.innerHTML = `<span class="review-history__title">${escapeHtml(item.prTitle)}</span><span class="review-history__meta">${escapeHtml(item.confidence)} · ${item.issueCount} issues</span>`;
    btn.addEventListener("click", () => {
      capturedPrMetadata = item.metadata;
      prUrlInput.value = item.prUrl;
      renderVerdict(item.verdict, item.mode, item.prUrl, { fromHistory: true });
      collapseThinkingPanel(0);
      thinkingPanel.hidden = true;
      navigateTo("dashboard", { animate: false });
    });
    li.appendChild(btn);
    reviewHistoryList.appendChild(li);
  }
}

function clearReviewWorkspace() {
  hideResults();
  resetThinkingPanel();
  hideTimeline();
  capturedPrMetadata = null;
  currentVerdict = null;
  workspaceEmpty.classList.remove("is-hidden");
}

function setSubmitLoading(loading) {
  submitBtn.disabled = loading;
  submitBtn.classList.toggle("primary-button--loading", loading);
  submitBtnLabel.textContent = loading ? "Reviewing…" : "Initiate AI Review";
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// Event listeners

stepper.addEventListener("click", (e) => {
  const btn = e.target.closest(".stepper__step");
  if (!btn || btn.disabled) return;
  const step = btn.dataset.step;
  if (step === "welcome") navigateTo("welcome");
  else if (step === "config") {
    restoreConfigFormFromState();
    navigateTo("config");
  } else if (step === "dashboard" && appState.llmPath) navigateTo("dashboard");
});

userProfile.addEventListener("click", toggleProfileMenu);
document.addEventListener("click", () => closeProfileMenu());
profileMenu.addEventListener("click", (e) => e.stopPropagation());

menuChangeLlm.addEventListener("click", () => {
  navigateTo("welcome");
  closeProfileMenu();
});

themeToggle.addEventListener("click", () => {
  toggleTheme();
});

homeLink.addEventListener("click", (e) => {
  e.preventDefault();
  if (appState.llmPath) navigateTo("dashboard");
  else navigateTo("welcome");
});

thinkingToggle.addEventListener("click", () => {
  if (thinkingPanel.classList.contains("thinking-panel--collapsed")) expandThinkingPanel();
  else collapseThinkingPanel(thinkingStepCount);
});
revisitStreamBtn.addEventListener("click", () => expandThinkingPanel({ scrollIntoView: true }));
actionsRevisitStream.addEventListener("click", () => expandThinkingPanel({ scrollIntoView: true }));

chooseFreeTier.addEventListener("click", () => {
  selectFreeTier();
  persistSession();
  restoreReviewModeToForm();
  navigateTo("dashboard");
});
chooseOwnLlm.addEventListener("click", () => {
  restoreConfigFormFromState();
  navigateTo("config");
});

configBack.addEventListener("click", (e) => {
  e.preventDefault();
  navigateTo("welcome");
});
changeLlmPath.addEventListener("click", () => navigateTo("welcome"));

toggleApiKey.addEventListener("click", () => {
  const show = configApiKey.type === "password";
  configApiKey.type = show ? "text" : "password";
  toggleApiKey.textContent = show ? "Hide" : "Show";
});

toggleGithubToken.addEventListener("click", () => {
  const show = githubTokenInput.type === "password";
  githubTokenInput.type = show ? "text" : "password";
  toggleGithubToken.textContent = show ? "Hide" : "Show";
});

configModel.addEventListener("change", onConfigFieldChange);
configModelCustom.addEventListener("input", onConfigFieldChange);
configApiKey.addEventListener("input", onConfigFieldChange);
configBaseUrl.addEventListener("input", onConfigFieldChange);
testConnectionBtn.addEventListener("click", testConnection);

configForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!appState.customLlm.connectionVerified) return;
  const vendor = getActiveVendor();
  if (!vendor) return;
  const { modelId, modelLabel } = readModelSelection();
  applyCustomLlmFromForm({
    vendorId: vendor.id,
    vendorLabel: vendor.label,
    modelId,
    modelLabel,
    apiKey: configApiKey.value.trim(),
    baseUrl: configBaseUrl.value.trim(),
  });
  persistSession();
  restoreReviewModeToForm();
  navigateTo("dashboard");
});

form.addEventListener("change", (e) => {
  if (e.target.name === "mode") {
    appState.reviewMode = form.mode.value;
    persistSession();
  }
});

issueFilters.addEventListener("click", (e) => {
  const btn = e.target.closest(".issue-filter");
  if (!btn || !currentVerdict) return;
  activeIssueFilter = btn.dataset.filter;
  for (const b of issueFilters.querySelectorAll(".issue-filter")) {
    b.classList.toggle("issue-filter--active", b === btn);
    b.setAttribute("aria-selected", b === btn ? "true" : "false");
  }
  renderIssues(currentVerdict.issues || []);
});

copySummaryBtn.addEventListener("click", async () => {
  if (!currentVerdict) return;
  const md = verdictToMarkdown({
    verdict: currentVerdict,
    prTitle: formatPrTitle(currentPrUrl, capturedPrMetadata),
    prUrl: currentPrUrl,
    mode: currentReviewMode,
    prMetadata: capturedPrMetadata,
  });
  await navigator.clipboard.writeText(md);
  showToast("Summary copied to clipboard", { type: "success" });
});

exportReviewBtn.addEventListener("click", () => {
  if (!currentVerdict) return;
  const md = verdictToMarkdown({
    verdict: currentVerdict,
    prTitle: formatPrTitle(currentPrUrl, capturedPrMetadata),
    prUrl: currentPrUrl,
    mode: currentReviewMode,
    prMetadata: capturedPrMetadata,
  });
  const slug = (capturedPrMetadata?.pr_number || "review").toString();
  downloadMarkdown(`pr-review-${slug}.md`, md);
  showToast("Review exported", { type: "success" });
});

newReviewBtn.addEventListener("click", () => {
  clearReviewWorkspace();
  prUrlInput.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  appState.reviewMode = form.mode.value;
  persistSession();
  const prUrl = prUrlInput.value.trim();
  capturedPrMetadata = null;
  clearReviewWorkspace();
  workspaceEmpty.classList.add("is-hidden");
  setSubmitLoading(true);
  setStatus("");
  initTimeline();
  showThinkingPanel();
  showToast("Review started", { type: "info", duration: 2500 });

  let stepCount = 0;
  let verdict = null;

  try {
    const result = await runReviewWithStream(buildRequestBody());
    verdict = result.verdict;
    stepCount = result.stepCount;

    if (!verdict) {
      streamLostBanner.hidden = false;
      setStatus("Connection lost before the review finished.", "error");
      showToast("Connection lost", { type: "error" });
      workspaceEmpty.classList.remove("is-hidden");
      return;
    }
    if (!result.sawDone) streamLostBanner.hidden = false;

    collapseThinkingPanel(stepCount);
    renderVerdict(verdict, appState.reviewMode, prUrl);
  } catch (err) {
    const msg = err instanceof ReviewStreamError ? err.message : "Could not reach the server.";
    setStatus(msg, "error");
    showToast(msg, { type: "error" });
    workspaceEmpty.classList.remove("is-hidden");
    if (verdict) {
      collapseThinkingPanel(stepCount);
      renderVerdict(verdict, appState.reviewMode, prUrl);
    }
  } finally {
    setSubmitLoading(false);
  }
});

document.addEventListener("keydown", (e) => {
  if (views.dashboard.hidden) return;
  if (e.key === "/" && document.activeElement !== prUrlInput) {
    e.preventDefault();
    prUrlInput.focus();
  }
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && document.activeElement === prUrlInput) {
    e.preventDefault();
    if (!submitBtn.disabled) form.requestSubmit();
  }
});

function restoreReviewModeToForm() {
  const radio = form.querySelector(`input[name="mode"][value="${appState.reviewMode}"]`);
  if (radio) radio.checked = true;
}

function boot() {
  applyTheme(loadTheme());
  initVendorPicker();

  const session = loadSession();
  if (session) {
    restoreSessionToState(session);
    if (session.prUrl) prUrlInput.value = session.prUrl;
    restoreReviewModeToForm();
    if (session.customLlm?.vendorId) {
      restoreConfigFormFromState();
      if (session.customLlm.apiKey) {
        appState.customLlm.apiKey = session.customLlm.apiKey;
      }
    }
  }

  renderReviewHistory();
  updateProfileTier();

  if (appState.llmPath) {
    navigateTo("dashboard", { animate: false });
    if (appState.llmPath === "custom" && !appState.customLlm.apiKey) {
      showToast("Re-enter your API key to run reviews with your LLM", { type: "info", duration: 6000 });
    }
  } else {
    navigateTo("welcome", { animate: false });
  }
}

boot();
