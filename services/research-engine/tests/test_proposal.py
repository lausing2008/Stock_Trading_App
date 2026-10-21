"""LLM-proposed plan: code computes every price from an anchor + ATR offset and checks it."""
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "services/research-engine/src"), str(ROOT / "services/research-engine")]

from src.copilot import proposal as PR          # noqa: E402
from src.copilot.plan import Position          # noqa: E402

# Hand-checkable fixture: close 100, ATR 2, 20-bar low/high 99/101, year high 110.
ANCHORS = {"level.close": 100.0, "level.low20": 99.0, "level.high20": 101.0, "level.sma50": 100.0,
           "level.high252": 110.0, "level.low252": 90.0}


def plan(kind="entry", position=None, trend_blocked=False, stale_key=None):
    items = {k: SimpleNamespace(stale=("bars are old" if k == stale_key else None)) for k in ANCHORS}
    items["rule.e5"] = SimpleNamespace(stale=None, display="No earnings within 10 days.")
    return SimpleNamespace(kind=kind, unsupported=False, position_refusals=[], atr=2.0, anchors=dict(ANCHORS),
                           items=items, position=position, currency="USD", trend_blocked=trend_blocked,
                           avg_volume20=1_000_000, as_of="2026-10-09T00:00:00")


def spec(tag):
    _, s, err = PR.extract(f"<answer>x</answer>{tag}")
    assert err is None, err
    return s


PULL = '<plan_proposal kind="pullback" entry="level.low20+0.5atr" stop="level.low20-0.5atr" target1="level.high252" target2="level.high252+2atr"/>'


def test_prices_are_computed_from_anchors_and_sized_by_the_rules_contract():
    p = PR.build(plan(position=Position(risk_amount=400)), spec(PULL), price_stale=None)
    # entry 99 + 1 = 100, stop 99 - 1 = 98, T1 110 (5.00R), T2 114; 400 / 2 = 200 shares.
    assert (p["state"], p["entry"], p["stop"], p["shares"]) == ("conditional", 100, 98, 200)
    assert [(t["price"], t["reward_risk"]) for t in p["targets"]] == [(110, 5.0), (114, 7.0)]
    assert p["max_fill"] == (110 + 2 * 98) / 3
    assert [(d["level"], d["anchor"], d["atr_multiple"]) for d in p["derivation"]] == [
        ("entry", "level.low20", 0.5), ("stop", "level.low20", -0.5), ("target1", "level.high252", 0.0), ("target2", "level.high252", 2.0)]
    assert {b["case"] for b in p["playbook"]} == {"rises", "falls", "sideways", "after_entry"}


def test_invalid_geometry_and_low_reward_risk_are_rejected_with_reasons():
    low_rr = PR.build(plan(), spec('<plan_proposal kind="pullback" entry="level.close" stop="level.close-1atr" target1="level.high20" target2="level.high252"/>'), price_stale=None)
    assert low_rr["state"] == "rejected" and any("0.50R; the minimum is 2R" in r for r in low_rr["reasons"])
    inverted = PR.build(plan(), spec('<plan_proposal kind="limit" entry="level.low20" stop="level.high20" target1="level.high252" target2="level.high252+1atr"/>'), price_stale=None)
    assert inverted["state"] == "rejected" and "stop < entry" in inverted["reasons"][0]
    tight = PR.build(plan(), spec('<plan_proposal kind="pullback" entry="level.low20" stop="level.low20-0.1atr" target1="level.high252" target2="level.high252+1atr"/>'), price_stale=None)
    assert any("inside normal daily noise" in r for r in tight["reasons"])
    breakout_below = PR.build(plan(), spec('<plan_proposal kind="breakout" entry="level.low20" stop="level.low20-1atr" target1="level.high252" target2="level.high252+1atr"/>'), price_stale=None)
    assert any("already been passed" in r for r in breakout_below["reasons"])


def test_mandatory_gates_block_stale_trend_and_unaffordable_proposals():
    stale = PR.build(plan(stale_key="level.low20"), spec(PULL), price_stale="the latest stored daily close is for 2026-10-05")
    assert stale["state"] == "blocked" and stale["reasons"] == ["Prices are stale: the latest stored daily close is for 2026-10-05."]
    assert [b["case"] for b in stale["playbook"]] == ["wait"]
    trend = PR.build(plan(trend_blocked=True), spec(PULL), price_stale=None)
    assert trend["state"] == "blocked" and "falling 50-bar average" in trend["reasons"][0]
    capped = PR.build(plan(position=Position(risk_amount=400, account=1000)), spec(PULL), price_stale=None)
    assert capped["shares"] == 10 and "stated account" in capped["sizing_note"]
    broke = PR.build(plan(position=Position(risk_amount=400, account=50)), spec(PULL), price_stale=None)
    assert broke["state"] == "blocked" and broke["shares"] == 0


def test_held_position_proposal_never_lowers_a_stated_stop():
    pos = Position(entry=95, shares=10, stop=97)
    ok = PR.build(plan("holding", pos), spec('<plan_proposal kind="hold" stop="level.low20-0.5atr" target1="level.high20" target2="level.high252"/>'), price_stale=None)
    assert (ok["state"], ok["entry"], ok["stop"], ok["shares"]) == ("conditional", 95, 98, 10)
    lower = PR.build(plan("holding", pos), spec('<plan_proposal kind="hold" stop="level.low20-2atr" target1="level.high20" target2="level.high252"/>'), price_stale=None)
    assert lower["state"] == "rejected" and any("never lowered" in r for r in lower["reasons"])
    wrong_kind = PR.build(plan("holding", pos), spec(PULL), price_stale=None)
    assert wrong_kind["state"] == "rejected"


def test_malformed_tags_and_unknown_anchors_are_format_errors():
    for tag in ('', PULL + PULL, '<plan_proposal kind="buy"/>',
                '<plan_proposal kind="pullback" entry="102.5" stop="level.low20" target1="level.high20" target2="level.high252"/>',
                '<plan_proposal kind="pullback" entry="level.low20+4atr" stop="level.low20" target1="level.high20" target2="level.high252"/>',
                '<plan_proposal kind="pullback" entry="level.vwap" stop="level.low20" target1="level.high20" target2="level.high252"/>'):
        _, s, err = PR.extract(f"<answer>x</answer>{tag}")
        assert s is None and err, tag
    _, s, err = PR.extract('<answer>x</answer><plan_proposal kind="wait"/>')
    assert s == {"kind": "wait"} and err is None
    assert PR.build(plan(), s, price_stale=None)["state"] == "wait"
