"""Tests for T270-BROKER-ADMIN-GATE.

Every route in broker.py used Depends(get_current_user), not Depends(get_admin_user) — the
frontend already hides broker-linking UI behind an isAdmin check, but that was purely
cosmetic: nothing server-side enforced it. This mattered most for
PUT /paper-portfolios/{id}/broker — PaperPortfolio has no user_id column (portfolios are
shared/global), so any authenticated non-admin user could call that endpoint directly and
assign/unassign a broker connection on any shared portfolio, with no admin privilege
required. Fixed by switching every route to get_admin_user.

broker.py can't be imported directly in this test environment (needs a real db/Postgres
session) — tested via source-text extraction, matching test_broker_route_staleauth_detection.py's
established technique for this exact file.
"""
import pathlib

_BROKER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "broker.py"
_BROKER_SOURCE = _BROKER_PATH.read_text()

_ALL_ROUTE_FUNCTIONS = [
    "list_connections",
    "create_connection",
    "update_connection",
    "delete_connection",
    "oauth_start",
    "oauth_complete",
    "reconnect",
    "get_account_info",
    "get_order_history",
    "get_broker_quote",
    "get_portfolio_broker",
    "assign_portfolio_broker",
]


def _route_body(func_name: str) -> str:
    start = _BROKER_SOURCE.index(f"def {func_name}(")
    next_def = _BROKER_SOURCE.find("\ndef ", start + 1)
    next_router = _BROKER_SOURCE.find("\n@router", start + 1)
    candidates = [x for x in (next_def, next_router) if x != -1]
    end = min(candidates) if candidates else len(_BROKER_SOURCE)
    return _BROKER_SOURCE[start:end]


def test_get_current_user_is_not_imported_at_all():
    """Regression guard: get_current_user must not even be importable from this file's own
    import line — the fix removes it entirely rather than leaving it imported-but-unused,
    which would make it trivial to accidentally reintroduce a get_current_user-gated route."""
    import_line = next(
        line for line in _BROKER_SOURCE.splitlines() if line.startswith("from .auth import")
    )
    assert "get_current_user" not in import_line
    assert "get_admin_user" in import_line


def test_get_current_user_never_appears_as_a_dependency_anywhere_in_the_file():
    assert "Depends(get_current_user)" not in _BROKER_SOURCE


def test_every_route_function_depends_on_get_admin_user():
    for func_name in _ALL_ROUTE_FUNCTIONS:
        body = _route_body(func_name)
        assert "Depends(get_admin_user)" in body, f"{func_name} is missing the admin gate"


def test_the_portfolio_broker_assignment_route_specifically_is_admin_gated():
    """The single highest-stakes route this fix closes — PUT /paper-portfolios/{id}/broker,
    the endpoint that actually links a real broker connection to a shared, ownerless
    portfolio. A dedicated, named test so this specific route can never silently regress
    without its own test failing (not just a generic loop-over-all-routes check)."""
    body = _route_body("assign_portfolio_broker")
    assert "Depends(get_admin_user)" in body
    assert "Depends(get_current_user)" not in body


def test_all_12_known_routes_are_still_present():
    """Guards against the list above silently going stale (e.g. a route renamed or removed)
    and this test suite quietly checking fewer routes than actually exist in the file."""
    real_route_count = _BROKER_SOURCE.count("@router.")
    assert real_route_count == len(_ALL_ROUTE_FUNCTIONS), (
        f"broker.py has {real_route_count} @router. decorators but this test only tracks "
        f"{len(_ALL_ROUTE_FUNCTIONS)} route functions — update _ALL_ROUTE_FUNCTIONS"
    )
