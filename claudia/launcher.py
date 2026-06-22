"""Build environments and spawn Claude Code (AGENTS.md §8).

Two environments are involved:
  * the LiteLLM subprocess env — carries the provider key + master key as the
    `os.environ/...` references in config.yaml resolve against it;
  * the Claude Code child env — points Claude at the proxy and supplies the
    master key as a Bearer token.

Secrets live only in these process environments, never on disk or in logs.
"""

from __future__ import annotations

import os
import secrets as _secrets
import subprocess
from typing import Optional, Sequence

from .constants import (
    MAIN_ALIAS,
    MASTER_KEY_ENV,
    PROVIDER_BASE_ENV,
    PROVIDER_KEY_ENV,
    SMALL_ALIAS,
)
from .litellm_config import Selection


def generate_master_key() -> str:
    """A random proxy master key. The `sk-` prefix matches LiteLLM convention."""
    return "sk-claudia-" + _secrets.token_hex(24)


def build_proxy_env(
    selection: Selection,
    api_key: Optional[str],
    master_key: str,
    base_env: Optional[dict] = None,
) -> dict:
    """Env for the LiteLLM subprocess (resolves `os.environ/...` refs)."""
    env = dict(base_env if base_env is not None else os.environ)
    # Force UTF-8 stdio in the proxy: LiteLLM prints a Unicode startup banner and
    # would crash with UnicodeEncodeError when its stdout is redirected to a file
    # on a Windows (cp1252) console. (AGENTS.md §14 Windows handling.)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env[MASTER_KEY_ENV] = master_key
    if api_key:
        env[PROVIDER_KEY_ENV] = api_key
    api_base = selection.api_base or selection.provider.default_api_base
    if api_base:
        env[PROVIDER_BASE_ENV] = api_base
    return env


def build_claude_env(
    base_url: str,
    master_key: str,
    base_env: Optional[dict] = None,
) -> dict:
    """Env injected into the spawned `claude` process (AGENTS.md §8 table)."""
    env = dict(base_env if base_env is not None else os.environ)

    # Route Claude Code through the proxy.
    env["ANTHROPIC_BASE_URL"] = base_url
    # Bearer auth that satisfies both Claude Code and the proxy master key.
    env["ANTHROPIC_AUTH_TOKEN"] = master_key
    # Never set both auth styles — Claude Code rejects that. Drop x-api-key.
    env.pop("ANTHROPIC_API_KEY", None)

    # Model aliases (must exist in the generated model_list).
    env["ANTHROPIC_MODEL"] = MAIN_ALIAS
    env["ANTHROPIC_SMALL_FAST_MODEL"] = SMALL_ALIAS
    # Map Claude Code's in-app /model tiers onto our aliases where supported.
    env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = MAIN_ALIAS
    env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = MAIN_ALIAS
    env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = SMALL_ALIAS

    return env


def launch_claude(
    claude_command: str,
    env: dict,
    extra_args: Sequence[str] = (),
) -> int:
    """Run `claude` in the foreground, inheriting the terminal. Returns its code.

    On Windows the resolved command may be a `.cmd` shim; subprocess handles that
    when given the full path. We do not use shell=True (avoids quoting issues).
    """
    cmd = [claude_command, *extra_args]
    try:
        completed = subprocess.run(cmd, env=env)
        return completed.returncode
    except KeyboardInterrupt:
        # Claude Code handles Ctrl-C itself; if it propagates, treat as clean.
        return 130
