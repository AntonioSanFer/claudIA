"""Inject proxy configuration into Claude Code's settings.json.

Claude Code's background daemon (which powers `claude agents`, subagents,
and scheduled tasks) outlives individual sessions and reads its configuration
from ``~/.claude/settings.json``, not from the environment of the spawning
process. When ClaudIA only sets env vars on the foreground ``claude`` child,
the daemon — and therefore every subagent — talks directly to
``api.anthropic.com`` using the user's real Anthropic subscription, bypassing
the LiteLLM proxy entirely.

This module temporarily merges the proxy routing and model aliases into
``settings.json`` so the daemon also routes through the proxy. The original
file (or its absence) is restored when the session ends.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional


def _claude_config_dir() -> Path:
    """Return the Claude Code per-user config directory."""
    if sys.platform == "win32":
        return Path(os.environ.get("USERPROFILE", "~")) / ".claude"
    return Path.home() / ".claude"


def _settings_path() -> Path:
    return _claude_config_dir() / "settings.json"


class SettingsProxy:
    """Context manager that injects proxy env vars into settings.json.

    Usage::

        with SettingsProxy(base_url="http://127.0.0.1:4000",
                           auth_token="sk-master", ...) as mgr:
            if mgr.injected:
                ...  # launch claude
        # settings.json is restored here.
    """

    def __init__(
        self,
        base_url: str,
        auth_token: str,
        main_model: str,
        small_model: str,
        subagent_model: Optional[str] = None,
    ):
        self._path = _settings_path()
        self._env_patch = {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": auth_token,
            "ANTHROPIC_MODEL": main_model,
            "ANTHROPIC_SMALL_FAST_MODEL": small_model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": main_model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": main_model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": small_model,
        }
        if subagent_model:
            self._env_patch["CLAUDE_CODE_SUBAGENT_MODEL"] = subagent_model

        self._original: Optional[dict] = None
        self._backup_path: Optional[Path] = None
        self._restore_needed = False
        self.injected = False

    def _read(self) -> dict:
        """Read settings.json, returning {} when missing or unreadable."""
        if not self._path.exists():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
            return json.loads(text)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict) -> None:
        """Atomically write settings.json via a temp-file + rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._path.parent,
            prefix=".settings-",
            suffix=".tmp",
            delete=False,
        )
        try:
            json.dump(data, tmp, indent=2, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            # Atomic replacement on the same filesystem.
            os.replace(tmp.name, str(self._path))
        except BaseException:
            # Clean up the temp file on any error, then re-raise.
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise

    def __enter__(self) -> "SettingsProxy":
        original = self._read()
        self._original = original

        # Merge patch into the existing "env" key, preserving user overrides.
        merged_env = dict(original.get("env", {}))
        # ClaudIA vars go first so at most user's own values take precedence.
        merged_env = {**self._env_patch, **merged_env}

        merged = {**original, "env": merged_env}

        # Nothing changed? Skip the write.
        if merged == original:
            self.injected = False
            return self

        # Back up the original to a sibling temp file (for crash recovery).
        if self._path.exists():
            backup = NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=".settings-backup-",
                suffix=".json",
                delete=False,
            )
            try:
                shutil.copy2(self._path, backup.name)
                self._backup_path = Path(backup.name)
            finally:
                backup.close()

        self._write(merged)
        self._restore_needed = True
        self.injected = True
        return self

    def __exit__(self, *args) -> None:
        if not self._restore_needed:
            return
        try:
            if self._original is not None:
                self._write(self._original)
            elif self._path.exists():
                self._path.unlink()
        except OSError:
            pass
        # Clean up backup.
        if self._backup_path and self._backup_path.exists():
            try:
                self._backup_path.unlink()
            except OSError:
                pass
