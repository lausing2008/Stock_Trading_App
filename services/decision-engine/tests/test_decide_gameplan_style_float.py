"""Regression test for BUG-DECIDE-GAMEPLAN-STYLEFLOAT.

_decide()'s game-plan resolution used to do a blanket `{k: float(v) for k, v in
req.game_plan.items()}` over every key in the incoming game_plan dict. The real production
caller — paper_trading_engine.py's _build_game_plan_for_style() — returns a dict that
legitimately includes a "style" key (a string like "GROWTH") alongside the numeric
entry1/entry2/breakout/stop/take_profit/current_price fields. Confirmed live in production:
3 real BUY candidates (AXON, DIVO, NET) hit a raw, unhandled ValueError on this line over a
single 24h window, each silently falling back to _should_enter() (the DE-outage fallback gate)
instead of getting decision-engine's real, primary scoring — with the only visible trace being
a "decision_engine.bad_status" warning log on the CALLING side, nothing on decision-engine's
own side beyond a bare 500.

Fix: convert only values that are actually numeric-convertible; pass anything else through
unchanged. Nothing in this service ever reads a non-numeric game_plan key (scorer.py/sizer.py/
hard_rejects.py all use game_plan.get("stop"/"take_profit"/etc.) with a numeric default), so
passing a string through unconverted is safe — it's simply never read.

routes.py is directly importable in this test environment (confirmed: only common/redis/httpx
need stubbing, matching test_game_plan_atr_mult.py's own established stub list), so this tests
the ACTUAL _decide() game-plan-resolution code path via source-text extraction of just that
block, run against a real dict — not a hand-copied reimplementation that could silently drift.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _resolve_game_plan(raw: dict) -> dict:
    """Extracts and runs the real game-plan-resolution block from _decide() against a plain
    dict, isolating it from the rest of the (async, heavily-dependent) function."""
    start = _ROUTES_SOURCE.index("        game_plan = {}\n        for k, v in req.game_plan.items():")
    end = _ROUTES_SOURCE.index("\n    else:\n", start)
    block = _ROUTES_SOURCE[start:end]
    # The block is indented as it sits inside _decide()'s `if req.game_plan:` body (8 spaces) —
    # dedent by that fixed amount before exec()'ing it as standalone top-level statements.
    dedented = "\n".join(line[8:] if line.startswith(" " * 8) else line for line in block.splitlines())
    namespace: dict = {"req": type("Req", (), {"game_plan": raw})()}
    exec(dedented, namespace)  # noqa: S102 — isolated eval of one real code block's actual source
    return namespace["game_plan"]


def test_numeric_fields_are_converted_to_float():
    result = _resolve_game_plan({"entry1": "24.50", "stop": 23.10, "take_profit": 26.0})
    assert result == {"entry1": 24.5, "stop": 23.1, "take_profit": 26.0}
    assert all(isinstance(v, float) for v in result.values())


def test_the_real_reported_bug_style_string_no_longer_crashes():
    """The exact production failure mode: a real game_plan dict from
    _build_game_plan_for_style() includes "style": "GROWTH" alongside numeric fields."""
    raw = {
        "entry1": 24.5, "entry2": 24.0, "breakout": 25.1,
        "stop": 23.1, "take_profit": 26.0, "current_price": 24.4,
        "style": "GROWTH",
    }
    result = _resolve_game_plan(raw)
    assert result["style"] == "GROWTH"  # passed through unchanged, not crashed on
    assert result["stop"] == 23.1
    assert result["take_profit"] == 26.0
    assert isinstance(result["stop"], float)
    assert isinstance(result["style"], str)


def test_every_real_style_value_survives_unconverted():
    """All 4 real trading styles this app uses — none of them should ever crash this block."""
    for style in ("SHORT", "SWING", "LONG", "GROWTH"):
        result = _resolve_game_plan({"stop": 10.0, "style": style})
        assert result["style"] == style


def test_none_value_also_passes_through_rather_than_crashing():
    """float(None) also raises TypeError — must be caught by the same guard as the
    ValueError-raising string case, not just the one exception type."""
    result = _resolve_game_plan({"stop": 10.0, "some_null_field": None})
    assert result["some_null_field"] is None


def test_a_purely_numeric_dict_is_unaffected_by_the_fix():
    """The common, non-buggy case (no style key at all) must produce identical output to the
    original blanket-float behavior — this fix must not change behavior for calls that never
    hit the bug in the first place."""
    raw = {"entry1": 24.5, "entry2": 24.0, "breakout": 25.1, "stop": 23.1, "take_profit": 26.0}
    result = _resolve_game_plan(raw)
    assert result == {k: float(v) for k, v in raw.items()}
