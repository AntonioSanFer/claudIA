"""End-to-end bridge orchestration (AGENTS.md §1 steps 4-7).

Ties config generation, the proxy lifecycle, and the Claude launcher together
with guaranteed teardown — even on Ctrl-C / SIGTERM. Used by both the headless
CLI (M1) and the TUI (M3).
"""

from __future__ import annotations

import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import state
from .launcher import (
    build_claude_env,
    build_proxy_env,
    generate_master_key,
    launch_claude,
)
from .litellm_config import Selection, write_config
from .paths import generated_config_file
from .proxy import ProxyManager
from .secrets import mask

Reporter = Callable[[str], None]


@dataclass
class BridgeResult:
    claude_returncode: int
    port: int
    base_url: str


def _noop(_: str) -> None:  # pragma: no cover - trivial
    pass


def run_bridge(
    selection: Selection,
    api_key: Optional[str],
    claude_command: str,
    port: int,
    extra_claude_args: Sequence[str] = (),
    master_key: Optional[str] = None,
    health_timeout: float = 60.0,
    report: Reporter = _noop,
    config_path: Optional[Path] = None,
) -> BridgeResult:
    """Generate config, start the proxy, launch Claude Code, then tear down.

    `report` receives human-readable status lines. The proxy is always stopped
    before returning, including on exceptions and signals.
    """
    master_key = master_key or generate_master_key()
    config_path = config_path or generated_config_file()

    write_config(selection, config_path)
    report(f"Wrote LiteLLM config -> {config_path}")

    proxy_env = build_proxy_env(selection, api_key, master_key)
    proxy = ProxyManager(config_path, port, proxy_env)

    # Ensure teardown even if a signal interrupts us mid-flight.
    previous_handlers: dict[int, object] = {}

    def _handle_signal(signum, _frame):  # pragma: no cover - signal path
        proxy.stop()
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            # Not in main thread (e.g. TUI worker) — skip; teardown still runs
            # via the finally block below.
            pass

    try:
        report(f"Starting LiteLLM proxy on {proxy.base_url} …")
        proxy.start()
        proxy.wait_healthy(timeout=health_timeout)
        report("Proxy healthy.")

        state.write_runtime(proxy.pid or -1, port, str(config_path))

        report(
            f"Mapping: Claude Code -> {selection_summary(selection)} "
            f"(key {mask(api_key)})"
        )
        report("Launching Claude Code - handing over the terminal...\n")

        claude_env = build_claude_env(proxy.base_url, master_key)
        code = launch_claude(claude_command, claude_env, extra_claude_args)
        report(f"\nClaude Code exited (code {code}).")
        return BridgeResult(code, port, proxy.base_url)
    finally:
        proxy.stop()
        state.clear_runtime()
        report("Proxy stopped; environment clean.")
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)  # type: ignore[arg-type]
            except (ValueError, OSError):
                pass


def selection_summary(selection: Selection) -> str:
    """One-line 'claudia-main -> provider/model' style summary."""
    main = f"claudia-main -> {selection.main_target}"
    if selection.effective_small_model != selection.main_model:
        return f"{main}; claudia-small -> {selection.small_target}"
    return main
