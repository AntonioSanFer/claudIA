"""Tests for the provider registry."""

import pytest

from claudia.providers import (
    AUTH_API_KEY,
    AUTH_NONE,
    AUTH_OAUTH,
    all_providers,
    get_provider,
    needs_responses_api,
    provider_ids,
)


def test_registry_non_empty_and_has_core_providers():
    ids = provider_ids()
    for required in ("openrouter", "deepseek", "ollama", "custom"):
        assert required in ids


def test_ids_are_unique():
    ids = provider_ids()
    assert len(ids) == len(set(ids))


def test_every_prefix_ends_with_slash():
    for p in all_providers():
        assert p.prefix.endswith("/"), p.id


def test_auth_styles_are_valid():
    valid = {AUTH_API_KEY, AUTH_OAUTH, AUTH_NONE}
    for p in all_providers():
        assert p.auth in valid, p.id


def test_get_provider_unknown_raises():
    with pytest.raises(KeyError):
        get_provider("does-not-exist")


def test_full_model_prefixing():
    openrouter = get_provider("openrouter")
    assert openrouter.full_model("deepseek/deepseek-chat") == "openrouter/deepseek/deepseek-chat"


def test_full_model_idempotent_when_already_prefixed():
    openrouter = get_provider("openrouter")
    already = "openrouter/deepseek/deepseek-chat"
    assert openrouter.full_model(already) == already


def test_full_model_strips_whitespace():
    deepseek = get_provider("deepseek")
    assert deepseek.full_model("  deepseek-chat  ") == "deepseek/deepseek-chat"


def test_providers_requiring_base_have_flag():
    ollama = get_provider("ollama")
    assert ollama.requires_api_base
    assert ollama.default_api_base
    assert ollama.auth == AUTH_NONE
    assert not ollama.needs_api_key


@pytest.mark.parametrize(
    "model",
    ["gpt-5.4", "gpt-5.5", "gpt-6", "gpt-5.1-codex", "gpt-5-codex", "GPT-5.4"],
)
def test_copilot_models_needing_responses_api(model):
    assert needs_responses_api("github_copilot", model)


@pytest.mark.parametrize(
    "model",
    ["gpt-5", "gpt-5-mini", "gpt-5.3", "gpt-4o", "gpt-4.1", "claude-sonnet-4.6"],
)
def test_copilot_models_staying_on_chat(model):
    assert not needs_responses_api("github_copilot", model)


def test_responses_api_only_for_copilot():
    # The same gpt-5.4 string on another provider is unaffected.
    assert not needs_responses_api("openai", "gpt-5.4")
    assert not needs_responses_api("openrouter", "gpt-5.1-codex")
