"""Tests for LiteLLM config.yaml generation."""

from claudia.constants import (
    MAIN_ALIAS,
    MASTER_KEY_ENV,
    PROVIDER_BASE_ENV,
    PROVIDER_KEY_ENV,
    SMALL_ALIAS,
)
from claudia.litellm_config import Selection, generate_config, write_config


def test_main_and_small_aliases_present():
    sel = Selection(provider_id="openrouter", main_model="deepseek/deepseek-chat")
    cfg = generate_config(sel)
    assert f"model_name: {MAIN_ALIAS}" in cfg
    assert f"model_name: {SMALL_ALIAS}" in cfg


def test_small_defaults_to_main():
    sel = Selection(provider_id="deepseek", main_model="deepseek-chat")
    assert sel.effective_small_model == "deepseek-chat"
    assert sel.small_target == "deepseek/deepseek-chat"


def test_distinct_small_model():
    sel = Selection(
        provider_id="openrouter",
        main_model="anthropic/claude-3.5-sonnet",
        small_model="meta-llama/llama-3.3-70b-instruct",
    )
    assert sel.main_target == "openrouter/anthropic/claude-3.5-sonnet"
    assert sel.small_target == "openrouter/meta-llama/llama-3.3-70b-instruct"


def test_no_secrets_in_output():
    sel = Selection(provider_id="openrouter", main_model="deepseek/deepseek-chat")
    cfg = generate_config(sel)
    # Only env references, never raw keys.
    assert f"os.environ/{PROVIDER_KEY_ENV}" in cfg
    assert f"os.environ/{MASTER_KEY_ENV}" in cfg
    assert "sk-" not in cfg


def test_api_key_omitted_for_keyless_provider():
    sel = Selection(provider_id="ollama", main_model="llama3.1", api_base="http://localhost:11434")
    cfg = generate_config(sel)
    assert f"os.environ/{PROVIDER_KEY_ENV}" not in cfg
    # api_base reference is present.
    assert f"os.environ/{PROVIDER_BASE_ENV}" in cfg


def test_api_base_reference_when_required():
    sel = Selection(provider_id="azure", main_model="gpt-4o", api_base="https://x.openai.azure.com")
    cfg = generate_config(sel)
    assert f"api_base: \"os.environ/{PROVIDER_BASE_ENV}\"" in cfg


def test_no_api_base_reference_when_not_needed():
    sel = Selection(provider_id="openrouter", main_model="deepseek/deepseek-chat")
    cfg = generate_config(sel)
    assert PROVIDER_BASE_ENV not in cfg


def test_catch_all_toggle():
    sel = Selection(provider_id="openrouter", main_model="deepseek/deepseek-chat", catch_all=True)
    # Catch-all alias must be quoted to be valid YAML.
    assert 'model_name: "*"' in generate_config(sel)
    sel2 = Selection(provider_id="openrouter", main_model="deepseek/deepseek-chat", catch_all=False)
    assert "model_name: " in generate_config(sel2)
    assert '"*"' not in generate_config(sel2)


def test_master_key_in_general_settings():
    sel = Selection(provider_id="deepseek", main_model="deepseek-chat")
    cfg = generate_config(sel)
    assert "general_settings:" in cfg
    assert f"master_key: \"os.environ/{MASTER_KEY_ENV}\"" in cfg


def test_drop_params_enabled():
    sel = Selection(provider_id="deepseek", main_model="deepseek-chat")
    assert "drop_params: true" in generate_config(sel)


def test_copilot_responses_mode_on_main():
    sel = Selection(provider_id="github_copilot", main_model="gpt-5.4")
    cfg = generate_config(sel)
    # main, small (defaults to main), and the catch-all all carry responses mode.
    assert cfg.count("mode: responses") == 3
    assert "model_info:" in cfg


def test_copilot_no_responses_mode_for_chat_model():
    sel = Selection(provider_id="github_copilot", main_model="gpt-4.1")
    cfg = generate_config(sel)
    assert "mode: responses" not in cfg
    assert "model_info:" not in cfg


def test_copilot_responses_mode_independent_per_tier():
    # gpt-5.4 main needs responses; gpt-4.1 small stays on chat.
    sel = Selection(
        provider_id="github_copilot",
        main_model="gpt-5.4",
        small_model="gpt-4.1",
        catch_all=False,
    )
    cfg = generate_config(sel)
    # Only the main entry carries it (catch_all disabled).
    assert cfg.count("mode: responses") == 1
    try:
        import yaml
    except Exception:
        return
    data = yaml.safe_load(cfg)
    by_name = {m["model_name"]: m for m in data["model_list"]}
    assert by_name[MAIN_ALIAS]["model_info"]["mode"] == "responses"
    assert "model_info" not in by_name[SMALL_ALIAS]


def test_write_config_roundtrip(tmp_path):
    sel = Selection(provider_id="groq", main_model="llama-3.3-70b-versatile")
    out = tmp_path / "nested" / "litellm.config.yaml"
    written = write_config(sel, out)
    assert written.exists()
    assert "model_list:" in written.read_text(encoding="utf-8")


def test_generated_yaml_parses_if_pyyaml_available():
    sel = Selection(provider_id="openrouter", main_model="deepseek/deepseek-chat")
    cfg = generate_config(sel)
    try:
        import yaml
    except Exception:
        return  # pyyaml not installed; structural checks above suffice
    data = yaml.safe_load(cfg)
    names = {m["model_name"] for m in data["model_list"]}
    assert {MAIN_ALIAS, SMALL_ALIAS, "*"} <= names
    assert data["general_settings"]["master_key"] == f"os.environ/{MASTER_KEY_ENV}"
    assert data["litellm_settings"]["drop_params"] is True
