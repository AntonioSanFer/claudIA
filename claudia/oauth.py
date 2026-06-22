"""OAuth provider helpers — login status + logout (AGENTS.md §9, §12).

ClaudIA never runs an OAuth flow itself. For OAuth providers (currently only
GitHub Copilot) the *sign-in* is performed by LiteLLM on first use via GitHub's
device-code flow, and the resulting tokens are cached on disk. We only

  * **inspect** those cached tokens to report login status, and
  * **delete** them to "log out",

so the TUI can show whether you're signed in and offer a one-click logout. No
tokens are read into logs or written by us.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .providers import AUTH_OAUTH, Provider


@dataclass
class OAuthStatus:
    """Result of inspecting an OAuth provider's cached credentials."""

    supported: bool  # we know how to inspect/clear this provider
    logged_in: bool
    detail: str
    files: list[Path] = field(default_factory=list)


# --- GitHub Copilot (LiteLLM device-code cache) -----------------------------
# Paths mirror litellm.llms.github_copilot.authenticator.Authenticator, including
# the same environment-variable overrides, so we look exactly where LiteLLM does.

def _github_copilot_files() -> tuple[Path, Path]:
    token_dir = Path(
        os.getenv(
            "GITHUB_COPILOT_TOKEN_DIR",
            os.path.expanduser("~/.config/litellm/github_copilot"),
        )
    )
    access = token_dir / os.getenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")
    api_key = token_dir / os.getenv("GITHUB_COPILOT_API_KEY_FILE", "api-key.json")
    return access, api_key


def _github_copilot_status() -> OAuthStatus:
    access, api_key = _github_copilot_files()
    present = [p for p in (access, api_key) if p.exists()]
    if not access.exists():
        return OAuthStatus(
            supported=True,
            logged_in=False,
            detail="Not signed in - LiteLLM will start the GitHub device login on launch.",
            files=present,
        )

    detail = "Signed in (GitHub Copilot token cached)."
    try:
        info = json.loads(api_key.read_text(encoding="utf-8"))
        expires_at = info.get("expires_at")
        if expires_at:
            when = datetime.fromtimestamp(expires_at)
            if when > datetime.now():
                detail = f"Signed in - access valid until {when:%Y-%m-%d %H:%M}."
            else:
                detail = (
                    f"Signed in - cached key expired {when:%Y-%m-%d %H:%M} "
                    "(LiteLLM will refresh on launch)."
                )
    except Exception:
        pass  # access-token alone is enough to consider the user signed in
    return OAuthStatus(supported=True, logged_in=True, detail=detail, files=present)


# --- public API -------------------------------------------------------------

def oauth_status(provider: Provider) -> OAuthStatus:
    """Report whether the user is signed in to an OAuth provider."""
    if provider.auth != AUTH_OAUTH:
        return OAuthStatus(False, False, "Not an OAuth provider.")
    if provider.id == "github_copilot":
        return _github_copilot_status()
    return OAuthStatus(
        supported=False,
        logged_in=False,
        detail=f"Login status not available for {provider.display_name}.",
    )


def _github_copilot_authenticator():
    """Return a LiteLLM GitHub Copilot Authenticator (imported lazily).

    Factored out so tests can substitute a fake without importing LiteLLM.
    """
    from litellm.llms.github_copilot.authenticator import Authenticator

    return Authenticator()


# Headers the Copilot model endpoint expects — a plain ``Authorization: Bearer``
# is rejected. Mirrors litellm.llms.github_copilot.common_utils.
_COPILOT_MODEL_HEADERS = {
    "Content-Type": "application/json",
    "Copilot-Integration-Id": "vscode-chat",
    "Editor-Version": "vscode/1.95.0",
    "Editor-Plugin-Version": "copilot-chat/0.26.7",
    "User-Agent": "GitHubCopilotChat/0.26.7",
    "X-GitHub-Api-Version": "2025-04-01",
}


def github_copilot_models(timeout: float = 8.0) -> list[str]:
    """Live chat-model ids for GitHub Copilot.

    Copilot's ``/models`` endpoint is reachable, but not via the generic catalog
    path: it needs the short-lived *Copilot API key* (not the GitHub OAuth token),
    a set of editor-specific headers, and the per-account API base — all of which
    LiteLLM's authenticator manages. We obtain those, query the OpenAI-compatible
    ``/models`` endpoint, and keep the chat models the official pickers show.

    Requires an existing sign-in; we never trigger the interactive device flow
    here. Raises on any failure so the caller falls back to the preloaded list.
    """
    if not _github_copilot_status().logged_in:
        raise RuntimeError("not signed in to GitHub Copilot")

    authenticator = _github_copilot_authenticator()
    # get_api_key() returns a valid Copilot token, refreshing it over the network
    # if the cached one expired (non-interactive — only reads the access token).
    api_key = authenticator.get_api_key()
    api_base = authenticator.get_api_base() or "https://api.githubcopilot.com"
    url = api_base.rstrip("/") + "/models"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Request-Id": str(uuid4()),
        **_COPILOT_MODEL_HEADERS,
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    data = payload.get("data", []) if isinstance(payload, dict) else payload
    chat = [
        m
        for m in data
        if isinstance(m, dict)
        and m.get("id")
        and m.get("capabilities", {}).get("type") == "chat"
    ]
    # Prefer the curated picker set; fall back to all chat models if an account
    # doesn't flag any (so we never return empty when chat models exist).
    picker = [m for m in chat if m.get("model_picker_enabled")]
    ids = sorted({m["id"] for m in (picker or chat)})
    if not ids:
        raise ValueError("empty model list")
    return ids


def ensure_oauth_login(provider: Provider, report=lambda _msg: None) -> tuple[bool, str]:
    """Make sure an OAuth provider is signed in *before* the proxy starts.

    The actual device-login prompt is emitted by LiteLLM's authenticator to
    stdout. Running it here (in the bridge's foreground process) surfaces that
    prompt directly in the terminal — framed by ``report`` — instead of burying
    it in the proxy log. Once the access token is cached, the proxy reuses it
    silently.

    Returns ``(ok, detail)``. ``ok`` is True when sign-in is not needed or
    completed; False if the flow could not run/finish (the caller may still
    proceed and let the proxy prompt as a fallback).
    """
    if provider.auth != AUTH_OAUTH:
        return True, ""
    if provider.id != "github_copilot":
        # Unknown OAuth provider — let the proxy handle auth on first request.
        return True, "unsupported"

    status = _github_copilot_status()
    if status.logged_in:
        report(f"GitHub Copilot: {status.detail}")
        return True, "already-signed-in"

    try:
        authenticator = _github_copilot_authenticator()
    except Exception as exc:  # pragma: no cover - import/env failure
        report(f"[warn] Could not start GitHub Copilot sign-in: {exc}")
        report("       LiteLLM will prompt on first request; see `claudia logs`.")
        return False, str(exc)

    bar = "=" * 60
    report("")
    report(bar)
    report("  GitHub Copilot needs a one-time sign-in (device login).")
    report("  Complete the step printed below, then the launch continues.")
    report(bar)
    try:
        authenticator.get_access_token()
    except Exception as exc:
        report(f"  Sign-in did not complete: {exc}")
        report("  Re-run the launch to try again.")
        report(bar)
        report("")
        return False, str(exc)
    report("  GitHub Copilot sign-in complete.")
    report(bar)
    report("")
    return True, "signed-in"


def logout(provider: Provider) -> bool:
    """Remove an OAuth provider's cached tokens. Returns True if anything was deleted."""
    if provider.auth != AUTH_OAUTH:
        return False
    if provider.id == "github_copilot":
        files = _github_copilot_files()
    else:
        return False
    removed = False
    for path in files:
        try:
            path.unlink()
            removed = True
        except OSError:
            pass
    return removed
