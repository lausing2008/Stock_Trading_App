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
                                            train at the same time; a later one waits

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
            log.error("train.batch_job_failed", symbol=symbol, model=model_name, style=style, error=str(exc))
    return {"done": done, "failed": failed, "seconds": round(time.monotonic() - t0)}


def run_training_batch(jobs, protocol: str, market: str = "ALL") -> int | None:
    """Background-task body: run the batch in a child process and wait for it."""
    jobs = [list(j) for j in jobs]
    if IN_PROCESS:
        run_jobs(jobs, protocol)
        return None
    log.info("train.batch_spawn", market=market, jobs=len(jobs))
    proc = subprocess.run(
        [sys.executable, "-m", "src.training.batch", "--market", market, "--protocol", protocol],
        input=json.dumps(jobs), text=True, cwd=str(_APP_ROOT))
    if proc.returncode != 0:
        log.error("train.batch_child_failed", market=market, returncode=proc.returncode)
    return proc.returncode


def main(argv=None, stdin=None, train=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True)
    ap.add_argument("--protocol", required=True)
    args = ap.parse_args(argv)
    jobs = json.load(stdin or sys.stdin)
    d = _lock_dir()
    with open(d / f"{args.market}.lock", "w") as mine:
        try:
            fcntl.flock(mine.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.warning("train.batch_already_running", market=args.market)
            return 0
        with open(d / "batch.lock", "w") as shared:
            t_wait = time.monotonic()
            fcntl.flock(shared.fileno(), fcntl.LOCK_EX)
            waited = round(time.monotonic() - t_wait)
            log.info("train.batch_start", market=args.market, jobs=len(jobs), waited_seconds=waited)
            out = run_jobs(jobs, args.protocol, train=train)
    log.info("train.batch_done", market=args.market, **out)
    return 0


if __name__ == "__main__":
    from common.config import get_settings
    configure_logging(get_settings().log_level)
    sys.exit(main())
