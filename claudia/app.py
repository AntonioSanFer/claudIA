"""ClaudIA entry point + Textual TUI (AGENTS.md §5.6, §10 — M3).

`main()` dispatches:
  * `claudia run ...`  → headless CLI bridge (M1/M2), see claudia.cli
  * `claudia`          → interactive TUI wizard (M3)

The TUI is a *configurator*: it runs preflight, collects provider / credentials /
model, then exits returning a LaunchRequest. The actual proxy+Claude bridge runs
in the normal terminal (outside Textual) so Claude Code inherits a clean TTY.
After Claude Code exits, the wizard is offered again (relaunch / change / quit).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

from . import secrets as secret_store
from . import state
from .bridge import run_bridge, selection_summary
from .constants import DEFAULT_PORT
from .litellm_config import Selection
from .preflight import check_litellm, find_free_port, resolve_claude
from .providers import AUTH_API_KEY, AUTH_OAUTH, Provider


@dataclass
class LaunchRequest:
    selection: Selection
    api_key: Optional[str]
    claude_command: str
    port: int


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "run":
        from .cli import main as cli_main

        return cli_main(argv[1:])

    if argv and argv[0] == "logs":
        return _show_logs(argv[1:])

    if argv and argv[0] in ("-h", "--help"):
        _print_top_help()
        return 0

    if argv and argv[0] in ("--version", "-V"):
        from . import __version__

        print(f"claudia {__version__}")
        return 0

    # Default: launch the TUI.
    return _run_tui_loop()


def _print_top_help() -> None:
    print(
        "claudia - provider bridge for Claude Code\n\n"
        "Usage:\n"
        "  claudia                Launch the interactive TUI.\n"
        "  claudia run [opts]     Headless bridge (see `claudia run --help`).\n"
        "  claudia logs [N]       Show the last N lines of the proxy log.\n"
        "  claudia --version      Print version.\n"
    )


def _show_logs(argv: list[str]) -> int:
    """Print the tail of the most recent proxy log (M5 logs view)."""
    from .paths import proxy_log_file

    path = proxy_log_file()
    if not path.exists():
        print(f"No proxy log yet at {path}")
        return 0
    n = 200
    if argv:
        try:
            n = int(argv[0])
        except ValueError:
            print(f"error: expected a line count, got {argv[0]!r}", file=sys.stderr)
            return 2
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"# {path} (last {min(n, len(lines))} of {len(lines)} lines)\n")
    for line in lines[-n:]:
        print(line)
    return 0


def _run_tui_loop() -> int:
    """Run TUI → bridge → repeat until the user quits."""
    try:
        from textual.app import App  # noqa: F401  (probe availability)
    except Exception:
        print(
            "The TUI requires Textual. Install it with:\n"
            "  pip install textual\n\n"
            "Or use the headless bridge: `claudia run --help`.",
            file=sys.stderr,
        )
        return 1

    while True:
        request = ClaudIAApp().run()
        if request is None:
            return 0

        # Persist the selection for next time.
        cfg = state.load_config()
        sel = request.selection
        cfg.remember_selection(
            sel.provider_id, sel.main_model, sel.small_model, sel.api_base
        )
        cfg.preferred_port = request.port
        state.save_config(cfg)

        result = run_bridge(
            selection=request.selection,
            api_key=request.api_key,
            claude_command=request.claude_command,
            port=request.port,
            report=lambda line: print(line),
        )
        print(f"\nClaude Code session ended (code {result.claude_returncode}).")
        try:
            again = input("Relaunch the wizard? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return 0
        if again in ("n", "no", "q", "quit"):
            return result.claude_returncode


# ---------------------------------------------------------------------------
# Textual TUI
# ---------------------------------------------------------------------------
# Imports are inside the functions/classes below guarded by _run_tui_loop having
# already verified Textual is importable.

def _build_tui_classes():  # pragma: no cover - requires textual at runtime
    from textual import work
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, VerticalScroll
    from textual.screen import Screen
    from textual.widgets import (
        Button,
        Checkbox,
        Footer,
        Header,
        Input,
        Label,
        OptionList,
        RichLog,
        Static,
    )
    from textual.widgets.option_list import Option

    from .catalog import list_models
    from .oauth import ensure_oauth_login
    from .oauth import logout as oauth_logout
    from .oauth import oauth_status
    from .preflight import install_litellm
    from .providers import all_providers, get_provider

    class PreflightScreen(Screen):
        """Resolve claude + ensure LiteLLM before anything else.

        Keyboard-navigable: ←/→ (or Tab) move between actions, Enter activates
        the focused one, and c/s/i/q are direct shortcuts.
        """

        BINDINGS = [
            ("left", "focus_previous", "Prev"),
            ("right", "focus_next", "Next"),
            ("c", "do_continue", "Continue"),
            ("s", "do_skip", "Skip next time"),
            ("i", "do_install", "Install"),
            ("q", "quit_app", "Quit"),
        ]

        CSS = """
        #log { height: 1fr; border: round $primary; padding: 0 1; }
        #buttons { height: auto; align: center middle; padding: 1 0; }
        Button { margin: 0 1; }
        """

        def __init__(self) -> None:
            super().__init__()
            self.claude_command: Optional[str] = None
            self._ready = False

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static("Preflight checks", classes="title")
            yield RichLog(id="log", highlight=False, markup=True)
            with Horizontal(id="buttons"):
                yield Button("Install LiteLLM", id="install", variant="primary", disabled=True)
                yield Button("Continue", id="continue", variant="success", disabled=True)
                yield Button("Skip next time", id="skip", variant="warning", disabled=True)
                yield Button("Quit", id="quit", variant="error")
            yield Footer()

        def on_mount(self) -> None:
            self.run_checks()

        # All UI mutations below run from thread workers, so they go through
        # call_from_thread (Textual requires this off the main thread).
        def _log(self, message: str) -> None:
            self.app.call_from_thread(self.query_one("#log", RichLog).write, message)

        def _set_disabled(self, button_id: str, disabled: bool) -> None:
            def apply() -> None:
                self.query_one(f"#{button_id}", Button).disabled = disabled

            self.app.call_from_thread(apply)

        @work(thread=True)
        def run_checks(self) -> None:
            self._log("[bold]Checking Claude Code…[/bold]")
            cfg = state.load_config()
            self.claude_command = resolve_claude(cfg.claude_command)
            if self.claude_command:
                self._log(f"  [green]✓[/green] claude → {self.claude_command}")
            else:
                self._log("  [red]✗ claude not found.[/red] Install Claude Code, then restart.")
                return

            self._log("[bold]Checking LiteLLM proxy…[/bold]")
            status = check_litellm()
            if status.ready:
                self._log(f"  [green]✓[/green] litellm ready (version {status.version})")
                self._enable_continue()
            else:
                self._log("  [yellow]! LiteLLM proxy not installed.[/yellow]")
                self._set_disabled("install", False)
                self.app.call_from_thread(self.query_one("#install", Button).focus)

        def _enable_continue(self) -> None:
            self._ready = True
            self._set_disabled("continue", False)
            self._set_disabled("skip", False)
            self.app.call_from_thread(self.query_one("#continue", Button).focus)

        @work(thread=True)
        def do_install(self) -> None:
            self._set_disabled("install", True)
            ok, _ = install_litellm(on_output=lambda line: self._log("  " + line))
            if ok:
                self._log("  [green]✓ LiteLLM installed.[/green]")
                self._enable_continue()
            else:
                self._log("  [red]✗ Install failed. Run: pip install 'litellm[proxy]'[/red]")

        # --- shared actions (used by both buttons and key bindings) ---
        def _continue(self) -> None:
            if self._ready:
                self.dismiss(self.claude_command)

        def _skip(self) -> None:
            """Persist 'don't show preflight when OK', then continue."""
            if not self._ready:
                return
            cfg = state.load_config()
            cfg.skip_preflight_when_ok = True
            state.save_config(cfg)
            self.notify("Preflight will be skipped next time (unless a problem is found).")
            self.dismiss(self.claude_command)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "quit":
                self.app.exit(None)
            elif event.button.id == "install":
                self.do_install()
            elif event.button.id == "continue":
                self._continue()
            elif event.button.id == "skip":
                self._skip()

        def action_focus_next(self) -> None:
            self.focus_next()

        def action_focus_previous(self) -> None:
            self.focus_previous()

        def action_do_continue(self) -> None:
            self._continue()

        def action_do_skip(self) -> None:
            self._skip()

        def action_do_install(self) -> None:
            btn = self.query_one("#install", Button)
            if not btn.disabled:
                self.do_install()

        def action_quit_app(self) -> None:
            self.app.exit(None)

    class ProviderScreen(Screen):
        CSS = """
        OptionList { height: 1fr; border: round $primary; }
        #hint { height: auto; color: $text-muted; padding: 1 1; }
        """

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static("Choose a provider", classes="title")
            options = [
                Option(f"{p.display_name}  —  {p.notes or p.prefix}", id=p.id)
                for p in all_providers()
            ]
            ol = OptionList(*options)
            cfg = state.load_config()
            if cfg.last_provider:
                for i, p in enumerate(all_providers()):
                    if p.id == cfg.last_provider:
                        ol.highlighted = i
                        break
            yield ol
            yield Static("Enter to select · q to quit", id="hint")
            yield Footer()

        def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
            self.dismiss(event.option.id)

        def on_key(self, event) -> None:
            if event.key == "q":
                self.app.exit(None)

    class CredentialScreen(Screen):
        """Provider detail / credentials.

        Keyboard-navigable: ↑/↓ (or Tab) move between fields and buttons, Enter
        activates a focused button, and Esc goes back. For OAuth providers it
        shows sign-in status and offers a one-click logout.
        """

        BINDINGS = [
            ("up", "focus_previous", "Prev"),
            ("down", "focus_next", "Next"),
            ("escape", "go_back", "Back"),
        ]

        CSS = """
        #form { padding: 1 2; height: auto; }
        Label { padding: 1 0 0 0; }
        Input { width: 100%; }
        #oauth_status { padding: 1 0; }
        #buttons { padding: 1 0; align: left middle; height: auto; }
        Button { margin: 0 1 0 0; }
        """

        def __init__(self, provider: Provider) -> None:
            super().__init__()
            self.provider = provider
            self._is_oauth = provider.auth == AUTH_OAUTH

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static(f"Credentials — {self.provider.display_name}", classes="title")
            cfg = state.load_config()
            with VerticalScroll(id="form"):
                if self.provider.auth == AUTH_API_KEY:
                    saved = secret_store.get_key(self.provider.id)
                    hint = f"(saved key ending {secret_store.mask(saved)} will be used if left blank)" if saved else ""
                    yield Label(f"API key {hint}")
                    yield Input(password=True, placeholder="sk-…", id="api_key")
                    yield Checkbox("Save this key", id="save_key", value=bool(saved))
                elif self._is_oauth:
                    st = oauth_status(self.provider)
                    icon = "[green]v[/green]" if st.logged_in else "[yellow]o[/yellow]"
                    yield Static(f"{icon} {st.detail}", id="oauth_status")
                    yield Label(
                        "Sign in now to load your live model list on the next screen; "
                        "otherwise sign-in happens automatically on launch."
                    )
                else:
                    yield Label(f"No API key required ({self.provider.auth}).")

                if self.provider.requires_api_base or self.provider.default_api_base:
                    base_default = (
                        (cfg.last_api_base if cfg.last_provider == self.provider.id else None)
                        or self.provider.default_api_base
                        or ""
                    )
                    yield Label("API base URL")
                    yield Input(value=base_default, placeholder="https://…", id="api_base")
            with Horizontal(id="buttons"):
                yield Button("Next", id="next", variant="primary")
                if self._is_oauth:
                    logged_in = oauth_status(self.provider).logged_in
                    yield Button("Sign in", id="signin", variant="primary", disabled=logged_in)
                    yield Button("Log out", id="logout", variant="warning", disabled=not logged_in)
                yield Button("Back", id="back")
            yield Footer()

        def _refresh_oauth(self) -> None:
            st = oauth_status(self.provider)
            icon = "[green]v[/green]" if st.logged_in else "[yellow]o[/yellow]"
            self.query_one("#oauth_status", Static).update(f"{icon} {st.detail}")
            self.query_one("#logout", Button).disabled = not st.logged_in
            self.query_one("#signin", Button).disabled = st.logged_in

        def _do_signin(self) -> None:
            if oauth_status(self.provider).logged_in:
                return
            # Drop out of the TUI so the device-login prompt (verification URL +
            # code) shows in the real terminal, then return and refresh status.
            with self.app.suspend():
                ok, _ = ensure_oauth_login(self.provider, report=print)
                if ok:
                    try:
                        input("\nPress Enter to continue…")
                    except (EOFError, KeyboardInterrupt):
                        pass
            self._refresh_oauth()
            if oauth_status(self.provider).logged_in:
                self.notify("Signed in — your live models will load on the next screen.")
            else:
                self.notify("Sign-in did not complete.", severity="warning")

        def _do_logout(self) -> None:
            removed = oauth_logout(self.provider)
            self._refresh_oauth()
            if removed:
                self.notify("Logged out — LiteLLM will prompt for sign-in on next launch.")
            else:
                self.notify("No cached sign-in to remove.", severity="warning")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "back":
                self.dismiss(False)
                return
            if event.button.id == "logout":
                self._do_logout()
                return
            if event.button.id == "signin":
                self._do_signin()
                return
            api_key = None
            save = False
            if self.provider.auth == AUTH_API_KEY:
                api_key = self.query_one("#api_key", Input).value.strip() or None
                if api_key is None:
                    api_key = secret_store.get_key(self.provider.id)
                save = self.query_one("#save_key", Checkbox).value
            api_base = None
            try:
                api_base = self.query_one("#api_base", Input).value.strip() or None
            except Exception:
                api_base = self.provider.default_api_base

            if self.provider.auth == AUTH_API_KEY and not api_key:
                self.notify("An API key is required for this provider.", severity="error")
                return
            if self.provider.requires_api_base and not api_base:
                self.notify("This provider requires an API base URL.", severity="error")
                return

            if save and api_key:
                secret_store.set_key(self.provider.id, api_key)
            self.dismiss({"api_key": api_key, "api_base": api_base})

        def action_focus_next(self) -> None:
            self.focus_next()

        def action_focus_previous(self) -> None:
            self.focus_previous()

        def action_go_back(self) -> None:
            self.dismiss(False)

    class ModelScreen(Screen):
        CSS = """
        #form { padding: 1 2; height: 1fr; }
        Label { padding: 1 0 0 0; }
        Input { width: 100%; }
        #status { color: $text-muted; }
        #models { height: 1fr; min-height: 6; border: round $primary; }
        #buttons { padding: 1 0; align: left middle; height: auto; }
        Button { margin: 0 1 0 0; }
        """

        def __init__(self, provider: Provider, api_key=None, api_base=None) -> None:
            super().__init__()
            self.provider = provider
            self.api_key = api_key
            self.api_base = api_base
            self._all_models: list[str] = []
            self._programmatic = False  # guard so list-select doesn't re-filter

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static(f"Models - {self.provider.display_name}", classes="title")
            cfg = state.load_config()
            default_main = (
                (cfg.last_main_model if cfg.last_provider == self.provider.id else None)
                or (self.provider.suggested_models[0] if self.provider.suggested_models else "")
            )
            with VerticalScroll(id="form"):
                yield Static("Loading models…", id="status")
                yield Label("Main model (type to filter, or pick below)")
                yield Input(value=default_main, id="main_model", placeholder="model id")
                yield OptionList(id="models")
                yield Label("Small/fast model (blank = reuse main)")
                yield Input(value="", id="small_model", placeholder="optional")
            with Horizontal(id="buttons"):
                yield Button("Launch", id="launch", variant="success")
                yield Button("Back", id="back")
            yield Footer()

        def on_mount(self) -> None:
            self.fetch_models()

        @work(thread=True)
        def fetch_models(self) -> None:
            catalog = list_models(self.provider, self.api_key, self.api_base)
            self.app.call_from_thread(self._apply_catalog, catalog)

        def _apply_catalog(self, catalog) -> None:
            self._all_models = catalog.models
            status = self.query_one("#status", Static)
            if catalog.is_live:
                status.update(f"[green]{len(catalog.models)} models (live)[/green]")
            else:
                why = f" - {catalog.error}" if catalog.error else ""
                status.update(
                    f"[yellow]{len(catalog.models)} models (preloaded{why})[/yellow]"
                )
            # Show the whole catalog on load; only narrow once the user types.
            self._populate("")

        def _populate(self, needle: str) -> None:
            ol = self.query_one("#models", OptionList)
            ol.clear_options()
            needle = needle.lower()
            shown = [m for m in self._all_models if needle in m.lower()] if needle else self._all_models
            for model_id in shown[:500]:  # cap for responsiveness
                ol.add_option(Option(model_id, id=model_id))

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id == "main_model" and not self._programmatic:
                self._populate(event.value.strip())

        def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
            self._programmatic = True
            self.query_one("#main_model", Input).value = event.option.id or ""
            self._programmatic = False

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "back":
                self.dismiss(False)
                return
            main_model = self.query_one("#main_model", Input).value.strip()
            small_model = self.query_one("#small_model", Input).value.strip() or None
            if not main_model:
                self.notify("A main model is required.", severity="error")
                return
            self.dismiss({"main_model": main_model, "small_model": small_model})

    class ClaudIAApp(App):
        TITLE = "ClaudIA — Claude Code provider bridge"
        BINDINGS = [("ctrl+c", "quit", "Quit")]
        CSS = """
        .title { text-style: bold; padding: 1 1 0 1; color: $accent; }
        """

        def on_mount(self) -> None:
            self.run_wizard()

        async def _preflight(self) -> Optional[str]:
            """Resolve claude (and verify LiteLLM), showing the preflight screen
            unless the user opted to skip it AND everything checks out.

            Returns the resolved claude command, or None to abort.
            """
            cfg = state.load_config()
            if cfg.skip_preflight_when_ok:
                # Run the checks silently; only surface the screen on a problem.
                claude_command = resolve_claude(cfg.claude_command)
                if claude_command and check_litellm().ready:
                    return claude_command
            return await self.push_screen_wait(PreflightScreen())

        @work
        async def run_wizard(self) -> None:
            claude_command = await self._preflight()
            if not claude_command:
                self.exit(None)
                return

            while True:
                provider_id = await self.push_screen_wait(ProviderScreen())
                if not provider_id:
                    self.exit(None)
                    return
                provider = get_provider(provider_id)

                cred = await self.push_screen_wait(CredentialScreen(provider))
                if cred is False:  # back
                    continue
                models = await self.push_screen_wait(
                    ModelScreen(provider, cred["api_key"], cred["api_base"])
                )
                if models is False:  # back
                    continue

                selection = Selection(
                    provider_id=provider_id,
                    main_model=models["main_model"],
                    small_model=models["small_model"],
                    api_base=cred["api_base"],
                    catch_all=True,
                )
                port = find_free_port(state.load_config().preferred_port or DEFAULT_PORT)
                self.exit(
                    LaunchRequest(
                        selection=selection,
                        api_key=cred["api_key"],
                        claude_command=claude_command,
                        port=port,
                    )
                )
                return

    # Expose inner screen classes for testing (they are closures otherwise).
    ClaudIAApp._screens = {
        "preflight": PreflightScreen,
        "provider": ProviderScreen,
        "credential": CredentialScreen,
        "model": ModelScreen,
    }
    return ClaudIAApp


class _LazyApp:
    """Defer building Textual classes until the TUI is actually run."""

    def run(self) -> Optional[LaunchRequest]:  # pragma: no cover - interactive
        app_cls = _build_tui_classes()
        return app_cls().run()


# Public name used by _run_tui_loop; constructed lazily so importing claudia.app
# never requires Textual.
def ClaudIAApp() -> _LazyApp:  # noqa: N802 - factory mimics a class
    return _LazyApp()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
