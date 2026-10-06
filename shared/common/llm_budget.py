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

#: SCOPES NEST. A resolver-fallback call spends news-classification budget too, so it must fit
#: under BOTH ceilings. Treating them as independent allowances would permit 300k + 25k = 325k.
_PARENT = {SCOPE_RESOLVER_FALLBACK: SCOPE_NEWS_CLASSIFY}

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
    enforcement: str                      # "redis" | "database" | "none"
    reservation_id: str = ""
    #: The UTC day the capacity came from. Settlement must return to THIS day, not to whatever
    #: day it is when the response arrives — a call started at 23:59:58 settles after midnight.
    day: str = ""
    keys: tuple = ()                      # every ceiling this reservation charged, parent first
    remaining_before: int | None = None


def scope_chain(scope: str) -> list[str]:
    """This scope and every ceiling above it, child first."""
    chain, cur = [], scope
    while cur:
        chain.append(cur)
        cur = _PARENT.get(cur)
    return chain


def upper_bound_tokens(prompt_chars: int, max_output_tokens: int) -> int:
    """The MOST a call could cost, not what it probably costs.

    Reserving an estimate and charging the difference afterwards RECORDS an overshoot; it cannot
    prevent one. So the reservation is an upper bound: a deliberately pessimistic input estimate
    plus the maximum output the request itself permits (`max_tokens`), which is the hard ceiling
    the provider will not exceed.

    The input divisor is 2.5 rather than the usual ~4 characters per token because an
    underestimate here is exactly the failure this function exists to remove; dense or
    non-English text tokenises worse than prose.
    """
    return int(prompt_chars / 2.5) + int(max_output_tokens) + 256


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


#: Reserve against EVERY ceiling or none. Redis has no conditional multi-key increment, and
#: doing it with separate commands leaves a window where a parent is charged and a child refuses.
_RESERVE_LUA = """
local n = tonumber(ARGV[1])
for i = 1, #KEYS do
  local cap = tonumber(ARGV[1 + i])
  local v = redis.call('INCRBY', KEYS[i], n)
  redis.call('EXPIRE', KEYS[i], 172800)
  if v > cap then
    for j = 1, i do redis.call('DECRBY', KEYS[j], n) end
    return {0, KEYS[i], v - n}
  end
end
return {1, '', 0}
"""


def _day_str(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()


def _seed_from_db_if_reset(r, scope: str, day: str) -> None:
    """A flushed or evicted Redis counter would admit a fresh ceiling's worth of calls.

    The database ledger is durable, so if it records more reserved capacity for this day than
    Redis does, Redis lost state and is re-seeded before any new call is admitted.
    """
    try:
        from sqlalchemy import text
        from db import SessionLocal
        with SessionLocal() as s:
            durable = int(s.execute(text(
                "SELECT COALESCE(reserved, 0) FROM llm_budget_day"
                " WHERE scope = :sc AND day = :d"), {"sc": scope, "d": day}).scalar() or 0)
        cur = int(r.get(utc_day_key(scope)) or 0)
        if durable > cur:
            r.set(utc_day_key(scope), durable, ex=172800)
            log.warning("llm_budget.redis_reseeded_after_reset", scope=scope,
                        redis=cur, durable=durable)
    except Exception as exc:
        log.warning("llm_budget.reseed_check_failed", scope=scope, error=str(exc))


def _db_reserve(chain: list[str], caps: list[int], n: int, day: str) -> tuple[bool, str, int]:
    """ATOMIC in the database: increment only if the result fits, in one statement per ceiling.

    This replaces a sum-plus-margin check, which could not bound concurrent callers: with usage
    at 260,000 of 300,000 every caller reads "below the margin" and every one proceeds, and
    in-flight calls are not logged yet so the real gap is wider than the measured one.
    """
    from sqlalchemy import text
    from db import SessionLocal
    charged: list[str] = []
    try:
        with SessionLocal() as s:
            for scope, cap in zip(chain, caps):
                row = s.execute(text(
                    "INSERT INTO llm_budget_day (scope, day, reserved) VALUES (:sc, :d, :n)"
                    " ON CONFLICT (scope, day) DO UPDATE SET reserved = llm_budget_day.reserved + :n"
                    "   WHERE llm_budget_day.reserved + :n <= :cap"
                    " RETURNING reserved"
                ), {"sc": scope, "d": day, "n": n, "cap": cap}).first()
                if row is None:
                    for done in charged:          # all or nothing
                        s.execute(text(
                            "UPDATE llm_budget_day SET reserved = reserved - :n"
                            " WHERE scope = :sc AND day = :d"), {"n": n, "sc": done, "d": day})
                    s.commit()
                    return False, scope, cap
                charged.append(scope)
            s.commit()
        return True, "", 0
    except Exception as exc:
        log.warning("llm_budget.db_reserve_failed", error=str(exc))
        raise


def reserve(prompt_chars: int, max_output_tokens: int, *,
            scope: str = SCOPE_NEWS_CLASSIFY) -> Reservation:
    """Claim an UPPER BOUND on this call's cost, against every ceiling that applies."""
    import uuid
    chain = scope_chain(scope)
    caps = [budget_for(sc) for sc in chain]
    day = _day_str()
    rid = uuid.uuid4().hex[:32]
    n = upper_bound_tokens(prompt_chars, max_output_tokens)

    if all(c <= 0 for c in caps):
        return Reservation(True, scope, 0, "no ceiling configured for this scope", "none",
                           rid, day)

    r = _redis()
    if r is not None:
        try:
            for sc in chain:
                _seed_from_db_if_reset(r, sc, day)
            keys = [utc_day_key(sc, datetime.now(timezone.utc)) for sc in chain]
            res = r.eval(_RESERVE_LUA, len(keys), *keys, n, *caps)
            ok = int(res[0]) == 1
            if not ok:
                blocked = res[1].decode() if isinstance(res[1], bytes) else str(res[1])
                return Reservation(
                    False, scope, 0,
                    f"ceiling reached on {blocked}: {int(res[2]):,} of its UTC-day allowance "
                    f"already reserved, and this call needs {n:,} more",
                    "redis", rid, day, tuple(keys))
            _record_reservation(rid, scope, day, n)
            return Reservation(True, scope, n, "reserved (upper bound)", "redis", rid, day,
                               tuple(keys))
        except Exception as exc:
            log.warning("llm_budget.redis_failed", scope=scope, error=str(exc))

    # REDIS UNAVAILABLE — the database reserves ATOMICALLY rather than estimating with a margin.
    try:
        ok, blocked, cap = _db_reserve(chain, caps, n, day)
    except Exception:
        # Neither counter can reserve. FAIL CLOSED: spending that cannot be bounded is deferred.
        return Reservation(
            False, scope, 0,
            "neither Redis nor the database could reserve capacity, so spending cannot be "
            "bounded; classification is deferred rather than issued blind",
            "none", rid, day)
    if not ok:
        return Reservation(
            False, scope, 0,
            f"ceiling reached on {blocked} under the database reservation: this call's upper "
            f"bound of {n:,} does not fit under {cap:,}",
            "database", rid, day)
    _record_reservation(rid, scope, day, n)
    return Reservation(True, scope, n, "reserved (upper bound, database)", "database", rid, day)


def _record_reservation(rid: str, scope: str, day: str, n: int) -> None:
    try:
        from sqlalchemy import text
        from db import SessionLocal
        with SessionLocal() as s:
            s.execute(text(
                "INSERT INTO llm_reservations (id, scope, day, reserved)"
                " VALUES (:i, :sc, :d, :n) ON CONFLICT (id) DO NOTHING"),
                {"i": rid, "sc": scope, "d": day, "n": n})
            s.commit()
    except Exception as exc:
        log.warning("llm_budget.reservation_record_failed", error=str(exc))


#: What a call's outcome says about what to settle.
OUTCOME_OK = "ok"              # the provider reported usage; settle at that
OUTCOME_AMBIGUOUS = "ambiguous"  # timeout or unknown; it may have been charged in full
OUTCOME_FAILED = "failed"      # refused before any work; the reservation returns


def reconcile(res: Reservation, actual_tokens: int | None, *,
              outcome: str = OUTCOME_OK) -> None:
    """Settle ONCE, against the day the capacity came from.

    AN AMBIGUOUS OUTCOME DOES NOT REFUND. A timed-out request may have been served and charged,
    so returning its capacity would let the ceiling be passed by exactly the calls nobody can
    account for. It settles at the full reservation instead.
    """
    if not res.allowed or not res.reservation_id:
        return
    if outcome == OUTCOME_AMBIGUOUS:
        settle = res.reserved
    elif outcome == OUTCOME_FAILED:
        settle = 0
    else:
        settle = int(actual_tokens or 0)

    # IDEMPOTENT: a second settlement would refund capacity that was spent.
    try:
        from sqlalchemy import text
        from db import SessionLocal
        with SessionLocal() as s:
            row = s.execute(text(
                "UPDATE llm_reservations SET settled_tokens = :t, outcome = :o,"
                " settled_at = now() WHERE id = :i AND settled_at IS NULL"
                " RETURNING reserved, day"), {"t": settle, "o": outcome, "i": res.reservation_id}
            ).first()
            if row is None:
                return                      # already settled; do nothing
            reserved, day = int(row[0]), row[1].isoformat()
            delta = settle - reserved
            if delta:
                for sc in scope_chain(res.scope):
                    s.execute(text(
                        "UPDATE llm_budget_day SET reserved = GREATEST(0, reserved + :d)"
                        " WHERE scope = :sc AND day = :day"),
                        {"d": delta, "sc": sc, "day": day})
            s.commit()
    except Exception as exc:
        log.warning("llm_budget.reconcile_db_failed", error=str(exc))
        return

    if delta and res.enforcement == "redis":
        r = _redis()
        if r is None:
            return
        try:
            # SETTLE AGAINST THE ORIGINAL DAY'S KEYS, not today's — a call started before UTC
            # midnight must return its capacity to the day it took it from.
            for sc in scope_chain(res.scope):
                key = f"llm:budget:{sc}:{day}"
                r.incrby(key, delta) if delta > 0 else r.decrby(key, -delta)
        except Exception as exc:
            log.warning("llm_budget.reconcile_redis_failed", error=str(exc))


def status(scope: str = SCOPE_NEWS_CLASSIFY) -> dict:
    """What the dashboard needs: used, remaining, and HOW the ceiling is being enforced."""
    budget = budget_for(scope)
    day = _day_str()
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
        try:
            from sqlalchemy import text
            from db import SessionLocal
            with SessionLocal() as s:
                reserved = int(s.execute(text(
                    "SELECT COALESCE(reserved,0) FROM llm_budget_day"
                    " WHERE scope = :sc AND day = :d"), {"sc": scope, "d": day}).scalar() or 0)
            enforcement = "database"
        except Exception:
            reserved = None
    return {
        "scope": scope,
        "parent_scope": _PARENT.get(scope),
        "budget": budget,
        "reserved_or_used": reserved,
        "remaining": (max(0, budget - reserved) if reserved is not None else None),
        "enforcement": enforcement,
        "day_basis": "UTC calendar day",
        "note": ("reservations are UPPER BOUNDS taken before each call and settled against the "
                 "day they were taken from. A nested scope also charges every ceiling above it, "
                 "so its spending is not additional to the parent's."),
    }
