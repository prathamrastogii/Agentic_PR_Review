/**
 * Bring-your-own-LLM vendor catalog — edit this file when models change.
 * UI reads from here; do not hardcode vendors in HTML.
 */
export const LLM_VENDORS = [
  {
    id: "anthropic",
    label: "Anthropic",
    hint: "Strong reasoning",
    models: [
      { id: "claude-opus-5", label: "Claude Opus 5" },
      { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
      { id: "claude-fable-5", label: "Claude Fable 5" },
    ],
  },
  {
    id: "openai",
    label: "OpenAI",
    hint: "Versatile generalist",
    models: [
      { id: "gpt-5.6-sol", label: "GPT-5.6 Sol" },
      { id: "gpt-5.6-terra", label: "GPT-5.6 Terra" },
      { id: "gpt-5.6-luna", label: "GPT-5.6 Luna" },
    ],
  },
  {
    id: "google",
    label: "Google",
    hint: "Fast + capable",
    backendProvider: "google",
    models: [
      { id: "gemini-3.1-pro", label: "Gemini 3.1 Pro" },
      { id: "gemini-3.5-flash", label: "Gemini 3.5 Flash" },
    ],
  },
  {
    id: "xai",
    label: "xAI",
    hint: "Large context",
    models: [{ id: "grok-4.5", label: "Grok 4.5" }],
  },
  {
    id: "meta",
    label: "Meta",
    hint: "Open weights style",
    models: [{ id: "muse-spark-1.1", label: "Muse Spark 1.1" }],
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    hint: "Code-focused",
    models: [{ id: "deepseek-v4", label: "DeepSeek V4" }],
  },
  {
    id: "moonshot",
    label: "Moonshot AI",
    hint: "Long documents",
    models: [{ id: "kimi-k3", label: "Kimi K3" }],
  },
  {
    id: "zai",
    label: "Z.ai",
    hint: "Efficient inference",
    models: [{ id: "glm-5.2", label: "GLM-5.2" }],
  },
  {
    id: "custom",
    label: "Custom endpoint",
    hint: "Self-hosted / compatible",
    customEndpoint: true,
    models: [],
  },
];

export function getVendorById(vendorId) {
  return LLM_VENDORS.find((vendor) => vendor.id === vendorId) ?? null;
}

export function getModelLabel(vendor, modelId) {
  if (!vendor) return modelId;
  if (vendor.customEndpoint) return modelId;
  const match = vendor.models.find((model) => model.id === modelId);
  return match?.label ?? modelId;
}

/** Maps BYO vendor to a backend provider id when one exists (e.g. Google → gemini). */
export function resolveBackendProvider(vendor) {
  if (!vendor) return null;
  return vendor.backendProvider ?? vendor.id;
}

export function getVendorInitial(label) {
  return (label || "?").charAt(0).toUpperCase();
}
