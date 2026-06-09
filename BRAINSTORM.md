# OctoOps — Module Brainstorming Canvas

A scratchpad for inventing new modules. It describes **what OctoOps is**, **what a
module can do (and can't)**, and gives **seeds** to spark ideas. The framework is
done and stable; everything new is a module dropped into `octoops/modules/` (or an
external `$OCTOOPS_HOME/modules/`). No recompile, no toolchain on the deploy box.

---

## What OctoOps is, in one picture

```
Telegram (operators) ──▶ Router ──▶ your module's handler ──▶ Response
                            │                                   │
                            │                          ┌────────┴─────────┐
                            ▼                          ▼                  ▼
                       Permissions               Telegram reply    (optional) WhatsApp mirror
                       (role check)

Scheduler ──(cron)──▶ your module's job                Event Bus ──▶ your module's listener
                                                       ▲
                                          any module publishes business events

MCP server (optional) ──▶ exposes your opt-in commands as AI tools + read-only resources
```

- **Telegram is the control plane.** Operators type commands; the Router checks
  their role and calls your handler; the reply goes back to Telegram.
- **WhatsApp is push-first.** Modules can *push* messages to WhatsApp chats (e.g.
  from a scheduled job). There's also an *optional* inbound path, but it routes
  every message from a whitelisted number to **one** configured command — so a
  WhatsApp user can drive a single module flow (e.g. `deadlines`), never the whole bot.
- **The core is domain-neutral.** All business logic lives in modules. Modules
  never import each other — they talk via the **event bus**.
- **Roles:** `Viewer < Operator < Admin`. Every command declares a `min_role`.
- **Commands can be conversational.** A module can ask follow-up questions (a menu,
  a multi-field flow) by driving a per-user state machine through
  `ctx.registry.conversations` — works on Telegram and WhatsApp.

---

## The shape of a module

A module is a folder with a `plugin.json` (name/version/description) and a Python
package exposing `load(ctx) -> ModuleRegistration`. In `load`, you register any mix of:

| You can register… | Via | Fires when… |
|---|---|---|
| **Commands** | `CommandDef(name, description, min_role, handler, ai_invokable=False)` | an operator runs `/name` in Telegram (or an AI tool, if `ai_invokable`) |
| **Scheduled jobs** | `JobDef(name, schedule, handler)` | a cron expression matches (in the configured timezone) |
| **Event listeners** | `ListenerDef(event, handler)` | another part of the system publishes `event` |
| **Config fields** | `ConfigField(key, label, description, required, default, kind)` | collected by the setup wizard, read at runtime |
| **Lifecycle hooks** | `on_startup`, `on_shutdown` | the bot starts / stops |

`ConfigFieldKind` ∈ `Text · Password · FilePath · IpAddress · Integer · Boolean`
(the wizard renders each appropriately; passwords masked, types validated).

Everything a handler/job/listener needs arrives in `ctx: ModuleContext`:

```python
ctx.config.get("key") / ctx.config.require("key")   # this module's config section
ctx.event_bus.publish("inventory.low", {...})       # tell other modules something happened
ctx.registry.transports["whatsapp"].send(Response(   # proactively push to WhatsApp
    text="...", chat_id="", whatsapp_chat_ids=["123@g.us"]))
ctx.registry.permissions.role_for(user_id)           # who's asking
ctx.registry.paths.resolve("data/foo.csv")           # base-dir-relative paths
ctx.registry.start_time / ctx.registry.module_names  # introspection
ctx.registry.router.entries()                        # the live command set (e.g. for /help)
ctx.registry.config.core.language                    # "en" | "pt-BR" — localize replies
ctx.registry.conversations                           # per-user multi-step flow state
```

A handler returns a `Response(text=..., chat_id=request.chat_id)`. Set
`mirror_to_whatsapp=True` + `whatsapp_chat_ids=[...]` to also send it to WhatsApp.

---

## How a module is onboarded & configured

The framework discovers, configures, and loads modules generically — **the core
and the wizard have no module-specific knowledge.** A module only has to *declare*
itself; everything below is automatic. (For the exact contract and a copy-paste
checklist, see `MODULE_BUILD_PROMPT.md`.)

```
drop folder ─▶ wizard discovers ─▶ enable + fill config ─▶ restart ─▶ loaded
 (modules/)     (reads config_fields)   (config.toml / .env)   (no hot-reload)
```

1. **Place it.** A folder under `octoops/modules/<name>/` (in-tree) or
   `$OCTOOPS_HOME/modules/<name>/` (external drop-in, no checkout edit), with a
   `plugin.json` and a `load(ctx) -> ModuleRegistration`.
2. **Discovery.** The setup wizard pre-scans every module (`plugin_loader.discover_modules`)
   by importing it with a stub context and calling `load()` — purely to harvest its
   declared `config_fields`. This is why the wizard never needs to know what your
   module *is*: it renders whatever fields you declare.
3. **Enable.** The wizard's *Module selection* screen lists every discovered module
   as a checkbox (all on by default on a fresh install). The gate is
   `config.toml [modules] enabled = [...]` — the loader skips anything not listed.
4. **Configure.** The *Module configuration* screen renders one validated input per
   `config_field` and writes a `[modules.<name>]` sub-table. At runtime you read
   **only your own** section via `ctx.config.get/require` — never the file directly.
5. **Secrets.** A `ConfigFieldKind.Password` field is the exception: it is **not**
   written to `config.toml` and **not** in `ctx.config`. The wizard writes it to a
   private `.env` (0600) beside `config.toml`; the runtime loads it into the
   environment (and the log scrubber) at startup; you read it from
   `os.environ["{MODULE}_{KEY}"]` (e.g. `BRAIN_API_KEY`). Keeps secrets out of the
   config file and the logs.
6. **Load.** On startup `plugin_loader.load_modules` imports each *enabled* module,
   builds a real `ModuleContext`, calls `load()`, and wires the result into the
   Router (commands), Scheduler (jobs), and Event Bus (listeners). A module that
   fails to import/load is logged and skipped — the rest of the bot still boots.

**No hot-reload.** The loop is *drop → enable → restart*; confirm with `/status`
(it lists loaded modules) or the `module.loaded` log line. Re-running `--setup` is
non-destructive: it pre-fills from the existing `config.toml`/`.env` and backs up
the old config before overwriting.

---

## The four interaction surfaces (the design space)

Every module idea is some combination of these. Ask: *which surfaces does this use?*

1. **Command-driven** — operator asks, bot answers. `/status`-like. Synchronous.
2. **Job-driven** — runs on a schedule, no human trigger. Pushes results to a chat
   (Telegram and/or WhatsApp). Good for reports, polls, watchdogs.
3. **Event-driven** — reacts to *business events* other modules publish (not
   transport events). Decouples "something happened" from "what to do about it."
4. **AI-exposed (MCP)** — mark a command `ai_invokable=True` and, with the MCP
   server enabled, an AI client can call it as a tool and read module/status
   resources. Lets an assistant *understand and operate* the bot.
5. **Conversational (multi-step)** — a command that asks follow-up questions (menu,
   guided "add"/"edit" flow) instead of taking everything on one line. Drive it
   with `ctx.registry.conversations` (a per-user state machine; see
   `MODULE_BUILD_PROMPT.md` §5b and the `deadlines` module). Works on Telegram and,
   via the single-command inbound + a keyword gate, on WhatsApp.

---

## What a module MAY do — and MUST NOT

**May:** define commands, register jobs, subscribe/publish events, declare config
fields, use startup/shutdown hooks, push proactive WhatsApp messages, read its own
config, resolve base-dir paths, opt a command into the AI/MCP surface, drive a
multi-step conversation (`ctx.registry.conversations`), introspect the command set
(`ctx.registry.router`), and localize text (`ctx.registry.config.core.language`).

**Must not** (these are hard rules — they keep the system robust):
- import another module (communicate via the event bus instead);
- read `config.toml` directly (use `ctx.config`);
- raise uncaught exceptions from a handler/job/listener (catch and return/log);
- register transport-level handlers, or build its own Telegram client;
- **add dependencies** not already available.

> **Open design questions** (these gate whole classes of modules — decide before
> building those):
> - **Outbound integrations.** Modules that talk to external APIs or on-prem
>   devices need an HTTP/socket client, but the rules discourage each module
>   spinning up its own. Likely answer: add a *shared* client/helper to the
>   `Registry` so modules borrow it (like they borrow the WhatsApp transport).
>   Config already hints at this (`IpAddress`, `FilePath` field kinds exist).
> - **Persistent storage.** There's no DB yet. Options: per-module files under
>   `ctx.registry.paths.data`, or a shared store added to the registry. Decide
>   before building anything stateful.
> - **Telegram → WhatsApp mirror routing.** The plumbing exists
>   (`Response.mirror_to_whatsapp` / `whatsapp_chat_ids`), but *which* response
>   goes to *which* WA chat is deliberately unspecified. A "relay/routing"
>   module or a routing-rules config is an open design.

---

## Brainstorming seeds

Don't copy old business logic — use these as *prompts*, not specs.

### By trigger
- **On a schedule:** end-of-day summary, periodic health poll of a device/endpoint,
  "nothing reported in N hours" watchdog, scheduled reminder/digest.
- **On command:** look something up, run an action and confirm, fetch-and-format a
  report, toggle a setting, kick off a longer job and report when done.
- **On an event:** when module A publishes `X`, module B notifies a WA group / logs
  it / escalates / aggregates it. (e.g. `threshold.crossed` → notify.)
- **By an AI:** expose safe read commands as MCP tools so an assistant can answer
  "what's the current state?" by composing module data.

### By capability axis (mix and match)
- **Notify** (push to TG/WA) · **Query** (read + format) · **Act** (do something,
  confirm) · **Watch** (poll + alert on change) · **Aggregate** (collect events →
  digest) · **Schedule** (time-based) · **Relay** (move info between chats/systems).

### Questions to generate ideas
- What does an operator currently check or do *manually and repeatedly*? → command or job.
- What "X happened" moments should *notify* someone? → event + listener + WA push.
- What recurring report would be useful at a fixed time? → job.
- What would you want to ask an AI assistant *about the bot's domain*? → MCP-exposed read commands.
- What two systems need a one-way bridge of information? → relay module (mind the mirror-routing question).

### Idea-shape templates
```
Module: <name>
Surfaces: [command | job | event | mcp]
Trigger: <cron / command / event name / AI tool>
Inputs (config_fields): <key:kind, ...>
Reads/talks to: <file? device? another module's events?>   ← check open questions
Output: <Telegram reply | WhatsApp push | published event>
Min role: <viewer | operator | admin>
```

---

## Reality check before committing to an idea

- Does it fit the **module contract** (commands/jobs/listeners/config)? If it needs
  something the contract doesn't offer, it may be a *core/registry* change first.
- Does it need an **outbound client** or **storage**? → resolve the open question above.
- Does it cross modules? → it must go through the **event bus**, not imports.
- Is it safe to **expose to AI**? Only mark read-only / low-risk commands `ai_invokable`.
- What **role** should gate it? Default to the least privilege that works.

---

# Design: the business "brain" (shared, file-backed AI memory)

> Status: **design only, not built.** A concrete plan for a persistent, AI-readable/
> writable knowledge base that any AI connecting through the MCP server hydrates from
> and contributes to. It is "shared file-backed agent memory, seeded by an onboarding
> interview, mediated by MCP" — the same proven pattern as Claude's memory tool and
> Managed-Agent memory stores, scoped to one business and one deploy box.

## Concept & lifecycle

```
1. ONBOARD (once)   curated business questions ──▶ writes ground-truth seed files
2. READ (always)    AI connects ──▶ reads the brain for context BEFORE answering
3. WRITE (ongoing)  AI records new/changed facts as the program grows
4. REVIEW (humans)  operator inspects/corrects/rolls back the brain folder directly
```

The brain is **just a folder of markdown files** under `paths.data/brain/`. Human-
readable on purpose: operators can open, edit, diff, and revert it. No database.

## Folder layout (trust tiers are folders)

```
$OCTOOPS_HOME/data/brain/
├── INDEX.md                  # manifest: what topics exist, last-updated dates
├── ground-truth/             # HIGH trust — operator-confirmed (onboarding + human edits)
│   ├── business.md           #   what the business is, goals, vocabulary
│   ├── people.md             #   roles, who-owns-what, escalation contacts
│   └── policies.md           #   rules, thresholds, SLAs, do/don't
├── observations/             # LOW trust — AI-derived notes. Disposable & regenerable.
│   └── 2026-06/...           #   never let load-bearing decisions rest on these alone
└── events/                   # FACTS — appended by an event-sink module (optional)
    └── timeline.jsonl        #   raw "what happened, when" feed for the brain
```

Every file carries provenance frontmatter so a later AI knows how much to trust it:

```markdown
---
trust: ground-truth | observation | event
source: onboarding | operator:<id> | ai:<session> | module:<name>
created: 2026-06-06
updated: 2026-06-06
---
<the fact>
```

## Config (`[brain]` section)

```toml
[brain]
enabled = false                 # expose the brain over MCP at all
dir = "data/brain"              # resolved against OCTOOPS_HOME
allow_writes = false            # SEPARATE from [mcp] allow_command_execution
require_human_approval = true   # writes go to a pending queue until an operator OKs
```

Reads and writes are gated independently from command execution: you can have a
brain that the AI may *read and learn into* but that cannot *run commands*, or a
read-only brain, etc.

## MCP surface

| Kind | Name | Gate | Purpose |
|---|---|---|---|
| Resource | `octoops://brain` | `enabled` | INDEX/manifest of topics + dates |
| Resource | `octoops://brain/{path}` | `enabled` | one brain file (with provenance) |
| Tool | `brain_search(query)` | `enabled` | find relevant brain entries (read) |
| Tool | `brain_remember(topic, content, source)` | `allow_writes` | add a new note (→ observations, or pending queue) |
| Tool | `brain_update(path, content, expected_hash)` | `allow_writes` | revise an existing fact (concurrency-safe) |

The MCP server's `instructions` field tells clients to **read `octoops://brain`
first**. (See enforcement caveat below — this is advisory for external clients.)

## The write protocol (the hard part — this is where brains rot)

1. **Trust tiers.** Writes from the AI land in `observations/` (low trust), never in
   `ground-truth/`. Only onboarding and human edits write `ground-truth/`.
2. **Disposable AI notes.** Treat `observations/` as regenerable. Nothing critical
   may depend on an AI note alone — it must trace back to ground-truth or an event.
3. **Update, don't append.** `brain_update` requires `expected_hash` (content hash of
   the file the AI read); mismatch → reject and make it re-read. Prevents clobbering
   and duplicate contradictory copies. (Mirrors Managed-Agent memory preconditions.)
4. **Provenance + timestamps on every write** so staleness and authorship are visible.
5. **No raw external content auto-written.** User/module text is never written
   verbatim as a "fact" (memory-poisoning / prompt-injection guard). The AI must
   summarize and attribute; high-value writes go through `require_human_approval`.
6. **Audit + rollback.** The brain folder is git-tracked (or versioned copies); every
   write is a commit so an operator can see and revert a bad write.
7. **Break the feedback loop.** Periodically reconcile `observations/` against
   `ground-truth/` + `events/`; drop or flag AI notes that contradict facts. Prevents
   the "AI reads its own earlier guess as gospel and elaborates" compounding error.

## Onboarding (seeding the brain)

A one-time guided flow — start **deterministic** (a curated question list you control),
not AI-driven, so the seed is clean:

- Surface: a `/onboard` Telegram flow (Admin-only) or `octoops brain init` CLI.
- Asks a fixed set of business questions (what the business does, key terms, who's who,
  thresholds/policies, what "normal" looks like).
- Writes answers to `ground-truth/*.md` with `source: onboarding`.
- Creates `INDEX.md`. Re-runnable to extend; never auto-overwrites ground truth.

Later, an AI may *propose* additions to ground-truth, but those become pending items
an operator confirms — they don't self-promote from observation to fact.

## Enforcement caveat (read this before promising "every AI reads first")

- **Embedded assistant (`/ask` module): enforceable.** You own the prompt, so you
  inject the brain (or instruct read-brain-first) on every call. Guaranteed.
- **External clients (Claude Desktop, Managed Agent, third-party): advisory only.**
  MCP lets you advertise resources + `instructions`, not mandate call order. A well-
  behaved client reads first; you can't force it. Good descriptions + the instructions
  field are the lever.

## Build options

- **A — local folder + embedded assistant (recommended for one box).** Brain lives in
  `data/brain/`; a `/ask` module (Claude API + `McpService`/registry as tools) reads it
  every call and answers in Telegram. Self-contained, private, enforceable, no port
  exposed. Hand-roll the write protocol above (it's just files + git).
- **B — Managed-Agent memory store (if you want multiple external agents sharing one
  managed brain).** Anthropic hosts a versioned, concurrency-safe, redactable memory
  the agent mounts as a filesystem — the hard parts (versioning, preconditions, audit)
  are already solved. Trade-off: cloud dependency + exposing/credentialing, vs. A's
  fully-local self-containment.

## Suggested phasing (lowest risk first)

1. **Read-only brain.** `[brain] enabled, allow_writes=false`. Onboarding seeds
   `ground-truth/`; MCP exposes it as resources; an embedded `/ask` reads it. Proves
   value with zero write risk.
2. **Event-sink module.** Persist business events to `events/timeline.jsonl` so the
   brain has a factual history to reason over.
3. **Gated writes.** Turn on `allow_writes` + `require_human_approval`: AI proposes
   notes to `observations/`; operator confirms via Telegram. Add git audit.
4. **Reconciliation job.** Scheduled pass that flags stale/contradictory observations.

## Open decisions

- Storage detail: plain folder + git vs. a small versioned store on the `Registry`
  (the BRAINSTORM "persistent storage" open question — the brain is its first concrete consumer).
- Privacy: what may leave the box if an *external* AI reads the brain? (redaction lines.)
- Who may write ground-truth, and how onboarding re-runs handle conflicts.
- Whether `brain_*` tools live in a dedicated `brain` module or in the core MCP surface.
