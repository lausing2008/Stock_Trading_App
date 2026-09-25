"""R04: the settlement clock guard proves the session ended, not that ingestion finished.

DA-05 stopped settlement while a session was still trading. After 16:15 ET, though, the lookup
accepted ANY daily row bearing the expected date — including one written intraday at 15:00 and
never refreshed because the post-close ingest failed. `prices` carries no fetched-at or finality
column, so the row itself cannot say when it was written: a fixed buffer proves the session
ended and proves nothing about the row.

The consequence is the one DA-05 exists to prevent, reached by a different route: a $100 put
read against an unfinished $101 print settles as expired-worthless and is excluded from every
retry, even if the real close was $90 and the outcome is a −$900 assignment.

THE EVIDENCE USED INSTEAD is independent corroboration, because none is stored. After the close
a symbol's live quote IS that session's close, so a stored bar that disagrees materially was
written before the session finished. And if a LATER session's bar exists, ingestion has already
moved past this one — which settles it without needing a quote at all.

EVERYTHING UNCERTAIN DEFERS. No symbol, no quote, a provider error, a disagreeing number: the
position stays open and the next run retries. A deferral costs one cycle; a wrong settlement is
permanent.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from src.services import options_income_engine as OIE

SESSION = date(2026, 9, 25)


def _corroborate(stored=100.0, quote=100.0, later_bar=False, symbol="AAPL", raises=False):
    from datetime import datetime

    class _S:
        def __init__(self):
            self.n = 0

        def execute(self, *_a, **_k):
            self.n += 1
            if self.n == 1:  # symbol lookup
                return SimpleNamespace(scalar_one_or_none=lambda: symbol)
            # max(Price.ts) for this symbol — a LATER session means ingestion moved past `want`.
            latest = datetime(2026, 9, 28) if later_bar else datetime(2026, 9, 25)
            return SimpleNamespace(scalar=lambda: latest)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fetch(_syms):
        if raises:
            raise RuntimeError("provider down")
        return {symbol: quote} if quote is not None else {}

    # The helper is imported INSIDE the function (`from .paper_trading_engine import ...`),
    # so the patch has to land on the module that relative import actually resolves to —
    # patching a separately-imported alias binds a different module object under this suite's
    # import layout, and the lookup then silently falls into the except branch.
    with patch.object(OIE, "SessionLocal", lambda: _S()), \
         patch("src.services.paper_trading_engine._fetch_live_prices", _fetch):
        return OIE._corroborate_settlement_close(1, SESSION, stored)


# ── The failure R04 describes ────────────────────────────────────────────────

def test_a_stored_close_that_disagrees_with_the_live_quote_defers():
    """The unfinished-bar case: stored $101 from 15:00, actual close $90."""
    ok, reason = _corroborate(stored=101.0, quote=90.0)
    assert ok is False
    assert "disagrees" in reason


def test_a_matching_close_is_corroborated():
    ok, reason = _corroborate(stored=100.0, quote=100.0)
    assert ok is True
    assert reason == "corroborated_by_live_quote"


def test_a_small_provider_difference_is_tolerated():
    """The tolerance is for rounding and adjusted-vs-raw feed differences, not for a moving
    price — after the close the two should be the same number."""
    ok, _ = _corroborate(stored=100.0, quote=100.2)
    assert ok is True


def test_a_difference_beyond_the_tolerance_is_not():
    ok, _ = _corroborate(stored=100.0, quote=101.0)
    assert ok is False


# ── A later session is proof on its own ──────────────────────────────────────

def test_a_later_sessions_bar_settles_it_without_needing_a_quote():
    """If ingestion has written a LATER day, it has finished with this one. This is what makes
    an older expiry settle normally without depending on a live feed that now reflects a
    different day entirely."""
    ok, reason = _corroborate(later_bar=True, quote=None)
    assert ok is True
    assert reason == "superseded_by_later_session"


# ── Everything uncertain defers ──────────────────────────────────────────────

def test_no_quote_defers():
    ok, reason = _corroborate(quote=None)
    assert ok is False
    assert reason == "no_independent_quote"


def test_a_zero_or_negative_quote_defers():
    for bad in (0.0, -5.0):
        ok, reason = _corroborate(quote=bad)
        assert ok is False, bad
        assert reason == "no_independent_quote"


def test_a_provider_error_defers_rather_than_settling():
    ok, reason = _corroborate(raises=True)
    assert ok is False
    assert reason.startswith("corroboration_failed")


def test_an_unresolvable_symbol_defers():
    ok, reason = _corroborate(symbol=None)
    assert ok is False
    assert reason == "symbol_not_found"


# ── The guard must be WIRED IN ───────────────────────────────────────────────
#
# DA-05 and DA-09 both had a sabotage pass because a helper was tested in isolation while
# nothing asserted it was called. Not repeating that here.

class _PriceSession:
    """Returns a daily bar unconditionally, so any refusal comes from the guards."""

    def execute(self, *_a, **_k):
        return SimpleNamespace(first=lambda: SimpleNamespace(close=101.0, ts=SESSION))


def test_settlement_defers_when_the_close_is_not_corroborated():
    with patch.object(OIE, "settlement_session_is_final", lambda *_a, **_k: True), \
         patch.object(OIE, "_corroborate_settlement_close",
                      lambda *_a, **_k: (False, "stored_close_disagrees_by_10%")):
        assert OIE._settlement_close(_PriceSession(), stock_id=1, expiry=SESSION) is None


def test_settlement_proceeds_when_the_close_is_corroborated():
    """The guard must not break the path that was already correct."""
    with patch.object(OIE, "settlement_session_is_final", lambda *_a, **_k: True), \
         patch.object(OIE, "_corroborate_settlement_close",
                      lambda *_a, **_k: (True, "corroborated_by_live_quote")):
        got = OIE._settlement_close(_PriceSession(), stock_id=1, expiry=SESSION)
    assert got is not None
    price, sess = got
    assert price == 101.0
    assert sess == OIE.expected_settlement_session(SESSION)


def test_the_clock_guard_still_runs_first():
    """Corroboration is a network call; a still-trading session must be refused before paying
    for it, and before any row is read."""
    called = {"corroborated": False}

    def _spy(*_a, **_k):
        called["corroborated"] = True
        return (True, "x")

    with patch.object(OIE, "settlement_session_is_final", lambda *_a, **_k: False), \
         patch.object(OIE, "_corroborate_settlement_close", _spy):
        assert OIE._settlement_close(_PriceSession(), stock_id=1, expiry=SESSION) is None
    assert called["corroborated"] is False
