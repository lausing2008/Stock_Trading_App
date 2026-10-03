"""Broker lifecycle controls — the dispatch gate and the closure disposition.

THE EVIDENCE SPLIT. The concurrency properties of this module cannot be established in-process:
they need two connections contending for one row in a real PostgreSQL, which lives in
`_broker_lifecycle_pg_probe.py` with its captured output beside it
(`docs/audits/evidence/2026-10-02-broker-lifecycle-pg-races.json`). What IS deterministic — when
an intent may be sent, and what a closure must record when one is already in flight — belongs in
the suite that runs on every commit, which is this file.

Each test below names the probe scenario it corresponds to, so a future reader can find the
concurrent evidence for the same rule rather than assuming these cover it.
"""
import importlib.util
import pathlib
import sys
import types
from datetime import datetime
from unittest.mock import MagicMock

import pytest

_SVC = pathlib.Path(__file__).resolve().parents[1] / "src"
_ROOT = pathlib.Path(__file__).resolve().parents[3]

for _m in ["redis", "httpx", "structlog"]:
    sys.modules.setdefault(_m, MagicMock())
sys.path.insert(0, str(_ROOT / "shared"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "bs_controls", _SVC / "services" / "broker_submission.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load()
NOW = datetime(2026, 10, 2, 13, 40)


def Q(price, *, age_s=0):
    """A quote in the required (price, as_of) form, `age_s` seconds old."""
    return (price, NOW - __import__("datetime").timedelta(seconds=age_s))


class Trade:
    """A stand-in carrying only the fields these controls read."""
    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.symbol = kw.get("symbol", "AAPL")
        self.entry_time = kw.get("entry_time", datetime(2026, 10, 2, 13, 35))
        self.entry_price = kw.get("entry_price", 100.0)
        self.broker_submission_state = kw.get("state")
        self.broker_client_order_id = kw.get("client_order_id", "pt1xabc")
        self.broker_order_id = kw.get("order_id")
        self.broker_fill_confirmed = kw.get("fill_confirmed", False)
        self.broker_error = kw.get("error")


# ── intent expiry (probe scenario R7) ──────────────────────────────────────────────────────

def test_fresh_intent_dispatches():
    assert bs.dispatch_block_reason(Trade(), now=NOW, quoted=Q(100.0)) is None


@pytest.mark.parametrize("minutes", [16, 60, 360])
def test_intent_older_than_the_limit_is_blocked(minutes):
    t = Trade(entry_time=datetime(2026, 10, 2, 13, 40) - __import__("datetime").timedelta(minutes=minutes))
    reason = bs.dispatch_block_reason(t, now=NOW, quoted=Q(100.0))
    assert reason and reason.startswith("intent_expired")


def test_the_boundary_itself_still_dispatches():
    """Exactly at the limit is not OVER the limit. A boundary that silently excludes its own
    value is the classic off-by-one in a control that blocks real orders."""
    t = Trade(entry_time=datetime(2026, 10, 2, 13, 40) - __import__("datetime").timedelta(
        seconds=bs.INTENT_MAX_AGE_SECONDS))
    assert bs.dispatch_block_reason(t, now=NOW, quoted=Q(100.0)) is None


def test_an_undateable_intent_blocks_rather_than_passes():
    """NOT MEASURED is not PASSED. Without an entry_time the age cannot be established, and a
    control that cannot be evaluated has not been satisfied."""
    reason = bs.dispatch_block_reason(Trade(entry_time=None), now=NOW, quoted=Q(100.0))
    assert reason and reason.startswith("intent_age_unverifiable")


def test_intent_age_is_none_not_zero_when_undateable():
    assert bs.intent_age_seconds(Trade(entry_time=None), NOW) is None


# ── price drift (probe scenario R9) ────────────────────────────────────────────────────────

@pytest.mark.parametrize("quoted,blocked", [
    (100.0, False), (100.5, False), (99.5, False),      # inside the 1% tolerance
    (101.0, False), (99.0, False),                      # exactly at it
    (102.0, True), (98.0, True), (150.0, True), (1.0, True),
])
def test_drift_blocks_only_outside_the_tolerance(quoted, blocked):
    reason = bs.dispatch_block_reason(Trade(), now=NOW, quoted=Q(quoted))
    assert bool(reason and reason.startswith("price_drift")) is blocked


def test_drift_is_blocked_in_both_directions():
    """A fill 2% BELOW the sized price is just as much a different position as 2% above — it is
    not 'a better entry', it is a position the risk checks never saw."""
    assert bs.dispatch_block_reason(Trade(), now=NOW, quoted=Q(98.0))
    assert bs.dispatch_block_reason(Trade(), now=NOW, quoted=Q(102.0))


def test_no_quote_blocks_rather_than_passes():
    reason = bs.dispatch_block_reason(Trade(), now=NOW, quoted=None)
    assert reason and reason.startswith("price_unverifiable")


def test_price_drift_pct_is_none_not_zero_when_unmeasurable():
    assert bs.price_drift_pct(Trade(), None) is None
    assert bs.price_drift_pct(Trade(entry_price=0.0), 100.0) is None


def test_drift_is_relative_so_it_means_the_same_at_any_price():
    cheap = bs.price_drift_pct(Trade(entry_price=8.0), 8.16)
    dear = bs.price_drift_pct(Trade(entry_price=800.0), 816.0)
    assert round(cheap, 6) == round(dear, 6) == 2.0


def test_expiry_is_checked_before_price():
    """An expired intent is refused even when the price has not moved at all — the two controls
    are independent, and the stale one must not be rescued by a steady quote."""
    t = Trade(entry_time=datetime(2026, 10, 2, 1, 0))
    assert bs.dispatch_block_reason(t, now=NOW, quoted=Q(100.0)).startswith("intent_expired")


# ── closure disposition (probe scenario R4) ────────────────────────────────────────────────

@pytest.mark.parametrize("state", [bs.SUBMITTING, bs.SUBMITTED, bs.UNKNOWN])
def test_closing_with_a_live_intent_is_recorded(state):
    t = Trade(state=state)
    disposition = bs.record_closure_disposition(t, actor="manual_exit", now=NOW)
    assert disposition is not None
    assert t.broker_error.startswith("closed_with_open_broker_intent")
    assert "manual_exit" in t.broker_error
    assert t.broker_client_order_id in t.broker_error


@pytest.mark.parametrize("state", [None, bs.PENDING, bs.NOT_ATTEMPTED, bs.REJECTED])
def test_closing_an_uninvolved_trade_records_nothing(state):
    """Every production trade today is in one of these states — M25 is off, so nothing ever
    reaches `submitting`. The guard must therefore be inert on the entire live book."""
    t = Trade(state=state)
    assert bs.record_closure_disposition(t, actor="manual_exit", now=NOW) is None
    assert t.broker_error is None


def test_a_confirmed_fill_is_still_reported_but_not_as_needing_reconciliation():
    t = Trade(state=bs.SUBMITTED, order_id="ORD-1", fill_confirmed=True)
    d = bs.closure_disposition(t)
    assert d["confirmed_fill"] is True
    assert d["needs_reconciliation"] is False


def test_an_order_id_without_a_confirmed_fill_is_not_a_fill():
    t = Trade(state=bs.SUBMITTED, order_id="ORD-1", fill_confirmed=False)
    assert bs.closure_disposition(t)["confirmed_fill"] is False


def test_the_disposition_does_not_change_the_trade_stage():
    """Recording is not deciding. The closure still closes; this only ensures it is not silent
    about the order that may be live at the broker."""
    t = Trade(state=bs.SUBMITTING)
    bs.record_closure_disposition(t, actor="scheduled_exit", now=NOW)
    assert not hasattr(t, "stage")


def test_disposition_fits_the_column():
    t = Trade(state=bs.SUBMITTING, client_order_id="x" * 400)
    bs.record_closure_disposition(t, actor="a" * 400, now=NOW)
    assert len(t.broker_error) <= 512


# ── Reported by the M25 lifecycle review, reproduced before the fix ────────────────────────
#
# Both of these returned None — no block — against the first version of these controls. The
# shape is one bug twice: a check written as "compare, and block if it trips" inverts to ALLOW
# whenever the comparison cannot be made, because every comparison against NaN is False and a
# negative age does not exceed a maximum.

def test_a_nan_quote_is_blocked():
    """REPORTED WITNESS. abs(NaN) > limit is False, so NaN sailed through the drift check."""
    reason = bs.dispatch_block_reason(Trade(), now=NOW, quoted=Q(float("nan")))
    assert reason and reason.startswith("quote_invalid")


def test_a_nan_entry_price_is_blocked():
    """The same hole from the other operand, found while reproducing the reported one: drift is
    NaN whichever side is NaN, and NaN never trips a comparison."""
    reason = bs.dispatch_block_reason(Trade(entry_price=float("nan")), now=NOW, quoted=Q(100.0))
    assert reason and reason.startswith("entry_price_invalid")


def test_an_intent_dated_in_the_future_is_blocked():
    """REPORTED WITNESS. A negative age does not exceed the maximum age, so a future-dated
    intent read as young rather than as invalid."""
    future = Trade(entry_time=NOW + __import__("datetime").timedelta(days=1))
    reason = bs.dispatch_block_reason(future, now=NOW, quoted=Q(100.0))
    assert reason and reason.startswith("intent_dated_in_the_future")


def test_small_clock_skew_is_tolerated_not_called_invalid():
    """Two clocks disagreeing by a second is skew, not a corrupt timestamp. Treating every
    future-dated stamp as invalid would block ordinary dispatches on ordinary drift."""
    skewed = Trade(entry_time=NOW + __import__("datetime").timedelta(
        seconds=bs.MAX_CLOCK_SKEW_SECONDS - 1))
    assert bs.dispatch_block_reason(skewed, now=NOW, quoted=Q(100.0)) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0,
                                 None, "100", True, b"100", object()])
def test_no_non_price_is_ever_treated_as_a_price(bad):
    reason = bs.dispatch_block_reason(Trade(), now=NOW, quoted=Q(bad))
    assert reason is not None
    # ...and it is reported as an invalid price, not as a 200% move.
    assert reason.startswith("quote_invalid")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -5.0, None])
def test_no_non_price_is_ever_treated_as_an_entry_price(bad):
    assert bs.dispatch_block_reason(Trade(entry_price=bad), now=NOW, quoted=Q(100.0)) is not None


def test_price_drift_is_none_never_nan():
    """The poisoned value must not even be returned, or the next comparison allows the order."""
    assert bs.price_drift_pct(Trade(), float("nan")) is None
    assert bs.price_drift_pct(Trade(entry_price=float("nan")), 100.0) is None
    assert bs.price_drift_pct(Trade(), float("inf")) is None


# ── Quote freshness ────────────────────────────────────────────────────────────────────────

def test_a_bare_number_is_not_a_quote():
    """Numeric availability is not freshness: a cached figure from hours ago is a number too."""
    reason = bs.dispatch_block_reason(Trade(), now=NOW, quoted=100.0)
    assert reason and reason.startswith("quote_freshness_unverifiable")


def test_a_stale_quote_is_blocked():
    reason = bs.dispatch_block_reason(
        Trade(), now=NOW, quoted=Q(100.0, age_s=bs.MAX_QUOTE_AGE_SECONDS + 1))
    assert reason and reason.startswith("quote_stale")


def test_a_quote_at_the_freshness_boundary_still_passes():
    assert bs.dispatch_block_reason(
        Trade(), now=NOW, quoted=Q(100.0, age_s=bs.MAX_QUOTE_AGE_SECONDS)) is None


def test_a_quote_dated_in_the_future_is_blocked():
    reason = bs.dispatch_block_reason(
        Trade(), now=NOW, quoted=Q(100.0, age_s=-(bs.MAX_CLOCK_SKEW_SECONDS + 60)))
    assert reason and reason.startswith("quote_dated_in_the_future")


@pytest.mark.parametrize("malformed", [(100.0,), (100.0, NOW, "extra"), (100.0, "not-a-date"),
                                       [100.0, None]])
def test_a_malformed_quote_is_blocked(malformed):
    assert bs.dispatch_block_reason(Trade(), now=NOW, quoted=malformed) is not None


# ── A broken policy value must not read as permission ──────────────────────────────────────

@pytest.mark.parametrize("kwargs", [
    {"max_age_seconds": float("nan")}, {"max_age_seconds": 0}, {"max_age_seconds": -1},
    {"max_drift_pct": float("nan")}, {"max_drift_pct": float("inf")}, {"max_drift_pct": 0},
    {"max_quote_age_seconds": float("nan")}, {"max_clock_skew_seconds": float("nan")},
    {"max_clock_skew_seconds": -1},
])
def test_an_invalid_limit_blocks_rather_than_disabling_the_control(kwargs):
    reason = bs.dispatch_block_reason(Trade(), now=NOW, quoted=Q(100.0), **kwargs)
    assert reason and reason.startswith("policy_invalid")


# ── A failing quote source blocks one symbol, not the batch ────────────────────────────────

def test_a_raising_quote_source_is_contained():
    def boom(trade):
        raise ConnectionError("price cache unreachable")
    value, error = bs.read_quote(boom, Trade())
    assert value is None
    assert error.startswith("quote_source_failed: ConnectionError")


def test_a_working_quote_source_passes_its_value_through():
    value, error = bs.read_quote(lambda t: Q(100.0), Trade())
    assert error is None and value[0] == 100.0
