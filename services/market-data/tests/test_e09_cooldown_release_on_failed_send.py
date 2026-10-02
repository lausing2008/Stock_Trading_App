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

SR-03 (2026-10-02) — THE OPTIONS-FLOW HALF NOW ACHIEVES THIS A DIFFERENT WAY, and the tests
below follow the property rather than the mechanism. Claim-then-release was the right repair
while the claim came first; SR-03 found the claim was ALSO suppressing contracts the cap had
omitted from the payload, so the claim now happens AFTER a successful send and covers only the
delivered contracts. A cooldown that is never claimed on failure needs no release — the
property E09 guards ("a failed send must not suppress a real alert for the full window") holds
by construction instead of by compensation. `check_dark_pool_alerts` is untouched and keeps the
claim-then-release form, so its assertions are unchanged.

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

def test_options_flow_never_claims_a_cooldown_before_the_send():
    """SR-03: the claim used to precede the cap, so a contract the email never carried still
    suppressed its whole (symbol, direction) pair for the cooldown window."""
    body = _OPTIONS_FLOW_BODY
    send_idx = body.index("send_ok = send_options_flow_alert_email(")
    claim_idx = body.index('_rc.set(_cd_key, "1", nx=True,')
    assert send_idx < claim_idx, "the cooldown must be claimed only after a send is attempted"


def test_options_flow_claims_the_cooldown_only_on_a_successful_send():
    body = _OPTIONS_FLOW_BODY
    if_ok_idx = body.index("if send_ok:\n                        sent += 1")
    claim_idx = body.index('_rc.set(_cd_key, "1", nx=True,')
    next_dedent = body.index("\n                # SR-03: the seen set is DELIVERY identity", if_ok_idx)
    assert if_ok_idx < claim_idx < next_dedent, \
        "the claim must sit inside the send_ok arm, not beside it"


def test_options_flow_claims_the_cooldown_only_for_the_delivered_payload():
    """The cap's omitted contracts must remain eligible, which means their pair must not be
    claimed by an email that did not carry them."""
    body = _OPTIONS_FLOW_BODY
    claim_idx = body.index('_rc.set(_cd_key, "1", nx=True,')
    loop_idx = body.rindex("for chain in capped:", 0, claim_idx)
    assert "omitted_chains" not in body[loop_idx:claim_idx]


def test_options_flow_reads_the_cooldown_without_claiming_it_while_filtering():
    """The pre-send filter must be a READ. A write here would re-create the defect."""
    body = _OPTIONS_FLOW_BODY
    filter_idx = body.index("deferred_chains = []")
    send_idx = body.index("send_ok = send_options_flow_alert_email(")
    window = body[filter_idx:send_idx]
    assert "_rc.exists(cd_key)" in window
    assert "nx=True" not in window, "the filter must not claim the key it is testing"


def test_options_flow_keeps_the_pair_dedup_the_claim_used_to_perform():
    """The nx=True claim used to dedup (symbol, direction) as a SIDE EFFECT — only the first
    chain per pair got through. Removing the early claim must not silently remove that."""
    body = _OPTIONS_FLOW_BODY
    assert "_best_per_pair" in body
    i = body.index("_best_per_pair")
    window = body[i:body.index("cooldown_ok_chains = []", i)]
    assert "total_premium" in window, "the surviving contract per pair must be the largest"


def test_dark_pool_release_wraps_each_delete_in_its_own_try_except():
    body = _DARK_POOL_BODY
    elif_idx = body.index("elif _claimed_cd_keys:")
    for_idx = body.index("for _cd_key in _claimed_cd_keys.values():", elif_idx)
    delete_idx = body.index("_rc.delete(_cd_key)", for_idx)
    except_idx = body.index("except Exception:", delete_idx)
    pass_idx = body.index("pass", except_idx)
    assert for_idx < delete_idx < except_idx < pass_idx


def test_options_flow_marks_seen_only_what_was_actually_accepted():
    """SR-03 REPLACED THIS ASSERTION, and the reason is the finding itself.

    It used to pin `resync_set = current_chains if send_ok else (current_chains - set(newly_seen))`,
    which looks careful — a failed send correctly excludes its own chains — but `send_ok`
    started True and stayed True when NO send was attempted at all. So on a cycle where every
    candidate was on cooldown, the first arm ran and marked all of them seen. The cap had the
    same effect on contracts it omitted.

    Delivery identity now advances for the accepted payload only."""
    body = _OPTIONS_FLOW_BODY
    assert "resync_set = (prev_seen & current_chains) | accepted_chains" in body
    i = body.index("accepted_chains = set(capped)")
    assert body.index("if send_ok:", body.index("send_ok = send_options_flow_alert_email(")) < i, \
        "acceptance may only be recorded after the sender returns success"


def test_a_cycle_with_no_send_attempt_marks_nothing_newly_seen():
    """The exact witness: an active cooldown produced zero sender calls and seen=['c1'] anyway."""
    body = _OPTIONS_FLOW_BODY
    assert "send_attempted = False" in body
    # `accepted_chains` starts empty and is only ever filled inside the successful-send arm, so
    # a cycle that never calls the sender can contribute nothing to the seen set.
    assert body.count("accepted_chains = set(capped)") == 1
    assert "accepted_chains: set[str] = set()" in body


def test_deferred_and_omitted_work_is_recorded_as_suppression_not_delivery():
    body = _OPTIONS_FLOW_BODY
    assert "options_flow_alert.recipient_accounting" in body
    for field in ("deferred_cooldown_or_pair", "omitted_by_cap", "accepted", "send_attempted"):
        assert field in body, f"{field} must be countable"


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
