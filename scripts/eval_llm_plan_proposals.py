"""Budgeted real-model evaluation of LLM-proposed Game Plans (run INSIDE research-engine).

    docker cp scripts/eval_llm_plan_proposals.py stockai-research-engine-1:/tmp/eval.py
    docker exec -e EVAL_MAX_CALLS=12 stockai-research-engine-1 python /tmp/eval.py <out.json>

Goes through the production path: C.answer with the admin shared key and model, the shared
copilot_chat budget, usage logging and normal Copilot history (questions are tagged [eval]).
A counter refuses any provider call beyond EVAL_MAX_CALLS. Every proposal is re-checked here,
independently of proposal.py: each price must equal anchor + k x ATR from the packet, a
'conditional' proposal must satisfy every gate, and the model's own prose may contain no digits.
"""
import asyncio
import json
import os
import re
import sys
import time
import uuid
import warnings

warnings.filterwarnings("ignore")
sys.path[:0] = ["/app", "/app/shared"]
from datetime import datetime, timedelta, timezone  # noqa: E402

from db import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402
from common import llm_budget  # noqa: E402
from src.copilot import service as C, llm as L, proposal as PR, plan as P  # noqa: E402
from src.copilot.packet import build_packet  # noqa: E402
from src.copilot import intent as I  # noqa: E402
from src.api.ai_proxy import _admin_key, _admin_model  # noqa: E402
from src.api.copilot_routes import _usage_logger  # noqa: E402

MAX_CALLS = int(os.environ.get("EVAL_MAX_CALLS", "12"))
CALLS: list[dict] = []

# Forces the retry path: a "pullback" entry above the latest close, which proposal.py rejects.
FORCED_BAD = ('<answer>Initial reading favours a pullback near {{level.high20}}.</answer>'
              '<plan_proposal kind="pullback" entry="level.high20" stop="level.low20" '
              'target1="level.high20+1atr" target2="level.high20+2atr"/>')

SCENARIOS = [
    dict(name="us_entry_MU", symbol="MU", ctx=dict(kind="entry", risk=500.0)),
    dict(name="us_entry_NVDA", symbol="NVDA", ctx=dict(kind="entry", risk=500.0)),
    dict(name="us_holding_MU", symbol="MU", ctx=dict(kind="holding", entry=800.0, shares=10, stop=780.0)),
    dict(name="hk_entry_trend_blocked_0005", symbol="0005.HK", ctx=dict(kind="entry", risk=5000.0)),
    dict(name="stale_entry_SSNLF", symbol="SSNLF", ctx=dict(kind="entry", risk=500.0)),
    dict(name="forced_rejection_then_real_repair_MU", symbol="MU", ctx=dict(kind="entry", risk=500.0), forced_first=True),
]


def make_call(forced_first: bool):
    state = {"forced": forced_first}

    async def call(provider, api_key, model, messages):
        if state["forced"]:
            state["forced"] = False
            CALLS.append({"scripted": True, "model": "scripted-invalid-first-attempt", "text": FORCED_BAD})
            return L.LlmReply(text=FORCED_BAD, model="scripted", usage={"input_tokens": 0, "output_tokens": 0}, http_status=200) \
                if hasattr(L, "LlmReply") else type("R", (), dict(text=FORCED_BAD, model="scripted", usage=None, http_status=200))()
        real = [c for c in CALLS if not c.get("scripted")]
        if len(real) >= MAX_CALLS:
            raise L.LlmError(f"eval budget of {MAX_CALLS} provider calls exhausted; not called")
        t0 = time.monotonic()
        reply = await L.call(provider, api_key, model, messages)
        CALLS.append({"scripted": False, "model": reply.model, "usage": reply.usage,
                      "seconds": round(time.monotonic() - t0, 1), "text": reply.text,
                      "repair": len(messages) > 1 and "failed the code checks" in json.dumps(messages[-1])})
        return reply
    return call


def prose_digits(raw: str) -> list[str]:
    """Digits the MODEL wrote in its prose (tags and {{placeholders}} removed)."""
    body = re.sub(r"<plan_proposal\b[^>]*/>", "", raw)
    body = re.sub(r"\{\{[^}]*\}\}", "", body)
    body = re.sub(r"</?answer>", "", body)
    return re.findall(r"\d[\d,.]*", body)


def independent_check(p: dict, packet) -> list[str]:
    """Re-derive every claim of a proposal from the packet, without proposal.py's logic."""
    problems = []
    plan = packet.plan
    for d in p.get("derivation", []):
        anchor = plan.anchors.get(d["anchor"])
        if anchor is None or abs(anchor - d["anchor_price"]) > 1e-6:
            problems.append(f"{d['level']}: anchor value differs from the packet")
        if abs(d["anchor_price"] + d["atr_multiple"] * plan.atr - d["price"]) > 1e-3:
            problems.append(f"{d['level']}: price is not anchor + k x ATR")
        if abs(d["atr_multiple"]) > PR.MAX_ATR_MULT + 1e-9:
            problems.append(f"{d['level']}: offset beyond {PR.MAX_ATR_MULT} ATR")
    if p["state"] == "conditional":
        e, s, t = p["entry"], p["stop"], [x["price"] for x in p["targets"]]
        price_f = packet.fields.get("price")
        if p["kind"] != "hold":
            if not (s < e < t[0] < t[1]):
                problems.append("conditional proposal with unordered levels")
            if (t[0] - e) / (e - s) + 1e-9 < P.MIN_REWARD_RISK:
                problems.append("conditional proposal below the minimum R")
            if plan.trend_blocked:
                problems.append("conditional proposal while the trend rule blocks new longs")
        if price_f is not None and price_f.state.value == "STALE":
            problems.append("conditional proposal on stale prices")
    return problems


async def run_one(sc, username, key, model, now):
    view = C.plan_view(SessionLocal, username, sc["symbol"], now=now, **sc["ctx"])
    ctx = {**sc["ctx"], "fingerprint": view["packet_fingerprint"]}
    req = C.TurnRequest(username=username, symbol=sc["symbol"], request_id="eval-" + uuid.uuid4().hex[:20],
                        question=f"[eval] Propose your own conditional Game Plan for {sc['symbol']}.",
                        conversation_id=None, provider="claude", model=model, api_key=key,
                        uses_shared_key=True, plan_context=ctx)
    before = len(CALLS)
    try:
        out = await C.answer(req, session_factory=SessionLocal, llm_call=make_call(sc.get("forced_first", False)),
                             budget=llm_budget, usage_logger=_usage_logger, now=now)
    except C.CopilotError as exc:
        return {"scenario": sc["name"], "error": f"{exc.status} {exc.message}", "calls": CALLS[before:]}
    msg = out["message"]
    with SessionLocal() as s:
        pi = I.PositionIntent(kind=sc["ctx"]["kind"], **{k: v for k, v in {
            "entry": sc["ctx"].get("entry"), "shares": sc["ctx"].get("shares"), "stop": sc["ctx"].get("stop"),
            "risk_amount": sc["ctx"].get("risk")}.items() if v is not None})
        packet = build_packet(s, sc["symbol"], now=now, plan_request=pi)
    p = msg.get("plan_proposal")
    mine = CALLS[before:]
    return {
        "scenario": sc["name"], "symbol": sc["symbol"], "inputs": sc["ctx"], "status": msg["status"],
        "model": msg.get("model"), "price_as_of": msg.get("price_as_of"),
        "price_state": packet.fields["price"].state.value, "price_reason": packet.fields["price"].reason,
        "rules_setups": [(x["id"], x["state"]) for x in view["plan"]["setups"]],
        "proposal": p, "rejection_reasons": msg.get("rejection_reasons"),
        "independent_problems": independent_check(p, packet) if p else [],
        "prose_digits_by_call": [prose_digits(c["text"]) for c in mine if not c.get("scripted")],
        "provider_calls": sum(1 for c in mine if not c.get("scripted")),
        "scripted_calls": sum(1 for c in mine if c.get("scripted")),
        "repair_after_code_rejection": any(c.get("repair") for c in mine),
        "answer": msg["content"], "raw_replies": [c["text"] for c in mine],
        "usage": [c.get("usage") for c in mine if not c.get("scripted")],
    }


async def main(out_path):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as s:
        username = s.execute(text("select username from users where is_active and role='ADMIN' order by id limit 1")).scalar()
    key, model = _admin_key("claude"), _admin_model("claude")
    assert key, "no admin Claude key"
    results = []
    only = set(filter(None, os.environ.get("EVAL_ONLY", "").split(",")))
    for sc in [x for x in SCENARIOS if not only or x["name"] in only]:
        results.append(await run_one(sc, username, key, model, now))
    real = [c for c in CALLS if not c.get("scripted")]
    report = {"run_at": now.isoformat(), "model_requested": model, "max_calls": MAX_CALLS,
              "provider_calls": len(real), "usage_total": {
                  "input_tokens": sum((c.get("usage") or {}).get("input_tokens", 0) for c in real),
                  "output_tokens": sum((c.get("usage") or {}).get("output_tokens", 0) for c in real)},
              "results": results}
    with open(out_path, "w") as f:
        json.dump(report, f, indent=1, default=str)
    for r in results:
        p = r.get("proposal") or {}
        print(r["scenario"], "|", r.get("error") or r["status"], "| calls", r.get("provider_calls"),
              "| proposal", p.get("kind"), p.get("state"), p.get("entry"), p.get("stop"),
              [t["price"] for t in p.get("targets", [])], "| problems", r.get("independent_problems"),
              "| prose digits", r.get("prose_digits_by_call"))
    print("provider calls", len(real), "tokens", report["usage_total"])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
