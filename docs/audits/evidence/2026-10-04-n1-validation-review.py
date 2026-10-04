"""Offline defect witnesses using N1's own fixture and actual packet/validator functions.

No model, network, database, or production mutations. These print observations, not acceptance.
"""
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[3]
n = runpy.run_path(str(ROOT / 'services/research-engine/tests/test_n1_packets.py'))
p = n['build_packet'](n['_report'](n['_mu_fields']()))
out = {}
for draft in (
    'Revenue was $33.42 billion.',
    'Revenue was $54.23 million.',
    'According to management, demand improved. AI caused the share rally because orders grew.',
    'Profits surpassed analyst forecasts.',
):
    r = n['validate'](draft, p, deterministic='fallback')
    out[draft] = {'accepted': r.accepted, 'violations': [v.kind for v in r.violations]}
before = p.packet_hash
p.fields['eps_actual']['value']['value'] = 999
out['nested_mutation_keeps_hash'] = p.packet_hash == before
f = n['_mu_fields'](accounting_basis=n['_f']({'estimate_basis': 'GAAP'}))
out['gaap_estimate_nongaap_actual_comparison_allowed'] = n['build_packet'](
    n['_report'](f)).allows(n['Claim'].COMPARISON)
print(json.dumps(out, indent=2))
