"""AUD-T409-UTCDATEBOUNDARY follow-up (2026-09-21) — three daily-snapshot upsert functions
(gex_snapshot.upsert_gex_snapshot, options_flow_snapshot.upsert_options_flow_snapshot,
volume_area.compute_value_area_levels_for_stocks) all defaulted their `as_of` upsert-key
parameter to `datetime.now(timezone.utc).date()` — a naive UTC truncation. Each row is upserted
on ON CONFLICT (stock_id, as_of), so a late/retried/manually-triggered run during the ~4-5 hour
evening window (UTC already past midnight, New York still on the prior calendar day) would
mis-date the snapshot under tomorrow's date instead of today's — the scheduled EOD job
(17:00-17:30 ET) is normally well clear of that window, but any late/retried/manual run is not.

Each function is a small, standalone, DB-argument-taking function — imported directly (these
modules have no heavy import-time DB/session dependency of their own beyond `from db import
...`, already stubbed by conftest.py) rather than source-extracted, since the fix is a one-line
default-argument change best verified by actually calling the function.
"""
from datetime import date, datetime, timezone
from unittest.mock import patch

from src.services import gex_snapshot, options_flow_snapshot, volume_area


class _FrozenDatetime(datetime):
    _frozen: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._frozen.astimezone(tz) if tz else cls._frozen


def _frozen_at(module, utc_iso: str):
    frozen = _FrozenDatetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc)
    _FrozenDatetime._frozen = frozen
    return patch.object(module, "datetime", _FrozenDatetime)


class _FakeSession:
    """sqlalchemy is fully stubbed by conftest.py, so pg_insert(...) is a MagicMock — no real
    statement is ever built or executed. session.add/execute just need to not raise."""
    def add(self, obj):
        pass

    def execute(self, stmt):
        class _Result:
            def scalar_one_or_none(self):
                return None
        return _Result()


def _as_of_passed_to_values(module) -> object:
    """pg_insert(Model).values(**values) — since pg_insert is a MagicMock (conftest.py's
    blanket sqlalchemy stub), the literal kwargs passed to `.values(...)` are inspectable
    directly off the shared mock's call history, no real statement compilation needed."""
    return module.pg_insert.return_value.values.call_args.kwargs["as_of"]


def test_gex_snapshot_defaults_as_of_to_et_today_not_naive_utc():
    """2026-09-18 20:40 ET == 2026-09-19 00:40 UTC — the naive version would key this
    snapshot under Sept 19; the correct trading-day key is Sept 18."""
    session = _FakeSession()
    levels = type("L", (), {
        "call_wall": 1.0, "put_wall": 1.0, "gamma_flip": 1.0, "gamma_magnet": 1.0,
    })()
    with _frozen_at(gex_snapshot, "2026-09-19T00:40:00"):
        gex_snapshot.upsert_gex_snapshot(session, stock_id=1, levels=levels, underlying_close=100.0)
    assert _as_of_passed_to_values(gex_snapshot) == date(2026, 9, 18)


def test_gex_snapshot_respects_an_explicit_as_of_override():
    """An explicitly-passed as_of (e.g. a backfill run) must never be silently replaced."""
    session = _FakeSession()
    levels = type("L", (), {
        "call_wall": 1.0, "put_wall": 1.0, "gamma_flip": 1.0, "gamma_magnet": 1.0,
    })()
    gex_snapshot.upsert_gex_snapshot(
        session, stock_id=1, levels=levels, underlying_close=100.0, as_of=date(2026, 1, 1),
    )
    assert _as_of_passed_to_values(gex_snapshot) == date(2026, 1, 1)


def test_options_flow_snapshot_defaults_as_of_to_et_today_not_naive_utc():
    session = _FakeSession()
    result = options_flow_snapshot.OptionsFlowResult(
        cp_ratio=1.0, cp_ratio_uncapped=1.0, call_volume=1, put_volume=1,
        call_premium=1.0, put_premium=1.0, whale_count=0, top_whale_premium=0.0,
        sentiment="neutral",
    )
    with _frozen_at(options_flow_snapshot, "2026-09-19T00:40:00"):
        options_flow_snapshot.upsert_options_flow_snapshot(session, stock_id=1, result=result)
    assert _as_of_passed_to_values(options_flow_snapshot) == date(2026, 9, 18)


def test_volume_area_defaults_as_of_to_et_today_not_naive_utc_source_check():
    """compute_value_area_levels_for_stocks() itself is established in this suite as "thin
    DB-facing glue" not covered by direct-call tests (test_volume_area.py's own docstring) —
    the `cutoff = datetime.now(timezone.utc) - timedelta(...)` line right below the as_of fix
    compares against a stubbed `Price.ts` MagicMock in this test environment, which isn't a
    real comparison this fix touches anyway. Verified via source-text check instead, matching
    that file's own established boundary for this function."""
    src = pathlib.Path(volume_area.__file__).read_text()
    start = src.index("def compute_value_area_levels_for_stocks(")
    end = src.index("\ndef ", start + 10)
    body = src[start:end]
    assert 'as_of = as_of or datetime.now(ZoneInfo("America/New_York")).date()' in body
    assert "as_of = as_of or datetime.now(timezone.utc).date()" not in body


# ── Source-text regression guard: the naive UTC literal must not survive in any of the three ──

import pathlib

_FILES = [
    "services/market-data/src/services/gex_snapshot.py",
    "services/market-data/src/services/options_flow_snapshot.py",
    "services/market-data/src/services/volume_area.py",
]
_REPO = pathlib.Path(__file__).resolve().parents[3]


def test_none_of_the_three_files_still_default_as_of_to_a_naive_utc_date():
    for relpath in _FILES:
        src = (_REPO / relpath).read_text()
        assert "as_of = as_of or datetime.now(timezone.utc).date()" not in src, (
            f"{relpath}: naive UTC as_of default survived"
        )
        assert 'ZoneInfo("America/New_York")' in src, (
            f"{relpath}: expected the ET-aware replacement, not found"
        )
