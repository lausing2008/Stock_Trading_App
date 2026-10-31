"""Durable records of what triggered a Little RZY rebuild (2026-10-30).

The 2026-10-30 rebuild investigation (docs/2026-10-30/rzy-rebuild-investigation.md) could count
rebuilds but not explain why rebuilds per revised bar rose: the before/after bars live only in
generation archives, pruned to five per stream, and nothing recorded when a stream had consumed the
bar that later changed, or whether the throttle had deferred it. This module records both.

It only RECORDS. The revision check (`_ohlc_revised`), the throttle and every trading rule are
unchanged, and a failed write is logged without affecting the rebuild.

Change classes, for `stored_completed_bar_changed`, from the first revised bar `_revision` found:
  price_basis    the adjustment basis changed;
  closed_at      the bar's close time changed;
  rounding_ohlc  every OHLC change is within max(0.005, 2e-5 x price), the rounding scale used
                 by the investigation;
  material_ohlc  any larger OHLC change.
Volume is recorded beside the class. It cannot trigger a rebuild on its own, because
`_ohlc_revised` ignores it, so a volume-only revision never reaches this record.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from common.logging import get_logger

log = get_logger(__name__)

_PRICE_FIELDS = ("open", "high", "low", "close")
_BAR_FIELDS = _PRICE_FIELDS + ("volume", "closed_at", "price_basis")
_REASON_CLASS = {
    "gap_after_checkpoint": "gap_after_checkpoint",
    "checkpoint_bar_missing_from_source": "bar_missing",
    "checkpoint_contains_excluded_off_grid_bar": "off_grid",
    "legacy_quarantine": "legacy_quarantine",
}


def _rounding_tolerance(a: float, b: float) -> float:
    return max(0.005, 2e-5 * max(abs(a), abs(b)))


def classify(reason: str, before: dict | None, after: dict | None, noise_rel: float) -> dict:
    """Change class and per-field differences for one trigger."""
    if reason != "stored_completed_bar_changed" or not before or not after:
        return {"change_class": _REASON_CLASS.get(reason, "other"), "fields_changed": [],
                "max_abs_change": None, "max_rel_change": None}
    changed, abs_max, rel_max, within_rounding = [], 0.0, 0.0, True
    for k in _PRICE_FIELDS:
        a, b = before.get(k), after.get(k)
        if a is None or b is None:
            continue
        diff = abs(a - b)
        scale = max(abs(a), abs(b))
        if diff > noise_rel * scale:
            changed.append(k)
            abs_max = max(abs_max, diff)
            rel_max = max(rel_max, diff / scale if scale else 0.0)
            if diff > _rounding_tolerance(a, b):
                within_rounding = False
    for k in ("closed_at", "price_basis"):
        if before.get(k) != after.get(k):
            changed.append(k)
    if "price_basis" in changed:
        cls = "price_basis"
    elif "closed_at" in changed:
        cls = "closed_at"
    elif within_rounding:
        cls = "rounding_ohlc"
    else:
        cls = "material_ohlc"
    return {"change_class": cls, "fields_changed": changed,
            "max_abs_change": abs_max if changed else None, "max_rel_change": rel_max if changed else None}


def _naive(value):
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _age_seconds(observed_at: datetime, closed_at: str | None) -> float | None:
    if not closed_at:
        return None
    try:
        closed = datetime.fromisoformat(closed_at)
    except ValueError:
        return None
    if closed.tzinfo is None:
        closed = closed.replace(tzinfo=timezone.utc)
    obs = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=timezone.utc)
    return (obs - closed).total_seconds()


def build(*, outcome, stream_key, symbol, timeframe, config_hash, reason, detail, prior, bars,
          observed_at, recorded_at, checkpoint_updated_at, new_generation, noise_rel,
          min_interval: timedelta, throttle_due=None) -> dict:
    """The row for one trigger. `prior` is the checkpoint the stream had before it; `bars` is the
    stored history the scan just loaded."""
    before = after = None
    bar_time = (detail or {}).get("time") if isinstance(detail, dict) else None
    if reason == "stored_completed_bar_changed" and bar_time:
        old = next((b for b in prior.get("bars", []) if b.get("time") == bar_time), None)
        new = next((b for b in bars if b.get("time") == bar_time), None)
        before = {k: old.get(k) for k in _BAR_FIELDS} if old else None
        after = {k: new.get(k) for k in _BAR_FIELDS} if new else None
    cls = classify(reason, before, after, noise_rel)
    started = prior.get("generation_started_at")
    if throttle_due is None and started:
        try:
            throttle_due = (datetime.fromisoformat(started) + min_interval).isoformat()
        except ValueError:
            throttle_due = None
    pending = prior.get("restatement_pending") or {}
    pbars = prior.get("bars") or []
    from . import rzy_data, rzy_workflow
    from .receipts.identity import identity
    ident = identity([rzy_workflow, rzy_data])
    return {
        "observed_at": _naive(observed_at), "recorded_at": _naive(recorded_at),
        "stream_key": stream_key, "symbol": symbol, "timeframe": timeframe, "config_hash": config_hash,
        "outcome": outcome, "reason": (reason or "")[:64], **cls,
        "prior_generation": prior.get("generation") or 1, "new_generation": new_generation,
        "bar_time": bar_time, "bar_closed_at": (before or {}).get("closed_at"),
        "bar_age_seconds": _age_seconds(observed_at, (before or {}).get("closed_at")),
        "volume_before": (before or {}).get("volume"), "volume_after": (after or {}).get("volume"),
        "before": before, "after": after,
        "detail": None if before else detail,
        "checkpoint_last_bar": pbars[-1].get("time") if pbars else None,
        "checkpoint_updated_at": _naive(checkpoint_updated_at),
        "generation_started_at": started, "pending_since": pending.get("observed_at"),
        "throttle_due": throttle_due if isinstance(throttle_due, str) or throttle_due is None else throttle_due.isoformat(),
        "release_sha": ident["release_sha"], "code_fingerprint": ident["code_fingerprint"],
    }


def record(session, **kwargs) -> str | None:
    """Build and write one trigger row; any failure is logged and returns None."""
    from .receipts.record import record_rzy_trigger
    try:
        values = build(**kwargs)
    except Exception as exc:  # noqa: BLE001 - measurement never changes the rebuild
        log.error("rzy.rebuild_trigger_build_failed", stream_key=kwargs.get("stream_key"),
                  error=f"{type(exc).__name__}: {exc}"[:300])
        return None
    return record_rzy_trigger(session, values)
