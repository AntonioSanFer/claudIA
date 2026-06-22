"""Model catalog (M5).

Two tiers, mirroring how tools like opencode source their model lists:

  1. **Live** — query the provider's own OpenAI-compatible ``/models`` endpoint
     (or Ollama's ``/api/tags``) so the picker shows exactly what the user's key
     can reach.
  2. **Preloaded** — fall back to the registry's ``suggested_models`` when there
     is no endpoint, no network, or the request fails.

Uses only stdlib (urllib); no extra dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from .providers import Provider


@dataclass
class ModelCatalog:
    """Result of a model lookup."""

    models: list[str] = field(default_factory=list)
    source: str = "preloaded"  # "live" | "preloaded"
    error: Optional[str] = None  # why live failed, if it did

    @property
    def is_live(self) -> bool:
        return self.source == "live"


def resolve_models_url(provider: Provider, api_base: Optional[str]) -> Optional[str]:
    """Resolve the concrete model-list URL, substituting ``{api_base}``."""
    template = provider.models_url
    if not template:
        return None
    if "{api_base}" in template:
        base = api_base or provider.default_api_base
        if not base:
            return None
        return template.replace("{api_base}", base.rstrip("/"))
    return template


def _parse(style: str, payload: object) -> list[str]:
    """Extract model ids from a provider response by shape."""
    if style == "ollama":
        models = payload.get("models", []) if isinstance(payload, dict) else []
        names = [m.get("name") for m in models if isinstance(m, dict)]
        return sorted({n for n in names if n})
    # openai-compatible: {"data": [{"id": ...}, ...]} (or a bare list)
    if isinstance(payload, dict):
        data = payload.get("data", [])
    elif isinstance(payload, list):
        data = payload
    else:
        data = []
    ids = [m.get("id") for m in data if isinstance(m, dict)]
    return sorted({i for i in ids if i})


def fetch_live(
    provider: Provider,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    timeout: float = 8.0,
) -> list[str]:
    """Fetch the live model list. Raises on any failure (caller falls back)."""
    url = resolve_models_url(provider, api_base)
    if not url:
        raise ValueError("provider has no model-list endpoint")
    headers = {"Accept": "application/json"}
    if provider.models_auth and api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    models = _parse(provider.models_style, payload)
    if not models:
        raise ValueError("empty model list")
    return models


def _humanize_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} from model endpoint"
    if isinstance(exc, urllib.error.URLError):
        return f"network error: {exc.reason}"
    return str(exc)


def list_models(
    provider: Provider,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    allow_network: bool = True,
    timeout: float = 8.0,
) -> ModelCatalog:
    """Return a ModelCatalog: live if reachable, else the preloaded fallback.

    Never raises — failures are reported via ``ModelCatalog.error`` while still
    returning the preloaded list so the picker always has something to show.
    """
    error: Optional[str] = None
    if allow_network and provider.models_url:
        try:
            models = fetch_live(provider, api_key, api_base, timeout)
            return ModelCatalog(models=models, source="live")
        except Exception as exc:  # noqa: BLE001 - any failure -> fallback
            error = _humanize_error(exc)
    elif not provider.models_url:
        error = "no live endpoint for this provider"
    else:
        error = "network disabled"
    return ModelCatalog(
        models=list(provider.suggested_models),
        source="preloaded",
        error=error,
    )
