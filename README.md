# OctoOps

Modular async bot runtime. **Telegram** is the control plane (operators send
commands, receive responses); **WhatsApp** is primarily an output transport (the
bot pushes messages via the Whatsmeow Go bridge), with an optional, opt-in inbound
path that routes whitelisted senders to a **single configured command**. Commands
can be **multi-step conversations** (menus, guided flows), and user-facing text is
**localizable** (English / Brazilian Portuguese). Business logic lives only in
drop-in modules under `octoops/modules/`; the core is small and domain-neutral.

## Quick start

OctoOps is managed with [uv](https://docs.astral.sh/uv/) — it provisions Python
and all dependencies from the lockfile, so no system Python is required.

```bash
./setup.sh        # Linux/macOS   (installs uv, uv sync, fetch bridge, --check)
./setup.ps1       # Windows (PowerShell)
```

Then:

```bash
uv run python -m octoops --setup    # configure (Textual wizard)
uv run python -m octoops            # start
uv run python -m octoops --check    # diagnostics, no start
uv run python -m octoops --check --verify-token   # also validate the token live
```

- No `config.toml` (or `--setup`) → setup wizard, then start.
- `--check` → validate Python/deps/config/bridge/ports/timezone/brain and exit.
- Otherwise → load config, bootstrap, run.

## Modules

The bot does nothing on its own — features are **modules** (a folder with a
`plugin.json` and a `load(ctx) -> ModuleRegistration`). The wizard discovers them
automatically and renders each module's declared config fields; enable them in
`config.toml [modules] enabled` and restart (no hot-reload). See
`MODULE_BUILD_PROMPT.md` for the contract and `BRAINSTORM.md` for the onboarding
flow and ideas.

Shipped modules:

- **`status`** — `/status`: uptime, loaded modules, your role (reference module).
- **`help`** — `/help` (and `/ajuda`): lists the commands you can run, grouped by
  module and **filtered by your role**, with localized framing.
- **`access`** — manage the whitelist from Telegram: `/whoami`, `/users`,
  `/grant`, `/revoke`, plus one-time `/invite` links for new users.
- **`brain`** — `/ask <question>`: an embedded AI assistant grounded in a folder
  of context prompts. **Provider-agnostic** via an OpenAI-compatible endpoint —
  one key reaches OpenRouter, Google Gemini, Groq, a local Ollama, and free-tier
  models; switch provider/model in `[modules.brain]`. Optionally reachable by
  whitelisted WhatsApp numbers (see *Configuration*).
- **`deadlines`** — `/deadlines` (or `/vencimentos` in pt-BR): an **interactive**
  deadline tracker. A menu offers nearest deadlines, all deadlines, add, and edit
  (with delete); the add/edit flows ask one field at a time. Works on Telegram and
  WhatsApp; records persist to a JSON file. Reference example of a multi-step,
  localized module.

## Configuration & secrets

Settings live in `config.toml` (written by the wizard, `0600`). Each module reads
its own `[modules.<name>]` section via `ctx.config`.

**Secrets never go in `config.toml`.** A module `Password` field (e.g. the brain's
API key) is written to a private `.env` sidecar (`0600`, beside `config.toml`),
loaded into the environment at startup, and scrubbed from logs. Read it from an
env var named `{MODULE}_{KEY}` — the brain reads **`BRAIN_API_KEY`**. You can set
that env var directly instead of using the wizard.

WhatsApp inbound (off by default — WhatsApp stays output-only) is configured under
`[transport]`: `whatsapp_inbound_enabled`, a phone-number `whatsapp_allow` list, a
role, and `whatsapp_command` — the **one** command every inbound message is routed
to. Whitelisted senders can only ever reach that single command (e.g. the brain's
`/ask`, or the deadlines flow `/vencimentos`), never the rest of the bot. The bot's
startup message on WhatsApp states which command is reachable (and warns if
`whatsapp_command` doesn't match a registered command).

The interface language is set in the wizard and persisted as `[core] language`
(`en` / `pt-BR`); modules localize their command names and replies from it.

## Portability

All runtime paths resolve against a single **base directory** (`OCTOOPS_HOME`,
else the directory of `config.toml`, else CWD), so the install is relocatable and
Task Scheduler's "Start in" isn't load-bearing. `config.toml`, `.env`, `logs/`,
`data/`, the bridge binary, and an optional external `modules/` drop-in folder all
live under that base. Built-in modules ship inside the package; operators can add
more by dropping a folder into `$OCTOOPS_HOME/modules/` and restarting — no rebuild.

The Whatsmeow bridge binary is fetched by `setup` from `OCTOOPS_BRIDGE_URL` (or
placed manually); a missing bridge only disables WhatsApp, nothing else.

## Build status

Core framework (complete, reviewed):

- **Core:** contracts, registry, router, event bus, scheduler (APScheduler 3.x),
  plugin loader, bootstrap, permissions, a per-user conversation store (multi-step
  flows), structured secret-scrubbing logging.
- **Transports:** Telegram (python-telegram-bot, supervised lifecycle; routes
  follow-up replies to open conversations), WhatsApp (supervised bridge sidecar;
  output + optional single-command inbound), response router.
- **Wizard:** Textual setup TUI — EN / pt-BR language picker (persisted to
  `[core] language`), dynamic per-module config from each module's `ConfigField`
  declarations, guided Telegram onboarding (token verify + chat-ID auto-detect),
  optional WhatsApp, secret `.env` writing, Windows Task Scheduler registration,
  non-destructive re-runs.
- **MCP server (optional `octoops[mcp]`):** exposes the module catalog + status as
  resources and opt-in commands as tools over Streamable HTTP (loopback, optional
  bearer token). Off by default; command execution is quadruple-gated.
- **Diagnostics:** `octoops --check` validates Python/deps/config/timezone/log dir/
  WhatsApp bridge & ports/brain key, with an opt-in live token check.

## Development

```bash
uv sync                       # provision Python + deps from uv.lock
uv run --extra mcp pytest     # full suite (309 tests; --extra mcp covers the MCP server)
```

`requirements*.txt` are kept for pip users, but `uv.lock` is the source of truth.
Copy `config.example.toml` to `config.toml` to run without the wizard.
