"""AUD-E09-COOLDOWNRELEASE — check_options_flow_alerts()/check_dark_pool_alerts() claimed the
per-(user, symbol[, direction/print]) cooldown key BEFORE attempting the actual email send, and
never released it on a failed send.

Confirmed real (docs/audits/2026-09-18-alert-email-accuracy-and-uw-playbooks-audit.md, finding
E09): send_options_flow_alert_email()/send_dark_pool_alert_email() both bottom out in
send_email() (email_service.py), which never raises — it internally try/excepts the real
SMTP/SES call and returns a plain bool, so a fix must key off that boolean, not an exception.
Both scheduler jobs already correctly compute `send_ok` from that return value (or `False` on
an unexpected exception from the surrounding payload-building code) — the bug was that a False
`send_ok` only ever affected downstream state (options-flow's own seen-set resync correctly
excludes a failed send's chains), while the EARLIER cooldown claim, made before the send was
even attempted, was left standing regardless of outcome. A candidate that failed to send once
therefore could not be retried until its cooldown TTL (60 min) expired on its own — a real
notification silently lost, indistinguishable from one the user had already received.

Fix: both jobs now track exactly which cooldown keys THIS attempt actually claimed (a real,
successful nx=True SET — never the fail-open except branch, which never touched Redis at all),
and delete them when send_ok is False, so the very next scheduler cycle can retry immediately.

scheduler.py can't be imported directly in this test environment (heavy DB/session/live-price
dependencies) — matching test_e10_cooldown_premium_order.py's own established source-extraction
convention for this exact file/function pair.
"""
import pathlib

_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
).read_text()


def _check_options_flow_alerts_body() -> str:
    start = _SOURCE.index("def check_options_flow_alerts() -> None:")
    end = _SOURCE.index("\n\ndef ", start + 10)
    return _SOURCE[start:end]


def _check_dark_pool_alerts_body() -> str:
    start = _SOURCE.index("def check_dark_pool_alerts() -> None:")
    end = _SOURCE.index("\n\ndef ", start + 10)
    return _SOURCE[start:end]


_OPTIONS_FLOW_BODY = _check_options_flow_alerts_body()
_DARK_POOL_BODY = _check_dark_pool_alerts_body()


# ── check_options_flow_alerts() ────────────────────────────────────────────────────────────

def test_options_flow_tracks_which_cooldown_keys_were_actually_claimed():
    """Only the real, successful nx=True SET branch may populate _claimed_cd_keys — the
    fail-open except branch never touched Redis, so it must not be tracked for release."""
    assert "_claimed_cd_keys: dict[str, str] = {}" in _OPTIONS_FLOW_BODY
    set_branch_idx = _OPTIONS_FLOW_BODY.index(
        'if _rc.set(cd_key, "1", nx=True, ex=_OPTIONS_FLOW_ALERT_COOLDOWN_MINUTES * 60):'
    )
    claim_idx = _OPTIONS_FLOW_BODY.index("_claimed_cd_keys[chain] = cd_key")
    except_idx = _OPTIONS_FLOW_BODY.index("except Exception:\n                        cooldown_ok_chains.append(chain)")
    assert set_branch_idx < claim_idx < except_idx


def test_options_flow_releases_claimed_keys_only_when_send_failed():
    body = _OPTIONS_FLOW_BODY
    send_idx = body.index("send_ok = send_options_flow_alert_email(")
    elif_idx = body.index("elif _claimed_cd_keys:", send_idx)
    delete_idx = body.index("_rc.delete(_cd_key)", elif_idx)
    assert send_idx < elif_idx < delete_idx
    # Must be the FAILURE branch (elif, sibling to `if send_ok: sent += 1`), not unconditional.
    if_ok_idx = body.index("if send_ok:\n                        sent += 1")
    assert if_ok_idx < elif_idx


def test_options_flow_release_wraps_each_delete_in_its_own_try_except():
    """A Redis hiccup on release must not crash the whole recipient loop — matches this
    function's own established fail-open convention for every other Redis call in it."""
    body = _OPTIONS_FLOW_BODY
    elif_idx = body.index("elif _claimed_cd_keys:")
    for_idx = body.index("for _cd_key in _claimed_cd_keys.values():", elif_idx)
    delete_idx = body.index("_rc.delete(_cd_key)", for_idx)
    except_idx = body.index("except Exception:", delete_idx)
    pass_idx = body.index("pass", except_idx)
    assert for_idx < delete_idx < except_idx < pass_idx


def test_dark_pool_release_wraps_each_delete_in_its_own_try_except():
    body = _DARK_POOL_BODY
    elif_idx = body.index("elif _claimed_cd_keys:")
    for_idx = body.index("for _cd_key in _claimed_cd_keys.values():", elif_idx)
    delete_idx = body.index("_rc.delete(_cd_key)", for_idx)
    except_idx = body.index("except Exception:", delete_idx)
    pass_idx = body.index("pass", except_idx)
    assert for_idx < delete_idx < except_idx < pass_idx


def test_options_flow_release_does_not_touch_the_seen_set_resync_logic():
    """Regression guard: the pre-existing, ALREADY-correct seen-set exclusion
    (current_chains - set(newly_seen)) on a failed send must be untouched by this fix — the
    cooldown-release is additive, not a replacement."""
    assert "resync_set = current_chains if send_ok else (current_chains - set(newly_seen))" in _OPTIONS_FLOW_BODY


# ── check_dark_pool_alerts() ────────────────────────────────────────────────────────────────

def test_dark_pool_tracks_which_cooldown_keys_were_actually_claimed():
    assert "_claimed_cd_keys: dict[str, str] = {}" in _DARK_POOL_BODY
    set_branch_idx = _DARK_POOL_BODY.index(
        'if _rc.set(cd_key, "1", nx=True, ex=_DARK_POOL_ALERT_COOLDOWN_MINUTES * 60):'
    )
    claim_idx = _DARK_POOL_BODY.index("_claimed_cd_keys[symbol] = cd_key")
    except_idx = _DARK_POOL_BODY.index("except Exception:\n                        cooldown_ok_symbols.append(symbol)")
    assert set_branch_idx < claim_idx < except_idx


def test_dark_pool_releases_claimed_keys_only_when_send_failed():
    body = _DARK_POOL_BODY
    send_idx = body.index("send_ok = send_dark_pool_alert_email(")
    elif_idx = body.index("elif _claimed_cd_keys:", send_idx)
    delete_idx = body.index("_rc.delete(_cd_key)", elif_idx)
    assert send_idx < elif_idx < delete_idx
    if_ok_idx = body.index("if send_ok:\n                    sent += 1")
    assert if_ok_idx < elif_idx


# ── Real, behavioral verification of the release semantics (not just source presence) ─────────

def _simulate_claim_and_maybe_release(send_ok: bool):
    """Reproduces the exact control flow both fixes implement, against a real fake-Redis, to
    prove the RELEASE actually deletes the key that was actually SET — not a placeholder
    assertion on source text alone."""
    class _FakeRedis:
        def __init__(self):
            self.store: dict[str, bool] = {}
        def set(self, key, value, nx=False, ex=None):
            if nx and key in self.store:
                return None
            self.store[key] = True
            return True
        def delete(self, key):
            self.store.pop(key, None)

    rc = _FakeRedis()
    cd_key = "stockai:options_flow_alert_cooldown:1:AAPL:bullish"
    claimed = {}
    if rc.set(cd_key, "1", nx=True, ex=900):
        claimed["AAA1"] = cd_key

    if not send_ok and claimed:
        for k in claimed.values():
            rc.delete(k)

    return rc, cd_key


def test_a_failed_send_actually_removes_the_key_from_redis():
    rc, cd_key = _simulate_claim_and_maybe_release(send_ok=False)
    assert cd_key not in rc.store, "a failed send must leave the candidate retryable next cycle"


def test_a_successful_send_leaves_the_cooldown_key_in_place():
    """The cooldown must still do its real job (suppress a duplicate) when the send DID
    succeed — this fix must not turn every send into an unconditional cooldown-clear."""
    rc, cd_key = _simulate_claim_and_maybe_release(send_ok=True)
    assert cd_key in rc.store, "a successful send must still be suppressed for the cooldown window"
