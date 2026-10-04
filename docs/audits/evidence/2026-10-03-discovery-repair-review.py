"""Execute discovery and repair against disposable SQLite, with a stubbed provider.

No network, production state or provider calls. Prints witnesses, not acceptance assertions.
"""
import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[3]
probe = ROOT / 'services/research-engine/tests/_intelligence_pg_probe.py'
os.environ['STOCKAI_INTEL_DB'] = 'sqlite:///' + tempfile.mkdtemp() + '/review.db'
ns = {'__file__': str(probe), '__name__': 'review_bootstrap'}
exec(compile(probe.read_text().split('def reset(')[0], str(probe), 'exec'), ns)
path = ROOT / 'services/event-intelligence/src/services/earnings_discovery.py'
spec = importlib.util.spec_from_file_location('discovery_review', path)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
d._db = lambda: (ns['EarningsEvent'], ns['Session'], ns['Stock'])
date, dt = ns['date'], ns['datetime']
with ns['Session']() as s:
    stock = ns['Stock'](symbol='REVIEW', name='Review fixture', market=ns['Market'].US,
                       exchange=ns['Exchange'].NASDAQ, currency='USD')
    s.add(stock); s.commit()
d._provider_rows = lambda symbol: ([dict(period_end=date(2026,8,31), eps_actual=33.42,
    eps_estimate=31.82, outcome=None, reason=None)], None)
plan = d.repair('REVIEW', commit=True, actor='local-disposable-review')
with ns['Session']() as s:
    event = s.query(ns['EarningsEvent']).one()
    out = {'repair_written': plan['written'], 'stored_report_date': str(event.report_date),
           'persisted_columns': list(ns['EarningsEvent'].__table__.columns.keys())}
    # An unrelated older period can be announced during the candidate interval.
    event.report_date = date(2026,9,15)
    event.eps_actual = None
    s.commit()
out['pending_event_matches_reported_period'] = d.discover('REVIEW')['present']

src = ROOT / 'services/event-intelligence/src/services/earnings.py'
tree = ast.parse(src.read_text())
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
            and n.name == '_compute_post_earnings_returns')
env = {'date': date}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(src), 'exec'), env)
out['return_calculated_before_sept30_release'] = env[node.name](
    [(date(2026,8,28),100), (date(2026,8,31),110), (date(2026,9,1),121)],
    date(2026,8,31))[0]
print(json.dumps(out, indent=2))
