"""Regression: a cross on a forming daily candle must not be emailed as confirmed."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calendar = load_file('real_calendar', ROOT / 'shared/common/market_calendar.py')
with patch.dict('sys.modules', {'common.market_calendar': calendar}):
    cross = load_file('daily_cross_tested', ROOT / 'services/market-data/src/services/daily_cross.py')


def rows(last=100):
    dates = pd.bdate_range(end='2026-10-22', periods=300)
    return [SimpleNamespace(ts=d.to_pydatetime(), close=last if i == 299 else 10)
            for i, d in enumerate(dates)]


def test_no_golden_cross_from_forming_daily_candle():
    assert cross.daily_ema_cross(rows(), 'US', datetime(2026, 10, 22, 13, 43, tzinfo=timezone.utc), 'above') is None


def test_close_buffer_and_daily_evidence():
    assert cross.daily_ema_cross(rows(), 'US', datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc), 'above') is None
    label, fast, price, end = cross.daily_ema_cross(rows(), 'US', datetime(2026, 10, 22, 20, 15, tzinfo=timezone.utc), 'above')
    assert 'daily candle 2026-10-22' in label and 'crossed above' in label
    assert price == 100 and fast > 10
    assert end.hour == 16  # exchange local time, DST handled by calendar


def test_downward_cross_is_death_cross_only():
    now = datetime(2026, 10, 22, 21, tzinfo=timezone.utc)
    assert cross.daily_ema_cross(rows(1), 'US', now, 'above') is None
    assert 'Death Cross' in cross.daily_ema_cross(rows(1), 'US', now, 'below')[0]


def test_insufficient_history_is_not_a_cross():
    assert cross.daily_ema_cross(rows()[-200:], 'US', datetime(2026, 10, 22, 21, tzinfo=timezone.utc), 'above') is None


def test_hk_close_is_separate_from_us_close():
    now = datetime(2026, 10, 22, 8, 15, tzinfo=timezone.utc)
    assert cross.daily_ema_cross(rows(), 'HK', now, 'above') is not None
    assert cross.daily_ema_cross(rows(), 'US', now, 'above') is None


def test_short_recursive_seed_must_not_invent_a_new_cross():
    dates = pd.bdate_range(end='2026-10-22', periods=755)
    values = [5] * 495 + [20] + [15] * 258 + [30]
    data = [SimpleNamespace(ts=d.to_pydatetime(), close=v) for d, v in zip(dates, values)]
    short = pd.Series(values[-260:])
    spread = short.ewm(span=50, adjust=False).mean() - short.ewm(span=200, adjust=False).mean()
    assert spread.iloc[-2] < 0 < spread.iloc[-1]  # reproduces the old false event
    assert cross.daily_ema_cross(data, 'US', datetime(2026, 10, 22, 21, tzinfo=timezone.utc), 'above') is None
