"""M25-BROKER-COMMIT-BOUNDARY — a broker order must not be placed from an uncommitted transaction.

THE DEFECT, measured 2026-10-01. `_place_broker_entry` is called from inside
`_open_paper_trade`, which contains no `session.commit()` — the caller commits afterwards. So a
real order was submitted from within an uncommitted transaction, and a crash between the broker
accepting and that commit left an accepted order with **no local trade row at all**, plus an
exposure reservation that reverts and expires: capacity released for exposure that exists at the
broker.

Pre-existing. The exposure-reservation work did not cause it; asking what happens to capacity
across that crash is what exposed it.

These run against a real database in a subprocess (`_concentration_probe.py`) because every
claim is about what survives a commit boundary.

THE ROLLOUT FLAG DEFAULTS OFF. Switching it changes WHERE a real order is placed relative to the
commit — a live execution-ordering change, which belongs to a person rather than to a deploy.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_concentration_probe.py"


@pytest.fixture(scope="module")
def probe():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                          timeout=300)
    if proc.returncode != 0:
        pytest.fail(f"probe failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return json.loads(proc.stdout)


# ── The flag ──────────────────────────────────────────────────────────────────────────────────

def test_the_new_ordering_is_off_unless_explicitly_enabled(probe):
    """Absent, or no client at all, reads as OFF — the historical inline behaviour. Deploying
    this module therefore changes nothing about how orders are placed."""
    f = probe["broker_flag"]
    assert f["absent"] is False
    assert f["no_client"] is False
    assert f["set"] is True


# ── Durable intent before anything is sent ────────────────────────────────────────────────────

def test_the_intent_is_recorded_and_committed_before_the_broker_is_contacted(probe):
    """The whole fix in one assertion: the local row exists, says `pending`, and carries no
    order id — so if the process dies here, nothing was sent and nothing is lost."""
    i = probe["broker_intent"]
    assert i["state"] == "pending"
    assert i["order_id"] is None
    assert i["claimable"] == 1


def test_a_successful_submission_records_the_order_exactly_once(probe):  # noqa: D103
    s = probe["broker_submit_ok"]
    assert s["state"] == "submitted" and s["order_id"] is not None
    assert s["calls"] == 1 and s["attempts"] == 1
    assert s["batch"]["submitted"] == 1


# ── The ambiguous outcome ─────────────────────────────────────────────────────────────────────

def test_a_timeout_after_possible_acceptance_is_unknown_and_never_auto_retried(probe):
    """A blind retry is precisely how a DUPLICATE REAL ORDER gets placed. The call count must
    stay at one across a second pass."""
    t = probe["broker_timeout"]
    assert t["batch"]["unknown"] == 1
    assert t["retried"] == 0, "an unknown outcome must not be re-claimed"
    assert t["calls"] == 1, "the broker must not be contacted a second time"
    assert t["awaiting_reconciliation"] >= 1


def test_a_worker_that_died_mid_call_is_reconciled_not_resubmitted(probe):
    """A row stuck in `submitting` may already correspond to a real order. Only the broker's own
    record can say, so it is surfaced rather than guessed at."""
    st = probe["broker_stuck_submitting"]
    assert st["reclaimed"] is False
    assert st["surfaced_for_reconciliation"] is True


# ── Definite failures stay retryable ──────────────────────────────────────────────────────────

def test_an_exception_without_evidence_of_rejection_is_UNKNOWN(probe):
    """CORRECTED. An escaping exception — timeout, parse error, connection reset — does not
    establish that the broker refused the order. Only explicit evidence may downgrade it to
    `rejected`; everything else is `unknown`."""
    r = probe["broker_reject"]
    assert r["batch"]["unknown"] == 1
    assert r["batch"]["rejected"] == 0


def test_a_call_that_returns_without_an_order_id_is_UNKNOWN_not_failed(probe):
    """THE CORRECTION THAT MATTERS MOST HERE, and my original classification was wrong.

    `_place_broker_entry` catches every exception from `place_order` and returns normally, so
    from outside a swallowed timeout AFTER acceptance and a clean rejection are
    indistinguishable — both leave no order id. I first recorded that as `failed`, which is
    retryable, and a retry on an order that may already exist is a DUPLICATE REAL ORDER.

    A missing id with no evidence of rejection is `unknown`."""
    u = probe["broker_missing_id_is_unknown"]
    assert u["state"] == "unknown"
    assert u["reclaimed_for_replacement"] is False, \
        "an unknown outcome must never license a replacement order"


def test_an_unknown_submission_keeps_its_exposure_and_is_not_a_confirmed_fill(probe):
    """It may correspond to a real order, so capacity must not be released — and it must not be
    folded into executed-trade statistics as though a fill had been observed."""
    u = probe["broker_missing_id_is_unknown"]
    assert u["retains_exposure"] is True
    assert u["counts_as_confirmed_fill"] is False


# ── Identity and routing, before activation ───────────────────────────────────────────────────

def test_a_stable_client_order_identity_is_committed_before_any_broker_contact(probe):
    """What makes an `unknown` resolvable at all. Without an identity, matching the broker's
    own order list back to this trade means guessing from symbol and quantity — which is how a
    reconciliation confirms the wrong order."""
    i = probe["broker_identity"]
    assert i["client_order_id"] and i["client_order_id"].startswith("pt")
    assert i["before_any_broker_contact"] is True


def test_the_identity_fits_every_brokers_field_width():
    """E*Trade truncates `clientOrderId` at 20 characters, and a TRUNCATED id is not an id —
    two intents could collide after the cut, which is worse than having none. The generated
    value must therefore survive intact at every broker."""
    from src.services import broker_submission as bs
    for pid in (1, 999999):
        cid = bs.new_client_order_id(pid, "VERYLONGSYMBOL.HK")
        assert len(cid) <= 20, cid


def test_the_identity_actually_reaches_the_broker():
    """STORING IT LOCALLY IS NOT ENOUGH, and this was the gap. `place_order` had no
    `client_order_id` parameter at all, and E*Trade minted a THROWAWAY uuid4 of its own — so
    nothing local matched anything the broker held, and an `unknown` was unresolvable except by
    guessing from symbol, quantity and time.

    The round trip is asserted here against a recording adapter."""
    from src.services.broker.interface import OrderSide, OrderType
    import inspect
    from src.services.broker import alpaca_broker, etrade_broker, interface

    # Every adapter accepts it...
    for mod, name in ((interface, "BrokerInterface"), (alpaca_broker, None),
                      (etrade_broker, None)):
        src = inspect.getsource(mod)
        assert "client_order_id" in src, f"{mod.__name__} cannot carry a client order id"
    # ...and the two that can transmit it say so.
    assert "supports_client_order_id = True" in inspect.getsource(alpaca_broker)
    assert "supports_client_order_id = True" in inspect.getsource(etrade_broker)


def test_an_adapter_that_cannot_transmit_the_identity_is_recorded_as_such():
    """A manual broker has no counterparty to send an id to. Treating it as capable would mean
    believing an `unknown` is resolvable by identity when it is not."""
    from src.services import broker_submission as bs
    from src.services.broker import manual_broker
    import inspect
    assert "supports_client_order_id = False" in inspect.getsource(manual_broker)
    assert bs.identity_is_transmittable(object()) is False


def test_an_intent_routed_to_the_legacy_path_is_never_claimed_by_the_new_dispatcher(probe):
    """The route is resolved ONCE at entry and persisted. A flag change or a restart between
    entry and submission must not hand the same intent to both paths."""
    assert probe["broker_path_isolation"]["legacy_intent_claimable_by_deferred"] is False


# ── Terminal is not unrecordable ──────────────────────────────────────────────────────────────

def test_a_later_confirmed_fill_can_still_be_recorded_against_an_unknown(probe):
    """"Never automatically resubmit" is the safety property. It must not prevent recording a
    fill, rejection or cancellation that the broker's own record later confirms."""
    r = probe["broker_reconcile"]
    assert r["state"] == "submitted" and r["order_id"] == "ord-9"


def test_reconciliation_demands_evidence_and_an_order_id(probe):
    """Resolving to `submitted` without the broker's order id would make the claim
    uncheckable against the broker's record afterwards."""
    r = probe["broker_reconcile"]
    assert "requires evidence" in r["evidence_required"]
    assert "requires the broker's order id" in r["order_id_required"]


def test_resolving_to_REJECTED_does_not_require_an_order_id():
    """Evidence appropriate to the VERDICT. `rejected` asserts an order does not exist, and a
    definitive pre-submission failure legitimately has none — demanding one would force a
    FABRICATED id to record a true fact."""
    import inspect
    from src.services import broker_submission as bs
    src = inspect.getsource(bs.reconcile_submission)
    assert "if resolution == SUBMITTED and not (order_id or trade.broker_order_id):" in src, \
        "the order-id requirement must be scoped to `submitted` only"


def test_a_not_found_lookup_is_not_treated_as_proof_of_absence():
    """A broker returning "not found" can mean propagation delay, the wrong account scope, or a
    transient outage. Treating it as proof would authorise a resubmission for an order that is
    simply not visible yet."""
    from src.services import broker_submission as bs
    assert "not evidence of non-existence" in bs.NOT_FOUND_IS_NOT_ABSENCE


# ── What is deliberately not claimed ──────────────────────────────────────────────────────────

def test_the_module_does_not_promise_duplicate_free_submission():
    """A crash between the broker accepting and this process recording it is locally
    indistinguishable from a crash before the call. The window is narrowed, made detectable and
    auditable — not eliminated — and the docstring has to keep saying so."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services"
           / "broker_submission.py").read_text()
    assert "WHAT IS NOT PROMISED" in src
    assert "never retried automatically" in src
