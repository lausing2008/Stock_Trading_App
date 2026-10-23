"""Audit R03/R04 (2026-10-22): unknown moneyness, volume, OI and IV stay unknown.

Before: the adapter used `int(v or 0)` / `0.0` and `itm=False` without a spot, the routes ran
`fillna(0)`, and the flow route read the chain with no spot at all — so every contract showed
OTM, half a chain showed 0% IV, and a chain with no measured puts read as "100% calls".

These tests run the REAL path: archived row -> uw_option_chain._to_chain_row -> routes'
_uw_option_chain frame -> the real get_options_flow body (decorators stripped).
"""
import ast
import inspect
import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from src.services import uw_option_chain as uwc

SRC = Path(__file__).resolve().parents[1] / "src/api/routes.py"
TREE = ast.parse(SRC.read_text())
NAMES = {"get_options_flow", "_with_current_scope", "unusual_premium_fields", "whale_coverage",
         "compute_options_pressure_score", "rank_unusual", "unusual_order_key"}
SESSION = date(2026, 10, 20)
EXP = date(2099, 1, 16)


def _arch(strike, kind, volume=500, oi=100, iv=0.4, sym="SYNTH"):
    return {"strike": strike, "option_type": kind, "expiry": EXP, "open_interest": oi, "volume": volume,
            "nbbo_bid": 1.0, "nbbo_ask": 1.2, "implied_volatility": iv, "delta": None, "gamma": None,
            "theta": None, "vega": None, "rho": None,
            "option_symbol": f"{sym}990116{kind[0].upper()}{int(strike * 1000):08d}"}


# ── adapter ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,value,status", [
    (None, None, "missing"), (0, 0, "ok"), (25, 25, "ok"), ("12", 12, "ok"),
    (-3, None, "invalid"), ("abc", None, "invalid"),
])
def test_counts_keep_zero_missing_and_invalid_distinct(raw, value, status):
    row = uwc._to_chain_row(_arch(100, "call", volume=raw, oi=raw), spot=None)
    assert row["volume"] == value and row["oi"] == value
    assert row["field_status"]["volume"] == status and row["field_status"]["oi"] == status


def test_unknown_iv_is_not_zero_volatility():
    assert uwc._to_chain_row(_arch(100, "call", iv=None), None)["iv"] is None
    assert uwc._to_chain_row(_arch(100, "call", iv=0.0), None)["iv"] == 0.0
    assert uwc._to_chain_row(_arch(100, "call", iv=-0.1), None)["field_status"]["iv"] == "invalid"


def test_moneyness_without_a_reference_is_unknown_not_otm():
    row = uwc._to_chain_row(_arch(100, "call"), spot=None)
    assert row["itm"] is None and row["moneyness"] is None


@pytest.mark.parametrize("kind,ref,expected", [
    ("call", 120, "ITM"), ("call", 80, "OTM"), ("call", 100, "ATM"),
    ("put", 80, "ITM"), ("put", 120, "OTM"), ("put", 100, "ATM"),
])
def test_moneyness_both_sides_and_exactly_at_strike(kind, ref, expected):
    row = uwc._to_chain_row(_arch(100, kind), spot=ref)
    assert row["moneyness"] == expected
    assert row["itm"] is (expected == "ITM")


def test_field_coverage_counts():
    rows = [uwc._to_chain_row(_arch(100 + i, "call", volume=v), None) for i, v in enumerate([5, None, -1, 0])]
    cov = uwc.field_coverage(rows)["volume"]
    assert cov == {"known": 2, "missing": 1, "invalid": 1, "total": 4}


# ── same-session reference price ───────────────────────────────────────────────────────────

class _Sess:
    def __init__(self, close=None, fail=False):
        self.close, self.fail, self.params = close, fail, []

    def execute(self, stmt, params):
        self.params.append((str(stmt), params))
        if self.fail:
            raise RuntimeError("db down")
        return SimpleNamespace(first=lambda: None if self.close is None else SimpleNamespace(close=self.close))

    def rollback(self):
        pass


def test_reference_is_the_chain_sessions_own_close_never_an_earlier_bar():
    s = _Sess(close=118.5)
    ref = uwc.session_reference_close(s, "synth", SESSION)
    assert ref == {"price": 118.5, "session": "2026-10-20", "source": "daily_close"}
    _, params = s.params[0]
    assert params == {"sym": "SYNTH", "d": SESSION}
    # conftest stubs sqlalchemy.text, so the predicate is checked in source: exact session only.
    src = inspect.getsource(uwc.session_reference_close)
    assert "DATE(p.ts) = :d" in src and "DATE(p.ts) <=" not in src


def test_no_matching_bar_or_a_failed_lookup_means_unknown():
    assert uwc.session_reference_close(_Sess(close=None), "SYNTH", SESSION) is None
    assert uwc.session_reference_close(_Sess(fail=True), "SYNTH", SESSION) is None
    assert uwc.session_reference_close(None, "SYNTH", SESSION) is None


def test_callers_cannot_pass_todays_price_into_a_settled_chain():
    assert "spot" not in inspect.signature(uwc.get_chain).parameters
    from src.api import routes
    assert "spot" not in inspect.signature(routes._uw_option_chain).parameters


def _quoted(strike, kind, bid, ask):
    return {**_arch(strike, kind), "nbbo_bid": bid, "nbbo_ask": ask}


# A session whose underlying traded at 120: parity K + C - P = 120 at every strike below.
PARITY_120 = [_quoted(100, "call", 20.9, 21.3), _quoted(100, "put", 0.9, 1.3),
              _quoted(120, "call", 2.9, 3.1), _quoted(120, "put", 2.9, 3.1),
              _quoted(140, "call", 0.4, 0.6), _quoted(140, "put", 20.4, 20.6)]


def _session_chain(rows, close):
    ref = (lambda s, sym, d: {"price": close, "session": d.isoformat(), "source": "daily_close"}) if close else (lambda *a: None)
    with patch.object(uwc, "_rows_for", lambda *a, **k: rows), \
         patch.object(uwc, "_coverage_for", lambda *a, **k: None), \
         patch.object(uwc, "session_reference_close", ref):
        return uwc.get_chain(None, "SYNTH", as_of=SESSION)


def test_chain_for_session_measures_moneyness_from_that_session():
    ch = _session_chain(PARITY_120, 120.0)
    assert ch["moneyness_reference"]["session"] == "2026-10-20"
    assert ch["moneyness_reference"]["basis_check"]["status"] == "compatible"
    by = {(r["strike"]): r for r in ch["calls"]}
    assert by[100.0]["moneyness"] == "ITM" and by[140.0]["moneyness"] == "OTM" and by[120.0]["moneyness"] == "ATM"
    assert {r["strike"]: r["moneyness"] for r in ch["puts"]}[140.0] == "ITM"
    assert ch["moneyness_unknown_reason"] is None


# ── R03 price basis: a split-adjusted stored close must not be paired with unadjusted strikes ──

def test_a_split_adjusted_close_against_pre_split_strikes_is_rejected():
    # After a 2-for-1 split, re-downloaded history stores this session's close as 60, while the
    # archived strikes (100/120/140) are still in pre-split terms around 120. Comparing 60 with
    # them would call every call OTM and every put ITM — wrong for all of them.
    ch = _session_chain(PARITY_120, 60.0)
    assert ch["moneyness_reference"] is None
    assert all(r["moneyness"] is None and r["itm"] is None for r in ch["calls"] + ch["puts"])
    assert "different price bases" in ch["moneyness_unknown_reason"] and "ratio 0.500" in ch["moneyness_unknown_reason"]


def test_a_reverse_split_adjustment_is_rejected_too():
    ch = _session_chain(PARITY_120, 1200.0)       # 1-for-10 reverse split
    assert ch["moneyness_reference"] is None and "ratio 10.000" in ch["moneyness_unknown_reason"]


def test_a_close_within_carry_tolerance_is_accepted():
    ch = _session_chain(PARITY_120, 123.0)        # 2.5% from parity: carry/dividends, not a split
    assert ch["moneyness_reference"]["price"] == 123.0


def test_a_5_for_4_split_is_outside_the_tolerance():
    ch = _session_chain(PARITY_120, 96.0)         # 120 / 1.25
    assert ch["moneyness_reference"] is None


def test_without_two_sided_call_put_pairs_the_basis_is_unverifiable_so_unknown():
    one_sided = [{**r, "nbbo_bid": 0} for r in PARITY_120]
    ch = _session_chain(one_sided, 120.0)
    assert ch["moneyness_reference"] is None
    assert "no two-sided call/put pair" in ch["moneyness_unknown_reason"]


def test_no_stored_close_is_its_own_reason():
    ch = _session_chain(PARITY_120, None)
    assert ch["moneyness_unknown_reason"] == "no daily close stored for the chain's session"


def test_parity_uses_the_pairs_nearest_the_money():
    implied = uwc.chain_implied_spot(PARITY_120)
    assert implied == {"price": 120.0, "pairs": 3}


# ── end to end through the flow route ──────────────────────────────────────────────────────

def _flow(rows: list[dict], reference: float | None = None, unknown_reason: str | None = None):
    from src.api import routes as _real

    def fake_get_chain(session, sym, *, expiry=None, allow_fetch=True, as_of=None):
        spot = reference
        calls = [uwc._to_chain_row(r, spot) for r in rows if r["option_type"] == "call"]
        puts = [uwc._to_chain_row(r, spot) for r in rows if r["option_type"] == "put"]
        return {"available": True, "as_of": SESSION.isoformat(), "calls": calls, "puts": puts,
                "coverage_status": None, "coverage_reason": None,
                "moneyness_reference": ({"price": reference, "session": SESSION.isoformat(), "source": "daily_close"}
                                        if reference else None),
                "moneyness_unknown_reason": unknown_reason,
                "field_coverage": {"calls": uwc.field_coverage(calls), "puts": uwc.field_coverage(puts)}}

    class _Cache:
        def get(self, k): return None
        def setex(self, *a): pass

    ns = {"__package__": "t.api", "Session": object, "Depends": lambda v: None, "get_session": lambda: None,
          "json": json, "_OPTIONS_TTL": 900, "_get_redis": _Cache,
          "_uw_session_expiries": lambda *_: (SESSION, [EXP.isoformat()]),
          "_options_flow_gex_component": lambda *_: None,
          "log": SimpleNamespace(warning=lambda *a, **k: None),
          "_uw_option_chain": _real._uw_option_chain}
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            node.decorator_list = []
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(SRC), "exec"), ns)
    ns.update({k: getattr(_real, k) for k in ("_UNUSUAL_MIN_OI", "_UNUSUAL_VOL_OI", "_LOW_OI_MIN_VOLUME",
                                             "contract_multiplier_status")})
    saved = {n: sys.modules.get(n) for n in ("t.services.uw_option_chain", "t.services.options_scope")}
    from src.services import options_scope as _scope
    sys.modules["t.services.options_scope"] = _scope
    mod = ModuleType("t.services.uw_option_chain"); mod.latest_as_of = lambda *_: SESSION
    sys.modules[mod.__name__] = mod
    try:
        with patch.object(uwc, "get_chain", fake_get_chain):
            return ns["get_options_flow"]("SYNTH", session=None)
    finally:
        for n, v in saved.items():
            if v is None: sys.modules.pop(n, None)
            else: sys.modules[n] = v


MIXED = [_arch(100, "call", 1300), _arch(105, "call", 200, oi=2000), _arch(95, "put", 1000), _arch(90, "put", 200, oi=2000)]


def test_baseline_mixed_chain_is_complete():
    out = _flow(MIXED)
    assert out["available"] and out["activity_composition"] == "mixed"
    assert out["composition_scope"] == "complete"
    assert out["volume_coverage"] == {"call": {"known": 2, "total": 2}, "put": {"known": 2, "total": 2}}


def test_removing_put_volume_does_not_make_an_all_call_reading():
    rows = [r if r["option_type"] == "call" else {**r, "volume": None} for r in MIXED]
    out = _flow(rows)
    assert out["available"] is True
    assert out["activity_composition"] == "unmeasured"       # never call_heavy
    assert out["call_share"] is None and out["cp_ratio"] is None and out["sentiment"] is None
    assert out["pressure_score"] is None
    assert out["volume_coverage"]["put"] == {"known": 0, "total": 2}
    assert out["put_volume"] is None and out["call_volume"] == 1500   # the frontend recomputes from these


def test_partial_missing_volume_is_an_observed_subtotal():
    rows = [*MIXED[:3], {**MIXED[3], "volume": None}]
    out = _flow(rows)
    assert out["composition_scope"] == "observed_subtotal"
    assert out["put_volume"] == 1000 and out["volume_coverage"]["put"] == {"known": 1, "total": 2}


def test_no_measured_volume_anywhere_is_unmeasured_not_no_volume():
    out = _flow([{**r, "volume": None} for r in MIXED])
    assert out["available"] is False and out["reason"] == "volume_unmeasured"


def test_missing_oi_is_unmeasured_not_a_new_listing():
    rows = [*MIXED, _arch(110, "call", volume=900, oi=None)]
    out = _flow(rows)
    assert all(r["strike"] != 110.0 for r in out["low_oi_activity"]["top"])
    assert all(u["strike"] != 110.0 for u in out["unusual"])
    assert out["unusual_unmeasured"]["call"] == 1


def test_unusual_rows_carry_unknown_iv_and_moneyness_as_none():
    rows = [_arch(100, "call", 1300, iv=None), *MIXED[2:]]
    out = _flow(rows)
    row = next(u for u in out["unusual"] if u["strike"] == 100.0)
    assert row["iv"] is None and row["itm"] is None and row["moneyness"] is None
    assert out["moneyness_reference"] is None
    json.dumps(out)   # no NaN in the payload


def test_flow_route_reports_why_moneyness_is_unknown():
    reason = "stored close 60.00 vs chain-implied 120.00 (ratio 0.500): different price bases, e.g. a split adjustment"
    out = _flow(MIXED, unknown_reason=reason)
    assert out["moneyness_reference"] is None and out["moneyness_unknown_reason"] == reason
    assert all(u["moneyness"] is None for u in out["unusual"])


def test_moneyness_measured_against_the_session_close_when_known():
    out = _flow(MIXED, reference=120.0)
    by = {(u["side"], u["strike"]): u for u in out["unusual"]}
    assert by[("call", 100.0)]["moneyness"] == "ITM" and by[("call", 100.0)]["itm"] is True
    assert by[("put", 95.0)]["moneyness"] == "OTM" and by[("put", 95.0)]["itm"] is False
    assert out["moneyness_reference"]["session"] == "2026-10-20"


# ── max pain and the snapshot writer ──────────────────────────────────────────────────────

def test_max_pain_excludes_unknown_oi_and_reports_coverage():
    from src.api.routes import compute_max_pain
    calls = [{"strike": 100.0, "oi": 500}, {"strike": 110.0, "oi": None}]
    puts = [{"strike": 90.0, "oi": 300}]
    mp = compute_max_pain(calls, puts)
    assert mp["total_call_oi"] == 500
    assert mp["oi_coverage"] == {"known": 2, "total": 3, "complete": False}


def test_snapshot_skips_a_chain_with_an_unmeasured_side():
    from src.services import options_flow_snapshot as ofs
    with patch.object(ofs, "_fetch_flow_response", lambda s: {"available": True, "cp_ratio": None,
                                                              "call_volume": 1500, "put_volume": 0}):
        assert ofs.compute_options_flow_with_reason("SYNTH") == (None, "volume_side_unmeasured")
