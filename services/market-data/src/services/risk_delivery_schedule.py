"""Per-account quiet hours for the opt-in price-risk EMAIL channel only.

Schedules live in persistent Redis alongside other user settings; no expiry is applied.
An unreadable schedule is unknown, never permission to send. Callers defer the outbox row.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import re

PREFIX = 'stockai:user:risk_email_schedule:'
DEFAULT = {'enabled': False, 'timezone': 'UTC', 'start': '22:00', 'end': '07:00'}


def validate_schedule(value):
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ValueError('enabled must be true or false')
    out = {k: value.get(k) for k in DEFAULT}
    for field in ('start', 'end'):
        if not isinstance(out[field], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', out[field]):
            raise ValueError(f'{field} must be HH:MM in 24-hour time')
    try:
        if not isinstance(out['timezone'], str) or len(out['timezone']) > 80:
            raise ValueError('timezone must be an IANA timezone')
        ZoneInfo(out['timezone'])
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('timezone must be a supported IANA timezone') from None
    if out['enabled'] and out['start'] == out['end']:
        raise ValueError('quiet-hour start and end must differ')
    return out


def load_schedule(redis_client, user_id):
    if redis_client is None:
        raise RuntimeError('quiet-hour preference source unavailable')
    raw = redis_client.get(PREFIX + str(user_id))
    return dict(DEFAULT) if raw is None else validate_schedule(json.loads(raw))


def save_schedule(redis_client, user_id, value):
    out = validate_schedule(value)
    if redis_client is None or not redis_client.set(PREFIX + str(user_id), json.dumps(out)):
        raise RuntimeError('quiet-hour preferences could not be saved')
    return out


def is_quiet(value, now):
    value = validate_schedule(value)
    if not value['enabled']:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(ZoneInfo(value['timezone']))
    clock = local.strftime('%H:%M')
    start, end = value['start'], value['end']
    return start <= clock < end if start < end else clock >= start or clock < end
