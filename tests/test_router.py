import pytest

from octoops.core.contracts import CommandDef
from octoops.core.errors import RouterError
from octoops.core.router import Router
from octoops.shared.models import Request, Response, Role, TransportSource


def make_request(command: str, user_id: str) -> Request:
    return Request(
        command=command,
        args=[],
        raw_text=f"/{command}",
        user_id=user_id,
        chat_id="chat1",
        source=TransportSource.Telegram,
    )


async def ok_handler(request, ctx):
    return Response(text="ok", chat_id=request.chat_id)


async def boom_handler(request, ctx):
    raise RuntimeError("handler exploded")


def test_register_and_has_command(permissions, module_ctx):
    router = Router(permissions)
    router.register(
        CommandDef("ping", "desc", Role.Viewer, ok_handler), module_ctx
    )
    assert router.has_command("ping")
    assert router.has_command("/ping")  # leading slash stripped


def test_duplicate_registration_is_fatal(permissions, module_ctx):
    router = Router(permissions)
    cmd = CommandDef("ping", "desc", Role.Viewer, ok_handler)
    router.register(cmd, module_ctx)
    with pytest.raises(RouterError):
        router.register(cmd, module_ctx)


@pytest.mark.asyncio
async def test_dispatch_is_case_insensitive(permissions, module_ctx):
    router = Router(permissions)
    # Module registers a mixed-case name; incoming arrives lowercased.
    router.register(
        CommandDef("Deploy", "desc", Role.Viewer, ok_handler), module_ctx
    )
    assert router.has_command("deploy")
    resp = await router.dispatch(make_request("deploy", "100"))
    assert resp.text == "ok"


@pytest.mark.asyncio
async def test_dispatch_authorized(permissions, module_ctx):
    router = Router(permissions)
    router.register(
        CommandDef("ping", "desc", Role.Viewer, ok_handler), module_ctx
    )
    resp = await router.dispatch(make_request("ping", "100"))  # viewer
    assert resp.text == "ok"


@pytest.mark.asyncio
async def test_dispatch_denied_for_insufficient_role(permissions, module_ctx):
    router = Router(permissions)
    router.register(
        CommandDef("admincmd", "desc", Role.Admin, ok_handler), module_ctx
    )
    resp = await router.dispatch(make_request("admincmd", "100"))  # viewer < admin
    assert "not authorized" in resp.text.lower()


@pytest.mark.asyncio
async def test_dispatch_unknown_command(permissions, module_ctx):
    router = Router(permissions)
    resp = await router.dispatch(make_request("nope", "100"))
    assert "unknown command" in resp.text.lower()


@pytest.mark.asyncio
async def test_role_override_authorizes_by_given_role(permissions, module_ctx):
    router = Router(permissions)
    router.register(
        CommandDef("op", "desc", Role.Operator, ok_handler), module_ctx
    )
    # user "100" is a Viewer, but the override decides authorization.
    req = make_request("op", "100")
    assert (await router.dispatch(req, role_override=Role.Operator)).text == "ok"
    denied = await router.dispatch(req, role_override=Role.Viewer)
    assert "not authorized" in denied.text.lower()


@pytest.mark.asyncio
async def test_handler_exception_returns_fallback(permissions, module_ctx):
    router = Router(permissions)
    router.register(
        CommandDef("boom", "desc", Role.Viewer, boom_handler), module_ctx
    )
    resp = await router.dispatch(make_request("boom", "100"))
    assert "went wrong" in resp.text.lower()
