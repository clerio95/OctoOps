"""brain module — provider adapter, prompt loading, and the /ask handler.

No network: the OpenAI-compatible adapter takes an injectable `post`, and the
handler path uses a fake provider.
"""

from __future__ import annotations

from types import SimpleNamespace

from octoops.core.registry import ModuleConfig
from octoops.modules import brain
from octoops.modules.brain import answer, handle_ask, load
from octoops.modules.brain.prompts import load_prompts
from octoops.core.contracts import ConfigFieldKind
from octoops.modules.brain.providers import (
    BrainError,
    OpenAICompatProvider,
    build_provider,
)
from octoops.shared.models import Request, Response, Role, TransportSource


# --- helpers -----------------------------------------------------------------


def _req(*words: str, user_id: str = "200") -> Request:
    args = list(words)
    return Request(
        command="ask",
        args=args,
        raw_text="/ask " + " ".join(args),
        user_id=user_id,
        chat_id="chat",
        source=TransportSource.Telegram,
    )


def _paths(root):
    """Minimal stand-in for AppPaths: resolve(rel) -> root/rel."""
    return SimpleNamespace(resolve=lambda rel: root / rel)


def _ctx(*, config: dict | None = None, paths=None):
    registry = SimpleNamespace(paths=paths)
    return SimpleNamespace(config=ModuleConfig(config or {}), registry=registry)


class _FakeProvider:
    def __init__(self, reply="hi", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.seen: tuple[str, str] | None = None

    async def ask(self, system: str, question: str) -> str:
        self.seen = (system, question)
        if self.error is not None:
            raise self.error
        return self.reply


# --- load() ------------------------------------------------------------------


def test_load_registers_ask_command_and_fields():
    reg = load(_ctx())
    assert reg.name == "brain"
    cmd = {c.name: c for c in reg.commands}["ask"]
    assert cmd.min_role is Role.Operator
    assert cmd.ai_invokable is False  # the brain is the AI client, not an AI-callable command
    fields = {f.key: f for f in reg.config_fields}
    assert {"provider", "base_url", "model", "prompts_dir", "max_tokens", "api_key"} <= set(
        fields
    )
    # The API key is a Password field -> wizard routes it to .env, not config.toml.
    assert fields["api_key"].kind is ConfigFieldKind.Password
    assert fields["api_key"].required is False


# --- load_prompts ------------------------------------------------------------


def test_load_prompts_concatenates_md_and_txt_sorted(tmp_path):
    (tmp_path / "b.txt").write_text("second", encoding="utf-8")
    (tmp_path / "a.md").write_text("first", encoding="utf-8")
    (tmp_path / "ignore.json").write_text("nope", encoding="utf-8")
    (tmp_path / ".hidden.md").write_text("hidden", encoding="utf-8")
    out = load_prompts(_paths(tmp_path), ".")
    assert out == "first\n\n---\n\nsecond"
    assert "nope" not in out and "hidden" not in out


def test_load_prompts_missing_dir_returns_empty(tmp_path):
    assert load_prompts(_paths(tmp_path), "does_not_exist") == ""


def test_load_prompts_none_paths_returns_empty():
    assert load_prompts(None, "data/brain/prompts") == ""


# --- OpenAICompatProvider ----------------------------------------------------


async def test_openai_compat_payload_and_parse():
    captured = {}

    async def fake_post(url, headers, body):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body
        return {"choices": [{"message": {"content": "  the answer  "}}]}

    p = OpenAICompatProvider(
        base_url="https://gw.example/api/v1/",  # trailing slash should be normalized
        model="some/model:free",
        api_key="secret",
        max_tokens=256,
        post=fake_post,
    )
    out = await p.ask("SYS", "Q?")
    assert out == "the answer"
    assert captured["url"] == "https://gw.example/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    body = captured["body"]
    assert body["model"] == "some/model:free"
    assert body["max_tokens"] == 256
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "Q?"},
    ]


async def test_openai_compat_bad_shape_raises_brain_error():
    async def fake_post(url, headers, body):
        return {"unexpected": True}

    p = OpenAICompatProvider(base_url="x", model="m", api_key="k", post=fake_post)
    try:
        await p.ask("s", "q")
    except BrainError:
        return
    raise AssertionError("expected BrainError")


async def test_openai_compat_non_text_content_raises():
    async def fake_post(url, headers, body):
        return {"choices": [{"message": {"content": {"not": "text"}}}]}

    p = OpenAICompatProvider(base_url="x", model="m", api_key="k", post=fake_post)
    try:
        await p.ask("s", "q")
    except BrainError:
        return
    raise AssertionError("expected BrainError")


# --- build_provider ----------------------------------------------------------


def test_build_provider_defaults_and_unknown():
    prov = build_provider(ModuleConfig({}), api_key="k")
    assert isinstance(prov, OpenAICompatProvider)
    try:
        build_provider(ModuleConfig({"provider": "mystery"}), api_key="k")
    except BrainError:
        return
    raise AssertionError("expected BrainError for unknown provider")


def test_build_provider_bad_max_tokens_falls_back():
    # int("abc") would raise; build_provider must tolerate it.
    prov = build_provider(ModuleConfig({"max_tokens": "abc"}), api_key="k")
    assert isinstance(prov, OpenAICompatProvider)


# --- answer() ----------------------------------------------------------------


async def test_answer_injects_context_and_returns_reply():
    fake = _FakeProvider(reply="grounded reply")
    out = await answer("what?", provider=fake, prompts="KNOWLEDGE")
    assert out == "grounded reply"
    system, question = fake.seen
    assert "KNOWLEDGE" in system and "<context>" in system
    assert question == "what?"


async def test_answer_without_prompts_has_no_context_block():
    fake = _FakeProvider(reply="ok")
    await answer("q", provider=fake, prompts="")
    system, _ = fake.seen
    assert "<context>" not in system


async def test_answer_provider_error_is_friendly():
    fake = _FakeProvider(error=BrainError("boom"))
    out = await answer("q", provider=fake, prompts="")
    assert "couldn't answer" in out.lower()


async def test_answer_unexpected_error_is_friendly():
    fake = _FakeProvider(error=RuntimeError("kaboom"))
    out = await answer("q", provider=fake, prompts="")
    assert "unexpected error" in out.lower()


# --- handle_ask --------------------------------------------------------------


async def test_handle_ask_empty_question_shows_usage():
    resp = await handle_ask(_req(), _ctx())
    assert isinstance(resp, Response)
    assert "Usage:" in resp.text


async def test_handle_ask_missing_key_reports_unconfigured(monkeypatch):
    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    resp = await handle_ask(_req("hello"), _ctx())
    assert "missing API key" in resp.text


async def test_handle_ask_happy_path(monkeypatch):
    monkeypatch.setenv("BRAIN_API_KEY", "k")
    fake = _FakeProvider(reply="42")
    monkeypatch.setattr(brain, "build_provider", lambda config, *, api_key: fake)
    resp = await handle_ask(_req("meaning", "of", "life"), _ctx())
    assert resp.text == "42"
    assert fake.seen[1] == "meaning of life"


async def test_handle_ask_truncates_long_reply(monkeypatch):
    monkeypatch.setenv("BRAIN_API_KEY", "k")
    fake = _FakeProvider(reply="x" * 5000)
    monkeypatch.setattr(brain, "build_provider", lambda config, *, api_key: fake)
    resp = await handle_ask(_req("long"), _ctx())
    assert len(resp.text) == 4000
