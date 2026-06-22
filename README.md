# ClaudIA — Provider Bridge for Claude Code

Run **Claude Code** against **any LLM provider** (DeepSeek, OpenRouter, Groq,
Ollama, …) by transparently proxying Claude Code's Anthropic API calls through a
local [LiteLLM](https://github.com/BerriAI/litellm) proxy.

ClaudIA is a **launcher/orchestrator**: it writes no model code and ships no
inference. It wires together two existing tools — your installed `claude` binary
and a LiteLLM proxy — and tears everything down cleanly on exit.

See [AGENTS.md](AGENTS.md) for the full specification.

```
Claude Code ──Anthropic /v1/messages──▶ LiteLLM proxy ──OpenAI /chat/completions──▶ Provider
 (`claude`)  ◀──Anthropic-format reply── (127.0.0.1)   ◀──────────────────────────  (DeepSeek/…)
```

## Status

Implemented through **M5** of the roadmap:

- **M1 — CLI bridge:** `claudia run` generates a LiteLLM config, starts the proxy,
  launches `claude` against it, and tears down cleanly.
- **M2 — Preflight + auto-install:** resolves your `claude` command, detects and
  (optionally) auto-installs `litellm[proxy]`, and picks a free loopback port.
- **M3 — TUI:** an interactive wizard for provider / credentials / model, with
  saved state and OS-keyring secret storage.
- **M5 — Polish:** live/preloaded model list, `/model` tier mapping, a `logs`
  view, and cross-platform hardening (Windows process-tree teardown, UTF-8 proxy
  I/O, robust port detection).

(M4 — provider-breadth "test connection" — is the main remaining item.)

## Install

```bash
pipx install .        # or: pip install .
```

ClaudIA does **not** bundle or install Claude Code — install that yourself
(e.g. `npm install -g @anthropic-ai/claude-code`). LiteLLM's proxy extra is
auto-installed on first run if missing.

## Usage

### Interactive TUI

```bash
claudia
```

Walks you through preflight → provider → credentials → model, then launches
Claude Code. On exit it offers to relaunch.

The preflight screen is keyboard-navigable: **←/→** (or Tab) move between
actions, **Enter** activates the focused one, and **c**/**s**/**i**/**q** are
direct shortcuts (Continue / Skip-next-time / Install / Quit). Choosing **Skip
next time** records a preference (`skip_preflight_when_ok` in `config.json`) so
future launches run the checks silently and only show this screen if a problem is
found (no `claude`, or LiteLLM missing).

### Headless CLI

```bash
# OpenRouter, key from $CLAUDIA_PROVIDER_KEY or a saved key
claudia run --provider openrouter --model deepseek/deepseek-chat

# Supply and save a key
claudia run --provider groq --model llama-3.3-70b-versatile \
            --api-key sk-... --save-key

# Local Ollama (no key)
claudia run --provider ollama --model qwen2.5-coder --api-base http://localhost:11434

# Inspect the generated config without starting anything
claudia run --print-config --provider deepseek --model deepseek-chat

# List supported providers
claudia run --list-providers

# List a provider's models — live from its API, or preloaded if offline
claudia run --list-models --provider openrouter
claudia run --list-models --provider deepseek --offline

# Tail the proxy log from the last run
claudia logs 100

# Forward args to claude after `--`
claudia run --provider openrouter --model deepseek/deepseek-chat -- --help
```

Key flags: `--small-model`, `--port`, `--no-catch-all`, `--no-install`.

## Model list

The picker shows a **live model list** pulled from the provider's own
OpenAI-compatible `/models` endpoint (Ollama uses `/api/tags`), so you see
exactly what your key can reach. When there's no network, no endpoint for that
provider (Azure, Copilot), or the request fails, it falls back to a **preloaded**
set of suggested models. You can always type any model id by hand — the list is a
convenience, not a constraint. In the TUI, type in the *Main model* field to
filter the list live; in the CLI use `claudia run --list-models` (add `--offline`
to force the preloaded set).

## How it works

1. You pick a provider + model and supply credentials (remembered for next time).
2. ClaudIA writes a LiteLLM `config.yaml` (under your per-user data dir) that maps
   `claudia-main` / `claudia-small` (and a `"*"` catch-all) onto the provider model.
   **No secrets are written to disk** — the provider key and proxy master key are
   passed to the LiteLLM process via environment variables (`os.environ/...`
   references in the YAML).
3. It starts a LiteLLM proxy bound to `127.0.0.1` only, health-gates it on
   `/health/liveliness`, then spawns your `claude` with `ANTHROPIC_BASE_URL`
   pointed at the proxy and `ANTHROPIC_AUTH_TOKEN` set to the master key.
4. Claude Code runs normally in the foreground; on exit the proxy is terminated
   (whole process tree, no orphans) and the environment is left clean.

## Security

- The proxy binds to loopback only — never reachable off-host.
- API keys are stored in the OS keyring when available, else a `0600` file.
- Secrets are passed via env vars, never written into `config.yaml` or logs
  (keys are masked to the last 4 chars).

## Limitations

Anthropic-only features (extended thinking, server tools, citations, prompt
caching, fine-grained streaming) degrade or are dropped by LiteLLM on third-party
models (`drop_params: true`). Token/cost estimates are approximate against a
proxied model. See [AGENTS.md §11](AGENTS.md).

## Development

```bash
pip install -e '.[dev]'
pytest
```

Adding a provider = one row in `claudia/providers.py` + a config-generation test.

## Layout

```
claudia/
  app.py             # entry point + Textual TUI
  cli.py             # headless `claudia run`
  bridge.py          # config -> proxy -> claude -> teardown orchestration
  preflight.py       # claude resolution, LiteLLM detect/install, port pick
  providers.py       # provider registry (incl. model-list endpoints)
  catalog.py         # live model fetch + preloaded fallback
  litellm_config.py  # config.yaml generation
  proxy.py           # LiteLLM proxy lifecycle
  launcher.py        # env build + spawn claude
  secrets.py         # keyring / 0600 file fallback
  state.py           # persisted config + runtime state
  paths.py           # per-user dirs (platformdirs + fallback)
  constants.py       # shared env-var / alias names
```
