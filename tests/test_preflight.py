"""Tests for preflight: claude resolution and port selection."""

import socket

import pytest

from claudia import preflight


def test_find_free_port_returns_preferred_when_free():
    # Grab an ephemeral port, release it, then ask preflight for it.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert preflight.find_free_port(port) == port


def test_find_free_port_skips_busy_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    busy = s.getsockname()[1]
    try:
        chosen = preflight.find_free_port(busy)
        assert chosen != busy
        assert preflight.is_port_free(chosen)
    finally:
        s.close()


def test_is_port_free_true_for_unbound():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert preflight.is_port_free(port)


def test_resolve_claude_uses_config_override_path(tmp_path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\n")
    resolved = preflight.resolve_claude(str(fake))
    assert resolved == str(fake)


def test_resolve_claude_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "claude.exe"
    fake.write_text("")
    monkeypatch.setenv("CLAUDE_CODE_PATH", str(fake))
    assert preflight.resolve_claude(None) == str(fake)


def test_resolve_claude_none_when_absent(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_PATH", raising=False)
    monkeypatch.setattr(preflight.shutil, "which", lambda *_: None)
    assert preflight.resolve_claude("nonexistent-cmd-xyz") is None


def test_check_litellm_shape():
    status = preflight.check_litellm()
    # Fields are present and typed; readiness depends on the environment.
    assert isinstance(status.importable, bool)
    assert isinstance(status.has_proxy_extra, bool)
    assert status.ready == (status.importable and status.has_proxy_extra)


def test_install_command_prefers_uv(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    cmd = preflight._install_command()
    assert cmd[0] == "uv"
    assert "litellm[proxy]" in cmd
    # Must target this interpreter so it works under pipx (no active venv).
    assert "--python" in cmd
    assert cmd[cmd.index("--python") + 1] == preflight.sys.executable


def test_install_command_falls_back_to_pip(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    cmd = preflight._install_command()
    assert cmd[1:] == ["-m", "pip", "install", "litellm[proxy]"]
