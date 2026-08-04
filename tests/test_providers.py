from unittest.mock import patch

import pytest
from pydantic import SecretStr

from backend.agent.providers import (
    PROVIDERS,
    LLMConfig,
    available_providers,
    build_chat_model,
    resolve_llm_config,
)


class TestResolveLLMConfig:
    def test_defaults_to_server_provider_and_model(self):
        with (
            patch("backend.config.LLM_PROVIDER", "groq"),
            patch("backend.config.GROQ_API_KEY", "server-key"),
            patch("backend.config.GROQ_MODEL", "llama-3.3-70b-versatile"),
        ):
            resolved = resolve_llm_config()

        assert resolved.provider == "groq"
        assert resolved.model == "llama-3.3-70b-versatile"
        assert resolved.api_key.get_secret_value() == "server-key"

    def test_caller_key_and_model_win_over_server(self):
        with (
            patch("backend.config.GOOGLE_API_KEY", "server-key"),
            patch("backend.config.GOOGLE_MODEL", "gemini-3.5-flash-lite"),
        ):
            resolved = resolve_llm_config(
                provider="google", model="gemini-3.6-flash", api_key="user-key"
            )

        assert resolved.provider == "google"
        assert resolved.model == "gemini-3.6-flash"
        assert resolved.api_key.get_secret_value() == "user-key"

    def test_falls_back_to_spec_default_model(self):
        with (
            patch("backend.config.GOOGLE_API_KEY", None),
            patch("backend.config.GOOGLE_MODEL", None),
        ):
            resolved = resolve_llm_config(provider="google", api_key="user-key")

        assert resolved.model == "gemini-3.5-flash-lite"

    def test_provider_name_is_case_insensitive(self):
        resolved = resolve_llm_config(provider="  GOOGLE ", api_key="k")
        assert resolved.provider == "google"

    def test_unknown_provider_is_rejected(self):
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            resolve_llm_config(provider="cohere", api_key="k")

    def test_missing_key_raises_actionable_error(self):
        with patch("backend.config.GOOGLE_API_KEY", None):
            with pytest.raises(ValueError, match="No API key available for Google"):
                resolve_llm_config(provider="google")

    def test_blank_caller_key_falls_back_to_server(self):
        with patch("backend.config.GROQ_API_KEY", "server-key"):
            resolved = resolve_llm_config(provider="groq", api_key="   ")
        assert resolved.api_key.get_secret_value() == "server-key"


class TestSecretHandling:
    def test_key_is_masked_in_repr(self):
        resolved = LLMConfig(
            provider="groq", model="m", api_key=SecretStr("super-secret")
        )
        assert "super-secret" not in repr(resolved)
        assert "super-secret" not in str(resolved)

    def test_provider_catalogue_excludes_keys(self):
        with patch("backend.config.GROQ_API_KEY", "server-key"):
            catalogue = available_providers()

        serialized = str(catalogue)
        assert "server-key" not in serialized
        groq = next(p for p in catalogue if p["id"] == "groq")
        assert groq["server_key_configured"] is True


class TestProviderCatalogue:
    def test_lists_all_supported_providers(self):
        assert set(PROVIDERS) == {"google", "openai", "groq", "anthropic"}

    def test_every_default_model_is_also_suggested(self):
        for spec in PROVIDERS.values():
            assert spec.default_model in spec.suggested_models


class TestBuildChatModel:
    def test_builds_gemini_with_google_api_key_kwarg(self):
        resolved = LLMConfig(
            provider="google",
            model="gemini-3.5-flash-lite",
            api_key=SecretStr("user-key"),
        )
        model = build_chat_model(resolved)

        assert model.model.endswith("gemini-3.5-flash-lite")
        assert model.google_api_key.get_secret_value() == "user-key"

    def test_builds_groq_with_requested_model(self):
        resolved = LLMConfig(
            provider="groq",
            model="llama-3.1-8b-instant",
            api_key=SecretStr("user-key"),
        )
        model = build_chat_model(resolved)

        assert model.model_name == "llama-3.1-8b-instant"

    def test_builds_openai_with_requested_model(self):
        resolved = LLMConfig(
            provider="openai",
            model="gpt-4o-mini",
            api_key=SecretStr("user-key"),
        )
        model = build_chat_model(resolved)

        assert model.model_name == "gpt-4o-mini"

    def test_builds_anthropic_with_requested_model(self):
        resolved = LLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-0",
            api_key=SecretStr("user-key"),
        )
        model = build_chat_model(resolved)

        assert model.model == "claude-sonnet-4-0"

    def test_passes_thinking_budget_when_configured(self):
        resolved = LLMConfig(
            provider="google", model="gemini-3.5-flash-lite", api_key=SecretStr("k")
        )
        with patch("backend.config.GOOGLE_THINKING_BUDGET", 2048):
            model = build_chat_model(resolved)

        assert model.thinking_budget == 2048

    def test_omits_thinking_budget_when_not_configured(self):
        resolved = LLMConfig(
            provider="google", model="gemini-3.5-flash-lite", api_key=SecretStr("k")
        )
        with patch("backend.config.GOOGLE_THINKING_BUDGET", None):
            model = build_chat_model(resolved)

        assert model.thinking_budget is None
