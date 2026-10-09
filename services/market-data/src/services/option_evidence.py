"""Source evidence rules: ambiguity is not an instruction to pick the first row."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def select_expiry_gex_row(data, today=None):
    """For expiry-grouped endpoints only. Ticker-wide GEX levels must never call this."""
    if isinstance(data, dict):
        return data
    if not isinstance(data, list) or not data:
        return None
    if len(data) == 1:
        return data[0] if isinstance(data[0], dict) else None
    today = today or datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York')).date()
    dated = []
    for row in data:
        try:
            expiry = date.fromisoformat(row['expiry'])
        except (KeyError, TypeError, ValueError):
            return None
        if expiry >= today:
            dated.append((expiry, row))
    if not dated:
        return None
    nearest = min(d for d, _ in dated)
    matches = [row for d, row in dated if d == nearest]
    return matches[0] if len(matches) == 1 else None


def dark_pool_qualifies(premium, baseline, minimum, multiple):
    """Two thresholds, independent of the later inference about direction or profitability."""
    return premium >= minimum and (baseline is None or premium >= baseline * multiple)
