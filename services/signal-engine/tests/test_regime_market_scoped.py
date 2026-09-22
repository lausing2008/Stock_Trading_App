"""AUD-REGIME-MARKETBLIND (2026-09-22) — regime is now scoped to the stock's own market.

`_fetch_market_regime()` hardcoded `params={"market": "US"}` for EVERY stock, so HK signals
had their style decision (`_decide_style`) and their main compression gates driven by SPY and
VIX rather than by HSI.

Measured in production: **2,462 of 2,604 HK BUY outcomes (94.5%) were tagged regime "bull"**.
Confirmed live that the two markets genuinely disagree at the same instant — the US endpoint
returned "bull" (SPY above 20/50EMA, VIX 14.9) while HK returned "choppy" (HSI 1.9% below its
SMA200). That matters beyond tidiness: in this platform's own measured data, `bull` is the
regime where BUY signals lose heavily (n=12,570, alpha -2.96%, t=-30.3) while `choppy` is
roughly flat (n=475, -0.21%, not significant), and decision-engine's `_REGIME_MULT` sizes
choppy at 0.75 versus bull at 1.00. So a wrongly-bullish HK regime both loosened the gates and
raised the size.

The file already knew this was wrong — the T224-B comment at the `_fetch_hsi_regime()` call
site reads "US SPY/VIX regime is irrelevant for HK timing" — but that fix wired HSI into only
ONE gate (`hsi_bear_gate`) and left the main regime path market-blind.

Deliberately NOT changed: `fear_greed`. `/stocks/fear_greed` takes no market parameter (it is a
US crowd-sentiment index), so HK callers still receive the US reading. Faking an HK variant
would be a new unvalidated signal, not a fix.

signals.py cannot be imported in this test environment, so these are source-level checks —
matching this file's established convention. See
docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md.
"""
import pathlib
import re

_SIGNALS_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "generators" / "signals.py"
)
_SOURCE = _SIGNALS_PATH.read_text()


def _fetch_market_regime_body() -> str:
    start = _SOURCE.index("def _fetch_market_regime(")
    end = _SOURCE.index("\ndef ", start + 1)
    return _SOURCE[start:end]


# ── The hardcode is gone ─────────────────────────────────────────────────────────────────────

def test_regime_fetch_no_longer_hardcodes_market_us():
    """The exact regression this file exists to prevent."""
    assert '"market": "US"' not in _fetch_market_regime_body()


def test_regime_fetch_takes_a_market_parameter_defaulting_to_us():
    """Default "US" keeps every other caller's behaviour unchanged."""
    assert 'def _fetch_market_regime(market: str = "US")' in _SOURCE


def test_regime_request_passes_the_parameter_through():
    assert '"market": market' in _fetch_market_regime_body()


# ── The call site actually scopes it ─────────────────────────────────────────────────────────

def test_call_site_passes_hk_for_hk_symbols():
    """Presence of the parameter is useless if the one real caller never varies it."""
    start = _SOURCE.index("market_regime, fg_score = _fetch_market_regime(")
    call = _SOURCE[start:start + 220]
    assert '"HK" if symbol.upper().endswith(".HK") else "US"' in call


def test_no_unscoped_regime_call_is_reintroduced():
    """Guards the precise regression shape: a real, argument-less INVOCATION assigned to a
    variable. Matching bare `_fetch_market_regime(` instead would also hit the several
    docstring references to it by name, which are prose, not calls.
    """
    unscoped = re.findall(r"=\s*_fetch_market_regime\(\s*\)", _SOURCE)
    assert unscoped == [], (
        f"found {len(unscoped)} unscoped _fetch_market_regime() call(s) — every call must "
        "pass the stock's own market"
    )


def test_market_test_matches_the_files_existing_hk_convention():
    """Uses the SAME .HK suffix test the hsi_regime block already relies on — a divergent
    market test would let the two regime paths disagree about what counts as an HK stock."""
    assert 'symbol.upper().endswith(".HK")' in _SOURCE
    assert _SOURCE.count('symbol.upper().endswith(".HK")') >= 2


# ── The deliberate exclusion is preserved ────────────────────────────────────────────────────

def test_fear_greed_is_deliberately_not_market_scoped():
    """/stocks/fear_greed takes no market param. If someone later passes one, it would be
    silently ignored by the endpoint while looking market-aware here — worse than the honest
    current limitation, which is documented in the docstring."""
    body = _fetch_market_regime_body()
    fg_start = body.index("/stocks/fear_greed")
    fg_call = body[fg_start - 120:fg_start + 120]
    assert "params=" not in fg_call


def test_the_limitation_is_documented_not_silent():
    body = _fetch_market_regime_body()
    assert "fear_greed" in body and "NOT market-scoped" in body
