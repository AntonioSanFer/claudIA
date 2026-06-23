"""Tests for env construction, state persistence, and secret masking."""

from claudia import secrets as secret_store
from claudia import state
from claudia.constants import MAIN_ALIAS, SMALL_ALIAS
from claudia.launcher import (
    build_claude_env,
    build_proxy_env,
    generate_master_key,
)
from claudia.litellm_config import Selection


def test_master_key_format():
    key = generate_master_key()
    assert key.startswith("sk-claudia-")
    assert len(key) > 20
    assert generate_master_key() != generate_master_key()


def test_build_claude_env_sets_required_vars():
    env = build_claude_env("http://127.0.0.1:4000", "sk-master", base_env={})
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4000"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-master"
    assert env["ANTHROPIC_MODEL"] == MAIN_ALIAS
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == SMALL_ALIAS


def test_build_claude_env_drops_api_key():
    env = build_claude_env("http://x", "k", base_env={"ANTHROPIC_API_KEY": "leak"})
    assert "ANTHROPIC_API_KEY" not in env  # never both auth styles


def test_build_claude_env_disables_telemetry_and_error_reporting():
    env = build_claude_env("http://x", "k", base_env={})
    assert env["DISABLE_TELEMETRY"] == "1"
    assert env["DISABLE_ERROR_REPORTING"] == "1"
    # Autoupdater and nonessential model calls are intentionally left enabled.
    assert "DISABLE_AUTOUPDATER" not in env
    assert "DISABLE_NON_ESSENTIAL_MODEL_CALLS" not in env
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in env


def test_build_claude_env_respects_user_override():
    env = build_claude_env("http://x", "k", base_env={"DISABLE_TELEMETRY": "0"})
    assert env["DISABLE_TELEMETRY"] == "0"  # setdefault keeps an explicit choice


def test_build_proxy_env_disables_telemetry_traffic():
    sel = Selection(provider_id="openrouter", main_model="deepseek/deepseek-chat")
    env = build_proxy_env(sel, "sk-provider", "sk-master", base_env={})
    assert env["LITELLM_DONT_SHOW_FEEDBACK_BOX"] == "True"
    # The bundled cost map is left disabled (pricing JSON fetched as usual).
    assert "LITELLM_LOCAL_MODEL_COST_MAP" not in env


def test_build_proxy_env_carries_secrets():
    sel = Selection(provider_id="openrouter", main_model="deepseek/deepseek-chat")
    env = build_proxy_env(sel, "sk-provider", "sk-master", base_env={})
    assert env["CLAUDIA_PROVIDER_KEY"] == "sk-provider"
    assert env["CLAUDIA_MASTER_KEY"] == "sk-master"


def test_build_proxy_env_uses_default_api_base_for_ollama():
    sel = Selection(provider_id="ollama", main_model="llama3.1")
    env = build_proxy_env(sel, None, "sk-master", base_env={})
    assert env["CLAUDIA_PROVIDER_BASE"] == "http://localhost:11434"
    assert "CLAUDIA_PROVIDER_KEY" not in env


def test_mask_secret():
    assert secret_store.mask("supersecretkey1234") == "...1234"
    assert secret_store.mask("ab") == "**"
    assert secret_store.mask(None) == "<none>"
    assert secret_store.mask("") == "<none>"


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "config_file", lambda: tmp_path / "config.json")
    cfg = state.load_config()
    assert cfg.last_provider is None
    cfg.remember_selection("groq", "llama-3.3-70b-versatile", None, None)
    state.save_config(cfg)

    reloaded = state.load_config()
    assert reloaded.last_provider == "groq"
    assert reloaded.last_main_model == "llama-3.3-70b-versatile"
    assert "groq" in reloaded.known_providers


def test_config_tolerates_unknown_keys(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"last_provider": "deepseek", "future_field": 42}', encoding="utf-8")
    monkeypatch.setattr(state, "config_file", lambda: path)
    cfg = state.load_config()
    assert cfg.last_provider == "deepseek"


def test_secret_file_fallback_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_store, "secrets_file", lambda: tmp_path / "secrets.json")
    # Force the file backend regardless of any real keyring on the host.
    monkeypatch.setattr(secret_store, "keyring_available", lambda: False)
    assert secret_store.get_key("openrouter") is None
    backend = secret_store.set_key("openrouter", "sk-abc123")
    assert backend == "file"
    assert secret_store.get_key("openrouter") == "sk-abc123"
    secret_store.delete_key("openrouter")
    assert secret_store.get_key("openrouter") is None
