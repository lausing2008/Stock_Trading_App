"""Local behavioral witnesses. Uses existing fixtures only in a throwaway SQLite database.
Not a production probe or a release gate; residual assertions require current defects.
"""
import ast
import json
import os
from pathlib import Path
import runpy
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['STOCKAI_INTEL_DB'] = 'sqlite:///' + tmp + '/review.db'
        h = runpy.run_path(str(ROOT / 'services/research-engine/tests/_intelligence_pg_probe.py'))
        G, S, Session, now = h['G'], h['S'], h['Session'], h['NOW']
        h['reset']()
        with Session() as s:
            f, evidence, meta, cov = G.stock_outlook(s, symbol='TESTCO', now=now - timedelta(days=7))
            future_price = f['price_as_of'].value['ts']
            assert datetime.fromisoformat(future_price) > meta['cutoff_at']
            assert evidence == []
            horizons = [f['outlook_' + k].value['conditional_outlook'] for k in ('short', 'medium', 'long')]
            assert len(set(horizons)) == 1
            # A bearish condition has an invalidation that is already true.
            bearish = G._outlook_by_horizon(G.calculated({'above_sma20': False, 'sma20': 100.}), G.unavailable('unused'))
            direction = bearish['outlook_short'].value
            assert 'below' in direction['conditional_outlook'] and 'below' in direction['invalidation']
        # Same-day release precedes the newly generated 'pre' report. The API has no actual
        # release-time test; mimic the documented release with a fixture timeline.
        h['reset'](actuals=True)
        with Session() as s:
            event = s.query(h['EarningsEvent']).one()
            event.report_date = now.date()
            s.commit()
            release = now - timedelta(hours=2)
            pre_time = now - timedelta(hours=1)
            built = G.pre_earnings(s, symbol='TESTCO', now=pre_time)
            pre, _ = S.save(s, *built)
            pre.generated_at = pre_time  # fixture generation clock, after the release
            s.commit()
            pre_id = pre.id
            assert S.frozen_pre_report(s, subject_key=pre.subject_key, before=release) is None
            event.eps_estimate = 1.95
            s.commit()
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.replace(tzinfo=timezone.utc) if tz else now
        path = ROOT / 'services/research-engine/src/api/intelligence_routes.py'
        fn = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'generate')
        fn.decorator_list = []
        ns = dict(datetime=FixedDatetime, timezone=timezone, SessionLocal=Session, G=G, S=S,
                  GenerateRequest=object, Depends=lambda x: None, get_current_username=lambda: '',
                  ReportType=G.ReportType, _GENERATORS={'post_earnings': 'post'},
                  _serialise=lambda r: {'id': r.id, 'pre_report_id': r.pre_report_id},
                  log=SimpleNamespace(info=lambda *a, **k: None))
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec'), ns)
        result = ns['generate'](SimpleNamespace(report_type='post_earnings', symbol='TESTCO', event_id=None))
        assert result['pre_report_id'] == pre_id
        with Session() as s:
            post = s.get(h['IntelligenceReport'], result['id'])
            fields = post.payload['fields']
            surprise = fields['eps_surprise_pct']['value']
            verdict = fields['thesis_verdict']['value']
            assert surprise['pct'] < 0 and verdict['result_vs_frozen'] == 'above'
        output = dict(engine='sqlite', future_price=future_price, report_cutoff=meta['cutoff_at'].isoformat(),
                      evidence_records=len(evidence), horizon_outlooks=horizons,
                      bearish_outlook=direction, release=release.isoformat(),
                      invalid_pre_generated_at=pre_time.isoformat(), api_selected_pre_report_id=pre_id,
                      current_estimate_surprise=surprise, frozen_baseline_verdict=verdict)
        h['ENGINE'].dispose()
        return output


if __name__ == '__main__':
    print(json.dumps(main(), indent=2))
