"""Tests for AUD261-CENSORING-NEVER-FIRED.

_lookup_outcome_price() (a nested closure inside evaluate_signal_outcomes()) used bisect_left
with NO upper bound — it returns the first (date, close) bar on or after the target date, no
matter how far in the future that bar actually is. A symbol with a long ingestion gap that
LATER RESUMES would silently return the first bar after the gap (potentially months later) as
if it were a normal, timely exit/entry price, instead of being censored by the already-correct
grace-window branch a few lines below (which only ever triggers on a bare `None` — never
reachable for exactly this "found something, just too late" case).

evaluate_signal_outcomes() can't be driven end-to-end in this test environment (250+ lines of
FastAPI/Depends/real-Postgres-shaped query construction) — following
test_evaluate_outcomes_nested_savepoint.py's established convention: _lookup_outcome_price()'s
own real source is extracted via exec() and run directly against a synthetic price map, since
it's a small, self-contained closure with no DB dependency of its own once its bucket/constant
inputs are supplied.
"""
import pathlib
from datetime import date, timedelta

_OUTCOMES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "outcomes.py"
_OUTCOMES_SOURCE = _OUTCOMES_PATH.read_text()

_REAL_GRACE_DAYS = 10  # must match signals_shared.py's real _OUTCOME_CENSOR_GRACE_DAYS


def _extract_lookup_outcome_price(price_map: dict[int, list[tuple]], grace_days: int = _REAL_GRACE_DAYS):
    """Pulls _lookup_outcome_price()'s real function body out of outcomes.py and exec()s it
    against a synthetic _outcome_price_map, matching the real closure's own dependency shape
    exactly (bisect, _outcome_price_map, _OUTCOME_CENSOR_GRACE_DAYS)."""
    import bisect

    start = _OUTCOMES_SOURCE.index("def _lookup_outcome_price(stock_id: int, on_or_after:")
    end = _OUTCOMES_SOURCE.index("\n\n    def _window_return(", start)
    func_source = _OUTCOMES_SOURCE[start:end]
    namespace = {
        "bisect": bisect,
        "_outcome_price_map": price_map,
        "_OUTCOME_CENSOR_GRACE_DAYS": grace_days,
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_lookup_outcome_price"]


def test_returns_the_exact_bar_when_on_or_after_matches_a_real_date():
    price_map = {1: [(date(2026, 1, 5), 100.0), (date(2026, 1, 6), 101.0)]}
    fn = _extract_lookup_outcome_price(price_map)
    assert fn(1, date(2026, 1, 5)) == (date(2026, 1, 5), 100.0)


def test_returns_the_next_bar_when_the_exact_target_date_has_no_bar_but_one_arrives_soon_after():
    """A normal weekend/holiday gap (well within the grace window) must still resolve to a
    real price — this is the common, healthy case the fix must not break."""
    price_map = {1: [(date(2026, 1, 5), 100.0), (date(2026, 1, 8), 102.0)]}  # Fri, then Mon
    fn = _extract_lookup_outcome_price(price_map)
    assert fn(1, date(2026, 1, 6)) == (date(2026, 1, 8), 102.0)  # Sat -> next real bar, 3 days later


def test_returns_none_when_the_nearest_bar_is_beyond_the_grace_window():
    """The exact regression this fix targets: a symbol resumed ingestion 30 days after a gap
    — the nearest available bar is real, but far too late to be a legitimate exit fill."""
    price_map = {1: [(date(2026, 1, 5), 100.0), (date(2026, 2, 20), 500.0)]}  # 46-day gap
    fn = _extract_lookup_outcome_price(price_map)
    assert fn(1, date(2026, 1, 6)) is None


def test_returns_none_when_no_bucket_exists_for_the_stock():
    fn = _extract_lookup_outcome_price({})
    assert fn(999, date(2026, 1, 5)) is None


def test_returns_none_when_no_bar_exists_on_or_after_the_target_at_all():
    price_map = {1: [(date(2026, 1, 1), 100.0)]}
    fn = _extract_lookup_outcome_price(price_map)
    assert fn(1, date(2026, 1, 5)) is None


def test_boundary_exactly_at_the_grace_window_still_resolves():
    """Exactly _OUTCOME_CENSOR_GRACE_DAYS late is still within the grace window (the caller's
    own censoring branch uses a strict > comparison, matched here)."""
    target = date(2026, 1, 6)
    bar_date = target + timedelta(days=_REAL_GRACE_DAYS)
    price_map = {1: [(bar_date, 100.0)]}
    fn = _extract_lookup_outcome_price(price_map)
    assert fn(1, target) == (bar_date, 100.0)


def test_boundary_one_day_past_the_grace_window_is_rejected():
    target = date(2026, 1, 6)
    bar_date = target + timedelta(days=_REAL_GRACE_DAYS + 1)
    price_map = {1: [(bar_date, 100.0)]}
    fn = _extract_lookup_outcome_price(price_map)
    assert fn(1, target) is None


def test_respects_a_custom_grace_days_value():
    """Regression guard: the bound must actually read the passed-in constant, not a hardcoded
    literal — a different grace window changes the boundary accordingly."""
    target = date(2026, 1, 6)
    bar_date = target + timedelta(days=3)
    price_map = {1: [(bar_date, 100.0)]}
    fn = _extract_lookup_outcome_price(price_map, grace_days=2)
    assert fn(1, target) is None  # 3 days late, but grace is only 2
