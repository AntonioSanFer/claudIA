"""Tests for OAuth login-status inspection and logout."""

import io
import json
import time

import pytest

from claudia import oauth
from claudia.providers import get_provider


def _point_to(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))


def test_status_non_oauth_provider():
    st = oauth.oauth_status(get_provider("openrouter"))
    assert not st.supported
    assert not st.logged_in


def test_status_not_signed_in(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path)
    st = oauth.oauth_status(get_provider("github_copilot"))
    assert st.supported
    assert not st.logged_in
    assert "Not signed in" in st.detail


def test_status_signed_in_with_expiry(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path)
    (tmp_path / "access-token").write_text("gho_x", encoding="utf-8")
    future = int(time.time()) + 3600
    (tmp_path / "api-key.json").write_text(
        json.dumps({"token": "tok", "expires_at": future}), encoding="utf-8"
    )
    st = oauth.oauth_status(get_provider("github_copilot"))
    assert st.logged_in
    assert "valid until" in st.detail


def test_status_signed_in_expired(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path)
    (tmp_path / "access-token").write_text("gho_x", encoding="utf-8")
    past = int(time.time()) - 3600
    (tmp_path / "api-key.json").write_text(
        json.dumps({"token": "tok", "expires_at": past}), encoding="utf-8"
    )
    st = oauth.oauth_status(get_provider("github_copilot"))
    assert st.logged_in
    assert "expired" in st.detail


def test_logout_removes_tokens(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path)
    access = tmp_path / "access-token"
    api_key = tmp_path / "api-key.json"
    access.write_text("gho_x", encoding="utf-8")
    api_key.write_text("{}", encoding="utf-8")

    assert oauth.logout(get_provider("github_copilot")) is True
    assert not access.exists()
    assert not api_key.exists()
    # Now reports signed out.
    assert not oauth.oauth_status(get_provider("github_copilot")).logged_in


def test_logout_noop_when_nothing_cached(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path)
    assert oauth.logout(get_provider("github_copilot")) is False


def test_logout_non_oauth_provider():
    assert oauth.logout(get_provider("deepseek")) is False


# --- ensure_oauth_login -----------------------------------------------------

def test_ensure_login_noop_for_non_oauth():
    ok, detail = oauth.ensure_oauth_login(get_provider("openrouter"))
    assert ok and detail == ""


def test_ensure_login_already_signed_in(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path)
    (tmp_path / "access-token").write_text("gho_x", encoding="utf-8")
    seen = []
    ok, detail = oauth.ensure_oauth_login(get_provider("github_copilot"), seen.append)
    assert ok and detail == "already-signed-in"
    assert any("GitHub Copilot" in line for line in seen)


def test_ensure_login_runs_device_flow(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path)

    class FakeAuth:
        def get_access_token(self):
            (tmp_path / "access-token").write_text("gho_new", encoding="utf-8")
            return "gho_new"

    monkeypatch.setattr(oauth, "_github_copilot_authenticator", lambda: FakeAuth())
    seen = []
    ok, detail = oauth.ensure_oauth_login(get_provider("github_copilot"), seen.append)
    assert ok and detail == "signed-in"
    assert any("one-time sign-in" in line for line in seen)
    assert any("complete" in line.lower() for line in seen)


def test_ensure_login_handles_flow_failure(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path)

    class FakeAuth:
        def get_access_token(self):
            raise RuntimeError("timed out")

    monkeypatch.setattr(oauth, "_github_copilot_authenticator", lambda: FakeAuth())
    seen = []
    ok, detail = oauth.ensure_oauth_login(get_provider("github_copilot"), seen.append)
    assert not ok
    assert "timed out" in detail
    assert any("did not complete" in line for line in seen)


# --- github_copilot_models --------------------------------------------------

_MODELS_PAYLOAD = {
    "data": [
        {"id": "claude-sonnet-4.6", "capabilities": {"type": "chat"}, "model_picker_enabled": True},
        {"id": "gpt-4.1", "capabilities": {"type": "chat"}, "model_picker_enabled": True},
        {"id": "gpt-4-0613", "capabilities": {"type": "chat"}, "model_picker_enabled": False},
        {"id": "text-embedding-3-small", "capabilities": {"type": "embeddings"}},
        {"id": "no-caps"},
    ]
}


class _FakeCopilotAuth:
    def __init__(self, captured):
        self._captured = captured

    def get_api_key(self):
        return "cop_token"

    def get_api_base(self):
        return "https://api.business.githubcopilot.com"


def _fake_urlopen(payload, captured):
    def opener(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    return opener


def test_copilot_models_requires_sign_in(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path)  # empty dir -> not signed in
    with pytest.raises(RuntimeError):
        oauth.github_copilot_models()


def test_copilot_models_filters_to_picker_chat(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path)
    (tmp_path / "access-token").write_text("gho_x", encoding="utf-8")
    captured: dict = {}
    monkeypatch.setattr(oauth, "_github_copilot_authenticator", lambda: _FakeCopilotAuth(captured))
    monkeypatch.setattr(oauth.urllib.request, "urlopen", _fake_urlopen(_MODELS_PAYLOAD, captured))

    models = oauth.github_copilot_models()
    assert models == ["claude-sonnet-4.6", "gpt-4.1"]  # chat + picker only, sorted
    # Uses the per-account base from the cache and Copilot's required headers.
    assert captured["url"] == "https://api.business.githubcopilot.com/models"
    lowered = {k.lower(): v for k, v in captured["headers"].items()}
    assert lowered["authorization"] == "Bearer cop_token"
    assert lowered["copilot-integration-id"] == "vscode-chat"


def test_copilot_models_falls_back_to_all_chat_when_no_picker(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path)
    (tmp_path / "access-token").write_text("gho_x", encoding="utf-8")
    payload = {
        "data": [
            {"id": "b-chat", "capabilities": {"type": "chat"}},
            {"id": "a-chat", "capabilities": {"type": "chat"}},
            {"id": "emb", "capabilities": {"type": "embeddings"}},
        ]
    }
    captured: dict = {}
    monkeypatch.setattr(oauth, "_github_copilot_authenticator", lambda: _FakeCopilotAuth(captured))
    monkeypatch.setattr(oauth.urllib.request, "urlopen", _fake_urlopen(payload, captured))

    models = oauth.github_copilot_models()
    assert models == ["a-chat", "b-chat"]
