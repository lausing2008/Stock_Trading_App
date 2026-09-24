"""DA-06: a stale archived ask could hide current short-option exposure.

THE DEFECT. `_latest_option_ask()` returned the most recent non-null ask with no age check and
no way for the caller to learn WHEN it was quoted, and `short_option_liability()` accepted any
non-negative value — including zero — in preference to the intrinsic fallback. The equity
snapshot then combined that possibly-ancient quote with a LIVE underlying price, and discarded
the returned mark-source string, so the persisted row could not even explain itself.

The audit's controlled example: one $100 short put, stock now $80, a stale $2 ask. Reported
liability $200 against an intrinsic floor of $2,000 — reported equity inflated by $1,800.

THE DECISIVE GUARD IS ARITHMETIC, NOT A TIMESTAMP. An option cannot be worth less than its
intrinsic value: buying back a $100 put with the stock at $80 costs at least $20/share by
arbitrage. So a $2 ask is not a cheap close, it is a quote from before the move. Freshness
metadata can be missing, wrong, or absent from a feed; that bound cannot. The age limit is the
second line of defence, not the first — and it matters for the opposite case, where a stale
quote is ABOVE intrinsic and would otherwise look perfectly plausible.

Marking at the ask remains right in the normal case: closing a SHORT means BUYING it back, and a
buyer pays the ask — the conservative direction for a liability.
"""
from datetime import date, timedelta
from types import SimpleNamespace

from src.services.options_income_engine import (
    _MAX_ASK_AGE_DAYS,
    _latest_option_ask,
    short_option_liability,
)

PUT = dict(strategy="CASH_SECURED_PUT", strike=100.0, contracts=1)
CALL = dict(strategy="COVERED_CALL", strike=100.0, contracts=1)


# ── The reported failure ─────────────────────────────────────────────────────

def test_a_quote_below_intrinsic_is_refused_and_the_floor_is_used():
    """The audit's own numbers: $100 put, stock $80, stale $2 ask."""
    liab, src = short_option_liability(**PUT, underlying_price=80.0, quote_ask=2.0)
    assert liab == 2000.0
    assert src == "intrinsic_quote_below_floor"


def test_a_zero_ask_cannot_erase_a_real_obligation():
    """Zero passed the old `>= 0` check outright, marking a deep in-the-money short at nothing."""
    liab, src = short_option_liability(**PUT, underlying_price=80.0, quote_ask=0.0)
    assert liab == 2000.0
    assert src != "quote_ask"


def test_the_refusal_is_labelled_rather_than_silently_swapped():
    """A caller must be able to tell 'marked on the floor because the quote was impossible'
    from 'no quote existed' — they are different data-quality states."""
    _, suspect = short_option_liability(**PUT, underlying_price=80.0, quote_ask=1.0)
    _, absent = short_option_liability(**PUT, underlying_price=80.0, quote_ask=None)
    assert suspect == "intrinsic_quote_below_floor"
    assert absent == "intrinsic"
    assert suspect != absent


# ── The normal case must still work ──────────────────────────────────────────

def test_a_plausible_ask_is_still_used_and_still_marks_at_the_ask():
    """Closing a short means buying it back, so the ask is the conservative mark. A quote ABOVE
    intrinsic carries real time value the floor would understate."""
    liab, src = short_option_liability(**PUT, underlying_price=80.0, quote_ask=21.5)
    assert liab == 2150.0
    assert src == "quote_ask"


def test_an_out_of_the_money_short_still_marks_on_its_quote():
    """Intrinsic is zero here, so any non-negative quote is legitimate — the guard must not
    collapse OTM positions to zero liability and re-create the bug it replaced."""
    liab, src = short_option_liability(**PUT, underlying_price=120.0, quote_ask=0.75)
    assert liab == 75.0
    assert src == "quote_ask"


def test_a_covered_call_uses_the_call_side_intrinsic():
    """A call is intrinsic when the underlying is ABOVE the strike — the mirror of the put."""
    liab, src = short_option_liability(**CALL, underlying_price=130.0, quote_ask=5.0)
    assert liab == 3000.0
    assert src == "intrinsic_quote_below_floor"
    liab2, src2 = short_option_liability(**CALL, underlying_price=70.0, quote_ask=0.4)
    assert liab2 == 40.0
    assert src2 == "quote_ask"


def test_contracts_scale_the_liability():
    liab, _ = short_option_liability(strategy="CASH_SECURED_PUT", strike=100.0, contracts=3,
                                     underlying_price=80.0, quote_ask=2.0)
    assert liab == 6000.0


def test_no_quote_at_all_falls_back_to_intrinsic_as_before():
    liab, src = short_option_liability(**PUT, underlying_price=80.0, quote_ask=None)
    assert liab == 2000.0
    assert src == "intrinsic"


# ── Quote age and look-ahead ─────────────────────────────────────────────────

class _Sess:
    def __init__(self):
        self.params = None

    def execute(self, _sql, params=None):
        self.params = params
        return SimpleNamespace(first=lambda: None)


def _ask_query_source() -> str:
    """The shipped source of the one lookup function.

    Asserting only that `floor`/`ref` are BOUND is not enough: deleting the WHERE clause leaves
    both parameters bound and the query unbounded, and both sabotages passed that way. This
    suite stubs sqlalchemy, so text() carries no readable SQL — the source is the only place the
    clause actually exists. No numeric literal is pinned (AUD-T401-SOURCETEXTTESTS).
    """
    import inspect

    from src.services import options_income_engine as OIE
    return inspect.getsource(OIE._latest_option_ask)


def test_the_query_itself_excludes_quotes_older_than_the_floor():
    assert "as_of >= :floor" in _ask_query_source()


def test_the_query_itself_excludes_quotes_after_the_valuation_date():
    assert "as_of <= :ref" in _ask_query_source()


def test_the_quote_lookup_is_bounded_below_by_a_maximum_age():
    s = _Sess()
    ref = date(2026, 9, 24)
    _latest_option_ask(s, "AAPL260925P00100000", as_of=ref)
    assert s.params["floor"] == ref - timedelta(days=_MAX_ASK_AGE_DAYS)


def test_the_quote_lookup_is_bounded_ABOVE_by_the_valuation_date():
    """A historical snapshot reaching forward for a quote that did not exist yet is look-ahead,
    and the old query's unconstrained `ORDER BY as_of DESC` did exactly that."""
    s = _Sess()
    ref = date(2026, 6, 1)
    _latest_option_ask(s, "AAPL260925P00100000", as_of=ref)
    assert s.params["ref"] == ref


def test_the_age_limit_is_short_enough_to_mean_something():
    """The archive is written daily; a limit of weeks would readmit exactly the stale marks this
    finding is about. Pins the magnitude, not the exact number."""
    assert 1 <= _MAX_ASK_AGE_DAYS <= 10
