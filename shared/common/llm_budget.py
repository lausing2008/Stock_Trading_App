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


#: Per-message framing the provider adds around the content this platform sends: role markers,
#: message delimiters and the response preamble. Not published as an exact figure, so it is
#: allowed for generously rather than assumed to be zero.
_FRAMING_TOKENS_PER_MESSAGE = 16
_MESSAGES_PER_CALL = 2          # system + user

#: JSON serialisation adds quoting and escaping around the text itself.
_SERIALISATION_FACTOR = 1.15


def upper_bound_tokens(request_text: str, max_output_tokens: int) -> int:
    """A CONSERVATIVE admission estimate. Read what it does and does not cover.

    WHAT IT RESTS ON. A byte-pair encoder builds each token from at least one byte, so a text
    cannot tokenise to more tokens than its UTF-8 byte count. That holds for the CONTENT this
    platform sends, and it is why bytes rather than characters are used: measured on strings
    this platform can actually receive, CJK is 3.0 bytes per character and emoji 4.0, so the
    character-based estimate this replaced under-reserved exactly where it mattered.

    WHAT IT DOES NOT ESTABLISH, stated because the previous version claimed an unconditional
    hard ceiling and was not entitled to:

      * The provider's accounting covers the serialised REQUEST, not the raw text. JSON
        quoting and escaping are allowed for by a factor, not derived.
      * Per-message framing — role markers, delimiters, any preamble — is added by the provider
        and is not published as an exact figure. A generous per-message allowance is included.
      * The provider's tokenizer is not this code. A future model, or a change to how a request
        is assembled, could in principle exceed this.

    So this is ADMISSION CONTROL that is conservative by construction and by a wide margin, not
    a proven provider-side ceiling. The ceiling that actually binds is on SETTLED usage, which
    is the number the provider reports and which reconciliation applies immediately after every
    call. A mis-estimate here affects how eagerly admission refuses, not what gets counted.
    """
    content = int(len(request_text.encode("utf-8")) * _SERIALISATION_FACTOR)
    framing = _FRAMING_TOKENS_PER_MESSAGE * _MESSAGES_PER_CALL
    return content + framing + int(max_output_tokens)


def _redis():
    """Only for `status()` caching. NOT part of the reservation decision — see `reserve`."""
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


def _day_str(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()


def _db_reserve(chain: list[str], caps: list[int], n: int, day: str,
                rid: str, scope: str) -> tuple[bool, str, int]:
    """Reserve against every ceiling AND record the reservation, in ONE transaction.

    THIS IS THE WHOLE DESIGN, and it replaces a Redis gate mirrored to a database record. That
    arrangement had no answer to the question "what is true between the two writes": a crash
    after Redis admitted but before the database recorded left capacity reserved in one store
    and absent from the other, and a later Redis reset would restore the LOWER figure and leak
    the difference. A caller on the Redis path and one on the database fallback could also
    admit against counters that did not know about each other.

    Rather than reconcile two authorities, there is now one. Every ceiling in the chain and the
    reservation row are written in a single transaction: it commits entirely or not at all, so
    there is no window in which part of it is true. The conditional UPSERT is what makes each
    increment atomic against concurrent transactions; the shared transaction is what makes the
    set of them atomic together.

    Redis is still used — to serve `status()` without a query — but it decides nothing.
    """
    from sqlalchemy import text
    from db import SessionLocal
    try:
        with SessionLocal() as s:
            for scope_name, cap in zip(chain, caps):
                row = s.execute(text(
                    # THE INSERT BRANCH NEEDS THE CAP TOO: with the check only on DO UPDATE,
                    # the FIRST reservation of a day inserted unconditionally, admitting a
                    # single call larger than the entire ceiling.
                    "INSERT INTO llm_budget_day (scope, day, reserved)"
                    " SELECT :sc, :d, :n WHERE :n <= :cap"
                    " ON CONFLICT (scope, day) DO UPDATE"
                    "   SET reserved = llm_budget_day.reserved + :n"
                    "   WHERE llm_budget_day.reserved + :n <= :cap"
                    " RETURNING reserved"
                ), {"sc": scope_name, "d": day, "n": n, "cap": cap}).first()
                if row is None:
                    s.rollback()          # nothing charged; the transaction never committed
                    return False, scope_name, cap
            s.execute(text(
                "INSERT INTO llm_reservations (id, scope, day, reserved)"
                " VALUES (:i, :sc, :d, :n) ON CONFLICT (id) DO NOTHING"),
                {"i": rid, "sc": scope, "d": day, "n": n})
            s.commit()
        return True, "", 0
    except Exception as exc:
        log.warning("llm_budget.db_reserve_failed", error=str(exc))
        raise


def reserve(request_text: str, max_output_tokens: int, *,
            scope: str = SCOPE_NEWS_CLASSIFY) -> Reservation:
    """Claim a conservative upper bound on this call's cost, against every ceiling that applies.

    ONE AUTHORITY. The ceilings and the reservation row are written in a single PostgreSQL
    transaction, so a crash at any point leaves either all of it or none of it. Admission STOPS
    when that transaction cannot be made — spending that cannot be recorded is not bounded,
    and a cost control that fails open is not a control.
    """
    import uuid
    chain = scope_chain(scope)
    caps = [budget_for(sc) for sc in chain]
    day = _day_str()
    rid = uuid.uuid4().hex[:32]
    n = upper_bound_tokens(request_text, max_output_tokens)

    if all(c <= 0 for c in caps):
        return Reservation(True, scope, 0, "no ceiling configured for this scope", "none",
                           rid, day)
    try:
        ok, blocked, cap = _db_reserve(chain, caps, n, day, rid, scope)
    except Exception:
        # FAIL CLOSED. There is no second counter to fall back to, by design: a fallback is a
        # second authority, and two authorities cannot bound one ceiling.
        return Reservation(
            False, scope, 0,
            "the budget ledger is unavailable, so this call's capacity cannot be reserved or "
            "recorded; classification is deferred rather than issued unbounded",
            "none", rid, day)
    if not ok:
        return Reservation(
            False, scope, 0,
            f"ceiling reached on {blocked}: this call's upper bound of {n:,} does not fit "
            f"under its {cap:,} UTC-day allowance",
            "database", rid, day)
    _cache_status(chain, n, day)
    return Reservation(True, scope, n, "reserved (conservative upper bound)", "database",
                       rid, day)


def _cache_status(chain: list[str], n: int, day: str) -> None:
    """Mirror to Redis so `status()` is cheap. Advisory only — it decides nothing."""
    r = _redis()
    if r is None:
        return
    try:
        for sc in chain:
            r.incrby(f"llm:budget:{sc}:{day}", n)
            r.expire(f"llm:budget:{sc}:{day}", 172800)
    except Exception:
        pass


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
    """Settle ONCE, against the day the capacity came from, in the ledger that granted it.

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

    try:
        from sqlalchemy import text
        from db import SessionLocal
        with SessionLocal() as s:
            # IDEMPOTENT AND ATOMIC WITH THE COUNTER ADJUSTMENT: claiming the row and returning
            # the difference happen in one transaction, so a crash between them is impossible
            # and a second settlement finds nothing to claim.
            row = s.execute(text(
                "UPDATE llm_reservations SET settled_tokens = :t, outcome = :o,"
                " settled_at = now() WHERE id = :i AND settled_at IS NULL"
                " RETURNING reserved, day"), {"t": settle, "o": outcome,
                                              "i": res.reservation_id}).first()
            if row is None:
                return
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
        log.warning("llm_budget.reconcile_failed", error=str(exc))
        return

    if delta:
        r = _redis()
        if r is None:
            return
        try:
            # ADVISORY CACHE, settled against the ORIGINAL day — a call started at 23:59:58
            # must not credit the new day.
            for sc in scope_chain(res.scope):
                key = f"llm:budget:{sc}:{day}"
                r.incrby(key, delta) if delta > 0 else r.decrby(key, -delta)
        except Exception:
            pass


def status(scope: str = SCOPE_NEWS_CLASSIFY) -> dict:
    """What the dashboard needs. The LEDGER is authoritative; Redis only avoids a query."""
    budget = budget_for(scope)
    day = _day_str()
    reserved, source = None, "none"
    try:
        from sqlalchemy import text
        from db import SessionLocal
        with SessionLocal() as s:
            reserved = int(s.execute(text(
                "SELECT COALESCE(reserved,0) FROM llm_budget_day"
                " WHERE scope = :sc AND day = :d"), {"sc": scope, "d": day}).scalar() or 0)
        source = "ledger"
    except Exception:
        r = _redis()
        if r is not None:
            try:
                reserved = int(r.get(f"llm:budget:{scope}:{day}") or 0)
                source = "redis cache (advisory — the ledger could not be read)"
            except Exception:
                reserved = None
    return {
        "scope": scope,
        "parent_scope": _PARENT.get(scope),
        "budget": budget,
        "reserved_or_used": reserved,
        "remaining": (max(0, budget - reserved) if reserved is not None else None),
        "enforcement": "postgresql (single authority)",
        "read_from": source,
        "day_basis": "UTC calendar day",
        "note": ("Ceilings and reservation rows are written in ONE PostgreSQL transaction, so "
                 "there is no state in which part of a reservation is true. Redis holds an "
                 "advisory copy for display and decides nothing. The input bound is a "
                 "CONSERVATIVE admission estimate, not a proven provider-side ceiling — see "
                 "upper_bound_tokens for what it does and does not cover."),
    }
