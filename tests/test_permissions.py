from octoops.core.permissions import Permissions
from octoops.shared.models import Role


def make_perms() -> Permissions:
    return Permissions(
        allowed_user_ids=["100", "300"],   # 300 also admin -> should resolve Admin
        operator_user_ids=["200"],
        admin_user_ids=["300"],
        default_role=Role.Viewer,
    )


def test_allowed_user_gets_default_role():
    assert make_perms().role_for("100") is Role.Viewer


def test_operator_role():
    assert make_perms().role_for("200") is Role.Operator


def test_highest_role_wins_across_lists():
    # 300 is in allowed (Viewer) and admin (Admin) -> Admin.
    assert make_perms().role_for("300") is Role.Admin


def test_unknown_user_denied():
    assert make_perms().role_for("999") is None


def test_authorize_hierarchy():
    perms = make_perms()
    assert perms.authorize("300", Role.Admin)         # admin meets admin
    assert perms.authorize("200", Role.Viewer)        # operator exceeds viewer
    assert not perms.authorize("100", Role.Operator)  # viewer below operator
    assert not perms.authorize("999", Role.Viewer)    # unknown -> fail closed


def test_role_from_str():
    assert Role.from_str("admin") is Role.Admin
    assert Role.from_str("VIEWER") is Role.Viewer
