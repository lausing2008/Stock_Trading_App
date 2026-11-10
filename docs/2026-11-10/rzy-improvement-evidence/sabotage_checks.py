"""Each deliberate break of the research engine must fail a test (run from the repository root)."""
import json, subprocess, sys
P = 'scripts/rzy_research_engine.py'
orig = open(P).read()
MUTATIONS = {
    'gate keeps the position': ("            del self.s['positions'][ident]\n", "            pass\n", 'gate_refuses'),
    'series reads one step ahead': ("        return bisect_right(self.times, when) - 1\n", "        return bisect_right(self.times, when)\n", 'features_ignore'),
    'missing context passes a gate': ("    if g.get('regime') and f['regime'] is not True: return 'regime'\n", "    if g.get('regime') and f['regime'] is False: return 'regime'\n", 'unknown_context'),
    'line exit never switched off': ("            self.LINE_EXIT = self.x['line_exit'] == 'close'\n", "            self.LINE_EXIT = True\n", 'line_exit_off'),
    'flat at close ignored': ("            if x['flat_at_close'] and b.get('session_last'):\n", "            if False and x['flat_at_close'] and b.get('session_last'):\n", 'flat_at_close'),
    'context twins ignore the trend': ("                      and trend[t] == trade['direction']\n", "", 'context_controls'),
    'target cap ignored': ("                    if sign * (st['target'] - capped) > 0:\n", "                    if False:\n", 'exit_rules'),
    'time stop ignores the bar count': ("i - pos['entry_index'] + 1 >= x['time_stop_bars']", "i - pos['entry_index'] + 1 >= 1", 'exit_rules'),
}
out = {}
try:
    for name, (a, b, k) in MUTATIONS.items():
        assert orig.count(a) == 1, name
        open(P, 'w').write(orig.replace(a, b))
        r = subprocess.run([sys.executable, '-m', 'pytest', 'tests/test_rzy_research.py', '-q', '-x', '-k', k], cwd='services/market-data', capture_output=True, text=True)
        out[name] = dict(test_filter=k, failed=r.returncode != 0, tail=r.stdout.strip().splitlines()[-1])
        print(name, '->', 'FAILS (good)' if r.returncode else 'PASSES (bad)', flush=True)
finally:
    open(P, 'w').write(orig)
json.dump(out, open('docs/2026-11-10/rzy-improvement-evidence/sabotage-results.json', 'w'), indent=1)
sys.exit(0 if all(v['failed'] for v in out.values()) else 1)
