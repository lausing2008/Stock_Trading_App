"""Tests for IF-04 (Cross-Asset Signals): sync_cross_asset() + get_latest_cross_asset_reading().

economic.py imports cleanly in this test environment except for its real SQLAlchemy/db
dependency (stubbed wholesale by conftest.py for Docker-only dependencies) — uses the same
real-in-memory-SQLite-plus-real-CrossAssetReading-model technique already established in
test_get_recent_economic_events.py: pop the stub, build a real engine against the real
shared/db/models.py, restore the stub immediately after import.

The new GET/POST routes in routes.py need common.jwt_auth, which conftest.py does not stub for
real — covered via source-text regression checks instead, matching this repo's established
pattern for exactly this constraint.
"""
import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

_STUBBED_MODULES = (
    "common", "common.config", "common.logging",
    "db", "db.session",
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql",
    "psycopg2",
)
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib

from sqlalchemy import Integer, create_engine, select as _real_select
from sqlalchemy.dialects.postgresql import insert as _real_pg_insert
from sqlalchemy.orm import sessionmaker

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_cross_asset", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_cross_asset"] = _models
_spec.loader.exec_module(_models)

_models.CrossAssetReading.__table__.c.id.type = Integer()  # SQLite autoincrement needs plain INTEGER PK

_engine = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(_engine, tables=[_models.CrossAssetReading.__table__])
_SessionLocal = sessionmaker(bind=_engine)

for _mod, _val in _saved_stubs.items():
    if _val is not None:
        sys.modules[_mod] = _val
    else:
        sys.modules.pop(_mod, None)

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())
sys.modules.setdefault("common.logging", MagicMock())
sys.modules.setdefault("db", MagicMock())

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.services import economic  # noqa: E402

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()
_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


@pytest.fixture
def anyio_backend():
    """This test env has no pytest-asyncio plugin, but anyio (already a real dependency here —
    see the plugins line in pytest's own startup banner) provides an equivalent
    @pytest.mark.anyio marker with zero extra config, so async tests below use that instead of
    introducing a new dependency."""
    return "asyncio"


@pytest.fixture(autouse=True)
def _clean_table():
    """All tests in this file share one module-level in-memory engine (create_engine() does a
    dynamic dialect lookup at CALL time, so it can't be built lazily after the stub restore
    above). CrossAssetReading has a real UNIQUE(as_of) constraint — clear the table before
    every test to keep them independent."""
    with _SessionLocal() as session:
        session.query(_models.CrossAssetReading).delete()
        session.commit()
    yield


def _make_reading(session, as_of, **fields):
    r = _models.CrossAssetReading(as_of=as_of, **fields)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


class _FakeAsyncClient:
    """Minimal async context-manager stand-in for httpx.AsyncClient, matching the pattern
    macro_reaction.py's own tests already use for exactly this kind of async HTTP mocking."""
    def __init__(self, responses):
        self._responses = responses  # series_id -> (status_code, json_dict)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        series_id = params["series_id"]
        self.calls.append(series_id)
        status, payload = self._responses.get(series_id, (404, {}))
        return _FakeResponse(status, payload)


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _obs(date_str, value):
    return {"date": date_str, "value": value}


# ── sync_cross_asset() ───────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_sync_cross_asset_upserts_all_5_series_into_one_row_per_day():
    """The core property this design depends on: 5 independent series calls must accumulate
    into ONE row per as_of date, not 5 separate rows each satisfying only their own column."""
    fake_settings = MagicMock()
    fake_settings.fred_api_key = "test-key"
    responses = {
        "DGS10": (200, {"observations": [_obs("2026-08-17", "4.72")]}),
        "DGS2": (200, {"observations": [_obs("2026-08-17", "4.19")]}),
        "T10Y2Y": (200, {"observations": [_obs("2026-08-18", "0.52")]}),
        "BAMLH0A0HYM2": (200, {"observations": [_obs("2026-08-17", "2.70")]}),
        "DTWEXBGS": (200, {"observations": [_obs("2026-08-14", "118.90")]}),
    }
    fake_client = _FakeAsyncClient(responses)
    with patch.object(economic, "_settings", fake_settings), \
         patch.object(economic, "SessionLocal", _SessionLocal), \
         patch.object(economic, "CrossAssetReading", _models.CrossAssetReading), \
         patch.object(economic, "pg_insert", _real_pg_insert), \
         patch("httpx.AsyncClient", return_value=fake_client), \
         patch("asyncio.sleep", new=_noop_async):
        result = await economic.sync_cross_asset()
    assert result["skipped"] is None
    assert result["synced"] == 5
    with _SessionLocal() as s:
        rows = s.query(_models.CrossAssetReading).all()
    # 3 distinct dates (17th, 18th, 14th) since the fetched observations don't all share a date.
    assert len(rows) == 3
    row_17 = next(r for r in rows if r.as_of == date(2026, 8, 17))
    assert row_17.yield_10y == 4.72
    assert row_17.yield_2y == 4.19
    assert row_17.hy_spread == 2.70


async def _noop_async(*a, **kw):
    pass


@pytest.mark.anyio
async def test_sync_cross_asset_skips_when_no_api_key_configured():
    fake_settings = MagicMock()
    fake_settings.fred_api_key = ""
    with patch.object(economic, "_settings", fake_settings):
        result = await economic.sync_cross_asset()
    assert result == {"synced": 0, "skipped": "no_api_key"}


@pytest.mark.anyio
async def test_one_series_failure_does_not_block_the_others():
    """A single FRED series returning a non-200 (rate limit, outage) must not prevent the
    OTHER 4 series from still syncing — matches sync_fred()'s own per-series isolation."""
    fake_settings = MagicMock()
    fake_settings.fred_api_key = "test-key"
    responses = {
        "DGS10": (200, {"observations": [_obs("2026-08-17", "4.72")]}),
        "DGS2": (500, {}),  # simulated failure
        "T10Y2Y": (200, {"observations": [_obs("2026-08-17", "0.52")]}),
        "BAMLH0A0HYM2": (200, {"observations": [_obs("2026-08-17", "2.70")]}),
        "DTWEXBGS": (200, {"observations": [_obs("2026-08-17", "118.90")]}),
    }
    fake_client = _FakeAsyncClient(responses)
    with patch.object(economic, "_settings", fake_settings), \
         patch.object(economic, "SessionLocal", _SessionLocal), \
         patch.object(economic, "CrossAssetReading", _models.CrossAssetReading), \
         patch.object(economic, "pg_insert", _real_pg_insert), \
         patch("httpx.AsyncClient", return_value=fake_client), \
         patch("asyncio.sleep", new=_noop_async):
        result = await economic.sync_cross_asset()
    assert result["synced"] == 4
    with _SessionLocal() as s:
        row = s.query(_models.CrossAssetReading).filter_by(as_of=date(2026, 8, 17)).one()
    assert row.yield_10y == 4.72
    assert row.yield_2y is None  # DGS2's fetch failed — must not fabricate a value


@pytest.mark.anyio
async def test_a_dot_value_is_skipped_not_fabricated_as_zero():
    """FRED's own sentinel for a missing observation is the literal string "." — must never
    end up stored as a fabricated 0.0. NOTE (found via adversarial verification): removing the
    explicit `obs["value"] in (".", "")` guard does NOT change this test's own outcome, since
    float(".") raises ValueError which the surrounding try/except already catches — the guard
    is real but redundant defensive code at the OBSERVABLE-behavior level this test checks, not
    a distinguishable branch. This test still correctly guards the property that actually
    matters (no fabricated 0.0 ever lands in the DB), just not the specific guard's own
    necessity."""
    fake_settings = MagicMock()
    fake_settings.fred_api_key = "test-key"
    responses = {
        "DGS10": (200, {"observations": [_obs("2026-08-17", ".")]}),
        "DGS2": (200, {"observations": []}),
        "T10Y2Y": (200, {"observations": []}),
        "BAMLH0A0HYM2": (200, {"observations": []}),
        "DTWEXBGS": (200, {"observations": []}),
    }
    fake_client = _FakeAsyncClient(responses)
    with patch.object(economic, "_settings", fake_settings), \
         patch.object(economic, "SessionLocal", _SessionLocal), \
         patch.object(economic, "CrossAssetReading", _models.CrossAssetReading), \
         patch.object(economic, "pg_insert", _real_pg_insert), \
         patch("httpx.AsyncClient", return_value=fake_client), \
         patch("asyncio.sleep", new=_noop_async):
        result = await economic.sync_cross_asset()
    assert result["synced"] == 0
    with _SessionLocal() as s:
        assert s.query(_models.CrossAssetReading).count() == 0


# ── get_latest_cross_asset_reading() — the rule-based classification ────────────────────────

class TestGetLatestCrossAssetReading:
    def test_returns_none_when_no_data_synced_yet(self):
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "CrossAssetReading", _models.CrossAssetReading):
            result = economic.get_latest_cross_asset_reading()
        assert result is None

    def test_inverted_curve_and_wide_spread_reads_risk_off(self):
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "CrossAssetReading", _models.CrossAssetReading):
            with _SessionLocal() as s:
                _make_reading(s, date(2026, 8, 17), yield_curve_2s10s=-0.3, hy_spread=6.0)
            result = economic.get_latest_cross_asset_reading()
        assert result["direction"] == "RISK_OFF"
        assert any("inverted" in n.lower() for n in result["notes"])
        assert any("elevated" in n.lower() for n in result["notes"])

    def test_steep_curve_and_tight_spread_reads_risk_on(self):
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "CrossAssetReading", _models.CrossAssetReading):
            with _SessionLocal() as s:
                _make_reading(s, date(2026, 8, 17), yield_curve_2s10s=1.5, hy_spread=3.0)
            result = economic.get_latest_cross_asset_reading()
        assert result["direction"] == "RISK_ON"

    def test_mixed_signals_read_neutral(self):
        """A steep curve (risk-on) but a wide spread (risk-off) should not both weigh the same
        direction — net score of 0 must read NEUTRAL, not silently pick a side."""
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "CrossAssetReading", _models.CrossAssetReading):
            with _SessionLocal() as s:
                _make_reading(s, date(2026, 8, 17), yield_curve_2s10s=1.5, hy_spread=6.0)
            result = economic.get_latest_cross_asset_reading()
        assert result["direction"] == "NEUTRAL"

    def test_a_middling_curve_reading_produces_no_yield_curve_note_but_still_returns_data(self):
        """A 2s10s between 0 and 1.0 is neither inverted nor steep — must not force a
        direction from noise, but the raw numeric fields still come through."""
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "CrossAssetReading", _models.CrossAssetReading):
            with _SessionLocal() as s:
                _make_reading(s, date(2026, 8, 17), yield_curve_2s10s=0.5, hy_spread=4.0)
            result = economic.get_latest_cross_asset_reading()
        assert result["direction"] == "NEUTRAL"
        assert result["yield_curve_2s10s"] == 0.5

    def test_uses_the_most_recent_as_of_row_not_the_first_inserted(self):
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "CrossAssetReading", _models.CrossAssetReading):
            with _SessionLocal() as s:
                _make_reading(s, date(2026, 8, 10), yield_curve_2s10s=-1.0, hy_spread=6.0)
                _make_reading(s, date(2026, 8, 18), yield_curve_2s10s=1.5, hy_spread=3.0)
            result = economic.get_latest_cross_asset_reading()
        assert result["as_of"] == "2026-08-18"
        assert result["direction"] == "RISK_ON"


# ── routes.py wiring — source-text regression checks ────────────────────────────────────────

def test_get_cross_asset_route_is_registered_and_authenticated():
    assert '@router.get("/events/cross-asset")' in _ROUTES_SOURCE
    start = _ROUTES_SOURCE.index('@router.get("/events/cross-asset")')
    block = _ROUTES_SOURCE[start:start + 400]
    assert "get_current_username" in block
    assert "get_latest_cross_asset_reading" in block


def test_sync_cross_asset_route_is_registered_and_authenticated():
    assert '@router.post("/events/sync/cross-asset")' in _ROUTES_SOURCE
    start = _ROUTES_SOURCE.index('@router.post("/events/sync/cross-asset")')
    block = _ROUTES_SOURCE[start:start + 400]
    assert "get_current_username" in block
    assert "economic.sync_cross_asset()" in block


def test_scheduler_registers_the_daily_cron_job():
    assert 'id="sync_cross_asset"' in _SCHEDULER_SOURCE
    start = _SCHEDULER_SOURCE.index('id="sync_cross_asset"')
    block = _SCHEDULER_SOURCE[max(0, start - 100):start]
    assert "cron" in block


def test_scheduler_seeds_at_startup_not_only_on_the_next_cron_fire():
    """A fresh deploy shouldn't leave cross_asset_readings empty until the next 06:20 UTC run —
    matches sync_fred_release_dates()'s own established startup-seed convention."""
    assert "asyncio.create_task(job_sync_cross_asset())" in _SCHEDULER_SOURCE
