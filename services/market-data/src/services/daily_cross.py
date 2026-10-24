"""Daily EMA cross evidence, excluding the session's unfinished candle."""
from datetime import datetime, timedelta

import pandas as pd
from common.market_calendar import session_close


def daily_ema_cross(rows, market: str, now: datetime, direction: str):
    # A short settlement buffer also avoids evaluating during the closing refresh.
    completed = [r for r in rows if (end := session_close(market, r.ts.date()))
                 is not None and end + timedelta(minutes=15) <= now]
    if len(completed) < 201:
        return None  # both crossing observations need a 200-bar warmup
    close = pd.Series([float(r.close) for r in completed[-1000:]])
    fast = close.ewm(span=50, adjust=False).mean()
    slow = close.ewm(span=200, adjust=False).mean()
    before, after = fast.iloc[-2] - slow.iloc[-2], fast.iloc[-1] - slow.iloc[-1]
    crossed = before <= 0 < after if direction == 'above' else before >= 0 > after
    if not crossed:
        return None
    bar = completed[-1]
    name = 'Golden Cross' if direction == 'above' else 'Death Cross'
    label = (f'{name} — daily candle {bar.ts.date().isoformat()} — '
             f'EMA50 ({fast.iloc[-1]:.6f}) crossed {direction} EMA200 ({slow.iloc[-1]:.6f})')
    return label, float(fast.iloc[-1]), float(close.iloc[-1]), session_close(market, bar.ts.date())
