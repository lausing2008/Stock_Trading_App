"""SR-01: one conviction-gate decision contract, read identically by every consumer.

THE DEFECT THIS EXISTS FOR. The alert system writes `conv_gate:{symbol}:{style}` when its
7-layer conviction check runs. Two consumers read it and they did not agree:

  * `paper_trading_engine._scan_for_entries` was fixed (AUD-CONVGATE-IDENTITY) to ignore a
    record whose evaluation time predates the signal being considered, and to prefer the
    explicit `gate_passed` field over the delivery-named `sent`.
  * `decision_engine.check_hard_rejects` — the AUTHORITATIVE gate, called by paper trading
    immediately afterwards — still read the raw record: `signal == "BUY" and sent is False`,
    with no identity check at all.

So the local fix bought nothing end to end. A September 30 failed record still vetoed an
October 1 signal through the authoritative path, with the reason "old signal failed".

WHY TIMESTAMPS ALONE ARE NOT THE FIX. The producer records WHEN IT EVALUATED, not WHAT IT
EVALUATED. "The gate ran after this signal was published" does not establish that the gate
ran *on* this signal — a later evaluation of an older signal is still not a decision about a
newer one. Comparing times is a necessary floor, not a sufficient test. The producer
therefore now stamps the identity of the signal it judged (`signal_ts`, and `signal_id`
where the caller has one), and this contract prefers identity whenever it is present,
falling back to the timestamp floor only for records written before that field existed.

THE THREE VERDICTS, and why "no information" is not "allow":

    blocks           the gate judged THIS signal and failed it. A real veto.
    allows           the gate judged THIS signal and passed it.
    no_information   no record, an unreadable record, a record about a different signal, or
                     one that predates the signal. The caller must fall through to its other
                     gates — this is neither permission nor a veto, and collapsing it into
                     either direction is how the original defect worked.

Pure: no Redis, no I/O, no logging. The caller fetches the raw value and hands it here, so
the decision is testable without a server and identical in both services.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .signal_time import parse_signal_instant

BLOCKS = "blocks"
ALLOWS = "allows"
NO_INFORMATION = "no_information"

# Bumped when the MEANING of a stored record changes, so a reader can tell a record it
# understands from one written under different rules.
CONTRACT_VERSION = 1


@dataclass(frozen=True)
class ConvictionVerdict:
    verdict: str
    reason: str = ""
    failed_layers: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_block(self) -> bool:
        return self.verdict == BLOCKS

    @property
    def informative(self) -> bool:
        return self.verdict != NO_INFORMATION


def _no_info(why: str, **detail: Any) -> ConvictionVerdict:
    return ConvictionVerdict(NO_INFORMATION, reason=why, detail=detail)


def evaluate_conviction_record(
    raw: Any,
    *,
    signal_ts: Any = None,
    signal_id: Any = None,
    now: datetime | None = None,
) -> ConvictionVerdict:
    """Decide what a stored conviction record says about THIS signal.

    `raw` is the Redis value (bytes, str, or an already-decoded dict). `signal_ts` and
    `signal_id` identify the signal now being considered.
    """
    if raw is None or raw == "" or raw == b"":
        return _no_info("no conviction record — gate has not run for this symbol/style")

    if isinstance(raw, dict):
        data = raw
    else:
        try:
            text = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            data = json.loads(text)
        except Exception as exc:
            return _no_info(f"conviction record could not be parsed: {exc}")
    if not isinstance(data, dict):
        return _no_info("conviction record is not an object")

    # ── Identity first: did this record judge the signal we are asking about? ──────────
    rec_signal_id = data.get("signal_id")
    if signal_id is not None and rec_signal_id is not None:
        if str(rec_signal_id) != str(signal_id):
            return _no_info(
                "conviction record is about a different signal",
                record_signal_id=str(rec_signal_id), asked_signal_id=str(signal_id))
    else:
        # No identity on one side or the other — fall back to the timestamp floor. This is
        # weaker on purpose and labelled as such: it can only rule a record OUT, never
        # confirm that it judged this exact signal.
        rec_sig_ts = parse_signal_instant(data.get("signal_ts"))
        asked = parse_signal_instant(signal_ts)
        if rec_sig_ts.usable and asked.usable:
            if rec_sig_ts.at != asked.at:
                return _no_info(
                    "conviction record judged a signal with a different timestamp",
                    record_signal_ts=rec_sig_ts.at.isoformat(),
                    asked_signal_ts=asked.at.isoformat())
        else:
            evaluated = parse_signal_instant(data.get("ts"))
            if evaluated.usable and asked.usable and evaluated.at < asked.at:
                return _no_info(
                    "conviction record predates this signal — it evaluated an older one",
                    evaluated_at=evaluated.at.isoformat(),
                    signal_ts=asked.at.isoformat())

    # ── The verdict itself ────────────────────────────────────────────────────────────
    # `gate_passed` is the explicit gate outcome. `sent` is named for DELIVERY but carries
    # the gate result at three of its four producer call sites; it remains the fallback for
    # records written before `gate_passed` existed, and nothing else. A delivery failure must
    # never be readable as a conviction failure.
    gate_passed = data.get("gate_passed")
    if gate_passed is None:
        gate_passed = data.get("sent")
        source = "sent (legacy record)"
    else:
        source = "gate_passed"
    if gate_passed is None:
        return _no_info("conviction record states no gate outcome")

    if data.get("signal") != "BUY":
        return _no_info(f"conviction record is for signal {data.get('signal')!r}, not BUY")

    failed_layers = [str(x) for x in (data.get("failed") or [])]
    if gate_passed is False:
        return ConvictionVerdict(
            BLOCKS,
            reason=f"{', '.join(failed_layers[:2]) or 'multiple layers'}",
            failed_layers=failed_layers,
            detail={"outcome_field": source, "conviction_tier": data.get("conviction_tier")},
        )
    return ConvictionVerdict(
        ALLOWS, reason="conviction gate passed this signal",
        detail={"outcome_field": source, "conviction_tier": data.get("conviction_tier")},
    )
