"""Persistent config + runtime state (AGENTS.md §13).

Uses JSON (stdlib) rather than TOML so the package has no extra parser
dependency and works identically on Python 3.10 (which lacks `tomllib`). The
spec permits "TOML or JSON".
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .constants import DEFAULT_PORT
from .paths import config_file, runtime_state_file


@dataclass
class AppConfig:
    """User-facing persisted settings."""

    # Last successful selection (for "remember last working setup").
    last_provider: Optional[str] = None
    last_main_model: Optional[str] = None
    last_small_model: Optional[str] = None
    last_api_base: Optional[str] = None

    # Overrides / preferences.
    claude_command: Optional[str] = None
    preferred_port: int = DEFAULT_PORT
    litellm_version: Optional[str] = None

    # Providers the user has configured at least once.
    known_providers: list[str] = field(default_factory=list)

    def remember_selection(
        self,
        provider_id: str,
        main_model: str,
        small_model: Optional[str],
        api_base: Optional[str],
    ) -> None:
        self.last_provider = provider_id
        self.last_main_model = main_model
        self.last_small_model = small_model
        self.last_api_base = api_base
        if provider_id not in self.known_providers:
            self.known_providers.append(provider_id)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_config() -> AppConfig:
    """Load persisted config, tolerating missing/unknown keys."""
    raw = _load_json(config_file())
    known = {f for f in AppConfig.__dataclass_fields__}
    filtered = {k: v for k, v in raw.items() if k in known}
    return AppConfig(**filtered)


def save_config(config: AppConfig) -> None:
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


# --- runtime state (live proxy pid/port for clean shutdown) -----------------

def write_runtime(pid: int, port: int, config_path: str) -> None:
    path = runtime_state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": pid, "port": port, "config": config_path}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_runtime() -> Optional[dict[str, Any]]:
    data = _load_json(runtime_state_file())
    return data or None


def clear_runtime() -> None:
    path = runtime_state_file()
    try:
        os.remove(path)
    except OSError:
        pass
