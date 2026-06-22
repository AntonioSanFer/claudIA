"""Tests for the model catalog (live fetch + preloaded fallback)."""

import urllib.error

import pytest

from claudia import catalog
from claudia.providers import get_provider


def test_resolve_url_plain():
    p = get_provider("openrouter")
    assert catalog.resolve_models_url(p, None) == "https://openrouter.ai/api/v1/models"


def test_resolve_url_with_api_base_placeholder():
    p = get_provider("ollama")
    assert catalog.resolve_models_url(p, "http://host:11434/") == "http://host:11434/api/tags"


def test_resolve_url_uses_default_api_base():
    p = get_provider("ollama")  # default_api_base http://localhost:11434
    assert catalog.resolve_models_url(p, None) == "http://localhost:11434/api/tags"


def test_resolve_url_none_when_no_endpoint():
    p = get_provider("azure")
    assert catalog.resolve_models_url(p, "https://x") is None


def test_parse_openai_shape():
    payload = {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}, {"nope": 1}]}
    assert catalog._parse("openai", payload) == ["gpt-4o", "gpt-4o-mini"]


def test_parse_openai_bare_list():
    payload = [{"id": "b"}, {"id": "a"}]
    assert catalog._parse("openai", payload) == ["a", "b"]  # sorted, deduped


def test_parse_ollama_shape():
    payload = {"models": [{"name": "llama3.1:latest"}, {"name": "qwen2.5-coder"}]}
    assert catalog._parse("ollama", payload) == ["llama3.1:latest", "qwen2.5-coder"]


def test_list_models_live(monkeypatch):
    p = get_provider("openrouter")
    monkeypatch.setattr(catalog, "fetch_live", lambda *a, **k: ["x/y", "a/b"])
    result = catalog.list_models(p, api_key="sk-x")
    assert result.is_live
    assert result.models == ["x/y", "a/b"]
    assert result.error is None


def test_list_models_falls_back_on_network_error(monkeypatch):
    p = get_provider("openrouter")

    def boom(*a, **k):
        raise urllib.error.URLError("no internet")

    monkeypatch.setattr(catalog, "fetch_live", boom)
    result = catalog.list_models(p, api_key="sk-x")
    assert not result.is_live
    assert result.source == "preloaded"
    assert result.models == list(p.suggested_models)
    assert "network error" in result.error


def test_list_models_offline_uses_preloaded(monkeypatch):
    p = get_provider("deepseek")
    called = False

    def spy(*a, **k):
        nonlocal called
        called = True
        return ["should-not-be-used"]

    monkeypatch.setattr(catalog, "fetch_live", spy)
    result = catalog.list_models(p, allow_network=False)
    assert not called
    assert result.models == list(p.suggested_models)


def test_list_models_no_endpoint_provider():
    p = get_provider("github_copilot")  # models_url=None
    result = catalog.list_models(p, api_key="x")
    assert not result.is_live
    assert result.models == list(p.suggested_models)
    assert "no live endpoint" in result.error


def test_fetch_live_raises_without_endpoint():
    p = get_provider("azure")
    with pytest.raises(ValueError):
        catalog.fetch_live(p, api_key="x", api_base="https://x")
