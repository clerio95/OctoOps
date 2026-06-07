"""Small text-formatting helpers shared across modules and transports."""

from __future__ import annotations

from datetime import timedelta


def humanize_duration(seconds: float) -> str:
    """Render a duration as e.g. '3d 4h 12m 5s', dropping leading zero units."""
    total = int(seconds)
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def humanize_timedelta(delta: timedelta) -> str:
    return humanize_duration(delta.total_seconds())
