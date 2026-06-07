"""One-time invite store: minting, single-use redemption, expiry, persistence."""

from octoops.core.invites import InviteStore
from octoops.shared.models import Role


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_create_and_redeem_once():
    store = InviteStore()
    invite = store.create(Role.Operator)
    assert invite.role is Role.Operator
    assert store.redeem(invite.nonce) is not None
    # Single-use: a second redemption fails.
    assert store.redeem(invite.nonce) is None


def test_redeem_unknown_nonce_returns_none():
    assert InviteStore().redeem("nope") is None


def test_expired_invite_is_not_redeemable():
    clock = _Clock()
    store = InviteStore(clock=clock, ttl_seconds=100)
    invite = store.create(Role.Viewer)
    clock.t += 101  # past expiry
    assert store.redeem(invite.nonce) is None


def test_pending_excludes_expired():
    clock = _Clock()
    store = InviteStore(clock=clock, ttl_seconds=100)
    a = store.create(Role.Viewer)
    clock.t += 50
    b = store.create(Role.Admin)
    clock.t += 60  # a expired (110 > 100), b still valid (60 < 100)
    pending = store.pending()
    assert [i.nonce for i in pending] == [b.nonce]
    assert a.nonce not in [i.nonce for i in pending]


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "data" / "invites.json"
    store = InviteStore(path)
    invite = store.create(Role.Admin)

    # A fresh store loads the persisted invite and can redeem it.
    reloaded = InviteStore(path)
    redeemed = reloaded.redeem(invite.nonce)
    assert redeemed is not None and redeemed.role is Role.Admin
    # Redemption persisted too — a third store sees it gone.
    assert InviteStore(path).redeem(invite.nonce) is None


def test_corrupt_store_loads_empty(tmp_path):
    path = tmp_path / "invites.json"
    path.write_text("not json", "utf-8")
    assert InviteStore(path).pending() == []


def test_invites_file_is_private(tmp_path):
    import os
    import stat

    path = tmp_path / "invites.json"
    InviteStore(path).create(Role.Viewer)
    if os.name != "nt":  # POSIX mode bits only
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
