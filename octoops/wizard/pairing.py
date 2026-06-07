"""WhatsApp QR/pairing flow.

After the wizard writes config.toml, if the bridge binary is present but not yet
authenticated, this spawns the bridge (which prints its QR code to the terminal)
and polls GET /health until logged_in is true or a timeout elapses, then stops
the bridge. The session is persisted by the bridge, so later runs start logged in.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from octoops.transports.whatsapp.bridge_client import BridgeClient

_POLL_INTERVAL = 2.0


async def wait_for_login(
    client: BridgeClient, timeout: float, *, interval: float = _POLL_INTERVAL
) -> bool:
    """Poll /health until logged_in is true, or timeout. Testable in isolation."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = await client.health()
            if data.get("logged_in"):
                return True
        except Exception:  # noqa: BLE001 - bridge may still be starting
            pass
        await asyncio.sleep(interval)
    return False


async def is_logged_in(client: BridgeClient) -> bool:
    try:
        data = await client.health()
        return bool(data.get("logged_in"))
    except Exception:  # noqa: BLE001
        return False


async def run_pairing(bridge_path: str, bridge_port: int, *, timeout: float = 120.0) -> bool:
    """Spawn the bridge, show its QR, wait for login, then stop it.

    Returns True if login succeeded. Best-effort: a missing binary returns False.
    """
    path = Path(bridge_path)
    if not path.exists():
        print(f"Bridge binary not found at {path}; skipping pairing.")
        return False

    client = BridgeClient(f"http://127.0.0.1:{bridge_port}")
    # Already paired from a previous run?
    if await is_logged_in(client):
        print("WhatsApp bridge is already logged in.")
        await client.close()
        return True

    print("\nStarting WhatsApp bridge — scan the QR code below with your phone.\n")
    proc = await asyncio.create_subprocess_exec(str(path))  # inherits stdout (QR)
    try:
        ok = await wait_for_login(client, timeout)
        print("\nWhatsApp paired successfully." if ok else "\nPairing timed out.")
        return ok
    finally:
        try:
            await asyncio.wait_for(client.shutdown(), timeout=3)
        except Exception:  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        await client.close()
