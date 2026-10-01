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


def test_risk_reducing_exits_are_not_gated_by_entry_caps_on_the_scheduled_path(probe):
    """The requirement: an account over its concentration limit must still be able to REDUCE
    risk.

    SCOPE: verified for `_monitor_positions`, the scheduled exit path — the cap names present
    there are a warning log and no return/continue is guarded by one. This is NOT a claim about
    every exit route; source inspection of one function cannot establish that manual exits,
    liquidation, conditional-order exits or broker-side closes behave the same way. Those are
    unverified."""
    e = probe["exits_when_capped"]
    assert e["cap_usage_is_warning_only"] is True
    assert e["exit_returns_guarded_by_a_cap"] == [], \
        "an exit must never be skipped because an entry cap is breached"


# ── Findings: gaps in what the caps can see ───────────────────────────────────────────────────

def test_the_witness_fixture_no_longer_breaches_the_sector_cap(probe):
    """THE DEFECT WITNESS, PRESERVED. This is the exact fixture that reproduced
    M15-DEFECT-CONCURRENT-CAP on 2026-10-01: two candidates sized against the SAME pre-fetched
    snapshot, so neither saw the other. It opened **20.02% of equity** in one sector against a
    **15%** cap — each entry individually legal, jointly over.

    The fixture is unchanged; only the expected outcome is. The second entry is now refused,
    because `exposure.reserve()` reads committed exposure fresh under a portfolio row lock
    instead of trusting the snapshot.

    Keeping the witness matters: if the reservation is ever bypassed or the fresh read reverts
    to a caller-supplied snapshot, this test fails with the original symptom rather than
    disappearing quietly."""
    c = probe["concurrent_entries"]
    assert c["first"] == "opened"
    assert c["second_same_snapshot"] == "sector_cap"
    assert c["breaches_sector_cap"] is False
    assert c["combined_pct_of_equity"] <= c["sector_cap_pct"]


# ── Reservation lifecycle ─────────────────────────────────────────────────────────────────────

def test_a_crashed_worker_does_not_block_entries_forever(probe):
    """A reservation held by a dead worker blocks while live — that is the point — and is
    reclaimed when its TTL lapses, both at read time and by the sweep, so the portfolio is never
    frozen by a crash."""
    w = probe["worker_crash"]
    assert w["blocked_while_held"] == "sector_cap"
    assert w["reserved_value_after_ttl"] == 0.0
    assert w["state"] == "expired" and w["expired_by_sweep"] >= 1
    assert w["reserve_after_expiry"] == "reserved"


def test_consuming_a_reservation_does_not_double_count_the_exposure(probe):
    """Once the position exists it carries the exposure. If the reservation kept counting, one
    entry would consume twice its own room and the cap would be wrong in the SAFE direction —
    still wrong, and it would block legitimate entries."""
    n = probe["no_double_count"]
    assert n["consumed"] is True
    assert n["reserved_before_consume"] == 10000.0
    assert n["reserved_after_consume"] == 0.0
    assert n["committed_after_consume"] == 10000.0
    assert n["total_counted_once"] == 10000.0


def test_a_rejected_entry_releases_its_exposure_immediately(probe):
    """A later gate rejecting the entry must not leave exposure claimed for the whole TTL, or
    one rejected candidate would crowd out the rest of the cycle."""
    r = probe["release"]
    assert r["held"] == 12000.0 and r["released"] is True and r["after"] == 0.0


def test_release_is_idempotent_by_refusal(probe):
    assert probe["release"]["second_release_is_refused"] is False


def test_terminal_reservations_cannot_be_reused(probe):
    """A consumed reservation cannot be released back into the pool, and an expired one cannot
    be consumed — the latter is why `_open_paper_trade` logs a warning when consume fails: the
    cap was not enforced atomically for that trade and reconciliation needs to see it."""
    sm = probe["state_machine"]
    assert sm["release_a_consumed_reservation"] is False
    assert sm["consume_an_expired_reservation"] is False


def test_reconciliation_surfaces_a_consumed_reservation_with_no_position(probe):
    """Means the entry was rolled back after its reservation was consumed. Distinguished from an
    ordinary close by the caller supplying the live open-trade ids."""
    o = probe["reconciliation_orphaned"]
    assert o["reconciles"] is False
    assert "dc-1" in o["consumed_without_open_trade"]
    assert o["expired_unconsumed"] >= 1


def test_an_UNVALUABLE_position_fails_closed(probe):
    """Opening a position is the risk-INCREASING action, so a cap that cannot be computed at all
    refuses it. Protective exits never route through this module and are never blocked by it.

    SCOPE, because it changes what this currently buys: the production caller substitutes
    `entry_price` whenever a live mark is missing and so NEVER reports a position as unvaluable.
    `exposure_unknown_mark` is therefore unreachable from the live entry path today — it guards
    a future caller that reports unvaluable positions honestly. The case that actually occurs in
    production is the FALLBACK below, which is permitted."""
    f = probe["fail_closed"]
    assert f["unvaluable_position"] == "exposure_unknown_mark"


def test_a_FALLBACK_mark_produces_a_number_that_is_not_current_exposure(probe):
    """The distinction that matters. A fallback is not an absence of a number — it is a number
    from a substitute source. It can understate or overstate current exposure, and it is
    currently ALLOWED to decide a cap."""
    f = probe["fail_closed"]
    assert f["stale_mark_default"] == "reserved", "a fallback does not block an entry today"
    assert f["stale_mark_when_fresh_required"] == "exposure_stale_mark"


def test_fresh_mark_enforcement_is_measured_in_shadow_before_being_enabled(probe):
    """`require_fresh_marks` stays OFF. The telemetry records what it WOULD have refused, so the
    decision to enable it can rest on a measured block rate rather than a guess — the same
    shadow-first discipline the anti-chase counters use."""
    sh = probe["shadow_fresh_marks"]
    assert sh["with_fallback"]["fallback_marks"] >= 1
    assert sh["with_fallback"]["would_block_on_fresh_marks"] is True
    assert sh["all_fresh"]["would_block_on_fresh_marks"] is False
    assert sh["entry_still_allowed_with_fallback"] == "reserved"


def test_stale_mark_policy_is_built_but_not_switched_on(probe):
    """`require_fresh_marks` refuses an entry when any position in the sector was valued by
    fallback. It defaults to False, preserving today's behaviour — the mechanism exists and is
    tested, the policy change is a separate decision."""
    f = probe["fail_closed"]
    assert f["stale_mark_default"] == "reserved"
    assert f["stale_mark_when_fresh_required"] == "exposure_stale_mark"


def test_rollback_is_not_asserted_under_this_driver_configuration_and_says_why(probe):
    """NOT a claim that SQLite cannot roll back. Under the DEFAULT pysqlite configuration used
    here, the driver does not emit BEGIN for DML, so a released SAVEPOINT is already durable and
    `session.rollback()` does not undo it. SQLite itself supports rollback, and the documented
    workaround (isolation_level=None plus an explicit BEGIN) would restore it.

    That is a driver/transaction-configuration property, not a property of the reservation, so
    the guarantee is asserted on PostgreSQL rather than claimed falsely here."""
    r = probe["reservation_rollback"]
    assert r["reserve_reason"] == "reserved"
    assert r["authoritative_on_this_engine"] is False
    assert "pysqlite" in r["why"]


def test_a_crashed_commit_does_not_free_capacity(probe):
    """`committing` is excluded from the expiry sweep: the trade may already exist, or its
    broker outcome may be unknown. Reclaiming there would let another candidate take room that
    is about to be occupied. Asserted in full against PostgreSQL."""
    assert probe["worker_crash"]["state"] == "expired", \
        "a plain RESERVED row still expires; only `committing` is protected"


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

    In the fixture — entry 100, live 200 — the value used when the mark is missing is **100**.
    The 50% figure is a property of THAT FIXTURE, not an estimate of production exposure: the
    understatement equals the position's unrealised gain, so it is zero for a flat position and
    unbounded for a large winner. The finding is the mechanism, not a magnitude."""
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
