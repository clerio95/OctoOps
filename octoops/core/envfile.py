"""Tiny stdlib .env reader/writer for module secrets.

Module Password config fields are written by the wizard to a private ``.env``
sidecar (0600) next to config.toml — never into config.toml itself — and loaded
into the process environment at startup. A module reads its secret from the
corresponding env var (e.g. the brain reads ``BRAIN_API_KEY``). No third-party
dependency: this parses a simple ``KEY=value`` format (``#`` comments, optional
surrounding quotes).
"""

from __future__ import annotations

from pathlib import Path


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
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def format_env(mapping: dict[str, str]) -> str:
    if not mapping:
        return ""
    lines = [f'{key}="{mapping[key]}"' for key in sorted(mapping)]
    return "\n".join(lines) + "\n"


def load_env_file(path: str | Path) -> dict[str, str]:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        return parse_env(p.read_text("utf-8"))
    except OSError:
        return {}
