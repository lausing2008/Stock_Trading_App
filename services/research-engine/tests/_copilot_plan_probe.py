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
          trend=False, last_bar=None,
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
            if i == 0 and last_bar is not None:
                hi, lo, cl = last_bar
            elif i == 0 and last_close is not None:
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


def pl9_review_c1_c5():
    """The independent review's reproductions (2026-10-15), through the real packet/grounding."""
    out = {}
    # C1 — stops across two snapshots of the same position, and a typed stop.
    reset(trend=True)
    a = pkt("I own 10 shares, avg cost 200")
    out["c1_first_h6"] = disp(a, "rule.h6")
    reset(trend=True, last_bar=(226.0, 224.0, 225.0))
    b = pkt("I own 10 shares, avg cost 200")
    out["c1_second_h6"] = disp(b, "rule.h6")
    out["c1_second_h4"] = disp(b, "rule.h4")
    c = pkt("I own 10 shares, avg cost 200, my stop is 226")
    out["c1_typed_kept_h6"] = disp(c, "rule.h6")
    out["c1_typed_stop_shown"] = disp(c, "position.stop")
    reset(trend=True)
    d = pkt("I own 10 shares, avg cost 200, my stop is 218")
    out["c1_typed_raised_h6"] = disp(d, "rule.h6")
    out["c1_any_maintains_claim"] = any(
        "active stop" in (q.display or "") for p_ in (a, b) for q in p_.quantities.values())
    # C2 — options positions get no stock rules at all, packet or fallback.
    reset()
    for label, q in (("c2_aapl_calls", "I bought 2 AAPL January calls at 50. What should I do?"),
                     ("c2_calls", "I bought calls at 5, what now?"),
                     ("c2_150c", "I bought the 150c at 3"),
                     ("c2_covered", "I hold 100 shares at 95 and sold a covered call")):
        p_ = pkt(q)
        out[label] = {"rules": sorted(k for k in p_.quantities if k.startswith("rule.")),
                      "unsupported": p_.plan.unsupported,
                      "fallback_rules": [k for k in G.fallback_keys(p_) if k.startswith("rule.")],
                      "calls_kept": "call.swing" in p_.quantities}
    # Same SENTENCE as the position verb, so the exclusion is what decides it.
    p_ = pkt("I bought 100 shares at 95, are the SWING calls still BUY?")
    out["c2_platform_calls_ok"] = any(k.startswith("rule.h") for k in p_.quantities)
    p_ = pkt("I own 100 shares at 95 and the platform's calls are BUY, what now?")
    out["c2_platform_calls_ok2"] = any(k.startswith("rule.h") for k in p_.quantities)
    # C3 — whole amounts through the real sizing path (stop 98, close 100: $2 a share).
    for label, q in (("c3_10k", "I want to buy, risk $10k"), ("c3_1_5k", "risk $1.5k, should I buy?"),
                     ("c3_bad", "risk $10,00, should I buy?"), ("c3_lose_2k", "I can lose 2k, where to buy?"),
                     ("c3_500", "should I buy? risk $500"),
                     ("c3_glued", "risk $5bn, should I buy?")):
        p_ = pkt(q)
        out[label] = {"e6": disp(p_, "rule.e6"),
                      "refusal": (p_.fields["plan_position_input"].reason
                                  if "plan_position_input" in p_.fields else None)}
    # C4 — through G.check, with a valid citation present.
    fp = pkt("forecast?")
    def chk(t):
        return G.check({"answer": t + " {{call.swing}}", "limitations": []}, fp).accepted
    out["c4"] = {t: chk(t) for t in (
        "If you are wondering about next week, the stock will rise.",
        "When considering the evidence, it will rally.",
        "If you ask me the stock will rise.",
        "Whether or not you agree, it will rise.",
        "It will not fall.",
        "I cannot say whether it will rise.",
        "The data does not say the price will rise.")}
    # C5 — targets against the typed entry, packet and endpoint.
    reset()
    out["c5_loss_h5"] = disp(pkt("I bought 10 shares at 104"), "rule.h5")
    out["c5_gain_h5"] = disp(pkt("I bought 10 shares at 95"), "rule.h5")
    out["c5_even_h5"] = disp(pkt("I bought 10 shares at 101"), "rule.h5")
    v = C.plan_view(Session, "alice", SYM, entry=104, shares=10, now=NOW)
    out["c5_endpoint_h5"] = next(x["display"] for x in v["plan"]["groups"]["rules"]
                                 if x["key"] == "rule.h5")
    v2 = C.plan_view(Session, "alice", SYM, entry=95, shares=10, stop=99.5, now=NOW)
    out["endpoint_stop_h6"] = next(x["display"] for x in v2["plan"]["groups"]["rules"]
                                   if x["key"] == "rule.h6")
    try:
        C.plan_view(Session, "alice", SYM, stop=99.5, now=NOW)
        out["endpoint_stop_without_entry"] = None
    except C.CopilotError as e:
        out["endpoint_stop_without_entry"] = e.status
    R["pl9"] = out


def pl10_review_residuals():
    reset()
    out = {"options": [], "amounts": {}, "forecasts": {}}
    for question in ("I have 2 AAPL January calls at 50. What should I do?",
                     "I bought long calls at 50. What should I do?",
                     "I bought LONG calls at 50. What should I do?"):
        p = pkt(question)
        out["options"].append({"unsupported": p.plan.unsupported,
                               "rules": [k for k in p.quantities if k.startswith("rule.")],
                               "fallback": [k for k in G.fallback_keys(p) if k.startswith("rule.")]})
    for question in ("I want to buy. I can lose 10%",
                     "I want to buy. I can lose 10%, account 50000",
                     "I want to buy, risk $1.2.3k",
                     "I want to buy, risk $1...",
                     "I want to buy, risk $10kfoo",
                     "I want to buy, risk $500, actually risk $1000",
                     "I want to buy, risk $500 and I can lose $1000",
                     "I bought 10 shares at 95, my stop is 5%",
                     "I bought 10 shares at 95, my stop is 99.5",
                     "I want to buy, account 50000, risk $500, risk $500"):
        p = pkt(question)
        it = I.parse_position(question)
        out["amounts"][question] = {"risk": it.risk_amount, "percent": it.risk_pct,
                                    "stop": it.stop, "refused": bool(it.refusals),
                                    "sizing": disp(p, "rule.e6"), "exit": disp(p, "rule.h6")}
    p = pkt("forecast?")
    for text_ in ("There is no doubt the stock will rise.",
                  "I do not own shares and the stock will rise.",
                  "I cannot say whether it will rise."):
        out["forecasts"][text_] = G.check(
            {"answer": text_ + " {{call.swing}}", "limitations": []}, p).accepted
    # Exercise the real retry and deterministic fallback, not just the checker.
    for i, text_ in enumerate(("There is no doubt the stock will rise.",
                               "I do not own shares and the stock will rise.")):
        raw = "<answer>" + text_ + " {{call.swing}}</answer>"
        model = ScriptedModel(raw, raw)
        msg = run(C.answer(turn("forecast for next week?", f"residual-{i:04d}"),
                           session_factory=Session, llm_call=model, now=NOW))["message"]
        out[f"fallback_{i}"] = {"status": msg["status"], "calls": len(model.calls),
                                  "has_plan": "Plan (calculated by the platform" in msg["content"]}
    R["pl10"] = out



def pl11_card_and_drafts():
    reset()
    out = {}
    view = C.plan_view(Session, "alice", SYM, kind="entry", include_bars=True, now=NOW)
    out["numeric"] = {x["key"]: x["price"] for x in view["plan"]["levels"]}
    out["all_evidence"] = all(e in view["evidence"] for items in view["plan"]["groups"].values()
                              for item in items for e in item["evidence_ids"])
    class Market:
        def __init__(self): self.rows, self.keys = [], {}
        async def alerts(self): return list(self.rows)
        async def create_alert(self, body):
            key = body["idempotency_key"]
            if key not in self.keys:
                self.keys[key] = {"id": len(self.keys) + 1, **body}
                self.rows.append(self.keys[key])
            return self.keys[key]
    market = Market()
    args = dict(level_key="level.pullback_stop", fingerprint=view["packet_fingerprint"],
                request_id="card-draft-0001", market=market, kind="entry", now=NOW)
    first = run(C.plan_level_draft(Session, "alice", SYM, **args))
    replay = run(C.plan_level_draft(Session, "alice", SYM, **args))
    out["draft"] = first["message"]["drafts"][0]
    out["no_write_before_confirm"] = len(market.rows) == 0
    out["replayed"] = replay["replayed"] and first["message"]["id"] == replay["message"]["id"]
    with Session() as ss: out["answer_quota"] = St.answers_today(ss, 1, NOW)
    def status(**overrides):
        try:
            run(C.plan_level_draft(Session, "alice", SYM, **{**args, **overrides}))
            return None
        except C.CopilotError as exc: return exc.status
    out["replay_conflict"] = status(level_key="level.high20")
    out["changed_plan"] = status(request_id="card-draft-0002", fingerprint="0" * 24)
    out["wrong_key"] = status(request_id="card-draft-0003", level_key="rule.e1")
    confirm = dict(message_id=first["message"]["id"], draft_id=out["draft"]["id"], market=market,
                   now=NOW, readiness=lambda: ("ready", "probe schema exists"))
    run(C.confirm_draft(Session, "alice", **confirm))
    run(C.confirm_draft(Session, "alice", **confirm))
    out["saved_once"] = len(market.rows) == 1
    try:
        run(C.confirm_draft(Session, "nobody", **confirm)); out["owner"] = None
    except C.CopilotError as exc: out["owner"] = exc.status
    exp = run(C.plan_level_draft(Session, "alice", SYM, **{**args, "request_id": "card-expire-0001"}))
    try:
        run(C.confirm_draft(Session, "alice", message_id=exp["message"]["id"],
            draft_id=exp["message"]["drafts"][0]["id"], market=market,
            now=NOW+timedelta(minutes=20), readiness=lambda: ("ready", "probe schema exists")))
        out["expired"] = None
    except C.CopilotError as exc: out["expired"] = exc.status
    late = NOW + timedelta(days=10)
    old = C.plan_view(Session, "alice", SYM, kind="entry", now=late)
    out["stale"] = status(request_id="card-stale-0001", fingerprint=old["packet_fingerprint"], now=late)
    R["pl11"] = out

def pl12_dual_plan():
    reset()
    inputs = dict(kind="holding", entry=95.0, shares=10, stop=97.0, risk=400.0)
    view = C.plan_view(Session, "alice", SYM, now=NOW, **inputs)
    context = {**inputs, "fingerprint": view["packet_fingerprint"]}
    model = ScriptedModel('<answer>One reading favors protecting the held position: {{rule.h1}} {{rule.h6}} {{setup.holding}}</answer><plan_setup id="holding"/>')
    req = turn("Compare alternative scenarios", "dual-plan-0001")
    req.plan_context = context
    first = run(C.answer(req, session_factory=Session, llm_call=model, now=NOW))
    replay = run(C.answer(req, session_factory=Session, llm_call=model, now=NOW))
    out = dict(status=first["message"]["status"], same_snapshot=first["message"]["packet_fingerprint"] == view["packet_fingerprint"],
               held_stop="$97.00" in first["message"]["content"], context_stored=first["message"]["plan_context"] == context, model=first["message"]["model"],
               replay=replay["replayed"], calls=len(model.calls))
    def denied(rid, ctx):
        bad = turn("Compare alternative scenarios", rid); bad.plan_context = ctx
        try:
            run(C.answer(bad, session_factory=Session, llm_call=model, now=NOW))
            return None
        except C.CopilotError as exc: return exc.status
    out["changed_replay"] = denied(req.request_id, {**context, "stop": 96.0})
    out["changed_snapshot"] = denied("dual-plan-0002", {**context, "fingerprint": "0"*24})
    out["invalid_risk"] = denied("dual-plan-0003", {**context, "risk": -1.0})
    derivative = turn("I bought call options at 5, what now?", "dual-plan-options")
    derivative.plan_context = context
    try:
        run(C.answer(derivative, session_factory=Session, llm_call=model, now=NOW))
        out["options"] = None
    except C.CopilotError as exc: out["options"] = exc.status
    later = NOW + timedelta(days=20)
    old = C.plan_view(Session, "alice", SYM, now=later, **inputs)
    stale_req = turn("Compare alternative scenarios", "dual-plan-stale")
    stale_req.plan_context = {**inputs, "fingerprint": old["packet_fingerprint"]}
    try:
        run(C.answer(stale_req, session_factory=Session, llm_call=model, now=later))
        out["stale"] = None
    except C.CopilotError as exc: out["stale"] = exc.status
    with Session() as ss: out["quota_after_denials"] = St.answers_today(ss, 1, NOW)
    bad = turn("Compare alternative scenarios", "dual-plan-0004"); bad.plan_context = context
    rejected = run(C.answer(bad, session_factory=Session, llm_call=ScriptedModel(PREDICT,PREDICT), now=NOW))
    out["fallback_status"] = rejected["message"]["status"]
    out["fallback_same_snapshot"] = rejected["message"]["packet_fingerprint"] == view["packet_fingerprint"]
    class NoActions:
        async def alerts(self): raise AssertionError("comparison reached alert settings")
        async def create_alert(self, *args): raise AssertionError("comparison wrote an alert")
    draft_req = turn("Compare alternative scenarios", "dual-plan-0005")
    draft_req.plan_context = context; draft_req.market = NoActions()
    attempt = ScriptedModel('<answer>One reading favors waiting: {{rule.h1}}</answer><plan_setup id="wait"/><draft kind="price_alert" condition="below" threshold="{{price.close}}"/>')
    ignored = run(C.answer(draft_req, session_factory=Session, llm_call=attempt, now=NOW))
    out["drafts"] = ignored["message"]["drafts"]
    R["pl12"] = out


def pl13_trade_setups():
    reset()
    entry = C.plan_view(Session, "alice", SYM, kind="entry", risk=400, now=NOW)
    out = {"entry": entry["plan"]["setups"], "target_prices": {x["key"]:x["price"] for x in entry["plan"]["levels"] if x["role"]=="target"}}
    out["held"] = C.plan_view(Session, "alice", SYM, kind="holding", entry=95, shares=10, stop=97, now=NOW)["plan"]["setups"]
    out["underwater"] = C.plan_view(Session, "alice", SYM, kind="holding", entry=104, shares=10, now=NOW)["plan"]["setups"]
    out["tiny_budget"] = C.plan_view(Session, "alice", SYM, kind="entry", risk=.5, now=NOW)["plan"]["setups"]
    other = C.plan_view(Session, "alice", SYM, kind="entry", risk=400.1, now=NOW)
    out["precision_bound"] = entry["packet_fingerprint"] != other["packet_fingerprint"]
    class ReadOnlyMarket:
        async def alerts(self): return []
    target_draft = run(C.plan_level_draft(Session, "alice", SYM, kind="entry", risk=400,
        fingerprint=entry["packet_fingerprint"], request_id="setup-target-draft", market=ReadOnlyMarket(),
        level_key="level.breakout_target1", now=NOW))
    out["target_draft"] = target_draft["message"]["drafts"][0]
    context = dict(kind="entry", risk=400, fingerprint=entry["packet_fingerprint"])
    req = turn("Compare detailed scenarios", "setups-select-0001"); req.plan_context = context
    # The first answer selects breakout but cites pullback. Repair must bind the chosen setup.
    wrong = '<answer>One reading favors this setup: {{setup.pullback}}</answer><plan_setup id="breakout"/>'
    good = '<answer>One reading favors the breakout alternative: {{setup.breakout}}</answer><plan_setup id="breakout"/>'
    model = ScriptedModel(wrong,good)
    response = run(C.answer(req, session_factory=Session, llm_call=model, now=NOW))["message"]
    out["selected"] = {"id":response["selected_setup_id"], "status":response["status"], "calls":len(model.calls), "setups":response["plan_setups"]}
    req2 = turn("Compare detailed scenarios", "setups-missing-0001"); req2.plan_context = context
    missing = '<answer>One reading favors this setup: {{setup.pullback}}</answer>'
    out["missing_tag_status"] = run(C.answer(req2, session_factory=Session, llm_call=ScriptedModel(missing,missing), now=NOW))["message"]["status"]
    reset(last_bar=(91,89,90))
    blocked = C.plan_view(Session, "alice", SYM, kind="entry", risk=400, now=NOW)
    out["blocked"] = blocked["plan"]["setups"]
    req3 = turn("Compare detailed scenarios", "setups-wait-0001")
    req3.plan_context = dict(kind="entry",risk=400,fingerprint=blocked["packet_fingerprint"])
    wait = '<answer>One reading favors waiting: {{rule.e3}}</answer><plan_setup id="wait"/>'
    chosen = run(C.answer(req3, session_factory=Session, llm_call=ScriptedModel(missing.replace('</answer>','</answer><plan_setup id="pullback"/>'),wait), now=NOW))["message"]
    out["wait"] = {"id":chosen["selected_setup_id"], "status":chosen["status"]}
    reset(last_bar=(102,98,100))
    out["low_rr"] = C.plan_view(Session, "alice", SYM, kind="entry", risk=400, now=NOW)["plan"]["setups"]
    reset()
    with Session() as ss:
        for bar in ss.query(Price).all():
            for field in ('open','high','low','close'):
                setattr(bar,field,getattr(bar,field)*1.3115)
        ss.commit()
    out["decimal"] = C.plan_view(Session, "alice", SYM, kind="entry", risk=400, now=NOW)["plan"]["setups"]
    reset(last_bar=(10000,99,100))
    out["invalid_stop"] = C.plan_view(Session, "alice", SYM, kind="entry", risk=400, now=NOW)["plan"]["setups"]
    unmeasurable = C.plan_view(Session, "alice", SYM, kind="holding", entry=100, shares=10, now=NOW)["plan"]
    out["invalid_held_stop"] = {"setups": unmeasurable["setups"], "reason": unmeasurable["unavailable"].get("setups")}
    R["pl13"] = out


def pl14_signal_timestamp_tie():
    # signal-engine writes every horizon with the SAME timestamp. "Latest signal" must resolve the
    # tie deterministically, or the packet fingerprint flips between the card's plan and the LLM
    # request and the comparison is refused with 409 "plan changed" (production, 2026-10-21).
    reset()
    tie = NOW - timedelta(hours=1)
    with Session() as s:
        st = s.query(Stock).filter_by(symbol=SYM).one()
        # Inserted lower id last so plain ts ordering is free to return either row.
        s.add(Signal(id=31, stock_id=st.id, ts=tie, signal=SignalType.HOLD, horizon=SignalHorizon.SHORT,
                     confidence=44.0, source="signal-engine"))
        s.add(Signal(id=30, stock_id=st.id, ts=tie, signal=SignalType.HOLD, horizon=SignalHorizon.GROWTH,
                     confidence=23.96, source="signal-engine"))
        s.commit()
    prints = set(); horizons = set()
    for _ in range(6):
        p = pkt(kind="entry")
        prints.add(p.fingerprint); horizons.add(p.fields["signal"].value["horizon"])
    view = C.plan_view(Session, "alice", SYM, kind="entry", now=NOW)
    R["pl14"] = {"fingerprints": sorted(prints), "horizons": sorted(horizons),
                 "plan_view_matches_packet": view["packet_fingerprint"] in prints}


def pl15_inflight_reask_after_plan_change():
    # Production 2026-10-21: the browser dropped a chat request mid-answer and re-asked with the
    # same request id. The plan had changed meanwhile, and the server answered 409 "plan changed"
    # before noticing the request was still being answered, so the page abandoned an answer
    # that was about to be stored. The in-progress check must come first.
    reset()
    view = C.plan_view(Session, "alice", SYM, kind="entry", now=NOW)
    context = {"kind": "entry", "fingerprint": view["packet_fingerprint"]}
    gate = None
    class Slow(ScriptedModel):
        async def __call__(self, *a, **k):
            await gate.wait()
            return await ScriptedModel.__call__(self, *a, **k)
    model = Slow('<answer>One reading favors waiting: {{rule.e1}}</answer><plan_setup id="wait"/>')
    out = {}
    async def scenario():
        nonlocal gate
        gate = asyncio.Event()
        first = turn("Compare alternative scenarios", "inflight-0001"); first.plan_context = context
        task = asyncio.ensure_future(C.answer(first, session_factory=Session, llm_call=model, now=NOW))
        await asyncio.sleep(0.2)
        with Session() as s:
            st = s.query(Stock).filter_by(symbol=SYM).one()
            s.add(Signal(id=77, stock_id=st.id, ts=NOW - timedelta(minutes=1), signal=SignalType.SELL,
                         horizon=SignalHorizon.SWING, confidence=70.0, source="signal-engine"))
            s.commit()
        out["plan_changed_meanwhile"] = C.plan_view(Session, "alice", SYM, kind="entry", now=NOW)["packet_fingerprint"] != context["fingerprint"]
        again = turn("Compare alternative scenarios", "inflight-0001"); again.plan_context = context
        try:
            await C.answer(again, session_factory=Session, llm_call=model, now=NOW); out["reask"] = "answered"
        except C.CopilotError as exc:
            out["reask"] = [exc.status, exc.message]
        gate.set()
        done = await task
        out["first_status"] = done["message"]["status"]
        replay = await C.answer(again, session_factory=Session, llm_call=model, now=NOW)
        out["replayed"] = replay["replayed"]; out["model_calls"] = len(model.calls)
    asyncio.new_event_loop().run_until_complete(scenario())
    R["pl15"] = out


for fn in (pl15_inflight_reask_after_plan_change, pl14_signal_timestamp_tie, pl13_trade_setups, pl12_dual_plan, pl11_card_and_drafts, pl10_review_residuals, pl9_review_c1_c5, pl8_tables, pl1_entry_arithmetic, pl2_holding_arithmetic, pl3_records_and_floors,
           pl4_staleness, pl5_routing_and_refusals, pl6_turns, pl7_plan_view):
    fn()
R["_meta"] = {"server": ENGINE.dialect.name}
print(json.dumps(R, default=str))
