"""T384-LEAPS-UNIVERSE — extending option-chain capture beyond the QQQ family.

USER REQUEST: "extend the leaps calls to more symbols? Is it possible?" then "help me to
define which symbols would be good candidates for Leap Calls? Is it a more stable and strong
company?"

THE PREMISE NEEDED CORRECTING, and that correction drove the selection. "Stable and strong" is
NOT the right filter. A 0.70-delta call held ~12 months only wins if the underlying TRENDS UP
enough to beat theta plus the bid/ask spread — a rock-solid company trading sideways for a year
loses most of the premium. T381 measured this directly: TQQQ returned **-73.78%** over a 336-day
hold while tracking an index the user is bullish on, because leveraged decay ate it.

SELECTION CRITERIA, every one measured against this platform's own price history:
  * 2-year return > +30% AND 1-year return > +15%  — a persistent trend, not one pop
  * max 1-year drawdown better than -40%           — the hole a LEAPS must sit through
  * a deep, liquid option chain
  * NOT leveraged

THE LIQUIDITY BAR IS LOWER THAN T380/T381 IMPLIED — calibrated on the 13 already-captured
symbols, and this is the finding that made the expansion safe:

    every mega-cap SINGLE STOCK: 100% of captured days have a delta-selectable >=330-DTE LEAPS
    (AAPL/MSFT/NVDA/META/AMZN/TSLA/AMD/PLTR all 130/130; SPY/QQQ/TQQQ all 549/549)
    thin ETFs degrade:           QLD 82%, QQQM 50%

So QLD's T380 failure was **ETF-specific**, not a general liquidity problem.

DELIBERATELY EXCLUDED: the top raw-return names (SNDK +1731%, LITE +419%, AAOI, BE, FCEL) —
buying a 0.70-delta LEAPS after a 17x run is buying the top, and that profile mean-reverts.
Also every leveraged product, and thin chains (ASX 278 contracts, RVMD 860).

A FACT THAT CHANGED DURING THIS WORK: T374 recorded UW's history reaching ~2023-10-11. Probed
2026-09-14, that date now returns **403** and bisection puts the boundary at **2023-10-23** —
the rolling window has moved ~12 days forward. That is the whole argument for capturing sooner
rather than later: uncaptured days do not merely stay uncaptured, they become UNCAPTURABLE.

NOT MEASURED, recorded as an honest gap: IV rank. UW's /api/stock/{symbol}/iv-rank returned
nothing usable for all 22 probed candidates, so IV — which a LEAPS buyer PAYS at entry — is an
unverified dimension of this list.
"""
import ast
import pathlib

import pytest

SCHED = pathlib.Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
SRC = SCHED.read_text()


def _symbols() -> list[str]:
    i = SRC.index("_OPTHIST_SYMBOLS = [")
    return ast.literal_eval(SRC[i + len("_OPTHIST_SYMBOLS = "):SRC.index("\n]", i) + 2])


# ── The list itself ─────────────────────────────────────────────────────────────────────

def test_the_original_thirteen_are_all_retained():
    """The QQQ-family comparison set (T374) and the original ten back the
    qqq-leaps-playbook page and every existing captured history. Dropping one would orphan
    549 days of archive."""
    syms = _symbols()
    for s in ("SPY", "QQQ", "META", "TSLA", "AMD", "NVDA", "MSFT", "AAPL", "AMZN", "PLTR",
              "QQQM", "QLD", "TQQQ"):
        assert s in syms, s


def test_all_eighteen_new_candidates_are_present():
    syms = _symbols()
    for s in ("TSM", "GOOG", "AVGO", "MU", "DELL", "HPE",
              "JPM", "CAT", "RTX", "GEV", "CRWD", "NET",
              "SMH", "SOXX", "XLK", "GLD"):
        assert s in syms, s


def test_no_duplicates():
    """MSFT and NVDA were in the ORIGINAL ten and are also core LEAPS candidates. A duplicate
    would double every capture request for them, forever."""
    syms = _symbols()
    dupes = [s for s in set(syms) if syms.count(s) > 1]
    assert dupes == [], f"duplicate capture symbols: {dupes}"


def test_every_symbol_is_a_plain_uppercase_ticker():
    for s in _symbols():
        assert s == s.upper() and s.isalnum(), s


# ── What must NOT be in the list ────────────────────────────────────────────────────────

@pytest.mark.parametrize("sym", ["SQQQ", "TNA", "SPXL", "UPRO", "LABU"])
def test_no_new_leveraged_products_were_added(sym):
    """MEASURED: TQQQ returned -73.78% over a 336-day hold in T381 while the index it tracks
    rose. Leveraged decay is fatal to a year-long LEAPS.

    TQQQ and QLD stay ONLY because they are the pre-existing T374 comparison set that the
    qqq-leaps-playbook page is built around — they are a deliberate counter-example, not an
    endorsement.

    SOXL is DELIBERATELY absent from this list now — see test_soxl_and_aaoi_are_explicit_
    overrides_not_silent_reversals below. It was added on explicit user request 2026-09-18,
    which is a decision this test must not silently re-forbid."""
    assert sym not in _symbols()


@pytest.mark.parametrize("sym", ["SNDK", "LITE", "BE", "FCEL", "RXT"])
def test_the_highest_raw_return_names_were_excluded(sym):
    """SNDK was +1731% over one year. Buying a 0.70-delta LEAPS after a 17x run is buying the
    top, and that profile mean-reverts — the opposite of the persistent trend a LEAPS needs.

    AAOI is DELIBERATELY absent from this list now — see test_soxl_and_aaoi_are_explicit_
    overrides_not_silent_reversals below."""
    assert sym not in _symbols()


def test_soxl_and_aaoi_are_explicit_overrides_not_silent_reversals():
    """2026-09-18: the user asked for SOXL and AAOI by name, alongside INTC/MRVL/CRWV/AAOX.
    Both were previously EXCLUDED by this file's own stated criteria — AAOI as a post-17x-run
    name (test above, until this change), SOXL as a leveraged product (ditto). Neither
    measurement was wrong; the user's request overrides the criteria for these two names.

    This test exists so that reversal is a decision on record, not a quietly weakened
    parametrize list. If this test is ever the only thing keeping SOXL/AAOI in the universe,
    that is the correct place for that fact to live."""
    assert {"SOXL", "AAOI"} <= set(_symbols())


@pytest.mark.parametrize("sym", ["ASX", "RVMD"])
def test_thin_chains_were_excluded(sym):
    """ASX had 278 contracts and RVMD 860, against >=1,000 for every included name. A thin
    chain is what made QLD unpriceable in T380 — greeks exist only where volume > 0."""
    assert sym not in _symbols()


def test_the_list_stays_small_enough_to_be_affordable():
    """Each symbol costs 1 UW request per captured day and ~1 MB/trading-day of disk. At 29
    symbols the daily job is ~174 requests (6-day self-heal window) against a 120k/day budget,
    and steady-state disk is ~19 GB against 64 GB free. A list that grew unbounded would
    quietly recreate the EBS-I/O incident this platform already had."""
    assert len(_symbols()) <= 40


# ── The reasoning must survive in the source ────────────────────────────────────────────

def _block() -> str:
    i = SRC.index("_OPTHIST_SYMBOLS = [")
    return SRC[i:SRC.index("\n]", i)]


def test_the_measured_criteria_are_recorded():
    """So the next person extending this list applies the same bar rather than adding whatever
    is trending that week."""
    b = _block()
    assert "2-year return" in b and "1-year return" in b
    assert "drawdown" in b.lower()


def test_the_tqqq_counterexample_is_recorded():
    """The single most important number for anyone reasoning about LEAPS candidate quality."""
    assert "-73.78%" in _block()


def test_the_liquidity_calibration_is_recorded():
    """QLD 82% / QQQM 50% vs 100% for mega-cap single stocks — the measurement that showed the
    T380 failure was ETF-specific and made this expansion safe."""
    b = _block()
    assert "QLD 82%" in b and "QQQM 50%" in b


def test_the_unmeasured_iv_gap_is_disclosed():
    """IV is PAID at entry. Leaving it unstated would imply it had been considered."""
    b = _block()
    assert "IV rank" in b
    assert "iv-rank" in b


def test_the_xlk_active_flag_inconsistency_is_flagged():
    """XLK carries active=false in `stocks` while still receiving fresh daily bars. Capture
    works, but silently depending on that would be the kind of thing that breaks later."""
    assert "active=false" in _block()


# ── The job that consumes the list is unchanged in shape ────────────────────────────────

def test_the_daily_job_still_uses_the_constant():
    assert "capture_option_chain_history(_OPTHIST_SYMBOLS, _start, _end, skip_existing=True)" in SRC


def test_skip_existing_is_still_on():
    """Without it the daily 6-day window would re-fetch already-settled days for all 29
    symbols every night — 29x the intended cost."""
    i = SRC.index("def _capture_option_chain_history_daily")
    assert "skip_existing=True" in SRC[i:i + 2000]


def test_the_retention_policy_still_exists():
    """29 symbols at ~1 MB/trading-day needs a cap, or the archive grows unbounded."""
    assert "_OPTHIST_RETENTION_DAYS = 800" in SRC
