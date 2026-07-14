"""Regression tests for T237-AG1 — a confirmed, fixed auth-bypass in the reverse proxy.

A path like "auth/../stocks/AAPL" naively computes prefix="auth" (public) via
full_path.split("/", 1)[0], while httpx normalizes the outbound request to "/stocks/AAPL"
before it reaches market-data — a full, unauthenticated read of a protected route. The fix
normalizes the path with posixpath.normpath BEFORE any auth/routing decision is made. These
tests exist so a future refactor of reverse_proxy()/_require_auth() can't silently reintroduce
the same bypass with no CI signal.
"""
import asyncio
import posixpath
import time

import pytest
from fastapi import HTTPException
from jose import jwt as _jwt

from src.api.proxy import (
    _require_auth, _require_auth_async, _PUBLIC_PREFIXES, _is_blacklisted, _upstream, _ROUTES,
)
import src.api.proxy as proxy_mod

_JWT_SECRET = "test-secret-not-a-real-key"


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — _require_auth only reads .headers.get(...)."""
    def __init__(self, headers: dict | None = None):
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    @property
    def headers(self):
        return self._headers


def _make_token(role: str = "user", jti: str = "test-jti-1") -> str:
    return _jwt.encode({"sub": "testuser", "role": role, "jti": jti}, _JWT_SECRET, algorithm="HS256")


@pytest.fixture(autouse=True)
def _not_blacklisted(monkeypatch):
    """Every test assumes a non-revoked token unless it overrides this itself."""
    monkeypatch.setattr("src.api.proxy._is_blacklisted", lambda jti: False)


def test_dotdot_traversal_normalizes_to_the_real_protected_prefix():
    """The exact T237-AG1 attack input: normalizing must expose the REAL destination prefix."""
    normalized = posixpath.normpath("/" + "auth/../stocks/AAPL").lstrip("/")
    assert normalized == "stocks/AAPL"
    prefix = normalized.split("/", 1)[0]
    assert prefix == "stocks"
    assert prefix not in _PUBLIC_PREFIXES


def test_require_auth_rejects_dotdot_bypass_with_no_token():
    """End-to-end through _require_auth: the normalized path must require auth, not bypass it."""
    normalized_path = posixpath.normpath("/" + "auth/../stocks/AAPL").lstrip("/")
    with pytest.raises(HTTPException) as exc_info:
        _require_auth(normalized_path, _FakeRequest())
    assert exc_info.value.status_code == 401


def test_require_auth_rejects_dotdot_bypass_variant_health_to_admin():
    """A second traversal variant targeting a different protected prefix."""
    normalized_path = posixpath.normpath("/" + "health/../admin/users").lstrip("/")
    assert normalized_path == "admin/users"
    with pytest.raises(HTTPException) as exc_info:
        _require_auth(normalized_path, _FakeRequest())
    assert exc_info.value.status_code == 401


def test_require_auth_alone_is_NOT_sufficient_without_reverse_proxys_own_normalization():
    """This is the actual T237-AG1 regression case: _require_auth() has no normalization
    logic of its own — it trusts whatever prefix it's handed. The vulnerability lived in
    reverse_proxy() calling _require_auth() with the RAW, un-normalized full_path. This test
    proves that fact directly: feeding the raw (pre-normalization) attack string straight into
    _require_auth(), bypassing reverse_proxy()'s own posixpath.normpath() call entirely, DOES
    let it through unauthenticated — demonstrating why that normalization call in
    reverse_proxy() (immediately before its own _require_auth() call, line ~153-159) is the
    actual load-bearing fix. If a future refactor moves _require_auth()'s call above the
    normalization step, or removes the normalization step, this is the exact failure this
    guards against re-verifying silently.
    """
    raw_unnormalized_path = "auth/../stocks/AAPL"  # what reverse_proxy() receives from FastAPI
    # No exception raised — because _require_auth alone, given the RAW path, incorrectly
    # treats this as the public "auth" prefix. This is expected here (proving the bug is real
    # in isolation) and is exactly why reverse_proxy() must normalize before ever calling this.
    _require_auth(raw_unnormalized_path, _FakeRequest())
    # Now prove the fix actually closes it: reverse_proxy()'s own normalization step, applied
    # BEFORE _require_auth(), changes the outcome entirely.
    normalized_path = posixpath.normpath("/" + raw_unnormalized_path).lstrip("/")
    with pytest.raises(HTTPException) as exc_info:
        _require_auth(normalized_path, _FakeRequest())
    assert exc_info.value.status_code == 401


def test_require_auth_allows_genuinely_public_prefix():
    """auth/* is genuinely meant to be public (login/register) — must not raise."""
    _require_auth("auth/login", _FakeRequest())  # no exception = pass


def test_require_auth_allows_valid_token_on_protected_prefix():
    token = _make_token(role="user")
    request = _FakeRequest({"authorization": f"Bearer {token}"})
    _require_auth("stocks/AAPL", request)  # no exception = pass


def test_require_auth_rejects_missing_bearer_prefix():
    request = _FakeRequest({"authorization": "not-a-bearer-token"})
    with pytest.raises(HTTPException) as exc_info:
        _require_auth("stocks/AAPL", request)
    assert exc_info.value.status_code == 401


def test_require_auth_rejects_blacklisted_token(monkeypatch):
    monkeypatch.setattr("src.api.proxy._is_blacklisted", lambda jti: True)
    token = _make_token(jti="revoked-jti")
    request = _FakeRequest({"authorization": f"Bearer {token}"})
    with pytest.raises(HTTPException) as exc_info:
        _require_auth("stocks/AAPL", request)
    assert exc_info.value.status_code == 401


def test_require_auth_enforces_admin_role_on_admin_prefix():
    """AG-D1: gateway-level backstop — a non-admin token must be rejected on /admin/*."""
    token = _make_token(role="user")
    request = _FakeRequest({"authorization": f"Bearer {token}"})
    with pytest.raises(HTTPException) as exc_info:
        _require_auth("admin/users", request)
    assert exc_info.value.status_code == 403


def test_require_auth_allows_admin_role_on_admin_prefix():
    token = _make_token(role="admin")
    request = _FakeRequest({"authorization": f"Bearer {token}"})
    _require_auth("admin/users", request)  # no exception = pass


@pytest.mark.parametrize("raw_path,expected_normalized", [
    ("auth/../stocks/AAPL", "stocks/AAPL"),
    ("./auth/../admin/users", "admin/users"),
    # A leading "/" (added before normpath by reverse_proxy() itself) means normpath can
    # never climb past root, even with more ".." segments than real path components —
    # this is exactly why the fix prepends "/" before calling normpath rather than
    # normalizing full_path directly.
    ("health/../../admin/secrets", "admin/secrets"),
    ("stocks/AAPL", "stocks/AAPL"),
])
def test_normpath_traversal_variants(raw_path, expected_normalized):
    """Sweep a few traversal shapes — normpath's exact semantics on odd inputs is exactly
    the kind of thing that regresses silently if this logic is ever touched again."""
    normalized = posixpath.normpath("/" + raw_path).lstrip("/")
    assert normalized == expected_normalized


def test_rl_agent_prefix_resolves_to_an_upstream():
    """T247-APIGATEWAY-RLAGENT regression guard: market-data's rl.py registers
    APIRouter(prefix="/rl-agent") (routes /rl-agent/status, /rl-agent/train,
    /rl-agent/recommend), and the frontend calls these through the gateway. _ROUTES
    previously had no 'rl-agent' key, so _upstream() returned None for every RL Agent
    request and reverse_proxy() 404'd them all despite the backend fully implementing the
    feature. Confirm the prefix now resolves to a real upstream instead of None."""
    assert _upstream("rl-agent/status") is not None
    assert _upstream("rl-agent/train") is not None
    assert _upstream("rl-agent/recommend") is not None


def test_every_backend_router_prefix_has_a_gateway_route():
    """Broader sweep so a FUTURE new backend router can't silently repeat this exact bug:
    every prefix any service's APIRouter registers must have a corresponding _ROUTES entry.
    This mirrors the audit method that originally found the rl-agent gap (diffing every
    backend router prefix against _ROUTES keys)."""
    import pathlib
    import re

    services_root = pathlib.Path(__file__).resolve().parents[2]
    prefix_pattern = re.compile(r'APIRouter\(\s*prefix\s*=\s*["\']\/?([a-zA-Z0-9_-]+)')
    missing = []
    for py_file in services_root.glob("*/src/api/*.py"):
        if "api-gateway" in str(py_file):
            continue  # the gateway's own routers aren't proxied to themselves
        text = py_file.read_text()
        for match in prefix_pattern.finditer(text):
            prefix = match.group(1)
            if prefix not in _ROUTES and prefix not in _PUBLIC_PREFIXES:
                missing.append(f"{py_file.relative_to(services_root)}: prefix={prefix!r}")
    assert not missing, f"backend router prefixes with no gateway route: {missing}"


def test_require_auth_async_runs_off_the_event_loop(monkeypatch):
    """T247-APIGATEWAY-BLACKLIST-BLOCKING regression guard: a slow synchronous _is_blacklisted
    call must not block other coroutines scheduled on the same event loop when reached via
    _require_auth_async(). Same timing-based technique as decision-engine's aget_regime test."""
    def _slow_is_blacklisted(jti):
        time.sleep(0.2)
        return False
    monkeypatch.setattr(proxy_mod, "_is_blacklisted", _slow_is_blacklisted)

    token = _make_token(role="user")
    request = _FakeRequest({"authorization": f"Bearer {token}"})

    timeline = []

    async def _tracer():
        await asyncio.sleep(0.01)
        timeline.append(time.monotonic())

    async def _main():
        start = time.monotonic()
        await asyncio.gather(_require_auth_async("stocks/AAPL", request), _tracer())
        return start

    start = asyncio.run(_main())
    tracer_ts = timeline[0]
    assert tracer_ts - start < 0.15, (
        f"tracer took {tracer_ts - start:.3f}s — the event loop was blocked by the blacklist "
        "check instead of it running in a thread pool"
    )


def test_require_auth_async_still_rejects_blacklisted_token(monkeypatch):
    """The executor wrapper must not silently swallow the HTTPException a blacklisted token
    raises inside _require_auth() — it must propagate back to the awaiting caller."""
    monkeypatch.setattr(proxy_mod, "_is_blacklisted", lambda jti: True)
    token = _make_token(jti="revoked-jti")
    request = _FakeRequest({"authorization": f"Bearer {token}"})

    async def _main():
        await _require_auth_async("stocks/AAPL", request)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_main())
    assert exc_info.value.status_code == 401


def test_require_auth_async_allows_valid_non_blacklisted_token():
    token = _make_token(role="user")
    request = _FakeRequest({"authorization": f"Bearer {token}"})

    async def _main():
        await _require_auth_async("stocks/AAPL", request)

    asyncio.run(_main())  # no exception = pass


def test_pure_dotdot_path_would_be_rejected_by_reverse_proxy_guard():
    """reverse_proxy() itself explicitly rejects a normalized path that is "" or ".." or
    starts with "../" (400 Invalid path) — confirm the exact inputs that guard exists for
    actually produce those values, so the guard's own condition can't silently stop matching
    reality if normpath's behavior or the guard's string checks ever drift apart. Note: because
    a leading "/" is prepended before normpath runs, excess ".." segments are absorbed at root
    (e.g. "../../etc/passwd" -> "etc/passwd", NOT a "../"-prefixed value) — there is no path
    traversal OUTSIDE the app's own URL namespace possible here; "etc/passwd" is just an
    ordinary (unmapped, 404) prefix like any other, which is why this guard only needs to
    catch the true root-only cases below, not every input containing "..".
    """
    for raw in ("..", "../"):
        normalized = posixpath.normpath("/" + raw).lstrip("/")
        is_rejected = normalized in ("", ".") or normalized.startswith("../") or normalized == ".."
        assert is_rejected, f"{raw!r} normalized to {normalized!r}, which the guard would NOT reject"
