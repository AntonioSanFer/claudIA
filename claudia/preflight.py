"""Preflight checks (AGENTS.md §5.1, §7, §6 port selection — M2).

Resolve the user's `claude` command, detect/auto-install LiteLLM, and pick a
free loopback port. None of this touches a provider or starts the proxy.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from .constants import DEFAULT_PORT, LOOPBACK_HOST

# Names to probe on PATH, accounting for Windows shims.
_CLAUDE_CANDIDATES = ("claude", "claude.cmd", "claude.exe")


# --- claude resolution ------------------------------------------------------

def resolve_claude(config_command: Optional[str] = None) -> Optional[str]:
    """Resolve the launch command for Claude Code (AGENTS.md §8).

    Resolution order:
      1. Explicit override (`config_command`, from ClaudIA config).
      2. CLAUDE_CODE_PATH env override.
      3. `claude` on PATH (incl. claude.cmd / claude.exe on Windows).

    Returns an absolute path/command string, or None if unresolved.
    """
    # 1. Config override — accept a bare command (resolved on PATH) or a path.
    if config_command:
        if os.path.isabs(config_command) and os.path.exists(config_command):
            return config_command
        found = shutil.which(config_command)
        if found:
            return found
        if os.path.exists(config_command):
            return config_command

    # 2. Env override.
    env_override = os.environ.get("CLAUDE_CODE_PATH")
    if env_override:
        if os.path.exists(env_override):
            return env_override
        found = shutil.which(env_override)
        if found:
            return found

    # 3. PATH.
    for name in _CLAUDE_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


# --- LiteLLM detection / install --------------------------------------------

@dataclass
class LiteLLMStatus:
    importable: bool
    cli_path: Optional[str]
    version: Optional[str]
    has_proxy_extra: bool

    @property
    def ready(self) -> bool:
        # The proxy extra (fastapi/uvicorn) is what we actually need to serve.
        return self.importable and self.has_proxy_extra


def _litellm_version() -> Optional[str]:
    try:
        from importlib.metadata import version

        return version("litellm")
    except Exception:
        return None


def _has_proxy_extra() -> bool:
    """The `[proxy]` extra pulls in fastapi + uvicorn; probe for them."""
    return (
        importlib.util.find_spec("fastapi") is not None
        and importlib.util.find_spec("uvicorn") is not None
    )


def check_litellm() -> LiteLLMStatus:
    """Detect LiteLLM and whether the proxy server deps are present."""
    importable = importlib.util.find_spec("litellm") is not None
    return LiteLLMStatus(
        importable=importable,
        cli_path=shutil.which("litellm"),
        version=_litellm_version(),
        has_proxy_extra=_has_proxy_extra(),
    )


def _install_command() -> list[str]:
    """Preferred install command: uv if available, else pip into this env."""
    if shutil.which("uv"):
        return ["uv", "pip", "install", "litellm[proxy]"]
    return [sys.executable, "-m", "pip", "install", "litellm[proxy]"]


def install_litellm(
    on_output: Optional[Callable[[str], None]] = None,
) -> tuple[bool, str]:
    """Install `litellm[proxy]`. Returns (ok, combined_output).

    Streams output line-by-line to `on_output` if provided. Idempotent: pip/uv
    treat an already-satisfied requirement as a no-op.
    """
    cmd = _install_command()
    if on_output:
        on_output(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        return False, f"Could not run installer: {exc}"

    captured: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        captured.append(line)
        if on_output:
            on_output(line)
    proc.wait()
    ok = proc.returncode == 0 and check_litellm().ready
    return ok, "\n".join(captured)


# --- port selection ---------------------------------------------------------

def is_port_free(port: int, host: str = LOOPBACK_HOST) -> bool:
    """True if `port` can be bound on `host`."""
    # Note: deliberately NO SO_REUSEADDR — on Windows it would let us bind a
    # port that is actively LISTENing, yielding a false "free" result.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def find_free_port(
    preferred: int = DEFAULT_PORT,
    host: str = LOOPBACK_HOST,
    attempts: int = 100,
) -> int:
    """Return `preferred` if free, else the next free port by linear scan.

    Falls back to an OS-assigned ephemeral port if the scan exhausts.
    """
    for candidate in range(preferred, preferred + attempts):
        if candidate <= 65535 and is_port_free(candidate, host):
            return candidate
    # Last resort: let the OS pick.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]
