"""Offline follow-up witnesses. No model, network or production action."""
from pathlib import Path
import json
import runpy

ROOT = Path(__file__).resolve().parents[3]
n = runpy.run_path(str(ROOT / 'services/research-engine/tests/test_n1_packets.py'))
fields = n['_mu_fields'](eps_expectation=n['_f']({
    'value': 31.818, 'units': 'USD/share', 'basis': 'non-GAAP adjusted',
    'period': 'fiscal Q4 2026'}))
p = n['build_packet'](n['_report'](fields))
C, K = n['Claim'], n['Kind']
cases = {
    'expectation_as_actual': C(K.REPORTED_FIGURE, ('eps_expectation',)),
    'spelled_out_contradiction': C(K.REPORTED_FIGURE, ('revenue_actual',),
                                  comment='Revenue was fifty billion dollars.'),
    'unsupported_attribution': C(K.ATTRIBUTED_INTERPRETATION,
                                comment='Demand caused the rally.',
                                attributed_to='Chief executive',
                                source_evidence_id='issuer_document:1'),
}
out = {}
for name, claim in cases.items():
    r = n['narrate']([claim], p, deterministic='fallback')
    out[name] = {'accepted': r.accepted, 'text': r.text}
print(json.dumps(out, indent=2))
