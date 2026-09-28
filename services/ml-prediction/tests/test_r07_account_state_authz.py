"""R07 (2026-09-24 follow-up audit): a disabled administrator's existing token still worked.

DA-08 closed the escalation — an ordinary signed-in account can no longer retrain the platform.
It left the ACCOUNT-STATE half open. `require_model_admin` accepted the token's `role` claim
after the signature and blacklist checks and never read the live row, while `toggle_user`
changes only `is_active` and `delete_user` does not invalidate anything. So disabling or
deleting an administrator did nothing to the token they already held: it kept authorizing
global retraining, Optuna tuning and `resweep_suppression?dry_run=false` until it expired.

The audit's phrasing of the bar: "Disablement must take effect before JWT expiry."

TWO LAYERS, because neither does the other's job.

  LIVE ACCOUNT STATE — require_model_admin re-reads the User row on every model mutation and
  re-checks existence, is_active and role. This is the authoritative layer. It is the only one
  that catches ROLE DEMOTION (admin → user), which revocation does not model at all, and it
  needs no Redis. It is a per-request database read, which is why it is scoped to routes that
  are rare and admin-triggered.

  REVOCATION — disabling, deleting or resetting the password on an account stamps a per-user
  "revoked before T" marker; tokens issued before T are rejected on EVERY route, not only the
  privileged ones. One Redis read, the same order as the JTI blacklist already on that path.
  It requires `iat` on the token, which _make_token now sets.

NOT a capability matrix. The audit is right that one belongs here; `svc` gained named scopes
(so the risk-snapshot and signal-engine principals can no longer retrain anything) but a real
permissions model is still future work.

STILL OPEN, deliberately and stated in the code: a self-service password change does NOT revoke
the user's other sessions, because doing so would log them out of the session they are standing
in and there is no token-refresh path on the frontend to hand them a new one.
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
_ROOT = Path(__file__).resolve().parents[3]

from tests.test_da08_model_authz import (  # noqa: E402  (one fake DB, not two)
    ACCOUNTS, _FakeRow, _load_real_jwt_auth,
)


class _FakeRedis:
    """Enough of Redis for the revocation marker, with a switch for "unreachable"."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.down = False

    def _guard(self):
        if self.down:
            raise RuntimeError("redis down")

    def get(self, key):
        self._guard()
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self._guard()
        self.store[key] = value

    def exists(self, _key):
        self._guard()
        return 0


@pytest.fixture
def auth(monkeypatch):
    """The real jwt_auth, with a real signing secret and a controllable fake Redis."""
    mod = _load_real_jwt_auth()
    monkeypatch.setattr(mod._settings, "jwt_secret", SECRET, raising=False)
    fake = _FakeRedis()
    monkeypatch.setattr(mod, "get_redis", lambda: fake)
    mod._BLACKLIST_MEM.clear()
    mod.redis = fake          # convenience handle for the tests
    ACCOUNTS.clear()
    db = types.ModuleType("db")
    from tests.test_da08_model_authz import _FakeRole, _FakeSession, _FakeUserModel
    db.SessionLocal = lambda: _FakeSession(ACCOUNTS)
    db.User = _FakeUserModel
    db.UserRole = _FakeRole
    monkeypatch.setitem(sys.modules, "db", db)
    return mod


def _token(iat=None, **claims):
    base = {"sub": "someone", "jti": str(uuid.uuid4()), "exp": int(time.time()) + 300}
    if iat is not None:
        base["iat"] = iat
    base.update(claims)
    return "Bearer " + jwt.encode(base, SECRET, algorithm="HS256")


# ── The finding: account state on a privileged route ─────────────────────────

def test_a_disabled_admins_existing_token_no_longer_authorizes_model_changes(auth):
    """THE CORE R07 CASE, in the audit's own words: disablement must take effect before JWT
    expiry. The token is valid, unexpired, unblacklisted and carries role=admin."""
    tok = _token(sub="root", role="admin")
    ACCOUNTS["root"] = _FakeRow(is_active=True, role="ADMIN")
    assert auth.require_model_admin(tok) == "root"          # works while enabled

    ACCOUNTS["root"] = _FakeRow(is_active=False, role="ADMIN")
    with pytest.raises(HTTPException) as e:
        auth.require_model_admin(tok)                        # same token, now refused
    assert e.value.status_code == 403


def test_a_deleted_admins_existing_token_no_longer_authorizes_model_changes(auth):
    tok = _token(sub="root", role="admin")
    ACCOUNTS["root"] = _FakeRow()
    assert auth.require_model_admin(tok) == "root"
    del ACCOUNTS["root"]
    with pytest.raises(HTTPException) as e:
        auth.require_model_admin(tok)
    assert e.value.status_code == 403


def test_a_demoted_admins_existing_token_no_longer_authorizes_model_changes(auth):
    """The case REVOCATION CANNOT COVER. Demotion issues no marker and changes no timestamp —
    only the live row says this account is no longer an admin, which is why the database read
    is the authoritative layer rather than a redundant one."""
    tok = _token(sub="root", role="admin")
    ACCOUNTS["root"] = _FakeRow(role="ADMIN")
    assert auth.require_model_admin(tok) == "root"
    ACCOUNTS["root"] = _FakeRow(role="USER")
    with pytest.raises(HTTPException) as e:
        auth.require_model_admin(tok)
    assert e.value.status_code == 403


def test_the_role_claim_alone_cannot_grant_access_to_a_nonexistent_account(auth):
    """A correctly-signed token for an account that was never created, or long since removed."""
    with pytest.raises(HTTPException) as e:
        auth.require_model_admin(_token(sub="ghost", role="admin"))
    assert e.value.status_code == 403


def test_an_unreachable_database_denies_rather_than_falling_back_to_the_claim(auth,
                                                                              monkeypatch):
    """FAILS CLOSED. A model mutation is rare and retryable; running one on the authority of a
    token whose account cannot be checked is not. 503, so the caller can tell "try again" from
    "you may not"."""
    ACCOUNTS["root"] = _FakeRow()
    db = sys.modules["db"]

    def boom():
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(db, "SessionLocal", boom)
    with pytest.raises(HTTPException) as e:
        auth.require_model_admin(_token(sub="root", role="admin"))
    assert e.value.status_code == 503


def test_an_active_admin_is_still_allowed(auth):
    ACCOUNTS["root"] = _FakeRow(is_active=True, role="ADMIN")
    assert auth.require_model_admin(_token(sub="root", role="admin")) == "root"


def test_the_lookup_is_case_insensitive_on_the_username(auth):
    """Usernames are stored lowercased; a token minted with different casing must resolve to
    the same row rather than silently finding nothing and denying a real admin."""
    ACCOUNTS["root"] = _FakeRow()
    assert auth.require_model_admin(_token(sub="RooT", role="admin")) == "RooT"


# ── Service principals ───────────────────────────────────────────────────────

def test_a_service_principal_does_not_need_a_user_row(auth):
    """The scheduler is not a user; requiring a row would break every nightly job."""
    assert auth.require_model_admin(_token(sub="scheduler", svc=["model"])) == "scheduler"


def test_a_service_principal_scoped_to_another_job_cannot_mutate_models(auth):
    """R07's narrowing: a leaked risk-snapshot or signal-engine token must not retrain the
    platform just because it is signed by the same secret."""
    for scope in (["risk-snapshots"], ["signal-engine"], "signal-engine", ["ingest"], []):
        with pytest.raises(HTTPException) as e:
            auth.require_model_admin(_token(sub="svc", svc=scope))
        assert e.value.status_code == 403, scope


def test_a_legacy_unscoped_service_token_still_works(auth):
    """`svc: True` is the DA-08 shape. Rejecting it would break the scheduler the moment this
    deploys — a worse outcome than the narrowing it delays. These re-mint on container start."""
    assert auth.require_model_admin(_token(sub="scheduler", svc=True)) == "scheduler"


def test_a_scope_list_containing_model_among_others_is_accepted(auth):
    assert auth.require_model_admin(_token(sub="s", svc=["ingest", "model"])) == "s"


# ── Revocation, on every route ───────────────────────────────────────────────

def test_a_token_issued_before_a_revocation_is_rejected(auth):
    now = int(time.time())
    tok = _token(sub="alice", role="user", iat=now - 100)
    assert auth.get_current_username(tok) == "alice"
    auth.revoke_user_tokens("alice")
    with pytest.raises(HTTPException) as e:
        auth.get_current_username(tok)
    assert e.value.status_code == 401


def test_a_token_issued_in_the_SAME_SECOND_as_the_revocation_is_rejected(auth):
    """FOUND 2026-09-28 (pre-deployment audit). `iat` and the marker are both integer seconds,
    so a token minted at the instant of revocation is indistinguishable from one minted just
    before it — and `<` let it through. The whole point of a revocation is that everything up
    to that moment stops working, so the comparison is `<=`. The cost is that a user who logs
    in within the same second of their own password reset needs one extra login, which is the
    correct direction to be wrong in."""
    now = int(time.time())
    auth.redis.store["auth:user_revoked_at:alice"] = str(now)
    with pytest.raises(HTTPException):
        auth.get_current_username(_token(sub="alice", role="user", iat=now))


def test_a_token_issued_after_a_revocation_still_works(auth):
    """Re-enabling an account, or simply logging back in, must work. A marker that killed every
    future token too would make disablement permanent and undebuggable."""
    auth.revoke_user_tokens("alice")
    fresh = _token(sub="alice", role="user", iat=int(time.time()) + 1)
    assert auth.get_current_username(fresh) == "alice"


def test_a_token_with_no_iat_is_rejected_once_its_account_is_revoked(auth):
    """Tokens minted before R07 carry no `iat`, so they cannot be shown to postdate the
    revocation. Fails closed — and only for accounts somebody actually disabled."""
    tok = _token(sub="alice", role="user")
    assert auth.get_current_username(tok) == "alice"      # unaffected while nothing is revoked
    auth.revoke_user_tokens("alice")
    with pytest.raises(HTTPException):
        auth.get_current_username(tok)


def test_revocation_applies_to_privileged_routes_too(auth):
    ACCOUNTS["root"] = _FakeRow()
    tok = _token(sub="root", role="admin", iat=int(time.time()) - 100)
    assert auth.require_model_admin(tok) == "root"
    auth.revoke_user_tokens("root")
    with pytest.raises(HTTPException) as e:
        auth.require_model_admin(tok)
    assert e.value.status_code == 401


def test_revoking_one_account_does_not_affect_another(auth):
    auth.revoke_user_tokens("alice")
    assert auth.get_current_username(_token(sub="bob", role="user")) == "bob"


def test_a_service_principal_is_not_affected_by_a_user_revocation(auth):
    """Service tokens have no user row. A marker for a same-named account must not disable the
    scheduler — the inverse of the DA-08 trap, where a user named "scheduler" gained authority."""
    auth.revoke_user_tokens("scheduler")
    assert auth.require_model_admin(_token(sub="scheduler", svc=["model"])) == "scheduler"


def test_an_unreadable_marker_allows_rather_than_logging_the_platform_out(auth):
    """A DELIBERATE fail-open, matching the JTI blacklist's existing behaviour for unknown
    JTIs. It is safe to state plainly because the database layer — which needs no Redis — is
    what actually guards the privileged routes."""
    auth.revoke_user_tokens("alice")
    tok = _token(sub="alice", role="user", iat=int(time.time()) - 100)
    with pytest.raises(HTTPException):
        auth.get_current_username(tok)
    auth.redis.down = True
    assert auth.get_current_username(tok) == "alice"


def test_a_corrupt_marker_denies(auth):
    """An unparseable value is not a reason to honour the token."""
    auth.redis.store["auth:user_revoked_at:alice"] = "not-a-timestamp"
    with pytest.raises(HTTPException):
        auth.get_current_username(_token(sub="alice", role="user", iat=int(time.time())))


def test_revoke_reports_failure_rather_than_claiming_success(auth):
    """The caller logs this and tells the admin. A disable that silently failed to cut off live
    tokens must not read as a clean success."""
    auth.redis.down = True
    assert auth.revoke_user_tokens("alice") is False
    auth.redis.down = False
    assert auth.revoke_user_tokens("alice") is True


def test_the_marker_outlives_any_user_token(auth):
    """If the marker expires first, the tokens it was set to kill quietly become valid again."""
    auth.revoke_user_tokens("alice")
    assert auth._USER_REVOKE_TTL >= 400 * 86400


# ── The other half must actually be WIRED IN ─────────────────────────────────

def test_minted_user_tokens_carry_iat():
    """Without `iat` every revocation check has to fail closed forever, which looks like a
    working guard while actually being a broken one."""
    src = (_ROOT / "services/market-data/src/api/auth.py").read_text()
    body = src[src.index("def _make_token("):]
    body = body[:body.index("\ndef ")]
    assert '"iat"' in body, "_make_token must stamp the issue time"


def test_disable_and_delete_and_password_reset_all_revoke():
    """Each of these is a point where somebody decided a holder should lose access. A route
    that changes the row but leaves the tokens alive makes the decision cosmetic."""
    src = (_ROOT / "services/market-data/src/api/auth.py").read_text()
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    for fn in ("def toggle_user(", "def delete_user(",
               "def admin_reset_password(", "def reset_password_public("):
        body = code[code.index(fn):]
        end = body.find("\n@router.")
        body = body[:end] if end != -1 else body
        assert "revoke_user_tokens(" in body, f"{fn} does not revoke"


def test_the_revocation_key_has_one_definition():
    """auth.py calls the shared helper instead of writing the key itself — two halves of a
    security check that can drift on the shape of a key is how this kind of guard dies."""
    src = (_ROOT / "services/market-data/src/api/auth.py").read_text()
    assert "auth:user_revoked_at" not in src, "auth.py must not hand-build the marker key"
    assert "from common.jwt_auth import revoke_user_tokens" in src


def test_self_service_password_change_deliberately_does_not_revoke():
    """PINS A KNOWN GAP so it is not mistaken for an oversight. Someone changing their password
    because they think it is compromised does NOT end the attacker's other sessions. Closing it
    means returning a fresh token from that route and teaching the client to swap it in. If this
    test starts failing, that work was done — delete it."""
    src = (_ROOT / "services/market-data/src/api/auth.py").read_text()
    body = src[src.index("def change_password("):]
    body = body[:body.index("\n# ── Admin endpoints")]
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    assert "revoke_user_tokens(" not in code
