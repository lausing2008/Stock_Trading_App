"""AUD-DE1 — config-merge precedence and decision-engine config threading.

Domain 1 of the 2026-09-07 six-part audit found three defects that were all the SAME class: a
deliberately-tuned parameter that never reaches the code enforcing it. None was a logic error.
These tests pin the two mechanisms so the class cannot recur silently.

Measured production evidence that motivated this (see
docs/audits/2026-09-07-six-part-audit-1-decision-making.md):

  - Both HK portfolios stored max_position_pct=0.1 / risk_per_trade_pct=0.01 (the US defaults),
    so T222-F's HK reduction to 0.07/0.007 had NEVER applied. Real HK positions reached 12.69%,
    11.28% and 10.06% of capital against an intended 7% cap.
  - All SWING portfolios stored min_kscore=48.0, defeating SWING's deliberate 52.0.
  - max_entry_gap_pct was tuned per style (SWING 0.03) but never sent to decision-engine, which
    is the AUTHORITATIVE gate, so DE always used its own hardcoded 0.04.
"""
import re

import src.services.paper_trading_engine as pte


# ── resolve_entry_config(): precedence ───────────────────────────────────────────────────

def test_hk_override_applies_when_user_value_merely_echoes_the_default():
    """THE CORE FIX. The UI round-trips the whole config on every save, so a key being PRESENT
    says nothing about intent. A stored value equal to the generic default is an echoed
    default, not a choice, and must not suppress the HK override."""
    cfg = pte.resolve_entry_config({
        "market": "HK", "trading_style": "GROWTH",
        "max_position_pct": pte._DEFAULT_CONFIG["max_position_pct"],       # 0.1 — echoed default
        "risk_per_trade_pct": pte._DEFAULT_CONFIG["risk_per_trade_pct"],   # 0.01 — echoed default
    })
    assert cfg["max_position_pct"] == pte._HK_MARKET_OVERRIDES["max_position_pct"] == 0.07
    assert cfg["risk_per_trade_pct"] == pte._HK_MARKET_OVERRIDES["risk_per_trade_pct"] == 0.007


def test_hk_override_is_still_suppressed_by_a_genuine_user_choice():
    """The guard's original INTENT was right — a real user choice must win. A value that
    DIFFERS from the generic default is a real choice."""
    cfg = pte.resolve_entry_config({
        "market": "HK", "trading_style": "GROWTH", "max_position_pct": 0.03,
    })
    assert cfg["max_position_pct"] == 0.03, "a deliberate user value must beat the HK override"


def test_style_override_survives_an_echoed_default():
    """min_kscore: SWING deliberately tightens to 52.0, but every SWING portfolio stored the
    generic 48.0. The echoed default must not win."""
    assert pte._STYLE_OVERRIDES["SWING"]["min_kscore"] == 52.0
    cfg = pte.resolve_entry_config({
        "trading_style": "SWING", "min_kscore": pte._DEFAULT_CONFIG["min_kscore"],  # 48.0
    })
    assert cfg["min_kscore"] == 52.0


def test_style_override_yields_to_a_genuine_user_choice():
    cfg = pte.resolve_entry_config({"trading_style": "SWING", "min_kscore": 60.0})
    assert cfg["min_kscore"] == 60.0


def test_hk_beats_style_but_user_choice_beats_both():
    """Full precedence chain: user choice > HK > style > default."""
    assert pte.resolve_entry_config({"market": "HK", "trading_style": "SWING"})["min_confidence"] == 65.0
    assert pte.resolve_entry_config(
        {"market": "HK", "trading_style": "SWING", "min_confidence": 80.0}
    )["min_confidence"] == 80.0


def test_us_portfolio_never_receives_hk_overrides():
    cfg = pte.resolve_entry_config({"market": "US", "trading_style": "GROWTH"})
    assert cfg["max_position_pct"] == pte._DEFAULT_CONFIG["max_position_pct"]


def test_a_key_absent_from_default_config_is_always_taken_verbatim():
    """A user key with no generic-default counterpart cannot be an 'echoed default', so it
    must always pass through — the echo rule must not swallow bespoke keys."""
    cfg = pte.resolve_entry_config({"trading_style": "SWING", "some_bespoke_key": 123})
    assert cfg["some_bespoke_key"] == 123


def test_falsy_zero_user_choice_is_not_discarded():
    """A deliberate 0 (e.g. min_ta_score=0.0, the documented 'disabled' state) is a real
    choice and must survive — the echo rule uses equality, never truthiness."""
    cfg = pte.resolve_entry_config({"market": "HK", "trading_style": "SWING", "min_ta_score": 0.0})
    assert cfg["min_ta_score"] == 0.0


def test_empty_and_none_config_do_not_raise():
    for arg in (None, {}):
        cfg = pte.resolve_entry_config(arg)
        assert cfg["trading_style"] == "GROWTH"      # documented default style
        assert cfg["min_kscore"] == pte._STYLE_OVERRIDES["GROWTH"].get(
            "min_kscore", pte._DEFAULT_CONFIG["min_kscore"])


def test_reproduces_the_measured_live_hk_config():
    """Anchored to the REAL stored config of both HK portfolios, not an invented one."""
    live_hk = {"market": "HK", "trading_style": "GROWTH", "min_kscore": 48.0,
               "max_position_pct": 0.1, "risk_per_trade_pct": 0.01,
               "min_confidence": 65.0, "min_entry_score": 6}
    cfg = pte.resolve_entry_config(live_hk)
    assert cfg["max_position_pct"] == 0.07, "the 12.69%-of-capital bug must not recur"
    assert cfg["risk_per_trade_pct"] == 0.007


# ── Systematic guard: every tuned key must actually REACH decision-engine ────────────────

def _de_request_source() -> str:
    """Source text of the config_overrides block sent to decision-engine."""
    src = open(pte.__file__).read()
    start = src.index('"config_overrides": {')
    # Walk to the matching close by brace depth so the slice can't silently truncate.
    depth, i = 0, start + len('"config_overrides": ')
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("could not find the end of the config_overrides block")


def test_every_de_threaded_key_actually_appears_in_the_request():
    """THE SYSTEMATIC GUARD.

    decision-engine is the AUTHORITATIVE entry gate (decision_engine_mode defaults to
    "primary"), so a key tuned locally but never sent is silently inert — DE falls back to its
    own hardcoded default and the tuning does nothing. That is exactly how max_entry_gap_pct's
    per-style values were lost.

    This asserts the whole set at once so the NEXT parameter added cannot repeat the failure
    without a test going red.
    """
    body = _de_request_source()
    missing = [k for k in pte._DE_THREADED_KEYS if f'"{k}"' not in body]
    assert not missing, (
        f"tuned but NOT sent to decision-engine: {missing} — DE will silently use its own "
        f"defaults and this tuning will do nothing"
    )


def _value_expr_for(body: str, key: str) -> str | None:
    """The value expression for `key`, matched on its OWN line only.

    Line-anchored deliberately: a `.+?` spanning newlines silently captured an unrelated
    conditional-spread expression from a later line while writing these tests, producing a
    confident but wrong failure. Match within one line or not at all.
    """
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(f'"{key}":'):
            return s[len(f'"{key}":'):].strip().rstrip(",")
    return None


def test_max_entry_gap_pct_is_sent_with_the_style_aware_value():
    """The specific regression: SWING tunes 0.03, DE hardcodes 0.04. Assert the key is sent and
    resolved from cfg (style-aware), not pinned to a literal."""
    expr = _value_expr_for(_de_request_source(), "max_entry_gap_pct")
    assert expr, "max_entry_gap_pct must be sent to decision-engine"
    assert "cfg.get(" in expr, "must resolve from the merged cfg so per-style values apply"


def test_de_threaded_keys_are_not_sent_as_none():
    """Passing None is WORSE than omitting the key: DE's own cfg.get(k, default) would resolve
    to None rather than falling back. Every fallback must be a concrete value."""
    body = _de_request_source()
    for k in pte._DE_THREADED_KEYS:
        expr = _value_expr_for(body, k)
        if expr is None:
            continue                      # coverage is asserted by the guard test above
        assert "None" not in expr, f"{k} may resolve to None — give it a concrete fallback"


def test_style_specific_gap_values_still_differ():
    """Guards the premise: if all styles were the same value, threading would be pointless.
    A future edit flattening these should fail here, not silently make the fix moot."""
    gaps = {s: pte._STYLE_OVERRIDES[s].get("max_entry_gap_pct")
            for s in ("GROWTH", "SWING", "LONG") if s in pte._STYLE_OVERRIDES}
    assert gaps.get("SWING") == 0.03
    assert len({v for v in gaps.values() if v is not None}) > 1
