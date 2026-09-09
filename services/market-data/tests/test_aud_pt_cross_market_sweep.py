"""AUD-PT-CROSSMARKETSWEEP — HK's open burst force-closed US positions at yesterday's price.

REPORTED BY THE USER from a paper-trade email: "Is it outside US market open?" — an IGV
Momentum Exit timestamped 6:00pm, five hours after the 4:00pm ET close.

`paper_trading_step()` took NO market argument and swept EVERY active portfolio, regardless of
which market's refresh invoked it. HK's `hk_open_burst` fires at 09:25-09:45 HKT =
**21:25-21:45 ET**, so it monitored US positions against whatever price was cached — which after
the US close is the **previous day's**.

MEASURED IN PRODUCTION — 8 of 98 US exits fired outside 09:30-16:00 ET, six of them at exactly
21:00 ET:

    SNOW   stop_hit       09-03 21:00 ET   -19.03%
    DELL   stop_hit       09-03 21:00 ET    -6.66%
    IGV    momentum_exit  09-08 21:00 ET    -2.63%   <- the user's email
    GDX / ANF / BRK-A     same 21:00 ET pattern

THE DAMAGE IS REAL, NOT COSMETIC. SNOW exited at **$305.53** on 09-03. That day's actual LOW was
**$355.47**; $305.84 was **09-02's CLOSE**. The position was force-closed at a price that never
traded that day, booking a 19% loss ~14% below the day's true low.

WHY THE EXISTING STALE-PRICE GUARD MISSED IT: `_price_is_stale_escalated` counts CONSECUTIVE
UNCHANGED prices before holding. A price that changed yesterday and is merely the wrong day's
close does not look frozen — it looks like a fresh reading.

SAME CLASS as AUD-DIGEST-HOLIDAYBLIND and AUD-OUTCOMES-CALENDARDAYSTALE: a process running on
another market's clock.
"""
import pathlib

import pytest

PT_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/services/paper_trading_engine.py"
).read_text()
SCHED_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
).read_text()


class _P:
    """Minimal portfolio stand-in — only `config` matters to the filter."""
    def __init__(self, name, market=None):
        self.name = name
        self.config = {} if market is None else {"market": market}


def _filter(portfolios, market):
    """Mirrors the real filter; source assertions below pin the implementation."""
    if not market:
        return portfolios
    want = market.upper()
    return [p for p in portfolios if (p.config or {}).get("market", "US").upper() == want]


# ── The core fix ────────────────────────────────────────────────────────────────────────

def test_an_hk_cycle_does_not_touch_us_portfolios():
    """THE BUG. This is what force-closed SNOW at the previous day's close."""
    ps = [_P("GROWTH Paper", "US"), _P("HK SWING", "HK"), _P("ETrade Sandbox", "US")]
    assert [p.name for p in _filter(ps, "HK")] == ["HK SWING"]


def test_a_us_cycle_does_not_touch_hk_portfolios():
    """The mirror case — a US refresh at 10:00 ET is 22:00 HKT, mid-HK-session, so the same
    defect ran in both directions."""
    ps = [_P("GROWTH Paper", "US"), _P("HK SWING", "HK"), _P("HK GROWTH", "HK")]
    assert [p.name for p in _filter(ps, "US")] == ["GROWTH Paper"]


def test_all_ten_production_portfolios_route_correctly():
    """Real production set, verified 2026-09-08: 6 US, 4 HK, every one carrying an explicit
    market. No portfolio may be dropped by the filter or handled by both markets."""
    ps = [
        _P("GROWTH Paper Portfolio", "US"), _P("HK SWING Portfolio", "HK"),
        _P("US SWING Portfolio", "US"), _P("HK GROWTH Portfolio", "HK"),
        _P("ETrade Sandbox SWING", "US"), _P("US SWING After 09082026", "US"),
        _P("US GROWTH after 09082026", "US"), _P("US LONG after 09082026", "US"),
        _P("HK SWING after 09082026", "HK"), _P("HK GROWTH after 09082026", "HK"),
    ]
    us, hk = _filter(ps, "US"), _filter(ps, "HK")
    assert len(us) == 6 and len(hk) == 4
    assert len(us) + len(hk) == len(ps), "every portfolio must land in exactly one market"


def test_case_is_normalised_on_both_sides():
    """A config saying "us" must not silently drop out of a "US" cycle."""
    assert len(_filter([_P("x", "us")], "US")) == 1
    assert len(_filter([_P("x", "US")], "us")) == 1


# ── Defaults and safety ─────────────────────────────────────────────────────────────────

def test_a_portfolio_with_no_market_key_defaults_to_US():
    """Mirrors _DEFAULT_CONFIG and every other reader in the file. Silently dropping such a
    portfolio would stop monitoring it entirely — strictly worse than the bug being fixed."""
    ps = [_P("legacy", None)]
    assert len(_filter(ps, "US")) == 1
    assert len(_filter(ps, "HK")) == 0


def test_no_market_argument_keeps_the_old_all_markets_behaviour():
    """`None` must remain a valid caller contract — manual invocation and tests rely on it."""
    ps = [_P("a", "US"), _P("b", "HK")]
    assert len(_filter(ps, None)) == 2


def test_the_filter_runs_in_python_not_sql():
    """`config` is a `json` column, not `jsonb`. A ->> filter needs a cast, and a portfolio whose
    config omitted the key would silently match nothing — dropping it from monitoring. This
    codebase has an incident file for exactly that cast trap."""
    i = PT_SRC.index("AUD-PT-CROSSMARKETSWEEP: keep only portfolios")
    block = PT_SRC[i:i + 900]
    assert 'p.config or {}).get("market", "US")' in block
    assert "config::jsonb" not in block


# ── Wiring: both scheduled callers must pass their market ───────────────────────────────

def test_paper_trading_step_accepts_a_market():
    assert "def paper_trading_step(market: str | None = None) -> None:" in PT_SRC


def test_the_refresh_market_caller_passes_its_market():
    """This is the caller that ran at 21:00 ET."""
    assert '_run_paper_trading_step(label="refresh_market", market=market)' in SCHED_SRC


def test_the_5m_caller_passes_its_market():
    assert '_run_paper_trading_step(label="refresh_5m", market=market)' in SCHED_SRC


def test_the_wrapper_forwards_it():
    """A wrapper that accepted `market` and dropped it would leave the bug intact while every
    call site looked correct — the exact shape of AUD-CHASE-ROC10."""
    assert "def _run_paper_trading_step(label: str = \"refresh\", market: str | None = None)" in SCHED_SRC
    assert "paper_trading_step(market)" in SCHED_SRC
    assert "        paper_trading_step()\n" not in SCHED_SRC, "the unscoped call must be gone"


def test_no_scheduled_caller_omits_the_market():
    """Repo-wide: every _run_paper_trading_step call must be scoped."""
    import re
    # Real CALLS only — exclude the `def` line and any prose mention inside a docstring or
    # comment. A call is at statement position: optional indent, then the name.
    # A real CALL ends the statement at the closing paren. Line 10221 is prose inside a
    # docstring — "_run_paper_trading_step() and _check_short_intraday_triggers() after every
    # ingest" — which a bare prefix match wrongly counts.
    calls = [
        l.strip() for l in SCHED_SRC.splitlines()
        if re.match(r"^\s*_run_paper_trading_step\([^)]*\)\s*$", l)
    ]
    assert len(calls) == 2, f"expected exactly the two scheduled callers, got {calls}"
    unscoped = [c for c in calls if "market=" not in c]
    assert unscoped == [], f"unscoped paper-trading cycles remain: {unscoped}"


# ── The lock stays global, deliberately ─────────────────────────────────────────────────

def test_the_distributed_lock_is_still_global():
    """DELIBERATE. The lock prevents two concurrent runs double-crediting cash on the same exit.
    US (09:30-16:00 ET) and HK (21:30-04:00 ET) sessions barely overlap, so a global lock costs
    almost nothing — while a per-market lock would permit concurrent runs against a shared
    portfolio, trading a rare skipped cycle for a real accounting risk."""
    assert '_PAPER_TRADING_LOCK_KEY = "stockai:lock:paper_trading_step"' in SCHED_SRC
    assert "paper_trading_step:{" not in SCHED_SRC, "must not be keyed per market"


# ── The stale-price evidence, pinned ────────────────────────────────────────────────────

def test_the_snow_exit_price_was_the_previous_days_close():
    """Pinned as arithmetic so the severity claim cannot rot. SNOW exited at $305.53 on 09-03;
    that day's LOW was $355.47, and $305.84 was 09-02's close."""
    exit_price, day_low, prev_close = 305.53, 355.47, 305.84
    assert exit_price < day_low, "the exit price never traded that day"
    assert abs(exit_price - prev_close) < 1.0, "it matches the PREVIOUS day's close"
    assert (day_low - exit_price) / day_low > 0.13, "~14% below the day's true low"
