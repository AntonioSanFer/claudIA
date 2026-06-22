"""Provider registry (AGENTS.md §9).

A declarative table of supported providers. Adding a provider is adding one
`Provider` row here plus a config-generation test — no special-casing elsewhere.
"""

from __future__ import annotations

import re
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
        suggested_models: Bare model ids; the *preloaded* fallback list and the
            seed for the picker before/without a live fetch.
        notes: Short free-text shown in the UI.
        models_url: Endpoint to GET a live model list. May contain a literal
            ``{api_base}`` placeholder (Ollama / custom). None disables live
            fetch (falls back to suggested_models).
        models_style: Response shape — "openai" (data[].id) or "ollama"
            (models[].name).
        models_auth: Whether to send ``Authorization: Bearer <key>`` when
            fetching the model list.
    """

    id: str
    display_name: str
    prefix: str
    auth: str = AUTH_API_KEY
    requires_api_base: bool = False
    default_api_base: Optional[str] = None
    suggested_models: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""
    models_url: Optional[str] = None
    models_style: str = "openai"
    models_auth: bool = True

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
        models_url="https://openrouter.ai/api/v1/models",
    ),
    Provider(
        id="deepseek",
        display_name="DeepSeek",
        prefix="deepseek/",
        auth=AUTH_API_KEY,
        suggested_models=("deepseek-chat", "deepseek-reasoner"),
        notes="Direct DeepSeek API.",
        models_url="https://api.deepseek.com/models",
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
        models_url="https://api.groq.com/openai/v1/models",
    ),
    Provider(
        id="mistral",
        display_name="Mistral",
        prefix="mistral/",
        auth=AUTH_API_KEY,
        suggested_models=("mistral-large-latest", "codestral-latest"),
        models_url="https://api.mistral.ai/v1/models",
    ),
    Provider(
        id="together_ai",
        display_name="Together AI",
        prefix="together_ai/",
        auth=AUTH_API_KEY,
        suggested_models=("deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-Coder-32B-Instruct"),
        notes="Open models.",
        models_url="https://api.together.xyz/v1/models",
    ),
    Provider(
        id="fireworks_ai",
        display_name="Fireworks AI",
        prefix="fireworks_ai/",
        auth=AUTH_API_KEY,
        suggested_models=("accounts/fireworks/models/deepseek-v3",),
        notes="Open models.",
        models_url="https://api.fireworks.ai/inference/v1/models",
    ),
    Provider(
        id="openai",
        display_name="OpenAI",
        prefix="openai/",
        auth=AUTH_API_KEY,
        suggested_models=("gpt-4o", "gpt-4o-mini", "o4-mini"),
        models_url="https://api.openai.com/v1/models",
    ),
    Provider(
        id="azure",
        display_name="Azure OpenAI",
        prefix="azure/",
        auth=AUTH_API_KEY,
        requires_api_base=True,
        suggested_models=("gpt-4o", "gpt-4o-mini"),
        notes="Needs api_base (your Azure endpoint) and a deployment name.",
        # Azure lists *deployments*, not models, via a mgmt API — skip live fetch.
        models_url=None,
    ),
    Provider(
        id="github_copilot",
        display_name="GitHub Copilot",
        prefix="github_copilot/",
        auth=AUTH_OAUTH,
        suggested_models=("gpt-4.1", "gpt-4o", "claude-sonnet-4.6", "gpt-5-mini"),
        notes="Uses your Copilot subscription auth, not a plain API key.",
        # Live list comes from the Copilot token cache, not a static URL — see
        # catalog.has_live_endpoint / oauth.github_copilot_models.
        models_url=None,
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
        models_url="{api_base}/api/tags",
        models_style="ollama",
        models_auth=False,
    ),
    Provider(
        id="custom",
        display_name="Custom OpenAI-compatible",
        prefix="openai/",
        auth=AUTH_API_KEY,
        requires_api_base=True,
        notes="Any OpenAI-compatible endpoint; supply api_base.",
        models_url="{api_base}/models",
    ),
)

# --- Responses-API routing --------------------------------------------------
# GitHub Copilot rejects the tools + reasoning_effort combination (which Claude
# Code always sends) on /v1/chat/completions for gpt-5.4 and newer, and for the
# *-codex models. LiteLLM routes those to /v1/responses when the config entry
# carries `model_info: { mode: responses }` — which keeps the reasoning slider
# working instead of having drop_params silently strip reasoning_effort.
_RESPONSES_ONLY_PROVIDER = "github_copilot"
_GPT_VERSION_RE = re.compile(r"gpt-(\d+)(?:\.(\d+))?")


def needs_responses_api(provider_id: str, model_id: str) -> bool:
    """Whether `model_id` must use the Responses API instead of chat/completions.

    Only GitHub Copilot is affected; for everyone else this is always False.
    Returns True for the *-codex models and for gpt-5.4 or newer.
    """
    if provider_id != _RESPONSES_ONLY_PROVIDER:
        return False
    model = model_id.strip().lower()
    if "codex" in model:
        return True
    m = _GPT_VERSION_RE.search(model)
    if not m:
        return False
    major = int(m.group(1))
    minor = int(m.group(2) or 0)
    return (major, minor) >= (5, 4)


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
