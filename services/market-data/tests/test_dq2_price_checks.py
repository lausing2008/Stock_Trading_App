"""AUD-DQ2 — the two data-quality-check fixes from Area 2 of the 2026-09-08 ingestion audit.

Measured evidence (docs/2026-09-08/INGESTION_AUDIT_PARTIAL.md):

  2a PER-SYMBOL BLINDNESS — the price checks are `SELECT MAX(p.ts) ... WHERE market='US'`, a
     single aggregate over every symbol. If ANY one symbol updated in 48h the check passes, no
     matter how many others have died. That is why SSNLF (305 days stale) and SKHYV (53 days)
     went unnoticed for months — both hidden behind the MAX.

  2b MISSING MARKET TAG — T242-DQ1's closed-market guard keys on check.get("market"), and the
     two price checks were the only market-specific staleness checks WITHOUT it. Caught live
     2026-09-08: prices_us_d1 read ok=false / age_hours=99.3 purely because the last US bar was
     Friday 09-04, Monday 09-07 was Labor Day, and it was Tuesday pre-close. The data was
     completely healthy; the check was crying wolf.
"""
import pathlib
import re

import src.services.scheduler as sch

SRC = pathlib.Path(sch.__file__).read_text()


def _dq_entry(name: str) -> str:
    """Source text of one _DQ_CHECKS entry, from its "name" key to the closing brace."""
    i = SRC.index(f'"name": "{name}"')
    # Walk back to the opening brace of this dict, forward to its close.
    start = SRC.rindex("{", 0, i)
    depth, j = 0, start
    while j < len(SRC):
        if SRC[j] == "{":
            depth += 1
        elif SRC[j] == "}":
            depth -= 1
            if depth == 0:
                return SRC[start:j + 1]
        j += 1
    raise AssertionError(f"could not bound the {name} entry")


# ── 2b: the market tag ───────────────────────────────────────────────────────────────────

def test_price_checks_now_carry_a_market_tag():
    """THE CORE 2b FIX. Without it, T242-DQ1's closed-market guard cannot apply and the check
    reports stale over every weekend and holiday."""
    assert '"market": "US"' in _dq_entry("prices_us_d1")
    assert '"market": "HK"' in _dq_entry("prices_hk_d1")


def test_price_checks_match_their_own_query_market():
    """A US-tagged check querying HK data (or vice versa) would skip on the wrong calendar —
    worse than no tag, because it would go quiet exactly when it should fire."""
    us, hk = _dq_entry("prices_us_d1"), _dq_entry("prices_hk_d1")
    assert "st.market='US'" in us and '"market": "US"' in us
    assert "st.market='HK'" in hk and '"market": "HK"' in hk


def test_sibling_market_checks_still_tagged():
    """rankings_*/signals_* already had the tag; the fix must not have disturbed them."""
    for name in ("rankings_us", "signals_us"):
        assert '"market": "US"' in _dq_entry(name)
    for name in ("rankings_hk", "signals_hk"):
        assert '"market": "HK"' in _dq_entry(name)


def test_the_closed_market_guard_keys_on_that_exact_field():
    """Pins the coupling: the tag is only useful because the runner reads check.get("market").
    If the guard were rekeyed, these tags would silently stop working."""
    assert 'market = check.get("market")' in SRC
    assert 'if market == "US" and not _is_us_trading_day():' in SRC


# ── 2a: per-symbol staleness ─────────────────────────────────────────────────────────────

def test_per_symbol_staleness_check_exists():
    """THE CORE 2a FIX — a check that can actually see one dead symbol among many healthy ones."""
    entry = _dq_entry("stale_symbols_d1")
    assert '"source": "gauge"' in entry
    assert '"counter_fn": _count_stale_symbols_d1' in entry


def test_it_groups_per_symbol_rather_than_aggregating_the_market():
    """The whole point: a GROUP BY stock_id with a per-group HAVING, not one global MAX()."""
    import inspect
    body = inspect.getsource(sch._count_stale_symbols_d1)
    assert "GROUP BY p.stock_id" in body
    assert "HAVING MAX(p.ts)" in body
    assert "st.active" in body, "must only count symbols we still claim to track"


def test_gauge_contract_is_respected():
    """Gauges are read via counter_key or counter_fn and always report ok=True. My first attempt
    at this used a SQL `query` plus a `max_value` key — NEITHER of which the runner reads, so it
    would have raised on the missing counter_key. Pin the real contract."""
    entry = _dq_entry("stale_symbols_d1")
    assert '"max_value"' not in entry, "the runner does not read max_value"
    assert '"query"' not in entry, "gauges do not take a SQL query"
    assert '"counter_fn"' in entry or '"counter_key"' in entry


def test_counter_fn_is_defined_before_the_checks_list_uses_it():
    """A NameError at import time would take down the whole scheduler module."""
    assert SRC.index("def _count_stale_symbols_d1") < SRC.index('"counter_fn": _count_stale_symbols_d1')


def test_counter_fn_fails_to_zero_rather_than_raising():
    """A gauge that raises is counted as a query_error and reported as an INFRASTRUCTURE
    failure — a far louder signal than this housekeeping metric deserves."""
    import inspect
    body = inspect.getsource(sch._count_stale_symbols_d1)
    assert "except Exception" in body
    assert "return 0" in body
    assert "dq.stale_symbols_count_failed" in body, "must log, not swallow silently"


def test_stale_check_is_deliberately_not_market_tagged():
    """A symbol dead for a week is dead whether or not its market is open right now — tagging it
    would let the closed-market skip mask exactly what it exists to surface."""
    assert '"market"' not in _dq_entry("stale_symbols_d1")


def test_seven_day_window_is_wider_than_a_long_weekend():
    """The threshold must not trip on a normal holiday weekend (max ~4 calendar days closed)."""
    import inspect
    body = inspect.getsource(sch._count_stale_symbols_d1)
    m = re.search(r"INTERVAL '(\d+) days'", body)
    assert m and int(m.group(1)) >= 5


def test_it_would_have_caught_the_two_dead_tickers():
    """Behavioural intent: SSNLF (305d) and SKHYV (53d) both exceed the 7-day window, so a
    correct implementation counts them. Guards the window against being widened past the point
    of usefulness."""
    import inspect
    m = re.search(r"INTERVAL '(\d+) days'", inspect.getsource(sch._count_stale_symbols_d1))
    window = int(m.group(1))
    assert window < 53, "must be tight enough to catch SKHYV at 53 days stale"
