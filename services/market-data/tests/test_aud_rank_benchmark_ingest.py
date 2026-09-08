"""AUD-RANK-BENCHINGEST / AUD-ING6-MARKETINFER — benchmark ETFs were never kept fresh.

Two findings that only surfaced while fixing the Ranking/K-Score audit's XLP staleness:

1. AUD-RANK-BENCHINGEST — `_symbols_for()` filters `Stock.active.is_(True)`, and EVERY benchmark
   ETF is seeded active=False. So the TIER94 block inside _refresh_market() is the only thing
   that ingests them. Its hardcoded list — the FOURTH copy of the sector-ETF set in this
   codebase — silently omitted XLP, which is why XLP sat 102 days stale in production while
   ranking-engine kept computing a confidently-wrong Consumer-Staples relative strength from
   its ancient bars. The other 6 ETFs were fresh, so nothing looked broken.

2. AUD-ING6-MARKETINFER — adapter selection derives the market from the SYMBOL SUFFIX
   (`symbol.endswith(".HK") or market == "HK"`) while `allow_zero_volume` tested only the
   `market` PARAMETER. `ingest_universe()` never passes a market, so it always defaulted to
   "US": an HK symbol was routed to the correct HK adapter while simultaneously being held to
   the strict US `volume > 0` rule. That is precisely the defect AUD-ING6-HKZEROVOLUME fixed
   for the explicit-market path, still reachable through any caller that omits the argument.
"""
import pathlib

import src.services.ingestion as ing

SCHED_SRC = (pathlib.Path(ing.__file__).parent / "scheduler.py").read_text()
ING_SRC = pathlib.Path(ing.__file__).read_text()


def _etf_block() -> str:
    """The TIER94 sector-ETF ingestion block inside _refresh_market()."""
    i = SCHED_SRC.index("# TIER94: Keep sector ETF prices fresh")
    return SCHED_SRC[i:SCHED_SRC.index("# Stage 2:", i)]


# ── AUD-RANK-BENCHINGEST ────────────────────────────────────────────────────────────────

def test_sector_etf_list_is_derived_not_hardcoded():
    """A hardcoded 4th copy is what let XLP go missing. Deriving from the canonical map means
    adding a sector cannot leave its benchmark unfed again."""
    block = _etf_block()
    assert "_SECTOR_ETF_MAP" in block, "must derive from the canonical sector map"
    assert '"XLK", "XLF"' not in block, "the hardcoded list must be gone"


def test_every_benchmark_etf_including_xlp_is_now_ingested():
    """THE REGRESSION. XLP is the one that was missing; assert the whole set so a future
    hand-edit cannot drop a different one."""
    from src.services.paper_trading_engine import _SECTOR_ETF_MAP
    derived = sorted(set(_SECTOR_ETF_MAP.values()) | {"SPY"})
    assert "XLP" in derived, "XLP — the ETF that sat 102 days stale — must be covered"
    for etf in ("XLK", "XLF", "XLV", "XLE", "XLY", "XLU", "XLI", "XLB", "XLC", "XLRE", "SPY"):
        assert etf in derived, f"{etf} regressed out of the benchmark set"


def test_the_hk_benchmark_proxy_is_kept_fresh_too():
    """2800.HK is seeded active=False exactly like the US benchmark ETFs, so without its own
    ingest call it would go stale the same way XLP did — silently re-breaking the HK
    relative-strength fix as soon as its bars aged past the staleness window."""
    assert '"2800.HK"' in SCHED_SRC
    i = SCHED_SRC.index('ingest_universe(["2800.HK"]')
    ctx = SCHED_SRC[max(0, i - 400):i]
    assert 'market == "HK"' in ctx, "must run on the HK refresh, not the US one"


def test_benchmark_ingest_failures_are_logged_not_swallowed():
    """A silent benchmark-ingest failure reproduces the original bug."""
    block = _etf_block()
    assert "scheduler.sector_etf_ingest_failed" in block
    assert "scheduler.hk_benchmark_ingest_failed" in SCHED_SRC


def test_benchmark_ingest_failure_does_not_abort_the_refresh():
    """Benchmarks are an input to ranking, not to price ingestion — a benchmark failure must
    not take down the whole market refresh."""
    block = _etf_block()
    assert "except Exception" in block
    assert "raise" not in block


def test_symbols_for_still_excludes_inactive_so_this_job_remains_necessary():
    """Pins WHY the job exists. If someone made _symbols_for() include inactive stocks, the ETFs
    would be ingested twice; if they removed this job assuming _symbols_for() covers them, every
    benchmark would silently rot."""
    fn = SCHED_SRC[SCHED_SRC.index("def _symbols_for("):SCHED_SRC.index("_REDIS_REFRESH_FAILED_KEY")]
    assert "Stock.active.is_(True)" in fn


# ── AUD-ING6-MARKETINFER ────────────────────────────────────────────────────────────────

def test_zero_volume_rule_infers_market_from_the_symbol():
    """The parameter alone was wrong: ingest_universe() omits it, so every HK symbol reached
    the strict US volume gate."""
    i = ING_SRC.index("allow_zero_volume = (")
    block = ING_SRC[i:ING_SRC.index("last_err:", i)]
    assert "_effective_market" in block
    assert 'symbol.endswith(".HK")' in ING_SRC[max(0, i - 900):i], \
        "the effective market must be derived from the suffix, as adapter selection does"


def test_market_inference_matches_adapter_selection_exactly():
    """The two must not drift apart again — that divergence IS the bug."""
    assert '_effective_market = "HK" if (symbol.endswith(".HK") or market == "HK") else market' in ING_SRC
    # adapter selection's own form, a few lines above
    assert 'symbol.endswith(".HK") or market == "HK"' in ING_SRC


def _allow_zero(symbol: str, market: str, timeframe: str) -> bool:
    """Mirrors the real derivation; the source assertions above pin the real operators."""
    eff = "HK" if (symbol.endswith(".HK") or market == "HK") else market
    return (eff == "US" and timeframe not in ("1d", "1w")) or eff == "HK"


def test_hk_symbol_allows_zero_volume_even_without_an_explicit_market():
    """THE FIX: this is the ingest_universe() path, which passes no market at all."""
    assert _allow_zero("2800.HK", "US", "1d") is True
    assert _allow_zero("1671.HK", "US", "1d") is True


def test_explicit_hk_market_still_works():
    assert _allow_zero("2800.HK", "HK", "1d") is True


def test_us_daily_stays_strict():
    """The US rule is deliberately strict — a zero-volume US daily bar is a data error, and
    relaxing it would let bad bars through. This fix must not widen that."""
    assert _allow_zero("AAPL", "US", "1d") is False
    assert _allow_zero("AAPL", "US", "1w") is False


def test_us_intraday_still_allows_zero_volume():
    """prepost=True premarket bars legitimately carry zero volume (T230-CHARTING-PREMARKET)."""
    assert _allow_zero("AAPL", "US", "5m") is True


def test_relaxing_still_lowers_the_floor_rather_than_skipping_the_check():
    """AUD-ING6-HKZEROVOLUME's own lesson: skipping the check lets a NEGATIVE volume through."""
    assert 'df[df["volume"] >= 0] if allow_zero_volume else df[df["volume"] > 0]' in ING_SRC
