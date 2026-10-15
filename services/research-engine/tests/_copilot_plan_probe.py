"""Plan mode exercised against a real database with a scripted model.

Same harness as `_copilot_probe.py`: PostgreSQL when STOCKAI_INTEL_DB names one, otherwise a
throwaway SQLite file. The fixtures are built so every level is computable by hand:
260 daily bars with high 101 / low 99 / close 100 give a true range of exactly 2 on every bar,
so ATR(14) = 2.00, the 20-bar low = 99, the 20-bar high = 101 and both 50-bar averages = 100.
Each scenario prints a measurement; test_copilot_plan.py asserts them.
"""
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import types
from datetime import datetime, timedelta
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "services" / "research-engine"))

for m in ["redis", "httpx", "structlog", "yfinance", "pandas"]:
    sys.modules.setdefault(m, MagicMock())

import common                                                        # noqa: E402
_cfg = types.ModuleType("common.config")
_DB_URL = os.environ.get("STOCKAI_INTEL_DB") or (
    "sqlite:///" + str(pathlib.Path(tempfile.mkdtemp()) / "copilot_plan.db"))
_cfg.get_settings = lambda: types.SimpleNamespace(
    database_url=_DB_URL, admin_password=None, jwt_secret="x", email_provider="", email_from="")
sys.modules["common.config"] = _cfg
common.config = _cfg

from sqlalchemy import create_engine, text                           # noqa: E402
from sqlalchemy.orm import sessionmaker                              # noqa: E402
from db.models import (Base, EarningsEvent, Exchange, Market, Price,  # noqa: E402
                       Signal, SignalHorizon, SignalOutcomeHorizon, SignalType, Stock,
                       TimeFrame, User)
from src.copilot import grounding as G                               # noqa: E402
from src.copilot import intent as I                                  # noqa: E402
from src.copilot import llm as L                                     # noqa: E402
from src.copilot import service as C                                 # noqa: E402
from src.copilot import store as St                                  # noqa: E402
from src.copilot.packet import PACKET_VERSION, build_packet          # noqa: E402

ENGINE = create_engine(_DB_URL)
Base.metadata.create_all(ENGINE)
Session = sessionmaker(bind=ENGINE)
NOW = datetime(2026, 10, 9, 21, 0)
R = {}
SYM = "TESTCO"


def reset(*, bar_age_days=0, bars=260, last_close=None, split_at=None, earnings_in=None,
          trend=False,
          cohort=(("SWING", "BUY", 5, 40, 24),), own=(("SWING", "BUY", 5, 7, 5),),
          verified=0, late_row=False, old_row=False):
    """Fixture: one stock, 260 hand-computable bars, calls on SWING (fresh BUY) and LONG
    (50h-old SELL), no SHORT/GROWTH calls, and outcome rows per (horizon, dir, window, n, hits)."""
    with Session() as s:
        for t in ("copilot_usage_day", "copilot_messages", "copilot_conversations",
                  "signal_outcome_horizons", "earnings_events", "signals", "prices", "stocks",
                  "users"):
            s.execute(text(f"DELETE FROM {t}"))
        s.add_all([User(id=1, username="alice", password_hash="x")])
        st = Stock(symbol=SYM, name="Test Co", market=Market.US, exchange=Exchange.NASDAQ,
                   sector="Technology", currency="USD")
        other = Stock(symbol="OTHER", name="Other", market=Market.US, exchange=Exchange.NASDAQ,
                      sector="Technology", currency="USD")
        s.add_all([st, other]); s.flush()
        for i in range(bars):
            px = 100.0 + ((bars - i) * 0.5 if trend else 0.0)
            if split_at is not None and i > split_at:
                px = 200.0                       # before the split, unadjusted closes doubled
            hi, lo, cl = px + 1, px - 1, px
            if i == 0 and last_close is not None:
                cl = last_close
                hi, lo = max(hi, cl), min(lo, cl)
            s.add(Price(id=i + 1, stock_id=st.id, ts=NOW - timedelta(days=i + bar_age_days),
                        timeframe=TimeFrame.D1, open=px, high=hi, low=lo, close=cl,
                        volume=1_000_000))
        s.add(Signal(id=1, stock_id=st.id, ts=NOW - timedelta(hours=3), signal=SignalType.BUY,
                     horizon=SignalHorizon.SWING, confidence=61.0, source="signal-engine"))
        s.add(Signal(id=2, stock_id=st.id, ts=NOW - timedelta(hours=50), signal=SignalType.SELL,
                     horizon=SignalHorizon.LONG, confidence=55.0, source="signal-engine"))
        if earnings_in is not None:
            s.add(EarningsEvent(id=1, stock_id=st.id,
                                report_date=(NOW + timedelta(days=earnings_in)).date()))
        rid = [0]

        def outcome(stock, h, d, w, ok, ret, status=None, resolved=NOW - timedelta(hours=1),
                    sdate=NOW.date() - timedelta(days=20)):
            rid[0] += 1
            # Its parent signal: PostgreSQL enforces the foreign key that SQLite does not.
            s.add(Signal(id=10_000 + rid[0], stock_id=stock.id,
                         ts=datetime.combine(sdate, datetime.min.time()) - timedelta(days=1),
                         signal=SignalType[d], horizon=SignalHorizon[h], confidence=50.0,
                         source="signal-engine"))
            s.flush()
            s.add(SignalOutcomeHorizon(
                id=rid[0], signal_id=10_000 + rid[0], stock_id=stock.id, symbol=stock.symbol,
                horizon=h, signal_direction=d, signal_date=sdate, window_days=w,
                entry_date=sdate, entry_price=100.0, target_date=sdate + timedelta(days=w),
                exit_date=sdate + timedelta(days=w), exit_price=100 * (1 + ret), pct_return=ret,
                is_correct=ok, status="resolved", resolved_at=resolved,
                evidence_status=status))
        v = verified
        for h, d, w, n, hits in cohort:
            for k in range(n):
                ok = k < hits
                ret = (0.02 if ok else -0.01) if d == "BUY" else (-0.02 if ok else 0.01)
                st_ = "verified" if v > 0 else None
                v -= 1
                outcome(other, h, d, w, ok, ret, status=st_)
        for h, d, w, n, hits in own:
            for k in range(n):
                ok = k < hits
                outcome(st, h, d, w, ok, 0.03 if ok else -0.02)
        if late_row:           # resolved AFTER now: must not be visible (look-ahead)
            outcome(other, "SWING", "BUY", 5, True, 0.5, resolved=NOW + timedelta(hours=2))
        if old_row:            # signal older than the 180-day lookback
            outcome(other, "SWING", "BUY", 5, True, 0.5,
                    sdate=NOW.date() - timedelta(days=400))
        s.commit()


def pkt(question=None, **pos):
    pi = I.parse_position(question) if question is not None else I.PositionIntent(**pos)
    with Session() as s:
        return build_packet(s, SYM, now=NOW, plan_request=pi if pi.kind else None)


def disp(p, key):
    q = p.quantities.get(key)
    return None if q is None else q.display


class ScriptedModel:
    def __init__(self, *drafts):
        self.drafts = list(drafts)
        self.calls = []

    async def __call__(self, provider, api_key, model, messages, system=None):
        self.calls.append(messages)
        d = self.drafts.pop(0)
        return L.LlmReply(text=d, model="scripted",
                          usage={"input_tokens": 1000, "output_tokens": 100}, http_status=200)


def turn(question, rid):
    return C.TurnRequest(username="alice", symbol=SYM, question=question, request_id=rid,
                         conversation_id=None, provider="claude", model="m", api_key="k",
                         uses_shared_key=False)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Scenarios ────────────────────────────────────────────────────────────────

def pl1_entry_arithmetic():
    reset()
    p = pkt("Should I buy? I can risk $500")
    keys = sorted(p.quantities)
    R["pl1"] = {
        "kind": p.plan.kind, "packet_version": PACKET_VERSION,
        "atr": disp(p, "level.atr"), "low20": disp(p, "level.low20"),
        "high20": disp(p, "level.high20"), "sma50": disp(p, "level.sma50"),
        "e1": disp(p, "rule.e1"), "e2": disp(p, "rule.e2"), "e3": disp(p, "rule.e3"),
        "e4": disp(p, "rule.e4"), "e6": disp(p, "rule.e6"),
        "holding_rules": [k for k in keys if k.startswith("rule.h")],
        "moves5": disp(p, "moves.5b"),
        "every_ref_resolves": all(e in p.book.records for q in p.quantities.values()
                                  for e in q.evidence_ids),
        "every_field_ref_resolves": all(e in p.book.records for f in p.fields.values()
                                        for e in f.evidence_ids),
        "atr_cites_inputs": len(p.quantities["level.atr"].evidence_ids),
        "atr_calc": p.book.records[p.quantities["level.atr"].evidence_ids[0]]["value"]["result"],
    }
    # Sizing: account and percentage; percentage with no account is refused.
    p2 = pkt("account 50,000 risk 1%, should I buy?")
    p3 = pkt("risk 1%, should I buy?")
    p4 = pkt("should I buy?")
    R["pl1"]["e6_pct"] = disp(p2, "rule.e6")
    R["pl1"]["e6_pct_no_account"] = disp(p3, "rule.e6")
    R["pl1"]["pct_no_account_refusal"] = (p3.fields.get("plan_position_input").reason
                                          if p3.fields.get("plan_position_input") else None)
    R["pl1"]["e6_no_budget"] = disp(p4, "rule.e6")
    # A reward:risk that meets the minimum: close nearer the low, high far above.
    reset(last_close=99.2)
    p5 = pkt("should I buy?")
    R["pl1"]["e3_low_close"] = disp(p5, "rule.e3")
    reset(last_close=101.5)
    R["pl1"]["e3_at_high"] = disp(pkt("should I buy?"), "rule.e3")
    reset(last_close=97.0)
    R["pl1"]["e3_broken"] = disp(pkt("should I buy?"), "rule.e3")
    reset(last_close=96.0)
    R["pl1"]["e3_broken_96"] = disp(pkt("should I buy?"), "rule.e3")


def pl2_holding_arithmetic():
    reset()
    p = pkt("I bought 100 shares at 95. What should I do next?")
    R["pl2"] = {k: disp(p, k) for k in ("position.entry", "position.shares", "rule.h1",
                                        "rule.h2", "rule.h3", "rule.h4", "rule.h5", "rule.h6",
                                        "rule.h8", "rule.e1")}
    R["pl2"]["kind"] = p.plan.kind
    pos_ev = p.quantities["position.entry"].evidence_ids[0]
    R["pl2"]["position_source"] = p.book.records[pos_ev]["source"]
    # Entry above the close: a loss, the stop below entry; breakeven not reached.
    p2 = pkt("I bought at 104, 10 shares")
    R["pl2"]["loss_h1"] = disp(p2, "rule.h1")
    R["pl2"]["loss_h2"] = disp(p2, "rule.h2")
    R["pl2"]["loss_h3"] = disp(p2, "rule.h3")
    # Trailing stop that DOES rise: entry far below, close well above it.
    p3 = pkt("I own 10 shares, avg cost 10")
    R["pl2"]["far_entry_refused"] = (p3.fields["plan_position_input"].reason
                                     if "plan_position_input" in p3.fields else None)
    R["pl2"]["far_entry_has_h"] = any(k.startswith("rule.h") for k in p3.quantities)
    # Rising fixture: close 230, ATR 2, prior-20 low 219 -> structure stop 218; entry 200 ->
    # breakeven reached, trailing stop 230 - 4 = 226 rises above 218.
    reset(trend=True)
    p4 = pkt("I own 10 shares, avg cost 200")
    R["pl2"]["trend_low20"] = disp(p4, "level.low20")
    R["pl2"]["trail_h4"] = disp(p4, "rule.h4")
    R["pl2"]["trail_h6"] = disp(p4, "rule.h6")
    R["pl2"]["trend_h5"] = disp(p4, "rule.h5")
    reset()
    # No entry stated: holding rules unavailable with a reason; entry rules still present.
    p5 = pkt("Should I sell?")
    R["pl2"]["no_entry_kind"] = p5.plan.kind
    R["pl2"]["no_entry_has_h"] = any(k.startswith("rule.h") for k in p5.quantities)
    R["pl2"]["no_entry_gap"] = p5.fields["plan_gaps"].reason if "plan_gaps" in p5.fields else None
    R["pl2"]["no_entry_refusal"] = (p5.fields["plan_position_input"].reason
                                    if "plan_position_input" in p5.fields else None)


def pl3_records_and_floors():
    out = {}
    reset()          # cohort 40 (24 correct), own 7
    p = pkt("what's the outlook for next week?")
    out["kind"] = p.plan.kind
    out["call_swing"] = disp(p, "call.swing")
    out["call_long"] = disp(p, "call.long")
    out["call_long_state"] = p.quantities["call.long"].state
    out["call_swing_state"] = p.quantities["call.swing"].state
    out["record"] = disp(p, "record.swing.5d")
    out["record_10d"] = disp(p, "record.swing.10d")
    out["own"] = disp(p, "record.swing.5d.stock")
    out["baseline"] = disp(p, "baseline.swing.5d")
    out["no_rules_for_forecast"] = not any(k.startswith("rule.") for k in p.quantities)
    out["gaps"] = p.fields["plan_gaps"].reason if "plan_gaps" in p.fields else None
    out["long_record"] = disp(p, "record.long.5d")
    # Floors: 29 vs 30 in the cohort, 7 vs 8 for the stock.
    reset(cohort=(("SWING", "BUY", 5, 29, 10),), own=())
    out["cohort_29"] = disp(pkt("forecast?"), "record.swing.5d")
    reset(cohort=(("SWING", "BUY", 5, 22, 10),), own=(("SWING", "BUY", 5, 8, 6),))
    p = pkt("forecast?")
    out["cohort_22_plus_own_8"] = disp(p, "record.swing.5d")
    out["own_8"] = disp(p, "record.swing.5d.stock")
    reset(cohort=(("SWING", "BUY", 5, 30, 10),), own=())
    out["cohort_30"] = disp(pkt("forecast?"), "record.swing.5d")
    # Evidence status is computed: 3 verified of 40.
    reset(verified=3)
    out["verified_3"] = disp(pkt("forecast?"), "record.swing.5d")
    # Look-ahead and lookback: a row resolved after NOW, and one older than 180 days, are not
    # counted (40 cohort rows + 7 own = 47 in the baseline either way).
    reset(late_row=True, old_row=True)
    out["with_late_and_old"] = disp(pkt("forecast?"), "record.swing.5d")
    R["pl3"] = out


def pl4_staleness():
    out = {}
    reset(bar_age_days=9)
    p = pkt("should I buy?")
    out["price_stale_rule_state"] = p.quantities["rule.e1"].state
    out["price_stale_level_state"] = p.quantities["level.atr"].state
    out["call_swing_state_when_price_stale"] = p.quantities["call.swing"].state
    out["rendered"] = G.render("{{rule.e1}}", p)
    reset(split_at=100)
    p = pkt("should I buy?")
    out["split_state"] = p.quantities["level.low20"].state
    out["split_note"] = p.quantities["level.low20"].note
    reset(bars=12)
    p = pkt("should I buy?")
    out["thin_gap"] = p.fields["plan_gaps"].reason if "plan_gaps" in p.fields else None
    out["thin_has_rules"] = any(k.startswith("rule.") for k in p.quantities)
    reset(earnings_in=4)
    out["earnings_near"] = disp(pkt("should I buy?"), "rule.e5")
    out["earnings_near_holding"] = disp(pkt("I bought 10 shares at 98"), "rule.h7")
    reset(earnings_in=30)
    out["earnings_far"] = disp(pkt("should I buy?"), "rule.e5")
    R["pl4"] = out


def pl5_routing_and_refusals():
    reset()
    out = {}
    base = pkt(None, kind=None)
    p = build_packet(Session(), SYM, now=NOW)
    out["plain_has_plan_keys"] = any(k.split(".")[0] in ("rule", "call", "record", "level",
                                                         "moves", "baseline")
                                     for k in p.quantities)
    out["plain_playbook_note"] = p.model_view().get("playbook")
    out["plain_plan_kind"] = p.model_view().get("plan_kind")
    out["plain_limitations_mention_playbook"] = any("playbook" in (m["reason"] or "").lower()
                                                    for m in p.missing())
    for label, q in (("options", "I bought calls at 5, what now?"),
                     ("short", "I shorted it at 120"),
                     ("two_entries", "I bought 50 at 98 and 50 at 102"),
                     ("incomplete", "I bought at 1,00")):
        pp = pkt(q)
        out[label] = {"kind": pp.plan.kind if pp.plan else None,
                      "has_h": any(k.startswith("rule.h") for k in pp.quantities),
                      "refusal": (pp.fields["plan_position_input"].reason
                                  if "plan_position_input" in pp.fields else None)}
    R["pl5"] = out
    del base


GOOD_PLAN = ("<answer>\nThe platform's SWING call is {{call.swing}}; across all stocks, "
             "{{record.swing.5d}}. For this stock alone: {{record.swing.5d.stock}}. Over 5 "
             "calendar days the stock's own past moves were {{moves.5b}}.\n"
             "For your position: {{rule.h1}} {{rule.h2}}\n</answer>\n"
             "<limitation>None of these outcomes is verified.</limitation>")
PREDICT = ("<answer>\nThe stock will rise next week toward {{level.high20}}.\n</answer>")
FIVE_DAYS = ("<answer>\nOver 5 days the stock moved {{moves.5b}}.\n</answer>")


def pl6_turns():
    out = {}
    reset()
    m = ScriptedModel(GOOD_PLAN)
    r = run(C.answer(turn("I bought 100 shares at 95. What should I do next week?", "plan-0001"),
                     session_factory=Session, llm_call=m, now=NOW))
    msg = r["message"]
    out["good_status"] = msg["status"]
    out["good_content"] = msg["content"]
    out["good_refs"] = sorted(x["key"] for x in msg["evidence_refs"])
    with Session() as s_:
        g = s_.execute(text("SELECT grounding FROM copilot_messages WHERE request_id='plan-0001' "
                            "AND role='assistant'")).scalar()
    g = json.loads(g) if isinstance(g, str) else g
    out["good_packet_version"] = g.get("packet_version")
    out["model_saw_rules"] = "rule.h2" in m.calls[0][-1]["content"]
    # STORAGE: each evidence record once per message; the API still serves preview + count, and
    # the detail endpoint the full list (measured before: 416 KB, 948 entries for one answer).
    with Session() as s_:
        row = s_.query(St.CopilotMessage).filter_by(request_id="plan-0001",
                                                     role="assistant").one()
        g_ = row.grounding if isinstance(row.grounding, dict) else json.loads(row.grounding)
        out["stored_bytes"] = len(json.dumps(g_, default=str))
        out["stored_records"] = len(g_.get("evidence_records") or {})
        out["refs_hold_ids"] = all("evidence_ids" in r and "evidence" not in r
                                   for r in g_["evidence_refs"])
        h2 = next(x for x in msg["evidence_refs"] if x["key"] == "rule.h2")
        out["api_preview"] = (len(h2["evidence"]), h2["evidence_count"])
        full = St.message_evidence(s_, row.user_id, row.id, "rule.h2")
        out["detail_full"] = len(full["evidence"])
        out["detail_ids_unique"] = len({e["id"] for e in full["evidence"]}) == len(full["evidence"])
        # A message stored in the OLD inline format still serves.
        legacy = St.CopilotMessage(conversation_id=row.conversation_id, user_id=row.user_id,
                                   role="assistant", content="x", request_id="legacy-0001",
                                   status="grounded", created_at=NOW,
                                   grounding={"evidence_refs": [{"key": "price.close",
                                       "evidence": [{"id": f"e{i}"} for i in range(7)]}]})
        s_.add(legacy); s_.commit()
        ls = St.serialise_message(legacy)["evidence_refs"][0]
        out["legacy_preview"] = (len(ls["evidence"]), ls["evidence_count"])
        out["legacy_detail"] = len(St.message_evidence(s_, row.user_id, legacy.id,
                                                       "price.close")["evidence"])
    m2 = ScriptedModel(PREDICT, PREDICT)
    r2 = run(C.answer(turn("Where will it go next week?", "plan-0002"),
                      session_factory=Session, llm_call=m2, now=NOW))
    out["predict_status"] = r2["message"]["status"]
    out["predict_reasons"] = r2["message"].get("rejection_reasons")
    out["predict_fallback_has_plan"] = "Plan (calculated by the platform" in r2["message"]["content"]
    out["predict_fallback_refs"] = sorted(x["key"] for x in r2["message"]["evidence_refs"])
    m3 = ScriptedModel(FIVE_DAYS, FIVE_DAYS)
    r3 = run(C.answer(turn("forecast for next week?", "plan-0003"), session_factory=Session,
                      llm_call=m3, now=NOW))
    out["five_days_status"] = r3["message"]["status"]
    out["five_days_reasons"] = r3["message"].get("rejection_reasons")
    # Narrow exemptions from the held-out run, each beside the case that must stay refused.
    reset()
    pf = pkt("I bought 50 at 98 and 50 at 102. Forecast?")
    def ok(answer):
        return G.check({"answer": answer, "limitations": []}, pf).accepted
    out["exempt"] = {
        "label_beside_record": ok("SWING BUY, 5-day: {{record.swing.5d}}."),
        "words_beside_record": ok("Over five days: {{record.swing.5d}}."),
        "calendar_beside_stock_record": ok("(10 calendar days) {{record.swing.10d.stock}}"),
        "baseline_label": ok("Baseline, 5-day: {{baseline.swing.5d}}."),
        "mismatched_window": ok("5-day: {{record.swing.10d}}."),
        "bar_return_called_days": ok("The 5-day return is {{return.5b}}."),
        "days_without_record": ok("Over five days the stock moved {{moves.5b}}."),
        "other_figure_beside_record": ok("SWING BUY, 5-day, 12 calls: {{record.swing.5d}}."),
        "quoted_example": ok('Restate it, e.g. "I bought shares at your average cost".'),
        "unquoted_first_person": ok("I bought the shares for you."),
        "position_note_citable": ok("Your entry could not be used: {{plan.position_note}}"),
        "gaps_citable": ok("Not built: {{plan.gaps}}"),
    }
    R["pl6"] = out


def pl7_plan_view():
    reset()
    out = {}
    v = C.plan_view(Session, "alice", SYM, entry=95, shares=100, now=NOW)
    out["kind"] = v["plan"]["kind"]
    out["groups"] = sorted(v["plan"]["groups"])
    out["rule_keys"] = [x["key"] for x in v["plan"]["groups"]["rules"]]
    out["no_price_records_inline"] = not any(k.startswith("price:") for k in v["evidence"])
    out["price_bar_records"] = v["price_bar_records"]
    out["parameters_note"] = v["plan"]["parameters"]["note"]
    with Session() as s:
        out["usage_rows"] = s.execute(text("SELECT count(*) FROM copilot_usage_day")).scalar()
    errs = {}
    for label, kw in (("neg_entry", {"entry": -1}), ("pct_no_account", {"risk_pct": 1}),
                      ("bad_kind", {"kind": "short"}), ("zero_shares", {"shares": 0}),
                      ("nan_risk", {"risk": float("nan")})):
        try:
            C.plan_view(Session, "alice", SYM, now=NOW, **kw)
            errs[label] = None
        except C.CopilotError as e:
            errs[label] = e.status
    for label, (user, sym) in (("unknown_user", ("mallory", SYM)), ("unknown_symbol", ("alice", "NOPE"))):
        try:
            C.plan_view(Session, user, sym, now=NOW)
            errs[label] = None
        except C.CopilotError as e:
            errs[label] = e.status
    out["errors"] = errs
    R["pl7"] = out


def pl8_tables():
    """Grammar and forecast-language tables, through the real modules."""
    grammar = {}
    for q in ("I bought 100 shares at 182.50, what now?", "bought 100 at $182.5",
              "Bought 100 at 182.5. What next?", "I own it, avg cost 1,250.75 and 30 shares",
              "I have 200 shares, cost basis 45.10", "I bought at 180, stop at 170?",
              "I bought at 180 at 9:30 today", "I bought 50 at 10 and 50 at 12",
              "I bought at 1,00", "I bought at 182.5k", "should I buy? risk $500",
              "account 50,000 risk 1% should I buy", "risk 1% should I buy",
              "I can lose 300 dollars, where to buy", "what is the prediction for next week",
              "I bought calls at 5", "I shorted it at 120", "I don't own it, should I buy",
              "Explain this signal", "Alert me if it falls below 170",
              "Should I sell? I bought at 90", "What is the K-Score?"):
        pi = I.parse_position(q)
        grammar[q] = {"kind": pi.kind, "entry": pi.entry, "shares": pi.shares,
                      "risk_amount": pi.risk_amount, "account": pi.account,
                      "risk_pct": pi.risk_pct, "refused": bool(pi.refusals)}
    forecast = {t: G._unconditional_forecasts(t) for t in (
        "The stock will rise next week.", "If a close is above the high, it will likely climb.",
        "The data does not say the price will rise.", "Nothing here shows it is going to fall.",
        "It should bounce from support.", "The engine is set to recalculate tomorrow.",
        "Once it reaches the 20-bar high, take partial profit.",
        "The stop will trigger if a close is below it.", "It is expected to reach the high.",
        "Past BUY calls rose in most cases.", "Shares are poised to rally.",
        "The price will go up.", "Whether it will rise is not something the packet shows.")}
    R["pl8"] = {"grammar": grammar, "forecast": forecast}


for fn in (pl8_tables, pl1_entry_arithmetic, pl2_holding_arithmetic, pl3_records_and_floors,
           pl4_staleness, pl5_routing_and_refusals, pl6_turns, pl7_plan_view):
    fn()
R["_meta"] = {"server": ENGINE.dialect.name}
print(json.dumps(R, default=str))
