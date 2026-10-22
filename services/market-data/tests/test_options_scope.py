"""Options review Phase B1 + backend A2: exact composition and historical-vs-current expiry scope.

Behavioural: the real options_scope functions and the real routes, with only the chain reader,
cache and clock replaced. Composition cases are shared with the frontend card's tests
(tests/fixtures/options_composition_cases.json) so the two rules cannot drift.
"""
import json
import pathlib
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from src.api import routes
from src.services import options_scope as OS

CASES = json.loads((pathlib.Path(__file__).parent / "fixtures/options_composition_cases.json").read_text())["cases"]
SESSION = date(2026, 10, 20)                 # Tuesday; chain settled at its close
UTC = timezone.utc


def _at(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC)


class _Clock:
    """Pins options_scope's clock; everything else keeps the real datetime."""
    def __init__(self, now):
        self.now_value = now

    def __enter__(self):
        now = self.now_value

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return now if tz is None else now.astimezone(tz)
        self._p = patch.object(OS, "datetime", _DT)
        self._p.start()
        return self

    def __exit__(self, *a):
        self._p.stop()


# ── composition ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", CASES, ids=[c["why"] for c in CASES])
def test_shared_composition_cases(case):
    assert OS.volume_composition(case["call"], case["put"])["activity_composition"] == case["kind"]


def test_zero_sides_are_symmetric_and_never_a_capped_ratio():
    calls = OS.volume_composition(500, 0)
    puts = OS.volume_composition(0, 500)
    assert (calls["call_share"], calls["put_share"]) == (1.0, 0.0)
    assert (puts["call_share"], puts["put_share"]) == (0.0, 1.0)
    assert calls["cp_ratio_exact"] is None and calls["cp_ratio_basis"] == "undefined_no_puts"
    assert puts["cp_ratio_exact"] == 0.0 and puts["cp_ratio_basis"] == "zero_calls"
    assert calls["sample_adequacy"] == puts["sample_adequacy"] == "few_contracts_on_one_side"


def test_unmeasured_and_sample_note():
    assert OS.volume_composition(None, 10)["activity_composition"] == "unmeasured"
    assert OS.volume_composition(99, 5000)["sample_adequacy"] == "few_contracts_on_one_side"
    assert OS.volume_composition(100, 5000)["sample_adequacy"] == "adequate"


# ── expiry scope ───────────────────────────────────────────────────────────────────────────

def _scope(expiries, now, session=SESSION):
    return OS.expiry_scope(expiries, session, now)


def test_a_contract_expiring_at_the_source_close_is_historical_not_current():
    s = _scope(["2026-10-20", "2026-10-23"], _at(2026, 10, 21, 2, 0))
    assert s["historical_expiries"] == ["2026-10-20", "2026-10-23"]
    assert s["current_expiries"] == ["2026-10-23"]
    assert s["excluded_from_current"][0]["reason"] == "expired_at_source_session_close"


def test_same_day_expiry_is_current_until_the_close_and_not_at_it():
    # 2026-10-21 is EDT: the 16:00 ET close is 20:00 UTC; the close itself is not tradeable.
    assert _scope(["2026-10-21"], _at(2026, 10, 21, 19, 59, 59))["current_expiries"] == ["2026-10-21"]
    after = _scope(["2026-10-21"], _at(2026, 10, 21, 20, 0))
    assert after["current_expiries"] == [] and after["historical_expiries"] == ["2026-10-21"]
    assert after["excluded_from_current"][0]["reason"] == "last_trading_instant_passed"


def test_standard_time_close_is_2100_utc():
    # 2026-11-06 is after DST ends (Nov 1): 16:00 EST = 21:00 UTC. A fixed UTC-4 rule would
    # expire it an hour early.
    s = date(2026, 11, 5)
    assert _scope(["2026-11-06"], _at(2026, 11, 6, 20, 30), s)["current_expiries"] == ["2026-11-06"]
    assert _scope(["2026-11-06"], _at(2026, 11, 6, 21, 0), s)["current_expiries"] == []


def test_early_close_day_ends_at_1300_et():
    s = date(2026, 11, 25)
    assert _scope(["2026-11-27"], _at(2026, 11, 27, 17, 59), s)["current_expiries"] == ["2026-11-27"]
    assert _scope(["2026-11-27"], _at(2026, 11, 27, 18, 0), s)["current_expiries"] == []


def test_expiry_on_a_holiday_uses_the_previous_session_close():
    # 2027-03-26 (Good Friday) is an NYSE holiday: trading ends Thursday 16:00 EDT = 20:00 UTC.
    s = date(2027, 3, 24)
    scope = _scope(["2027-03-26"], _at(2027, 3, 25, 20, 0), s)
    assert scope["current_expiries"] == []
    assert scope["expiries"][0]["last_trading_basis"].startswith("previous_session_close")


def test_expiries_before_the_session_or_without_one_are_neither():
    s = _scope(["2026-10-19", "2026-10-23"], _at(2026, 10, 20, 12, 0))
    assert s["historical_expiries"] == ["2026-10-23"]
    assert s["excluded_from_current"][0]["reason"] == "before_source_session"
    none = OS.expiry_scope(["2026-10-23"], None, _at(2026, 10, 20, 12, 0))
    assert none["current_expiries"] == [] and none["excluded_from_current"][0]["reason"] == "source_session_unknown"


# ── options-flow route ─────────────────────────────────────────────────────────────────────

class _Cache:
    def __init__(self, stored=None):
        self.stored = stored
    def get(self, key):
        return self.stored
    def setex(self, key, ttl, value):
        self.stored = value


def _side(total, unusual=0, expiry_note=0):
    rows = [{"strike": 100 + i, "volume": 200, "openInterest": 100, "lastPrice": 0.0, "last_price_available": False,
             "bid": 1.0, "ask": 1.2, "impliedVolatility": 0.3, "inTheMoney": False} for i in range(unusual)]
    rows.append({"strike": 150, "volume": max(total - 200 * unusual, 0), "openInterest": 10_000_000, "lastPrice": 0.0,
                 "last_price_available": False, "bid": 1.0, "ask": 1.2, "impliedVolatility": 0.3, "inTheMoney": False})
    return pd.DataFrame(rows)


def _flow(expiries, per_exp, *, cache=None, now=_at(2026, 10, 21, 2, 0)):
    """per_exp: expiry -> (calls, puts, as_of, coverage) or an Exception to raise."""
    calls_seen = []

    def chain(session, sym, exp, spot=None, as_of=None):
        calls_seen.append((exp, as_of))
        v = per_exp[exp]
        if isinstance(v, Exception):
            raise v
        c, p, a, cov = v
        return SimpleNamespace(calls=_side(c, unusual=1), puts=_side(p), as_of=a.isoformat(),
                               coverage_status=cov[0], coverage_reason=cov[1])

    with patch.object(routes, "_uw_session_expiries", lambda *_: (SESSION, expiries)), \
         patch.object(routes, "_uw_option_chain", chain), \
         patch.object(routes, "_get_redis", lambda: cache or _Cache()), \
         patch.object(routes, "_options_flow_gex_component", lambda *_: None), _Clock(now):
        return routes.get_options_flow("SYNTH", session=None), calls_seen


COMPLETE = ("complete", "terminal_page")


def test_route_labels_from_exact_volumes_and_keeps_the_legacy_fields():
    out, _ = _flow(["2026-10-23"], {"2026-10-23": (1296, 1000, SESSION, COMPLETE)})
    assert out["cp_ratio"] == 1.3 and out["sentiment"] == "bullish"     # legacy, unchanged
    assert out["activity_composition"] == "mixed"                        # 1.296 < 1.3
    assert out["call_share"] == pytest.approx(1296 / 2296)
    assert out["cp_ratio_basis"] == "exact"


def test_every_slice_is_pinned_to_one_session_and_coverage_is_carried():
    out, seen = _flow(["2026-10-20", "2026-10-23"], {
        "2026-10-20": (500, 500, SESSION, COMPLETE), "2026-10-23": (700, 300, SESSION, COMPLETE)})
    assert {a for _, a in seen} == {SESSION}
    assert out["source_session"] == out["chain_as_of"] == "2026-10-20"
    assert (out["coverage_status"], out["coverage_reason"]) == COMPLETE


def test_a_slice_from_another_session_is_flagged_and_never_pooled():
    out, _ = _flow(["2026-10-21", "2026-10-23"], {
        "2026-10-21": (500, 500, SESSION, COMPLETE), "2026-10-23": (9000, 1, date(2026, 10, 21), COMPLETE)})
    assert out["expiries_used"] == ["2026-10-21"]
    assert out["call_volume"] == 500
    assert out["mixed_session_slices"] == [{"expiry": "2026-10-23", "as_of": "2026-10-21"}]
    assert out["coverage_status"] == "partial"


def test_an_unreadable_slice_makes_coverage_partial_not_archive_complete():
    out, _ = _flow(["2026-10-21", "2026-10-23"], {
        "2026-10-21": (500, 500, SESSION, COMPLETE), "2026-10-23": RuntimeError("read failed")})
    assert out["coverage_status"] == "partial"


def test_same_day_expiry_stays_as_evidence_but_is_not_a_current_candidate():
    out, _ = _flow(["2026-10-20", "2026-10-23"], {
        "2026-10-20": (500, 500, SESSION, COMPLETE), "2026-10-23": (700, 300, SESSION, COMPLETE)})
    assert out["call_volume"] == 1200                                   # both in the session's volume
    assert out["expiry_scope"]["current_expiries"] == ["2026-10-23"]
    flags = {c["expiry"]: c["current_candidate"] for c in out["unusual"]}
    assert flags == {"2026-10-20": False, "2026-10-23": True}


def test_scope_is_recomputed_on_a_cached_payload():
    """Cached at 15:59 ET, served at 16:05 ET on the expiry day: no longer current."""
    cache = _Cache()
    first, _ = _flow(["2026-10-21"], {"2026-10-21": (500, 500, SESSION, COMPLETE)},
                     cache=cache, now=_at(2026, 10, 21, 19, 59))
    assert first["expiry_scope"]["current_expiries"] == ["2026-10-21"]
    assert "expiry_scope" not in json.loads(cache.stored)               # never cached
    later, seen = _flow(["2026-10-21"], {}, cache=cache, now=_at(2026, 10, 21, 20, 5))
    assert seen == []                                                   # served from cache
    assert later["expiry_scope"]["current_expiries"] == []
    assert later["unusual"][0]["current_candidate"] is False


# ── options-chain route ────────────────────────────────────────────────────────────────────

def _chain_route(expiry, now):
    def chain(session, sym, exp, spot=None, as_of=None):
        return SimpleNamespace(calls=_side(10), puts=_side(10), as_of=as_of.isoformat(),
                               coverage_status="complete", coverage_reason="terminal_page")
    with patch.object(routes, "_uw_session_expiries", lambda *_: (SESSION, ["2026-10-21", "2026-10-23"])), \
         patch.object(routes, "_uw_option_chain", chain), patch.object(routes, "_get_redis", lambda: _Cache()), \
         _Clock(now):
        return routes.get_options_chain("SYNTH", expiry=expiry, session=None)


def test_chain_defaults_to_the_nearest_current_expiry():
    out = _chain_route(None, _at(2026, 10, 21, 20, 5))
    assert out["expiry"] == "2026-10-23" and out["expiry_is_current"] is True
    assert out["expiries"] == ["2026-10-21", "2026-10-23"]              # history still listed
    assert out["current_expiries"] == ["2026-10-23"]


def test_an_expired_expiry_is_served_on_request_and_labelled():
    out = _chain_route("2026-10-21", _at(2026, 10, 21, 20, 5))
    assert out["expiry"] == "2026-10-21" and out["expiry_is_current"] is False


# ── game plan: trade candidates only from current expiries ─────────────────────────────────

def test_game_plan_route_never_offers_an_expired_expiry():
    seen = {}

    def spy(expiries, today, lo, hi):
        seen.setdefault("expiries", list(expiries))
        return None
    with patch.object(routes, "_uw_session_expiries", lambda *_: (SESSION, ["2026-10-21", "2026-11-20"])), \
         patch.object(routes, "_goal_current_price", lambda *_: 100.0), \
         patch.object(routes, "_nearest_expiry_in_dte_window", spy), _Clock(_at(2026, 10, 21, 20, 5)):
        routes.get_options_game_plan("SYNTH", session=None, _user=None)
    assert seen["expiries"] == ["2026-11-20"]


def test_game_plan_route_says_so_when_nothing_is_current():
    with patch.object(routes, "_uw_session_expiries", lambda *_: (SESSION, ["2026-10-20"])), \
         _Clock(_at(2026, 10, 21, 2, 0)):
        out = routes.get_options_game_plan("SYNTH", session=None, _user=None)
    assert out["available"] is False and out["reason"] == "no_current_expiries"


def test_game_plan_snapshot_never_offers_an_expired_expiry():
    from src.services import options_game_plan_snapshot as GPS
    seen = {}

    def spy(expiries, today, lo, hi):
        seen.setdefault("expiries", list(expiries))
        raise RuntimeError("stop here")
    with patch.object(routes, "_uw_session_expiries", lambda *_: (SESSION, ["2026-10-21", "2026-11-20"])), \
         patch.object(routes, "_goal_current_price", lambda *_: 100.0), \
         patch.object(routes, "_nearest_expiry_in_dte_window", spy), _Clock(_at(2026, 10, 21, 20, 5)):
        try:
            GPS.compute_options_game_plan_snapshot(SimpleNamespace(execute=lambda *a, **k: SimpleNamespace(
                scalars=lambda: SimpleNamespace(first=lambda: None))), 1, "SYNTH")
        except Exception:
            pass
    assert seen.get("expiries") == ["2026-11-20"]


def test_session_expiries_are_sorted():
    """The nearest-N and default-expiry logic index into this list; provider order is not a contract."""
    import src.services.uw_option_chain as UW
    with patch.object(UW, "get_session_expiries", lambda *a, **k: (SESSION, ["2026-11-20", "2026-10-23", "2026-10-30"])):
        assert routes._uw_session_expiries(None, "SYNTH") == (SESSION, ["2026-10-23", "2026-10-30", "2026-11-20"])


def test_incomplete_archive_is_still_reported_when_a_slice_also_failed():
    out, _ = _flow(["2026-10-21", "2026-10-23"], {
        "2026-10-21": (500, 500, SESSION, ("incomplete", "request_failed")), "2026-10-23": RuntimeError("read failed")})
    assert out["coverage_status"] == "partial"
    assert (out["archive_coverage_status"], out["archive_coverage_reason"]) == ("incomplete", "request_failed")


def test_a_pinned_read_returns_that_session_and_never_fetches():
    """get_chain(as_of=...) against two archived sessions: rows, expiries and coverage all come
    from the pinned (older) session, and no on-demand capture runs."""
    import src.services.uw_option_chain as UW
    older, newer = date(2026, 10, 19), date(2026, 10, 20)
    archive = {
        older: [{"strike": 100, "option_type": "call", "expiry": date(2026, 10, 23), "open_interest": 5, "volume": 7}],
        newer: [{"strike": 100, "option_type": "call", "expiry": date(2026, 10, 30), "open_interest": 9, "volume": 9}],
    }
    asked = []

    def rows_for(session, symbol, as_of, expiry=None):
        asked.append(as_of)
        return [r for r in archive[as_of] if expiry is None or r["expiry"] == expiry]
    cov = {older: {"status": "incomplete", "completion_reason": "request_failed", "pages_fetched": 1,
                   "page_size": 500, "rows_returned": 1, "retrieved_at": None}}
    with patch.object(UW, "_rows_for", rows_for), patch.object(UW, "_coverage_for", lambda s, sym, a: cov.get(a)), \
         patch.object(UW, "latest_as_of", lambda *a, **k: newer), \
         patch("src.services.scheduler.capture_option_chain_history", side_effect=AssertionError("fetched")):
        ch = UW.get_chain(None, "SYNTH", as_of=older)
    assert ch["available"] and ch["as_of"] == "2026-10-19"
    assert ch["expiries"] == ["2026-10-23"] and ch["calls"][0]["volume"] == 7
    assert (ch["coverage_status"], ch["coverage_reason"]) == ("incomplete", "request_failed")
    assert set(asked) == {older}


# ── B2/B3: turnover labelling and selection disclosure on the real route ──────────────────

def _rows(specs):
    return pd.DataFrame([{"strike": k, "volume": v, "openInterest": oi, "lastPrice": 0.0, "last_price_available": False,
                          "bid": 1.0, "ask": 1.2, "impliedVolatility": 0.3, "inTheMoney": False,
                          "option_symbol": sym} for k, v, oi, sym in specs])


def _flow_frames(calls, puts):
    def chain(session, sym, exp, spot=None, as_of=None):
        return SimpleNamespace(calls=calls, puts=puts, as_of=SESSION.isoformat(),
                               coverage_status="complete", coverage_reason="terminal_page")
    with patch.object(routes, "_uw_session_expiries", lambda *_: (SESSION, ["2026-10-23"])), \
         patch.object(routes, "_uw_option_chain", chain), patch.object(routes, "_get_redis", lambda: _Cache()), \
         patch.object(routes, "_options_flow_gex_component", lambda *_: None), _Clock(_at(2026, 10, 21, 2, 0)):
        return routes.get_options_flow("SYNTH", session=None)


def test_selection_rule_discloses_eligible_kept_and_displayed_counts_per_side():
    calls = _rows([(100 + i, 1000 + i, 100, None) for i in range(5)] + [(200, 10, 10_000_000, None)])
    puts = _rows([(90, 500, 100, None), (80, 300, 10, None), (70, 150, 0, None), (60, 99, 5, None)])
    out = _flow_frames(calls, puts)
    sel = out["unusual_selection"]
    assert sel["eligible"] == {"call": 5, "put": 1}          # all five calls qualified...
    assert sel["kept"] == {"call": 3, "put": 1}              # ...the per-side cap kept three
    assert sel["displayed"] == {"call": 3, "put": 1}
    assert "does not show that positions were opened" in sel["rule"]
    low = out["low_oi_activity"]
    assert low["count"] == {"call": 0, "put": 2}             # oi <= 50 and volume >= 100 only
    assert [r["strike"] for r in low["top"]] == [80.0, 70.0]


def test_route_rows_carry_turnover_basis_and_never_a_whale_from_the_chain():
    calls = _rows([(100, 5000, 100, "SYNTH261023C00100000"), (101, 5000, 100, "SYNTH1261023C00101000")])
    out = _flow_frames(calls, _rows([(90, 10, 10_000_000, None)]))
    by_strike = {c["strike"]: c for c in out["unusual"]}
    assert by_strike[100.0]["estimated_turnover"] == 5000 * 1.1 * 100
    assert by_strike[100.0]["turnover_basis"] == "volume_x_midpoint_x_100"
    assert by_strike[101.0]["estimated_turnover"] is None                    # adjusted contract
    assert by_strike[101.0]["multiplier_status"] == "nonstandard_root_deliverable_unknown"
    assert all(c["traded_premium"] is None and c["is_whale"] is None for c in out["unusual"])
    assert out["whale_count"] is None


def test_route_last_price_only_row_is_labelled_turnover_end_to_end():
    calls = pd.DataFrame([{"strike": 100, "volume": 5000, "openInterest": 100, "lastPrice": 2.0, "last_price_available": True,
                           "bid": 0.0, "ask": 0.0, "impliedVolatility": 0.3, "inTheMoney": False, "option_symbol": None}])
    out = _flow_frames(calls, _rows([(90, 10, 10_000_000, None)]))
    row = out["unusual"][0]
    assert row["turnover_basis"] == "volume_x_last_price_x_100" and row["estimated_turnover"] == 1_000_000.0
    assert row["traded_premium"] is None and row["is_whale"] is None
    assert out["whale_count_basis"].startswith("unavailable — no event-level")
