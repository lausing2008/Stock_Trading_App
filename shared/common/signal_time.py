"""SR-02: one canonical reading of a signal's timestamp, for every consumer.

THE DEFECT THIS EXISTS FOR. `check_hard_rejects` normalised naive `datetime` OBJECTS to UTC
but not naive ISO *strings*. `datetime.fromisoformat("2026-09-20T15:00:00")` returns a naive
value; subtracting it from an aware `now` raises `TypeError`; the gate's blanket
`except Exception: pass` then skipped the staleness check entirely. The same instant written
as `2026-09-20T15:00:00+00:00` was correctly rejected as 264 hours old. So the signal-age
gate enforced or ignored itself depending on how the producer happened to serialise a
timestamp — and `row.ts.isoformat()` over a naive UTC database column produces exactly the
form that bypassed it.

The display path in `routes.py` already handled naive strings correctly, so the age SHOWN to
a reader could be right while the age ENFORCED was never computed. That is the worst version
of the bug: the evidence looks present.

WHAT THIS MODULE GUARANTEES. One parse, four distinct outcomes, no exceptions escaping:

    absent   nothing was supplied. The caller decides whether that is acceptable; for an
             optional parameter it means "gate not applicable", which is NOT the same as
             "evidence says fresh".
    invalid  something was supplied and could not be read. This is missing evidence wearing
             the costume of present evidence, and it must never read as approval.
    future   the instant is beyond a small skew tolerance ahead of now. A signal from the
             future is a clock or data defect; its "age" would be negative and would pass
             any maximum-age test trivially.
    known    a real instant, normalised to aware UTC.

Naive input is read as UTC, deliberately and visibly (`assumed_utc`): this platform's own
database columns are naive UTC, and the previous code already made that assumption for
`datetime` objects. Making it explicit is the point — an assumption that is recorded can be
checked, and one buried in a branch cannot.

Pure: no I/O, no config, no logging. Importable by any service and by a probe.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# A timestamp this far ahead of now is treated as clock skew rather than as a defect. Beyond
# it, the value is `future` and the caller must not treat it as fresh evidence.
DEFAULT_FUTURE_TOLERANCE_SECONDS = 300

STATES = ("known", "absent", "invalid", "future")


@dataclass(frozen=True)
class SignalInstant:
    """The result of reading one timestamp. `at` is meaningful only when state is `known`."""

    state: str
    at: datetime | None = None
    raw: Any = None
    assumed_utc: bool = False
    detail: str = ""

    @property
    def usable(self) -> bool:
        """True only for a real, readable, non-future instant."""
        return self.state == "known" and self.at is not None

    @property
    def evidence_supplied(self) -> bool:
        """True when the caller passed something — even something unreadable.

        The distinction `absent` vs `invalid` is the whole reason this type exists: one means
        the gate does not apply, the other means the gate applies and its input is broken.
        """
        return self.state != "absent"

    def age_hours(self, now: datetime | None = None) -> float | None:
        if not self.usable:
            return None
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - self.at).total_seconds() / 3600.0


def parse_signal_instant(
    raw: Any,
    *,
    now: datetime | None = None,
    future_tolerance_seconds: int = DEFAULT_FUTURE_TOLERANCE_SECONDS,
) -> SignalInstant:
    """Read any supported timestamp form into a canonical aware-UTC instant.

    Accepts `datetime` (aware or naive) and ISO-8601 strings including a trailing `Z`.
    Never raises: every failure becomes a state the caller can act on.
    """
    if raw is None:
        return SignalInstant("absent", raw=raw, detail="no timestamp supplied")
    if isinstance(raw, str) and not raw.strip():
        return SignalInstant("absent", raw=raw, detail="empty timestamp string")

    assumed_utc = False
    try:
        if isinstance(raw, datetime):
            parsed = raw
        else:
            text = str(raw).strip()
            # `Z` is valid ISO-8601 but `fromisoformat` only accepts it from 3.11; normalising
            # here keeps the behaviour identical across interpreter versions.
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        return SignalInstant("invalid", raw=raw, detail=f"unparseable timestamp: {exc}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
        assumed_utc = True
    else:
        parsed = parsed.astimezone(timezone.utc)

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ahead = (parsed - now).total_seconds()
    if ahead > future_tolerance_seconds:
        return SignalInstant(
            "future", at=parsed, raw=raw, assumed_utc=assumed_utc,
            detail=f"timestamp is {ahead / 60:.1f} minutes ahead of now",
        )

    return SignalInstant("known", at=parsed, raw=raw, assumed_utc=assumed_utc,
                         detail="assumed UTC (naive input)" if assumed_utc else "")
