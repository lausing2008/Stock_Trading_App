import json
import sys
sys.path[:0] = ['/app/shared', '/app']
from src.api import calibration
names = {'calibrate_ml_weight', 'calibrate_ta_weights', 'calibrate_conviction_weights',
         'outcomes_calibrate_apply', 'tune_sell_pillars', 'tune_style_profiles', 'tune_strategy', 'signal_watchdog'}
registered = {r.endpoint.__name__: r.endpoint for r in calibration.router.routes if r.endpoint.__name__ in names}
assert set(registered) == names
forbidden = {fn.__wrapped__.__code__ for fn in registered.values()}
def profile(frame, event, arg):
    if event == 'call' and frame.f_code in forbidden:
        raise AssertionError('guarded writer body entered: '+frame.f_code.co_name)
def reject(*a, **k): raise AssertionError('external state requested')
calibration._get_redis = reject
calibration._record_tune_history = reject
sys.setprofile(profile)
try:
    for name, fn in registered.items():
        result = fn()
        assert result['status']=='research_only' and result['applied'] is False
        assert result['promoted'] is False and result['existing_settings_changed'] is False
finally:
    sys.setprofile(None)
print(json.dumps({'actual_registered_writers_checked': sorted(registered), 'count': len(registered), 'body_entries':0}))
