"""Headless CLI bridge — `claudia run ...` (AGENTS.md M1 + M2 preflight).

Resolves a selection from flags / saved state / stored secrets, runs preflight
(resolve `claude`, ensure LiteLLM, pick a port), then bridges to Claude Code.
This path needs no TUI and proves the whole pipeline.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from . import secrets as secret_store
from . import state
from .bridge import run_bridge, selection_summary
from .constants import DEFAULT_PORT, PROVIDER_KEY_ENV
from .litellm_config import Selection, generate_config
from .preflight import (
    check_litellm,
    find_free_port,
    install_litellm,
    resolve_claude,
)
from .providers import AUTH_API_KEY, all_providers, get_provider, provider_ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claudia run",
        description="Bridge Claude Code to any provider via a local LiteLLM proxy.",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help=f"Provider id. One of: {', '.join(provider_ids())}. "
        "Defaults to last used, else openrouter.",
    )
    parser.add_argument("--model", default=None, help="Main model id (bare, no prefix).")
    parser.add_argument(
        "--small-model",
        default=None,
        help="Small/fast model id (defaults to --model).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help=f"Provider API key. Falls back to ${PROVIDER_KEY_ENV}, then saved key.",
    )
    parser.add_argument("--api-base", default=None, help="Override/required api_base.")
    parser.add_argument("--port", type=int, default=None, help="Preferred proxy port.")
    parser.add_argument(
        "--no-catch-all",
        action="store_true",
        help='Do not add a "*" model alias for unmapped tiers.',
    )
    parser.add_argument(
        "--save-key",
        action="store_true",
        help="Persist the provided API key (keyring or 0600 file).",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Do not auto-install LiteLLM if missing; error instead.",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="Print the generated config.yaml and exit (no proxy, no claude).",
    )
    parser.add_argument(
        "--list-providers",
        action="store_true",
        help="List supported providers and exit.",
    )
    parser.add_argument(
        "claude_args",
        nargs=argparse.REMAINDER,
        help="Arguments after `--` are forwarded to claude.",
    )
    return parser


def _resolve_api_key(args, provider) -> Optional[str]:
    if provider.auth != AUTH_API_KEY:
        return None
    if args.api_key:
        return args.api_key
    env_key = os.environ.get(PROVIDER_KEY_ENV)
    if env_key:
        return env_key
    return secret_store.get_key(provider.id)


def _print_providers() -> None:
    print("Supported providers:\n")
    for p in all_providers():
        auth = p.auth
        base = " (needs api_base)" if p.requires_api_base else ""
        print(f"  {p.id:<16} {p.display_name:<26} auth={auth}{base}")
        if p.suggested_models:
            print(f"      models: {', '.join(p.suggested_models)}")
    print()


def _strip_leading_dashdash(rest: list[str]) -> list[str]:
    # argparse.REMAINDER keeps the leading `--`; drop it for a clean argv.
    if rest and rest[0] == "--":
        return rest[1:]
    return rest


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_providers:
        _print_providers()
        return 0

    cfg = state.load_config()

    provider_id = args.provider or cfg.last_provider or "openrouter"
    try:
        provider = get_provider(provider_id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Model resolution: flag → saved (same provider) → first suggested.
    main_model = args.model
    if not main_model and cfg.last_provider == provider_id:
        main_model = cfg.last_main_model
    if not main_model:
        main_model = provider.suggested_models[0] if provider.suggested_models else None
    if not main_model:
        print(
            f"error: no model specified for provider '{provider_id}' and none "
            "suggested; pass --model.",
            file=sys.stderr,
        )
        return 2

    small_model = args.small_model
    if not small_model and cfg.last_provider == provider_id:
        small_model = cfg.last_small_model

    api_base = args.api_base
    if not api_base and cfg.last_provider == provider_id:
        api_base = cfg.last_api_base
    if not api_base:
        api_base = provider.default_api_base
    if provider.requires_api_base and not api_base:
        print(
            f"error: provider '{provider_id}' requires --api-base.",
            file=sys.stderr,
        )
        return 2

    selection = Selection(
        provider_id=provider_id,
        main_model=main_model,
        small_model=small_model,
        api_base=api_base,
        catch_all=not args.no_catch_all,
    )

    # --print-config is a pure dry-run; skip preflight/secrets entirely.
    if args.print_config:
        print(generate_config(selection))
        return 0

    api_key = _resolve_api_key(args, provider)
    if provider.auth == AUTH_API_KEY and not api_key:
        print(
            f"error: provider '{provider_id}' needs an API key "
            f"(--api-key, ${PROVIDER_KEY_ENV}, or a saved key).",
            file=sys.stderr,
        )
        return 2

    if args.save_key and api_key:
        backend = secret_store.set_key(provider_id, api_key)
        print(f"Saved API key for {provider_id} ({backend}).")

    # --- Preflight (M2) ---
    claude_command = resolve_claude(cfg.claude_command)
    if not claude_command:
        print(
            "error: could not find Claude Code (`claude`).\n"
            "Install it (e.g. `npm install -g @anthropic-ai/claude-code`) or set "
            "CLAUDE_CODE_PATH / claude_command in config.",
            file=sys.stderr,
        )
        return 3

    status = check_litellm()
    if not status.ready:
        if args.no_install:
            print(
                "error: LiteLLM proxy not available and --no-install was given.\n"
                "Install with: pip install 'litellm[proxy]'",
                file=sys.stderr,
            )
            return 4
        print("LiteLLM proxy not found — installing 'litellm[proxy]' …")
        ok, _ = install_litellm(on_output=lambda line: print("  " + line))
        if not ok:
            print(
                "error: LiteLLM install failed. Install manually:\n"
                "  pip install 'litellm[proxy]'",
                file=sys.stderr,
            )
            return 4
        status = check_litellm()
        print(f"LiteLLM ready (version {status.version}).")

    preferred_port = args.port or cfg.preferred_port or DEFAULT_PORT
    port = find_free_port(preferred_port)
    if port != preferred_port:
        print(f"Port {preferred_port} busy — using {port} instead.")

    # Persist selection + environment facts for next time.
    cfg.remember_selection(provider_id, main_model, small_model, api_base)
    cfg.preferred_port = preferred_port
    cfg.litellm_version = status.version
    state.save_config(cfg)

    print(f"Provider : {provider.display_name}")
    print(f"Mapping  : {selection_summary(selection)}")
    print(f"Claude   : {claude_command}")
    print()

    extra = _strip_leading_dashdash(args.claude_args)
    result = run_bridge(
        selection=selection,
        api_key=api_key,
        claude_command=claude_command,
        port=port,
        extra_claude_args=extra,
        report=lambda line: print(line),
    )
    return result.claude_returncode


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
