from datetime import datetime, timezone
import pytest
from src.services.risk_delivery_schedule import is_quiet, validate_schedule, load_schedule, save_schedule, PREFIX


def policy(**kwargs):
    return {'enabled': True, 'timezone': 'America/Los_Angeles', 'start': '22:00', 'end': '07:00', **kwargs}


@pytest.mark.parametrize('hour,expected', [(4, False), (5, True), (12, True), (14, False)])
def test_overnight_schedule_uses_the_account_timezone(hour, expected):
    assert is_quiet(policy(), datetime(2026, 10, 15, hour, tzinfo=timezone.utc)) is expected


def test_day_window_boundaries_and_disabled_policy():
    p = policy(timezone='UTC', start='09:00', end='17:00')
    assert is_quiet(p, datetime(2026, 10, 15, 9))
    assert not is_quiet(p, datetime(2026, 10, 15, 17))
    assert not is_quiet({**p, 'enabled': False}, datetime(2026, 10, 15, 10))


def test_both_repeated_hours_at_dst_fallback_remain_quiet():
    p = policy(start='01:00', end='02:00')
    assert is_quiet(p, datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc))
    assert is_quiet(p, datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc))
    assert not is_quiet(p, datetime(2026, 11, 1, 10, tzinfo=timezone.utc))


@pytest.mark.parametrize('bad', [{'timezone': 'not/a-zone'}, {'start': '25:00'}, {'end': '7:00'}, {'end': '22:00'}, {'enabled': 'false'}])
def test_invalid_settings_cannot_be_saved(bad):
    with pytest.raises(ValueError): validate_schedule(policy(**bad))


def test_storage_is_per_account_and_unreadable_state_does_not_mean_permission():
    class Redis:
        def __init__(self): self.data = {}
        def set(self, key, value): self.data[key] = value; return True
        def get(self, key): return self.data.get(key)
    r = Redis(); save_schedule(r, 1, policy())
    assert load_schedule(r, 1)['enabled'] and not load_schedule(r, 2)['enabled']
    r.data[PREFIX+'1'] = 'broken'
    with pytest.raises(ValueError): load_schedule(r, 1)
    with pytest.raises(RuntimeError): load_schedule(None, 1)
