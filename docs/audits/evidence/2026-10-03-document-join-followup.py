"""Read-only source reproduction using disposable SQLite; never accesses production.

Prints observations, not a release gate. Reuses the existing report probe's bootstrap.
"""
import ast
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[3]
probe = ROOT / 'services/research-engine/tests/_intelligence_pg_probe.py'
os.environ['STOCKAI_INTEL_DB'] = 'sqlite:///' + tempfile.mkdtemp() + '/review.db'
ns = {'__file__': str(probe), '__name__': 'review_bootstrap'}
exec(compile(probe.read_text().split('def reset(')[0], str(probe), 'exec'), ns)
Session, D = ns['Session'], ns['D']
Stock, Doc = ns['Stock'], ns['IssuerDocument']
date, datetime = ns['date'], ns['datetime']
out = {}
with Session() as s:
    stock = Stock(symbol='REVIEW', name='Review fixture', market=ns['Market'].US,
                  exchange=ns['Exchange'].NASDAQ, currency='USD')
    s.add(stock); s.flush()
    def add_doc(url, period, published):
        d = Doc(stock_id=stock.id, document_type='press_release', source_url=url,
                fiscal_period_end=period, fiscal_source='issuer release',
                published_at=published, retrieved_at=published, facts={})
        s.add(d); s.flush()
        return d
    intended = add_doc('https://example.invalid/q1', date(2026,3,31), datetime(2026,5,15))
    neighbour = add_doc('https://example.invalid/q2', date(2026,6,30), datetime(2026,7,20))
    selected = D.documents_for_period(s, stock.id, period_end=None, report_date=date(2026,5,15))
    out['report_date_join'] = {'expected_document': intended.id,
                              'selected_document': selected[0].id,
                              'selected_period': str(selected[0].fiscal_period_end)}
    # The API accepts now, but the implementation never applies it to document selection.
    future = add_doc('https://example.invalid/future', date(2026,12,31), datetime(2027,1,20))
    result = D.confirm_missing_event(s, stock.id, now=datetime(2026,10,3))
    out['future_document_counted_as_missing'] = future.id in [
        r['document_id'] for r in result.value['orphan_documents']]
    revision = Doc(stock_id=stock.id, document_type='press_release', source_url=intended.source_url,
                   fiscal_period_end=intended.fiscal_period_end,
                   retrieved_at=datetime(2026,5,16), supersedes_id=intended.id, facts={})
    try:
        with s.begin_nested():
            s.add(revision); s.flush()
    except Exception as exc:
        out['same_url_revision'] = type(exc).__name__

source = ROOT / 'services/event-intelligence/src/services/earnings.py'
tree = ast.parse(source.read_text())
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_coverage_outcome')
env = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), env)
out['partial_history_plus_calendar_write'] = env['_coverage_outcome'](
    {'rows_returned': 4, 'rows_mapped': 0, 'dropped': {'mapping': 4}}, 1, None)
print(json.dumps(out, indent=2))
