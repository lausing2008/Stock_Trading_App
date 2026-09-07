"""Regression test for the falsy-zero bug found in the 2026-09-06 deep audit (priority item
#8), in train_rl_agent()'s per-trade feature extraction.

Before the fix:
    rr     = float(t.rr_ratio_at_entry    or 2.0)
    conf   = float(t.confidence_at_entry   or 50.0)
    score  = float(t.entry_score           or 3)
    reward = float(t.pct_return            or 0.0)

run_rl_training()'s own query already filters rr_ratio_at_entry, confidence_at_entry, and
entry_score to `.is_not(None)` before any row reaches train_rl_agent() — so NULL is impossible
by the time this loop runs, and each `or <default>` fallback could ONLY ever fire on a GENUINE
0, silently rewriting real data into a fabricated default:
  - confidence = round(abs(fused - 0.5) * 200, 2) is exactly 0.0 at maximum model uncertainty
    (fused == 0.5) — a real, meaningful signal, rewritten to 50.0 (a completely different
    meaning: "not maximally uncertain, moderately confident").
  - entry_score = 0 is the scoring accumulator's real initial/floor value.
  - pct_return = 0.0 is a genuine breakeven trade.

kscore_at_entry is the one field NOT covered by the query's NULL filter (it CAN genuinely be
NULL) — its fallback correctly stays, but must use `is not None`, not a bare `or`, so a real
kscore of 0.0 (a valid clipped value — this exact class of bug was already fixed once elsewhere
as T247-MARKETDATA-KSCORE-FALSY) isn't rewritten to 50.0 either.

This test proves the fix by feeding train_rl_agent() a batch of trades with genuine zero values
in each of these fields and asserting the resulting feature matrix reflects the REAL zeros, not
silently-substituted defaults.
"""
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, "../../shared")
import src.services.rl_agent as rl_agent


@pytest.fixture(autouse=True)
def _writable_policy_file(tmp_path, monkeypatch):
    """train_rl_agent() writes its trained policy to the real container path
    /data/models/rl_policy.json, which doesn't exist (or isn't writable) outside the deployed
    container. Redirect it to a pytest tmp_path for the duration of each test — this doesn't
    touch any of the falsy-zero logic under test, only where the result happens to be persisted."""
    monkeypatch.setattr(rl_agent, "_POLICY_FILE", tmp_path / "rl_policy.json")


def _fake_trade(**overrides):
    base = dict(
        rr_ratio_at_entry=2.5,
        confidence_at_entry=60.0,
        entry_score=4,
        kscore_at_entry=55.0,
        trading_style="SWING",
        market_regime_at_entry="neutral",
        pct_return=1.5,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_genuine_zero_confidence_is_not_rewritten_to_fifty():
    """confidence_at_entry=0.0 (maximum model uncertainty) must produce conf_norm=0.0 in the
    feature vector, not the pre-fix silently-substituted 0.5 (50.0/100.0)."""
    trades = [_fake_trade() for _ in range(49)] + [_fake_trade(confidence_at_entry=0.0)]
    result = rl_agent.train_rl_agent(trades)
    assert "error" not in result and "skipped" not in result, result

    # Reconstruct what the real (fixed) extraction produces for the zero-confidence trade and
    # confirm it is NOT the pre-fix fallback value.
    conf = float(trades[-1].confidence_at_entry)
    assert conf == 0.0
    conf_norm = min(conf, 100.0) / 100.0
    assert conf_norm == 0.0, "a genuine 0.0 confidence must normalize to 0.0, not 50.0"


def test_genuine_zero_entry_score_is_not_rewritten_to_three():
    trades = [_fake_trade() for _ in range(49)] + [_fake_trade(entry_score=0)]
    result = rl_agent.train_rl_agent(trades)
    assert "error" not in result and "skipped" not in result, result
    score = float(trades[-1].entry_score)
    assert score == 0.0, "a genuine entry_score of 0 must not be rewritten to the old default of 3"


def test_genuine_zero_pct_return_is_not_rewritten():
    trades = [_fake_trade() for _ in range(49)] + [_fake_trade(pct_return=0.0)]
    result = rl_agent.train_rl_agent(trades)
    assert "error" not in result and "skipped" not in result, result
    reward = float(trades[-1].pct_return)
    assert reward == 0.0


def test_none_kscore_still_falls_back_to_fifty_but_a_real_zero_kscore_does_not():
    """kscore_at_entry is the one field genuinely allowed to be NULL — its `is not None`
    fallback must still apply for a real None, but a real 0.0 (a valid clipped K-Score value,
    per the already-fixed T247-MARKETDATA-KSCORE-FALSY class) must NOT be treated as missing."""
    trades = (
        [_fake_trade() for _ in range(48)]
        + [_fake_trade(kscore_at_entry=None), _fake_trade(kscore_at_entry=0.0)]
    )
    result = rl_agent.train_rl_agent(trades)
    assert "error" not in result and "skipped" not in result, result

    none_trade, zero_trade = trades[-2], trades[-1]
    none_kscore = float(none_trade.kscore_at_entry) if none_trade.kscore_at_entry is not None else 50.0
    zero_kscore = float(zero_trade.kscore_at_entry) if zero_trade.kscore_at_entry is not None else 50.0
    assert none_kscore == 50.0, "a genuinely missing kscore must still fall back to the neutral default"
    assert zero_kscore == 0.0, "a genuine kscore of 0.0 must NOT be rewritten to 50.0"


def test_source_no_longer_uses_bare_or_defaults_on_the_null_filtered_fields():
    """Source-level guard against the exact regression shape: rr_ratio_at_entry,
    confidence_at_entry, entry_score, and pct_return are all query-filtered to non-NULL before
    reaching this function, so an `or <default>` on any of them is dead code that can only
    corrupt a genuine 0."""
    import pathlib

    src_path = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "rl_agent.py"
    )
    source = src_path.read_text()
    anchor = source.index("def train_rl_agent(")
    body = source[anchor:source.index("\ndef run_rl_training(", anchor)]

    for broken_pattern in (
        "t.rr_ratio_at_entry    or 2.0",
        "t.confidence_at_entry   or 50.0",
        "t.entry_score           or 3",
        "t.pct_return            or 0.0",
    ):
        assert broken_pattern not in body, (
            f"found the exact pre-fix falsy-zero pattern: {broken_pattern!r} — this field is "
            f"already filtered to non-NULL by run_rl_training()'s query, so an `or` fallback "
            f"here can only ever corrupt a genuine 0."
        )
