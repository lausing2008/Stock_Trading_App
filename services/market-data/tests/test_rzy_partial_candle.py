"""AUD-RZY-PARTIALCANDLE B: Little RZY consumes an intraday bar only after a write after its close.

Ingest stores the newest bar while it is forming. When the next run was skipped (UW cooldown) or
failed, that snapshot outlived its close and was consumed as completed; the later correction then
rebuilt every affected stream (docs/2026-11-06/rzy-rebuild-attribution.md). bar_provenance
records each write's time and values, including an unchanged re-send after completion.
"""
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import importlib.util

import pytest


def _helper(name):
    """The shipped pure helpers, without the suite's container-dependency common stub."""
    spec = importlib.util.spec_from_file_location('rzy_partial_' + name, pathlib.Path(__file__).resolve().parents[3] / 'shared/common' / f'{name}.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


session_bounds = _helper('market_calendar').session_bounds
resolve_ohlc = _helper('price_basis').resolve_ohlc
from src.services import rzy_data as D  # noqa: E402

OPEN = datetime(2026, 10, 16, 13, 30, tzinfo=timezone.utc)          # Fri 09:30 ET
PREV = datetime(2026, 10, 15, 13, 30, tzinfo=timezone.utc)          # Thu 09:30 ET


def _row(t, c=100.0):
    return SimpleNamespace(ts=t.replace(tzinfo=None), open=c - .2, high=c + .6, low=c - .8, close=c,
                           adj_close=None, volume=1000.0, session='REGULAR')


def _bars(start, n):
    return [_row(start + timedelta(minutes=5 * i), 100 + i * .1) for i in range(n)]


def _written(rows, override=None):
    """Provenance as ingest leaves it: every bar written 20 s after its close, same values."""
    out = {}
    for r in rows:
        out[r.ts] = (r.ts + timedelta(minutes=5, seconds=20), (r.open, r.high, r.low, r.close))
    out.update(override or {})
    return out


def _prep(rows, now, written_at):
    return D.prepare_bars(rows, '5m', now, session_bounds=session_bounds, resolve_ohlc=resolve_ohlc, written_at=written_at)


def test_bars_written_after_their_close_are_consumed_unchanged():
    rows = _bars(OPEN, 12); now = OPEN + timedelta(minutes=63)
    bars, ev = _prep(rows, now, _written(rows))
    assert len(bars) == 12 and ev['held_until_post_close_write'] == 0 and ev['status'] == 'research_only'


def test_a_head_written_only_while_forming_is_held_and_the_stream_reads_stale():
    rows = _bars(OPEN, 12); now = OPEN + timedelta(minutes=63)
    head = rows[-1].ts
    bars, ev = _prep(rows, now, _written(rows, {head: (head + timedelta(seconds=27), (rows[-1].open, rows[-1].high, rows[-1].low, rows[-1].close))}))
    assert len(bars) == 11 and ev['held_until_post_close_write'] == 1 and ev['status'] == 'stale'
    assert {'time': OPEN.replace(minute=25, hour=14).isoformat(), 'reason': 'not_written_after_close'} in ev['exclusions']
    assert not ev['gaps'], 'a held tail is not a gap'


def test_an_unconfirmed_middle_bar_holds_everything_after_it_without_a_gap():
    rows = _bars(OPEN, 12); now = OPEN + timedelta(minutes=63)
    mid = rows[5]
    bars, ev = _prep(rows, now, _written(rows, {mid.ts: (mid.ts + timedelta(minutes=4), (mid.open, mid.high, mid.low, mid.close))}))
    assert len(bars) == 5 and ev['held_until_post_close_write'] == 7 and not ev['gaps']


def test_a_bar_without_provenance_is_held():
    rows = _bars(OPEN, 6); now = OPEN + timedelta(minutes=33)
    w = _written(rows); w.pop(rows[-1].ts)
    bars, ev = _prep(rows, now, w)
    assert len(bars) == 5 and ev['held_until_post_close_write'] == 1


def test_a_write_between_the_two_reads_is_held():
    """The price read is the partial value; provenance (read after) already shows the final one."""
    rows = _bars(OPEN, 6); now = OPEN + timedelta(minutes=33)
    head = rows[-1]
    final = (head.open, head.high + 2.0, head.low, head.close + 1.5)
    bars, ev = _prep(rows, now, _written(rows, {head.ts: (head.ts + timedelta(minutes=5, seconds=30), final)}))
    assert len(bars) == 5 and ev['held_until_post_close_write'] == 1


def test_only_the_latest_session_is_checked():
    """Thursday's bars predate retained provenance or were completed long ago: never re-checked."""
    rows = _bars(PREV, 78) + _bars(OPEN, 6); now = OPEN + timedelta(minutes=33)
    w = _written(rows[78:])
    bars, ev = _prep(rows, now, w)
    assert len(bars) == 84 and ev['held_until_post_close_write'] == 0


def test_without_write_times_nothing_changes():
    """Replays and backtests call prepare_bars without provenance."""
    rows = _bars(OPEN, 12); now = OPEN + timedelta(minutes=63)
    bars, ev = D.prepare_bars(rows, '5m', now, session_bounds=session_bounds, resolve_ohlc=resolve_ohlc)
    assert len(bars) == 12 and ev['held_until_post_close_write'] == 0


def test_daily_bars_are_never_held():
    rows = [SimpleNamespace(ts=datetime(2026, 10, d), open=1, high=2, low=.5, close=1.5, adj_close=None, volume=1, session='REGULAR')
            for d in (13, 14, 15)]
    bars, ev = D.prepare_bars(rows, '1d', OPEN, session_bounds=session_bounds, resolve_ohlc=resolve_ohlc, written_at={})
    assert len(bars) == 3


def test_a_15m_bin_with_a_held_member_waits_and_is_not_a_gap():
    rows = _bars(OPEN, 6); now = OPEN + timedelta(minutes=33)
    head = rows[-1]
    src, ev = _prep(rows, now, _written(rows, {head.ts: (head.ts + timedelta(seconds=27), (head.open, head.high, head.low, head.close))}))
    bars, ev15 = D.aggregate_completed_bars(src, ev, '15m', now, session_bounds=session_bounds)
    assert len(bars) == 1
    assert any(x['reason'] == 'awaiting_post_close_write' for x in ev15['exclusions'])
    assert not any(g['reason'] == 'missing_aggregate_constituents' for g in ev15['gaps'])


URL = os.environ.get('RZY_PG_URL')


@pytest.fixture(scope='module')
def evidence():
    run = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name('_rzy_partial_candle_probe.py'))],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-4000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_real_workflow_holds_the_partial_candle(evidence):
    h = evidence['held_5m']
    assert h['held'] == 1 and h['status'] == 'stale' and h['last'] == h['expected_last']
    assert evidence['held_15m'] == {'awaiting': True, 'gap': False}


def test_real_workflow_consumes_the_completed_candle_without_a_rebuild(evidence):
    c = evidence['completed']
    assert c == {'rebuilt_5m': False, 'rebuilt_15m': False, 'last': '2026-10-16T18:25:00+00:00',
                 'high': 113.0, 'rebuild_rows': 0}


def test_real_reader_takes_the_latest_regular_session_not_a_premarket_head(evidence):
    """At 08:40 ET the newest stored 5m bar is premarket; the check must cover the last session."""
    assert evidence['reader'] == {'premarket_head': 60, 'regular_only': 60}


def test_real_workflow_last_hour_waits_for_the_post_close_write(evidence):
    assert evidence['close_1h_held'] == {'last_closed': '2026-10-16T19:30:00+00:00', 'awaiting': True}
    assert evidence['close_1h_done'] == {'last_open': '2026-10-16T19:30:00+00:00',
                                         'last_closed': '2026-10-16T20:00:00+00:00'}
