"""Local defect witnesses executing extracted production branches, not full engine tests.
No DB, Redis, network, email or trading calls. Assertions require current defects;
these must not be used as post-fix release acceptance tests.
"""
import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / 'services/market-data/src/services/paper_trading_engine.py'
source = path.read_text()
module = ast.parse(source)
scan = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == '_scan_for_entries')
branch = next(n for n in scan.body if isinstance(n, ast.If) and '_gates_override' in ast.unparse(n.test) and 'max_consec_losses' in ast.unparse(n.test))
markers = {}
blocks = []
noop = lambda *a, **kw: None
ctx = dict(max_consec_losses=3, _gates_override=False, open_count=0,
           portfolio=SimpleNamespace(id=2, name='test'), session=None,
           log=SimpleNamespace(warning=noop, info=noop),
           _recovery_grant_used=lambda pid, streak: markers.get(pid) == streak,
           _mark_recovery_grant=lambda pid, streak: markers.__setitem__(pid, streak),
           _clear_gate_block=noop,
           _write_gate_block=lambda *args: blocks.append(args[2]))
fn = ast.parse('def run(_consec_losses):\n    pass').body[0]
fn.body = [copy.deepcopy(branch), ast.Return(value=ast.Constant(True))]
exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), str(path), 'exec'), ctx)
first = ctx['run'](4)
# Simulate the documented downstream no-candidate/rejection case: no trade opens.
second = ctx['run'](4)
assert first is True and second is None and markers[2] == 4
recovery = dict(first_scan_reaches_candidates=True, trades_opened=0,
                second_scan_reaches_candidates=False, marker_streak=markers[2], blocks=blocks,
                source_line=branch.lineno)

cached = next(n for n in ast.walk(scan) if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == '_cgval')
fn = ast.parse('def check_cache(_cgval):\n    passed = []\n    for _ in [0]:\n        pass\n    return passed').body[0]
fn.body[1].body = [copy.deepcopy(cached), ast.parse('passed.append(True)').body[0]]
cache_ctx = dict(json=json, log=SimpleNamespace(info=noop), stock=SimpleNamespace(symbol='TEST'),
                 _style='SWING', _skip_tally={})
exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), str(path), 'exec'), cache_ctx)
old = json.dumps({'signal':'BUY','sent':False,'failed':['old evaluation'],
                  'ts':'2026-09-29T14:00:00+00:00','signal_id':100})
blocked = cache_ctx['check_cache'](old)
missing = cache_ctx['check_cache'](None)
assert blocked == [] and missing == [True]
print(json.dumps(dict(source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                     scope='Extracted gate branches with simulated dependencies; not a full scan or historical replay',
                     recovery=recovery,
                     conviction=dict(old_failure_passes_gate=bool(blocked), absent_cache_passes_gate=bool(missing),
                                     current_signal_identity_or_age_checked=False, source_line=cached.lineno)),indent=2))
