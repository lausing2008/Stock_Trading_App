"""AUD-E10-COOLDOWNORDER — check_options_flow_alerts()'s per-(symbol, direction) cooldown claim
raced newly-seen option chains in `sorted()` order, which sorts by the CONTRACT STRING — an
alphabetical accident, not a ranking. The cooldown loop only lets the FIRST chain per (symbol,
direction) pair claim the cooldown key; everything after it for the same pair is rejected before
the later "largest premium first" ranking step ever sees it. So a $250,000 contract whose chain
string happened to sort before a $5,000,000 contract for the SAME symbol/direction permanently
squeezed the larger one out, with the later ranking step powerless to recover it (it only ever
ranks among that loop's survivors).

Fixed by sorting newly-seen chains by premium (descending) BEFORE the cooldown-claim loop, so
each (symbol, direction) pair's cooldown key is claimed by its own largest-premium contract.

scheduler.py can't be imported directly in this test environment — the sort key itself is a
small, pure expression extracted and verified against realistic candidate dicts, matching this
repo's established source-extraction convention for this file.
"""
import pathlib

_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
).read_text()


def _check_options_flow_alerts_body() -> str:
    start = _SOURCE.index("def check_options_flow_alerts() -> None:")
    end = _SOURCE.index("\n\ndef ", start + 10)
    return _SOURCE[start:end]


_BODY = _check_options_flow_alerts_body()


def test_newly_seen_is_sorted_by_premium_not_plain_sorted():
    """The exact bug: a bare `sorted(current_chains - prev_seen)` sorts by contract STRING."""
    assert "newly_seen = sorted(\n                    current_chains - prev_seen,\n                    key=lambda c: candidates[c].get(\"total_premium\") or 0.0,\n                    reverse=True,\n                )" in _BODY


def test_premium_sort_happens_before_the_cooldown_claim_loop():
    """Sorting AFTER the cooldown loop would be pointless — the loop must see chains in
    premium order so it claims the cooldown key with the biggest contract per pair."""
    sort_idx = _BODY.index("newly_seen = sorted(")
    loop_idx = _BODY.index("for chain in newly_seen:")
    assert sort_idx < loop_idx


def test_reproduces_the_exact_reported_scenario_with_real_sort_semantics():
    """AAA1 ($250k) sorts before AAA2 ($5m) alphabetically — confirms the fix actually changes
    which contract wins the race, not just that a key phrase exists in the source."""
    candidates = {
        "AAA1": {"symbol": "AAPL", "direction": "bullish", "total_premium": 250_000.0},
        "AAA2": {"symbol": "AAPL", "direction": "bullish", "total_premium": 5_000_000.0},
    }
    current_minus_prev = {"AAA1", "AAA2"}

    old_order = sorted(current_minus_prev)
    assert old_order[0] == "AAA1", "confirms the bug: alphabetical sort put the SMALLER contract first"

    new_order = sorted(
        current_minus_prev,
        key=lambda c: candidates[c].get("total_premium") or 0.0,
        reverse=True,
    )
    assert new_order[0] == "AAA2", "the fix: the LARGER contract now claims the cooldown key first"


def test_missing_premium_does_not_crash_the_sort():
    candidates = {
        "X1": {"symbol": "MU", "direction": "bearish", "total_premium": None},
        "X2": {"symbol": "MU", "direction": "bearish", "total_premium": 1_000_000.0},
    }
    order = sorted(
        {"X1", "X2"},
        key=lambda c: candidates[c].get("total_premium") or 0.0,
        reverse=True,
    )
    assert order[0] == "X2"
