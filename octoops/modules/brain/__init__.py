"""brain module — /ask: an embedded AI assistant grounded in context prompts.

Provider-agnostic (OpenAI-compatible endpoint; one BRAIN_API_KEY reaches Claude,
Gemini, free-tier models, a local Ollama, ...). Answers are contextualized by a
configurable folder of prompt files (see prompts.py). The API key is read from
the BRAIN_API_KEY environment variable — never from config.toml, so it can't leak
into the config file or logs.

This is the inverse of the MCP server: here the bot embeds an AI to answer
operators, rather than exposing the bot's commands to an external AI. Respects
the module contract: declares only via load(), touches only ctx, imports no other
module, raises nothing from the handler.

Step 1 exposes the brain via Telegram /ask. The WhatsApp brain-only inbound path
reuses answer()/the /ask command and is added in a follow-up (transport change).
"""

from __future__ import annotations

import os

from octoops.core.contracts import (
    CommandDef,
    ConfigField,
    ConfigFieldKind,
    ModuleRegistration,
)
from octoops.core.logging import get_logger
from octoops.core.registry import ModuleContext
from octoops.shared.models import Request, Response, Role

from .prompts import load_prompts
from .providers import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    BrainError,
    BrainProvider,
    build_provider,
)

log = get_logger("octoops.modules.brain")

# The API key lives in the environment, not config.toml (keeps the secret out of
# the config file and the log-scrubbing surface).
API_KEY_ENV = "BRAIN_API_KEY"
_DEFAULT_PROMPTS_DIR = "data/brain/prompts"
# Telegram caps a message near 4096 chars; stay comfortably under it.
_REPLY_LIMIT = 4000

_SYSTEM_PREAMBLE = (
    "You are OctoOps' embedded assistant. Answer concisely and plainly for a chat "
    "message — no markdown tables, keep it short. Use the CONTEXT below as your "
    "source of truth; if it doesn't cover the question, say so plainly rather than "
    "inventing an answer."
)


def load(ctx: ModuleContext) -> ModuleRegistration:
    return ModuleRegistration(
        name="brain",
        commands=[
            CommandDef(
                name="ask",
                description="Ask the assistant a question: /ask <your question>",
                min_role=Role.Operator,  # spends API budget + reads context; above Viewer
                handler=handle_ask,
            )
        ],
        config_fields=[
            ConfigField(
                key="provider",
                label="AI provider",
                description="Provider adapter (currently: openai_compat).",
                required=False,
                default=DEFAULT_PROVIDER,
                kind=ConfigFieldKind.Text,
            ),
            ConfigField(
                key="base_url",
                label="API base URL",
                description="OpenAI-compatible endpoint, e.g. https://openrouter.ai/api/v1",
                required=True,
                default=DEFAULT_BASE_URL,
                kind=ConfigFieldKind.Text,
            ),
            ConfigField(
                key="model",
                label="Model",
                description="Model id, e.g. google/gemini-2.0-flash-exp:free",
                required=True,
                default=DEFAULT_MODEL,
                kind=ConfigFieldKind.Text,
            ),
            ConfigField(
                key="api_key",
                label="API key",
                description="Provider API key. Stored in a private .env (0600), "
                "not config.toml; equivalent to the BRAIN_API_KEY env var.",
                required=False,
                default=None,
                kind=ConfigFieldKind.Password,
            ),
            ConfigField(
                key="prompts_dir",
                label="Prompts folder",
                description="Folder of context prompt files (.md/.txt), base-dir-relative.",
                required=False,
                default=_DEFAULT_PROMPTS_DIR,
                kind=ConfigFieldKind.FilePath,
            ),
            ConfigField(
                key="max_tokens",
                label="Max answer length",
                description="Upper bound on the answer length, in tokens.",
                required=False,
                default=str(DEFAULT_MAX_TOKENS),
                kind=ConfigFieldKind.Integer,
            ),
        ],
    )


def _build_system(prompts: str) -> str:
    if prompts:
        return f"{_SYSTEM_PREAMBLE}\n\n<context>\n{prompts}\n</context>"
    return _SYSTEM_PREAMBLE


async def answer(question: str, *, provider: BrainProvider, prompts: str) -> str:
    """Core Q&A used by /ask (and, later, the WhatsApp path). Never raises."""
    system = _build_system(prompts)
    try:
        reply = await provider.ask(system, question)
    except BrainError as exc:
        log.error("brain.provider_failed", error=str(exc))
        return "⚠️ The assistant couldn't answer right now. Please try again later."
    except Exception as exc:  # noqa: BLE001 - boundary: never propagate out of a handler
        log.error("brain.unexpected", error=str(exc), error_type=type(exc).__name__)
        return "⚠️ The assistant hit an unexpected error."
    return reply or "(no answer)"


async def handle_ask(request: Request, ctx: ModuleContext) -> Response:
    question = " ".join(request.args).strip()
    if not question:
        return Response(text="Usage: /ask <your question>", chat_id=request.chat_id)

    # Primary path: the wizard writes the key to .env -> BRAIN_API_KEY. Fall back
    # to a manually-set [modules.brain] api_key so that path works too.
    api_key = os.environ.get(API_KEY_ENV) or ctx.config.get("api_key")
    if not api_key:
        log.error("brain.no_api_key", env=API_KEY_ENV)
        return Response(
            text="⚠️ The assistant isn't configured yet (missing API key).",
            chat_id=request.chat_id,
        )

    try:
        provider = build_provider(ctx.config, api_key=api_key)
    except BrainError as exc:
        log.error("brain.config_error", error=str(exc))
        return Response(text="⚠️ The assistant is misconfigured.", chat_id=request.chat_id)

    prompts = load_prompts(
        ctx.registry.paths, ctx.config.get("prompts_dir") or _DEFAULT_PROMPTS_DIR
    )
    text = await answer(question, provider=provider, prompts=prompts)
    return Response(text=text[:_REPLY_LIMIT], chat_id=request.chat_id)
