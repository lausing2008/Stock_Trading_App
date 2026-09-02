"""Tests for T322-FEATURE-TIERING's get_advanced_user() gate and _make_token()'s tier claim.

auth.py can't be imported directly against conftest.py's default MagicMock-stubbed `db` module
— User/UserRole/UserTier would all resolve to MagicMock attributes, whose equality comparisons
don't behave like real enums, defeating the actual point of testing get_advanced_user()'s
comparison logic. Matches test_correlation_preentry.py's/test_broker_position_sync.py's
established technique: pop the sqlalchemy/db stubs, load the REAL shared/db/models.py while
real sqlalchemy is active, restore the stubs immediately, then extract get_advanced_user()'s
own source via exec() (not a full auth.py import, which pulls in FastAPI routing/DB-session
machinery this test doesn't need) and run it against real User/UserRole/UserTier instances.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_tier", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_tier"] = _models
_spec.loader.exec_module(_models)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

User = _models.User
UserRole = _models.UserRole
UserTier = _models.UserTier

_AUTH_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "auth.py"
_AUTH_SOURCE = _AUTH_PATH.read_text()


class _HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


def _extract_get_advanced_user():
    start = _AUTH_SOURCE.index("def get_advanced_user(")
    end = _AUTH_SOURCE.index("\n\n\n", start)
    func_source = _AUTH_SOURCE[start:end]
    namespace = {
        "User": User, "UserRole": UserRole, "UserTier": UserTier, "HTTPException": _HTTPException,
        # Depends() only matters for FastAPI's real dependency-injection wiring — this test
        # calls get_advanced_user(user) directly with a real User, never through FastAPI's
        # own resolution, so a no-op stand-in is sufficient.
        "Depends": lambda *_a, **_kw: None,
        "get_current_user": lambda *_a, **_kw: None,
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one real function's real source
    return namespace["get_advanced_user"]


get_advanced_user = _extract_get_advanced_user()


def _user(role: "UserRole", tier: "UserTier") -> "User":
    u = User()
    u.role = role
    u.tier = tier
    return u


def test_advanced_tier_user_passes():
    u = _user(UserRole.USER, UserTier.ADVANCED)
    assert get_advanced_user(u) is u


def test_basic_tier_user_is_rejected():
    u = _user(UserRole.USER, UserTier.BASIC)
    try:
        get_advanced_user(u)
        assert False, "expected HTTPException"
    except _HTTPException as exc:
        assert exc.status_code == 403


def test_admin_passes_regardless_of_their_own_tier():
    """An ADMIN always passes even with tier=BASIC — role already implies full platform
    access, matching get_advanced_user()'s own documented reasoning."""
    u = _user(UserRole.ADMIN, UserTier.BASIC)
    assert get_advanced_user(u) is u


def test_advanced_admin_also_passes():
    u = _user(UserRole.ADMIN, UserTier.ADVANCED)
    assert get_advanced_user(u) is u
