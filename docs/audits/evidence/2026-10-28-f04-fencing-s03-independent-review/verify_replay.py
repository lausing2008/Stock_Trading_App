"""Offline hashes and payload comparison for the submitted S0-3 evidence. Run from any cwd."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[4]
REV = 'a9f21607'
BASE = 'docs/audits/evidence/2026-10-28-s0-3-two-model-replay/'

def read(path):
    return subprocess.check_output(['git', '-C', str(REPO), 'show', REV+':'+path])

manifest = json.loads(read(BASE+'manifest.json'))
checks = {}
for name, sha in manifest['outputs_raw_sha256'].items():
    data = read(BASE+'full/'+name+('.gz' if name.endswith('.jsonl') else ''))
    if name.endswith('.jsonl'):
        data = gzip.decompress(data)
    checks[name] = hashlib.sha256(data).hexdigest() == sha
for name, sha in manifest['outputs_gzip_sha256'].items():
    checks[name] = hashlib.sha256(read(BASE+'full/'+name)).hexdigest() == sha
checks['replay.py'] = hashlib.sha256(read(BASE+'replay.py')).hexdigest() == manifest['code']['replay.py']
rows = {key: [json.loads(line) for line in gzip.decompress(read(BASE+'full/'+key+'.jsonl.gz')).splitlines()]
        for key in ('old', 'control', 'new')}
indexed = {key: {(r['symbol'], r['style']): r for r in records} for key, records in rows.items()}

def clean(row):
    row = json.loads(json.dumps(row))
    row.pop('seconds', None)
    row.get('reasons', {}).get('input_contract', {}).pop('generated_at', None)
    return row

control_diff = [key for key, row in indexed['old'].items() if clean(row) != clean(indexed['control'][key])]
changed = [key for key, row in indexed['old'].items() if clean(row) != clean(indexed['new'][key])]
output_changed = [key for key in changed if any(indexed['old'][key][field] != indexed['new'][key][field]
                   for field in ('signal', 'confidence', 'bullish_probability'))]
result = {'hash_checks': checks, 'rows': {k: len(v) for k, v in rows.items()},
          'control_mismatches': control_diff, 'payload_changed': len(changed),
          'decision_output_changed': len(output_changed), 'metadata_only': len(changed)-len(output_changed)}
assert all(checks.values()) and not control_diff
print(json.dumps(result, indent=2))
