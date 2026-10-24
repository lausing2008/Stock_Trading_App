"""Explicit research bounds, not optimized strategy parameters."""
import math


def normalize_preferences(values=None):
    values = values or {}
    result = {}
    for name in ('min_dte', 'max_dte', 'min_abs_delta', 'max_abs_delta'):
        value = values.get(name)
        limit = 1095 if name.endswith('dte') else 1
        floor = 1 if name.endswith('dte') else 0
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not floor <= value <= limit
                or (name.endswith('dte') and int(value) != value)):
            raise ValueError(f'{name} must be {floor}–{limit}' + (' whole calendar days' if name.endswith('dte') else ''))
        result[name] = value
    for low, high in (('min_dte', 'max_dte'), ('min_abs_delta', 'max_abs_delta')):
        if result[low] is not None and result[high] is not None and result[low] > result[high]:
            raise ValueError(f'{low} cannot exceed {high}')
    return result


def delta_rejection(value, right, preferences):
    low, high = preferences.get('min_abs_delta'), preferences.get('max_abs_delta')
    if low is None and high is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1:
        return 'delta_unavailable_or_invalid'
    if (right == 'call' and value < 0) or (right == 'put' and value > 0):
        return 'delta_sign_mismatch'
    if (low is not None and abs(value) < low) or (high is not None and abs(value) > high):
        return 'outside_abs_delta_range'
    return None
