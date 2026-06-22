"""Provider registry (AGENTS.md §9).

A declarative table of supported providers. Adding a provider is adding one
`Provider` row here plus a config-generation test — no special-casing elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Auth styles.
AUTH_API_KEY = "api-key"
AUTH_OAUTH = "oauth"
AUTH_NONE = "none"


@dataclass(frozen=True)
class Provider:
    """One supported provider.

    Attributes:
        id: Stable identifier used in config/state (e.g. "openrouter").
        display_name: Human label shown in the picker.
        prefix: LiteLLM model prefix, including the trailing slash
            (e.g. "openrouter/"). Combined with a bare model id to form the
            full LiteLLM model string.
        auth: One of AUTH_API_KEY / AUTH_OAUTH / AUTH_NONE.
        requires_api_base: Whether an `api_base` must be supplied by the user.
        default_api_base: Pre-filled `api_base` suggestion (e.g. Ollama).
        suggested_models: Bare model ids to pre-populate the model picker.
        notes: Short free-text shown in the UI.
    """

    id: str
    display_name: str
    prefix: str
    auth: str = AUTH_API_KEY
    requires_api_base: bool = False
    default_api_base: Optional[str] = None
    suggested_models: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""

    @property
    def needs_api_key(self) -> bool:
        return self.auth == AUTH_API_KEY

    def full_model(self, model_id: str) -> str:
        """Combine the provider prefix with a bare model id.

        If the caller already passed a fully-prefixed model (starts with this
        provider's prefix), it is returned unchanged so power users can paste a
        complete LiteLLM model string.
        """
        model_id = model_id.strip()
        if model_id.startswith(self.prefix):
            return model_id
        return f"{self.prefix}{model_id}"


# Order here is the order shown in the TUI picker.
_PROVIDERS: tuple[Provider, ...] = (
    Provider(
        id="openrouter",
        display_name="OpenRouter",
        prefix="openrouter/",
        auth=AUTH_API_KEY,
        suggested_models=(
            "deepseek/deepseek-chat",
            "anthropic/claude-3.5-sonnet",
            "qwen/qwen-2.5-coder-32b-instruct",
            "meta-llama/llama-3.3-70b-instruct",
        ),
        notes="Gateway to many models behind a single key.",
    ),
    Provider(
        id="deepseek",
        display_name="DeepSeek",
        prefix="deepseek/",
        auth=AUTH_API_KEY,
        suggested_models=("deepseek-chat", "deepseek-reasoner"),
        notes="Direct DeepSeek API.",
    ),
    Provider(
        id="groq",
        display_name="Groq",
        prefix="groq/",
        auth=AUTH_API_KEY,
        suggested_models=(
            "llama-3.3-70b-versatile",
            "moonshotai/kimi-k2-instruct",
            "openai/gpt-oss-120b",
        ),
        notes="Very fast inference.",
    ),
    Provider(
        id="mistral",
        display_name="Mistral",
        prefix="mistral/",
        auth=AUTH_API_KEY,
        suggested_models=("mistral-large-latest", "codestral-latest"),
    ),
    Provider(
        id="together_ai",
        display_name="Together AI",
        prefix="together_ai/",
        auth=AUTH_API_KEY,
        suggested_models=("deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-Coder-32B-Instruct"),
        notes="Open models.",
    ),
    Provider(
        id="fireworks_ai",
        display_name="Fireworks AI",
        prefix="fireworks_ai/",
        auth=AUTH_API_KEY,
        suggested_models=("accounts/fireworks/models/deepseek-v3",),
        notes="Open models.",
    ),
    Provider(
        id="openai",
        display_name="OpenAI",
        prefix="openai/",
        auth=AUTH_API_KEY,
        suggested_models=("gpt-4o", "gpt-4o-mini", "o4-mini"),
    ),
    Provider(
        id="azure",
        display_name="Azure OpenAI",
        prefix="azure/",
        auth=AUTH_API_KEY,
        requires_api_base=True,
        suggested_models=("gpt-4o", "gpt-4o-mini"),
        notes="Needs api_base (your Azure endpoint) and a deployment name.",
    ),
    Provider(
        id="github_copilot",
        display_name="GitHub Copilot",
        prefix="github_copilot/",
        auth=AUTH_OAUTH,
        suggested_models=("gpt-4o", "claude-3.5-sonnet"),
        notes="Uses your Copilot subscription auth, not a plain API key.",
    ),
    Provider(
        id="ollama",
        display_name="Ollama (local)",
        prefix="ollama/",
        auth=AUTH_NONE,
        requires_api_base=True,
        default_api_base="http://localhost:11434",
        suggested_models=("llama3.1", "qwen2.5-coder", "deepseek-coder-v2"),
        notes="Local models; point api_base at your Ollama server.",
    ),
    Provider(
        id="custom",
        display_name="Custom OpenAI-compatible",
        prefix="openai/",
        auth=AUTH_API_KEY,
        requires_api_base=True,
        notes="Any OpenAI-compatible endpoint; supply api_base.",
    ),
)

_BY_ID = {p.id: p for p in _PROVIDERS}


def all_providers() -> tuple[Provider, ...]:
    """All registered providers, in display order."""
    return _PROVIDERS


def get_provider(provider_id: str) -> Provider:
    """Look up a provider by id; raise KeyError with a helpful message."""
    try:
        return _BY_ID[provider_id]
    except KeyError:
        known = ", ".join(sorted(_BY_ID))
        raise KeyError(f"Unknown provider {provider_id!r}. Known: {known}") from None


def provider_ids() -> list[str]:
    return [p.id for p in _PROVIDERS]
