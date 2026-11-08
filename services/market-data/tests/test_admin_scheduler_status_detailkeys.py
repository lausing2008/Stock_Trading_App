"""AUD-SCHEDSTATUS-DETAILKEYS: /admin/scheduler-status must return only job-status records.

Detail reports share the `scheduler:job:*` prefix; returning them crashed the System Health
page (`job.status.startsWith` on a record with no status).
"""
import json
from unittest.mock import MagicMock

from src.api import admin


class _FakeRedis:
    def __init__(self, store):
        self._store = store

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self._store if k.startswith(prefix)]

    def get(self, key):
        return self._store.get(key)


def test_detail_reports_and_malformed_values_are_excluded(monkeypatch):
    store = {
        "scheduler:job:us_5m_reconcile": json.dumps(
            {"job": "us_5m_reconcile", "status": "ok", "last_run": "2026-11-06T21:00:00+00:00",
             "duration_s": 3.2, "error": None}),
        "scheduler:job:us_5m_reconcile:detail": json.dumps(
            {"market": "US", "session": "2026-11-06", "pass": 2, "symbols": 15}),
        "scheduler:job:hk_5m_reconcile:detail:2026-11-05:p1": json.dumps(
            {"market": "HK", "session": "2026-11-05", "pass": 1}),
        "scheduler:job:options_flow_eod:detail": json.dumps(
            {"expected_session": "2026-11-05", "written": ["IONQ"]}),
        "scheduler:job:broken_json": "{not json",
        "scheduler:job:list_value": json.dumps([1, 2]),
        "scheduler:job:null_status": json.dumps({"job": "x", "status": None}),
    }
    monkeypatch.setattr(admin, "_get_redis", lambda: _FakeRedis(store))

    jobs = admin.scheduler_status(MagicMock())["jobs"]

    assert [j["job"] for j in jobs] == ["us_5m_reconcile"]
    assert all(isinstance(j["status"], str) for j in jobs)
