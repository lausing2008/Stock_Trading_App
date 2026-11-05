"""AUD-ML-TRAINCHILD: the nightly batch runs in its own process, one market per post-close.

The batch used to run inside the API process, which then kept its ~2.5 GB training peak for
the rest of the day (measured 2026-11-04). These drive training/batch.py with a recording
trainer; the real trainer runs in the child inside the image
(test_f04_publication_nightly_path_container.py).
"""
import fcntl
import importlib.util
import io
import json
import pathlib
import sys
import threading
import time

import pytest

# Loaded from its file: importing src.training pulls in the trainer, which needs lightgbm.
_p = pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "batch.py"
_spec = importlib.util.spec_from_file_location("batch_under_test", _p)
batch = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = batch
_spec.loader.exec_module(batch)

JOBS = [["AAA", "xgboost", 10, "SWING"], ["AAA", "random_forest", 10, "SWING"],
        ["BBB", "xgboost", 10, "SWING"], ["BBB", "random_forest", 10, "SWING"]]


class _Trainer:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, symbol, model_name, horizon, style=None, protocol=None):
        self.calls.append((symbol, model_name, horizon, style, protocol))
        if (symbol, model_name) == self.fail:
            raise RuntimeError("boom")


@pytest.fixture
def locks(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "_lock_dir", lambda: tmp_path)
    return tmp_path


def _main(market, train, jobs=JOBS, **kw):
    kw.setdefault("poll_s", 0.05)
    return batch.main(["--market", market, "--protocol", "v1"], stdin=io.StringIO(json.dumps(jobs)), train=train, **kw)


def test_every_job_runs_in_order_with_the_protocol(locks):
    t = _Trainer()
    assert _main("US", t) == 0
    assert t.calls == [(s, m, h, st, "v1") for s, m, h, st in JOBS]


def test_one_failing_job_does_not_stop_the_rest(locks):
    """BackgroundTasks stopped at the first exception, dropping every later job."""
    t = _Trainer(fail=("AAA", "random_forest"))
    out = batch.run_jobs(JOBS, "v1", train=t)
    assert len(t.calls) == 4 and out["done"] == 3 and out["failed"] == 1


def test_a_second_run_for_the_same_market_is_skipped(locks):
    with open(locks / "US.lock", "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        t = _Trainer()
        assert _main("US", t) == 0
        assert t.calls == []


def test_another_market_waits_for_the_running_batch(locks):
    held = open(locks / "batch.lock", "w")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX)
    t, done = _Trainer(), threading.Event()
    th = threading.Thread(target=lambda: (_main("HK", t), done.set()))
    th.start()
    time.sleep(0.3)
    assert not done.is_set() and t.calls == [], "must wait while another batch trains"
    held.close()
    th.join(5)
    assert done.is_set() and len(t.calls) == 4


def test_waiting_for_another_batch_gives_up_instead_of_blocking_forever(locks):
    """A stuck batch used to hold batch.lock forever; the next market then waited forever."""
    with open(locks / "batch.lock", "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        t = _Trainer()
        assert _main("HK", t, wait_limit_s=0.2) == 3
        assert t.calls == []


def test_a_batch_where_every_job_failed_exits_non_zero(locks):
    class _All(_Trainer):
        def __call__(self, *a, **k):
            raise RuntimeError("database down")
    assert _main("US", _All()) == 1
    assert _main("US", _Trainer(fail=("AAA", "xgboost"))) == 0     # partial failure is counted, not fatal


def test_a_child_over_the_run_limit_is_killed_and_reported(monkeypatch):
    def run(cmd, **kw):
        assert kw["timeout"] == batch.RUN_LIMIT_S
        raise batch.subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(batch.subprocess, "run", run)
    monkeypatch.setattr(batch, "IN_PROCESS", False)
    assert batch.run_training_batch(JOBS, "v1", market="US") is None


def test_production_spawns_a_fresh_interpreter_with_the_jobs_on_stdin(monkeypatch):
    seen = {}

    class _Done:
        returncode = 0

    def run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return _Done()
    monkeypatch.setattr(batch.subprocess, "run", run)
    monkeypatch.setattr(batch, "IN_PROCESS", False)
    assert batch.run_training_batch([tuple(j) for j in JOBS], "v1", market="HK") == 0
    assert seen["cmd"] == [sys.executable, "-m", "src.training.batch", "--market", "HK", "--protocol", "v1"]
    assert json.loads(seen["input"]) == JOBS
    assert seen["cwd"] == str(batch._APP_ROOT) and (batch._APP_ROOT / "src" / "training" / "batch.py").exists()


def test_in_process_mode_is_for_tests_only():
    assert batch.IN_PROCESS is False


def test_tuning_runs_through_the_same_child_entry_point(locks):
    """tune_all used to run in the API process and keep its peak there all week."""
    seen = []

    def tune(sym, n_trials, horizon, style, triggered_by):
        seen.append((sym, n_trials, horizon, style, triggered_by))
        return {"symbol": sym, "skipped": True, "reason": "fixture"}     # no post-refresh HTTP
    payload = {"symbols": ["AAA", "BBB"], "n_trials": 3, "horizon": 10, "style": "SWING", "triggered_by": "weekly"}
    rc = batch.main(["--kind", "tune", "--market", "TUNE", "--protocol", "v1"],
                    stdin=io.StringIO(json.dumps(payload)), tune=tune, poll_s=0.05)
    assert seen == [("AAA", 3, 10, "SWING", "weekly"), ("BBB", 3, 10, "SWING", "weekly")]
    assert rc == 1, "every symbol skipped is reported as a failed batch"


def test_tuning_waits_for_a_running_training_batch(locks):
    with open(locks / "batch.lock", "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        payload = {"symbols": ["AAA"], "n_trials": 1, "horizon": 10, "style": "SWING", "triggered_by": "stale_guard"}
        rc = batch.main(["--kind", "tune", "--market", "TUNE", "--protocol", "v1"],
                        stdin=io.StringIO(json.dumps(payload)), tune=lambda *a, **k: {}, wait_limit_s=0.2, poll_s=0.05)
        assert rc == 3


def test_tune_spawn_uses_its_own_kind_and_longer_limit(monkeypatch):
    seen = {}

    class _Done:
        returncode = 0

    def run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return _Done()
    monkeypatch.setattr(batch.subprocess, "run", run)
    monkeypatch.setattr(batch, "IN_PROCESS", False)
    assert batch.run_tune_batch(["AAA"], 60, 10, "SWING", "weekly") == 0
    assert seen["cmd"][-6:] == ["--kind", "tune", "--market", "TUNE", "--protocol", "v1"]
    assert seen["timeout"] == batch.TUNE_LIMIT_S > batch.RUN_LIMIT_S
    assert json.loads(seen["input"])["symbols"] == ["AAA"]
