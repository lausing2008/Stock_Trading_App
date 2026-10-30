"""Intraday Stage C contract: sessions, identities, canonical hashes, states and decision rules.

docs/features/2026-10-29-intraday-stage-c-contract-design.md (revision 3). Pure: stdlib plus the
exchange calendar. No database, no provider calls, no generator. Every rule a later writer or
shadow generator must follow lives here so it can be tested on frozen inputs.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from common.market_calendar import calendar_coverage, session_bounds

CONTRACT_VERSION = "intraday-c1"
STRATEGY_ID = "AI_INTRADAY"
SETTLE = timedelta(minutes=2)          # RZY's settle cutoff: an eligibility bound, not availability
STEP_MIN = {"5m": 5, "15m": 15}
NY = ZoneInfo("America/New_York")
OBSERVED_RUN = "observed"


class ContractViolation(ValueError):
    """A record that breaks the contract. It is refused, never stored."""


class CalendarCoverageExpired(ValueError):
    pass


# ── Parameters (versioned research defaults; no performance claim) ─────────────────────────

@dataclass(frozen=True)
class Params:
    entry_window_min: int = 15
    last_entry_offset_min: int = 30
    liquidation_offset_min: int = 10
    max_hold_min: int = 120
    forecast_windows_min: tuple = (30, 60, 120)
    slippage_bps: float = 5.0           # adverse, on market and stop fills
    cost_bps: float = 0.0               # per side
    fee_per_share: float = 0.0          # per side; the user's broker is commission-free
    replay_latency_s: int = 60          # replay_logical clock only; an assumption, labelled

    def as_config(self) -> dict:
        d = asdict(self)
        d["forecast_windows_min"] = list(self.forecast_windows_min)
        return d


def config_hash(params: Params) -> str:
    return sha(params.as_config())


# ── Canonical encoding and hashes (design §4) ─────────────────────────────────────────────

def utc(value: datetime) -> datetime:
    """Aware UTC. Naive values are read as UTC, the platform's storage convention."""
    if not isinstance(value, datetime):
        raise ContractViolation(f"not a datetime: {value!r}")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _norm(v):
    if isinstance(v, datetime):
        return utc(v).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool) or v is None or isinstance(v, (int, str)):
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ContractViolation("non-finite number in a hashed record")
        return v                       # json renders float via repr(): shortest round trip
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    raise ContractViolation(f"unhashable value type {type(v).__name__}")


def canonical(value) -> bytes:
    """UTF-8 JSON, no whitespace, sorted keys, NaN/inf refused. Hashed records are arrays."""
    return json.dumps(_norm(value), separators=(",", ":"), sort_keys=True, ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def raw_hash(*, stock_id, symbol, timeframe, bar_open, open, high, low, close, adj_close, volume, session):
    return sha(["raw", stock_id, symbol.upper(), timeframe, bar_open, open, high, low, close, adj_close,
                volume, session])


def source_hash(raw: str, provider: str | None):
    return sha(["source", raw, provider or "unknown"])


def input_hash(*, timeframe, bar_open, open, high, low, close, volume, price_basis, price_basis_reason):
    return sha(["input", timeframe, bar_open, open, high, low, close, volume, price_basis, price_basis_reason])


def derived_hash(input_h: str, constituent_source_hashes):
    return sha(["derived", input_h, list(constituent_source_hashes)])


def identity_hash(*, strategy_id, strategy_version, strategy_source_hash, contract_version, config_hash):
    return sha(["identity", strategy_id, strategy_version, strategy_source_hash, contract_version, config_hash])


def evaluation_key(*, run_key, identity, stock_id, trigger_bar_open):
    return sha(["evaluation", run_key, identity, stock_id, trigger_bar_open])


def occurrence_key(*, run_key, identity, symbol, trigger_bar_open, direction):
    return sha(["occurrence", run_key, identity, symbol.upper(), trigger_bar_open, direction])


def outcome_key(*, occurrence, kind, window, evaluator_version, evidence_hash):
    return sha(["outcome", occurrence, kind, window, evaluator_version, evidence_hash])


# ── Venue and session (design §3) ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SessionTimes:
    session_date: date
    open: datetime
    close: datetime
    last_entry_at: datetime
    liquidation_at: datetime


def session_times(market: str, d: date, params: Params = Params()) -> SessionTimes | None:
    """The session's times in UTC, or None on a non-session date. Raises UnsupportedMarket for
    anything but US and CalendarCoverageExpired past the calendar's coverage."""
    from common.horizons import require_intraday_market
    require_intraday_market(market)
    if d.year > calendar_coverage()["nyse"]:
        raise CalendarCoverageExpired(d.isoformat())
    b = session_bounds("US", d)
    if not b:
        return None
    o, c = utc(b[0]), utc(b[1])
    return SessionTimes(d, o, c, c - timedelta(minutes=params.last_entry_offset_min),
                        c - timedelta(minutes=params.liquidation_offset_min))


def session_date_of(bar_open: datetime) -> date:
    return utc(bar_open).astimezone(NY).date()


def bar_close(bar_open: datetime, timeframe: str, times: SessionTimes) -> datetime:
    return min(utc(bar_open) + timedelta(minutes=STEP_MIN[timeframe]), times.close)


def readable_at(bar_open: datetime, timeframe: str, times: SessionTimes) -> datetime:
    return bar_close(bar_open, timeframe, times) + SETTLE


def grid_opens(times: SessionTimes, step_min: int = 5):
    t = times.open
    while t < times.close:
        yield t
        t += timedelta(minutes=step_min)


def first_grid_open_after(after: datetime, before: datetime, times: SessionTimes):
    """Smallest 5m grid open t with after < t < before (strict both sides), else None."""
    after, before = utc(after), utc(before)
    for t in grid_opens(times):
        if after < t < before:
            return t
    return None


def first_grid_open_at_or_after(at: datetime, times: SessionTimes):
    at = utc(at)
    for t in grid_opens(times):
        if t >= at:
            return t
    return None


# ── States (design §9) ────────────────────────────────────────────────────────────────────

PRE_ACTIONABLE_TERMINAL = ("expired_before_detection", "no_fill_opportunity",
                           "cancelled_before_actionable", "research_only_unsupported_direction")
#: Entered, but the session ended without a recorded exit (`unresolved_exit_at_close`), or the
#: holding path has an unobserved interval in which an exit could have happened unseen
#: (`path_indeterminate`, review IC-04). Both are open research exposure: session-terminal,
#: not final, and listed until explicitly reconciled.
OPEN_EXPOSURE = ("unresolved_exit_at_close", "path_indeterminate")
SESSION_TERMINAL = PRE_ACTIONABLE_TERMINAL + (
    "expired", "cancelled", "rejected_invalid_bracket_at_fill", "exited", "chronology_ambiguous") + OPEN_EXPOSURE
FINAL = tuple(s for s in SESSION_TERMINAL if s not in OPEN_EXPOSURE) + ("reconciled",)
STATES = ("detected", "actionable", "entered") + SESSION_TERMINAL + ("reconciled",)
LEGAL_PAIRS = frozenset(
    [(None, "detected")]
    + [("detected", s) for s in ("actionable",) + PRE_ACTIONABLE_TERMINAL + ("chronology_ambiguous",)]
    + [("actionable", s) for s in ("entered", "expired", "cancelled", "rejected_invalid_bracket_at_fill",
                                   "chronology_ambiguous")]
    + [("entered", "exited"), ("entered", "unresolved_exit_at_close"), ("entered", "path_indeterminate"),
       ("unresolved_exit_at_close", "reconciled"), ("path_indeterminate", "reconciled")])
#: A post-actionable cancellation is always decided from data. The session's end is not a
#: cancellation: an unfilled setup is `expired`, an open one is liquidated or left unresolved.
CANCEL_REASONS = ("invalidated_price", "input_revised", "data_unavailable")
RESULTS = ("setup", "no_setup", "wait", "data_unavailable", "unsupported_market", "calendar_coverage_expired")


def check_append(latest: tuple | None, seq: int, from_state, to_state) -> None:
    """Validate one transition append against the latest stored one: `latest` is
    (seq, to_state) or None. The database trigger enforces the same rule (design §9)."""
    if (from_state, to_state) not in LEGAL_PAIRS:
        raise ContractViolation(f"illegal transition {from_state}->{to_state}")
    if latest is None:
        if not (seq == 1 and from_state is None and to_state == "detected"):
            raise ContractViolation("first transition must be seq 1 (NULL -> detected)")
        return
    if seq != latest[0] + 1 or from_state != latest[1]:
        raise ContractViolation(f"append out of order: latest {latest}, got seq {seq} from {from_state}")


# ── Detection-time decision (design §5) ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Detection:
    state: str                         # 'actionable' or a pre-actionable terminal state
    reason: str | None
    actionable_at: datetime | None
    entry_expires_at: datetime


def decide_at_detection(*, trigger_bar_open, detected_at, direction, times: SessionTimes,
                        params: Params = Params(), stop_breached: bool, inputs_ok: bool) -> Detection:
    """The four checks, in priority order, using only evidence readable at `detected_at`."""
    detected_at = utc(detected_at)
    expires = min(bar_close(trigger_bar_open, "5m", times) + timedelta(minutes=params.entry_window_min),
                  times.last_entry_at)
    if detected_at < readable_at(trigger_bar_open, "5m", times):
        raise ContractViolation("detected before the trigger bar was readable")
    if detected_at >= expires:
        return Detection("expired_before_detection", None, None, expires)
    if first_grid_open_after(detected_at, expires, times) is None:
        return Detection("no_fill_opportunity", None, None, expires)
    if not inputs_ok:
        return Detection("cancelled_before_actionable", "data_unavailable", None, expires)
    if stop_breached:
        return Detection("cancelled_before_actionable", "invalidated_price", None, expires)
    if direction == "bearish":
        return Detection("research_only_unsupported_direction", "borrow_unknown", None, expires)
    if direction != "bullish":
        raise ContractViolation(f"unknown direction {direction!r}")
    return Detection("actionable", None, detected_at, expires)


# ── Later-event timing (design §5, CDR-R2-01) ─────────────────────────────────────────────

@dataclass(frozen=True)
class EventTimes:
    eligible_at: datetime | None       # lower bound only
    observed_at: datetime | None
    decided_at: datetime | None
    clock_basis: str                   # 'observed' | 'replay_logical'


def effective_time(ev: EventTimes, params: Params = Params()):
    """When the event takes effect, or None when its chronology cannot be established."""
    if ev.clock_basis == "observed":
        if ev.decided_at is None or ev.observed_at is None:
            return None
        if ev.eligible_at is not None and not utc(ev.eligible_at) <= utc(ev.observed_at):
            raise ContractViolation("evidence observed before it was eligible")
        if not utc(ev.observed_at) <= utc(ev.decided_at):
            raise ContractViolation("decided before the evidence was observed")
        return utc(ev.decided_at)
    if ev.clock_basis == "replay_logical":
        if ev.eligible_at is None:
            return None
        return utc(ev.eligible_at) + timedelta(seconds=params.replay_latency_s)
    raise ContractViolation(f"unknown clock basis {ev.clock_basis!r}")


def candidate_entry_open(actionable_at: datetime) -> datetime:
    """The first 5m grid open strictly after `actionable_at`: where a market entry would fill.
    US sessions open on a 5-minute boundary, so the grid is the 5-minute clock (equal to
    first_grid_open_after within a session; the database trigger uses the same arithmetic)."""
    a = utc(actionable_at)
    floor = a - timedelta(minutes=a.minute % 5, seconds=a.second, microseconds=a.microsecond)
    return floor + timedelta(minutes=5)


def entry_bar_readable_at(entry_open: datetime) -> datetime:
    """When the entry bar's absence becomes knowable: its readable_at. The entry precedes the last
    entry time, so the bar never reaches the session close."""
    return utc(entry_open) + timedelta(minutes=STEP_MIN["5m"]) + SETTLE


# ── Transition timing by event type (review ICR-01) ───────────────────────────────────────

#: Research fills and scheduled orders: they take effect at their bar open, which may precede
#: the time they are recorded. Their own fill rules (§6) and chronology bound them.
FILL_STATES = ("entered", "rejected_invalid_bracket_at_fill", "exited", "unresolved_exit_at_close",
               "path_indeterminate")
#: Records that claim no market time: they take effect when decided.
DECISION_TIME_STATES = ("chronology_ambiguous", "reconciled")


def required_effective_time(to_state: str, ev: EventTimes, *, detected_at, entry_expires_at,
                            params: Params = Params()):
    """The only effective time a non-fill transition may carry, or None for a fill state (which
    supplies its own). A data-dependent cancellation takes effect when it was decided (observed)
    or at eligible + replay latency (replay): it is never dated earlier, and without its
    observation evidence it is not a cancellation at all (record chronology_ambiguous)."""
    if to_state in FILL_STATES:
        return None
    if to_state == "detected" or to_state in ("actionable",) + PRE_ACTIONABLE_TERMINAL:
        return utc(detected_at)
    if to_state == "expired":
        return utc(entry_expires_at)
    if to_state in DECISION_TIME_STATES:
        return utc(ev.decided_at)
    if to_state == "cancelled":
        eff = effective_time(ev, params)
        if eff is None:
            raise ContractViolation("a cancellation without its observation evidence has no known time: "
                                    "record chronology_ambiguous")
        return eff
    raise ContractViolation(f"no timing rule for {to_state!r}")


def check_cancellation_before_entry(*, effective_at, actionable_at, reason, eligible_at) -> None:
    """A cancellation defeats the entry only if it took effect before the candidate entry opened
    (§5); at or after it the entry stands, and a tie is chronology_ambiguous. The one exception
    is the entry bar's own confirmed absence, which is knowable only after that bar."""
    if reason not in CANCEL_REASONS:
        raise ContractViolation(f"cancellation reason {reason!r} is not one of {CANCEL_REASONS}")
    t = candidate_entry_open(actionable_at)
    if utc(effective_at) < t:
        return
    if reason == "data_unavailable" and eligible_at is not None and utc(eligible_at) == entry_bar_readable_at(t):
        return
    raise ContractViolation(f"cancellation effective {utc(effective_at).isoformat()} is not before the "
                            f"candidate entry {t.isoformat()}: the entry stands")


def cancellation_vs_entry(cancel_effective, entry_open) -> str:
    """'cancelled' when the cancellation took effect before the entry bar opened, 'entered'
    when it took effect after, 'chronology_ambiguous' when either time is unknown or the two
    coincide (which happened first at that instant is not knowable). Never guessed."""
    if cancel_effective is None or entry_open is None:
        return "chronology_ambiguous"
    ce, eo = utc(cancel_effective), utc(entry_open)
    if ce == eo:
        return "chronology_ambiguous"
    return "cancelled" if ce < eo else "entered"


# ── Invariants (design §5) ────────────────────────────────────────────────────────────────

def check_setup_invariants(*, detected_at, actionable_at, entry_expires_at, times: SessionTimes,
                           inputs: list) -> None:
    """`inputs`: dicts with readable_at and input_read_at. Raises ContractViolation."""
    detected_at = utc(detected_at)
    for i in inputs:
        if not utc(i["readable_at"]) <= utc(i["input_read_at"]) <= detected_at:
            raise ContractViolation("input read before readable, or after detection")
    if actionable_at is not None:
        if not (utc(actionable_at) == detected_at < utc(entry_expires_at) <= times.last_entry_at):
            raise ContractViolation("actionable invariant")
    if utc(entry_expires_at) > times.last_entry_at:
        raise ContractViolation("expiry after the last entry time")


@dataclass
class InputRecord:
    role: str
    timeframe: str
    bar_open: datetime
    source: str                        # native | derived_from_5m
    values: dict
    raw_hash: str | None
    source_hash: str | None
    input_hash: str
    derived_hash: str | None
    readable_at: datetime
    input_read_at: datetime
    read_clock_basis: str = "observed"         # observed | replay_logical
    evidence: dict = field(default_factory=dict)   # EVIDENCE_VERSION payload: recomputes every hash
    first_available_at: datetime | None = None
    availability_basis: str = "unknown_exact_version"
    provider_published_at: datetime | None = None
    extra: dict = field(default_factory=dict)


# ── Input evidence (review IC-03) ─────────────────────────────────────────────────────────

EVIDENCE_VERSION = 1
RAW_FIELDS = ("stock_id", "symbol", "timeframe", "bar_open", "open", "high", "low", "close", "adj_close",
              "volume", "session")
TRANSFORMED_FIELDS = ("timeframe", "bar_open", "open", "high", "low", "close", "volume", "price_basis",
                      "price_basis_reason")


def bar_evidence(raw: dict, provider: str | None, provider_basis: str, transformed: dict) -> dict:
    """The persisted, versioned evidence of one native input: everything its hashes cover, in
    canonical form (timestamps as canonical strings), so they can be recomputed from storage."""
    return {"version": EVIDENCE_VERSION,
            "raw": {k: _norm(raw[k]) for k in RAW_FIELDS},
            "provider": provider or "unknown", "provider_basis": provider_basis,
            "transformed": {k: _norm(transformed[k]) for k in TRANSFORMED_FIELDS}}


def derived_evidence(transformed: dict, constituents: list[dict]) -> dict:
    """A derived bar's evidence: its transformed values plus the ORDERED constituents' full
    native evidence, so the constituent source hashes need no other table."""
    return {"version": EVIDENCE_VERSION, "transformed": {k: _norm(transformed[k]) for k in TRANSFORMED_FIELDS},
            "constituents": list(constituents)}


CONSUMED_FIELDS = ("open", "high", "low", "close", "volume", "price_basis")


def consumed_values(ev: dict) -> dict:
    """The strategy-facing values of an input: a projection of its hashed evidence, the one
    authoritative representation (review ICR-02)."""
    return {k: ev["transformed"][k] for k in CONSUMED_FIELDS}


def check_input_consistency(rec: InputRecord, *, stock_id: int, symbol: str, times: SessionTimes) -> None:
    """Refuse an input whose consumed values, timeframe, bar open, source kind, stock/symbol or
    constituent shape differ from the evidence its hashes are computed from (review ICR-02)."""
    ev = rec.evidence or {}
    hashes_from_evidence(ev)                       # version and structure
    tr = ev["transformed"]
    if rec.values != consumed_values(ev):
        raise ContractViolation("consumed values differ from the evidence they are hashed from")
    if rec.timeframe != tr["timeframe"] or _norm(utc(rec.bar_open)) != tr["bar_open"]:
        raise ContractViolation("input timeframe or bar open differs from its evidence")
    derived = "constituents" in ev
    if rec.source != ("derived_from_5m" if derived else "native"):
        raise ContractViolation(f"source {rec.source!r} does not match {'derived' if derived else 'native'} evidence")
    if rec.timeframe != ("15m" if derived else "5m"):
        raise ContractViolation(f"a {'derived' if derived else 'native'} input cannot be {rec.timeframe}")
    members = ev["constituents"] if derived else [ev]
    for mem in members:
        if "constituents" in mem or "raw" not in mem:
            raise ContractViolation("a constituent must be native evidence")
        raw, mt = mem["raw"], mem["transformed"]
        if raw["stock_id"] != stock_id or raw["symbol"] != symbol.upper():
            raise ContractViolation("input evidence belongs to another stock")
        if not (raw["timeframe"] == mt["timeframe"] == "5m" and raw["bar_open"] == mt["bar_open"]):
            raise ContractViolation("native evidence timeframe or bar open is inconsistent")
    if derived:
        t = utc(rec.bar_open)
        end = min(t + timedelta(minutes=STEP_MIN["15m"]), times.close)
        expected = [_norm(t + timedelta(minutes=5 * k)) for k in range(int((end - t).total_seconds() // 300))]
        mts = [mem["transformed"] for mem in members]
        if [x["bar_open"] for x in mts] != expected:
            raise ContractViolation("derived bar does not have its complete, ordered 5m constituents")
        agg = dict(open=mts[0]["open"], high=max(x["high"] for x in mts), low=min(x["low"] for x in mts),
                   close=mts[-1]["close"], volume=sum(x["volume"] for x in mts))
        if any(tr[k] != v for k, v in agg.items()) or any(
                (x["price_basis"], x["price_basis_reason"]) != (tr["price_basis"], tr["price_basis_reason"])
                for x in mts):
            raise ContractViolation("derived values are not the aggregate of their constituents")


def hashes_from_evidence(ev: dict) -> dict:
    """Recompute every hash a stored input advertises, solely from its evidence payload."""
    if ev.get("version") != EVIDENCE_VERSION:
        raise ContractViolation(f"unknown evidence version {ev.get('version')!r}")
    ih = input_hash(**ev["transformed"])
    if "constituents" in ev:
        members = [hashes_from_evidence(m) for m in ev["constituents"]]
        return {"raw_hash": None, "source_hash": None, "input_hash": ih,
                "derived_hash": derived_hash(ih, [h["source_hash"] for h in members])}
    rh = raw_hash(**ev["raw"])
    return {"raw_hash": rh, "source_hash": source_hash(rh, None if ev["provider"] == "unknown" else ev["provider"]),
            "input_hash": ih, "derived_hash": None}
