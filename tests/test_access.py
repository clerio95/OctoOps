"""Runtime whitelist management: layered Permissions, RoleStore, access module."""

from types import SimpleNamespace

import pytest

from octoops.core.errors import PermissionsError
from octoops.core.invites import InviteStore
from octoops.core.permissions import Permissions
from octoops.core.role_store import RoleStore
from octoops.modules import access
from octoops.shared.models import Request, Role, TransportSource


def _perms(store=None, runtime_grants=None, **kw):
    base = dict(allowed_user_ids=[], operator_user_ids=[], admin_user_ids=["1"])
    base.update(kw)
    return Permissions(store=store, runtime_grants=runtime_grants, **base)


# --- layered resolution -------------------------------------------------------


def test_runtime_grant_adds_access():
    perms = _perms()
    assert perms.role_for("50") is None
    perms.grant("50", Role.Operator)
    assert perms.role_for("50") is Role.Operator
    assert perms.authorize("50", Role.Viewer) is True
    assert perms.authorize("50", Role.Admin) is False


def test_effective_role_is_highest_across_layers():
    # config gives admin; a lower runtime grant can't downgrade them.
    perms = _perms(admin_user_ids=["1"])
    perms.grant("1", Role.Viewer)
    assert perms.role_for("1") is Role.Admin


def test_known_users_merges_config_and_runtime():
    perms = _perms(allowed_user_ids=["7"], admin_user_ids=["1"], default_role=Role.Viewer)
    perms.grant("9", Role.Operator)
    assert perms.known_users() == {"1": Role.Admin, "7": Role.Viewer, "9": Role.Operator}
    assert perms.is_runtime_only("9") is True
    assert perms.is_runtime_only("1") is False


# --- revoke rules -------------------------------------------------------------


def test_revoke_removes_runtime_grant():
    perms = _perms()
    perms.grant("50", Role.Operator)
    perms.revoke("50")
    assert perms.role_for("50") is None


def test_revoke_config_user_is_rejected():
    perms = _perms(admin_user_ids=["1"])
    with pytest.raises(PermissionsError, match="config.toml"):
        perms.revoke("1")


def test_revoke_unknown_user_is_rejected():
    with pytest.raises(PermissionsError, match="no runtime grant"):
        _perms().revoke("999")


def test_cannot_revoke_last_admin():
    # No config admin; the only admin is a runtime grant -> protected.
    perms = _perms(admin_user_ids=[])
    perms.grant("50", Role.Admin)
    with pytest.raises(PermissionsError, match="last remaining admin"):
        perms.revoke("50")


def test_can_revoke_runtime_admin_when_a_config_admin_remains():
    perms = _perms(admin_user_ids=["1"])  # config admin survives
    perms.grant("50", Role.Admin)
    perms.revoke("50")  # allowed
    assert perms.role_for("50") is None


# --- persistence (RoleStore) --------------------------------------------------


def test_grant_persists_and_reloads(tmp_path):
    path = tmp_path / "data" / "access.json"
    store = RoleStore(path)
    perms = _perms(store=store)
    perms.grant("50", Role.Operator)
    perms.grant("60", Role.Admin)

    # A fresh Permissions built from the reloaded store sees the grants.
    reloaded = _perms(runtime_grants=RoleStore(path).load())
    assert reloaded.role_for("50") is Role.Operator
    assert reloaded.role_for("60") is Role.Admin


def test_revoke_persists(tmp_path):
    path = tmp_path / "access.json"
    store = RoleStore(path)
    perms = _perms(store=store)
    perms.grant("50", Role.Operator)
    perms.revoke("50")
    assert RoleStore(path).load() == {}


def test_corrupt_store_loads_empty(tmp_path):
    path = tmp_path / "access.json"
    path.write_text("{not valid json", "utf-8")
    assert RoleStore(path).load() == {}


def test_missing_store_loads_empty(tmp_path):
    assert RoleStore(tmp_path / "nope.json").load() == {}


def test_role_store_file_is_private(tmp_path):
    import os
    import stat

    path = tmp_path / "access.json"
    RoleStore(path).save({"50": Role.Operator})
    if os.name != "nt":  # POSIX mode bits only
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# --- access module handlers ---------------------------------------------------


def _ctx(perms, *, invites=None, bot_username=None):
    return SimpleNamespace(
        registry=SimpleNamespace(
            permissions=perms, invites=invites, bot_username=bot_username
        )
    )


def _req(command, *args, user_id="1"):
    return Request(
        command=command,
        args=list(args),
        raw_text=f"/{command} {' '.join(args)}".strip(),
        user_id=user_id,
        chat_id="chat",
        source=TransportSource.Telegram,
    )


def test_module_registers_expected_commands():
    reg = access.load(_ctx(_perms()))
    cmds = {c.name: c for c in reg.commands}
    assert set(cmds) == {"whoami", "users", "grant", "revoke", "invite", "invites"}
    assert cmds["whoami"].min_role is Role.Viewer
    assert cmds["users"].min_role is Role.Admin
    assert cmds["grant"].min_role is Role.Admin
    assert cmds["invite"].min_role is Role.Admin


@pytest.mark.asyncio
async def test_whoami_reports_id_and_role():
    perms = _perms(admin_user_ids=["1"])
    resp = await access.handle_whoami(_req("whoami"), _ctx(perms))
    assert "`1`" in resp.text and "Admin" in resp.text


@pytest.mark.asyncio
async def test_grant_then_revoke_flow():
    perms = _perms()
    granted = await access.handle_grant(_req("grant", "operator", "50"), _ctx(perms))
    assert "Granted Operator" in granted.text
    assert perms.role_for("50") is Role.Operator

    revoked = await access.handle_revoke(_req("revoke", "50"), _ctx(perms))
    assert "Revoked" in revoked.text
    assert perms.role_for("50") is None


@pytest.mark.asyncio
async def test_grant_rejects_bad_role_and_nonnumeric_id():
    perms = _perms()
    bad_role = await access.handle_grant(_req("grant", "wizard", "50"), _ctx(perms))
    assert "Unknown role" in bad_role.text
    bad_id = await access.handle_grant(_req("grant", "operator", "abc"), _ctx(perms))
    assert "not a numeric" in bad_id.text
    assert perms.known_users() == {"1": Role.Admin}


@pytest.mark.asyncio
async def test_grant_wrong_arity_shows_usage():
    resp = await access.handle_grant(_req("grant", "operator"), _ctx(_perms()))
    assert resp.text.startswith("Usage:")


@pytest.mark.asyncio
async def test_revoke_config_user_reports_error():
    perms = _perms(admin_user_ids=["1"])
    resp = await access.handle_revoke(_req("revoke", "1"), _ctx(perms))
    assert "config.toml" in resp.text


@pytest.mark.asyncio
async def test_users_lists_with_source():
    perms = _perms(admin_user_ids=["1"])
    perms.grant("9", Role.Operator)
    resp = await access.handle_users(_req("users"), _ctx(perms))
    assert "`1`" in resp.text and "(config)" in resp.text
    assert "`9`" in resp.text and "(runtime)" in resp.text


# --- invite handlers ----------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_creates_link_with_bot_username():
    invites = InviteStore()
    ctx = _ctx(_perms(), invites=invites, bot_username="MyBot")
    resp = await access.handle_invite(_req("invite", "operator"), ctx)
    assert "https://t.me/MyBot?start=" in resp.text
    assert "Operator" in resp.text
    # The invite is actually pending and redeemable.
    pending = invites.pending()
    assert len(pending) == 1 and pending[0].role is Role.Operator


@pytest.mark.asyncio
async def test_invite_falls_back_when_username_unknown():
    ctx = _ctx(_perms(), invites=InviteStore(), bot_username=None)
    resp = await access.handle_invite(_req("invite", "viewer"), ctx)
    assert "/start " in resp.text  # manual fallback instruction


@pytest.mark.asyncio
async def test_invite_rejects_bad_role():
    ctx = _ctx(_perms(), invites=InviteStore())
    resp = await access.handle_invite(_req("invite", "wizard"), ctx)
    assert "Unknown role" in resp.text


@pytest.mark.asyncio
async def test_invites_lists_pending():
    invites = InviteStore()
    invites.create(Role.Viewer)
    ctx = _ctx(_perms(), invites=invites, bot_username="MyBot")
    resp = await access.handle_invites(_req("invites"), ctx)
    assert "Pending invites" in resp.text and "Viewer" in resp.text


@pytest.mark.asyncio
async def test_invites_empty():
    ctx = _ctx(_perms(), invites=InviteStore())
    resp = await access.handle_invites(_req("invites"), ctx)
    assert "No pending invites" in resp.text


# --- transport gate redemption ------------------------------------------------


@pytest.mark.asyncio
async def test_gate_redeems_valid_invite_and_grants(monkeypatch):
    from octoops.transports.telegram import adapter as adapter_mod

    sent = []

    async def _capture(response, registry):
        sent.append(response)

    monkeypatch.setattr(adapter_mod, "route_response", _capture)

    perms = _perms(admin_user_ids=["1"])
    invites = InviteStore()
    invite = invites.create(Role.Operator)
    transport = adapter_mod.TelegramTransport(token="t")
    transport._registry = SimpleNamespace(permissions=perms, invites=invites)

    # Unknown user redeems the nonce -> granted + welcomed.
    await transport._maybe_redeem_invite("start", [invite.nonce], "777", "777")
    assert perms.role_for("777") is Role.Operator
    assert len(sent) == 1 and "Operator access" in sent[0].text
    # Single-use: nonce is now spent.
    assert invites.redeem(invite.nonce) is None


@pytest.mark.asyncio
async def test_gate_ignores_bad_nonce_and_plain_messages(monkeypatch):
    from octoops.transports.telegram import adapter as adapter_mod

    sent = []

    async def _capture(response, registry):
        sent.append(response)

    monkeypatch.setattr(adapter_mod, "route_response", _capture)

    perms = _perms()
    transport = adapter_mod.TelegramTransport(token="t")
    transport._registry = SimpleNamespace(permissions=perms, invites=InviteStore())

    await transport._maybe_redeem_invite("start", ["wrongnonce"], "777", "777")
    await transport._maybe_redeem_invite("status", [], "777", "777")  # not /start
    assert sent == []  # bot stays silent to non-invited unknown users
    assert perms.role_for("777") is None
