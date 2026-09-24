"""DA-08: authentication was being used as authorization for global model mutation.

THE DEFECT. Every model-mutation route on ml-prediction guarded itself with
`get_current_username`, which establishes only that SOMEBODY signed in. The gateway adds a role
check for the "/admin" prefix alone, so "/ml" never received one. Any ordinary authenticated
account could therefore trigger global retraining, Optuna tuning, or
`resweep_suppression?dry_run=false` — actions that mutate shared model state for every user of
the platform, and consume hours of compute doing it.

THE PART THAT MAKES THE FIX NON-OBVIOUS. The scheduler must keep working, and it is not a user:
it holds a 365-day service token with sub="scheduler" and no role claim. Granting access by
trusting that `sub` STRING would be the wrong fix, because a user account named "scheduler"
would then inherit it — swapping an authorization hole for an impersonation one. Service tokens
instead carry an explicit `svc` capability claim, which only a holder of the signing secret can
mint.

SCOPE, stated so it is not mistaken for more than it is: this closes the open door on mutation.
It is not the capability matrix the audit recommends, and the two expensive READ routes
(/feature_ablation, /walkforward) are deliberately left on plain authentication — they compute
hard but change nothing for anyone else. Rate-limiting expensive reads is a real concern and a
separate one.
"""
import importlib.util
import sys
import time
import types
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from jose import jwt

SECRET = "test-signing-secret"

# This suite stubs the whole `common` package, so `common.jwt_auth` is a MagicMock whose
# require_model_admin() returns a truthy Mock for EVERY caller — an authorization test against
# it would pass no matter who asked. The real module is loaded here instead. Its own imports
# (common.config, common.redis_client) stay stubbed: the signing secret is injected below and
# the blacklist lookup is patched, so nothing reaches Redis.
_ROOT = Path(__file__).resolve().parents[3]


def _load_real_jwt_auth():
    spec = importlib.util.spec_from_file_location(
        "real_jwt_auth", _ROOT / "shared" / "common" / "jwt_auth.py")
    mod = importlib.util.module_from_spec(spec)
    # jwt_auth does `from common.config import get_settings` at import time; give it a real
    # settings object so `_settings.jwt_secret` is a string rather than a Mock.
    fake_cfg = types.ModuleType("common.config")
    fake_cfg.get_settings = lambda: types.SimpleNamespace(jwt_secret=SECRET)
    fake_redis = types.ModuleType("common.redis_client")
    fake_redis.get_redis = lambda: (_ for _ in ()).throw(RuntimeError("no redis in tests"))
    prev = {k: sys.modules.get(k) for k in ("common.config", "common.redis_client")}
    sys.modules["common.config"] = fake_cfg
    sys.modules["common.redis_client"] = fake_redis
    try:
        spec.loader.exec_module(mod)
    finally:
        for k, v in prev.items():
            if v is not None:
                sys.modules[k] = v
    return mod


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    jwt_auth = _load_real_jwt_auth()
    monkeypatch.setattr(jwt_auth._settings, "jwt_secret", SECRET, raising=False)
    # This suite is about authorization, not revocation; the blacklist path needs Redis.
    monkeypatch.setattr(jwt_auth, "_check_blacklist", lambda _jti: False)
    return jwt_auth


def _token(**claims):
    base = {"sub": "someone", "jti": str(uuid.uuid4()), "exp": int(time.time()) + 300}
    base.update(claims)
    return "Bearer " + jwt.encode(base, SECRET, algorithm="HS256")


# ── Who is refused ───────────────────────────────────────────────────────────

def test_an_ordinary_authenticated_user_cannot_mutate_models(_auth):
    """The finding itself: a valid login was sufficient to retrain every model on the platform."""
    with pytest.raises(HTTPException) as e:
        _auth.require_model_admin(_token(sub="regular_user", role="user"))
    assert e.value.status_code == 403


def test_a_user_with_no_role_claim_is_refused(_auth):
    """Older tokens predate the role claim. Absence must not read as permission."""
    with pytest.raises(HTTPException) as e:
        _auth.require_model_admin(_token(sub="legacy_user"))
    assert e.value.status_code == 403


def test_an_advanced_tier_user_is_still_not_an_admin(_auth):
    """Tier gates FEATURES; role gates AUTHORITY. Conflating them is how a paid tier quietly
    becomes an admin."""
    with pytest.raises(HTTPException) as e:
        _auth.require_model_admin(_token(sub="paid_user", role="user", tier="ADVANCED"))
    assert e.value.status_code == 403


def test_a_user_merely_NAMED_like_a_service_is_refused(_auth):
    """The reason the capability is a signed CLAIM and not the `sub` string: granting access to
    sub=="scheduler" would let anyone who registers that username retrain the platform."""
    with pytest.raises(HTTPException) as e:
        _auth.require_model_admin(_token(sub="scheduler", role="user"))
    assert e.value.status_code == 403


def test_a_forged_service_claim_signed_with_the_wrong_key_is_rejected(_auth):
    """The claim is only as good as the signature over it."""
    forged = "Bearer " + jwt.encode(
        {"sub": "scheduler", "svc": True, "jti": str(uuid.uuid4()), "exp": int(time.time()) + 300},
        "not-the-real-secret", algorithm="HS256",
    )
    with pytest.raises(HTTPException) as e:
        _auth.require_model_admin(forged)
    assert e.value.status_code == 401


def test_missing_or_malformed_credentials_are_401_not_403(_auth):
    """401 means "I do not know who you are"; 403 means "I do, and no". Collapsing them makes an
    expired session look like a permissions problem."""
    for bad in (None, "", "Token abc", "Bearer "):
        with pytest.raises(HTTPException) as e:
            _auth.require_model_admin(bad)
        assert e.value.status_code == 401, bad


def test_an_expired_token_is_rejected(_auth):
    expired = "Bearer " + jwt.encode(
        {"sub": "admin", "role": "admin", "jti": str(uuid.uuid4()), "exp": int(time.time()) - 10},
        SECRET, algorithm="HS256",
    )
    with pytest.raises(HTTPException) as e:
        _auth.require_model_admin(expired)
    assert e.value.status_code == 401


def test_a_revoked_token_is_rejected_even_for_an_admin(_auth, monkeypatch):
    monkeypatch.setattr(_auth, "_check_blacklist", lambda _jti: True)
    with pytest.raises(HTTPException) as e:
        _auth.require_model_admin(_token(sub="admin", role="admin"))
    assert e.value.status_code == 401


def test_a_token_without_a_jti_cannot_be_revoked_so_is_refused(_auth):
    no_jti = "Bearer " + jwt.encode(
        {"sub": "admin", "role": "admin", "exp": int(time.time()) + 300}, SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        _auth.require_model_admin(no_jti)
    assert e.value.status_code == 401


# ── Who is allowed ───────────────────────────────────────────────────────────

def test_an_admin_user_may_mutate_models(_auth):
    assert _auth.require_model_admin(_token(sub="root", role="admin")) == "root"


def test_the_scheduler_service_principal_may_mutate_models(_auth):
    """The weekly tune and nightly retrain must keep working — breaking them to close the hole
    would be trading one outage for another."""
    assert _auth.require_model_admin(_token(sub="scheduler", svc=True)) == "scheduler"


def test_every_service_token_minter_carries_the_capability_claim():
    """A minter that forgets `svc` produces a token that authenticates fine and then fails at
    the privileged call — which surfaces as a broken scheduled job, not as an auth error."""
    root = _ROOT
    for rel in ("services/market-data/src/services/scheduler.py",
                "services/signal-engine/src/api/signals_shared.py",
                "services/market-data/src/api/risk_snapshots.py"):
        src = (root / rel).read_text()
        assert '"svc": True' in src, rel


# ── The routes themselves ────────────────────────────────────────────────────

def test_every_mutation_route_is_guarded_and_every_predict_route_is_not():
    """Pins the BOUNDARY, not a count: a new train_* route added without the guard is exactly
    the regression this finding is about."""
    import re

    src = (Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py").read_text()
    lines = src.split("\n")
    route = None
    guarded, plain = set(), set()
    for line in lines:
        rm = re.match(r'^@router\.(get|post)\("([^"]+)"', line)
        if rm:
            route = rm.group(2)
        if route and "Depends(require_model_admin)" in line:
            guarded.add(route)
        elif route and "Depends(get_current_username)" in line:
            plain.add(route)

    must_be_guarded = {"/train", "/train_all", "/tune", "/tune_all", "/train_meta",
                       "/train_all_ensemble", "/train_all_ensemble_three",
                       "/train_all_horizons", "/resweep_suppression"}
    assert must_be_guarded <= guarded, f"unguarded mutation routes: {must_be_guarded - guarded}"
    # Prediction must stay reachable by ordinary users, or the product stops working.
    assert {"/predict", "/predict_ensemble", "/predict_ensemble_three"} <= plain
    assert not (must_be_guarded & plain)
