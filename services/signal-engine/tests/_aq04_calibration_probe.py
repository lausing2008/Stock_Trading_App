"""Run the REAL calibrate_ml_weight loop against SQLite with a planted, unadjustable split.

AQ04 residual (2026-10-12). This suite's conftest stubs `db` as a MagicMock, which would make
every assertion here pass regardless of what the code does — so the module is loaded by path
with real models behind it. The question being answered is not "does the helper classify
correctly" but "does the function that WRITES the production ML weight actually refuse the
corrupt observation".
"""
import ast
import numpy as np
import importlib.util
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root / 'shared'), str(root / 'services/signal-engine')]

common = types.ModuleType('common'); common.__path__ = [str(root / 'shared/common')]
sys.modules['common'] = common
cfg = types.ModuleType('common.config')
cfg.get_settings = lambda: types.SimpleNamespace(database_url='sqlite://', redis_url='')
sys.modules['common.config'] = cfg
logmod = types.ModuleType('common.logging'); logmod.get_logger = lambda *a, **k: MagicMock()
sys.modules['common.logging'] = logmod
jwt = types.ModuleType('common.jwt_auth'); jwt.get_current_username = lambda: 'tester'
sys.modules['common.jwt_auth'] = jwt
for name in ('outcome_price_basis',):
    spec = importlib.util.spec_from_file_location(f'common.{name}', root / f'shared/common/{name}.py')
    m = importlib.util.module_from_spec(spec); sys.modules[f'common.{name}'] = m
    spec.loader.exec_module(m); setattr(common, name, m)

db = types.ModuleType('db'); db.__path__ = [str(root / 'shared/db')]
sys.modules['db'] = db
spec = importlib.util.spec_from_file_location('db.models', root / 'shared/db/models.py')
models = importlib.util.module_from_spec(spec); sys.modules['db.models'] = models
spec.loader.exec_module(models)
for n in ('CorporateAction', 'CorporateActionCoverage', 'Price', 'Signal', 'SignalHorizon',
          'SignalOutcome', 'SignalType', 'Stock', 'TimeFrame', 'TuneHistory', 'Market',
          'Exchange', 'Base'):
    setattr(db, n, getattr(models, n))
db.get_session = lambda: None

# signals_shared pulls redis/indicators; only these names are read by calibration.py.
ss = types.ModuleType('api.signals_shared')
ss._CONF_CAL_CACHE_KEY = 'k'; ss._CONF_CAL_MIN_COUNT = 1
ss._OUTCOME_HOLD_DAYS = {'SWING': 14, 'LONG': 28, 'GROWTH': 14}
ss._get_confidence_calibration = lambda *a, **k: {}
ss._get_redis = lambda *a, **k: None
ss._mark_tuned = lambda *a, **k: None
ss._record_tune_history = lambda *a, **k: None
ss._redis_get_float = lambda *a, **k: None
ss._tuning_staleness = lambda *a, **k: {}
ss.log = MagicMock()
ss.set_ml_weight_global_cap = lambda *a, **k: None
src_pkg = types.ModuleType('src'); src_pkg.__path__ = [str(root / 'services/signal-engine/src')]
sys.modules['src'] = src_pkg
api_pkg = types.ModuleType('src.api'); api_pkg.__path__ = [str(root / 'services/signal-engine/src/api')]
sys.modules['src.api'] = api_pkg
sys.modules['src.api.signals_shared'] = ss
# Load real pure fusion/profile definitions; override external policy reads for this price-basis probe.
gen_pkg = types.ModuleType('src.generators'); gen_pkg.__path__ = [str(root / 'services/signal-engine/src/generators')]
sys.modules['src.generators'] = gen_pkg
sig_mod = types.ModuleType('src.generators.signals')
sig_mod.set_ml_weight_global_cap = lambda *a, **k: None
sig_mod._ml_weight_global_cap = 0.5
sig_source = (root / 'services/signal-engine/src/generators/signals.py').read_text()
sig_nodes = [n for n in ast.parse(sig_source).body
             if (isinstance(n, ast.FunctionDef) and n.name == '_calculate_ml_weight')
             or (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                 and n.target.id == '_STYLE_PROFILES')]
sig_mod.np = np
exec(compile(ast.Module(body=sig_nodes, type_ignores=[]), '<fusion>', 'exec'), vars(sig_mod))
sig_mod._get_style_tuned_param = lambda style, param, default: default
sig_mod._get_dynamic_buy_threshold = lambda style, regime: None
sys.modules['src.generators.signals'] = sig_mod

spec = importlib.util.spec_from_file_location(
    'src.api.calibration', root / 'services/signal-engine/src/api/calibration.py')
calib = importlib.util.module_from_spec(spec); sys.modules['src.api.calibration'] = calib
spec.loader.exec_module(calib)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

engine = create_engine('sqlite://')
models.Base.metadata.create_all(engine, tables=[
    models.Stock.__table__, models.Price.__table__, models.Signal.__table__,
    models.CorporateAction.__table__, models.CorporateActionCoverage.__table__,
    models.TuneHistory.__table__,
])

failures = []
def check(label, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + label + ('' if cond else f'  {detail}'))
    if not cond:
        failures.append(label)


def build(split_on: date | None, *, coverage: bool, n_signals: int = 120):
    """One symbol, a flat $100 series, and optionally a 2:1 split mid-window.

    The price series is FLAT, so every honest forward return is 0%. A 2:1 split halves the
    quoted price, so an unadjusted reading of the same flat stock is -50% — a number large
    enough to dominate any weight sweep fitted on it.
    """
    with Session(engine) as s:
        for t in (models.CorporateActionCoverage, models.CorporateAction,
                  models.Signal, models.Price, models.Stock):
            s.query(t).delete()
        s.commit()
        st = models.Stock(id=1, symbol='SPLT', name='SPLT', market=models.Market.US,
                          exchange=models.Exchange.NASDAQ, active=True)
        s.add(st)
        base = date(2026, 1, 5)
        pid = 0
        for i in range(260):
            d = base + timedelta(days=i)
            px = 100.0
            if split_on and d >= split_on:
                px = 50.0
            pid += 1
            s.add(models.Price(id=pid, stock_id=1, ts=datetime(d.year, d.month, d.day),
                               timeframe=models.TimeFrame.D1, open=px, high=px, low=px,
                               close=px, volume=1_000_000))
        for i in range(n_signals):
            sd = base + timedelta(days=30 + i)
            s.add(models.Signal(id=i + 1, stock_id=1, ts=datetime(sd.year, sd.month, sd.day),
                                horizon=models.SignalHorizon.SWING,
                                signal=models.SignalType.BUY, confidence=60,
                                reasons={'ml_probability': .85, 'ta_score': .8,
                                         'ml_test_auc': .7, 'market_regime': 'bull'}))
        if split_on:
            s.add(models.CorporateAction(id=1, symbol='SPLT', ex_date=split_on, action_type='split',
                                         split_ratio=None, source='test', retrieved_at=datetime(2026, 10, 1)))
        if coverage:
            s.add(models.CorporateActionCoverage(
                id=1, symbol='SPLT', requested_from=base, requested_to=date(2026, 9, 1),
                evidenced_from=base, evidenced_to=date(2026, 9, 1),
                completeness_basis='source_guarantee', source='test',
                method='cumulative_split_factor_v1',
                retrieved_at=datetime(2026, 10, 1)))
        s.commit()


def run(**kw):
    build(**kw)
    with Session(engine) as s:
        return calib.calibrate_ml_weight.__wrapped__(lookback_days=730, session=s, _='t') \
            if hasattr(calib.calibrate_ml_weight, '__wrapped__') else \
            calib.calibrate_ml_weight(lookback_days=730, session=s, _='t')


# 1. a recorded split with no usable ratio must not become an observation
# The split sits inside the NEWEST slice on purpose. `avg_return_pct` on the returned curve
# is measured on the held-out 30%, so a split placed in the oldest rows would corrupt the
# weight SELECTION while leaving the reported curve looking clean — the test would then
# pass on a number the defect never touched.
r = run(split_on=date(2026, 5, 15), coverage=False)
rb = r.get('return_basis', {})
check('a recorded, unappliable action drops its observations',
      rb.get('dropped_unadjustable_action', 0) > 0, r)
# The stock is FLAT, so every honest forward return is 0%. Windows entirely after the split
# are honest and must survive; only windows that SPAN it are corrupt. So the test of the fix is
# not how many rows remain, it is whether a -50% reading reached the curve the weight is fitted
# on — which is the number, not the bookkeeping.
_rets = [pt['avg_return_pct'] for pt in r.get('curve', []) if pt.get('avg_return_pct') is not None]
check('no split-spanning window reaches the fitted curve',
      _rets and max(abs(x) for x in _rets) < 1.0, _rets[:5])
check('honest windows on the far side of the split are kept, not thrown away',
      rb.get('observations_used', 0) > 0, rb)

# 2. no action recorded at all: usable, but labelled unverified rather than silently trusted
r = run(split_on=None, coverage=False)
rb = r.get('return_basis', {})
check('a window with nothing recorded is still usable',
      rb.get('observations_used', 0) > 0, r)
check('...and is counted as unverified completeness, not quietly trusted',
      rb.get('used_unverified_completeness', 0) > 0, r)
check('a clean flat series yields no dropped observations',
      rb.get('dropped_unadjustable_action', -1) == 0, r)

# 3. the basis is reported on every exit, so the decision carries its own evidence
check('the applying calibrator reports its return basis', 'return_basis' in r, sorted(r))


# 4. A split between the calendar target and the session the exit actually fills on.
#    Weekday-only bars: the 14-day target lands on a Saturday, so the exit fills the following
#    Monday — and the split's ex_date is that Monday. Classifying over the calendar target
#    instead of the real holding window puts this action just outside the window being checked,
#    and admits a -50% reading as a genuine return.
def build_gap():
    with Session(engine) as s:
        for t in (models.CorporateActionCoverage, models.CorporateAction,
                  models.Signal, models.Price, models.Stock):
            s.query(t).delete()
        s.commit()
        s.add(models.Stock(id=1, symbol='GAPT', name='GAPT', market=models.Market.US,
                           exchange=models.Exchange.NASDAQ, active=True))
        split_day = date(2026, 5, 18)      # a Monday
        pid = 0
        d = date(2026, 1, 5)
        while d <= date(2026, 9, 1):
            if d.weekday() < 5:            # sessions on weekdays only
                pid += 1
                px = 50.0 if d >= split_day else 100.0
                s.add(models.Price(id=pid, stock_id=1, ts=datetime(d.year, d.month, d.day),
                                   timeframe=models.TimeFrame.D1, open=px, high=px, low=px,
                                   close=px, volume=1_000_000))
            d += timedelta(days=1)
        # ONLY the two signals whose 14-day target lands on the weekend before the split, so
        # the gap is the only thing that can admit a corrupt return. Any signal whose target
        # falls ON or after the split is corrupt under both readings and would mask the
        # difference this case exists to show.
        sid = 0
        for sd in (date(2026, 5, 2), date(2026, 5, 3)):
            sid += 1
            s.add(models.Signal(id=sid, stock_id=1, ts=datetime(sd.year, sd.month, sd.day),
                                horizon=models.SignalHorizon.SWING, signal=models.SignalType.BUY,
                                confidence=60,
                                reasons={'ml_probability': .85, 'ta_score': .8,
                                         'ml_test_auc': .7, 'market_regime': 'bull'}))
        s.add(models.CorporateAction(id=1, symbol='GAPT', ex_date=split_day, action_type='split',
                                     split_ratio=None, source='test',
                                     retrieved_at=datetime(2026, 10, 1)))
        s.commit()


build_gap()
with Session(engine) as _s:
    r = calib.calibrate_ml_weight(lookback_days=730, session=_s, _='t')
_rets = [pt['avg_return_pct'] for pt in r.get('curve', []) if pt.get('avg_return_pct') is not None]
check('a split between the target date and the filling session is still caught',
      r.get('return_basis', {}).get('dropped_unadjustable_action', 0) == 2, r.get('return_basis'))
check('nothing from the target/fill gap reaches the sample',
      r.get('return_basis', {}).get('observations_used', 1) == 0, r.get('return_basis'))

# 5. GET /ml-weight-validation is a read-only preview of the calibrator (2026-10-22).
#    A steadily rising series makes every resolved return positive. With the live cap at 0, the
#    SWING floor (.15) keeps fused just under the .81 boundary, so the incumbent fires nothing and
#    a higher candidate cap must promote — the case where a write would be most tempting.
def build_trend():
    with Session(engine) as s:
        for t in (models.CorporateActionCoverage, models.CorporateAction,
                  models.Signal, models.Price, models.Stock, models.TuneHistory):
            s.query(t).delete()
        s.commit()
        s.add(models.Stock(id=1, symbol='TRND', name='TRND', market=models.Market.US,
                           exchange=models.Exchange.NASDAQ, active=True))
        base = date(2026, 1, 5)
        for i in range(260):
            d = base + timedelta(days=i)
            px = 100.0 + i
            s.add(models.Price(id=i + 1, stock_id=1, ts=datetime(d.year, d.month, d.day),
                               timeframe=models.TimeFrame.D1, open=px, high=px, low=px,
                               close=px, volume=1_000_000))
        for i in range(120):
            sd = base + timedelta(days=30 + i)
            s.add(models.Signal(id=i + 1, stock_id=1, ts=datetime(sd.year, sd.month, sd.day),
                                horizon=models.SignalHorizon.SWING,
                                signal=models.SignalType.BUY, confidence=60,
                                reasons={'ml_probability': .85, 'ta_score': .8,
                                         'ml_test_auc': .7, 'market_regime': 'bull'}))
        s.commit()


def table_counts(s):
    return {m.__name__: s.query(m).count() for m in (
        models.Stock, models.Price, models.Signal, models.TuneHistory,
        models.CorporateAction, models.CorporateActionCoverage)}


cap_writes, history_writes = [], []
sig_mod._ml_weight_global_cap = 0.0
sig_mod.set_ml_weight_global_cap = lambda cap: cap_writes.append(cap)
calib._record_tune_history = lambda *a, **k: history_writes.append(k)
sig_mod._get_dynamic_buy_threshold = lambda style, regime: .81

build_trend()
with Session(engine) as _s:
    before = table_counts(_s)
    g = calib.ml_weight_validation(lookback_days=730, session=_s)
    check('GET leaves no pending session changes',
          not (_s.new or _s.dirty or _s.deleted), (_s.new, _s.dirty, _s.deleted))
    _s.commit()
with Session(engine) as _s:
    check('GET writes no rows', table_counts(_s) == before, (before, table_counts(_s)))
check('GET never sets the cap or records tune history',
      not cap_writes and not history_writes, (cap_writes, history_writes))
check('GET reports both slices\' fired counts on every curve point',
      all({'fired'} <= set(pt['train']) and {'fired'} <= set(pt['validation'])
          for pt in g['curve']) and g['curve'], g.get('curve', [])[:2])
check('the preview predicts a promotion here', g['preview']['would_apply'] is True, g['preview'])

with Session(engine) as _s:
    r = calib.calibrate_ml_weight(lookback_days=730, session=_s, _='t')
check('POST makes the decision GET predicted', r['applied'] is g['preview']['would_apply'], r)
check('POST writes the cap GET selected', cap_writes == [g['optimal_weight']], cap_writes)
check('the same curve drives both', r['curve'] == g['curve'])
_hist = history_writes[0] if history_writes else {}
check('POST records exactly what GET said it would',
      len(history_writes) == 1 and all(_hist.get(k) == v
                                        for k, v in g['preview']['would_record'].items()),
      (_hist, g['preview'].get('would_record')))

print('FAILURES:', failures)
sys.exit(1 if failures else 0)
