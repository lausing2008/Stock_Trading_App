"""Tests for OPTHIST-1's UW historical option-chain capture.

Focused on the coercion helpers, because that is where this feature's real risk lives: UW
returns several numerics as STRINGS (strike "95", nbbo_ask "0.21"), returns real nulls for the
greeks of any contract that didn't trade that day, and a genuine 0 (zero bid, zero volume, zero
delta) is meaningful data that must NEVER collapse to None via a truthiness check. This codebase
has fixed that falsy-zero class repeatedly.

Measured shape this is written against (AAPL 2026-06-02, a real response):
  - 3,598 contracts across 26 expiries
  - open_interest / volume / nbbo_bid / nbbo_ask: 100% populated
  - greeks + IV: 47.6% populated, correlating EXACTLY 1:1 with volume>0 (1,714 rows have both;
    zero have one without the other)
"""
import src.services.scheduler as sch


# ── _opthist_f (float coercion) ───────────────────────────────────────────────────────────

def test_float_parses_a_plain_number():
    assert sch._opthist_f(1.25) == 1.25


def test_float_parses_uws_string_numerics():
    """UW sends strike as "95" and nbbo_ask as "0.21" — strings, not numbers."""
    assert sch._opthist_f("95") == 95.0
    assert sch._opthist_f("0.21") == 0.21


def test_float_preserves_a_genuine_zero():
    """THE CORE DISCIPLINE: a zero bid or a zero delta is real data, not absence. Collapsing it
    to None would silently corrupt any downstream payoff/GEX computation."""
    assert sch._opthist_f(0) == 0.0
    assert sch._opthist_f("0") == 0.0


def test_float_preserves_a_negative_value():
    """Put deltas are negative — clamping or dropping them would be a real bug."""
    assert sch._opthist_f(-0.42) == -0.42


def test_float_returns_none_for_none():
    """Greeks are genuinely null for contracts that didn't trade that day — that is UW's
    behaviour, not a fetch failure, and must persist as NULL."""
    assert sch._opthist_f(None) is None


def test_float_returns_none_for_unparseable_input():
    assert sch._opthist_f("not-a-number") is None
    assert sch._opthist_f({}) is None


def test_float_returns_none_for_nan():
    assert sch._opthist_f(float("nan")) is None


# ── _opthist_i (int coercion) ─────────────────────────────────────────────────────────────

def test_int_parses_plain_and_string_integers():
    assert sch._opthist_i(148) == 148
    assert sch._opthist_i("148") == 148


def test_int_preserves_a_genuine_zero_volume():
    """volume=0 is the single most common real value in a chain (52% of rows in the measured
    sample) and is exactly what distinguishes a contract that has greeks from one that doesn't.
    It must survive as 0."""
    assert sch._opthist_i(0) == 0
    assert sch._opthist_i("0") == 0


def test_int_returns_none_for_none():
    assert sch._opthist_i(None) is None


def test_int_returns_none_for_unparseable_input():
    assert sch._opthist_i("abc") is None


def test_int_truncates_a_float_valued_count():
    """Counts should arrive integral, but coercing via float() first means a "148.0" still
    lands as 148 rather than failing the whole row."""
    assert sch._opthist_i("148.0") == 148


# ── Fetcher contract ──────────────────────────────────────────────────────────────────────

def test_historical_chain_fetcher_returns_a_list_not_none_on_failure(monkeypatch):
    """get_historical_option_chain() must return [] on every failure path, never None and never
    raising — matching get_greeks()'s own list-returning contract, so callers never special-case
    which failure occurred. An out-of-window date raises UnusualWhalesAuthError (HTTP 403), and
    that must be caught like any other failure."""
    import src.services.unusual_whales as uw

    monkeypatch.setattr(uw, "is_available", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("simulated 403 / network failure")

    monkeypatch.setattr(uw, "_get", _boom)
    assert uw.get_historical_option_chain("AAPL", "2020-01-01") == []


def test_historical_chain_fetcher_short_circuits_when_uw_unavailable(monkeypatch):
    import src.services.unusual_whales as uw

    monkeypatch.setattr(uw, "is_available", lambda: False)
    called = {"n": 0}

    def _tracked(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr(uw, "_get", _tracked)
    assert uw.get_historical_option_chain("AAPL", "2026-06-02") == []
    assert called["n"] == 0, "must not spend a request when UW is disabled/unconfigured"


def test_historical_chain_fetcher_drops_non_dict_rows(monkeypatch):
    """Defensive: a malformed row must not poison the whole capture for that day."""
    import src.services.unusual_whales as uw

    monkeypatch.setattr(uw, "is_available", lambda: True)
    monkeypatch.setattr(uw, "_get", lambda *a, **k: [{"option_symbol": "X"}, "garbage", None])
    assert uw.get_historical_option_chain("AAPL", "2026-06-02") == [{"option_symbol": "X"}]
