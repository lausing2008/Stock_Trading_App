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
    assert bs.dispatch_block_reason(Trade(), now=NOW, quoted=100.0) is None


@pytest.mark.parametrize("minutes", [16, 60, 360])
def test_intent_older_than_the_limit_is_blocked(minutes):
    t = Trade(entry_time=datetime(2026, 10, 2, 13, 40) - __import__("datetime").timedelta(minutes=minutes))
    reason = bs.dispatch_block_reason(t, now=NOW, quoted=100.0)
    assert reason and reason.startswith("intent_expired")


def test_the_boundary_itself_still_dispatches():
    """Exactly at the limit is not OVER the limit. A boundary that silently excludes its own
    value is the classic off-by-one in a control that blocks real orders."""
    t = Trade(entry_time=datetime(2026, 10, 2, 13, 40) - __import__("datetime").timedelta(
        seconds=bs.INTENT_MAX_AGE_SECONDS))
    assert bs.dispatch_block_reason(t, now=NOW, quoted=100.0) is None


def test_an_undateable_intent_blocks_rather_than_passes():
    """NOT MEASURED is not PASSED. Without an entry_time the age cannot be established, and a
    control that cannot be evaluated has not been satisfied."""
    reason = bs.dispatch_block_reason(Trade(entry_time=None), now=NOW, quoted=100.0)
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
    reason = bs.dispatch_block_reason(Trade(), now=NOW, quoted=quoted)
    assert bool(reason and reason.startswith("price_drift")) is blocked


def test_drift_is_blocked_in_both_directions():
    """A fill 2% BELOW the sized price is just as much a different position as 2% above — it is
    not 'a better entry', it is a position the risk checks never saw."""
    assert bs.dispatch_block_reason(Trade(), now=NOW, quoted=98.0)
    assert bs.dispatch_block_reason(Trade(), now=NOW, quoted=102.0)


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
    assert bs.dispatch_block_reason(t, now=NOW, quoted=100.0).startswith("intent_expired")


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
