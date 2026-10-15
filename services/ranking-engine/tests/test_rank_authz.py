"""RANK-AUTHZ: the ranking refresh and both K-Score tune routes required only a login.

THE DEFECT. `POST /rankings/refresh`, `/rankings/tune_kscore_weights` and
`/rankings/tune_kscore_curve` were guarded by `get_current_username`, which proves only that
somebody signed in. Any ordinary account could trigger a full re-score of the shared universe or
retune the K-Score every user's rankings are computed with. The gateway's role check covers the
`/admin` prefix alone.

THE FIX reuses ml-prediction's `require_model_admin`: a CURRENTLY-active admin (re-read from the
database) or a service token scoped `model`. Both internal callers — market-data's scheduler and
`admin.py`'s add-stock refresh — send `_service_token()`, which carries `svc: ["model"]`.

These tests drive the REAL routes over HTTP with the REAL dependency. Only the database session
and the heavy work behind each route are replaced, so an authorization regression cannot hide
behind a stubbed `common.jwt_auth` (which this suite's conftest otherwise installs as a Mock that
would accept anyone).
"""
import importlib
import importlib.util
import sys
import time
import types
import uuid
from pathlib import Path

import pytest
import asyncio

import httpx
from fastapi import FastAPI
from jose import jwt

SECRET = "rank-authz-secret"
_ROOT = Path(__file__).resolve().parents[3]
_SERVICE = Path(__file__).resolve().parents[1]
ROUTES = ("/rankings/refresh", "/rankings/tune_kscore_weights", "/rankings/tune_kscore_curve")


def _load_real_jwt_auth():
    spec = importlib.util.spec_from_file_location(
        "real_jwt_auth_rank", _ROOT / "shared" / "common" / "jwt_auth.py")
    mod = importlib.util.module_from_spec(spec)
    cfg = types.ModuleType("common.config")
    cfg.get_settings = lambda: types.SimpleNamespace(jwt_secret=SECRET)
    rds = types.ModuleType("common.redis_client")
    rds.get_redis = lambda: (_ for _ in ()).throw(RuntimeError("no redis in tests"))
    prev = {k: sys.modules.get(k) for k in ("common.config", "common.redis_client")}
    sys.modules["common.config"], sys.modules["common.redis_client"] = cfg, rds
    try:
        spec.loader.exec_module(mod)
    finally:
        for k, v in prev.items():
            if v is not None:
                sys.modules[k] = v
    mod._check_blacklist = lambda _jti: False
    mod.user_tokens_revoked = lambda _p: False
    return mod


class _Col:
    def __eq__(self, other):
        return ("username", other)


class _Accounts:
    """The live-account lookup require_model_admin performs, keyed by username."""
    rows: dict = {}

    class _Q:
        def __init__(self, rows):
            self.rows, self.pred = rows, None

        def filter(self, pred):
            self.pred = pred
            return self

        def one_or_none(self):
            return self.rows.get(self.pred[1])

    def __call__(self):
        outer = self

        class _S:
            def query(self, _m):
                return _Accounts._Q(outer.rows)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return _S()


class _Expr:
    """Stands in for a model class or column: every attribute, call and comparison yields
    another expression. The routes only BUILD queries with these; `_Session` answers them all
    with no rows, so each route takes its empty-universe path."""
    def __getattr__(self, _n):
        return _Expr()

    def __call__(self, *a, **k):
        return _Expr()

    __eq__ = __ne__ = __ge__ = __gt__ = __le__ = __lt__ = lambda self, other: _Expr()
    __hash__ = object.__hash__


class _Stmt:
    def where(self, *a, **k):
        return self

    order_by = where


class _Result:
    def scalars(self):
        return []

    def all(self):
        return []


class _Session:
    def execute(self, *_a, **_k):
        return _Result()


@pytest.fixture
def wired(monkeypatch):
    """The real routes module and the real jwt_auth, with only the database replaced."""
    jwt_auth = _load_real_jwt_auth()
    accounts = _Accounts()
    accounts.rows = {}
    fake_db = types.ModuleType("db")
    for name in ("Fundamental", "Price", "Ranking", "Signal", "SignalType", "Stock", "TimeFrame",
                 "TuneHistory"):
        setattr(fake_db, name, _Expr())   # absorbs column expressions; queries return nothing
    fake_db.get_session = lambda: _Session()
    fake_db.SessionLocal = accounts
    fake_db.User = types.SimpleNamespace(username=_Col())
    fake_db.UserRole = types.SimpleNamespace(ADMIN="ADMIN", USER="USER")
    monkeypatch.setitem(sys.modules, "common.jwt_auth", jwt_auth)
    monkeypatch.setitem(sys.modules, "db", fake_db)
    monkeypatch.syspath_prepend(str(_SERVICE))
    for m in [m for m in sys.modules if m == "src" or m.startswith("src.")]:
        monkeypatch.delitem(sys.modules, m, raising=False)
    routes = importlib.import_module("src.api.routes")
    kscore = importlib.import_module("src.scoring.kscore")
    # The heavy work behind each route. Authorization is decided before any of it runs.
    monkeypatch.setattr(routes, "select", lambda *a, **k: _Stmt())
    monkeypatch.setattr(routes, "_persist_rankings", lambda *_a, **_k: None)
    monkeypatch.setattr(kscore, "_load_active_weights", lambda: {})
    monkeypatch.setattr(kscore, "_load_active_curve_params", lambda: {})
    return types.SimpleNamespace(routes=routes, jwt_auth=jwt_auth, accounts=accounts.rows)


def _worker_threads_run() -> bool:
    """FastAPI runs a SYNC dependency (require_model_admin is one) on an AnyIO worker thread.
    The independent re-review (2026-10-14) saw that worker never complete in its environment,
    so every HTTP case timed out. Probe it once, bounded."""
    import anyio

    async def go():
        return await asyncio.wait_for(anyio.to_thread.run_sync(lambda: 1), timeout=5)
    try:
        return asyncio.run(go()) == 1
    except Exception:
        return False


@pytest.fixture
def client(wired):
    # HONEST SKIP, never a pass: without worker threads these HTTP cases cannot run at all.
    # The thread-free tests below still prove the wiring and every decision.
    if not _worker_threads_run():
        pytest.skip("AnyIO worker threads do not run in this environment; HTTP cases not run "
                    "(thread-free wiring and decision tests still apply)")
    app = FastAPI()
    app.include_router(wired.routes.router)
    app.dependency_overrides[wired.routes.get_session] = lambda: _Session()
    c = _Client(app)
    c.accounts = wired.accounts
    return c


class _Client:
    """In-process ASGI calls on ONE event loop — no TestClient portal thread.

    The independent review (2026-10-13) saw TestClient stall on its first request in another
    environment, waiting in the AnyIO portal. httpx's ASGITransport drives the same app, real
    routes and real dependency, without that thread. Each call is bounded by a timeout so a
    stall fails the test instead of hanging the suite.
    """
    def __init__(self, app):
        self.app = app

    def post(self, path, headers=None):
        async def go():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                         base_url="http://test") as c:
                return await asyncio.wait_for(c.post(path, headers=headers or {}), timeout=20)
        return asyncio.run(go())


def _bearer(**claims):
    base = {"sub": "someone", "jti": str(uuid.uuid4()), "exp": int(time.time()) + 300}
    base.update(claims)
    return {"Authorization": "Bearer " + jwt.encode(base, SECRET, algorithm="HS256")}


@pytest.mark.parametrize("path", ROUTES)
def test_unauthenticated_is_401(client, path):
    assert client.post(path).status_code == 401


@pytest.mark.parametrize("path", ROUTES)
def test_an_ordinary_signed_in_user_is_403(client, path):
    client.accounts["alice"] = types.SimpleNamespace(is_active=True, role="USER")
    r = client.post(path, headers=_bearer(sub="alice", role="user"))
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("path", ROUTES)
def test_a_demoted_admin_whose_token_still_says_admin_is_403(client, path):
    """The role is re-read from the database, not trusted from the token."""
    client.accounts["bob"] = types.SimpleNamespace(is_active=True, role="USER")
    assert client.post(path, headers=_bearer(sub="bob", role="admin")).status_code == 403


@pytest.mark.parametrize("path", ROUTES)
def test_an_active_admin_reaches_the_route(client, path):
    client.accounts["root"] = types.SimpleNamespace(is_active=True, role="ADMIN")
    r = client.post(path, headers=_bearer(sub="root", role="admin"))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("path", ROUTES)
def test_the_scheduler_service_token_reaches_the_route(client, path):
    """The scheduled refresh and the weekly tunes must keep working."""
    r = client.post(path, headers=_bearer(sub="scheduler", svc=["model"]))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("path", ROUTES)
def test_a_service_token_without_the_model_scope_is_403(client, path):
    assert client.post(path, headers=_bearer(sub="svc", svc=["other"])).status_code == 403


def test_both_internal_callers_send_the_model_scoped_service_token():
    """The callers found by audit. If one sent no token, or a token without `model`, the gate
    would silently break a scheduled job instead of failing loudly here."""
    sched = (_ROOT / "services/market-data/src/services/scheduler.py").read_text()
    admin = (_ROOT / "services/market-data/src/api/admin.py").read_text()
    assert '"svc": ["model"]' in sched
    # scheduler: every POST to these routes goes through _post(), which attaches _service_token().
    post_body = sched[sched.index("def _post(url: str"):]
    post_body = post_body[:post_body.index("\ndef ", 1)]
    assert "_service_token()" in post_body
    for route in ("/rankings/refresh", "/rankings/tune_kscore_weights",
                  "/rankings/tune_kscore_curve"):
        for line in [ln for ln in sched.splitlines() if route in ln and "ranking_engine_url" in ln]:
            assert line.strip().startswith("_post("), f"scheduler calls {route} without _post: {line}"
    # admin.py: the add-stock refresh builds its headers from _service_token().
    seg = admin[admin.index("/rankings/refresh") - 600: admin.index("/rankings/refresh")]
    assert "_service_token()" in seg and "Authorization" in seg



# ── THREAD-FREE proof (re-review 2026-10-14): no HTTP, no worker thread ──────────────────

def _dependency_calls(dependant):
    out = []
    for d in dependant.dependencies:
        out.append(d.call)
        out += _dependency_calls(d)
    return out


@pytest.mark.parametrize("path", ROUTES)
def test_each_route_is_wired_to_the_real_admin_dependency(wired, path):
    """Introspects the REAL FastAPI route objects — not source text."""
    route = next(r for r in wired.routes.router.routes
                 if getattr(r, "path", None) == path and "POST" in r.methods)
    calls = _dependency_calls(route.dependant)
    assert wired.jwt_auth.require_model_admin in calls
    assert wired.jwt_auth.get_current_username not in calls


@pytest.mark.parametrize("who,claims,row,expect", [
    ("signed out", None, None, 401),
    ("ordinary user", {"sub": "alice", "role": "user"}, ("alice", "USER"), 403),
    ("demoted admin", {"sub": "bob", "role": "admin"}, ("bob", "USER"), 403),
    ("disabled admin", {"sub": "dan", "role": "admin"}, ("dan", "ADMIN", False), 403),
    ("unscoped service", {"sub": "svc", "svc": ["other"]}, None, 403),
    ("active admin", {"sub": "root", "role": "admin"}, ("root", "ADMIN"), "root"),
    ("scheduler", {"sub": "scheduler", "svc": ["model"]}, None, "scheduler"),
])
def test_the_dependency_decides_every_role_without_http(wired, who, claims, row, expect):
    from fastapi import HTTPException
    if row:
        wired.accounts[row[0]] = types.SimpleNamespace(
            is_active=row[2] if len(row) > 2 else True, role=row[1])
    auth = _bearer(**claims)["Authorization"] if claims else None
    if isinstance(expect, int):
        with pytest.raises(HTTPException) as e:
            wired.jwt_auth.require_model_admin(auth)
        assert e.value.status_code == expect, who
    else:
        assert wired.jwt_auth.require_model_admin(auth) == expect, who
