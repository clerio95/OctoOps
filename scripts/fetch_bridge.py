#!/usr/bin/env python3
"""Fetch the Whatsmeow bridge binary into the OctoOps base directory.

Best-effort and dependency-free (stdlib only) so it can run during setup before
the project env exists. The download URL comes from the OCTOOPS_BRIDGE_URL
environment variable; if unset, this prints where to place the binary and exits
0 (a missing bridge is not fatal — WhatsApp output is simply disabled).

Usage:
    OCTOOPS_BRIDGE_URL=https://.../whatsmeow-bridge.exe python scripts/fetch_bridge.py
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path


def _home() -> Path:
    env = os.environ.get("OCTOOPS_HOME")
    return Path(env).expanduser().resolve() if env else Path.cwd().resolve()


def _filename() -> str:
    return "whatsmeow-bridge.exe" if sys.platform.startswith("win") else "whatsmeow-bridge"


def main() -> int:
    target = _home() / _filename()
    if target.is_file():
        print(f"bridge already present: {target}")
        return 0

    url = os.environ.get("OCTOOPS_BRIDGE_URL")
    if not url:
        print(
            "OCTOOPS_BRIDGE_URL not set — skipping bridge download.\n"
            f"Place the bridge binary manually at: {target}\n"
            "(WhatsApp output stays disabled until it is present.)"
        )
        return 0

    print(f"downloading bridge from {url} -> {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as resp, open(target, "wb") as out:  # noqa: S310
            out.write(resp.read())
        if not sys.platform.startswith("win"):
            target.chmod(0o755)
        print(f"bridge installed: {target}")
        return 0
    except Exception as exc:  # noqa: BLE001 - best effort
        print(f"bridge download failed: {exc}", file=sys.stderr)
        print(f"Place the binary manually at: {target}", file=sys.stderr)
        return 0  # non-fatal


if __name__ == "__main__":
    raise SystemExit(main())
