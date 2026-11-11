"""Options candidate outcome study — Part A archive replay (design: docs/features/
2026-10-29-options-candidate-outcome-study-design.md).

QUESTION. The decision desk shows, per strategy, a nearest-strike preset, up to three
alternatives and a "research lead" ranked by quote quality. Did any of those choices do better
than another, or than doing nothing? Research only: nothing here changes a live default.

RULES (the T399 income-backtest rules, src/backtest/options_income_backtest.py):
  1. REPLAY THE REAL BUILDER. Candidates come from options_strategies.build_strategy_matrix with
     the route's own expiry picker and the real archive-row adapter. The one port is the research
     lead, which lives in the frontend; `research_lead` reproduces it and is pinned to the
     TypeScript reference by shared golden cases (tests/fixtures/option_research_lead_golden.json).
  2. NO LOOKAHEAD. An entry on session D sees only D's chain and D's close. Later sessions are
     read only to mark the outcome of a trade already chosen.
  3. NO INVENTED INPUTS. Historical stop/target levels are not stored per day, so the builder runs
     with neither and uses its own at-the-money anchors. Preferences are empty.
  4. CONSERVATIVE FILLS. Buy at the archived ask, sell at the archived bid, on entry and on exit.
     A leg without the needed quote is not entered or not resolved, and that is counted.
  5. PRICE BASIS. A stored daily close is used only where the session's own put-call parity agrees
     with it (uw_option_chain.reference_basis): ingest may re-adjust history for splits while
     archived strikes keep their session's terms. A disagreement skips the entry or leaves the
     exit unresolved, never silently mixes bases. Plausible is not proof: see the R03 notes.

WHAT IT DOES NOT MODEL: real fills, size or impact; early assignment (calls before ex-dividend);
intraday paths; symbols outside the captured 150 (survivorship); whether an end-of-day NBBO was
executable. These make every figure optimistic in the same way for every arm.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import time as _time
from collections import defaultdict
from datetime import date, timedelta

# v2 (2026-11-11): adds the Call Finder's long-call choices (finder, finder_aggressive,
# finder_atm) and their paired comparisons. v1 arms and rules are unchanged.
STUDY_VERSION = "options_candidate_study_v2"
STRATEGIES = ("long_call", "protective_put", "covered_call", "cash_secured_put")
# The desk's existing evaluation defaults (frontend OPTION_HOLDING_DEFAULTS), not optimal windows.
HOLDING_DAYS = {"SHORT": 7, "SWING": 14, "LONG": 28, "GROWTH": 14}
# Frozen BEFORE the first run (design §"Order and gates"): the most recent full quarter.
DEVELOPMENT_END = date(2026, 6, 30)
HELD_OUT = (date(2026, 7, 1), date(2026, 9, 30))
BASE_SEED = 20261030
BOOTSTRAP_RESAMPLES = 2000
# Route expiry windows (routes._OPTIONS_GAME_PLAN_*_DTE); asserted equal in the tests.
PUT_DTE = (25, 60)
CALL_DTE = (14, 45)


# ── the research lead (port of frontend/src/lib/optionResearchLead.ts) ──────────────────────

from ..services.option_research_lead import (  # noqa: F401  (re-exported for the study and its tests)
    _MAX_SPREAD_PCT, _UNSET, _dte, _finite, lead_rows, lead_valid, research_lead)


# ── building one session's candidates with the real builder ───────────────────────────────

def chain_rows(archive_rows: list[dict], symbol: str) -> list[dict]:
    """Archive rows -> the desk's chain-row shape, via the real adapter and contract terms."""
    from ..services.uw_option_chain import _to_chain_row
    from ..services.options_contract import contract_terms
    out = []
    for r in archive_rows:
        row = _to_chain_row(r, None)
        row["contract_terms"] = contract_terms(row.get("option_symbol"), symbol)
        out.append(row)
    return out


def _pick_expiry(expiries: list[str], today: date, window: tuple[int, int]):
    from ..api.routes import _nearest_expiry_in_dte_window
    return _nearest_expiry_in_dte_window(expiries, today, window[0], window[1])


def build_session_plan(symbol: str, today: date, session_rows: list[dict], spot: float) -> dict | None:
    """The desk's default plan for `symbol` on `today`, from that session's archive rows only."""
    from ..services.options_strategies import build_strategy_matrix
    expiries = sorted({r["expiry"].isoformat() for r in session_rows
                       if r.get("expiry") is not None and r["expiry"] > today})
    put_exp, call_exp = _pick_expiry(expiries, today, PUT_DTE), _pick_expiry(expiries, today, CALL_DTE)
    if not put_exp and not call_exp:
        return None

    def side(exp, kind):
        return chain_rows([r for r in session_rows if r.get("expiry") is not None and r["expiry"].isoformat() == exp
                           and (r.get("option_type") or "").lower().startswith(kind)], symbol) if exp else []
    matrix = build_strategy_matrix(current_price=spot, stop_loss=None, take_profit=None, signal=None,
                                   put_rows=side(put_exp, "p"), put_expiry=put_exp,
                                   call_rows=side(call_exp, "c"), call_expiry=call_exp, today=today)
    return {"matrix": matrix, "put_expiry": put_exp, "call_expiry": call_exp}


# ── choosing the arms ─────────────────────────────────────────────────────────────────────

def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, (BASE_SEED,) + parts)).encode()).hexdigest()[:12], 16)


def choose_arms(symbol: str, today: date, plan: dict, key: str, horizon: str) -> dict:
    """{arm: strategy | None, '_reason': {arm: why not}} for one strategy and horizon."""
    matrix = plan["matrix"]
    review = (matrix.get("candidate_review") or {}).get(key) or {}
    hold = HOLDING_DAYS[horizon]
    arms, why = {}, {}
    lead = research_lead(key, matrix, review, today, hold)
    arms["lead"] = lead[0] if lead else None
    if not lead:
        why["lead"] = "no_qualifying_candidate"
    preset = (matrix.get("singles") or {}).get(key)
    arms["preset"] = preset if preset and (review.get("original") or {}).get("reason") is None else None
    if arms["preset"] is None:
        why["preset"] = (review.get("original") or {}).get("reason") or "no_preset"
    pool = lead_rows(key, matrix, review, today, hold)
    if pool:
        rng = random.Random(_seed(symbol, today.isoformat(), key, horizon))
        arms["random"] = rng.choice(pool)[0]
    else:
        arms["random"], why["random"] = None, "no_qualifying_candidate"
    arms["_reason"] = why
    return arms


# ── outcomes ──────────────────────────────────────────────────────────────────────────────

def _quote(row) -> tuple:
    if not row:
        return None, None
    bid, ask = row.get("nbbo_bid"), row.get("nbbo_ask")
    return (float(bid) if _finite(bid) else None), (float(ask) if _finite(ask) else None)


def entry_price(action: str, row) -> float | None:
    """Buy at the ask, sell at the bid; both must form a two-sided, uncrossed quote."""
    bid, ask = _quote(row)
    if bid is None or ask is None or bid <= 0 or ask <= 0 or bid > ask:
        return None
    return ask if action == "buy" else bid


def exit_price(action: str, row) -> float | None:
    """Close a long at the bid (0 is a real, worthless bid); buy back a short at the ask (> 0)."""
    bid, ask = _quote(row)
    if action == "buy":
        return bid if bid is not None and bid >= 0 else None
    return ask if ask is not None and ask > 0 else None


def intrinsic(right: str, strike: float, spot: float) -> float:
    return max(0.0, spot - strike) if right == "call" else max(0.0, strike - spot)


def trade_pnl(key: str, *, strike: float, opt_entry: float, opt_exit: float,
              stock_entry: float | None, stock_exit: float | None) -> tuple[float, float]:
    """(P&L per share, capital per share). Capital follows the calculator's ROI basis: premium
    for a long option, shares held (+ premium) when stock is part of the position, the strike
    for a cash-secured put."""
    if key == "long_call":
        return opt_exit - opt_entry, opt_entry
    if key == "protective_put":
        return (stock_exit - stock_entry) + (opt_exit - opt_entry), stock_entry + opt_entry
    if key == "covered_call":
        return (stock_exit - stock_entry) + (opt_entry - opt_exit), stock_entry
    if key == "cash_secured_put":
        return opt_entry - opt_exit, strike
    raise ValueError(key)


_NEEDS_STOCK = {"protective_put", "covered_call"}


def period_of(d: date) -> str:
    if d <= DEVELOPMENT_END:
        return "development"
    if HELD_OUT[0] <= d <= HELD_OUT[1]:
        return "held_out"
    return "post_freeze"


# ── the per-symbol replay ─────────────────────────────────────────────────────────────────

def session_spot(session_rows: list[dict], close: float | None) -> tuple[float | None, str]:
    """The stored close if this session's parity agrees with it, else None and why."""
    from ..services.uw_option_chain import chain_implied_spot, reference_basis
    if close is None or not (close > 0):
        return None, "no_stored_close"
    basis = reference_basis(close, chain_implied_spot(session_rows))
    return (close, "plausible") if basis["status"] == "plausible" else (None, f"price_basis_{basis['status']}")


def replay_symbol(symbol: str, sessions_iter, closes: dict, earnings: list[date], counters: dict) -> list[dict]:
    """Stream one symbol's archive (chronological (as_of, rows) pairs) and return resolved trades.

    Holding exits resolve at the first session on/after entry + H calendar days; expiry exits
    on the expiry date's own session. Anything that cannot resolve is counted, never guessed."""
    pending: list[dict] = []
    done: list[dict] = []
    prev_session: date | None = None

    def bump(k):
        counters[k] = counters.get(k, 0) + 1

    for as_of, rows in sessions_iter:
        by_symbol = {r.get("option_symbol"): r for r in rows if r.get("option_symbol")}
        spot, spot_status = session_spot(rows, closes.get(as_of))

        # 1) resolve exits that this session reaches
        still = []
        for t in pending:
            if t["exit_type"] == "holding":
                if as_of < t["target"]:
                    still.append(t)
                    continue
                if as_of >= date.fromisoformat(t["expiry"]):
                    bump("holding_exit_at_or_after_expiry"); continue
                px = exit_price(t["action"], by_symbol.get(t["option_symbol"]))
                if px is None:
                    bump("holding_exit_no_quote"); continue
            else:  # expiry
                exp = date.fromisoformat(t["expiry"])
                if as_of < exp:
                    still.append(t)
                    continue
                if as_of > exp:
                    bump("expiry_no_settlement_session"); continue
                if spot is None:
                    bump(f"expiry_{spot_status}"); continue
                px = intrinsic(t["right"], t["strike"], spot)
            stock_exit = None
            if t["strategy"] in _NEEDS_STOCK:
                if spot is None:
                    bump(f"exit_stock_{spot_status}"); continue
                stock_exit = spot
            pnl, capital = trade_pnl(t["strategy"], strike=t["strike"], opt_entry=t["opt_entry"], opt_exit=px,
                                     stock_entry=t["stock_entry"], stock_exit=stock_exit)
            stock_ret = (spot / t["stock_ref"] - 1.0) if spot is not None and t["stock_ref"] else None
            done.append({**{k: v for k, v in t.items() if k not in ("target",)},
                         "exit_session": as_of.isoformat(), "opt_exit": px, "stock_exit": stock_exit,
                         "pnl_per_share": pnl, "capital_per_share": capital, "roc": pnl / capital if capital > 0 else None,
                         "days_held": (as_of - date.fromisoformat(t["entry"])).days,
                         "stock_return": stock_ret,
                         "earnings_in_window": any(date.fromisoformat(t["entry"]) < e <= as_of for e in earnings)})
        pending = still

        # 2) entries: the first archived session of each ISO week (fewer, less correlated entries)
        wk_first = prev_session is None or prev_session.isocalendar()[:2] != as_of.isocalendar()[:2]
        prev_session = as_of
        if not wk_first:
            continue
        bump("entry_sessions")
        if spot is None:
            bump(f"entry_{spot_status}"); continue
        plan = build_session_plan(symbol, as_of, rows, spot)
        if plan is None:
            bump("entry_no_expiry_in_any_window"); continue
        for key in STRATEGIES:
            for horizon in HOLDING_DAYS:
                arms = choose_arms(symbol, as_of, plan, key, horizon)
                for arm in ("lead", "preset", "random"):
                    strat = arms.get(arm)
                    if strat is None:
                        bump(f"arm_{arm}_{arms['_reason'].get(arm, 'missing')}"); continue
                    leg = strat["legs"][0]
                    entry_row = by_symbol.get(leg.get("option_symbol"))
                    px = entry_price(leg["action"], entry_row)
                    if px is None:
                        bump(f"arm_{arm}_entry_not_two_sided"); continue
                    base = {"symbol": symbol, "entry": as_of.isoformat(), "period": period_of(as_of),
                            "strategy": key, "horizon": horizon, "arm": arm, "option_symbol": leg["option_symbol"],
                            "right": leg["right"], "action": leg["action"], "strike": float(leg["strike"]),
                            "expiry": leg["expiry"], "opt_entry": px, "stock_entry": spot if key in _NEEDS_STOCK else None,
                            "stock_ref": spot, "spread_pct": leg.get("spread_pct"), "oi": leg.get("oi")}
                    pending.append({**base, "exit_type": "holding", "target": as_of + timedelta(days=HOLDING_DAYS[horizon])})
                    pending.append({**base, "exit_type": "expiry", "target": None})
        # v2: the Call Finder, run on this session's chain only, with the same fills and exits.
        for horizon, chosen in finder_arms(symbol, as_of, rows, spot, counters).items():
            for arm, c in chosen.items():
                entry_row = by_symbol.get(c["option_symbol"])
                px = entry_price("buy", entry_row)
                if px is None:
                    bump(f"arm_{arm}_entry_not_two_sided"); continue
                base = {"symbol": symbol, "entry": as_of.isoformat(), "period": period_of(as_of),
                        "strategy": "long_call", "horizon": horizon, "arm": arm, "option_symbol": c["option_symbol"],
                        "right": "call", "action": "buy", "strike": float(c["strike"]), "expiry": c["expiry"],
                        "opt_entry": px, "stock_entry": None, "stock_ref": spot,
                        "spread_pct": c.get("spread_pct"), "oi": c.get("oi")}
                pending.append({**base, "exit_type": "holding", "target": as_of + timedelta(days=HOLDING_DAYS[horizon])})
                pending.append({**base, "exit_type": "expiry", "target": None})
    for t in pending:
        bump(f"unresolved_at_archive_end_{t['exit_type']}")
    # The same contract/exit can be chosen by several horizons or arms; each choice is its own
    # observation of that arm, which is what the comparison needs. Duplicates are not merged.
    return done


def finder_arms(symbol: str, as_of: date, rows: list[dict], spot: float, counters: dict) -> dict:
    """{horizon: {arm: contract}} from the real Call Finder on one session's chain.

    finder            the half-move pick (the finder's headline)
    finder_aggressive the highest return at the full target (its labelled alternative)
    finder_atm        the eligible contract nearest the money in the SAME expiry, through the same
                      gates: isolates the strike rule from the expiry rule
    No IV rank, events or 200-day average existed historically, so warnings do not apply; the
    target is the finder's own default (one implied standard deviation up).
    """
    from ..services import option_call_finder as F

    def bump(k):
        counters[k] = counters.get(k, 0) + 1

    calls = [r for r in chain_rows(rows, symbol)
             if r.get("expiry") and r["expiry"] > as_of.isoformat()
             and str(r.get("option_symbol") or "")[-9:-8] == "C"]
    out = {}
    for horizon, hold in HOLDING_DAYS.items():
        res = F.find("near", calls, spot=spot, today=as_of, hold_days=hold)
        if not res.get("available"):
            bump(f"arm_finder_unavailable"); continue
        pick = res["pick"]
        agg = res.get("aggressive_alternative") or pick
        cands, _rej, _wide = F.eligible(calls, res["expiry"], spot, as_of, F.NEAR)
        atm = min(cands, key=lambda c: (abs(c["strike"] - spot), c["strike"])) if cands else None
        chosen = {"finder": pick, "finder_aggressive": agg}
        if atm is not None:
            chosen["finder_atm"] = atm
        out[horizon] = chosen
    return out


# Paired comparisons: (arm, baseline), each on identical (symbol, entry, strategy, horizon, exit).
PAIRS = (("lead", "preset"), ("lead", "random"),
         ("finder", "finder_atm"), ("finder", "finder_aggressive"), ("finder", "preset"), ("finder", "random"))


# ── aggregation ───────────────────────────────────────────────────────────────────────────

def _clustered_ci(values_by_date: dict, seed: int) -> tuple:
    """Mean of per-entry values with a 95% CI from resampling whole entry DATES."""
    dates = sorted(values_by_date)
    if not dates:
        return None, None, None
    flat = [v for d in dates for v in values_by_date[d]]
    mean = statistics.fmean(flat)
    if len(dates) < 2:
        return mean, None, None
    rng = random.Random(seed)
    sums = {d: (sum(values_by_date[d]), len(values_by_date[d])) for d in dates}
    boots = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        s = n = 0
        for d in (rng.choice(dates) for _ in dates):
            s += sums[d][0]; n += sums[d][1]
        boots.append(s / n)
    boots.sort()
    return mean, boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]


def summarise(trades: list[dict]) -> dict:
    """Per period × strategy × horizon × exit × arm, with and without earnings windows; paired
    lead-minus-preset and lead-minus-random on identical (symbol, entry, strategy, horizon, exit);
    and a by-month table, which is read before any pooled figure."""
    out = {"groups": [], "paired": [], "monthly": []}
    for earnings_rule in ("all", "excluding_earnings"):
        pool = [t for t in trades if t["roc"] is not None and (earnings_rule == "all" or not t["earnings_in_window"])]
        groups = defaultdict(lambda: defaultdict(list))
        for t in pool:
            groups[(t["period"], t["strategy"], t["horizon"], t["exit_type"], t["arm"])][t["entry"]].append(t["roc"])
        for gk, by_date in sorted(groups.items()):
            flat = [v for vs in by_date.values() for v in vs]
            mean, lo, hi = _clustered_ci(by_date, _seed(*gk, earnings_rule))
            out["groups"].append({"earnings": earnings_rule, "period": gk[0], "strategy": gk[1], "horizon": gk[2],
                                  "exit": gk[3], "arm": gk[4], "n": len(flat), "entry_dates": len(by_date),
                                  "mean_roc": mean, "ci95": [lo, hi], "median_roc": statistics.median(flat),
                                  "win_rate": sum(v > 0 for v in flat) / len(flat), "worst_roc": min(flat)})
        keyed = defaultdict(dict)
        for t in pool:
            keyed[(t["period"], t["strategy"], t["horizon"], t["exit_type"], t["symbol"], t["entry"])][t["arm"]] = t["roc"]
        for arm_a, other in PAIRS:
            diffs = defaultdict(lambda: defaultdict(list))
            for (period, s, h, x, _sym, entry), arms in keyed.items():
                if arm_a in arms and other in arms:
                    diffs[(period, s, h, x)][entry].append(arms[arm_a] - arms[other])
            for gk, by_date in sorted(diffs.items()):
                # v1 seeds kept for lead pairs so v1 figures reproduce exactly.
                seed = _seed(*gk, other, earnings_rule) if arm_a == "lead" else _seed(*gk, arm_a, other, earnings_rule)
                mean, lo, hi = _clustered_ci(by_date, seed)
                out["paired"].append({"earnings": earnings_rule, "period": gk[0], "strategy": gk[1], "horizon": gk[2],
                                      "exit": gk[3], "comparison": f"{arm_a}_minus_{other}",
                                      "pairs": sum(len(v) for v in by_date.values()), "entry_dates": len(by_date),
                                      "mean_diff": mean, "ci95": [lo, hi]})
        if earnings_rule == "all":
            months = defaultdict(list)
            for t in pool:
                months[(t["entry"][:7], t["strategy"], t["horizon"], t["exit_type"], t["arm"])].append(t["roc"])
            out["monthly"] = [{"month": k[0], "strategy": k[1], "horizon": k[2], "exit": k[3], "arm": k[4],
                               "n": len(v), "mean_roc": statistics.fmean(v)} for k, v in sorted(months.items())]
    return out


# ── production source (read-only, bounded) and runner ────────────────────────────────────

_ROW_COLS = ("strike", "option_type", "expiry", "open_interest", "volume", "nbbo_bid", "nbbo_ask",
             "implied_volatility", "delta", "gamma", "theta", "vega", "rho", "option_symbol")


def iter_archive_sessions(session, symbol: str, *, chunk_days: int = 92, pause_s: float = 0.5,
                          timeout_ms: int = 90_000):
    """Yield (as_of, rows) chronologically, one bounded query per date chunk, read-only."""
    from sqlalchemy import text
    bounds = session.execute(text("SELECT min(as_of), max(as_of) FROM option_chain_history WHERE symbol = :s"),
                             {"s": symbol}).one()
    if bounds[0] is None:
        return
    start, end = bounds
    while start <= end:
        stop = start + timedelta(days=chunk_days)
        session.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
        res = session.execute(text(f"SELECT as_of, {', '.join(_ROW_COLS)} FROM option_chain_history "
                                   "WHERE symbol = :s AND as_of >= :a AND as_of < :b ORDER BY as_of"),
                              {"s": symbol, "a": start, "b": stop})
        current, rows = None, []
        for r in res:
            m = dict(r._mapping)
            d = m.pop("as_of")
            if d != current and rows:
                yield current, rows
                rows = []
            current = d
            rows.append(m)
        if rows:
            yield current, rows
        session.rollback()                      # end the read transaction between chunks
        _time.sleep(pause_s)
        start = stop


def load_closes(session, symbol: str) -> dict:
    from sqlalchemy import text
    rows = session.execute(text("""SELECT DATE(p.ts) AS d, p.close FROM prices p JOIN stocks s ON s.id = p.stock_id
                                   WHERE s.symbol = :s AND p.timeframe = 'D1' AND p.close IS NOT NULL"""),
                           {"s": symbol}).all()
    return {r.d: float(r.close) for r in rows}


def load_earnings(session, symbol: str) -> list[date]:
    from sqlalchemy import text
    return [r.report_date for r in session.execute(text("""SELECT e.report_date FROM earnings_events e
        JOIN stocks s ON s.id = e.stock_id WHERE s.symbol = :s AND e.report_date IS NOT NULL"""), {"s": symbol})]


def run(session, symbols: list[str], out_dir: str, *, pause_s: float = 0.5) -> dict:
    """Read-only over the archive; writes trades.jsonl, counters.json and summary.json to out_dir."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    counters: dict = {}
    trades_path = os.path.join(out_dir, "trades.jsonl")
    trades: list[dict] = []
    from sqlalchemy import text
    # Every transaction this run opens is read-only: a write anywhere below would fail loudly.
    session.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    with open(trades_path, "w") as fh:
        for i, sym in enumerate(symbols, 1):
            t0 = _time.time()
            closes, earnings = load_closes(session, sym), load_earnings(session, sym)
            session.rollback()
            got = replay_symbol(sym, iter_archive_sessions(session, sym, pause_s=pause_s), closes, earnings, counters)
            for t in got:
                fh.write(json.dumps(t) + "\n")
            trades += got
            print(json.dumps({"symbol": sym, "n": i, "of": len(symbols), "trades": len(got),
                              "seconds": round(_time.time() - t0, 1)}), flush=True)
    summary = {"study_version": STUDY_VERSION, "development_end": DEVELOPMENT_END.isoformat(),
               "held_out": [HELD_OUT[0].isoformat(), HELD_OUT[1].isoformat()], "holding_days": HOLDING_DAYS,
               "symbols": len(symbols), "trades": len(trades), "counters": dict(sorted(counters.items())),
               **summarise(trades)}
    with open(os.path.join(out_dir, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, default=str)
    return summary


if __name__ == "__main__":      # python -m src.backtest.options_candidate_study OUT_DIR [SYMBOL ...]
    import sys
    from db import SessionLocal
    from sqlalchemy import text
    out = sys.argv[1]
    with SessionLocal() as s:
        syms = sys.argv[2:] or [r[0] for r in s.execute(text(
            "WITH RECURSIVE t AS ((SELECT symbol FROM option_chain_history ORDER BY symbol LIMIT 1) UNION ALL "
            "SELECT (SELECT symbol FROM option_chain_history WHERE symbol > t.symbol ORDER BY symbol LIMIT 1) "
            "FROM t WHERE t.symbol IS NOT NULL) SELECT symbol FROM t WHERE symbol IS NOT NULL"))]
        s.rollback()
        run(s, syms, out)
