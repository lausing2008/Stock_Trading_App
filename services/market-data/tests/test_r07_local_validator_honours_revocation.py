"""R07, found by the 2026-09-28 pre-deployment audit: this service's OWN validator ignored the
revocation marker.

R07 added a per-user "revoked before T" marker and enforced it in `shared/common/jwt_auth.py`.
market-data has its own `get_current_user`, which decodes the JWT directly and never consulted
it. That validator guards most of the platform's endpoints.

It was not a total bypass — it re-reads the live row and rejects a user who is disabled or
deleted, which covers the headline case. What it missed is the PASSWORD-RESET path: the admin
reset and the public reset both call `revoke_user_tokens()`, and an attacker holding a token
issued before that reset kept full access to every market-data endpoint until the token expired.
The reset was cosmetic for up to jwt_expire_days.

This is the same shape R07 was itself fixing — a guard placed on one route while another route
goes around it — which is why the finding is worth its own file rather than a line in a diff.
"""
import sys
import types
from unittest.mock import MagicMock

import pytest

from src.api import auth as A


class _Creds:
    def __init__(self, token="decoded-by-the-stub-below"):
        self.credentials = token


@pytest.fixture
def account(monkeypatch):
    """A live, ACTIVE, existing user — so nothing but the marker can reject the request."""
    user = types.SimpleNamespace(username="alice", is_active=True,
                                 role=A.UserRole.ADMIN, tier=A.UserTier.ADVANCED)
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = user
    monkeypatch.setattr(A.jwt, "decode",
                        lambda *a, **k: {"sub": "alice", "jti": "j1", "iat": 100})
    monkeypatch.setattr(A, "_is_blacklisted", lambda _jti: False)
    return user, session


def test_an_active_user_is_accepted_when_nothing_is_revoked(account, monkeypatch):
    user, session = account
    monkeypatch.setattr(A, "user_tokens_revoked", lambda _p: False)
    assert A.get_current_user(_Creds(), session) is user


def test_a_token_predating_a_password_reset_is_rejected_here_too(account, monkeypatch):
    """THE FINDING. The account is active and exists, so every pre-existing check passes; only
    the marker says this token must stop working."""
    _user, session = account
    monkeypatch.setattr(A, "user_tokens_revoked", lambda _p: True)
    with pytest.raises(A.HTTPException) as e:
        A.get_current_user(_Creds(), session)
    assert e.value.status_code == 401


def test_the_revocation_check_runs_before_the_user_row_is_loaded_is_not_required(account,
                                                                                 monkeypatch):
    """Ordering is deliberately NOT asserted. The marker check is cheap and the row lookup is
    the authority on is_active; either order is correct, and pinning one would make a future
    reordering look like a regression. What matters is that both run."""
    _user, session = account
    monkeypatch.setattr(A, "user_tokens_revoked", lambda _p: True)
    with pytest.raises(A.HTTPException):
        A.get_current_user(_Creds(), session)


def test_a_disabled_user_is_still_rejected_by_the_pre_existing_row_check(account, monkeypatch):
    """The half that already worked must keep working — this fix adds a check, it does not
    replace one."""
    user, session = account
    user.is_active = False
    monkeypatch.setattr(A, "user_tokens_revoked", lambda _p: False)
    with pytest.raises(A.HTTPException) as e:
        A.get_current_user(_Creds(), session)
    assert e.value.status_code == 401


def test_a_deleted_user_is_still_rejected(account, monkeypatch):
    _user, session = account
    session.execute.return_value.scalar_one_or_none.return_value = None
    monkeypatch.setattr(A, "user_tokens_revoked", lambda _p: False)
    with pytest.raises(A.HTTPException):
        A.get_current_user(_Creds(), session)


def test_both_validators_consult_the_same_helper():
    """One marker, one implementation. A second copy of the rule here is how the two halves of
    a security check drift into disagreeing about what "revoked" means."""
    import inspect

    src = inspect.getsource(A)
    assert "from common.jwt_auth import revoke_user_tokens, user_tokens_revoked" in src
    assert "auth:user_revoked_at" not in src, "this module must not hand-build the marker key"
    body = inspect.getsource(A.get_current_user)
    assert "user_tokens_revoked(payload)" in body


def test_the_stub_in_conftest_does_not_fail_open_in_the_dangerous_direction():
    """A bare MagicMock attribute returns a truthy Mock, so an unstubbed `user_tokens_revoked`
    would report EVERY token revoked and 401 the whole suite — loud, but for the wrong reason.
    Stubbed to the real default instead, which is what makes the tests above meaningful."""
    import common.jwt_auth as j
    assert j.user_tokens_revoked({"sub": "anyone"}) is False
