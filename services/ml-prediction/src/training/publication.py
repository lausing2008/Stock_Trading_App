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
  <model_dir>/../ml_candidates/rollback/...      content-addressed copies pinned at selection
  <model_dir>/.publication_established          written with the first policy; marks that a
                                                 policy has existed (no glob of model_dir/*/*
                                                 can match it)

POLICY (policy.json):
  epoch       increments on EVERY change (enable, disable, authorize, revoke, select, rollback,
              serve_current_live).
              A promotion must name the epoch it was approved under; any change since refuses it.
  v2_enabled  default False. Nothing v2 reaches serving while False.
  cohort      {"SYMBOL|STYLE": {...}} cells authorized for v2 ownership.
  selections  {"SYMBOL|STYLE|FAMILY": {"path", "sha256", "kind", "rollback", ...}} the explicit
              serving manifest. `rollback` is the hash-pinned copy of the live artifact taken at
              selection time.

DECISIONS:
  v1 write, cell not owned (v2 off, or cell not in cohort) -> LIVE path, unchanged behaviour.
  v1 write, cell owned (v2 on AND cell in cohort)          -> candidate root (v1-fenced), never live.
  v2 write                                                 -> candidate root, always. Never live.
  unreadable policy, or missing after one was established  -> refuse (fail closed); nothing written.

PROMOTION is refused outright until v2 qualification records exist (review PF-02): a directory
and a caller-supplied hash are not qualification. The selection code below it stays, bound to
the exact cell/family path, so it is exercised by tests; the candidate manifest is v2 work.

SERVING (review PF-03..PF-05):
  no policy and never established -> the live path (today's behaviour).
  policy unreadable, or missing after establishment -> ServingUnavailable: ML is unavailable.
      Never a silent return to v1 -- the policy may have held a selection.
  selection -> its file, verified: the bytes deserialised are the bytes hashed, against the
      selection's sha256. A mismatch or a missing file is ServingUnavailable. The bundle cache
      is keyed by that hash, and the file is re-read and re-hashed on every load, so a cache
      hit cannot hide a replaced file.
Disabling v2 or revoking a cell does not change serving. Rollback is explicit: `rollback`
serves the pinned pre-selection artifact; `serve_current_live` (separately named, needs a
recorded reason) returns the cell to whatever the live path holds now.

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


class ServingUnavailable(Exception):
    """No artifact may be served for this request: ML is unavailable, never a v1 fallback."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


_MARKER = ".publication_established"


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
    """Missing and never established -> the default (v2 off). Present but unreadable, or missing
    after a policy was established -> PolicyUnreadable."""
    path = roots(model_dir)[1] / "policy.json"
    if not path.exists():
        if (roots(model_dir)[0] / _MARKER).exists():
            raise PolicyUnreadable(f"{path}: missing after establishment")
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
    live, pdir, _ = roots(model_dir)
    marker = live / _MARKER
    if not marker.exists():
        # Before the policy itself, so no state exists where a policy was written but its loss
        # would read as first-time legacy operation.
        live.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"established_at": _now(), "policy": str(pdir / "policy.json")}))
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
    Selections are NOT removed here -- serving changes only by an explicit rollback or
    serve_current_live."""
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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


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


def rewrite_live(model_dir, artifact: Path, *, symbol: str, style: str, rewrite) -> dict:
    """Rewrite one LIVE artifact in place (the suppression resweep), under the publication lock
    for the whole read -> check -> recompute -> replace (review PF-01).

    `rewrite(data: bytes)` deserialises from exactly the bytes read here and returns either None
    (nothing to change) or a `write_bundle(path)` callable. Refused, with nothing written, when
    the policy is unreadable, the cell is owned by v2, or the file no longer holds the bytes that
    were read when the replacement is about to land. Holding the lock means a training publish
    or a policy change cannot interleave; the identity re-check also refuses a writer that does
    not take the lock.
    """
    artifact = Path(artifact)
    with locked(model_dir):
        try:
            policy = read_policy(model_dir)
        except PolicyUnreadable as exc:
            return {"written": False, "reason": f"policy_unreadable:{exc}"}
        if owned_by_v2(policy, symbol, style):
            return {"written": False, "reason": f"publication_fenced:{cell_key(symbol, style)}"}
        data = artifact.read_bytes()
        identity = _sha256_bytes(data)
        write_bundle = rewrite(data)
        if write_bundle is None:
            return {"written": False, "reason": "unchanged", "sha256": identity}
        fd, tmp = tempfile.mkstemp(dir=artifact.parent, suffix=".tmp")
        try:
            os.close(fd)
            write_bundle(tmp)
            if not artifact.exists() or _sha256(artifact) != identity:
                raise PublicationRefused("artifact_changed_since_read")
            os.replace(tmp, artifact)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return {"written": True, "reason": "rewritten", "replaced_sha256": identity}


def _validate_qualification(qualification, *, symbol: str, style: str, family: str, sha256: str) -> dict:
    """The gate a selection must pass. There are no v2 qualification records yet, so every
    promotion is refused (review PF-02). The candidate manifest binding hash, cell, family,
    evaluation protocol and qualification evidence is built with the remaining v2 work and is
    validated here; nothing else may stand in for it."""
    raise PublicationRefused("promotion_unavailable:qualification_records_not_implemented")


def promote(model_dir, symbol: str, style: str, family: str, candidate_path: str, sha256: str,
            expected_epoch: int, by: str, *, live_path: Path, qualification=None) -> dict:
    """Select a candidate for serving. Refused today: see _validate_qualification.

    Beyond qualification it requires v2 enabled, the cell in the cohort, the policy unchanged
    since `expected_epoch`, a candidate written for exactly this cell and family, and the bytes
    matching `sha256`. It pins the current live artifact as a content-addressed rollback copy.
    """
    if family not in FAMILIES:
        raise PublicationRefused(f"unknown_family:{family}")
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
        if path.parent != (cand_root / "v2" / family).resolve() \
                or not path.name.startswith(f"{symbol.upper()}_{style.lower()}.") or path.suffix != ".joblib":
            raise PublicationRefused("candidate_not_for_this_cell_family")
        qual = _validate_qualification(qualification, symbol=symbol, style=style, family=family, sha256=sha256)
        if not path.exists() or _sha256_bytes(path.read_bytes()) != sha256:
            raise PublicationRefused("candidate_hash_mismatch")
        live_path = Path(live_path)
        if live_path.exists():
            data = live_path.read_bytes()
            pin_sha = _sha256_bytes(data)
            pin = cand_root / "rollback" / family / f"{symbol.upper()}_{style.lower()}.{pin_sha}.joblib"
            if not pin.exists():
                _atomic_write_bytes(pin, data)
            rollback = {"path": str(pin), "sha256": pin_sha, "pinned_from": str(live_path), "pinned_at": _now()}
        else:
            rollback = {"unavailable": True, "reason": "no_live_artifact_at_selection"}
        policy["selections"][selection_key(symbol, style, family)] = {
            "path": str(path), "sha256": sha256, "kind": "candidate", "qualification": qual,
            "rollback": rollback, "selected_at": _now(), "by": by}
        policy["epoch"] += 1
        policy["log"] = (policy.get("log") or [])[-499:] + [{"at": _now(), "by": by, "action": "select",
                                                            "epoch": policy["epoch"], "cell": cell_key(symbol, style),
                                                            "family": family, "sha256": sha256,
                                                            "rollback_sha256": rollback.get("sha256")}]
        _write_policy(model_dir, policy)
        return policy


def rollback(model_dir, symbol: str, style: str, family: str, by: str) -> dict:
    """Serve the artifact pinned when the cell/family was selected (review PF-05). The pinned
    copy is content-addressed and verified now; a nightly v1 write since selection does not
    change what this serves."""
    key = selection_key(symbol, style, family)

    def fn(p):
        sel = p["selections"].get(key)
        if not sel:
            raise PublicationRefused("no_selection")
        rb = sel.get("rollback") or {}
        if rb.get("unavailable") or not rb.get("path"):
            raise PublicationRefused(f"no_pinned_rollback:{rb.get('reason', 'missing')}")
        pinned = Path(rb["path"])
        if not pinned.exists() or _sha256_bytes(pinned.read_bytes()) != rb["sha256"]:
            raise PublicationRefused("pinned_rollback_integrity")
        p["selections"][key] = {"path": rb["path"], "sha256": rb["sha256"], "kind": "rollback",
                                "rollback": rb, "rolled_back_from": sel.get("sha256"),
                                "selected_at": _now(), "by": by}
        return {"cell": cell_key(symbol, style), "family": family, "sha256": rb["sha256"],
                "from_sha256": sel.get("sha256")}
    return _mutate(model_dir, by, "rollback", fn)


def serve_current_live(model_dir, symbol: str, style: str, family: str, live_path: Path, by: str,
                       reason: str) -> dict:
    """Remove the selection so the cell serves WHATEVER the live path holds now -- a moving
    target the nightly v1 job may have replaced since selection. Deliberately separate from
    `rollback`, and it records the reason and the live bytes' hash at the moment of the change."""
    if not (reason or "").strip():
        raise PublicationRefused("reason_required")
    key = selection_key(symbol, style, family)

    def fn(p):
        sel = p["selections"].pop(key, None)
        if sel is None:
            raise PublicationRefused("no_selection")
        lp = Path(live_path)
        return {"cell": cell_key(symbol, style), "family": family, "reason": reason,
                "from_sha256": sel.get("sha256"),
                "live_sha256": _sha256_bytes(lp.read_bytes()) if lp.exists() else None}
    return _mutate(model_dir, by, "serve_current_live", fn)


_SERVING_CACHE: dict = {}
_BUNDLE_CACHE: dict = {}
_BUNDLE_CACHE_MAX = 256


def _selections(model_dir, log=None) -> dict:
    """The policy's selections, or ServingUnavailable when the policy is unreadable or was lost
    after being established. Never an empty dict for a lost policy."""
    pfile = roots(model_dir)[1] / "policy.json"
    try:
        st = pfile.stat()
    except FileNotFoundError:
        if (roots(model_dir)[0] / _MARKER).exists():
            _SERVING_CACHE.pop(str(pfile), None)
            if log is not None:
                log.error("publication.policy_missing_serving_unavailable", policy=str(pfile))
            raise ServingUnavailable("publication_policy_unavailable:missing_after_establishment")
        return {}
    # Every policy write is an os.replace of a NEW file, so the inode changes on each change.
    # mtime alone is not enough (clock-tick coarse): a deselect once kept serving the candidate.
    # ctime is added because it cannot be set back by utime after an in-place write.
    stamp = (st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)
    cached = _SERVING_CACHE.get(str(pfile))
    if cached is None or cached[0] != stamp:
        try:
            cached = (stamp, read_policy(model_dir)["selections"])
        except PolicyUnreadable as exc:
            _SERVING_CACHE.pop(str(pfile), None)
            if log is not None:
                log.error("publication.policy_unreadable_serving_unavailable", error=str(exc))
            raise ServingUnavailable(f"publication_policy_unavailable:{exc}") from exc
        _SERVING_CACHE[str(pfile)] = cached
    return cached[1]


def serving_path(model_dir, live_path: Path, symbol: str, style: str, family: str, log=None) -> Path:
    """Which file a prediction would load. No selection -> `live_path` (today's behaviour). A
    selection whose file is gone is ServingUnavailable, not "not trained"."""
    sel = _selections(model_dir, log).get(selection_key(symbol, style, family))
    if not sel:
        return Path(live_path)
    path = Path(sel["path"])
    if not path.exists():
        raise ServingUnavailable(f"selected_artifact_missing:{selection_key(symbol, style, family)}")
    return path


def load_serving(model_dir, live_path: Path, symbol: str, style: str, family: str, loader, log=None):
    """Load the bundle a prediction uses. `loader` takes a path or a binary file object
    (joblib.load). For a selection the bytes are read once, hashed, compared with the
    selection's sha256 and deserialised from that same buffer (review PF-04)."""
    import io
    sel = _selections(model_dir, log).get(selection_key(symbol, style, family))
    if not sel:
        live_path = Path(live_path)
        if not live_path.exists():
            raise FileNotFoundError(f"No trained model at {live_path}")
        return loader(live_path)
    key = selection_key(symbol, style, family)
    try:
        data = Path(sel["path"]).read_bytes()
    except OSError as exc:
        raise ServingUnavailable(f"selected_artifact_missing:{key}") from exc
    sha = _sha256_bytes(data)
    if sha != sel["sha256"]:
        if log is not None:
            log.error("publication.selected_artifact_hash_mismatch", cell=key,
                      expected=sel["sha256"], actual=sha)
        raise ServingUnavailable(f"selected_artifact_hash_mismatch:{key}")
    bundle = _BUNDLE_CACHE.get(sha)
    if bundle is None:
        bundle = loader(io.BytesIO(data))
        if len(_BUNDLE_CACHE) >= _BUNDLE_CACHE_MAX:
            _BUNDLE_CACHE.clear()
        _BUNDLE_CACHE[sha] = bundle
    return bundle


def _main(argv: list[str]) -> int:
    """Operator CLI (inside the ml-prediction container):
        python -m src.training.publication show
        python -m src.training.publication enable|disable --by NAME
        python -m src.training.publication authorize|revoke SYMBOL STYLE --by NAME
        python -m src.training.publication rollback SYMBOL STYLE FAMILY --by NAME
        python -m src.training.publication serve-current-live SYMBOL STYLE FAMILY --by NAME --reason TEXT
    """
    import argparse
    from common.config import get_settings
    ap = argparse.ArgumentParser(prog="publication")
    ap.add_argument("command", choices=["show", "enable", "disable", "authorize", "revoke", "rollback",
                                        "serve-current-live"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--by")
    ap.add_argument("--reason")
    a = ap.parse_args(argv)
    md = get_settings().model_dir
    if a.command == "show":
        print(json.dumps(read_policy(md), indent=1))
        return 0
    if not a.by:
        ap.error("--by is required for a change")
    if a.command in ("rollback", "serve-current-live"):
        if len(a.args) != 3:
            ap.error(f"{a.command} needs SYMBOL STYLE FAMILY")
        sym, sty, fam = a.args
        if a.command == "rollback":
            p = rollback(md, sym, sty, fam, a.by)
        else:
            from .trainer import _artifact_path
            p = serve_current_live(md, sym, sty, fam, _artifact_path(sym, fam, sty), a.by, a.reason or "")
        print(json.dumps({k: p[k] for k in ("epoch", "selections")}, indent=1))
        return 0
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
