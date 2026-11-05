"""Nightly training in its own process (AUD-ML-TRAINCHILD, 2026-11-05).

The nightly job used to run ~1,000 train_model calls as BackgroundTasks inside the API
process. Python rarely returns freed memory to the OS, so ml-prediction kept its training
high-water mark (~2.5 GB measured on 2026-11-04) resident for the rest of the day, on a host
whose swap was already full. The batch now runs in a fresh interpreter that exits when it is
done, so the memory goes back to the host.

A fresh interpreter, not a fork: the API process has already run XGBoost, and GNU OpenMP's
thread pool is not fork-safe, so a forked child can hang inside its first fit.

Locks (fcntl, released by the kernel if the child dies):
  <model_dir>/../ml_training/<market>.lock  held for the whole run; a second run for the same
                                            market while one is in progress is skipped
  <model_dir>/../ml_training/batch.lock     serialises runs, so the HK and US batches never
                                            train at the same time; a later one waits, for at
                                            most WAIT_LIMIT_S, then gives up (exit 3)

The child is killed after RUN_LIMIT_S. Without a limit one stuck fit would hold batch.lock
forever and stop nightly training for both markets with nothing reported but a daily
"already running" line (review of 62c0d240, 2026-11-05).

Jobs arrive as JSON on stdin: [[symbol, model_name, horizon, style], ...] in the order the
route built them.
"""
import argparse
import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path

from common.logging import configure_logging, get_logger

log = get_logger("ml.training.batch")

# Tests set this so the batch runs in the calling process, where their monkeypatched loaders
# apply. Production always spawns.
IN_PROCESS = False

_APP_ROOT = Path(__file__).resolve().parents[2]
# A full two-market batch took ~1.5 h on 2026-11-04; one market is less.
RUN_LIMIT_S = 4 * 3600
WAIT_LIMIT_S = 3 * 3600
# POST /ml/tune_all: 3-5 h for ~123 symbols x 60 trials (its own docstring).
TUNE_LIMIT_S = 8 * 3600


def _lock_dir() -> Path:
    from common.config import get_settings
    d = Path(get_settings().model_dir).parent / "ml_training"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_jobs(jobs, protocol: str, train=None) -> dict:
    """Train each job in order. One failing job is counted and the rest still run."""
    if train is None:
        from .trainer import train_model as train
    done = failed = 0
    t0 = time.monotonic()
    for symbol, model_name, horizon, style in jobs:
        try:
            train(symbol, model_name, horizon, style=style, protocol=protocol)
            done += 1
        except Exception as exc:
            failed += 1
            log.error("train.batch_job_failed", symbol=symbol, model=model_name, style=style,
                      error=str(exc), exc_info=True)
    return {"done": done, "failed": failed, "seconds": round(time.monotonic() - t0)}


def run_tune_jobs(symbols, n_trials: int, horizon: int, style: str, triggered_by: str, tune_symbol=None):
    """The weekly Optuna sweep, moved verbatim from routes.tune_all (AUD-ML-TRAINCHILD)."""
    if tune_symbol is None:
        from .tuner import tune_symbol
    results = []
    for sym in symbols:
        try:
            result = tune_symbol(sym, n_trials=n_trials, horizon=horizon, style=style, triggered_by=triggered_by)
        except Exception as exc:
            log.warning("tune_all.symbol_failed", symbol=sym, error=str(exc))
            result = {"symbol": sym, "skipped": True, "reason": str(exc)}
        results.append(result)

    # TIER95: After all models are retrained, trigger signal refreshes so new models
    # are used immediately (not at the next scheduled refresh 5× per day).
    tuned_count = sum(1 for r in results if not r.get("skipped"))

    # AUD263-TUNEALL-STALE-GUARD-NOT-WEEKLY: this is the real completion marker
    # (scheduler.py's own "tune_all_sent" only records that the POST was DISPATCHED, not
    # that this ~2-4h background run actually finished). Written unconditionally, even
    # when tuned_count is 0 — a run that genuinely finished with zero symbols tuned (e.g.
    # every symbol legitimately failed) must still be distinguishable from a run that never
    # finished at all (container recreate mid-run, an unhandled crash outside the per-symbol
    # try/except above). Fails open — a Redis hiccup here must never mask that tuning itself
    # genuinely completed.
    try:
        from common.redis_client import get_redis as _get_pool_redis
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        _get_pool_redis().set(
            "stockai:tune_all_completed",
            _json.dumps({
                "completed_at": _dt.now(_tz.utc).isoformat(),
                "tuned": tuned_count,
                "total": len(symbols),
                "triggered_by": triggered_by,
            }),
        )
    except Exception as _mark_exc:
        log.warning("tune_all.completion_marker_failed", error=str(_mark_exc))

    if tuned_count > 0:
        log.info("tune_all.complete", tuned=tuned_count, total=len(symbols))
        try:
            from common.config import get_settings as _gs
            import httpx as _hx
            import uuid as _uuid
            from jose import jwt as _jwt
            _s = _gs()
            _tok = _jwt.encode(
                {"sub": "ml-prediction", "jti": str(_uuid.uuid4()), "exp": int(__import__("time").time()) + 3600},
                _s.jwt_secret, algorithm="HS256",
            )
            _hdrs = {"Authorization": f"Bearer {_tok}"}
            for _mkt in ("US", "HK"):
                try:
                    _r = _hx.post(
                        f"{_s.signal_engine_url}/signals/refresh",
                        params={"market": _mkt},
                        headers=_hdrs, timeout=30,
                    )
                    log.info("tune_all.post_signal_refresh", market=_mkt, status=_r.status_code)
                except Exception as _exc:
                    log.warning("tune_all.post_signal_refresh_failed", market=_mkt, error=str(_exc))
        except Exception as _exc:
            log.warning("tune_all.post_refresh_setup_failed", error=str(_exc))

    return results


def _spawn(args: list[str], payload, label: str, limit_s: int) -> int | None:
    try:
        proc = subprocess.run([sys.executable, "-m", "src.training.batch", *args],
                              input=json.dumps(payload), text=True, cwd=str(_APP_ROOT), timeout=limit_s)
    except subprocess.TimeoutExpired:   # run() has already killed and reaped the child
        log.error("train.batch_child_timeout", market=label, limit_s=limit_s)
        return None
    if proc.returncode != 0:
        log.error("train.batch_child_failed", market=label, returncode=proc.returncode)
    return proc.returncode


def run_training_batch(jobs, protocol: str, market: str = "ALL") -> int | None:
    """Background-task body: run the batch in a child process and wait for it."""
    jobs = [list(j) for j in jobs]
    if IN_PROCESS:
        run_jobs(jobs, protocol)
        return None
    log.info("train.batch_spawn", market=market, jobs=len(jobs))
    return _spawn(["--market", market, "--protocol", protocol], jobs, market, RUN_LIMIT_S)


def run_tune_batch(symbols, n_trials: int, horizon: int, style: str, triggered_by: str) -> int | None:
    """Background-task body for POST /ml/tune_all (3-5 h measured), in a child process."""
    params = {"symbols": list(symbols), "n_trials": n_trials, "horizon": horizon, "style": style,
              "triggered_by": triggered_by}
    if IN_PROCESS:
        run_tune_jobs(**params)
        return None
    log.info("train.batch_spawn", market="TUNE", jobs=len(params["symbols"]))
    return _spawn(["--kind", "tune", "--market", "TUNE", "--protocol", "v1"], params, "TUNE", TUNE_LIMIT_S)


def _acquire(fh, label: str, wait_limit_s: float, poll_s: float) -> float | None:
    """batch.lock with a bounded wait; None once the wait limit has passed."""
    t_wait = time.monotonic()
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return round(time.monotonic() - t_wait)
        except BlockingIOError:
            if time.monotonic() - t_wait > wait_limit_s:
                log.error("train.batch_wait_timeout", market=label, limit_s=wait_limit_s)
                return None
            time.sleep(poll_s)


def main(argv=None, stdin=None, train=None, tune=None, wait_limit_s=WAIT_LIMIT_S, poll_s=5.0) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=("train", "tune"), default="train")
    ap.add_argument("--market", required=True)
    ap.add_argument("--protocol", required=True)
    args = ap.parse_args(argv)
    payload = json.load(stdin or sys.stdin)
    d = _lock_dir()
    # A same-kind run for the same scope is skipped; every run then queues on batch.lock.
    with open(d / f"{args.market}.lock", "w") as mine:
        try:
            fcntl.flock(mine.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.warning("train.batch_already_running", market=args.market)
            return 0
        with open(d / "batch.lock", "w") as shared:
            waited = _acquire(shared, args.market, wait_limit_s, poll_s)
            if waited is None:
                return 3
            if args.kind == "tune":
                log.info("train.batch_start", market=args.market, jobs=len(payload["symbols"]), waited_seconds=waited)
                results = run_tune_jobs(**payload, tune_symbol=tune)
                out = {"done": sum(1 for r in results if not r.get("skipped")),
                       "failed": sum(1 for r in results if r.get("skipped"))}
            else:
                log.info("train.batch_start", market=args.market, jobs=len(payload), waited_seconds=waited)
                out = run_jobs(payload, args.protocol, train=train)
    log.info("train.batch_done", market=args.market, kind=args.kind, **out)
    # Every job failing (database down, say) is a failed batch, not a quiet success.
    return 1 if out["failed"] and not out["done"] else 0


if __name__ == "__main__":
    from common.config import get_settings
    configure_logging(get_settings().log_level)
    sys.exit(main())
