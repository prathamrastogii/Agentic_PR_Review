/**
 * Bring-your-own-LLM vendor catalog. Edit this file when models change.
 * UI reads from here; do not hardcode vendors in HTML.
 */
export const LLM_VENDORS = [
  {
    id: "google",
    label: "Google Gemini",
    hint: "Fast + capable",
    backendProvider: "google",
    models: [
      { id: "gemini-3.5-flash-lite", label: "Gemini 3.5 Flash Lite" },
      { id: "gemini-3.6-flash", label: "Gemini 3.6 Flash" },
      { id: "gemini-3.1-flash-lite", label: "Gemini 3.1 Flash Lite" },
      { id: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro Preview" },
    ],
  },
  {
    id: "openai",
    label: "OpenAI",
    hint: "Versatile generalist",
    backendProvider: "openai",
    models: [
      { id: "gpt-4o", label: "GPT-4o" },
      { id: "gpt-4o-mini", label: "GPT-4o Mini" },
      { id: "gpt-4-turbo", label: "GPT-4 Turbo" },
    ],
  },
  {
    id: "groq",
    label: "Groq",
    hint: "Fast inference",
    backendProvider: "groq",
    models: [
      { id: "llama-3.3-70b-versatile", label: "Llama 3.3 70B" },
      { id: "llama-3.1-8b-instant", label: "Llama 3.1 8B Instant" },
    ],
  },
  {
    id: "anthropic",
    label: "Anthropic",
    hint: "Strong reasoning",
    backendProvider: "anthropic",
    models: [
      { id: "claude-sonnet-4-0", label: "Claude Sonnet 4" },
      { id: "claude-3-5-haiku-latest", label: "Claude 3.5 Haiku" },
      { id: "claude-3-5-sonnet-latest", label: "Claude 3.5 Sonnet" },
    ],
  },
];

export function getVendorById(vendorId) {
  return LLM_VENDORS.find((vendor) => vendor.id === vendorId) ?? null;
}

export function getModelLabel(vendor, modelId) {
  if (!vendor) return modelId;
  const match = vendor.models.find((model) => model.id === modelId);
  return match?.label ?? modelId;
}

/** Maps BYO vendor to a backend provider id when one exists (e.g. Google → google). */
export function resolveBackendProvider(vendor) {
  if (!vendor) return null;
  return vendor.backendProvider ?? vendor.id;
}

export function getVendorInitial(label) {
  return (label || "?").charAt(0).toUpperCase();
}
