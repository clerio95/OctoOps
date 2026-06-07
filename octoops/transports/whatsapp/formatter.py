"""Response -> WhatsApp message text.

WhatsApp supports the same lightweight *bold* / _italic_ markers modules emit,
so this is currently a passthrough. Kept as a seam for future divergence.
"""

from __future__ import annotations

from octoops.shared.models import Response


def format_text(response: Response) -> str:
    return response.text
