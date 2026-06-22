"""Per-user config / data directory resolution.

Prefers `platformdirs` (a declared dependency) but degrades gracefully to a
hand-rolled, platform-appropriate location if it is not importable, so the
package never hard-crashes on import.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .constants import APP_NAME

try:  # pragma: no cover - exercised indirectly
    import platformdirs

    _HAS_PLATFORMDIRS = True
except Exception:  # pragma: no cover - fallback path
    platformdirs = None  # type: ignore[assignment]
    _HAS_PLATFORMDIRS = False


def _fallback_base(kind: str) -> Path:
    """Platform-appropriate base dir when platformdirs is unavailable.

    `kind` is "config" or "data".
    """
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        return Path(root) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    # Linux / other: respect XDG.
    if kind == "config":
        root = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(root) / APP_NAME


def config_dir() -> Path:
    """Directory for user config (settings, last selection)."""
    if _HAS_PLATFORMDIRS:
        return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))
    return _fallback_base("config")


def data_dir() -> Path:
    """Directory for runtime artifacts (generated config.yaml, logs, pid file)."""
    if _HAS_PLATFORMDIRS:
        return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))
    return _fallback_base("data")


def ensure_dir(path: Path) -> Path:
    """Create `path` (and parents) if missing; return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return config_dir() / "config.json"


def secrets_file() -> Path:
    return config_dir() / "secrets.json"


def generated_config_file() -> Path:
    return data_dir() / "litellm.config.yaml"


def proxy_log_file() -> Path:
    return data_dir() / "proxy.log"


def runtime_state_file() -> Path:
    return data_dir() / "runtime.json"
