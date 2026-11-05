"""AUD-SIG-REFRESHOVERLAP: one signal refresh pass per market at a time.

market-data posts /signals/refresh every 5 minutes and a US pass takes ~9, so every request
used to start another full pass; on 2026-11-04 about a dozen ran at once and exhausted
market-data's connection pool. routes.py cannot be imported here (conftest stubs `common`/`db`),
so the real refresh_signals and _guarded_refresh are compiled from the source and run against
fakes.
"""
import ast
import collections
import pathlib
import threading
import time
import types

SRC = (pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py").read_text()


class _Log:
    def __init__(self):
        self.events = []

    def info(self, event, **kw):
        self.events.append((event, kw))

    warning = error = info


class _Q:
    def where(self, *a):
        return self


class _Col:
    def is_(self, *_):
        return True

    def __eq__(self, other):
        return True


class _Session:
    def execute(self, q):
        return types.SimpleNamespace(scalars=lambda: ["AAA", "BBB"])


class _Tasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *a, **k):
        self.tasks.append((fn, a, k))


def _load(bulk):
    tree = ast.parse(SRC)
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in ("refresh_signals", "_guarded_refresh"))
            or (isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "_REFRESH_LOCKS")]
    for n in keep:
        if isinstance(n, ast.FunctionDef):
            n.decorator_list = []
    stock = types.SimpleNamespace(symbol="sym", active=_Col(), delisted=_Col(), market=_Col())
    log = _Log()
    ns = {"collections": collections, "threading": threading, "time": time, "log": log,
          "_bulk_persist": bulk, "select": lambda *a: _Q(), "Stock": stock,
          "BackgroundTasks": object, "Session": object, "Depends": lambda f: None,
          "get_session": None, "get_current_username": None,
          "_get_redis": lambda: (_ for _ in ()).throw(RuntimeError("no redis"))}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "routes.py", "exec"), ns)
    return ns, log


def _post(ns, market="US"):
    tasks = _Tasks()
    out = ns["refresh_signals"](tasks, market=market, session=_Session(), _="svc")
    return out, tasks


def test_a_request_during_a_running_pass_is_skipped_and_the_next_one_runs():
    started, release, runs = threading.Event(), threading.Event(), []

    def bulk(symbols):
        runs.append(list(symbols))
        started.set()
        assert release.wait(5)
    ns, log = _load(bulk)
    out, tasks = _post(ns)
    assert out["status"] == "scheduled"
    fn, a, k = tasks.tasks[0]
    th = threading.Thread(target=fn, args=a, kwargs=k)
    th.start()
    assert started.wait(5)

    out2, tasks2 = _post(ns)                      # 5 minutes later, pass still running
    assert out2["status"] == "skipped_running" and tasks2.tasks == []
    out3, tasks3 = _post(ns, market="HK")         # the other market is independent
    assert out3["status"] == "scheduled"

    release.set()
    th.join(5)
    assert [e for e, _ in log.events].count("signals.refresh.pass_done") == 1
    out4, tasks4 = _post(ns)                      # after the pass, the next request runs
    assert out4["status"] == "scheduled"
    assert runs == [["AAA", "BBB"]]


def test_two_tasks_enqueued_together_run_only_once():
    """Two requests can both pass the endpoint check before either task starts."""
    gate, runs = threading.Event(), []

    def bulk(symbols):
        runs.append(1)
        assert gate.wait(5)
    ns, log = _load(bulk)
    (_, t1), (_, t2) = _post(ns), _post(ns)
    a = threading.Thread(target=t1.tasks[0][0], args=t1.tasks[0][1])
    a.start()
    time.sleep(0.2)
    t2.tasks[0][0](*t2.tasks[0][1])               # returns at once: lock is held
    gate.set()
    a.join(5)
    assert runs == [1]
    assert ("signals.refresh.skip_overlap", {"market": "US"}) in log.events


def test_a_failing_pass_releases_the_lock():
    def bulk(symbols):
        raise RuntimeError("boom")
    ns, _ = _load(bulk)
    _, tasks = _post(ns)
    fn, a, _k = tasks.tasks[0]
    try:
        fn(*a)
    except RuntimeError:
        pass
    assert _post(ns)[0]["status"] == "scheduled"
