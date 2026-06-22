"""Pilot tests for the provider detail / credential screen (OAuth + nav)."""

import asyncio
import json
import time

import pytest

pytest.importorskip("textual")

from textual.app import App  # noqa: E402
from textual.widgets import Button, Static  # noqa: E402

from claudia.app import _build_tui_classes  # noqa: E402
from claudia.providers import get_provider  # noqa: E402

CredentialScreen = _build_tui_classes()._screens["credential"]


class _Host(App):
    def __init__(self, provider):
        super().__init__()
        self._provider = provider

    async def on_mount(self) -> None:
        await self.push_screen(CredentialScreen(self._provider))


def test_oauth_screen_shows_signed_in_and_logout(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
    (tmp_path / "access-token").write_text("gho_x", encoding="utf-8")
    (tmp_path / "api-key.json").write_text(
        json.dumps({"token": "t", "expires_at": int(time.time()) + 3600}),
        encoding="utf-8",
    )

    async def scenario():
        app = _Host(get_provider("github_copilot"))
        async with app.run_test() as pilot:
            screen = app.screen
            status = screen.query_one("#oauth_status", Static)
            assert "Signed in" in str(status.render())
            logout_btn = screen.query_one("#logout", Button)
            assert not logout_btn.disabled

            screen._do_logout()
            await pilot.pause()

            assert logout_btn.disabled  # now signed out
            assert not (tmp_path / "access-token").exists()
            assert "Not signed in" in str(status.render())

    asyncio.run(scenario())


def test_oauth_screen_signed_out_disables_logout(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))

    async def scenario():
        app = _Host(get_provider("github_copilot"))
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.screen.query_one("#logout", Button).disabled

    asyncio.run(scenario())


def test_escape_goes_back_and_no_logout_for_non_oauth(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))

    async def scenario():
        app = _Host(get_provider("deepseek"))
        async with app.run_test() as pilot:
            assert not app.screen.query("#logout")  # non-OAuth: no logout button
            result = []
            app.screen.dismiss = lambda value=None: result.append(value)
            await pilot.press("escape")
            assert result == [False]

    asyncio.run(scenario())
