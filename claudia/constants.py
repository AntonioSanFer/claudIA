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
#
# They MUST be real Anthropic model identifiers, not invented names: Claude
# Code's agents view (and other surfaces) validate the model id against the
# known Anthropic catalogue *before* hitting the proxy, and reject anything it
# doesn't recognise ("It may not exist or you may not have access to it"). The
# interactive session trusts ANTHROPIC_MODEL, but the agents view does not — so
# a name like "claudia-main" breaks subagents. We expose recognised ids here and
# let LiteLLM route them (and the "*" catch-all) to the chosen provider model.
# Bump these when Anthropic ships newer tier ids.
MAIN_ALIAS = "claude-sonnet-4-6"
SMALL_ALIAS = "claude-haiku-4-5-20251001"

# --- Proxy defaults ---------------------------------------------------------
DEFAULT_PORT = 4000
LOOPBACK_HOST = "127.0.0.1"
HEALTH_PATH = "/health/liveliness"

# Application name used for per-user config/data directories.
APP_NAME = "claudIA"
