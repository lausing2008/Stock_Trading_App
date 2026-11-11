"""Options candidate outcome study (Part A): the replay harness, on a synthetic archive.

The production run reads the real archive; these tests pin the rules that make its numbers
mean anything: the lead port matches the TypeScript reference, the real builder is used, fills
are conservative, nothing looks ahead, price bases are not mixed, and nothing unresolved is
silently dropped.
"""
import copy
import json
import math
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.backtest import options_candidate_study as ocs

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = Path(__file__).with_name("fixtures") / "option_research_lead_golden.json"


# ── the lead port matches the TypeScript reference ──────────────────────────────────────

def _golden_cases():
    return json.loads(GOLDEN.read_text())["cases"]


@pytest.mark.parametrize("case", _golden_cases(), ids=lambda c: c["name"])
def test_research_lead_reproduces_the_typescript_reference(case):
    holding = case["holding_days"] if "holding_days" in case else ocs._UNSET
    lead = ocs.research_lead(case["key"], case["matrix"], case["review"], date.fromisoformat(case["today"]), holding)
    got = None if lead is None else {"index": lead[1], "option_symbol": lead[0]["legs"][0]["option_symbol"]}
    assert got == case["expected"]


def test_constants_match_the_route_and_the_frontend():
    from src.api import routes
    assert ocs.PUT_DTE == (routes._OPTIONS_GAME_PLAN_MIN_PUT_DTE, routes._OPTIONS_GAME_PLAN_MAX_PUT_DTE)
    assert ocs.CALL_DTE == (routes._OPTIONS_GAME_PLAN_MIN_CALL_DTE, routes._OPTIONS_GAME_PLAN_MAX_CALL_DTE)
    ts = (ROOT / "frontend/src/lib/optionResearchContext.ts").read_text()
    m = re.search(r"OPTION_HOLDING_DEFAULTS[^=]*=\s*\{([^}]*)\}", ts)
    assert {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", m.group(1))} == ocs.HOLDING_DAYS
    # The 20% spread screen is pinned behaviourally by the golden cases ("spread exactly 20
    # qualifies", "spread above 20"), which the TypeScript reference generated.


# ── fills and arithmetic ────────────────────────────────────────────────────────────────

def test_fills_buy_at_ask_sell_at_bid_and_refuse_bad_quotes():
    row = {"nbbo_bid": 1.9, "nbbo_ask": 2.1}
    assert ocs.entry_price("buy", row) == 2.1 and ocs.entry_price("sell", row) == 1.9
    for bad in ({"nbbo_bid": 0, "nbbo_ask": 2}, {"nbbo_bid": 2.2, "nbbo_ask": 2.1}, {"nbbo_bid": None, "nbbo_ask": 2}, None):
        assert ocs.entry_price("buy", bad) is None
    assert ocs.exit_price("buy", {"nbbo_bid": 0.0, "nbbo_ask": 0.05}) == 0.0        # worthless is real
    assert ocs.exit_price("sell", {"nbbo_bid": 0.0, "nbbo_ask": 0.0}) is None        # no offer to buy back
    assert ocs.exit_price("sell", {"nbbo_bid": 0.0, "nbbo_ask": 0.05}) == 0.05


@pytest.mark.parametrize("key,kw,expected", [
    ("long_call", dict(strike=100, opt_entry=2.0, opt_exit=5.0, stock_entry=None, stock_exit=None), (3.0, 2.0)),
    ("protective_put", dict(strike=95, opt_entry=1.0, opt_exit=6.0, stock_entry=100, stock_exit=89), (-6.0, 101.0)),
    ("covered_call", dict(strike=105, opt_entry=1.5, opt_exit=5.0, stock_entry=100, stock_exit=110), (6.5, 100.0)),
    ("cash_secured_put", dict(strike=95, opt_entry=1.2, opt_exit=0.0, stock_entry=None, stock_exit=None), (1.2, 95.0)),
])
def test_trade_pnl_and_capital(key, kw, expected):
    pnl, cap = ocs.trade_pnl(key, **kw)
    assert (round(pnl, 6), cap) == expected


def test_periods_are_frozen_before_the_run():
    assert ocs.period_of(date(2026, 6, 30)) == "development"
    assert ocs.period_of(date(2026, 7, 1)) == "held_out" and ocs.period_of(date(2026, 9, 30)) == "held_out"
    assert ocs.period_of(date(2026, 10, 1)) == "post_freeze"


# ── a synthetic archive in which parity holds exactly ───────────────────────────────────

SYM = "SYN"


def _occ(exp: date, kind: str, strike: float) -> str:
    return f"{SYM}{exp:%y%m%d}{kind}{int(round(strike * 1000)):08d}"


def _session(day: date, spot: float, expiries: list[date]) -> list[dict]:
    rows = []
    for exp in expiries:
        if exp <= day:
            continue
        for i, k in enumerate(range(70, 145, 5)):              # a fixed listed grid, as a real chain keeps
            tv = max(0.4, 3.0 - 0.12 * abs(k - spot)) * math.sqrt((exp - day).days / 30)
            half = 0.03 * (1 + i % 3)
            for kind, mid in (("C", tv + max(0.0, spot - k)), ("P", tv + max(0.0, k - spot))):
                rows.append({"strike": float(k), "option_type": "call" if kind == "C" else "put", "expiry": exp,
                             "open_interest": 100 + 37 * i, "volume": 10, "nbbo_bid": round(mid - half, 2),
                             "nbbo_ask": round(mid + half, 2), "implied_volatility": 0.3, "delta": None, "gamma": None,
                             "theta": None, "vega": None, "rho": None, "option_symbol": _occ(exp, kind, k)})
    return rows


def _archive(n_days=70, start=date(2026, 3, 2), path=lambda i: 100.0 + 0.2 * i):
    days = [start + timedelta(days=i) for i in range(n_days) if (start + timedelta(days=i)).weekday() < 5]
    # Fridays listed well beyond the archive's end, as a real chain always lists later expiries.
    expiries = [start + timedelta(days=i) for i in range(n_days + 120) if (start + timedelta(days=i)).weekday() == 4]
    sessions = [(d, _session(d, path(i), expiries)) for i, d in enumerate(days)]
    closes = {d: path(i) for i, d in enumerate(days)}
    return sessions, closes


def _run(sessions, closes, earnings=()):
    counters = {}
    trades = ocs.replay_symbol(SYM, iter(sessions), closes, list(earnings), counters)
    return trades, counters


def test_entries_only_on_the_first_session_of_each_week_and_trades_resolve():
    sessions, closes = _archive()
    trades, counters = _run(sessions, closes)
    assert trades
    assert {date.fromisoformat(t["entry"]).weekday() for t in trades} == {0}          # Mondays only
    assert counters["entry_sessions"] == len({d.isocalendar()[:2] for d, _ in sessions})
    # v2: the Call Finder's three long-call choices run beside the v1 arms.
    assert {t["arm"] for t in trades} == {"lead", "preset", "random", "finder", "finder_aggressive", "finder_atm"}
    assert {t["strategy"] for t in trades} == set(ocs.STRATEGIES)
    assert {t["exit_type"] for t in trades} == {"holding", "expiry"}


def test_the_real_builder_and_route_expiry_picker_are_used():
    sessions, closes = _archive()
    day, rows = sessions[0]
    plan = ocs.build_session_plan(SYM, day, rows, closes[day])
    from src.api import routes
    exps = sorted({r["expiry"].isoformat() for r in rows})
    assert plan["call_expiry"] == routes._nearest_expiry_in_dte_window(exps, day, 14, 45)
    assert plan["put_expiry"] == routes._nearest_expiry_in_dte_window(exps, day, 25, 60)
    assert set(plan["matrix"]["candidate_review"]) == set(ocs.STRATEGIES)


def test_fills_and_exits_are_read_from_the_archive_for_the_chosen_contract():
    sessions, closes = _archive()
    trades, _ = _run(sessions, closes)
    by_day = {d: {r["option_symbol"]: r for r in rows} for d, rows in sessions}
    t = next(t for t in trades if t["strategy"] == "long_call" and t["exit_type"] == "holding" and t["arm"] == "lead")
    entry_row = by_day[date.fromisoformat(t["entry"])][t["option_symbol"]]
    exit_row = by_day[date.fromisoformat(t["exit_session"])][t["option_symbol"]]
    assert t["opt_entry"] == entry_row["nbbo_ask"] and t["opt_exit"] == exit_row["nbbo_bid"]
    assert date.fromisoformat(t["exit_session"]) >= date.fromisoformat(t["entry"]) + timedelta(days=ocs.HOLDING_DAYS[t["horizon"]])
    e = next(t for t in trades if t["strategy"] == "cash_secured_put" and t["exit_type"] == "expiry")
    spot = closes[date.fromisoformat(e["exit_session"])]
    assert e["exit_session"] == e["expiry"] and e["opt_exit"] == max(0.0, e["strike"] - spot)


def test_no_lookahead_future_sessions_do_not_change_what_was_chosen():
    sessions, closes = _archive()
    base, _ = _run(sessions, closes)
    shocked = [(d, [{**r, "nbbo_bid": r["nbbo_bid"] * 3, "nbbo_ask": r["nbbo_ask"] * 3} for r in rows]) if i > 0 else (d, rows)
               for i, (d, rows) in enumerate(sessions)]
    other, _ = _run(shocked, closes)
    first = sessions[0][0].isoformat()
    pick = lambda ts: sorted((t["strategy"], t["horizon"], t["arm"], t["exit_type"], t["option_symbol"], t["opt_entry"])
                             for t in ts if t["entry"] == first)
    assert pick(base) == pick(other)


def test_a_split_adjusted_close_blocks_the_entry_instead_of_mixing_bases():
    sessions, closes = _archive()
    halved = {d: (c / 2 if d == sessions[0][0] else c) for d, c in closes.items()}   # re-ingested after a 2:1 split
    trades, counters = _run(sessions, halved)
    assert not any(t["entry"] == sessions[0][0].isoformat() for t in trades)
    assert counters.get("entry_price_basis_incompatible") == 1


def test_a_missing_exit_quote_is_counted_not_guessed():
    sessions, closes = _archive()
    trades, _ = _run(sessions, closes)
    victim = next(t for t in trades if t["exit_type"] == "holding" and t["arm"] == "lead")
    gone = [(d, [r for r in rows if not (d.isoformat() == victim["exit_session"] and r["option_symbol"] == victim["option_symbol"])])
            for d, rows in sessions]
    trades2, counters2 = _run(gone, closes)
    assert counters2.get("holding_exit_no_quote", 0) >= 1
    assert len(trades2) < len(trades)


def test_contracts_still_open_when_the_archive_ends_are_counted():
    sessions, closes = _archive()
    _, counters = _run(sessions[:12], closes)
    assert counters.get("unresolved_at_archive_end_expiry", 0) > 0


def test_earnings_inside_the_window_are_flagged():
    sessions, closes = _archive()
    trades, _ = _run(sessions, closes, earnings=[sessions[3][0]])
    first = sessions[0][0].isoformat()
    assert all(t["earnings_in_window"] for t in trades if t["entry"] == first and t["exit_session"] >= sessions[3][0].isoformat())


# ── aggregation ─────────────────────────────────────────────────────────────────────────

def _t(arm, roc, entry="2026-03-02", sym="A", **kw):
    return {"period": "development", "strategy": "long_call", "horizon": "SWING", "exit_type": "holding",
            "arm": arm, "roc": roc, "entry": entry, "symbol": sym, "earnings_in_window": False, **kw}


def test_paired_differences_use_only_matched_entries():
    trades = [_t("lead", 0.10), _t("preset", 0.02), _t("lead", 0.30, sym="B")]    # B has no preset
    s = ocs.summarise(trades)
    pair = next(p for p in s["paired"] if p["comparison"] == "lead_minus_preset" and p["earnings"] == "all")
    assert pair["pairs"] == 1 and pair["mean_diff"] == pytest.approx(0.08)


def test_earnings_exclusion_and_monthly_breakdown():
    trades = [_t("lead", 0.1), _t("lead", -0.5, earnings_in_window=True, entry="2026-04-06")]
    s = ocs.summarise(trades)
    g = {(x["earnings"]): x for x in s["groups"] if x["arm"] == "lead"}
    assert g["all"]["n"] == 2 and g["excluding_earnings"]["n"] == 1
    assert {m["month"] for m in s["monthly"]} == {"2026-03", "2026-04"}


def test_confidence_interval_resamples_dates_not_rows():
    # 50 identical rows on one date are ONE observation of that date, not fifty.
    mean, lo, hi = ocs._clustered_ci({"2026-03-02": [1.0] * 50, "2026-03-09": [0.0]}, 1)
    assert mean == pytest.approx(50 / 51)
    assert lo == pytest.approx(0.0) and hi == pytest.approx(1.0)



# ── v2: the Call Finder's choices (2026-11-11) ──────────────────────────────────────────────

def test_finder_arms_are_long_calls_from_the_real_finder_on_the_session_chain():
    from src.services import option_call_finder as F
    sessions, closes = _archive()
    trades, counters = _run(sessions, closes)
    finder = [t for t in trades if t["arm"].startswith("finder")]
    assert finder and {t["strategy"] for t in finder} == {"long_call"}
    assert {t["action"] for t in finder} == {"buy"} and {t["right"] for t in finder} == {"call"}
    for t in finder:
        hold = ocs.HOLDING_DAYS[t["horizon"]]
        dte = (date.fromisoformat(t["expiry"]) - date.fromisoformat(t["entry"])).days
        assert max(2 * hold, F.NEAR["min_dte_floor"]) <= dte <= F.NEAR["max_dte"]


def test_finder_atm_shares_the_finders_expiry_and_is_nearest_the_money():
    sessions, closes = _archive()
    day, rows = sessions[0]
    arms = ocs.finder_arms(SYM, day, rows, closes[day], {})
    assert arms
    for chosen in arms.values():
        assert chosen["finder_atm"]["expiry"] == chosen["finder"]["expiry"]
        assert abs(chosen["finder_atm"]["strike"] - closes[day]) <= abs(chosen["finder"]["strike"] - closes[day])


def test_finder_entries_buy_at_the_archived_ask():
    sessions, closes = _archive()
    trades, _ = _run(sessions, closes)
    asks = {(d.isoformat(), r["option_symbol"]): r["nbbo_ask"] for d, rows in sessions for r in rows}
    t = next(t for t in trades if t["arm"] == "finder")
    assert t["opt_entry"] == asks[(t["entry"], t["option_symbol"])]


def test_finder_pairs_are_reported():
    sessions, closes = _archive()
    trades, _ = _run(sessions, closes)
    names = {p["comparison"] for p in ocs.summarise(trades)["paired"]}
    assert {"finder_minus_finder_atm", "finder_minus_finder_aggressive", "finder_minus_preset",
            "finder_minus_random", "lead_minus_preset", "lead_minus_random"} <= names
