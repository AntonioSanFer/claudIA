# ClaudIA — Provider Bridge for Claude Code

> A terminal UI that lets you run **Claude Code** against **any LLM provider**
> (DeepSeek, OpenRouter, GitHub Copilot, Groq, Ollama, …) by transparently
> proxying Claude Code's Anthropic API calls through a local **LiteLLM** proxy.

This document is the source-of-truth specification for the project. It is written
for both humans and coding agents working in this repo. Keep it updated as the
design evolves.

---

## 1. What it does (in one breath)

1. You launch `claudia` (the TUI).
2. It verifies LiteLLM is installed, auto-installing it if not.
3. You pick a provider + model and supply credentials (saved for next time).
4. It writes a LiteLLM config (default to user/.claudIA/.config), starts a **local LiteLLM proxy** that speaks the
   Anthropic Messages API and translates to the chosen provider.
5. It launches your **already-installed** `claude` binary with
   `ANTHROPIC_BASE_URL` pointed at that proxy.
6. You use Claude Code as normal — but the model behind it is whatever you chose.
7. On exit, the proxy is shut down and the environment is left clean.

ClaudIA is a **launcher / orchestrator**. It writes no model code of its own and
ships no inference — it wires together two existing tools (Claude Code + LiteLLM).

---

## 2. Goals & non-goals

### Goals
- Zero-config-by-default: sensible defaults, remember the last working setup.
- Bring-your-own-`claude`: never bundle or reinstall Claude Code; use the user's
  installed version/command as-is.
- Auto-manage LiteLLM: detect, install, configure, run, and tear it down.
- Multi-provider: a curated, extensible list of OpenAI-compatible providers.
- Cross-platform: Windows, macOS, Linux.
- Safe with secrets: never print or commit API keys.

### Non-goals
- Not a replacement for Claude Code; it only launches it.
- Not a general LiteLLM admin UI; it exposes only what's needed to bridge.
- Does not try to reproduce Anthropic-only features on models that lack them
  (extended thinking, server tools, fine-grained streaming) — those degrade
  gracefully via LiteLLM's translation layer (see §11 Limitations).
- Does not host or relay traffic anywhere off the local machine; the proxy binds
  to loopback only.

---

## 3. Architecture

```
┌──────────────┐    Anthropic /v1/messages     ┌──────────────────┐   OpenAI-format    ┌──────────────┐
│  Claude Code │ ───────────────────────────▶ │  LiteLLM proxy    │ ─────────────────▶ │  Provider     │
│  (`claude`)  │   ANTHROPIC_BASE_URL=         │  (localhost:PORT) │  /chat/completions │  DeepSeek /    │
│              │ ◀─────────────────────────── │  Anthropic⇄OpenAI │ ◀───────────────── │  OpenRouter /  │
└──────────────┘     Anthropic-format reply    │  translation      │                    │  Copilot / …  │
        ▲                                       └──────────────────┘                    └──────────────┘
        │ spawns with env vars                          ▲
        │                                                │ config.yaml + start/stop
┌───────┴────────────────────────────────────────────────┴─────────┐
│                         ClaudIA TUI                                │
│  preflight checks · provider picker · config gen · process mgmt   │
└───────────────────────────────────────────────────────────────────┘
```

### Request flow
1. Claude Code issues `POST /v1/messages` (and `/v1/messages/count_tokens`) to
   `ANTHROPIC_BASE_URL`.
2. LiteLLM's Anthropic endpoint matches the request body's `model` against its
   `model_list` aliases and routes to the mapped provider model, translating the
   request and the response (including streaming SSE) between Anthropic and
   OpenAI shapes.
3. Claude Code receives a normal Anthropic-shaped response and never knows the
   difference.

The whole trick is: **Claude Code talks Anthropic; LiteLLM answers Anthropic but
forwards OpenAI.**

---

## 4. Tech stack

| Concern        | Choice                          | Why |
|----------------|---------------------------------|-----|
| Language       | **Python 3.10+**                | LiteLLM is a Python package; auto-install and process control are trivial in-process. |
| TUI            | **Textual** (Rich under the hood) | Real interactive TUI: lists, forms, key handling. (Alt: `prompt_toolkit` for a lighter footprint.) |
| Proxy          | **LiteLLM** (`litellm[proxy]`)  | Provides the Anthropic-compatible `/v1/messages` endpoint + 100+ providers. |
| Process mgmt   | `subprocess` / `asyncio`        | Spawn the proxy in the background and `claude` in the foreground. |
| Config / state | TOML or JSON in a per-user dir  | Remember providers, models, ports, last selection. |
| Secrets        | OS keyring (`keyring`) preferred, file fallback (0600) | Never store keys in plaintext if avoidable. |
| Packaging      | `pipx`-installable console script `claudia` | Isolated install of the tool itself. |

---

## 5. Components

### 5.1 Preflight (`claudia/preflight.py`)
Runs on every launch, before the UI is usable.
- **`claude` present?** Resolve the launch command (see §8). If missing, show a
  clear error with install guidance and exit — ClaudIA never installs Claude Code.
- **LiteLLM present?** Detect, then auto-install if needed (see §7).
- **Port availability** for the proxy (see §6).

### 5.2 Provider registry (`claudia/providers.py`)
A declarative table of supported providers (see §9). Each entry declares: id,
display name, LiteLLM `model:` prefix, auth style (api-key / oauth / none),
optional `api_base`, and a small set of suggested models. Adding a provider =
adding one table row; no code changes elsewhere.

### 5.3 Config generator (`claudia/litellm_config.py`)
Takes the user's selection and emits a LiteLLM `config.yaml` (see §6). Maps
**every** model tier Claude Code may request to the chosen provider model(s).

The `model_name` aliases are **recognised Anthropic model ids**
(`claude-sonnet-4-6`, `claude-haiku-4-5-20251001`), not custom labels such as
`claudia-main` / `claudia-small`. Claude Code's agents view validates the model
id against the known Anthropic catalogue *before* the request reaches the proxy
and rejects anything it doesn't recognise — a custom name breaks subagents even
though the interactive session would accept it. The alias is only a client-side
label; LiteLLM still routes it (and the `"*"` catch-all) to the provider model
the user actually selected. Bump these ids when Anthropic ships newer tiers.

### 5.4 Proxy manager (`claudia/proxy.py`)
- Start: `litellm --config <generated> --port <port> --host 127.0.0.1`
  (or start the proxy in-process via LiteLLM's ASGI app for tighter control).
- Health-gate: poll `GET /health/liveliness` until ready before launching Claude.
- Stop: terminate on Claude Code exit, on TUI quit, and on signal; ensure no
  orphaned process is left listening.

### 5.5 Claude launcher (`claudia/launcher.py`)
Builds the environment (see §8) and runs the resolved `claude` command in the
foreground, inheriting the terminal, until the user exits Claude Code.

### 5.6 TUI app (`claudia/app.py`)
Screens described in §10. Orchestrates the components above.

---

## 6. LiteLLM integration

### Endpoint
LiteLLM proxy exposes an **Anthropic-format** endpoint at `/v1/messages` (plus
`/v1/messages/count_tokens`). Pointing `ANTHROPIC_BASE_URL` at the proxy root is
all Claude Code needs.

### Port selection
Default `4000`; if busy, scan for the next free loopback port and record it.
Bind to `127.0.0.1` only — the proxy must never be reachable off-host.

### Generated `config.yaml` (representative)
```yaml
model_list:
  # Main model — what ANTHROPIC_MODEL points Claude Code at.
  - model_name: claude-sonnet-4-6
    litellm_params:
      model: openrouter/deepseek/deepseek-chat
      api_key: os.environ/CLAUDIA_PROVIDER_KEY
      # api_base: os.environ/CLAUDIA_PROVIDER_BASE   # when provider needs it

  # Small/fast model — Claude Code's background tasks (titles, git, etc.).
  - model_name: claude-haiku-4-5-20251001
    litellm_params:
      model: openrouter/deepseek/deepseek-chat
      api_key: os.environ/CLAUDIA_PROVIDER_KEY

litellm_settings:
  drop_params: true          # silently drop Anthropic params the target can't take
  telemetry: false           # opt out of LiteLLM anonymous usage telemetry
  # num_retries, request_timeout, etc. as needed

general_settings:
  master_key: os.environ/CLAUDIA_MASTER_KEY   # the token Claude Code must present
```

> **Critical:** Claude Code requests more than one model. At minimum map the
> **main** model and the **small/fast** model. If a tier is unmapped, those
> requests 404 at the proxy and Claude Code surfaces confusing errors. A
> catch-all (`model_name: "*"`) is an acceptable convenience but explicit
> aliases are clearer and safer.

> **Note:** the `model_name` values above are recognised Anthropic model ids, not
> custom labels like `claudia-main`. Claude Code rejects unrecognised ids before
> the call reaches the proxy, which would break subagents — see §5.3 for the full
> rationale.

The provider API key and the proxy master key are passed to the LiteLLM process
via environment variables (`os.environ/...` references in the YAML), **never
written into the YAML file**, so the config on disk contains no secrets.

### Responses-API routing (GitHub Copilot gpt-5.4+/codex)

Claude Code always sends `tools` together with `reasoning_effort`. GitHub
Copilot **rejects that combination on `/v1/chat/completions`** for `gpt-5.4` and
newer (and for the `*-codex` models), returning:

```
Function tools with reasoning_effort are not supported for gpt-5.4 in
/v1/chat/completions. Please use /v1/responses instead.
```

For those models the generator emits `model_info: { mode: responses }` on the
entry so LiteLLM routes the request to `/v1/responses`. This keeps the reasoning
slider working instead of having `drop_params` silently strip `reasoning_effort`.

```yaml
  - model_name: claude-sonnet-4-6
    model_info:
      mode: responses          # -> /v1/responses instead of /chat/completions
    litellm_params:
      model: github_copilot/gpt-5.4
```

The decision is made **per tier** by `providers.needs_responses_api(provider_id,
model_id)` — only `github_copilot` is affected, so a chat-only small model (e.g.
`gpt-4.1`) stays on `/chat/completions` while main goes through Responses. The
catch-all (`"*"`) inherits the main model's mode since it maps to main.

---

## 7. LiteLLM auto-install (startup check)

On launch, after confirming `claude` exists:

1. **Detect**: try `shutil.which("litellm")` and/or import `litellm`; verify the
   proxy extra is present (the `[proxy]` server deps).
2. **If missing**: prompt the user (default = yes) and install. Preference order:
   - `uv pip install 'litellm[proxy]'` if `uv` is available (fast), else
   - `pip install 'litellm[proxy]'` into ClaudIA's own environment, or
   - a dedicated managed venv under the user data dir to avoid polluting global
     site-packages.
3. **Verify**: re-check after install; on failure, show the captured stderr and a
   manual install command, then exit non-zero.
4. **Pin/record** the installed version in state for reproducibility.

Auto-install must be explicit (visible progress, confirmable) and idempotent —
re-running ClaudIA when LiteLLM is already present does nothing.

---

## 8. Launching the user's Claude Code

### Resolving the command
Use the user's installed Claude Code; do not bundle or upgrade it. Resolution
order:
1. An explicit override in ClaudIA config (`claude_command`).
2. `CLAUDE_CODE_PATH` / equivalent env override if set.
3. `claude` on `PATH` (`shutil.which`), accounting for Windows shims
   (`claude.cmd` / `claude.exe`).
If none resolve, error with guidance to install Claude Code (npm global, native
installer, etc.) — ClaudIA does not perform this install.

### Environment injected into the child `claude` process
| Variable | Value | Purpose |
|---|---|---|
| `ANTHROPIC_BASE_URL` | `http://127.0.0.1:<port>` | Route Claude Code through the proxy. |
| `ANTHROPIC_AUTH_TOKEN` | the LiteLLM master key | Sent as `Authorization: Bearer`; satisfies Claude Code's auth requirement and the proxy's. |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Main model alias (matches `model_list`). |
| `ANTHROPIC_SMALL_FAST_MODEL` | `claude-haiku-4-5-20251001` | Background/small-task model alias. |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` / `..._SONNET_MODEL` / `..._HAIKU_MODEL` | aliases | Map Claude Code's in-app `/model` tiers onto our aliases (set where supported by the installed version). |
| `DISABLE_TELEMETRY` / `DISABLE_ERROR_REPORTING` | `1` | Opt out of Claude Code's telemetry and Sentry error reporting — reports to Anthropic infra are meaningless when pointed at a third-party provider (`setdefault`, so a user override wins). |

Notes:
- The model aliases are **real Anthropic model ids, not custom names** (see §5.3).
  Claude Code validates the requested id against its known catalogue before the
  call reaches the proxy, so an invented label like `claudia-main` would be
  rejected by the agents view and break subagents; LiteLLM routes the recognised
  id to the provider model regardless.
- Prefer `ANTHROPIC_AUTH_TOKEN` (Bearer) over `ANTHROPIC_API_KEY` (`x-api-key`)
  for proxy auth; do not set both (Claude Code rejects having both).
- These are set **only for the spawned child**, never exported globally.
- `claude` runs in the foreground, inheriting stdin/stdout/tty, so the full
  Claude Code UX is preserved. ClaudIA waits for it to exit, then tears down.

---

## 9. Supported providers (initial set)

Extensible registry; ship a useful core and grow it. Each maps to a LiteLLM
provider prefix.

| Provider | LiteLLM prefix | Auth | Notes |
|---|---|---|---|
| DeepSeek | `deepseek/` | API key | Direct DeepSeek API. |
| OpenRouter | `openrouter/` | API key | Gateway to many models; single key. |
| GitHub Copilot | `github_copilot/` | OAuth token | Uses Copilot subscription auth, not a plain key. gpt-5.4+/codex are routed to the Responses API (§6). |
| Groq | `groq/` | API key | Fast inference. |
| Together / Fireworks | `together_ai/`, `fireworks_ai/` | API key | Open models. |
| Mistral | `mistral/` | API key | |
| OpenAI / Azure OpenAI | `openai/`, `azure/` | API key (+ base for Azure) | |
| Ollama (local) | `ollama/` | none | Local models; `api_base` to the Ollama server. |
| Custom OpenAI-compatible | `openai/` + `api_base` | API key | Any compatible endpoint. |

Per-provider the registry records: auth style, whether an `api_base` is required,
and a few suggested model ids to pre-populate the picker.

---

## 10. TUI flow

1. **Splash / preflight** — runs §5.1 checks with live status; offers LiteLLM
   auto-install if needed; blocks until green or exits with guidance.
2. **Provider picker** — list of providers from the registry; remembers last used.
3. **Credential entry** — masked API-key field (or "use saved", or OAuth flow for
   Copilot, or `api_base` for Ollama/custom). Option to save to keyring.
4. **Model selection** — choose main model from a **live model list** fetched
   from the provider's OpenAI-compatible `/models` endpoint (Ollama: `/api/tags`),
   with type-to-filter; falls back to a preloaded suggested list when offline or
   unsupported. Free-text entry is always allowed. Optionally choose a distinct
   small/fast model (default: reuse main). See `claudia/catalog.py`.
5. **Launch** — generate config → start proxy → health-gate → spawn `claude`.
   Show the effective mapping ("Claude Code → claude-sonnet-4-6 → openrouter/…").
6. **Running** — hand the terminal to Claude Code. On its exit, return to a
   summary screen (relaunch / change provider / quit).

Keep navigation keyboard-first; every blocking step shows clear status and errors.

---

## 11. Limitations (be upfront in the UI)

- **Anthropic-only features degrade.** Extended/adaptive thinking, server tools
  (web search/fetch, code execution), citations, prompt-cache semantics, and
  fine-grained streaming are Anthropic features; on third-party models they are
  dropped or emulated by LiteLLM (`drop_params: true`). Tool use generally works
  via OpenAI function-calling translation; quality varies by model.
- **Model aliasing must be complete.** Any model tier Claude Code requests that
  isn't in `model_list` fails — keep main + small mapped at minimum.
- **Context windows & token counts differ** from real Claude models; Claude
  Code's token/cost estimates are approximate against a proxied model.
- **Provider quirks**: rate limits, tool-call fidelity, and streaming behavior are
  the provider's, not Anthropic's.

---

## 12. Security

- Proxy binds to `127.0.0.1` only; never `0.0.0.0`.
- API keys: keyring first; file fallback must be `0600` and excluded from VCS.
- Secrets are passed to the LiteLLM subprocess via env vars, not written into
  `config.yaml`.
- Never log full keys (mask to last 4). Never print the master key.
- Generated config and any temp files live under the user data dir, not the repo.

---

## 13. Configuration & state

Per-user data dir (`platformdirs`):
- `config.toml` — providers seen, last selection, `claude_command` override,
  preferred port, LiteLLM version pin.
- secrets in OS keyring (or `secrets.json`, `0600`, as fallback).
- runtime: generated `config.yaml`, proxy logs (rotated), pid/port of a live
  proxy for clean shutdown.

---

## 14. Edge cases to handle

- `claude` not found → actionable error, no install attempt.
- LiteLLM install fails (no network, no pip) → show stderr + manual command.
- Chosen port busy → auto-pick next free port; update env accordingly.
- Proxy starts but never becomes healthy → timeout, surface proxy logs, abort.
- Provider auth invalid → first request 401/403 surfaces inside Claude Code;
  offer a "test connection" before launch to catch it early.
- Claude Code exits non-zero / crashes → still tear down the proxy.
- Ctrl-C / SIGTERM at any stage → guaranteed proxy cleanup (no orphans).
- Windows: resolve `claude.cmd`/`claude.exe`; pass env correctly to the shim.

---

## 15. Repository layout (proposed)

```
claudIA/
├── AGENTS.md                 # this spec
├── README.md                 # user-facing quickstart
├── pyproject.toml            # console_script: claudia = claudia.app:main
├── claudia/
│   ├── app.py                # TUI entry / orchestration
│   ├── preflight.py          # claude + litellm + port checks
│   ├── providers.py          # provider registry
│   ├── litellm_config.py     # config.yaml generation
│   ├── proxy.py              # start/health/stop LiteLLM
│   ├── launcher.py           # env build + spawn claude
│   ├── secrets.py            # keyring / file fallback
│   └── state.py              # config + runtime state
└── tests/
    ├── test_litellm_config.py
    ├── test_providers.py
    └── test_preflight.py
```

---

## 16. Milestones

- **M1 — CLI MVP (no TUI):** flags → generate config → start proxy → launch
  `claude` → clean teardown, for one provider (OpenRouter). Proves the bridge.
- **M2 — Preflight + auto-install:** detect/install LiteLLM, resolve `claude`,
  port handling.
- **M3 — TUI:** provider/model/credential screens, saved state, keyring.
- **M4 — Provider breadth:** registry of all §9 providers + custom endpoint;
  "test connection".
- **M5 — Polish:** model-tier mapping for `/model`, logs view, cross-platform
  hardening, docs. **(done)** — plus a live/preloaded model catalog
  (`claudia/catalog.py`), `claudia logs`, and `claudia run --list-models`.

### Status
M1, M2, M3, and M5 are implemented and verified (proxy lifecycle smoke-tested,
TUI driven via Textual's pilot, 51 unit tests). M4 (provider-breadth
"test connection") is the main remaining item.

---

## 17. Guidance for agents working in this repo

- This is an **orchestrator**, not an Anthropic SDK app — there is no
  Anthropic/OpenAI client code to write here. The "API" surface is env vars and a
  generated LiteLLM YAML.
- Never bundle, install, or modify the user's Claude Code; only resolve and spawn
  it.
- Treat LiteLLM as an external process with a stable contract (Anthropic
  `/v1/messages` in, provider out). Validate against the LiteLLM proxy docs before
  relying on endpoint/flag specifics; pin a known-good version.
- Keep secrets out of disk config and logs.
- When adding a provider, add a registry row + a config-generation test; avoid
  special-casing elsewhere.
- Verify behavior end-to-end by actually launching the proxy and a real `claude`
  session against a cheap model, not just unit tests.
