"""Call Finder (design: docs/features/2026-11-10-options-tab-call-finder-design.md).

A synthetic chain priced by the same model, so the expected answers follow from the design's
own worked example: the half-move rule lands near delta 0.70 for a near-term call and higher
for a one-year call, while max-at-target lands further out of the money.
"""
from datetime import date, timedelta

from src.services import option_call_finder as F
from src.services import option_pricing as bs

TODAY = date(2026, 11, 10)
SPOT = 100.0
TERMS = {"automatic_payoff_supported": True, "premium_multiplier": 100}


def chain(dtes=(21, 35, 45, 60, 91, 300, 400, 770), iv=0.40, spread=0.04, oi=500, greeks=True, strikes=None):
    rows = []
    for dte in dtes:
        exp = (TODAY + timedelta(days=dte)).isoformat()
        for k in strikes or [x * 2.5 for x in range(24, 57)]:
            mid = bs.call_price(SPOT, k, dte / 365, iv)
            if mid is None or mid < 0.05:
                continue
            g = bs.call_greeks(SPOT, k, dte / 365, iv)
            rows.append({"expiry": exp, "strike": k, "bid": round(mid * (1 - spread / 2), 2),
                         "ask": round(mid * (1 + spread / 2), 2), "oi": oi, "iv": iv * 100,
                         "delta": g["delta"] if greeks else None, "gamma": g["gamma"] if greeks else None,
                         "option_symbol": f"X{exp}C{k}", "contract_terms": TERMS})
    return rows


def test_near_term_expiry_rule():
    rows = chain()
    exp, why = F.pick_near_expiry(rows, TODAY, 14)
    assert exp == (TODAY + timedelta(days=35)).isoformat()        # >= max(2x14, 30) = 30 -> 35
    exp, _ = F.pick_near_expiry(rows, TODAY, 28)
    assert exp == (TODAY + timedelta(days=60)).isoformat()        # >= 56 -> 60
    assert F.pick_near_expiry(chain(dtes=(21, 120)), TODAY, 14)[0] is None


def test_near_term_prefers_the_more_liquid_of_two_dates_within_a_week():
    rows = chain(dtes=(35, 40))
    for r in rows:
        if r["expiry"] == (TODAY + timedelta(days=40)).isoformat():
            r["oi"] = 5000
    assert F.pick_near_expiry(rows, TODAY, 14)[0] == (TODAY + timedelta(days=40)).isoformat()


def test_leaps_expiry_rule_and_fallback():
    rows = chain()
    for r in rows:
        if r["expiry"] == (TODAY + timedelta(days=400)).isoformat():
            r["oi"] = 9000
    exp, _, full = F.pick_leaps_expiry(rows, TODAY)
    assert exp == (TODAY + timedelta(days=400)).isoformat() and full
    exp, _, full = F.pick_leaps_expiry(chain(dtes=(35, 300)), TODAY)
    assert exp == (TODAY + timedelta(days=300)).isoformat() and full is False
    assert F.pick_leaps_expiry(chain(dtes=(35,)), TODAY)[0] is None


def test_the_half_move_pick_is_in_the_money_and_the_aggressive_one_is_further_out():
    out = F.find("near", chain(), spot=SPOT, today=TODAY, hold_days=14)
    assert out["available"]
    pick, agg = out["pick"], out["aggressive_alternative"]
    assert 0.60 <= pick["delta"] <= 0.85
    assert agg is not None and agg["strike"] > pick["strike"]
    assert agg["scenarios"]["target"] > pick["scenarios"]["target"]
    assert pick["scenarios"]["half_move"] >= agg["scenarios"]["half_move"]


def test_leaps_pick_is_deeper_in_the_money():
    out = F.find("leaps", chain(), spot=SPOT, today=TODAY, hold_days=14)
    assert out["available"] and out["hold_days"] == 180
    assert out["pick"]["delta"] >= 0.70


def test_default_target_is_one_implied_sigma_and_a_given_target_is_used():
    out = F.find("near", chain(), spot=SPOT, today=TODAY, hold_days=14)
    assert 107.0 < out["target"] < 109.0 and "standard deviation" in out["target_basis"]
    out = F.find("near", chain(), spot=SPOT, today=TODAY, hold_days=14, target=120.0)
    assert out["target"] == 120.0 and out["target_basis"] == "your target"


def test_every_figure_and_reason_is_present():
    p = F.find("near", chain(), spot=SPOT, today=TODAY, hold_days=14)["pick"]
    assert set(p["reasons"]) == {"expiration", "strike", "delta", "gamma", "open_interest", "iv"}
    assert set(p["scenarios"]) >= {"target", "half_move", "flat", "down_one_sigma", "target_twice_as_late"}
    assert p["scenarios"]["flat"] < 0 and p["scenarios"]["down_one_sigma"] < p["scenarios"]["flat"]
    assert p["breakeven"] == round(p["strike"] + p["ask"], 4)
    assert 0 < p["model_prob_above_breakeven"] < 1


def test_gates_remove_and_count():
    rows = chain(dtes=(35,))
    rows[0]["bid"] = 0                                        # no two-sided quote
    rows[1]["oi"] = None                                      # unknown OI
    rows[2]["iv"] = None                                      # unknown IV
    rows[3]["contract_terms"] = {"automatic_payoff_supported": False}
    out = F.find("near", rows, spot=SPOT, today=TODAY, hold_days=14)
    r = out["rejected"]
    assert r["no_two_sided_quote"] >= 1 and r["open_interest_unknown_or_low"] >= 1
    assert r["iv_unknown_or_invalid"] >= 1 and r["unsupported_contract_terms"] == 1
    assert r["delta_outside_range"] > 0


def test_missing_greeks_are_estimated_and_labelled():
    out = F.find("near", chain(greeks=False), spot=SPOT, today=TODAY, hold_days=14)
    assert out["pick"]["greeks_estimated"] is True
    assert any("estimated" in w for w in out["warnings"])


def test_wide_quotes_fall_back_with_a_warning_and_nothing_qualifies_says_why():
    out = F.find("near", chain(spread=0.15), spot=SPOT, today=TODAY, hold_days=14)
    assert out["available"] and out["wide_quote_fallback"] and any("fallback" in w for w in out["warnings"])
    out = F.find("near", chain(spread=0.40), spot=SPOT, today=TODAY, hold_days=14)
    assert out["available"] is False and out["rejected"]["spread_too_wide"] > 0
    assert "most removed by" in out["reason"]          # names the largest cause


def test_warnings_for_events_iv_rank_and_trend():
    earn = (TODAY + timedelta(days=5)).isoformat()
    out = F.find("near", chain(), spot=SPOT, today=TODAY, hold_days=14, iv_rank=75,
                 events=[("Earnings", earn), ("Ex-dividend", (TODAY + timedelta(days=60)).isoformat())])
    w = " ".join(out["warnings"])
    assert "Earnings" in w and "Ex-dividend" not in w and "IV rank 75" in w
    out = F.find("leaps", chain(), spot=SPOT, today=TODAY, hold_days=14, sma200=110.0)
    assert any("200-day" in w for w in out["warnings"])


def test_no_verified_price_means_no_pick():
    assert F.find("near", chain(), spot=None, today=TODAY, hold_days=14)["available"] is False


# ── the route, with the archive and context stubbed ──────────────────────────────────────────

def _route(chain_rows, reference=True, iv_rank=(55.0, "2026-11-09", "ok")):
    from unittest.mock import patch
    from src.api import routes
    import src.services.uw_option_chain as uwc
    import src.services.options_scope as scope
    session_day = TODAY - timedelta(days=1)
    exps = sorted({r["expiry"] for r in chain_rows})
    calls = [{k: v for k, v in r.items() if k != "contract_terms"} for r in chain_rows]
    ch = {"available": True, "calls": calls, "puts": [],
          "moneyness_reference": {"price": SPOT} if reference else None,
          "moneyness_unknown_reason": None if reference else "no daily close stored"}
    with patch.object(routes, "_uw_session_expiries", lambda *_: (session_day, exps)), \
         patch.object(scope, "current_expiries", lambda e, *a, **k: e), \
         patch.object(uwc, "get_chain", lambda *a, **k: ch), \
         patch.object(routes, "_today_et", lambda: TODAY), \
         patch.object(routes, "_desk_iv_rank", lambda s, t: iv_rank), \
         patch.object(routes, "_call_finder_context", lambda *a: {"hv20": 0.35, "sma200": 95.0}), \
         patch.object(routes, "_call_finder_events", lambda s: [("Earnings", (TODAY + timedelta(days=40)).isoformat())]):
        return routes.get_options_call_finder("xyz", holding_days=14, target_price=None, session=None, _user=None)


def _real_symbols(rows):
    """OCC-style identities so the real contract-terms check accepts them."""
    out = []
    for r in rows:
        e = date.fromisoformat(r["expiry"])
        out.append({**r, "option_symbol": f"XYZ{e:%y%m%d}C{int(round(r['strike'] * 1000)):08d}"})
    return out


def test_route_returns_both_picks_and_the_glance_strip():
    out = _route(_real_symbols(chain()))
    assert out["available"] and out["near"]["available"] and out["leaps"]["available"]
    g = out["glance"]
    assert g["price"] == SPOT and g["iv_rank"] == 55.0 and g["hv20_pct"] == 35.0
    assert g["atm_iv_pct"] == 40.0 and g["implied_move_pct"] is not None
    assert g["events"] == [{"label": "Earnings", "date": (TODAY + timedelta(days=40)).isoformat()}]
    assert len(out["assumptions"]) == 4


def test_route_refuses_without_a_verified_price():
    out = _route(_real_symbols(chain()), reference=False)
    assert out["available"] is False and out["reason"] == "price_basis_unverified"


def test_route_refuses_contracts_whose_identity_does_not_match():
    out = _route(chain())        # option_symbol does not encode strike/expiry: terms refused
    assert out["available"] is False
    assert out["near"]["rejected"]["unsupported_contract_terms"] > 0


def test_a_flat_round_trip_costs_the_spread_entry_at_ask_exit_at_mid_less_half_spread():
    """Buying at the ask and selling back immediately at an unchanged price loses about the
    whole quoted spread. Pricing the entry at the mid would hide half of it."""
    mid = bs.call_price(SPOT, 100.0, 35 / 365, 0.40)
    c = {"strike": 100.0, "dte": 35, "iv": 0.40, "ask": mid * 1.02, "mid": mid, "spread_pct": 4.0}
    ret = F.scenario_return(c, SPOT, 0)
    assert abs(ret - (mid * 0.98 / (mid * 1.02) - 1)) < 1e-12
    assert -0.041 < ret < -0.038
