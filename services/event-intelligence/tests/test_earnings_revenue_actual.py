"""Tests for AUD-EARNINGS-REVENUEACTUAL — EarningsEvent.revenue_actual/revenue_surprise_pct
were real columns, read by generate_earnings_impact()'s LLM prompt and returned to the frontend
via _row_to_dict()'s actual_revenue field, but NOTHING in _fetch_earnings_for_symbol() ever
wrote either one. ticker.earnings_history (the historical loop) only carries EPS fields;
ticker.calendar's "Revenue Estimate" (already written elsewhere in this function) is a
forward-looking, pre-report figure that can never carry an actual by construction. Real
historical revenue is joined from ticker.quarterly_financials's "Total Revenue" row, keyed by
the SAME period-end dates ticker.earnings_history already uses (confirmed directly against a
real yfinance response before writing this fix).

_compute_surprise_pct() is a pure, dependency-free function — tested directly. The join/wiring
inside _fetch_earnings_for_symbol() makes real yfinance + DB calls end-to-end, so it can't be
exercised directly here — covered via source-text regression checks, matching
test_earnings_report_date_wiring.py's own established pattern for this exact function.
"""
from pathlib import Path

_SRC = (Path(__file__).parent.parent / "src" / "services" / "earnings.py").read_text()


def _fetch_body() -> str:
    start = _SRC.index("def _fetch_earnings_for_symbol(")
    end = _SRC.index("\ndef _compute_strength(")
    return _SRC[start:end]


# ── _compute_surprise_pct() — pure, tested directly ─────────────────────────────────────────

def _extract_compute_surprise_pct():
    start = _SRC.index("def _compute_surprise_pct(")
    end = _SRC.index("\n\n\ndef _compute_strength(")
    namespace: dict = {}
    exec(_SRC[start:end], namespace)  # noqa: S102 — isolated eval of one pure function's own source
    return namespace["_compute_surprise_pct"]


def test_a_genuine_beat_computes_a_positive_surprise():
    fn = _extract_compute_surprise_pct()
    # actual 110, estimate 100 -> +10% beat
    assert fn(100.0, 110.0) == 10.0


def test_a_genuine_miss_computes_a_negative_surprise():
    fn = _extract_compute_surprise_pct()
    assert fn(100.0, 90.0) == -10.0


def test_estimate_none_degrades_to_none_not_a_crash():
    fn = _extract_compute_surprise_pct()
    assert fn(None, 90.0) is None


def test_actual_none_degrades_to_none_not_a_crash():
    fn = _extract_compute_surprise_pct()
    assert fn(100.0, None) is None


def test_zero_estimate_degrades_to_none_not_a_divide_by_zero_crash():
    fn = _extract_compute_surprise_pct()
    assert fn(0.0, 50.0) is None


def test_uses_abs_of_estimate_so_a_negative_estimate_still_reports_the_correct_sign():
    """A negative estimate (a loss-making quarter) must not flip the sign of the surprise —
    beating a -0.50 estimate with a -0.20 actual is a real beat (a smaller loss than
    expected), and must report positive, not negative."""
    fn = _extract_compute_surprise_pct()
    # actual -0.20 beats estimate -0.50 by 0.30 -> (−0.20 − (−0.50)) / abs(−0.50) * 100 = +60%
    assert fn(-0.50, -0.20) == 60.0


def test_exact_match_reports_zero_not_none():
    fn = _extract_compute_surprise_pct()
    assert fn(100.0, 100.0) == 0.0


# ── _fetch_earnings_for_symbol() wiring — source-text regression checks ─────────────────────

def test_quarterly_financials_is_fetched_and_joined_by_period_end():
    body = _fetch_body()
    assert "ticker.quarterly_financials" in body
    assert '"Total Revenue" in qf.index' in body
    assert "revenue_actual_by_period_end" in body


def test_revenue_actual_join_is_wrapped_in_its_own_try_except():
    """A failure fetching quarterly_financials (bad response shape, network hiccup) must
    degrade to revenue_actual_by_period_end = {} rather than aborting the whole historical
    sync for this symbol — matching the earnings_dates join's own established convention.
    Anchors on the real CODE line (`qf = ticker.quarterly_financials`), not the first
    occurrence of the bare substring, which also appears in this fix's own explanatory
    comment ABOVE the real call site — the exact 'matched the docstring, not the call' trap
    this codebase's own test-writing history has hit before."""
    body = _fetch_body()
    join_idx = body.index("qf = ticker.quarterly_financials")
    preceding = body[max(0, join_idx - 30):join_idx]
    assert "try:" in preceding
    assert "revenue_actual_by_period_end: dict[str, float] = {}" in body


def test_rev_act_is_looked_up_per_row_by_the_same_period_end_key():
    body = _fetch_body()
    assert "rev_act = revenue_actual_by_period_end.get(period_end.isoformat())" in body


def test_existing_pending_branch_sets_both_new_fields():
    """The existing_pending (calendar-row-already-created) update path must set BOTH
    revenue_actual and revenue_surprise_pct — a fix that only set one would silently leave
    the other permanently null even when the data to compute it was available."""
    body = _fetch_body()
    pending_idx = body.index("if existing_pending is not None:")
    insert_idx = body.index("            else:", pending_idx)
    pending_block = body[pending_idx:insert_idx]
    assert "existing_pending.revenue_actual = rev_act" in pending_block
    assert "existing_pending.revenue_surprise_pct = rev_surprise" in pending_block


def test_revenue_surprise_reuses_the_row_own_revenue_estimate_not_a_fresh_fetch():
    """revenue_estimate for an already-pending row was set by the EARLIER calendar-path write
    — the fix must reuse existing_pending.revenue_estimate to compute the surprise, never
    re-derive or assume a separate revenue_estimate source (there isn't one at this point in
    the historical-report path)."""
    body = _fetch_body()
    assert "_compute_surprise_pct(existing_pending.revenue_estimate, rev_act)" in body


def test_insert_branch_sets_revenue_actual_in_both_the_values_and_conflict_update_clauses():
    """A fresh INSERT (no prior pending row) still has real revenue_actual data available from
    the quarterly_financials join — both the initial pg_insert().values() AND its
    on_conflict_do_update() set_= clause must carry revenue_actual, or a re-run of this sync
    would silently drop the value it just inserted the first time."""
    body = _fetch_body()
    insert_idx = body.index("stmt = (\n                                    pg_insert(EarningsEvent)")
    conflict_idx = body.index('constraint="uq_earnings_stock_report_date"', insert_idx)
    values_block = body[insert_idx:conflict_idx]
    set_block = body[conflict_idx:conflict_idx + 400]
    assert "revenue_actual=rev_act," in values_block
    assert "revenue_actual=rev_act," in set_block


def test_eps_surprise_now_routes_through_the_shared_helper_not_a_duplicated_inline_formula():
    """AUD-EARNINGS-REVENUEACTUAL also extracted the pre-existing EPS surprise formula into
    _compute_surprise_pct() (shared with the new revenue computation) — the old inline
    round((eps_act - eps_est) / abs(eps_est) * 100, 2) formula must be gone from the
    historical loop, replaced by a call to the shared helper."""
    body = _fetch_body()
    assert "surprise = _compute_surprise_pct(eps_est, eps_act)" in body
    assert "round((eps_act - eps_est) / abs(eps_est) * 100, 2)" not in body
