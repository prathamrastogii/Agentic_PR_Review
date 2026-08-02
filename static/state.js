/** In-memory app state. API keys kept in memory only; labels persisted via session.js */
export const appState = {
  /** @type {'free' | 'custom' | null} */
  llmPath: null,
  customLlm: {
    vendorId: "",
    vendorLabel: "",
    modelId: "",
    modelLabel: "",
    apiKey: "",
    baseUrl: "",
    connectionVerified: false,
  },
  reviewMode: "agent",
};

export function selectFreeTier() {
  appState.llmPath = "free";
  appState.customLlm.connectionVerified = false;
}

export function resetCustomLlmVerification() {
  appState.customLlm.connectionVerified = false;
}

export function applyCustomLlmFromForm({
  vendorId,
  vendorLabel,
  modelId,
  modelLabel,
  apiKey,
  baseUrl,
}) {
  appState.llmPath = "custom";
  appState.customLlm.vendorId = vendorId;
  appState.customLlm.vendorLabel = vendorLabel;
  appState.customLlm.modelId = modelId;
  appState.customLlm.modelLabel = modelLabel;
  appState.customLlm.apiKey = apiKey;
  appState.customLlm.baseUrl = baseUrl;
  appState.customLlm.connectionVerified = true;
}

export function restoreSessionToState(session) {
  if (!session) return;
  appState.llmPath = session.llmPath ?? null;
  appState.reviewMode = session.reviewMode ?? "agent";
  if (session.customLlm) {
    appState.customLlm = {
      ...appState.customLlm,
      ...session.customLlm,
      apiKey: "",
    };
    if (!appState.customLlm.apiKey) {
      appState.customLlm.connectionVerified = false;
    }
  }
}

export function startOver() {
  appState.llmPath = null;
  appState.reviewMode = "agent";
  appState.customLlm = {
    vendorId: "",
    vendorLabel: "",
    modelId: "",
    modelLabel: "",
    apiKey: "",
    baseUrl: "",
    connectionVerified: false,
  };
}
