"""LLM provider registry.

Adding a vendor means adding one `ProviderSpec` plus a builder function. Keys are
resolved per request and never persisted or logged.
"""

import logging
from dataclasses import dataclass
from typing import Callable

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, SecretStr

from backend import config

logger = logging.getLogger(__name__)


class LLMConfig(BaseModel):
    """A fully resolved provider choice, ready to build a chat model from."""

    provider: str
    model: str
    api_key: SecretStr

    model_config = ConfigDict(frozen=True)


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    default_model: str
    suggested_models: tuple[str, ...]
    api_key_url: str
    build: Callable[[LLMConfig], BaseChatModel]
    server_key: Callable[[], str | None]
    server_model: Callable[[], str | None]


def _build_groq(llm_config: LLMConfig) -> BaseChatModel:
    try:
        from langchain_groq import ChatGroq
    except ImportError as exc:  # pragma: no cover - depends on install
        raise ValueError(
            "Groq support requires the 'langchain-groq' package."
        ) from exc
    return ChatGroq(
        model=llm_config.model,
        api_key=llm_config.api_key.get_secret_value(),
        temperature=0,
    )


def _build_google(llm_config: LLMConfig) -> BaseChatModel:
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:  # pragma: no cover - depends on install
        raise ValueError(
            "Gemini support requires the 'langchain-google-genai' package."
        ) from exc
    kwargs: dict = {
        "model": llm_config.model,
        "google_api_key": llm_config.api_key.get_secret_value(),
        "temperature": 0,
    }
    # Only pass thinking_budget when explicitly configured. Gemini 3.5 rejects 0.
    if config.GOOGLE_THINKING_BUDGET is not None:
        kwargs["thinking_budget"] = config.GOOGLE_THINKING_BUDGET
    return ChatGoogleGenerativeAI(**kwargs)


PROVIDERS: dict[str, ProviderSpec] = {
    "groq": ProviderSpec(
        id="groq",
        label="Groq",
        default_model="llama-3.3-70b-versatile",
        suggested_models=(
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
        ),
        api_key_url="https://console.groq.com/keys",
        build=_build_groq,
        server_key=lambda: config.GROQ_API_KEY,
        server_model=lambda: config.GROQ_MODEL,
    ),
    "google": ProviderSpec(
        id="google",
        label="Google Gemini",
        default_model="gemini-3.5-flash-lite",
        suggested_models=(
            "gemini-3.5-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.1-flash-lite",
            "gemini-3.1-pro-preview",
        ),
        api_key_url="https://aistudio.google.com/apikey",
        build=_build_google,
        server_key=lambda: config.GOOGLE_API_KEY,
        server_model=lambda: config.GOOGLE_MODEL,
    ),
}


def resolve_llm_config(
    provider: str | None = None,
    model: str | None = None,
    api_key: SecretStr | str | None = None,
) -> LLMConfig:
    """Combine a caller's choices with server defaults into a usable config.

    Caller-supplied values win, so a user can bring their own key and model
    without any server configuration.
    """
    provider_id = (provider or config.LLM_PROVIDER).strip().lower()
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        supported = ", ".join(sorted(PROVIDERS))
        raise ValueError(
            f"Unsupported LLM provider '{provider_id}'. Supported: {supported}."
        )

    if isinstance(api_key, str):
        api_key = SecretStr(api_key) if api_key.strip() else None
    resolved_key = api_key or (
        SecretStr(spec.server_key()) if spec.server_key() else None
    )
    if resolved_key is None:
        raise ValueError(
            f"No API key available for {spec.label}. Send one with the request or "
            f"set it in the server environment. Create a key at {spec.api_key_url}."
        )

    resolved_model = (model or "").strip() or spec.server_model() or spec.default_model
    return LLMConfig(provider=provider_id, model=resolved_model, api_key=resolved_key)


def resolve_fallback_config(current: LLMConfig) -> LLMConfig | None:
    """Return the next provider to try when `current` is rate-limited, or None."""
    fallback_id = config.LLM_FALLBACK_PROVIDER
    if not fallback_id:
        # Default free-tier stack: Gemini primary, Groq backup.
        fallback_id = "groq" if current.provider == "google" else None
    if not fallback_id or fallback_id == current.provider:
        return None
    try:
        return resolve_llm_config(provider=fallback_id)
    except ValueError as exc:
        logger.warning(
            "Fallback provider %r unavailable (%s); cannot recover from rate limit",
            fallback_id,
            exc,
        )
        return None


def build_chat_model(llm_config: LLMConfig) -> BaseChatModel:
    spec = PROVIDERS[llm_config.provider]
    return spec.build(llm_config)


def available_providers() -> list[dict]:
    """Provider catalogue for the UI. Never includes key material."""
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "default_model": spec.server_model() or spec.default_model,
            "suggested_models": list(spec.suggested_models),
            "api_key_url": spec.api_key_url,
            "server_key_configured": bool(spec.server_key()),
        }
        for spec in PROVIDERS.values()
    ]
