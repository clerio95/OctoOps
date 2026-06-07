"""Load the operator-curated context prompts that ground the brain's answers.

A plain folder of ``.md`` / ``.txt`` files (resolved base-dir-relative via
AppPaths). The operator drops in possible answers and insights; every file is
concatenated into the assistant's system prompt. Human-editable on purpose — no
DB, no redeploy. Best-effort: any read problem yields no context, never an error.
"""

from __future__ import annotations

from typing import Any

_ALLOWED_SUFFIXES = (".md", ".txt")
_SEPARATOR = "\n\n---\n\n"


def load_prompts(paths: Any, prompts_dir: str) -> str:
    """Concatenate every prompt file under ``prompts_dir``. Returns "" if none.

    ``paths`` is ctx.registry.paths (AppPaths) or None (e.g. the wizard pre-scan
    / tests with no base dir), in which case there is no folder to read.
    """
    if paths is None or not prompts_dir:
        return ""
    try:
        base = paths.resolve(prompts_dir)
    except Exception:  # noqa: BLE001 - resolution must never break a handler
        return ""
    if not base.is_dir():
        return ""

    chunks: list[str] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.suffix.lower() not in _ALLOWED_SUFFIXES:
            continue
        try:
            text = entry.read_text("utf-8").strip()
        except OSError:
            continue
        if text:
            chunks.append(text)
    return _SEPARATOR.join(chunks)
