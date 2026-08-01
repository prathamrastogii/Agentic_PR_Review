const SESSION_KEY = "pr-review-agent";
const HISTORY_KEY = "pr-review-history";
const MAX_HISTORY = 5;

export function saveSession(state, { prUrl = "" } = {}) {
  const payload = {
    llmPath: state.llmPath,
    reviewMode: state.reviewMode,
    prUrl,
    customLlm: {
      vendorId: state.customLlm.vendorId,
      vendorLabel: state.customLlm.vendorLabel,
      modelId: state.customLlm.modelId,
      modelLabel: state.customLlm.modelLabel,
      connectionVerified: state.customLlm.connectionVerified,
    },
  };
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(payload));
  } catch {
    /* quota or private mode */
  }
}

export function loadSession() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function clearSession() {
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* ignore */
  }
}

export function saveReviewHistory(entry) {
  try {
    const raw = sessionStorage.getItem(HISTORY_KEY);
    const list = raw ? JSON.parse(raw) : [];
    const filtered = list.filter((item) => item.prUrl !== entry.prUrl);
    filtered.unshift(entry);
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(filtered.slice(0, MAX_HISTORY)));
  } catch {
    /* ignore */
  }
}

export function loadReviewHistory() {
  try {
    const raw = sessionStorage.getItem(HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function loadTheme() {
  try {
    return sessionStorage.getItem("pr-review-theme") || "light";
  } catch {
    return "light";
  }
}

export function saveTheme(theme) {
  try {
    sessionStorage.setItem("pr-review-theme", theme);
  } catch {
    /* ignore */
  }
}
