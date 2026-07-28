/** In-memory app state — not persisted (no localStorage/sessionStorage). */
export const appState = {
  /** @type {'free' | 'custom' | null} */
  llmPath: null,
  customLlm: {
    provider: "",
    model: "",
    apiKey: "",
    baseUrl: "",
    connectionVerified: false,
  },
  /** Agent vs baseline review mode on the dashboard */
  reviewMode: "agent",
};

export function selectFreeTier() {
  appState.llmPath = "free";
  appState.customLlm.connectionVerified = false;
}

export function resetCustomLlmVerification() {
  appState.customLlm.connectionVerified = false;
}

export function applyCustomLlmFromForm({ provider, model, apiKey, baseUrl }) {
  appState.llmPath = "custom";
  appState.customLlm.provider = provider;
  appState.customLlm.model = model;
  appState.customLlm.apiKey = apiKey;
  appState.customLlm.baseUrl = baseUrl;
}
