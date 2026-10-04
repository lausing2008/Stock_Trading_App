"""Re-runs the N1 review's own four witnesses against the REMEDIATED code.

Every line prints False, where the review recorded True. Offline: no model, network, database
or production action. This is a defect recheck, not a release gate.
"""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
from intelligence.evidence_packet import build_packet, ClaimKind
from intelligence.claims import Claim, check_claim, narrate

def _f(v, state="OK", **kw): return {"value": v, "state": state, **kw}
fields = {
  "fiscal_period": _f({"label": "FY2026 Q4"}),
  "official_figures": _f({"revenue": {"value": 54230000000.0, "units": "USD", "basis": "GAAP", "period": "fiscal Q4 2026"},
                          "eps_adjusted": {"value": 33.42, "units": "USD/share", "basis": "non-GAAP adjusted", "period": "fiscal Q4 2026"}}),
  "revenue_actual": _f({"value": 54230000000.0, "units": "USD", "basis": "GAAP", "period": "fiscal Q4 2026"}),
  "eps_actual": _f({"value": 33.42, "units": "USD/share", "basis": "non-GAAP adjusted", "period": "fiscal Q4 2026"}),
  "eps_expectation": _f({"value": 31.818}),
  "guidance_current": _f({"guidance_q1_revenue": {"value": 61500000000.0, "units": "USD", "basis": "company guidance", "period": "fiscal Q1 2027"}}),
  "guidance_change": _f({"current_guidance_available": True}, state="UNKNOWN"),
  "return_1d": _f({"pct": 3.03, "window": "2026-09-29 close to 2026-10-01 close"}),
  "three_verdicts": _f({"forward_outlook": "whether it is a RAISE is NOT established"}),
}
r = SimpleNamespace(id=12, version=1, subject_key="s", report_type="post_earnings",
                    contract_version=3, policy_version="3", cutoff_at=datetime(2026,10,4),
                    generated_at=datetime(2026,10,4), payload={"fields": fields, "evidence": {}})
p = build_packet(r)
D = "fallback"
def prose(text, kind=ClaimKind.REPORTED_FIGURE, **kw):
    return check_claim(Claim(kind=kind, quantity_ids=("revenue_actual",), comment=text, **kw),
                       p).accepted

# ---- structured-claims follow-up probes -------------------------------------------
ev = {"doc:1": {"source": "sec.gov", "value": {"type": "press_release"}}}
r3 = SimpleNamespace(**{**r.__dict__, "payload": {"fields": fields, "evidence": ev}})
p3 = build_packet(r3)
print("F1 estimate published as actual        accepted =",
      narrate([Claim(kind=ClaimKind.REPORTED_FIGURE, quantity_ids=("eps_expectation",))],
              p3, deterministic=D).accepted)
print("F2 contradictory comment appended      accepted =",
      narrate([Claim(kind=ClaimKind.REPORTED_FIGURE, quantity_ids=("revenue_actual",),
                     comment="Revenue was fifty billion dollars.")],
              p3, deterministic=D).accepted)
print("F3 attribution with no recorded quote  accepted =",
      narrate([Claim(kind=ClaimKind.ATTRIBUTED_INTERPRETATION, attributed_to="Chief executive",
                     source_evidence_id="doc:1", comment="Demand caused the rally")],
              p3, deterministic=D).accepted)
f4 = {k: (dict(v) if isinstance(v, dict) else v) for k, v in fields.items()}
f4["revenue_actual"] = {**f4["revenue_actual"], "value": {**f4["revenue_actual"]["value"], "tags": {"audited"}}}
r4 = SimpleNamespace(**{**r.__dict__, "payload": {"fields": f4, "evidence": {}}})
p4 = build_packet(r4)
tags = p4.fields["revenue_actual"]["value"]["tags"]
print("F4 set leaf mutable                           =",
      hasattr(tags, "add"), "| type =", type(tags).__name__)

print()
print("N1-R01a  'Revenue was $33.42 billion.'   accepted =", prose("Revenue was $33.42 billion."))
print("N1-R01b  'Revenue was $54.23 million.'   accepted =", prose("Revenue was $54.23 million."))
print("N1-R03a  'Profits surpassed analyst forecasts.' accepted =",
      prose("Profits surpassed analyst forecasts."))
print("N1-R03b  safe-phrase bypass (attribution on ANOTHER claim) accepted =",
      narrate([Claim(kind=ClaimKind.ATTRIBUTED_INTERPRETATION, attributed_to="management",
                     source_evidence_id="x", comment="demand improved"),
               Claim(kind=ClaimKind.REPORTED_FIGURE, quantity_ids=("revenue_actual",),
                     comment="AI caused the rally because orders grew.")],
              p, deterministic=D).accepted)

g = dict(fields); g["eps_expectation"] = _f({"value": 31.818, "units": "USD/share", "basis": "GAAP", "period": "fiscal Q4 2026"})
r2 = SimpleNamespace(**{**r.__dict__, "payload": {"fields": g, "evidence": {}}})
p2 = build_packet(r2)
print("N1-R02   GAAP estimate vs non-GAAP actual comparable =",
      p2.comparison("eps_actual", "eps_expectation").allowed)

h0 = p.packet_hash
try:
    p.fields["eps_actual"]["value"]["value"] = 999
    mutated = True
except Exception:
    mutated = False
print("N1-R04   nested mutation succeeded =", mutated,
      "| original report mutated =", fields["eps_actual"]["value"]["value"] == 999,
      "| verify() =", p.verify())
