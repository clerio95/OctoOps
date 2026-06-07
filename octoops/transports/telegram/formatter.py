"""Response -> Telegram send_message kwargs.

Modules emit lightweight Markdown (e.g. *bold*). We request legacy Markdown
parse mode; the adapter retries without it if Telegram rejects the formatting.
"""

from __future__ import annotations

from typing import Any

from telegram.constants import ParseMode

from octoops.shared.models import Response


def format_response(response: Response) -> dict[str, Any]:
    return {"text": response.text, "parse_mode": ParseMode.MARKDOWN}
