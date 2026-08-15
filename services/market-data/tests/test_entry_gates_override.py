"""Tests for T264-ENTRYGATESOVERRIDE — _entry_gates_override_active() and its wiring into
every "market condition / recent performance" gate in _scan_for_entries() (drawdown,
daily_loss, weekly_loss, weekly_gain_lock, consecutive_losses, regime_bear, regime_suspension),
plus the two decision-engine values (daily_pnl_pct, consec_losses) that must be suppressed at
the real _call_decision_engine() call site so DE's own independent hard rejects for those same
two conditions don't still block while the override is active.

Generalizes T232-HKOVERRIDE's proven time-boxed-override pattern (risk_off only) to a broader
set of gates, per a direct user request: "give me a button to remove the conditions and let
the paper trading moving... when I think the market is still good."

Deliberately does NOT cover max_positions, the equity-floor circuit breaker, or the
live-price-sparsity safety check (hard capital-preservation/data-integrity limits, not market-
condition judgment calls) — confirmed by their absence from every test below.

Deliberately does NOT fully cover decision-engine's own regime_state=="bear" hard reject —
that check has no config-gated escape hatch on DE's own side at all (unlike regime_risk_off_gate,
which DE explicitly checks). See the CAVEAT in the API endpoint's own docstring
(services/market-data/src/api/paper_portfolio.py's set_entry_gates_override()).

paper_trading_engine.py can't be imported directly in this test environment (apscheduler/
db.models import chain) — tested via source-text extraction, matching
test_risk_off_equity_floor_config_wiring.py's established technique. _entry_gates_override_active()
itself is pure (datetime comparison only) and is additionally exercised via exec() for real
behavioral coverage, not just a source-text presence check.
"""
import pathlib
from datetime import datetime, timedelta

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _scan_for_entries_body() -> str:
    start = _pte_source.index("def _scan_for_entries(")
    end = _pte_source.index("\n\ndef ", start + 1)
    return _pte_source[start:end]


_SCAN_BODY = _scan_for_entries_body()


def _decision_call_block() -> str:
    call_start = _pte_source.index("de_result = _call_decision_engine(")
    call_end = _pte_source.index(")\n", call_start)
    return _pte_source[call_start:call_end]


_DECISION_CALL = _decision_call_block()


# ── _entry_gates_override_active() — real behavioral tests ──────────────────────────────────

_start = _pte_source.index("def _entry_gates_override_active(")
_end = _pte_source.index("\n\n\n# Mirrors scheduler._STYLE_PARAMS", _start)
_namespace: dict = {"datetime": datetime}
exec(_pte_source[_start:_end], _namespace)
_entry_gates_override_active = _namespace["_entry_gates_override_active"]


class TestEntryGatesOverrideActive:
    def test_no_key_present_is_inactive(self):
        assert _entry_gates_override_active({}) is False

    def test_future_timestamp_is_active(self):
        until = (datetime.utcnow() + timedelta(hours=2)).isoformat()
        assert _entry_gates_override_active({"entry_gates_override_until": until}) is True

    def test_past_timestamp_is_inactive_expired_on_its_own(self):
        until = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        assert _entry_gates_override_active({"entry_gates_override_until": until}) is False

    def test_malformed_timestamp_is_inactive_not_a_crash(self):
        assert _entry_gates_override_active({"entry_gates_override_until": "not-a-real-timestamp"}) is False

    def test_none_value_is_inactive(self):
        assert _entry_gates_override_active({"entry_gates_override_until": None}) is False


# ── Wiring: _gates_override computed once, used at every covered gate ───────────────────────

def test_gates_override_flag_is_computed_once_near_the_top_of_scan_for_entries():
    assert "_gates_override = _entry_gates_override_active(cfg)" in _SCAN_BODY


def test_drawdown_gate_respects_the_override():
    idx = _SCAN_BODY.index("if max_dd_cfg and max_dd_cfg > 0")
    line_end = _SCAN_BODY.index("\n", idx)
    line = _SCAN_BODY[idx:line_end]
    assert "not _gates_override" in line


def test_daily_loss_gate_respects_the_override():
    idx = _SCAN_BODY.index("if daily_net_pnl < 0 and abs(daily_net_pnl)")
    line_end = _SCAN_BODY.index("\n", idx)
    line = _SCAN_BODY[idx:line_end]
    assert "not _gates_override" in line


def test_weekly_loss_gate_respects_the_override():
    idx = _SCAN_BODY.index("if max_weekly_loss and weekly_net_pnl < 0")
    line_end = _SCAN_BODY.index("\n", idx)
    line = _SCAN_BODY[idx:line_end]
    assert "not _gates_override" in line


def test_weekly_gain_lock_gate_respects_the_override():
    idx = _SCAN_BODY.index("if max_weekly_gain and weekly_net_pnl > 0")
    line_end = _SCAN_BODY.index("\n", idx)
    line = _SCAN_BODY[idx:line_end]
    assert "not _gates_override" in line


def test_consecutive_losses_gate_respects_the_override():
    idx = _SCAN_BODY.index("if max_consec_losses and max_consec_losses > 0")
    line_end = _SCAN_BODY.index("\n", idx)
    line = _SCAN_BODY[idx:line_end]
    assert "not _gates_override" in line


def test_regime_bear_gate_respects_the_override():
    idx = _SCAN_BODY.index('if regime_state == "bear"')
    line_end = _SCAN_BODY.index("\n", idx)
    line = _SCAN_BODY[idx:line_end]
    assert "not _gates_override" in line


def test_regime_risk_off_gate_respects_BOTH_the_specific_and_the_general_override():
    """The pre-existing risk-off-specific override must keep working standalone — this test
    guards against the general override's wiring accidentally replacing rather than
    supplementing it."""
    idx = _SCAN_BODY.index('if (regime_state == "risk_off"')
    block_end = _SCAN_BODY.index(":", idx)
    block = _SCAN_BODY[idx:block_end]
    assert "_regime_risk_off_override_active(cfg)" in block
    assert "_gates_override" in block


def test_regime_suspension_gate_respects_the_override():
    idx = _SCAN_BODY.index("if len(_recent_bad) >= _regime_suspend_days")
    line_end = _SCAN_BODY.index("\n", idx)
    line = _SCAN_BODY[idx:line_end]
    assert "not _gates_override" in line


# ── Gates deliberately NOT covered — must never reference _gates_override ───────────────────

def test_max_positions_gate_is_not_touched_by_the_override():
    idx = _SCAN_BODY.index("if open_count >= cfg[\"max_positions\"]:")
    line_end = _SCAN_BODY.index("\n", idx)
    line = _SCAN_BODY[idx:line_end]
    assert "_gates_override" not in line


def test_equity_floor_gate_is_not_touched_by_the_override():
    idx = _SCAN_BODY.index("if _floor_ratio < _equity_floor_pct:")
    line_end = _SCAN_BODY.index("\n", idx)
    line = _SCAN_BODY[idx:line_end]
    assert "_gates_override" not in line


def test_live_price_sparsity_gate_is_not_touched_by_the_override():
    idx = _SCAN_BODY.index("if expected_prices > 0 and len(live_prices)")
    line_end = _SCAN_BODY.index("\n", idx)
    line = _SCAN_BODY[idx:line_end]
    assert "_gates_override" not in line


# ── Decision-engine suppression: daily_pnl_pct / consec_losses sent as "no problem" ──────────

def test_daily_pnl_pct_is_zeroed_for_decision_engine_when_override_is_active():
    assert "daily_pnl_pct=(0.0 if _gates_override else _daily_pnl_pct)," in _DECISION_CALL


def test_consec_losses_is_zeroed_for_decision_engine_when_override_is_active():
    assert "consec_losses=(0 if _gates_override else _consec_losses)," in _DECISION_CALL
