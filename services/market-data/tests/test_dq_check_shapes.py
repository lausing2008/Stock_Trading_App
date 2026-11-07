"""AUD-DQ-NAMEONLY (2026-11-01): every _DQ_CHECKS entry must be one the runner can dispatch.

`check_llm_daily_budget` and `capture_prospective_estimates` were registered with only a name
and a description. The runner fell through to `check["query"]`, raised KeyError on both on every
run from 2026-10-05/06 until this fix, and neither job was watched — one of them guards a capture
whose missed days are unrecoverable. The existing tests only checked that the names appeared.

The list holds lambdas (gauge `counter_fn`), so it is read from the AST, not literal_eval.
"""
import ast
import pathlib

_SRC = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py").read_text()

_REQUIRED = {
    None: {"query", "max_age_hours", "is_date"},
    "job_status": {"job_name", "max_age_hours", "is_date"},
    "ratio": {"numerator_key", "denominator_key", "min_ratio", "min_denominator"},
}


def _checks():
    tree = ast.parse(_SRC)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name) and n.target.id == "_DQ_CHECKS")
    out = []
    for d in node.value.elts:
        assert isinstance(d, ast.Dict)
        keys = {k.value: v for k, v in zip(d.keys, d.values)}
        src = keys.get("source")
        out.append((keys["name"].value, set(keys), src.value if src is not None else None))
    return out


def test_the_list_is_found_and_non_trivial():
    assert len(_checks()) > 40


def test_every_check_has_the_keys_its_branch_reads():
    bad = []
    for name, keys, source in _checks():
        assert {"name", "description"} <= keys, name
        if source == "gauge":
            if not ({"counter_key", "counter_fn"} & keys):
                bad.append((name, "gauge needs counter_key or counter_fn"))
            continue
        assert source in _REQUIRED, f"{name}: unknown source {source!r}"
        missing = _REQUIRED[source] - keys
        if missing:
            bad.append((name, sorted(missing)))
    assert bad == []


def test_names_are_unique():
    names = [n for n, _, _ in _checks()]
    assert len(names) == len(set(names))


def test_the_two_previously_name_only_checks_are_now_watched():
    by = {n: (k, s) for n, k, s in _checks()}
    keys, source = by["check_llm_daily_budget"]
    assert source == "job_status" and "'llm_daily_budget_check'" not in keys
    assert 'id="llm_daily_budget_check"' in _SRC or "id='llm_daily_budget_check'" in _SRC
    seg = _SRC[_SRC.index('"name": "capture_prospective_estimates"'):][:600]
    assert "FROM estimate_snapshots" in seg and "captured_at" in seg


def test_the_capture_window_allows_for_a_midnight_stamped_daily_run():
    """captured_at is the capture DAY at 00:00 and the run is at 11:00 UTC, so a healthy series
    is up to 35h old just before the next run; anything under that alarms every morning."""
    seg = _SRC[_SRC.index('"name": "capture_prospective_estimates"'):][:600]
    max_age = int(seg.split('"max_age_hours":')[1].split(",")[0])
    assert 35 < max_age <= 40


# ── 2026-11-07 (review of AUD-DQ-NAMEONLY): checks that mean something ──────────────────────

def _recorded_job_names() -> tuple[set[str], list[str]]:
    """Literal job names passed to _record_job_status() anywhere in the service, plus f-string
    prefixes (some jobs, e.g. conditional_orders.py, record their own status)."""
    names, prefixes = set(), []
    src_root = pathlib.Path(__file__).resolve().parents[1] / "src"
    trees = [ast.parse(f.read_text()) for f in src_root.rglob("*.py")]
    for n in (n for t in trees for n in ast.walk(t)):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_record_job_status" and n.args:
            a = n.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                names.add(a.value)
            elif isinstance(a, ast.JoinedStr):
                head = a.values[0]
                if isinstance(head, ast.Constant):
                    prefixes.append(head.value)
    return names, prefixes


def _job_names_by_check() -> dict[str, str]:
    tree = ast.parse(_SRC)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name) and n.target.id == "_DQ_CHECKS")
    out = {}
    for d in node.value.elts:
        keys = {k.value: v for k, v in zip(d.keys, d.values)}
        if "job_name" in keys:
            out[keys["name"].value] = keys["job_name"].value
    return out


def test_the_budget_check_watches_the_key_its_job_writes():
    assert _job_names_by_check()["check_llm_daily_budget"] == "llm_daily_budget_check"


def test_every_job_status_check_names_a_job_that_records_status():
    names, prefixes = _recorded_job_names()
    unknown = [(c, j) for c, j in _job_names_by_check().items()
               if j not in names and not any(j.startswith(p) for p in prefixes if p)]
    assert unknown == [], "a job_status check reading a key nothing writes always reports stale"


# ── The runner itself, extracted and run against stand-ins ───────────────────────────────────

import json as _json
import types as _types
from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz


class _FakeRedis:
    def __init__(self, d=None):
        self.d = dict(d or {})

    def get(self, k):
        return self.d.get(k)

    def setex(self, k, ttl, v):
        self.d[k] = v

    def set(self, k, v):
        self.d[k] = v


def _run(checks, redis, query_results):
    """Run the real run_data_quality_checks over `checks`; query_results maps SQL -> value."""
    import time as _time
    fn_src = next(ast.get_source_segment(_SRC, n) for n in ast.parse(_SRC).body
                  if isinstance(n, ast.FunctionDef) and n.name == "run_data_quality_checks")
    statuses, logs = [], []

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, q):
            if isinstance(q, str):
                v = query_results[q]
                if isinstance(v, Exception):
                    raise v
                return _types.SimpleNamespace(scalar=lambda: v)
            return _types.SimpleNamespace(scalars=lambda: _types.SimpleNamespace(all=lambda: []))

        def rollback(self):
            pass

    class _Q:
        def where(self, *a):
            return self

    class _UserCol:
        def isnot(self, _):
            return True

        def __ne__(self, _):
            return True

    ns = {
        "time": _time, "json": _json, "datetime": _dt, "timezone": _tz,
        "_get_redis": lambda: redis, "SessionLocal": _Session, "_DQ_CHECKS": checks,
        "text": lambda q: q, "_is_us_trading_day": lambda: True, "_is_hk_holiday": lambda: False,
        "select": lambda *a: _Q(), "User": _types.SimpleNamespace(email=_UserCol()),
        "send_data_quality_alert_email": lambda *a: False,
        "_record_job_status": lambda *a: statuses.append(a),
        "log": _types.SimpleNamespace(
            info=lambda *a, **k: logs.append((a, k)), warning=lambda *a, **k: logs.append((a, k)),
            error=lambda *a, **k: logs.append((a, k))),
    }
    exec(fn_src, ns)
    ns["run_data_quality_checks"]()
    results = {k[len("dq_check:"):]: _json.loads(v) for k, v in redis.d.items()
               if k.startswith("dq_check:") and not k.startswith("dq_check:last_")}
    return results, statuses, logs


def _real_check(name):
    tree = ast.parse(_SRC)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name) and n.target.id == "_DQ_CHECKS")
    for d in node.value.elts:
        keys = {k.value: v for k, v in zip(d.keys, d.values)}
        if keys["name"].value == name:
            return ast.literal_eval(d)
    raise AssertionError(name)


def test_the_capture_check_passes_a_healthy_midnight_stamp_and_fails_a_missed_day():
    check = _real_check("capture_prospective_estimates")
    # A healthy series is at most 35 h old (yesterday's midnight stamp, just before today's
    # 11:00 UTC run); a missed day pushes it past 59 h.
    now = _dt.now(_tz.utc).replace(tzinfo=None)
    res, _, _ = _run([check], _FakeRedis(), {check["query"]: now - _td(hours=35)})
    assert res["capture_prospective_estimates"]["ok"] is True
    res, _, _ = _run([check], _FakeRedis(), {check["query"]: now - _td(hours=59)})
    assert res["capture_prospective_estimates"]["ok"] is False


def test_the_budget_check_reads_the_job_record_for_freshness():
    check = _real_check("check_llm_daily_budget")
    key = f"scheduler:job:{check['job_name']}"
    fresh = _json.dumps({"last_run": _dt.now(_tz.utc).isoformat()})
    stale = _json.dumps({"last_run": (_dt.now(_tz.utc) - _td(hours=5)).isoformat()})
    assert _run([check], _FakeRedis({key: fresh}), {})[0]["check_llm_daily_budget"]["ok"] is True
    assert _run([check], _FakeRedis({key: stale}), {})[0]["check_llm_daily_budget"]["ok"] is False
    assert _run([check], _FakeRedis(), {})[0]["check_llm_daily_budget"]["ok"] is False


def test_a_raising_gauge_is_recorded_and_later_checks_still_run():
    def boom():
        raise RuntimeError("redis gone")
    checks = [
        {"name": "g", "description": "gauge", "source": "gauge", "counter_fn": boom},
        {"name": "t", "description": "table", "query": "Q", "max_age_hours": 2, "is_date": False},
    ]
    res, statuses, logs = _run(checks, _FakeRedis(), {"Q": _dt.now(_tz.utc)})
    assert res["t"]["ok"] is True, "the check after the raising gauge must still run"
    assert not any(a and a[0] == "dq_check.run_failed" for a, _ in logs)
    assert statuses and statuses[-1][1] == "error"  # 1 of 2 errored, the half threshold
