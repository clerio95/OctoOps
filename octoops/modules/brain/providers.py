"""Provider abstraction for the brain module.

The brain is provider-agnostic: a single OpenAI-compatible HTTP adapter reaches
OpenRouter, Google Gemini (its OpenAI endpoint), Groq, Together, a local Ollama,
and many free-tier models — switch provider/model by config alone, with one
``BRAIN_API_KEY``. The adapter speaks plain HTTP over aiohttp (already a project
dependency), so no new packages are required.

A native ``anthropic`` (or other) adapter can be added later behind the same
``BrainProvider`` protocol and selected by the ``provider`` config key.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

import aiohttp

# Sensible defaults aimed at the "one key, free tier" goal: OpenRouter + a free
# Gemini model. The operator overrides these in [modules.brain].
DEFAULT_PROVIDER = "openai_compat"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-2.0-flash-exp:free"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT = 30.0


class BrainError(Exception):
    """A provider/config failure the handler turns into a friendly reply."""


@runtime_checkable
class BrainProvider(Protocol):
    async def ask(self, system: str, question: str) -> str:
        """Return the assistant's answer text. Raise BrainError on failure."""
        ...


# (url, headers, json_body) -> parsed JSON dict. Injectable so the adapter is
# unit-testable without a live network.
PostFn = Callable[[str, dict[str, str], dict[str, Any]], Awaitable[dict[str, Any]]]


async def _aiohttp_post(
    url: str, headers: dict[str, str], body: dict[str, Any], *, timeout: float
) -> dict[str, Any]:
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
        async with session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise BrainError(f"provider HTTP {resp.status}: {text[:200]}")
            return await resp.json()


class OpenAICompatProvider:
    """Talks to any OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        post: PostFn | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        if post is not None:
            self._post: PostFn = post
        else:
            async def _default(u: str, h: dict[str, str], b: dict[str, Any]) -> dict[str, Any]:
                return await _aiohttp_post(u, h, b, timeout=timeout)

            self._post = _default

    def _payload(self, system: str, question: str) -> dict[str, Any]:
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ],
        }

    async def ask(self, system: str, question: str) -> str:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        data = await self._post(url, headers, self._payload(system, question))
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BrainError(f"unexpected provider response shape: {exc}") from exc
        if not isinstance(content, str):
            raise BrainError("provider returned non-text content")
        return content.strip()


def build_provider(config: Any, *, api_key: str) -> BrainProvider:
    """Construct the configured provider from a ModuleConfig view.

    ``config`` is the module's ctx.config (anything with ``.get(key, default)``).
    Raises BrainError for an unknown provider.
    """
    provider = (config.get("provider") or DEFAULT_PROVIDER)
    if provider != "openai_compat":
        raise BrainError(f"unknown brain provider: {provider!r}")
    base_url = config.get("base_url") or DEFAULT_BASE_URL
    model = config.get("model") or DEFAULT_MODEL
    try:
        max_tokens = int(config.get("max_tokens") or DEFAULT_MAX_TOKENS)
    except (TypeError, ValueError):
        max_tokens = DEFAULT_MAX_TOKENS
    return OpenAICompatProvider(
        base_url=base_url, model=model, api_key=api_key, max_tokens=max_tokens
    )
