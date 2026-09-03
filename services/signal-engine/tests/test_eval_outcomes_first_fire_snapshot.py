"""Regression tests for AUD-SIGNAL3-EVALSELECTIONBIAS (AI Signal deep audit, 2026-09-02).

Background: the `signals` table is upserted ~77x/trading day (every /signals/refresh cycle).
evaluate_signal_outcomes() previously read the LIVE Signal.signal/confidence/bullish_
probability/reasons columns — the day's FINAL, most-recently-overwritten state — for BOTH
selecting which signals to score (WHERE Signal.signal.in_([BUY, SELL])) AND what data to score
them with. This produced two distinct corruptions: (1) a signal that was genuinely BUY/SELL
intraday but faded to HOLD by close was invisible to evaluation entirely — a systematic
selection effect on the whole outcome table, since only signals STILL BUY/SELL at 4pm were
ever recorded; (2) even a signal that stayed BUY/SELL all day was scored using its end-of-day
confidence/reasons, not the state that actually fired the trade thesis being measured.

Fix: 5 new nullable columns on Signal (first_buy_sell_at/signal/confidence/
bullish_probability/reasons) frozen via COALESCE in the upsert at the FIRST BUY/SELL
transition of each calendar day, never overwritten for the rest of that day regardless of how
many times the live columns change. evaluate_signal_outcomes() now reads exclusively from
these frozen columns.

evaluate_signal_outcomes() can't be driven end-to-end in this test environment (250+ lines of
FastAPI/Depends/real-Postgres-shaped query construction) — following test_delisted_loss_
scoring.py's/test_evaluate_outcomes_nested_savepoint.py's established convention: source-text
extraction for structural/shape assertions against the real production code.
"""
import pathlib

_OUTCOMES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "outcomes.py"
_OUTCOMES_SOURCE = _OUTCOMES_PATH.read_text()

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


_EVAL_FUNC_SOURCE = _extract(
    _OUTCOMES_SOURCE,
    "def evaluate_signal_outcomes(",
    "\ndef ",
)


# ── The selection query must filter on the frozen columns, not the live ones ────────────

def test_where_clause_filters_on_first_buy_sell_signal_not_live_signal():
    assert "Signal.first_buy_sell_signal.in_([SignalType.BUY, SignalType.SELL])" in _EVAL_FUNC_SOURCE


def test_where_clause_no_longer_filters_on_the_live_signal_column():
    """The exact bug: filtering on the live, ever-overwritten Signal.signal column instead of
    the frozen first-fire snapshot. Must not regress back to this."""
    assert "Signal.signal.in_([SignalType.BUY, SignalType.SELL])" not in _EVAL_FUNC_SOURCE


def test_cutoff_filter_uses_first_buy_sell_at_not_live_ts():
    assert "Signal.first_buy_sell_at <= datetime.combine(cutoff, _time.max)" in _EVAL_FUNC_SOURCE
    assert "Signal.ts <= datetime.combine(cutoff, _time.max)" not in _EVAL_FUNC_SOURCE


def test_ordering_uses_first_buy_sell_at():
    assert ".order_by(Signal.first_buy_sell_at)" in _EVAL_FUNC_SOURCE


# ── Every per-row read must use the frozen first-fire columns ──────────────────────────

def test_signal_date_derived_from_first_buy_sell_at_not_live_ts():
    assert "signal_date = sig.first_buy_sell_at.date()" in _EVAL_FUNC_SOURCE
    assert "signal_date = sig.ts.date()" not in _EVAL_FUNC_SOURCE


def test_hold_days_selection_reads_frozen_signal():
    assert "sig.first_buy_sell_signal == SignalType.SELL" in _EVAL_FUNC_SOURCE


def test_no_remaining_reads_of_the_live_signal_confidence_or_reasons_columns():
    """Exhaustive check: every REAL (non-comment) sig.<field> read in this function must be a
    first_buy_sell_* field, never the bare live signal/confidence/bullish_probability/reasons/ts.
    (sig.stock_id, sig.id, sig.horizon are legitimately immutable/non-live fields, unaffected by
    this bug.) Strips comment lines first — this function's own docstring/inline comments
    legitimately mention the old live-column names as historical context."""
    import re
    code_lines = [ln for ln in _EVAL_FUNC_SOURCE.splitlines() if not ln.strip().startswith("#")]
    code_only = "\n".join(code_lines)
    live_field_reads = re.findall(r"sig\.(signal|confidence|bullish_probability|reasons|ts)\b", code_only)
    assert live_field_reads == [], f"found un-fixed live-column reads: {live_field_reads}"


def test_outcome_construction_uses_frozen_confidence_and_bullish_probability():
    assert "confidence=sig.first_buy_sell_confidence" in _EVAL_FUNC_SOURCE
    assert "fused_prob=sig.first_buy_sell_bullish_probability" in _EVAL_FUNC_SOURCE


def test_outcome_construction_uses_frozen_reasons_for_ta_ml_and_regime_fields():
    assert 'reasons = sig.first_buy_sell_reasons or {}' in _EVAL_FUNC_SOURCE
    assert '(sig.first_buy_sell_reasons or {}).get("ta_score")' in _EVAL_FUNC_SOURCE


def test_delisting_check_uses_frozen_signal():
    assert "sig.first_buy_sell_signal == SignalType.BUY" in _EVAL_FUNC_SOURCE


def test_window_return_direction_uses_frozen_signal():
    assert '_sig_dir = sig.first_buy_sell_signal.value' in _EVAL_FUNC_SOURCE


# ── The upsert itself must correctly freeze first-fire state ────────────────────────────

def test_upsert_inserts_first_buy_sell_columns_conditionally_on_buy_or_sell():
    assert "CASE WHEN :sig IN ('BUY', 'SELL') THEN NOW() ELSE NULL END" in _ROUTES_SOURCE
    assert "CASE WHEN :sig IN ('BUY', 'SELL') THEN CAST(:sig AS signaltype) ELSE NULL END" in _ROUTES_SOURCE


def test_upsert_freezes_via_coalesce_never_overwrites_once_set():
    """The load-bearing correctness property: once a day's first-fire snapshot exists, it must
    never be replaced by a later same-day upsert, no matter what the live columns do."""
    assert "first_buy_sell_at                  = COALESCE(signals.first_buy_sell_at, EXCLUDED.first_buy_sell_at)" in _ROUTES_SOURCE
    assert "first_buy_sell_confidence           = COALESCE(signals.first_buy_sell_confidence, EXCLUDED.first_buy_sell_confidence)" in _ROUTES_SOURCE
    assert "first_buy_sell_bullish_probability  = COALESCE(signals.first_buy_sell_bullish_probability, EXCLUDED.first_buy_sell_bullish_probability)" in _ROUTES_SOURCE
    assert "first_buy_sell_reasons              = COALESCE(signals.first_buy_sell_reasons, EXCLUDED.first_buy_sell_reasons)" in _ROUTES_SOURCE


def test_live_display_columns_still_always_reflect_the_latest_state():
    """The other half of correctness: this fix must NOT change what a live user sees as the
    current signal — signal/confidence/bullish_probability/reasons in the DO UPDATE SET clause
    must remain unconditional overwrites (EXCLUDED.<col>, no COALESCE), matching the pre-fix
    behavior exactly for these 4 columns."""
    assert "signal                    = EXCLUDED.signal," in _ROUTES_SOURCE
    assert "confidence                = EXCLUDED.confidence," in _ROUTES_SOURCE
    assert "bullish_probability       = EXCLUDED.bullish_probability," in _ROUTES_SOURCE
    assert "reasons                   = EXCLUDED.reasons," in _ROUTES_SOURCE
