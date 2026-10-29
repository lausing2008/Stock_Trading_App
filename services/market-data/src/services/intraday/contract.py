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
SESSION_TERMINAL = PRE_ACTIONABLE_TERMINAL + (
    "expired", "cancelled", "rejected_invalid_bracket_at_fill", "exited", "unresolved_exit_at_close",
    "chronology_ambiguous")
FINAL = tuple(s for s in SESSION_TERMINAL if s != "unresolved_exit_at_close") + ("reconciled",)
STATES = ("detected", "actionable", "entered") + SESSION_TERMINAL + ("reconciled",)
LEGAL_PAIRS = frozenset(
    [(None, "detected")]
    + [("detected", s) for s in ("actionable",) + PRE_ACTIONABLE_TERMINAL + ("chronology_ambiguous",)]
    + [("actionable", s) for s in ("entered", "expired", "cancelled", "rejected_invalid_bracket_at_fill",
                                   "chronology_ambiguous")]
    + [("entered", "exited"), ("entered", "unresolved_exit_at_close"),
       ("unresolved_exit_at_close", "reconciled")])
CANCEL_REASONS = ("invalidated_price", "input_revised", "data_unavailable", "session_end")
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


def cancellation_vs_entry(cancel_effective, entry_open) -> str:
    """'cancelled' when the cancellation took effect before the entry bar opened, 'entered'
    when it did not, 'chronology_ambiguous' when either time is unknown. Never guessed."""
    if cancel_effective is None or entry_open is None:
        return "chronology_ambiguous"
    return "cancelled" if utc(cancel_effective) < utc(entry_open) else "entered"


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
    first_available_at: datetime | None = None
    availability_basis: str = "unknown_exact_version"
    provider_published_at: datetime | None = None
    extra: dict = field(default_factory=dict)
