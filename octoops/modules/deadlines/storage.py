"""JSON-backed deadline records.

A deadline file is a JSON array of objects. Keys are stored upper-cased (matching
the shape an external sheet-export would have), values as trimmed strings. The
schema mirrors a typical deadlines sheet — DESCRICAO, a due date, ORGAO,
FREQUENCIA, CRITICO, ALERTA_DIAS — plus an ID so a record can be referenced later.

Reads are forgiving (a missing or corrupt file yields an empty list); writes are
atomic (write to a temp file, then replace) so a crash mid-write can't truncate
the store.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime
from pathlib import Path

# Where a record's due date may live, in priority order (sheet-export aliases).
DATE_KEYS = ("PROXIMA_DATA", "DATA", "DATA_BASE")
PRIMARY_DATE_KEY = "PROXIMA_DATA"

_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d")


def parse_date(value: str) -> date | None:
    """Parse dd/mm/yyyy or yyyy-mm-dd; None if neither matches."""
    value = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def format_date(value: date) -> str:
    """Canonical on-disk/display form: dd/mm/yyyy."""
    return value.strftime("%d/%m/%Y")


def resolve_date(row: dict) -> date | None:
    for key in DATE_KEYS:
        if key in row:
            parsed = parse_date(row[key])
            if parsed:
                return parsed
    return None


def to_int(value, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize(item: dict) -> dict:
    return {
        str(k).upper(): (str(v).strip() if v is not None else "")
        for k, v in item.items()
    }


def load_deadlines(path: str | Path) -> list[dict]:
    """Read and normalize the records. Returns [] for a missing/corrupt file."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [_normalize(item) for item in data if isinstance(item, dict)]


def save_deadlines(path: str | Path, rows: list[dict]) -> None:
    """Atomically write the records as a UTF-8 JSON array (parent dirs created)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _new_id(existing: set[str]) -> str:
    while True:
        candidate = secrets.token_hex(3)  # 6 hex chars, e.g. "a1b2c3"
        if candidate not in existing:
            return candidate


def add_deadline(path: str | Path, record: dict) -> dict:
    """Append a record (assigning a unique ID) and persist. Returns the saved row."""
    rows = load_deadlines(path)
    existing_ids = {r.get("ID", "") for r in rows}
    stored = _normalize(record)
    stored["ID"] = _new_id(existing_ids)
    rows.append(stored)
    save_deadlines(path, rows)
    return stored


def find_deadline(rows: list[dict], deadline_id: str) -> dict | None:
    for row in rows:
        if row.get("ID") == deadline_id:
            return row
    return None


def update_deadline(path: str | Path, deadline_id: str, changes: dict) -> dict | None:
    """Apply ``changes`` (upper-cased keys) to the record with ``deadline_id``.

    Returns the updated record, or None if no record has that ID.
    """
    rows = load_deadlines(path)
    for row in rows:
        if row.get("ID") == deadline_id:
            for key, value in changes.items():
                row[str(key).upper()] = str(value).strip() if value is not None else ""
            save_deadlines(path, rows)
            return row
    return None


def delete_deadline(path: str | Path, deadline_id: str) -> bool:
    """Remove the record with ``deadline_id``. Returns True if one was removed."""
    rows = load_deadlines(path)
    kept = [r for r in rows if r.get("ID") != deadline_id]
    if len(kept) == len(rows):
        return False
    save_deadlines(path, kept)
    return True


def upcoming(rows: list[dict], within_days: int, today: date) -> list[dict]:
    """Records due between today and today+within_days, soonest first.

    Each returned row is the original plus ``_date`` (the resolved date) and
    ``_remaining`` (days until due). Records without a parseable date are skipped.
    """
    out: list[dict] = []
    for row in rows:
        due = resolve_date(row)
        if due is None:
            continue
        remaining = (due - today).days
        if 0 <= remaining <= within_days:
            out.append({**row, "_date": due, "_remaining": remaining})
    out.sort(key=lambda r: r["_date"])
    return out


def all_sorted(rows: list[dict], today: date) -> list[dict]:
    """Every record, soonest date first (undated last). Adds ``_date``/``_remaining``."""
    enriched = []
    for row in rows:
        due = resolve_date(row)
        enriched.append(
            {
                **row,
                "_date": due,
                "_remaining": (due - today).days if due else None,
            }
        )
    enriched.sort(key=lambda r: (r["_date"] is None, r["_date"] or date.max))
    return enriched
