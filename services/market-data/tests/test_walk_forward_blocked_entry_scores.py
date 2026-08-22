"""Tests for AUD298-BLOCKED-ENTRY-SCORES-VALIDATE-FIRST — gate_harness.py's new
replay_should_enter_excluding_scores() and walk_forward_blocked_entry_scores().

Background: PAPER_TRADING_DEEP_AUDIT_2025-08-22.md observed that _should_enter()'s
min_entry_score gate is a pure `score >= threshold` comparison, structurally unable to
express "exclude 5 and 6 specifically, but allow 7+" the way its own per-score win-rate table
superficially suggests is needed. walk_forward_min_entry_score() (the existing sibling sweep)
can only search threshold candidates, never a discrete exclusion set — this is the new sweep
that can, gated behind the same chronological train/validation promotion-margin discipline
every other walk-forward endpoint in this module already enforces.

replay_should_enter_excluding_scores() is a near-verbatim copy of the ALREADY-SHIPPED
replay_should_enter() (same fetch/game-plan/ATR/confidence-delta machinery, unchanged) with
exactly one new line: `if not should or score in excluded_scores: continue`. That surrounding
machinery is not independently behaviorally tested anywhere in this codebase today (confirmed
via grep — test_gate_harness_review_fixes.py only tests _resolvable_window_end()/
_passes_promotion_margin(), never replay_should_enter() end-to-end; that pipeline was instead
live-verified against real production data this same session for the sibling sweeps). Given
that, this file tests the ONE genuinely new piece of logic in isolation — the exclusion-set
filtering itself — by injecting a fake _should_enter() so the test proves the filtering
semantics directly, without needing to also stand up a full DB-backed replay of machinery
that's already proven correct by virtue of being an unmodified copy. The sweep orchestration
(walk_forward_blocked_entry_scores) is covered via source-text regression checks, matching
test_walk_forward_calibration_feedback.py's own established convention for this file's Docker-
only-dependency constraint (gate_harness.py can't be imported directly — conftest.py stubs
sqlalchemy/db wholesale).
"""
import pathlib

_GH_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "backtest" / "gate_harness.py"
_GH_SOURCE = _GH_PATH.read_text()


def _extract_replay_excluding_scores():
    """Pulls the real replay_should_enter_excluding_scores() source out of gate_harness.py and
    exec()s it with every real dependency (_fetch_matched_signals, _historical_atr,
    _build_game_plan_for_style, _historical_confidence_delta, _entry_as_of, _should_enter)
    replaced by lightweight fakes — proves the exclusion-filtering logic itself, the one line
    of code this function actually adds on top of the already-shipped replay_should_enter()."""
    start = _GH_SOURCE.index("def replay_should_enter_excluding_scores(")
    end = _GH_SOURCE.index("\ndef walk_forward_blocked_entry_scores(", start)
    func_source = _GH_SOURCE[start:end]

    class _FakeStock:
        def __init__(self, id_, symbol):
            self.id = id_
            self.symbol = symbol

    class _FakeSignal:
        def __init__(self, id_, signal="BUY", confidence=60.0, bullish_probability=0.6, reasons=None):
            self.id = id_
            self.signal = type("S", (), {"value": signal})()
            self.confidence = confidence
            self.bullish_probability = bullish_probability
            self.reasons = reasons or {}

    class _FakeOutcome:
        def __init__(self, entry_price, signal_date, entry_date=None, return_10d=0.02, is_correct_10d=True):
            self.entry_price = entry_price
            self.signal_date = signal_date
            self.entry_date = entry_date or signal_date
            self.return_10d = return_10d
            self.is_correct_10d = is_correct_10d

    def _fake_fetch_matched(session, style, market, window_start, window_end):
        return session["matched"]

    def _fake_historical_atr(session, stock_id, as_of, period=14):
        return 1.0

    def _fake_build_game_plan(symbol, style, live_price, reasons, atr):
        return {"stop": live_price * 0.95, "take_profit": live_price * 1.1}

    def _fake_historical_confidence_delta(session, stock_id, style, signal_date, confidence):
        return 0.0

    def _fake_entry_as_of(entry_date, market):
        return entry_date

    namespace = {
        "_fetch_matched_signals": _fake_fetch_matched,
        "_historical_atr": _fake_historical_atr,
        "_build_game_plan_for_style": _fake_build_game_plan,
        "_historical_confidence_delta": _fake_historical_confidence_delta,
        "_entry_as_of": _fake_entry_as_of,
        "_HORIZON_BUCKET": {"SWING": "10d", "SHORT": "5d", "LONG": "20d", "GROWTH": "10d"},
        "MIN_SAMPLES_PER_SPLIT": 2,  # lowered for small test fixtures
        "BacktestResult": None,  # set below
        "Session": object,  # unused at runtime — only referenced in the function's own type hint
        "date": __import__("datetime").date,
    }
    # BacktestResult itself is a dataclass defined earlier in the module — extract it too.
    bt_start = _GH_SOURCE.index("class BacktestResult:")
    bt_end = _GH_SOURCE.index("\n\n\ndef _entry_as_of(", bt_start)
    bt_source = "@dataclass\n" + _GH_SOURCE[bt_start:bt_end]
    import datetime as _bt_dt
    bt_ns = {
        "dataclass": __import__("dataclasses").dataclass,
        "field": __import__("dataclasses").field,
        "date": _bt_dt.date,
    }
    exec(bt_source, bt_ns)  # noqa: S102 — isolated eval of real source
    namespace["BacktestResult"] = bt_ns["BacktestResult"]

    # _should_enter is patched per-test via a mutable holder so each test can control it.
    _should_enter_holder = {"fn": None}
    namespace["_should_enter"] = lambda *a, **kw: _should_enter_holder["fn"](*a, **kw)

    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["replay_should_enter_excluding_scores"], _should_enter_holder, _FakeStock, _FakeSignal, _FakeOutcome


_replay_excl, _should_enter_holder, FakeStock, FakeSignal, FakeOutcome = _extract_replay_excluding_scores()


def _make_matched(n, score_sequence):
    """Builds n (Signal, SignalOutcome, Stock) tuples, one per entry in score_sequence — the
    fake _should_enter below reads score_sequence via a shared counter to hand back a
    different score per call, simulating a real replay seeing different scores per signal."""
    import datetime as _dt
    matched = []
    for i in range(n):
        sig = FakeSignal(id_=i)
        outcome = FakeOutcome(entry_price=100.0, signal_date=_dt.date(2026, 1, 1) + _dt.timedelta(days=i))
        stock = FakeStock(id_=i, symbol=f"SYM{i}")
        matched.append((sig, outcome, stock))
    return matched


def _session_with_matched(matched):
    return {"matched": matched}


def test_a_score_in_the_exclusion_set_is_rejected_even_when_should_enter_says_yes():
    """The one genuinely new behavior this function adds: score in excluded_scores must
    override a real should=True."""
    scores = iter([5, 5, 5])
    _should_enter_holder["fn"] = lambda *a, **kw: (True, next(scores), [])
    matched = _make_matched(3, [5, 5, 5])
    result = _replay_excl(
        _session_with_matched(matched), "SWING", "US", {}, frozenset({5}),
        __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 10),
    )
    assert result.n_entered == 0


def test_a_score_not_in_the_exclusion_set_is_admitted_normally():
    scores = iter([7, 7, 7])
    _should_enter_holder["fn"] = lambda *a, **kw: (True, next(scores), [])
    matched = _make_matched(3, [7, 7, 7])
    result = _replay_excl(
        _session_with_matched(matched), "SWING", "US", {}, frozenset({5, 6}),
        __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 10),
    )
    assert result.n_entered == 3


def test_a_hard_reject_stays_excluded_regardless_of_the_exclusion_set():
    """should=False (e.g. a real hard-reject returning score=-99) must never be admitted just
    because -99 happens not to be in the exclusion set — the exclusion check narrows what the
    plain threshold already admits, it can never widen it."""
    _should_enter_holder["fn"] = lambda *a, **kw: (False, -99, ["hard reject"])
    matched = _make_matched(3, [-99, -99, -99])
    result = _replay_excl(
        _session_with_matched(matched), "SWING", "US", {}, frozenset({5, 6}),
        __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 10),
    )
    assert result.n_entered == 0


def test_empty_exclusion_set_behaves_identically_to_a_plain_threshold_replay():
    """An empty exclusion set must be a true no-op — this is the "baseline" candidate the
    sweep uses, and it must reduce to exactly what replay_should_enter() itself would do."""
    scores = iter([4, 7, 9])
    _should_enter_holder["fn"] = lambda *a, **kw: (True, next(scores), [])
    matched = _make_matched(3, [4, 7, 9])
    result = _replay_excl(
        _session_with_matched(matched), "SWING", "US", {}, frozenset(),
        __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 10),
    )
    assert result.n_entered == 3


def test_mixed_batch_excludes_only_the_matching_scores():
    """A realistic mixed batch: scores 4, 5, 6, 7 with {5, 6} excluded must admit exactly the
    4 and 7, not all-or-nothing."""
    scores = iter([4, 5, 6, 7])
    _should_enter_holder["fn"] = lambda *a, **kw: (True, next(scores), [])
    matched = _make_matched(4, [4, 5, 6, 7])
    result = _replay_excl(
        _session_with_matched(matched), "SWING", "US", {}, frozenset({5, 6}),
        __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 10),
    )
    assert result.n_entered == 2


def test_below_sample_floor_reports_skipped_reason_not_a_fabricated_result():
    _should_enter_holder["fn"] = lambda *a, **kw: (True, 7, [])
    matched = _make_matched(1, [7])
    result = _replay_excl(
        _session_with_matched(matched), "SWING", "US", {}, frozenset({5, 6}),
        __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 10),
    )
    assert result.n_entered == 0
    assert result.skipped_reason is not None


# ── walk_forward_blocked_entry_scores() orchestration — source-text regression checks ───────
# Matches test_walk_forward_calibration_feedback.py's established convention: gate_harness.py
# can't be imported directly (conftest.py stubs sqlalchemy/db wholesale), and the sweep's own
# per-signal replay machinery is already covered above / by the sibling sweep's own live
# production verification — these checks lock in the sweep's orchestration structure.

def _sweep_function_body() -> str:
    start = _GH_SOURCE.index("def walk_forward_blocked_entry_scores(")
    end = _GH_SOURCE.index("\ndef walk_forward_calibration_feedback(", start)
    return _GH_SOURCE[start:end]


def test_sweep_function_exists_and_is_extractable():
    body = _sweep_function_body()
    assert "def walk_forward_blocked_entry_scores(" in body
    assert len(body) > 500


def test_sweep_pulls_window_end_back_by_the_style_resolution_lag_before_splitting():
    """Same BUG233-BACKTESTHARNESS-EMPTYVALIDATION guard every other walk-forward function in
    this module applies."""
    body = _sweep_function_body()
    assert "resolvable_end = _resolvable_window_end(window_end, style)" in body
    assert "if resolvable_end <= window_start:" in body


def test_sweep_uses_a_chronological_not_random_split():
    body = _sweep_function_body()
    assert "split_days = max(1, int(total_days * 0.7))" in body


def test_sweep_baseline_uses_the_empty_exclusion_set_not_a_hardcoded_default():
    """The baseline must be "plain threshold, zero exclusions" — testing frozenset() directly
    guards against a future edit accidentally seeding the baseline with a non-empty default."""
    body = _sweep_function_body()
    assert "replay_should_enter_excluding_scores(\n        session, style, market, base_cfg, frozenset()," in body


def test_sweep_default_candidates_include_the_docs_own_specific_claim():
    """The doc's own claim was specifically about excluding 5, 6, or both together — the
    default candidate list must actually test that claim, not some unrelated set."""
    body = _sweep_function_body()
    assert "frozenset({5})" in body
    assert "frozenset({6})" in body
    assert "frozenset({5, 6})" in body


def test_sweep_promotion_decision_uses_the_shared_promotion_margin_gate():
    """Must reuse _passes_promotion_margin() (the BUG233-BACKTESTHARNESS-COINFLIP fix) rather
    than a bare comparison."""
    body = _sweep_function_body()
    assert "promoted = _passes_promotion_margin(best_val, baseline_val)" in body


def test_sweep_note_discloses_this_is_research_only_not_a_live_config_change():
    body = _sweep_function_body()
    assert "NOT an automatic config change" in body


def test_sweep_note_discloses_the_de_outage_fallback_scope_limitation():
    body = _sweep_function_body()
    assert "DE-outage" in body
    assert "fallback gate" in body


def test_sweep_returns_early_when_no_candidate_clears_the_train_sample_floor():
    body = _sweep_function_body()
    assert "if best_excl is None:" in body
    assert '"skipped_reason": "no exclusion-set candidate cleared the sample floor on the train slice"' in body
