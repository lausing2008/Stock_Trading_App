"""AUD-LIVEBAR-T196: the T196 price-drift ("don't chase") gate in paper_trading_engine.py
resolved its reference close with `func.date(Price.ts) <= _sig_date`.

The D1 row for the CURRENT trading day is upserted every ~5 min as it live-updates — the
Price table has no is_final/is_settled column — and `live_price` reads from that same
continuously-moving row. So for a signal that fired TODAY, the reference bar WAS today's live
bar, and the gate compared the live price against itself: drift ≈ 0%, gate never fires. It
silently disabled itself in exactly the scenario it exists to catch (a stock that has already
run hard intraday).

Fixed by bounding the reference to the last SETTLED bar:
    _ref_cutoff = min(_sig_date, date.today() - timedelta(days=1))
"""
import pathlib
import re
from datetime import date, timedelta

_ENGINE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "services" / "paper_trading_engine.py"
)
_SOURCE = _ENGINE_PATH.read_text()


def _t196_block() -> str:
    """The T196 batch-fetch block that resolves each candidate's reference close."""
    start = _SOURCE.index("# T196: Batch-fetch daily close at signal date")
    # End at the except-clause that closes the batch-fetch loop. NOT "fail-open" — that
    # phrase also appears in this block's own opening comment, which would truncate to
    # nothing and make every source assertion below vacuously fail.
    end = _SOURCE.index("except Exception:", start)
    return _SOURCE[start:end]


# ── source-level: the reference bar is capped at a settled close ──────────────

def test_reference_close_is_bounded_by_a_settled_cutoff_not_raw_signal_date():
    block = _t196_block()
    assert "_ref_cutoff" in block, "reference query must use an explicit settled cutoff"
    assert "func.date(Price.ts) <= _ref_cutoff" in block
    assert "func.date(Price.ts) <= _sig_date" not in block, (
        "raw _sig_date bound would re-admit today's live bar"
    )


def test_cutoff_is_min_of_signal_date_and_yesterday():
    """min() matters in BOTH directions: an OLD signal must keep its own (already settled)
    signal-date bar, and a TODAY signal must be pulled back to yesterday."""
    block = _t196_block()
    normalized = re.sub(r"\s+", " ", block)
    assert "min(_sig_date, date.today() - timedelta(days=1))" in normalized


def test_fix_is_documented_with_its_audit_tag():
    assert "AUD-LIVEBAR-T196" in _t196_block()


# ── behavioral: the cutoff expression itself ─────────────────────────────────

def _cutoff(sig_date: date, today: date) -> date:
    """The exact expression the engine uses."""
    return min(sig_date, today - timedelta(days=1))


def test_signal_fired_today_is_pulled_back_to_yesterday():
    """The core bug: a signal generated today must NOT reference today's live bar."""
    today = date(2026, 9, 4)
    assert _cutoff(today, today) == date(2026, 9, 3)


def test_older_signal_keeps_its_own_settled_signal_date():
    """A signal from last week already references a settled bar — the fix must not move it,
    or the drift window would silently widen and change unrelated gate behavior."""
    today = date(2026, 9, 4)
    sig = date(2026, 8, 28)
    assert _cutoff(sig, today) == sig


def test_yesterdays_signal_is_unchanged():
    today = date(2026, 9, 4)
    sig = date(2026, 9, 3)
    assert _cutoff(sig, today) == sig


def test_cutoff_is_never_today_regardless_of_signal_date():
    today = date(2026, 9, 4)
    for offset in range(0, 10):
        assert _cutoff(today - timedelta(days=offset), today) < today


def test_drift_is_measurable_against_a_settled_baseline():
    """Regression intent: with a settled reference, a real intraday run-up produces real
    drift instead of the ~0% the live-bar-vs-itself comparison produced.

    Uses NVDA's actual observed bars (2026-09-03 settled 228.45, 2026-09-04 live 230.36).
    """
    settled_close = 228.45   # 2026-09-03, settled
    live_price = 230.36      # 2026-09-04, still forming — what live_price reads

    drift_fixed = live_price / settled_close - 1
    drift_old = live_price / live_price - 1  # old behavior: same row on both sides

    assert drift_old == 0.0, "old bound compared the live bar against itself"
    assert drift_fixed > 0.008, "settled baseline exposes the real intraday run-up"
