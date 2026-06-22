"""Secret storage (AGENTS.md §12): OS keyring preferred, 0600 file fallback.

Stores per-provider API keys. Never logs full keys (see `mask`).
"""

from __future__ import annotations

import json
import os
import stat
import sys
from typing import Optional

from .paths import secrets_file

_SERVICE = "claudIA"

try:  # pragma: no cover - depends on environment
    import keyring
    import keyring.errors

    _HAS_KEYRING = True
except Exception:  # pragma: no cover
    keyring = None  # type: ignore[assignment]
    _HAS_KEYRING = False


def keyring_available() -> bool:
    """True if a usable (non-fail) keyring backend is present."""
    if not _HAS_KEYRING:
        return False
    try:
        from keyring.backends.fail import Keyring as FailKeyring

        return not isinstance(keyring.get_keyring(), FailKeyring)
    except Exception:
        return False


def mask(secret: Optional[str]) -> str:
    """Mask a secret for display/logging — last 4 chars only."""
    if not secret:
        return "<none>"
    if len(secret) <= 4:
        return "*" * len(secret)
    return f"...{secret[-4:]}"


# --- file fallback ----------------------------------------------------------

def _read_file_store() -> dict:
    path = secrets_file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_file_store(data: dict) -> None:
    path = secrets_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write then chmod 0600 (best effort; no-op semantics on Windows).
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if sys.platform != "win32":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


# --- public API -------------------------------------------------------------

def get_key(provider_id: str) -> Optional[str]:
    """Return the stored API key for a provider, or None."""
    if keyring_available():
        try:
            value = keyring.get_password(_SERVICE, provider_id)
            if value:
                return value
        except Exception:
            pass
    return _read_file_store().get(provider_id)


def set_key(provider_id: str, key: str) -> str:
    """Persist an API key. Returns the storage backend used ("keyring"|"file")."""
    if keyring_available():
        try:
            keyring.set_password(_SERVICE, provider_id, key)
            return "keyring"
        except Exception:
            pass
    data = _read_file_store()
    data[provider_id] = key
    _write_file_store(data)
    return "file"


def delete_key(provider_id: str) -> None:
    """Remove a stored API key from both backends (best effort)."""
    if keyring_available():
        try:
            keyring.delete_password(_SERVICE, provider_id)
        except Exception:
            pass
    data = _read_file_store()
    if provider_id in data:
        del data[provider_id]
        _write_file_store(data)
