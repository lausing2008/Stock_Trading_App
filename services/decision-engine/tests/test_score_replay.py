"""Tests for T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group A scorer sweep's new
POST /decide/score-replay endpoint (routes.py::score_replay()).

This is the REAL entry point market-data's own walk-forward sweep calls to score N already-
resolved historical BUY signals against a candidate cfg — it delegates straight to the real
compute_score()/min_score_for_regime() (never a re-implementation), so these tests exercise
the actual live scoring code, not a mock of it.

routes.py imports cleanly in this test environment once common/common.config/common.jwt_auth/
common.redis_client/common.ai_keys are stubbed (matching test_entry_gate_params.py's own
established convention) — confirmed no FastAPI app/DB/network access happens at import time.
"""
import importlib
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())
sys.modules.setdefault("common.jwt_auth", MagicMock())
sys.modules.setdefault("common.redis_client", MagicMock())
sys.modules.setdefault("common.ai_keys", MagicMock())

from src.api.core.models import ScoreReplayInput, ScoreReplayRequest  # noqa: E402

# T234-CONFIG-UNJUSTIFIED-THRESHOLDS: a sibling test file collected earlier in the same pytest
# process (test_entry_gate_params.py / test_entry_weights.py) does
# `sys.modules.setdefault("fastapi", MagicMock())` for its OWN, unrelated purpose — pytest
# collects/imports all test files into one shared process, so once that stub lands, ANY later
# `import src.api.routes` in the SAME process reuses the already-cached module object built
# against the FAKE fastapi (Python's own module cache, not a per-file reset). Since routes.py
# functions are all `@router.post(...)`/`@router.get(...)`-decorated, a mocked fastapi makes
# `@router.post(...)` return a MagicMock instead of the real function — silently discarding
# score_replay() entirely and making every assertion below fail against a Mock, not the real
# code. Confirmed via direct sabotage-and-observe: running this file's tests standalone always
# passes; running them after either of those two sibling files in the same pytest invocation
# reproduced this exact failure. Fix: force-reload routes.py here, with the REAL fastapi
# restored first, so this file's own tests always exercise the genuine decorated function
# regardless of what an earlier-collected sibling file left in sys.modules.
if isinstance(sys.modules.get("fastapi"), MagicMock):
    del sys.modules["fastapi"]
importlib.import_module("fastapi")  # forces the real package back into sys.modules
if "src.api.routes" in sys.modules:
    importlib.reload(sys.modules["src.api.routes"])
from src.api.routes import score_replay  # noqa: E402


def _input(signal_id=1, **overrides):
    base = dict(
        signal_id=signal_id,
        live_price=100.0,
        game_plan={"entry2": 94.0, "breakout": 103.5, "stop": 88.0, "take_profit": 135.0},
        confidence=70.0,
        bullish_probability=0.65,
        reasons={},
        research_rec=None,
        research_score_val=None,
        regime_state="neutral",
        kscore=None,
        pct_return=0.05,
    )
    base.update(overrides)
    return ScoreReplayInput(**base)


class TestScoreReplayBasics:
    def test_a_single_input_returns_exactly_one_result_with_matching_signal_id(self):
        req = ScoreReplayRequest(inputs=[_input(signal_id=42)], cfg={"min_entry_score": 4})
        resp = score_replay(req, _="dummy")
        assert len(resp.results) == 1
        assert resp.results[0].signal_id == 42

    def test_pct_return_is_carried_through_unchanged(self):
        req = ScoreReplayRequest(inputs=[_input(pct_return=-0.12)], cfg={})
        resp = score_replay(req, _="dummy")
        assert resp.results[0].pct_return == -0.12

    def test_entered_is_true_exactly_when_score_meets_or_exceeds_min_score(self):
        # live_price=100, entry2=94, breakout=103.5 -> price_zone Layer 1 hits the "in optimal
        # zone" branch (+2 pts); rr = (135-100)/(100-88) = 2.9166 -> "good" R:R tier (+1 pt);
        # bull_prob=0.65 is between 0.58 and 0.70 -> moderate (0 pts); regime=neutral (0 pts).
        # Total score = 3.
        req = ScoreReplayRequest(inputs=[_input()], cfg={"min_entry_score": 3})
        resp = score_replay(req, _="dummy")
        assert resp.results[0].score == 3
        assert resp.results[0].min_score == 3
        assert resp.results[0].entered is True

        req2 = ScoreReplayRequest(inputs=[_input()], cfg={"min_entry_score": 4})
        resp2 = score_replay(req2, _="dummy")
        assert resp2.results[0].entered is False

    def test_multiple_inputs_in_one_request_are_scored_independently(self):
        req = ScoreReplayRequest(
            inputs=[_input(signal_id=1, bullish_probability=0.80), _input(signal_id=2, bullish_probability=0.30)],
            cfg={"min_entry_score": 100},  # deliberately unreachable, isolates the raw score diff
        )
        resp = score_replay(req, _="dummy")
        by_id = {r.signal_id: r.score for r in resp.results}
        # A stronger bull_prob (0.80 -> +1) must score strictly higher than a weak one (0.30 -> -1).
        assert by_id[1] > by_id[2]


class TestScoreReplayFreshnessOmission:
    """Layer 3e (signal freshness) must never fire on a replay — sig_ts is deliberately never
    threaded into signal_data at all (see ScoreReplayInput's own field-level comment)."""

    def test_score_is_identical_regardless_of_reasons_content_unrelated_to_freshness(self):
        """A sanity check that the endpoint genuinely calls compute_score() with no 'ts' key
        in signal_data — if it did leak a real 'ts', the freshness layer would apply a -1
        penalty for every replayed row (any historical signal_date reads as enormously stale
        against the real current wall-clock), silently shifting every score down by 1."""
        req = ScoreReplayRequest(inputs=[_input()], cfg={})
        resp = score_replay(req, _="dummy")
        # Hand-computed expected score assuming freshness is skipped entirely (see the
        # docstring math in test_entered_is_true_exactly_when_score_meets_or_exceeds_min_score):
        # price_zone(+2) + rr_quality(+1) + ml_signal(0) + regime(0) = 3.
        assert resp.results[0].score == 3


class TestScoreReplayCfgIsolation:
    def test_cfg_is_applied_identically_to_every_input_in_the_batch(self):
        """The candidate cfg under sweep is a single dict shared across the whole batch — not
        re-derived per input — confirming both rows in a 2-item batch see the SAME
        min_entry_score."""
        req = ScoreReplayRequest(
            inputs=[_input(signal_id=1), _input(signal_id=2)],
            cfg={"min_entry_score": 3},
        )
        resp = score_replay(req, _="dummy")
        assert resp.results[0].min_score == resp.results[1].min_score == 3

    def test_kscore_from_the_input_not_cfg_reaches_the_kscore_layer(self):
        """kscore is per-SIGNAL data (item.kscore), not part of the shared candidate cfg —
        confirms it's threaded through correctly into the merged cfg passed to compute_score()
        on a per-input basis, not accidentally shared/overwritten across the batch."""
        req = ScoreReplayRequest(
            inputs=[_input(signal_id=1, kscore=80.0), _input(signal_id=2, kscore=20.0)],
            cfg={"min_entry_score": -100},  # always "entered" — isolates the raw score
        )
        resp = score_replay(req, _="dummy")
        by_id = {r.signal_id: r.score for r in resp.results}
        # kscore>=55 -> +1; kscore<55 -> -1 (Layer 6) — a real, measurable 2-point spread.
        assert by_id[1] - by_id[2] == 2


class TestScoreReplayRegimeGating:
    def test_min_score_for_regime_is_recomputed_per_input_not_reused_from_the_first_row(self):
        """Two inputs with DIFFERENT regime_state values in the same batch must each get their
        own correctly-regime-adjusted min_score, not the first row's value silently reused for
        the rest of the batch."""
        req = ScoreReplayRequest(
            inputs=[_input(signal_id=1, regime_state="bull"), _input(signal_id=2, regime_state="risk_off")],
            cfg={"min_entry_score": 4, "regime_risk_off_min_score": 7},
        )
        resp = score_replay(req, _="dummy")
        by_id = {r.signal_id: r.min_score for r in resp.results}
        assert by_id[1] == 4     # bull: base, no regime floor raise
        assert by_id[2] == 7     # risk_off: raised to regime_risk_off_min_score


class TestScoreReplayInputValidation:
    def test_rejects_a_batch_larger_than_5000(self):
        import pytest
        with pytest.raises(Exception):
            ScoreReplayRequest(inputs=[_input(signal_id=i) for i in range(5001)], cfg={})


class TestScoreReplayBreakoutExtensionHardReject:
    """item #3: max_breakout_extension_pct is a HARD reject (entered forced False regardless
    of score), inlined directly rather than routed through check_hard_rejects() — see the
    endpoint's own docstring for why (that function's OTHER checks read the real wall-clock)."""

    def test_extended_beyond_the_default_6pct_threshold_forces_entered_false_regardless_of_score(self):
        # breakout=100, live_price=107 -> 7% extension > the 6.0 default threshold.
        req = ScoreReplayRequest(
            inputs=[_input(
                live_price=107.0,
                game_plan={"entry2": 94.0, "breakout": 100.0, "stop": 88.0, "take_profit": 160.0},
                bullish_probability=0.95,  # would otherwise score very high
            )],
            cfg={"min_entry_score": -100},  # would otherwise always enter
        )
        resp = score_replay(req, _="dummy")
        assert resp.results[0].entered is False
        assert resp.results[0].score == 0

    def test_within_the_default_threshold_is_not_forced_out(self):
        # breakout=100, live_price=103 -> 3% extension, well under 6.0.
        req = ScoreReplayRequest(
            inputs=[_input(
                live_price=103.0,
                game_plan={"entry2": 94.0, "breakout": 100.0, "stop": 88.0, "take_profit": 160.0},
            )],
            cfg={"min_entry_score": -100},
        )
        resp = score_replay(req, _="dummy")
        assert resp.results[0].entered is True

    def test_the_threshold_itself_is_read_from_cfg_not_hardcoded(self):
        # Same 7% extension as the first test, but this time cfg raises the bar to 10% —
        # confirms the sweep can actually vary this candidate, not just apply a fixed 6.0.
        req = ScoreReplayRequest(
            inputs=[_input(
                live_price=107.0,
                game_plan={"entry2": 94.0, "breakout": 100.0, "stop": 88.0, "take_profit": 160.0},
            )],
            cfg={"min_entry_score": -100, "max_breakout_extension_pct": 10.0},
        )
        resp = score_replay(req, _="dummy")
        assert resp.results[0].entered is True

    def test_a_missing_or_zero_breakout_is_not_treated_as_extended(self):
        req = ScoreReplayRequest(
            inputs=[_input(
                live_price=107.0,
                game_plan={"entry2": 94.0, "stop": 88.0, "take_profit": 160.0},  # no breakout key
            )],
            cfg={"min_entry_score": -100},
        )
        resp = score_replay(req, _="dummy")
        assert resp.results[0].entered is True
