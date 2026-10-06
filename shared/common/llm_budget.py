"""An ENFORCED token ceiling: capacity is reserved before a call, not alerted on afterwards.

THE CORRECTION THIS EXISTS FOR. The first attempt at "daily token budgets" was an email sent
when the day's total crossed 400,000. An alert does not prevent call 400,001 — it reports that
spending happened. A budget that cannot refuse is a threshold wearing a budget's name.

HOW IT BOUNDS SPEND. Capacity is reserved ATOMICALLY before a call and reconciled against the
usage the API actually reports afterwards. A reservation is an estimate, so reconciliation
matters in both directions: an over-estimate returns the difference, an under-estimate consumes
it. Without reservation, N concurrent callers each read "under budget" and all proceed.

WHAT HAPPENS WHEN IT IS EXHAUSTED. Ingestion continues and the headline is stored. Classification
is marked DEFERRED — never "neutral", never "not material", and an existing risk flag is never
cleared because a classification did not run. Absence of a label is not evidence of safety, and
a budget is a cost control, not a reason to assert that news was benign.

THE RESOLVER-OUTAGE FALLBACK HAS ITS OWN, SMALLER ALLOWANCE. "Classify everything loudly" makes
a failure visible without containing it: logging is not a bound. When the universe cannot load,
the platform cannot tell untracked from unknown, so it classifies — but only up to a separate
allowance, after which it defers and says so.

TIMEZONE AND SCOPE ARE EXPLICIT, because the previous version left both unstated: the day is the
UTC calendar day, and each scope has its own ceiling.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from .logging import get_logger

log = get_logger("llm_budget")

#: Scope names. Separate ceilings, because one runaway caller must not consume another's room.
SCOPE_NEWS_CLASSIFY = "news_classify"
SCOPE_RESOLVER_FALLBACK = "news_classify_resolver_fallback"

_DEFAULTS = {
    # Measured weekday baseline before the scope fix was 215k-256k. Set above that so normal
    # operation is not throttled, and low enough that a regression is contained rather than
    # merely reported.
    SCOPE_NEWS_CLASSIFY: 300_000,
    # Deliberately a small fraction: this path runs only when the resolver is broken, and its
    # whole purpose is to keep hot-news gating alive, not to classify the world.
    SCOPE_RESOLVER_FALLBACK: 25_000,
}


def budget_for(scope: str) -> int:
    """The ceiling for a scope, from the environment, defaulting to the measured figures above.

    Read on every call rather than cached, so a value changed in the container's environment
    takes effect at the next reservation. Changing a process environment variable still needs a
    restart to reach the process — this is not hot reconfiguration and is not described as such.
    """
    env = f"LLM_BUDGET_{scope.upper()}"
    try:
        return max(0, int(os.getenv(env, str(_DEFAULTS.get(scope, 0)))))
    except ValueError:
        return _DEFAULTS.get(scope, 0)


def utc_day_key(scope: str, now: datetime | None = None) -> str:
    """The day is the UTC calendar day, stated rather than inherited from a server setting."""
    d = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
    return f"llm:budget:{scope}:{d}"


@dataclass(frozen=True)
class Reservation:
    """The outcome of asking for capacity. `allowed=False` means DEFER, never 'assume benign'."""
    allowed: bool
    scope: str
    reserved: int
    reason: str
    key: str
    enforcement: str          # "redis" | "database" | "none"
    remaining_before: int | None = None


def _redis():
    try:
        from .redis_client import get_redis   # type: ignore
        return get_redis()
    except Exception:
        try:
            import redis as _r
            url = os.getenv("REDIS_URL")
            return _r.from_url(url) if url else None
        except Exception:
            return None


def _db_used_today(scope: str) -> int | None:
    """Authoritative but NOT atomic: a fallback when Redis is unavailable."""
    try:
        from sqlalchemy import text
        from db import SessionLocal
        call_site = SCOPE_NEWS_CLASSIFY
        with SessionLocal() as s:
            return int(s.execute(text(
                "SELECT COALESCE(SUM(COALESCE(input_tokens,0)+COALESCE(output_tokens,0)),0)"
                " FROM llm_call_log"
                " WHERE call_site = :cs AND created_at >= date_trunc('day', now() at time zone 'utc')"
            ), {"cs": call_site}).scalar() or 0)
    except Exception as exc:
        log.warning("llm_budget.db_fallback_failed", error=str(exc))
        return None


#: With no atomic counter, concurrent callers can each pass the same check. The margin makes
#: that overshoot bounded rather than unbounded; it is not a substitute for reservation.
_DB_FALLBACK_MARGIN = 0.90


def reserve(estimated_tokens: int, *, scope: str = SCOPE_NEWS_CLASSIFY) -> Reservation:
    """Claim capacity BEFORE a call. Returns allowed=False when the ceiling is reached."""
    budget = budget_for(scope)
    key = utc_day_key(scope)
    if budget <= 0:
        return Reservation(True, scope, 0, "no ceiling configured for this scope", key, "none")

    r = _redis()
    if r is not None:
        try:
            used = int(r.incrby(key, estimated_tokens))
            r.expire(key, 172800)
            if used > budget:
                r.decrby(key, estimated_tokens)       # give it back; we are not spending it
                return Reservation(
                    False, scope, 0,
                    f"daily {scope} ceiling reached: {used - estimated_tokens:,} of "
                    f"{budget:,} UTC-day tokens already reserved",
                    key, "redis", remaining_before=max(0, budget - (used - estimated_tokens)))
            return Reservation(True, scope, estimated_tokens, "reserved", key, "redis",
                               remaining_before=budget - (used - estimated_tokens))
        except Exception as exc:
            log.warning("llm_budget.redis_failed", scope=scope, error=str(exc))

    # REDIS UNAVAILABLE — an explicit policy, not silence. The database knows what was spent
    # but cannot reserve, so concurrent callers can overshoot; a margin bounds that overshoot.
    used = _db_used_today(scope)
    if used is None:
        # Neither counter is available. FAIL CLOSED: an unbounded spend path with no way to
        # measure it is the one case where deferring is clearly safer than proceeding.
        return Reservation(
            False, scope, 0,
            "neither Redis nor the database could report today's usage, so spending cannot be "
            "bounded; classification is deferred rather than issued blind",
            key, "none")
    allowance = int(budget * _DB_FALLBACK_MARGIN)
    if used + estimated_tokens > allowance:
        return Reservation(
            False, scope, 0,
            f"daily {scope} ceiling reached under the database fallback: {used:,} of "
            f"{allowance:,} (a {int(_DB_FALLBACK_MARGIN * 100)}% margin of {budget:,}, because "
            f"without Redis the count cannot be reserved atomically)",
            key, "database", remaining_before=max(0, allowance - used))
    return Reservation(True, scope, estimated_tokens, "allowed under the database fallback",
                       key, "database", remaining_before=allowance - used)


def reconcile(res: Reservation, actual_tokens: int) -> None:
    """Settle a reservation against what the API actually reported.

    BOTH DIRECTIONS MATTER. An over-estimate that is never returned shrinks the day's real
    capacity until the budget throttles work it could have afforded; an under-estimate that is
    never charged lets the ceiling be exceeded while reporting that it was not.
    """
    if not res.allowed or res.enforcement != "redis":
        return
    delta = int(actual_tokens) - int(res.reserved)
    if delta == 0:
        return
    r = _redis()
    if r is None:
        return
    try:
        r.incrby(res.key, delta) if delta > 0 else r.decrby(res.key, -delta)
    except Exception as exc:
        log.warning("llm_budget.reconcile_failed", scope=res.scope, error=str(exc))


def status(scope: str = SCOPE_NEWS_CLASSIFY) -> dict:
    """What the dashboard needs: used, remaining, and HOW the ceiling is being enforced."""
    budget = budget_for(scope)
    key = utc_day_key(scope)
    r = _redis()
    reserved = None
    enforcement = "none"
    if r is not None:
        try:
            raw = r.get(key)
            reserved = int(raw) if raw is not None else 0
            enforcement = "redis"
        except Exception:
            reserved = None
    if reserved is None:
        reserved = _db_used_today(scope)
        enforcement = "database" if reserved is not None else "none"
    return {
        "scope": scope,
        "budget": budget,
        "reserved_or_used": reserved,
        "remaining": (max(0, budget - reserved) if reserved is not None else None),
        "enforcement": enforcement,
        "day_basis": "UTC calendar day",
        "note": ("`reserved_or_used` is reserved-and-reconciled capacity under Redis, or "
                 "actual logged usage under the database fallback — these are different "
                 "measures and the enforcement field says which is in effect"),
    }
