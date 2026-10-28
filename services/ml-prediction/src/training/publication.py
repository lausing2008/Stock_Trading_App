"""F04 nightly-publication fencing: who may write, select and serve a model artifact.

The plan (docs/features/2026-10-27-f04-evaluation-version-comparison-and-rollout-plan.md, Option 1):
v2 training writes only to an isolated CANDIDATE root; non-enabled cells keep the exact live v1
behaviour; training success never grants promotion; revocation fences in-flight publication; and
disabling promotion does not silently roll back serving. This module is that control plane. It is
deliberately small, stdlib-only (plus joblib supplied by the caller), and decides every write AT
WRITE TIME under one host-wide file lock -- so a job queued, retried or already running when the
policy changes is judged by the policy in force when it publishes, never by the one it was
enqueued under.

LAYOUT (all on the shared /data volume, siblings of the live model_dir):
  <model_dir>/<family>/<symbol>_<style>.joblib   LIVE: the only tree live discovery scans (the
                                                 trainer's _artifact_path, predictors, resweep)
  <model_dir>/../ml_publication/policy.json      the policy; missing = default (v2 OFF)
  <model_dir>/../ml_publication/.lock            fcntl lock serialising every decision + write
  <model_dir>/../ml_candidates/<protocol>/...    candidates; never on a live discovery path

POLICY (policy.json):
  epoch       increments on EVERY change (enable, disable, authorize, revoke, select, deselect).
              A promotion must name the epoch it was approved under; any change since refuses it.
  v2_enabled  default False. Nothing v2 reaches serving while False.
  cohort      {"SYMBOL|STYLE": {...}} cells authorized for v2 ownership.
  selections  {"SYMBOL|STYLE|FAMILY": {"path", "sha256", ...}} the explicit serving manifest.

DECISIONS:
  v1 write, cell not owned (v2 off, or cell not in cohort) -> LIVE path, unchanged behaviour.
  v1 write, cell owned (v2 on AND cell in cohort)          -> candidate root (v1-fenced), never live.
  v2 write                                                 -> candidate root, always. Never live.
  unreadable policy                                        -> refuse (fail closed); nothing written.
Serving: a selection serves its candidate file; otherwise the live path. Disabling v2 does not
remove selections (explicit `deselect` is the rollback). An unreadable policy serves live and
logs, which with no selections is exactly today's behaviour.

Mutations are deliberately NOT HTTP endpoints. Enabling v2 is an operator action run inside the
ml-prediction container: `python -m src.training.publication <command> ...`.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
PROTOCOLS = ("v1", "v2")
FAMILIES = ("xgboost", "lightgbm", "random_forest")
DEFAULT_POLICY = {"schema": SCHEMA, "epoch": 0, "v2_enabled": False, "cohort": {}, "selections": {}, "log": []}


class PublicationRefused(Exception):
    """Nothing was written. `reason` says why."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PolicyUnreadable(Exception):
    pass


def roots(model_dir: str | os.PathLike) -> tuple[Path, Path, Path]:
    """(live model_dir, policy dir, candidate root) -- the latter two are siblings of model_dir,
    so no glob or path under model_dir can reach them."""
    live = Path(model_dir)
    return live, live.parent / "ml_publication", live.parent / "ml_candidates"


def cell_key(symbol: str, style: str) -> str:
    return f"{symbol.upper()}|{style.upper()}"


def selection_key(symbol: str, style: str, family: str) -> str:
    return f"{cell_key(symbol, style)}|{family}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_policy(model_dir) -> dict:
    """Missing file -> the default (v2 off). Present but unreadable -> PolicyUnreadable."""
    path = roots(model_dir)[1] / "policy.json"
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_POLICY))
    try:
        policy = json.loads(path.read_text())
    except Exception as exc:
        raise PolicyUnreadable(f"{path}: {type(exc).__name__}") from exc
    if not isinstance(policy, dict) or policy.get("schema") != SCHEMA or not isinstance(policy.get("epoch"), int) \
            or not isinstance(policy.get("v2_enabled"), bool) or not isinstance(policy.get("cohort"), dict) \
            or not isinstance(policy.get("selections"), dict):
        raise PolicyUnreadable(f"{path}: schema")
    return policy


@contextlib.contextmanager
def locked(model_dir):
    """Host-wide exclusive lock for every decision and write (ml-prediction is one container on
    one host; the lock file sits on the shared volume)."""
    pdir = roots(model_dir)[1]
    pdir.mkdir(parents=True, exist_ok=True)
    with open(pdir / ".lock", "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _write_policy(model_dir, policy: dict) -> None:
    pdir = roots(model_dir)[1]
    fd, tmp = tempfile.mkstemp(dir=pdir, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(policy, f, indent=1, sort_keys=True)
    os.replace(tmp, pdir / "policy.json")


def _mutate(model_dir, by: str, action: str, fn) -> dict:
    with locked(model_dir):
        policy = read_policy(model_dir)
        detail = fn(policy)
        policy["epoch"] += 1
        policy["log"] = (policy.get("log") or [])[-499:] + [
            {"at": _now(), "by": by, "action": action, "epoch": policy["epoch"], **(detail or {})}]
        _write_policy(model_dir, policy)
        return policy


def set_v2_enabled(model_dir, enabled: bool, by: str) -> dict:
    return _mutate(model_dir, by, "enable_v2" if enabled else "disable_v2",
                   lambda p: p.__setitem__("v2_enabled", bool(enabled)))


def authorize(model_dir, symbol: str, style: str, by: str) -> dict:
    def fn(p):
        p["cohort"][cell_key(symbol, style)] = {"authorized_at": _now(), "by": by}
        return {"cell": cell_key(symbol, style)}
    return _mutate(model_dir, by, "authorize", fn)


def revoke(model_dir, symbol: str, style: str, by: str) -> dict:
    """Removes ownership and bumps the epoch, so a promotion approved before this is refused.
    Selections are NOT removed here -- serving changes only by an explicit deselect."""
    def fn(p):
        p["cohort"].pop(cell_key(symbol, style), None)
        return {"cell": cell_key(symbol, style)}
    return _mutate(model_dir, by, "revoke", fn)


def owned_by_v2(policy: dict, symbol: str, style: str) -> bool:
    return bool(policy["v2_enabled"]) and cell_key(symbol, style) in policy["cohort"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(model_dir, live_path: Path, write_bundle, *, protocol: str, symbol: str, style: str,
            family: str, job_epoch: int | None = None) -> dict:
    """Decide and write ONE artifact under the lock. `write_bundle(path)` serialises the bundle.

    Returns {"target": "live"|"candidate", "path", "reason", "epoch"}; raises PublicationRefused
    (nothing written) for an unknown protocol or an unreadable policy.
    """
    if protocol not in PROTOCOLS:
        raise PublicationRefused(f"unknown_protocol:{protocol}")
    live_root, _, cand_root = roots(model_dir)
    with locked(model_dir):
        try:
            policy = read_policy(model_dir)
        except PolicyUnreadable as exc:
            raise PublicationRefused(f"policy_unreadable:{exc}") from exc
        owned = owned_by_v2(policy, symbol, style)
        if protocol == "v1" and not owned:
            target, reason, dest = "live", "v1_cell_not_owned_by_v2", Path(live_path)
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            sub = "v1-fenced" if protocol == "v1" else "v2"
            reason = "v1_fenced_cell_owned_by_v2" if protocol == "v1" else (
                "v2_candidate_only" if job_epoch in (None, policy["epoch"]) else "v2_candidate_stale_epoch")
            target = "candidate"
            dest = cand_root / sub / family / f"{symbol.upper()}_{style.lower()}.{stamp}.joblib"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if target == "live" and cand_root in dest.resolve().parents:
            raise PublicationRefused("live_path_inside_candidate_root")
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
        try:
            os.close(fd)
            write_bundle(tmp)
            os.replace(tmp, dest)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return {"target": target, "path": str(dest), "reason": reason, "epoch": policy["epoch"]}


def promote(model_dir, symbol: str, style: str, family: str, candidate_path: str, sha256: str,
            expected_epoch: int, by: str) -> dict:
    """Select a candidate for serving. Requires v2 enabled, the cell in the cohort, the policy
    unchanged since `expected_epoch` (any enable/revoke/select since refuses), and the file's
    bytes matching `sha256`. Only v2 candidates are selectable."""
    _, _, cand_root = roots(model_dir)
    with locked(model_dir):
        policy = read_policy(model_dir)
        if not policy["v2_enabled"]:
            raise PublicationRefused("v2_disabled")
        if cell_key(symbol, style) not in policy["cohort"]:
            raise PublicationRefused("cell_not_authorized")
        if policy["epoch"] != expected_epoch:
            raise PublicationRefused(f"stale_epoch:{expected_epoch}!={policy['epoch']}")
        path = Path(candidate_path).resolve()
        if (cand_root / "v2").resolve() not in path.parents:
            raise PublicationRefused("not_a_v2_candidate")
        if not path.exists() or _sha256(path) != sha256:
            raise PublicationRefused("candidate_hash_mismatch")
        policy["selections"][selection_key(symbol, style, family)] = {
            "path": str(path), "sha256": sha256, "selected_at": _now(), "by": by}
        policy["epoch"] += 1
        policy["log"] = (policy.get("log") or [])[-499:] + [{"at": _now(), "by": by, "action": "select",
                                                            "epoch": policy["epoch"], "cell": cell_key(symbol, style),
                                                            "family": family, "sha256": sha256}]
        _write_policy(model_dir, policy)
        return policy


def deselect(model_dir, symbol: str, style: str, family: str, by: str) -> dict:
    """The explicit serving rollback: the cell/family serves its live (v1) artifact again."""
    def fn(p):
        p["selections"].pop(selection_key(symbol, style, family), None)
        return {"cell": cell_key(symbol, style), "family": family}
    return _mutate(model_dir, by, "deselect", fn)


_SERVING_CACHE: dict = {}


def serving_path(model_dir, live_path: Path, symbol: str, style: str, family: str, log=None) -> Path:
    """Which file a prediction loads. No selection -> `live_path` (today's behaviour)."""
    pfile = roots(model_dir)[1] / "policy.json"
    try:
        st = pfile.stat()
    except FileNotFoundError:
        return Path(live_path)
    # Every policy write is an os.replace of a NEW file, so the inode changes on each change.
    # mtime alone is not enough: file timestamps are clock-tick coarse, and two changes in quick
    # succession (promote, then deselect) can share one -- the container test caught a deselect
    # that kept serving the candidate.
    stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
    cached = _SERVING_CACHE.get(str(pfile))
    if cached is None or cached[0] != stamp:
        try:
            cached = (stamp, read_policy(model_dir)["selections"])
        except PolicyUnreadable as exc:
            if log is not None:
                log.error("publication.policy_unreadable_serving_live", error=str(exc))
            return Path(live_path)
        _SERVING_CACHE[str(pfile)] = cached
    sel = cached[1].get(selection_key(symbol, style, family))
    return Path(sel["path"]) if sel else Path(live_path)


def _main(argv: list[str]) -> int:
    """Operator CLI (inside the ml-prediction container):
        python -m src.training.publication show
        python -m src.training.publication enable|disable --by NAME
        python -m src.training.publication authorize|revoke SYMBOL STYLE --by NAME
    """
    import argparse
    from common.config import get_settings
    ap = argparse.ArgumentParser(prog="publication")
    ap.add_argument("command", choices=["show", "enable", "disable", "authorize", "revoke"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--by")
    a = ap.parse_args(argv)
    md = get_settings().model_dir
    if a.command == "show":
        print(json.dumps(read_policy(md), indent=1))
        return 0
    if not a.by:
        ap.error("--by is required for a change")
    if a.command in ("enable", "disable"):
        p = set_v2_enabled(md, a.command == "enable", a.by)
    elif len(a.args) != 2:
        ap.error("authorize/revoke need SYMBOL STYLE")
    else:
        p = (authorize if a.command == "authorize" else revoke)(md, a.args[0], a.args[1], a.by)
    print(json.dumps({k: p[k] for k in ("epoch", "v2_enabled", "cohort")}, indent=1))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
