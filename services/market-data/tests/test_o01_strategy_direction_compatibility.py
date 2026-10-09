"""O01 — a bearish view must never receive a bullish primary recommendation.

REPRODUCED BY THE REVIEWER: `signal=SELL`, no shares, IV rank 80, with valid bearish AND bullish
spreads available, `_recommend` returned `cash_secured_put` as the primary. A cash-secured put
obliges you to BUY at the strike: its risk is the stock falling, which is the very thing the
signal said would happen.

Two separate causes, and fixing either alone leaves the other:
  * the ranking tested the IV regime BEFORE direction, so rich IV reached an income trade first;
  * the final branch treated BEARISH as merely "not bullish", lumping it with no-view.

So compatibility is applied as a CONSTRAINT beside the holdings constraint — before every
ranking branch and before the fallback — rather than as a preference order a later branch can
overrule. That is the SF-03 lesson: fixing the order left the fallback handing back what the
order had demoted.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from services.options_strategies import (  # noqa: E402
    _recommend, _exposure_is_compatible, BULLISH_EXPOSURES, BEARISH_EXPOSURES)


def _entry(name, exposure, *, requires_shares=False, max_profit=500.0):
    return {"name": name, "directional_exposure": exposure,
            "requires_shares": requires_shares, "max_profit_per_contract": max_profit}


def _chain(**over):
    singles = {"long_call": _entry("Long call", "bullish"),
               "cash_secured_put": _entry("Cash-secured put", "income_bullish")}
    combos = {"bull_call_spread": _entry("Bull call spread", "bullish"),
              "bear_put_spread": _entry("Bear put spread", "bearish")}
    return {"singles": singles, "combos": combos, **over}


# ---- the reported witness -------------------------------------------------------------------

def test_a_sell_signal_with_no_shares_and_rich_iv_does_not_get_a_cash_secured_put():
    """THE EXACT REPRODUCED CASE."""
    got = _recommend(**_chain(), signal="SELL", iv_rank=80.0, holds_shares=False,
                     coverable_contracts=0)
    assert got["primary"] != "cash_secured_put"
    assert got["primary"] == "bear_put_spread"
    assert "cash_secured_put" in got["incompatible_with_direction"]


def test_the_same_holds_for_every_bearish_signal_and_every_iv_regime():
    """The IV regime decides HOW to express a view, never whether to invert it."""
    for signal in ("SELL", "STRONG_SELL"):
        for iv in (5.0, 50.0, 95.0, None):
            got = _recommend(**_chain(), signal=signal, iv_rank=iv, holds_shares=False,
                             coverable_contracts=0)
            assert got["primary"] not in ("cash_secured_put", "long_call", "bull_call_spread"), \
                f"{signal} / IV {iv} returned a long-exposure primary: {got['primary']}"


def test_a_bearish_view_with_no_bearish_structure_abstains_rather_than_inverting():
    """Acceptance from the finding: choose an eligible bearish structure OR abstain — never a
    bullish primary through an IV fallback."""
    chain = _chain(combos={"bull_call_spread": _entry("Bull call spread", "bullish")})
    got = _recommend(**chain, signal="SELL", iv_rank=80.0, holds_shares=False,
                     coverable_contracts=0)
    assert got["primary"] is None
    assert "contradict" in got["reason"]
    assert sorted(got["incompatible_with_direction"]) == [
        "bull_call_spread", "cash_secured_put", "long_call"]


def test_the_fallback_cannot_reintroduce_an_incompatible_structure():
    """SF-03's lesson: the previous fix changed the ORDER and the fallback handed back what the
    order had demoted. Only an incompatible structure is priced here, so the fallback is the
    only path that can run."""
    chain = {"singles": {"cash_secured_put": _entry("Cash-secured put", "income_bullish")},
             "combos": {}}
    got = _recommend(**chain, signal="STRONG_SELL", iv_rank=90.0, holds_shares=False,
                     coverable_contracts=0)
    assert got["primary"] is None


# ---- the exposure classification itself ------------------------------------------------------

def test_a_cash_secured_put_is_classified_as_long_exposure():
    """It reads as "get paid to wait", but the obligation is to BUY at the strike, so its risk
    is a falling stock. Mislabelling this is what made the bug possible."""
    assert "income_bullish" in BULLISH_EXPOSURES
    assert "income_bullish" not in BEARISH_EXPOSURES


def test_a_structure_that_declares_no_exposure_is_not_assumed_compatible():
    """Unknown is not permission — a structure added later without declaring which way it leans
    must not slip through on a default."""
    assert _exposure_is_compatible({}, bullish=False, bearish=True, holds_shares=False) is False
    assert _exposure_is_compatible({}, bullish=True, bearish=False, holds_shares=False) is False


def test_every_built_structure_declares_its_exposure():
    """Keyed off the structure's own field rather than a name list kept elsewhere, so this is
    what makes a newly added structure inherit the rule."""
    import ast
    src = (Path(__file__).resolve().parents[1]
           / "src/services/options_strategies.py").read_text()
    tree = ast.parse(src)
    built = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Subscript)
                and isinstance(node.targets[0].value, ast.Name)
                and node.targets[0].value.id in ("singles", "combos")
                and isinstance(node.value, ast.Dict)):
            key = node.targets[0].slice
            name = key.value if isinstance(key, ast.Constant) else None
            keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
            built.add((name, "directional_exposure" in keys))
    assert built, "no structures found — the walk is wrong, not the code"
    missing = sorted(n for n, has in built if not has)
    assert not missing, f"structures built without declaring exposure: {missing}"


# ---- a hedge of stock actually held is compatible with a bearish view -------------------------

def test_a_bearish_holder_may_hedge_the_stock_they_own():
    chain = {"singles": {"protective_put": _entry("Protective put", "hedge_long",
                                                  requires_shares=True)},
             "combos": {}}
    got = _recommend(**chain, signal="SELL", iv_rank=40.0, holds_shares=True,
                     coverable_contracts=2, holds_any_shares=True)
    assert got["primary"] == "protective_put"


def test_a_hedge_is_not_offered_to_someone_holding_nothing_to_hedge():
    chain = {"singles": {"protective_put": _entry("Protective put", "hedge_long",
                                                  requires_shares=True)},
             "combos": {}}
    got = _recommend(**chain, signal="SELL", iv_rank=40.0, holds_shares=False,
                     coverable_contracts=0)
    assert got["primary"] is None


# ---- no stated view is not a bullish view ----------------------------------------------------

def test_no_signal_does_not_present_an_income_trade_as_a_directional_case():
    """The game-plan route calls construction with `signal=None`. A default income answer must
    not masquerade as thesis-specific advice."""
    got = _recommend(**_chain(), signal=None, iv_rank=80.0, holds_shares=False,
                     coverable_contracts=0)
    assert got["stated_direction"] is None
    assert "No directional view was supplied" in got["direction_basis"]
    assert "not as a case for a direction" in got["direction_basis"]


def test_a_stated_view_carries_no_such_disclaimer():
    got = _recommend(**_chain(), signal="BUY", iv_rank=20.0, holds_shares=False,
                     coverable_contracts=0)
    assert got["stated_direction"] == "bullish"
    assert got["direction_basis"] is None


def test_the_no_view_primary_is_labelled_a_structure_comparison():
    """Being priceable is not being suitable. Without this label a primary recommendation on no
    view reads as an opportunity — and the game-plan route calls this with `signal=None`."""
    got = _recommend(**_chain(), signal=None, iv_rank=80.0, holds_shares=False,
                     coverable_contracts=0)
    assert got["primary_label"] == "Structure comparison — direction not assessed"
    assert "not the same as an opportunity being suitable" in got["direction_basis"]


def test_a_stated_view_carries_no_structure_comparison_label():
    got = _recommend(**_chain(), signal="BUY", iv_rank=20.0, holds_shares=False,
                     coverable_contracts=0)
    assert got["primary_label"] is None
