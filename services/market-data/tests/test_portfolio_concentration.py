"""M15 — portfolio concentration limits, exercised with SYNTHETIC positions.

The register's own note: "test limits now with synthetic positions; do not wait for trades to
test a hard limit." Waiting for live exposure means the first time a hard limit binds is in
production, on real money, and a limit that has never bound is a limit nobody has evidence works.

These call the REAL `_open_paper_trade` — the same function organic entries and conditional
orders both route through — against a real database with hand-built open positions. The cap
arithmetic is not reimplemented here; reimplementing logic under test is how a test ends up
asserting its own copy of a bug.

SOME OF THESE ASSERT A GAP. Where the current implementation does not account for something,
the test pins the CURRENT behaviour and says so, so the gap is visible and a later fix has a
failing test to flip. They are findings, not failures.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_concentration_probe.py"


@pytest.fixture(scope="module")
def probe():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                          timeout=300)
    if proc.returncode != 0:
        pytest.fail(f"concentration probe failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return json.loads(proc.stdout)


# ── What works ────────────────────────────────────────────────────────────────────────────────

def test_existing_exposure_binds_the_sector_cap(probe):
    """Three open positions at the cap; a fourth is refused. The limit does bind on exposure
    that is already open — which is the case it was written for."""
    assert probe["existing_exposure_binds"]["reason"] == "sector_cap"


def test_risk_reducing_exits_are_not_gated_by_entry_caps(probe):
    """The requirement: an account over its concentration limit must still be able to REDUCE
    risk. Entry caps and exits live in different functions, and the cap names that do appear in
    the exit path are a warning log — no return or continue is guarded by one."""
    e = probe["exits_when_capped"]
    assert e["cap_usage_is_warning_only"] is True
    assert e["exit_returns_guarded_by_a_cap"] == [], \
        "an exit must never be skipped because an entry cap is breached"


# ── Findings: gaps in what the caps can see ───────────────────────────────────────────────────

def test_FINDING_concurrent_entries_can_jointly_breach_a_sector_cap(probe):
    """Both candidates are sized against the SAME pre-fetched snapshot, so neither sees the
    other. Measured: two entries opened **20.02% of equity** in one sector against a **15%**
    cap — each individually legal, jointly over.

    `prefetched_open` is captured once before the candidate loop (AUD19-PERF2, to avoid a query
    per candidate). That is a real performance fix; the cost is that within one scan cycle the
    caps are evaluated against a stale view of the portfolio."""
    c = probe["concurrent_entries"]
    assert c["first"] == "opened" and c["second_same_snapshot"] == "opened"
    assert c["breaches_sector_cap"] is True
    assert c["combined_pct_of_equity"] > c["sector_cap_pct"]


def test_FINDING_pending_and_unfilled_orders_are_invisible_to_the_caps(probe):
    """The snapshot query selects `PaperTrade.stage == "open"` and nothing else, so committed
    but unfilled exposure — a working conditional order, an accepted broker order not yet
    filled — contributes zero to every concentration check."""
    p = probe["pending_orders_counted"]
    assert 'PaperTrade.stage == "open"' in p["snapshot_query_filters"]
    assert p["mentions_pending_or_order_state"] is False


def test_FINDING_a_missing_mark_values_a_position_at_its_entry_price(probe):
    """`_best_price` falls back to `entry_price` when no live mark exists. A position that has
    doubled is then counted at HALF its real value, so concentration is understated exactly when
    a winner has grown into the risk the cap exists to limit.

    Measured: entry 100, live 200, value used when the mark is missing **100** — a 50%
    understatement."""
    m = probe["stale_marks"]
    assert m["priced_with_live_mark"] == 200.0
    assert m["price_when_mark_missing"] == 100
    assert m["exposure_understated_by_pct"] == 50.0


def test_FINDING_concentration_sums_local_currency_without_conversion(probe):
    """A 300,000 HKD position is summed raw against USD equity — about 3x equity, when its true
    weight is roughly 38%.

    LATENT, NOT LIVE: a portfolio carries a single `cfg["market"]`, so US and HK holdings do not
    currently share one book. The arithmetic is nonetheless currency-naive, and would be wrong
    the moment a mixed-currency portfolio existed."""
    c = probe["currency"]
    assert c["raw_ratio_vs_equity"] == 3.0
    assert "no FX conversion" in c["note"]


def test_FINDING_there_is_no_ordered_versus_filled_distinction(probe):
    """`PaperTrade.shares` is the only quantity field. A partial fill is representable only by
    writing the filled quantity, so the model cannot express "ordered 100, filled 30" — and
    therefore cannot reserve the unfilled 70 against a cap."""
    p = probe["partial_fills"]
    assert p["shares_field_is_filled_quantity"] == 30
    assert "no ordered-vs-filled distinction" in p["note"]


def test_FINDING_assigned_option_exposure_has_no_representation(probe):
    """Concentration reads `PaperTrade` rows with `stage='open'`, and `PaperTrade` carries no
    option or assignment fields. Shares delivered by assignment therefore reach the caps only if
    something writes an ordinary open trade for them."""
    o = probe["options_assignment"]
    assert o["paper_trade_has_option_fields"] == []
    assert "stage='open'" in o["concentration_reads"]
