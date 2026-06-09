"""Tiny stdlib .env reader/writer for module secrets.

Module Password config fields are written by the wizard to a private ``.env``
sidecar (0600) next to config.toml — never into config.toml itself — and loaded
into the process environment at startup. A module reads its secret from the
corresponding env var (e.g. the brain reads ``BRAIN_API_KEY``). No third-party
dependency: this parses a simple ``KEY=value`` format (``#`` comments, optional
surrounding quotes).

Values are always written double-quoted with C-style escaping (``\\``, ``\"``,
``\n``, ``\r``) so a secret containing quotes, backslashes, or newlines survives a
round-trip and can never break out of its line. Double-quoted values are
unescaped on read; bare and single-quoted values are taken literally (the latter
for hand-edited files).
"""

from __future__ import annotations

from pathlib import Path

_UNESCAPE = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}


def _unescape(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append(_UNESCAPE.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = _unescape(value)
        if key:
            out[key] = value
    return out


def format_env(mapping: dict[str, str]) -> str:
    if not mapping:
        return ""
    lines = [f'{key}="{_escape(mapping[key])}"' for key in sorted(mapping)]
    return "\n".join(lines) + "\n"


def load_env_file(path: str | Path) -> dict[str, str]:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        return parse_env(p.read_text("utf-8"))
    except OSError:
        return {}
