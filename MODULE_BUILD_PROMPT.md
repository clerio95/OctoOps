# OctoOps — Build-a-Module Prompt

> **Give this whole file to an AI as its instructions** when you want it to build a
> new OctoOps module. It is self-contained: an assistant that reads only this file
> can produce a **plug-and-play** module that loads cleanly the next time the bot
> restarts. Everything here is verified against the actual codebase — follow it
> exactly; do not invent APIs.

---

## 0. Your mission

Build **one** OctoOps module: a self-contained folder that the runtime discovers,
loads, and wires automatically at startup. You will:

1. Create the module folder + `plugin.json` + `__init__.py` exposing `load(ctx)`.
2. Register some mix of **commands / jobs / listeners / config fields / lifecycle
   hooks** by returning a `ModuleRegistration`.
3. Implement async handlers that take a `ModuleContext` and use only the services
   it exposes.
4. Write tests that match the repo's conventions.
5. Make sure the module's name is in `[modules] enabled` so it loads on restart.

You **must not** touch core, add dependencies, or import other modules. If your
idea needs something the contract below doesn't offer, **stop and say so** — it's a
core change, not a module.

---

## 1. Mental model (how OctoOps works)

```
Telegram (operators) ─▶ Router ─▶ your command handler ─▶ Response ─▶ Telegram reply
                          │                                      └▶ (optional) WhatsApp mirror
                          ▼
                     Permissions (role check: Viewer < Operator < Admin)

Scheduler ─(cron)─▶ your job handler            Event Bus ─▶ your listener
                                                    ▲
                                   any module publishes a business event
```

- **Telegram is the control plane.** Operators type `/command`; the Router checks
  their role, calls your handler, sends your `Response` back.
- **WhatsApp is output-only and optional.** Modules may *push* to WhatsApp, but it
  may be disabled — always check `transports.get("whatsapp")` for `None`.
- **The core is domain-neutral.** All business logic lives in modules. Modules
  never import each other — they communicate via the **event bus**.
- **Modules load once, at startup.** There is no hot-reload. Drop the folder in,
  enable it, **restart** — that's the plug-and-play loop.

---

## 2. Plug-and-play rules (read these first)

A module is loaded on the next restart **only if all of these hold**:

1. **Location** — one of:
   - built-in: `octoops/modules/<name>/` (inside the checkout), or
   - external drop-in: `$OCTOOPS_HOME/modules/<name>/` (next to `config.toml`; no
     checkout edit needed). A built-in of the same name wins over an external one.
2. **Folder name** does not start with `_` or `.`.
3. **`plugin.json` present** with a `name` (see §3). The `name` must be **unique**
   across all modules.
4. **Enabled in config** — the `name` is listed in `config.toml` `[modules] enabled`.
   The loader **skips any module not in that list** (logs `module.skipped`).
   - Either re-run `uv run python -m octoops --setup` (it auto-discovers modules and
     default-enables them; re-running is non-destructive and pre-fills your config),
     **or** add the name to `[modules] enabled` by hand.
5. **`load(ctx)` returns a `ModuleRegistration`** without raising. A module that
   fails to import or whose `load()` raises is **logged and skipped** — the rest of
   the bot still starts (`module.load_failed`).
6. **No duplicate command names.** Command names share one global namespace; a
   duplicate is **fatal at startup** (`RouterError`). Name your commands distinctly.

Then: **restart the bot.** Confirm with `/status` (it lists loaded modules) or the
startup log line `module.loaded module=<name> ...`.

---

## 3. The manifest — `plugin.json`

```json
{
  "name": "myfeature",
  "version": "1.0.0",
  "description": "One sentence shown in the setup wizard and logs."
}
```

- `name` is the identity used everywhere: the folder, `[modules] enabled`, the
  `ModuleRegistration(name=...)`, and `ctx.config` (it reads `[modules.<name>]`).
  Keep all four identical. Use a short, lowercase, unique slug.

---

## 4. The package — `__init__.py`

Every module exposes exactly one entry point:

```python
def load(ctx: ModuleContext) -> ModuleRegistration: ...
```

`load()` is called **once at startup** (and once by the wizard with a stub context,
to harvest your declared commands/config fields). It must do nothing but **declare**
— build and return a `ModuleRegistration`. Do not start tasks, open sockets, or do
I/O in `load()`; use `on_startup` for that.

### The contract types (exact signatures)

```python
from octoops.core.contracts import (
    CommandDef, JobDef, ListenerDef, ConfigField, ConfigFieldKind, ModuleRegistration,
)
from octoops.core.registry import ModuleContext
from octoops.shared.models import Request, Response, Role

@dataclass
class ModuleRegistration:
    name: str
    commands:   list[CommandDef]   = []   # operator-typed /commands
    listeners:  list[ListenerDef]  = []   # react to published events
    jobs:       list[JobDef]       = []   # cron-scheduled work
    config_fields: list[ConfigField] = [] # wizard-collected settings
    on_startup:  LifecycleHook | None = None   # async (ctx) -> None
    on_shutdown: LifecycleHook | None = None   # async (ctx) -> None

@dataclass
class CommandDef:
    name: str                 # matched case-insensitively, leading "/" stripped
    description: str
    min_role: Role            # Role.Viewer | Role.Operator | Role.Admin
    handler: CommandHandler   # async (Request, ModuleContext) -> Response
    ai_invokable: bool = False # opt-in to the MCP/AI surface; default OFF

@dataclass
class JobDef:
    name: str
    schedule: str             # 5-field crontab, e.g. "0 8 * * *" (configured tz)
    handler: JobHandler       # async (ModuleContext) -> None

@dataclass
class ListenerDef:
    event: str                # business event name, e.g. "inventory.low"
    handler: ListenerHandler  # async (payload, ModuleContext) -> None

@dataclass
class ConfigField:
    key: str
    label: str
    description: str
    required: bool
    default: str | None
    kind: ConfigFieldKind     # Text|Password|FilePath|IpAddress|Integer|Boolean
                              # Password -> .env / env var, NOT ctx.config (see §9 Secrets)
```

### Handler signatures (all `async`)

```python
async def my_command(request: Request, ctx: ModuleContext) -> Response: ...
async def my_job(ctx: ModuleContext) -> None: ...
async def my_listener(payload, ctx: ModuleContext) -> None: ...
async def my_startup(ctx: ModuleContext) -> None: ...   # also on_shutdown
```

`Request` = `command, args: list[str], raw_text, user_id, chat_id, source`.
`Response(text, chat_id, reply_to=None, mirror_to_whatsapp=False, whatsapp_chat_ids=[])`.
Always return `Response(text=..., chat_id=request.chat_id)` from a command.

---

## 5. What you get — the `ModuleContext` surface

Everything a handler/job/listener needs arrives in `ctx`. Use **only** these:

```python
# --- this module's own config (the [modules.<name>] section) ---
ctx.config.get("key", default=None)   # typed by the field's kind (int/bool/str)
ctx.config.require("key")             # raises ConfigError if missing/empty
ctx.config.as_dict()
#   NOTE: Password fields are NOT here — read them from os.environ (see §9 Secrets).

# --- talk to other modules (decoupled; never import them) ---
await ctx.event_bus.publish("something.happened", {"any": "dict"})
#   (you receive events by registering a ListenerDef in load())

# --- who is asking / authorization ---
ctx.registry.permissions.role_for(request.user_id)   # -> Role | None

# --- base-dir-relative paths (for reading your own data files) ---
ctx.registry.paths.resolve("data/myfeature/state.json")  # -> absolute Path
ctx.registry.paths.data                                   # $OCTOOPS_HOME/data/

# --- proactive push (jobs/listeners have no Response return path) ---
tg = ctx.registry.transports.get("telegram")
if tg is not None:
    await tg.send(Response(text="…", chat_id=some_chat_id))
wa = ctx.registry.transports.get("whatsapp")   # may be None (WhatsApp optional!)
if wa is not None:
    await wa.send(Response(text="…", chat_id="", whatsapp_chat_ids=["123@g.us"]))

# --- WhatsApp group discovery ---
# Populated automatically when the bridge connects and the bot is logged in.
# Each entry: {"jid": "...", "name": "...", "participants": N}.
# None = bridge not started or not logged in yet; [] = logged in, no groups.
groups = ctx.registry.whatsapp_groups  # list[dict] | None
# The same list is persisted at data/whatsapp_groups.json on every bridge reconnect
# so wizard tooling can read it even while the bridge isn't running.
# Typical module pattern: store the desired group JID in a config field (set once
# via --setup or hand-edit), then send to it at runtime:
#   group_jid = ctx.config.require("group_jid")
#   Response(text="…", chat_id="", whatsapp_chat_ids=[group_jid])

# --- introspection ---
ctx.registry.start_time          # datetime, tz-aware
ctx.registry.module_names        # list[str] of loaded modules
ctx.registry.config.core.timezone

# --- optional: structured logging (core infra, fine to use) ---
from octoops.core.logging import get_logger
log = get_logger("octoops.modules.myfeature")
```

`ctx.event_bus` and `ctx.scheduler` are also exposed directly on `ctx` (same
instances as on `ctx.registry`).

Handy formatters: `from octoops.shared.text import humanize_timedelta, humanize_duration`.

---

## 6. Lifecycle — when your code runs

| Phase | What fires | Notes |
|---|---|---|
| **build** (startup) | `load(ctx)`; commands registered; listeners subscribed | synchronous; declare only |
| **start** (startup) | jobs scheduled; **`on_startup(ctx)`** per module; scheduler starts | do async init here |
| **running** | command handlers (on `/cmd`), job handlers (on cron), listeners (on `publish`) | each isolated (see §7) |
| **stop** (shutdown) | **`on_shutdown(ctx)`** (reverse order); scheduler stops; event bus drains | clean up here |

A command flows through the **Router**: it resolves the caller's role, enforces
`min_role` (replies `⛔ not authorized` if too low), runs your handler inside a
try/except, and logs latency. Unknown commands get a generic hint — they never
reach you.

---

## 7. Hard rules (these keep the system robust — do not break them)

**You MAY:** declare commands/jobs/listeners/config-fields/lifecycle-hooks; read
your own config; publish & subscribe to events; resolve base-dir paths; push
proactive Telegram/WhatsApp messages (guarding for a missing transport); opt a
read-only command into the AI surface (`ai_invokable=True`).

**You MUST NOT:**
- ❌ **import another module** — communicate via the event bus.
- ❌ **read `config.toml` directly** — use `ctx.config`.
- ❌ **let a handler/job/listener raise uncaught** — catch expected errors and
  return a helpful `Response` (or log and return `None` for jobs). The framework
  has a safety net, but it only emits a generic "something went wrong."
- ❌ **build your own Telegram client or register transport-level handlers.**
- ❌ **add dependencies** that aren't already in `pyproject.toml`. Standard library
  only, plus what core already provides.
- ❌ **do blocking I/O** in async handlers (no `time.sleep`, no blocking `requests`).
  If unavoidable, use `await asyncio.to_thread(...)`.
- ❌ **block in `load()`** — declare only; defer work to `on_startup`.

**Open questions — if your module needs these, raise it instead of improvising:**
- **Outbound HTTP / device access:** there is no shared client yet. Don't each
  spin up your own; this is a pending core decision.
- **Persistent storage / DB:** none yet. Small per-module files under
  `ctx.registry.paths.data` are acceptable; anything bigger is a pending decision.
- **Targeting a WhatsApp group:** use `ctx.registry.whatsapp_groups` (fetched at
  bridge startup) or `data/whatsapp_groups.json` to look up available JIDs. Store
  the chosen JID in a module config field so the operator sets it once. Then pass
  it in `whatsapp_chat_ids=[jid]` on any `Response`. The *automatic mirror* path
  (`mirror_to_whatsapp=True`) is still unspecified and should not be used.

---

## 8. Roles & gating

`Role` is an ordered enum: `Viewer(1) < Operator(2) < Admin(3)`. Set each command's
`min_role` to the **least privilege that works**:
- **Viewer** — read-only / safe queries.
- **Operator** — actions that change something but are routine.
- **Admin** — sensitive/destructive or access-management actions.

Only mark a command `ai_invokable=True` if it is **read-only / low-risk**; it is
then reachable by an AI via the optional MCP server (still gated by the MCP
service-role and a global execution switch).

---

## 9. Config fields (optional)

Declare them in `load()`; the **setup wizard renders them** (types validated) and
writes `[modules.<name>]`. Read them at runtime via `ctx.config`. Values arrive
already typed per their `kind` (Integer→int, Boolean→bool).

```python
ConfigField(
    key="report_chat_id",
    label="Report chat ID",
    description="Telegram chat the daily report is sent to.",
    required=True,
    default=None,
    kind=ConfigFieldKind.Text,
)
# later:  chat_id = ctx.config.require("report_chat_id")
```

### Secrets — `ConfigFieldKind.Password` is special

A `Password` field is **not** written to `config.toml` and is **not** available
via `ctx.config`. Keeping secrets out of `config.toml` (and the logs) is the whole
point. Instead:

- The wizard masks the input and writes the value to a private `.env` sidecar
  (next to `config.toml`, mode `0600`).
- At startup the runtime loads that `.env` into the process environment (a real
  environment variable already set wins) and adds the values to the log scrubber.
- **You read it from an environment variable**, never `ctx.config`. The name is
  derived as `f"{module}_{key}".upper()` with non-alphanumerics replaced by `_`.

So a module named `weatherapi` with `ConfigField(key="api_key", kind=Password)`
reads its secret from `os.environ["WEATHERAPI_API_KEY"]`:

```python
import os

ConfigField(
    key="api_key",
    label="API key",
    description="Provider API key (stored in .env / the WEATHERAPI_API_KEY env var).",
    required=False,            # don't force it at setup; the env var may be set out-of-band
    default=None,
    kind=ConfigFieldKind.Password,
)

# later, in a handler — env first, with an optional manual config.toml fallback:
key = os.environ.get("WEATHERAPI_API_KEY") or ctx.config.get("api_key")
if not key:
    return Response(text="⚠️ Not configured (missing API key).", chat_id=request.chat_id)
```

Notes:
- An operator can skip the wizard and just set the env var directly — same result.
- Keep `required=False` so setup doesn't block when the key is supplied via the
  environment instead of the wizard.
- The `ctx.config.get("api_key")` fallback only catches a manually hand-edited
  `config.toml`; the wizard never puts the secret there.

---

## 10. Complete skeleton (copy, then adapt)

`octoops/modules/myfeature/plugin.json`
```json
{ "name": "myfeature", "version": "1.0.0", "description": "Example module." }
```

`octoops/modules/myfeature/__init__.py`
```python
"""myfeature module — <one-line purpose>.

Respects the OctoOps module contract: declares commands/jobs/listeners via load(),
touches only ctx, imports no other module, raises nothing from a handler.
"""

from __future__ import annotations

from octoops.core.contracts import (
    CommandDef,
    ConfigField,
    ConfigFieldKind,
    JobDef,
    ListenerDef,
    ModuleRegistration,
)
from octoops.core.logging import get_logger
from octoops.core.registry import ModuleContext
from octoops.shared.models import Request, Response, Role

log = get_logger("octoops.modules.myfeature")


def load(ctx: ModuleContext) -> ModuleRegistration:
    return ModuleRegistration(
        name="myfeature",
        commands=[
            CommandDef(
                name="ping",
                description="Reply pong (demonstrates a Viewer command).",
                min_role=Role.Viewer,
                handler=handle_ping,
            ),
        ],
        jobs=[
            JobDef(name="daily_report", schedule="0 8 * * *", handler=daily_report),
        ],
        listeners=[
            ListenerDef(event="myfeature.triggered", handler=on_triggered),
        ],
        config_fields=[
            ConfigField(
                key="report_chat_id",
                label="Report chat ID",
                description="Telegram chat for the daily report.",
                required=False,
                default=None,
                kind=ConfigFieldKind.Text,
            ),
        ],
        on_startup=on_startup,
    )


# --- command -----------------------------------------------------------------
async def handle_ping(request: Request, ctx: ModuleContext) -> Response:
    return Response(text="🏓 pong", chat_id=request.chat_id)


# --- job (no Response return path; push via a transport) ----------------------
async def daily_report(ctx: ModuleContext) -> None:
    chat_id = ctx.config.get("report_chat_id")
    if not chat_id:
        return
    tg = ctx.registry.transports.get("telegram")
    if tg is None:
        return
    try:
        await tg.send(Response(text="📊 Daily report …", chat_id=chat_id))
    except Exception as exc:  # never propagate out of a job
        log.error("myfeature.report_failed", error=str(exc))


# --- listener (reacts to a business event another module published) ----------
async def on_triggered(payload, ctx: ModuleContext) -> None:
    log.info("myfeature.triggered", payload=payload)


# --- lifecycle ---------------------------------------------------------------
async def on_startup(ctx: ModuleContext) -> None:
    log.info("myfeature.ready")
```

---

## 11. Tests (required)

Tests live in `tests/test_<name>.py`. `pytest` runs with `asyncio_mode=auto`, so
async test functions need no decorator. Build a `Request` and a fake/real `ctx`
and call handlers directly. Minimal pattern:

```python
from types import SimpleNamespace
from octoops.modules import myfeature
from octoops.shared.models import Request, Response, Role, TransportSource


def _req(command, *args, user_id="1"):
    return Request(
        command=command, args=list(args),
        raw_text=f"/{command}", user_id=user_id, chat_id="chat",
        source=TransportSource.Telegram,
    )


def _ctx(**overrides):
    registry = SimpleNamespace(transports={}, **overrides)
    return SimpleNamespace(registry=registry, config=SimpleNamespace(get=lambda k, d=None: d))


def test_registers_expected_commands():
    reg = myfeature.load(_ctx())
    names = {c.name for c in reg.commands}
    assert "ping" in names


async def test_ping_replies_pong():
    resp = await myfeature.handle_ping(_req("ping"), _ctx())
    assert isinstance(resp, Response)
    assert "pong" in resp.text
```

Run: `uv run --extra mcp pytest -q` (all green before you call it done).

---

## 12. Final plug-and-play checklist

- [ ] Folder at `octoops/modules/<name>/` (or `$OCTOOPS_HOME/modules/<name>/`).
- [ ] `plugin.json` with a **unique** `name`, matching the folder and
      `ModuleRegistration(name=...)`.
- [ ] `__init__.py` exposes `load(ctx) -> ModuleRegistration`; declares only.
- [ ] Command names are unique across the whole bot.
- [ ] Every handler/job/listener is `async`, takes `ctx`, and **catches its own
      errors**; commands return `Response(chat_id=request.chat_id)`.
- [ ] No imports of other modules; no direct `config.toml` reads; no new deps; no
      blocking I/O.
- [ ] `min_role` set to least privilege; `ai_invokable` only on read-only commands.
- [ ] Tests added and `uv run --extra mcp pytest` is green.
- [ ] `<name>` added to `[modules] enabled` (or re-run `--setup`).
- [ ] **Restart the bot** → confirm via `/status` or the `module.loaded` log line.

> If your idea can't be expressed within this contract, it is a **core/registry
> change, not a module** — surface that rather than working around the rules.
> For ideation and the project's open design questions, see `BRAINSTORM.md`.
