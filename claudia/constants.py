"""Shared constants: env var names and model aliases.

Centralised so the YAML generator, the proxy manager, and the Claude launcher
all agree on the same names. Changing a name here changes it everywhere.
"""

from __future__ import annotations

# --- Env vars that carry secrets into the LiteLLM subprocess ----------------
# The generated config.yaml references these as `os.environ/<NAME>` so that no
# secret is ever written to disk (see AGENTS.md §6, §12).
PROVIDER_KEY_ENV = "CLAUDIA_PROVIDER_KEY"
PROVIDER_BASE_ENV = "CLAUDIA_PROVIDER_BASE"
MASTER_KEY_ENV = "CLAUDIA_MASTER_KEY"

# --- Model aliases exposed to Claude Code -----------------------------------
# These are the `model_name` entries in the generated model_list and the values
# Claude Code is pointed at via ANTHROPIC_MODEL / ANTHROPIC_SMALL_FAST_MODEL.
MAIN_ALIAS = "claudia-main"
SMALL_ALIAS = "claudia-small"

# --- Proxy defaults ---------------------------------------------------------
DEFAULT_PORT = 4000
LOOPBACK_HOST = "127.0.0.1"
HEALTH_PATH = "/health/liveliness"

# Application name used for per-user config/data directories.
APP_NAME = "claudIA"
