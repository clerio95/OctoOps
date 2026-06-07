"""Integration: status module load() path + a happy-path /status dispatch."""

import pytest

from octoops.core.bootstrap import build_runtime
from octoops.shared.models import Request, TransportSource


def make_request(user_id: str) -> Request:
    return Request(
        command="status",
        args=[],
        raw_text="/status",
        user_id=user_id,
        chat_id="chat1",
        source=TransportSource.Telegram,
    )


@pytest.mark.asyncio
async def test_status_dispatch_happy_path(app_config):
    runtime = build_runtime(app_config)
    assert "status" in runtime.registry.module_names
    assert runtime.router.has_command("status")

    resp = await runtime.router.dispatch(make_request("300"))  # admin user
    assert "OctoOps status" in resp.text
    assert "Uptime:" in resp.text
    assert "Admin" in resp.text  # requester's resolved role
    assert "status" in resp.text  # loaded module list


@pytest.mark.asyncio
async def test_status_reports_viewer_role(app_config):
    runtime = build_runtime(app_config)
    resp = await runtime.router.dispatch(make_request("100"))  # allowed -> Viewer
    assert "Viewer" in resp.text
