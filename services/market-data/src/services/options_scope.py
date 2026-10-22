"""Options review Phase B: volume composition and historical-versus-current expiry scope.

Pure functions, shared by every options route, so a route and its tests cannot drift apart.

COMPOSITION (review F2/F3). The label used to come from `cp_ratio = round(min(c / max(p, 1), 10), 2)`,
so 1.296 rounded to 1.30 and read as call-heavy, and a put side of zero became a capped "10:1".
The cut points are unchanged (agreed 2026-10-22): call-heavy when calls/puts >= 1.3, put-heavy
when <= 0.8, mixed otherwise — evaluated on the EXACT volumes. A label describes volume, never
direction. The legacy `cp_ratio`/`sentiment` fields stay as they were for signal-engine and the
stored ML feature; nothing here changes them.

EXPIRY SCOPE (review F4). The archive is a SETTLED session's chain, read later. An expiry that
belonged to that session is valid historical evidence of that session, including a contract that
expired at that very close. It is not a current trade candidate once its last trading instant
has passed. Two lists, never one:

  historical_expiries  listed in the source session, on or after its date
  current_expiries     historical AND still before their last trading instant now

The last trading instant is the expiry date's regular-session close from the NYSE calendar
(13:00 ET on an early-close day). An expiry dated on a non-session day is treated as the previous
session's close and the basis says so. Index products that settle later are not distinguished:
16:00 is the conservative bound for an equity option, and it can only exclude a contract early,
never admit an expired one.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from common.market_calendar import session_close

CP_CALL_HEAVY = 1.3
CP_PUT_HEAVY = 0.8
LOW_SIDE_VOLUME = 100

COMPOSITION_RULE = ("call_heavy when calls/puts >= 1.3, put_heavy when <= 0.8, mixed otherwise, "
                    "from exact contract volumes (never a rounded ratio); describes volume, not direction")


def volume_composition(call_volume, put_volume) -> dict:
    """Label, shares and sample note from exact volumes. Mirrors frontend `composition()`."""
    c = None if call_volume is None else int(call_volume)
    p = None if put_volume is None else int(put_volume)
    base = {"composition_rule": COMPOSITION_RULE}
    if c is None or p is None or c < 0 or p < 0 or c + p == 0:
        return {**base, "activity_composition": "unmeasured", "call_share": None, "put_share": None,
                "cp_ratio_exact": None, "cp_ratio_basis": "unmeasured", "sample_adequacy": "unmeasured"}
    total = c + p
    if p == 0:
        label, ratio, basis = "call_heavy", None, "undefined_no_puts"
    elif c == 0:
        label, ratio, basis = "put_heavy", 0.0, "zero_calls"
    else:
        ratio, basis = c / p, "exact"
        label = "call_heavy" if ratio >= CP_CALL_HEAVY else "put_heavy" if ratio <= CP_PUT_HEAVY else "mixed"
    return {**base, "activity_composition": label, "call_share": c / total, "put_share": p / total,
            "cp_ratio_exact": ratio, "cp_ratio_basis": basis,
            "sample_adequacy": "few_contracts_on_one_side" if min(c, p) < LOW_SIDE_VOLUME else "adequate"}


def last_trading_instant(expiry: date) -> tuple[datetime, str]:
    """When a contract with this expiry date stops trading, and how that was decided."""
    close = session_close("US", expiry)
    if close is not None:
        return close, "expiry_session_close"
    d = expiry
    for _ in range(10):
        d -= timedelta(days=1)
        close = session_close("US", d)
        if close is not None:
            return close, "previous_session_close (expiry date is not a session)"
    raise ValueError(f"no NYSE session within 10 days before {expiry}")


def expiry_scope(expiries, source_session: date | None, now: datetime | None = None) -> dict:
    """Split one source session's expiries into historical evidence and current candidates."""
    now = now or datetime.now(timezone.utc)
    rows, historical, current = [], [], []
    for e in sorted({str(x) for x in (expiries or [])}):
        try:
            d = date.fromisoformat(e[:10])
        except ValueError:
            rows.append({"expiry": e, "historical": False, "current": False, "reason": "unparseable_expiry",
                         "last_trading_instant": None})
            continue
        lti, basis = last_trading_instant(d)
        hist = source_session is not None and d >= source_session
        cur = hist and now < lti
        reason = (None if cur else
                  "source_session_unknown" if source_session is None else
                  "before_source_session" if not hist else
                  "expired_at_source_session_close" if d == source_session else
                  "last_trading_instant_passed")
        rows.append({"expiry": e, "historical": hist, "current": cur, "reason": reason,
                     "last_trading_instant": lti.isoformat(), "last_trading_basis": basis})
        if hist:
            historical.append(e)
        if cur:
            current.append(e)
    return {
        "source_session": source_session.isoformat() if source_session else None,
        "evaluated_at": now.astimezone(timezone.utc).isoformat(),
        "historical_expiries": historical,
        "current_expiries": current,
        "excluded_from_current": [{"expiry": r["expiry"], "reason": r["reason"],
                                   "last_trading_instant": r["last_trading_instant"]}
                                  for r in rows if not r["current"]],
        "expiries": rows,
        "rule": ("historical = listed in the source session on or after its date (valid evidence of "
                 "that session); current = historical and before its last trading instant "
                 "(regular-session close of the expiry date, NYSE calendar) at evaluated_at"),
    }


def current_expiries(expiries, source_session: date | None, now: datetime | None = None) -> list[str]:
    """Only the expiries a current trade candidate may use."""
    return expiry_scope(expiries, source_session, now)["current_expiries"]


# ── Stored snapshots in the premarket brief (2026-10-22) ───────────────────────────────────
#
# OptionsFlowSnapshot.sentiment has held three vocabularies: the legacy bullish/bearish ladder
# (to 2026-10-08), call_heavy/put_heavy/mixed from a ROUNDED ratio (2026-10-09 to the Phase B
# deploy), and the exact rule (rows whose evidence carries `composition_rule`). The brief must
# not repeat the legacy direction words: a volume mix does not establish trade direction or
# intent. Rows without the exact rule are relabelled from their stored volumes and say so.

_COMPOSITION_WORDS = {"call_heavy": "call-heavy", "put_heavy": "put-heavy", "mixed": "mixed",
                      "unmeasured": "not measured", "unclassified": "unclassified"}


def describe_flow_snapshot(row: dict) -> dict:
    """Composition, classification version, source session, completeness and direction for one
    stored snapshot row ({sentiment, call_volume, put_volume, evidence, as_of})."""
    ev = row.get("evidence") or {}
    stored = row.get("sentiment")
    calls, puts = row.get("call_volume"), row.get("put_volume")
    if ev.get("composition_rule") and stored in ("call_heavy", "put_heavy", "mixed", "unmeasured"):
        label, version = stored, "exact_v1"
        comp = volume_composition(calls, puts) if calls is not None and puts is not None else {}
        sample = ev.get("sample_adequacy") or comp.get("sample_adequacy")
        call_share = ev.get("call_share", comp.get("call_share"))
    elif calls is not None and puts is not None:
        comp = volume_composition(calls, puts)
        label, version = comp["activity_composition"], "relabelled_from_stored_volumes"
        sample, call_share = comp["sample_adequacy"], comp["call_share"]
    else:
        label, version, sample, call_share = "unclassified", "legacy_without_volumes", "unmeasured", None
    session = ev.get("chain_as_of")
    as_of = row.get("as_of")
    session_text = (f"session {str(session)[:10]}" if session else
                    f"snapshot {str(as_of)[:10]}, source session not recorded" if as_of else "source session unknown")
    coverage = ev.get("archive_coverage_status") or ev.get("coverage_status")
    coverage_text = f"coverage {coverage}" if coverage else "coverage not recorded"
    # Direction only from INDEPENDENT directional evidence carried with the row; none exists in
    # the snapshot today, so this always reads "not established".
    direction = ev.get("directional_intent")
    direction_text = (f"direction {direction} ({ev['directional_basis']})"
                      if direction in ("bullish", "bearish") and ev.get("directional_basis") else
                      "direction not established")
    shares = (f" ({call_share * 100:.0f}% calls / {(1 - call_share) * 100:.0f}% puts)"
              if isinstance(call_share, (int, float)) and label in ("call_heavy", "put_heavy", "mixed") else "")
    relabel = " · relabelled from stored volumes with the exact rule" if version == "relabelled_from_stored_volumes" else ""
    few = " · few contracts on one side" if sample == "few_contracts_on_one_side" else ""
    return {
        "label": label, "version": version, "sample_adequacy": sample,
        "notable": label in ("call_heavy", "put_heavy") and sample == "adequate",
        "text": (f"Options volume {_COMPOSITION_WORDS[label]}{shares} · {session_text} · {coverage_text}"
                 f"{few}{relabel} · {direction_text}"),
    }
