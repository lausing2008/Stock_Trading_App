"""Descriptive call/put measurements from exactly one settled chain snapshot.

No trade direction, paid premium, opening activity or provider OI report date is
inferred from a chain. Incomplete field populations never produce share estimates.
"""
import math
from datetime import datetime, timezone

from .options_scope import volume_composition


def flow_cache_key(symbol):
    """One key contract for the route and post-capture snapshot invalidation."""
    return f"options_flow:v7:{symbol.upper()}"


class DominanceAccumulator:
    def __init__(self, symbol, source_session, requested):
        self.symbol = symbol
        self.session = str(source_session) if source_session else None
        self.requested = list(requested)
        self.slices = []
        self.fields = {metric: {side: {"known": 0, "total": 0, "subtotal": 0}
                               for side in ("call", "put")}
                       for metric in ("volume", "open_interest")}

    def add(self, expiry, calls, puts, *, source_session, coverage_status=None, coverage_reason=None):
        # Refuse cross-session or duplicate slices even if a future caller forgets its guard.
        if str(source_session)[:10] != self.session or expiry not in self.requested:
            raise ValueError("dominance slice outside its declared snapshot")
        if any(s["expiry"] == expiry for s in self.slices):
            raise ValueError("duplicate dominance expiration")
        self.slices.append({"expiry": expiry, "coverage_status": coverage_status or "unverified",
                            "coverage_reason": coverage_reason})
        for metric, column in (("volume", "volume"), ("open_interest", "openInterest")):
            for side, frame in (("call", calls), ("put", puts)):
                stats = self.fields[metric][side]
                for raw in frame[column].tolist():
                    stats["total"] += 1
                    try:
                        value = float(raw)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if isinstance(raw, bool) or not math.isfinite(value) or value < 0 or not value.is_integer():
                        continue
                    stats["known"] += 1
                    stats["subtotal"] += int(value)

    def _row(self, metric):
        sides = {}
        for side, stat in self.fields[metric].items():
            complete = stat["total"] > 0 and stat["known"] == stat["total"]
            sides[side] = {"known": stat["known"], "total": stat["total"],
                           "value": stat["subtotal"] if complete else None,
                           "observed_subtotal": stat["subtotal"] if stat["known"] else None}
        c, p = sides["call"]["value"], sides["put"]["value"]
        measured = c is not None and p is not None
        total = c + p if measured else None
        status = ("zero" if total == 0 else "measured") if measured else (
            "partial" if any(s["known"] for s in sides.values()) else "unavailable")
        label = "unmeasured"
        if total:
            label = volume_composition(c, p)["activity_composition"] if metric == "volume" else (
                "more_calls" if c > p else "more_puts" if p > c else "equal")
        return {"metric": metric, "status": status, "sides": sides,
                "call_share": c / total if total else None,
                "put_share": p / total if total else None, "composition": label,
                "snapshot_session": self.session,
                "reported_as_of": self.session if metric == "volume" else None,
                "note": ("Shares describe the included expirations only; volume does not establish trade direction or opening activity."
                         if metric == "volume" else
                         "OI is from these same chain rows. The provider's OI reporting date is not supplied; snapshot session is not proof of its reporting date.")}

    def summary(self, *, computed_at=None, unavailable_reason=None):
        included = [s["expiry"] for s in self.slices]
        missing = [e for e in self.requested if e not in included]
        archive_complete = bool(self.slices) and all(s["coverage_status"] == "complete" for s in self.slices)
        archive_incomplete = any(s["coverage_status"] in ("partial", "incomplete", "failed") for s in self.slices)
        rows = [self._row(metric) for metric in ("volume", "open_interest")]
        rows += [
            {"metric": "traded_premium", "status": "unavailable",
             "reason": "trade_events_not_captured_for_this_scope",
             "note": "Paid premium needs trade-level events. Chain marks and estimated turnover are not paid premium."},
            {"metric": "aggressor_estimate", "status": "unavailable",
             "reason": "no_comparable_event_window",
             "note": "No comparable trade-event window and classified-share evidence; buying, selling and opening/closing intent are not established."},
        ]
        volume, oi = rows[:2]
        disagreement = ((volume["composition"] == "call_heavy" and oi["composition"] == "more_puts")
                        or (volume["composition"] == "put_heavy" and oi["composition"] == "more_calls"))
        return {"method_version": "options_dominance_v1", "symbol": self.symbol,
                "available": bool(included), "reason": unavailable_reason if not included else None,
                "source": "unusual_whales_settled_chain", "source_session": self.session,
                "computed_at": computed_at or datetime.now(timezone.utc).isoformat(),
                "scope": {"universe": "nearest_four_expirations_of_source_session",
                          "requested": self.requested, "included": included, "missing": missing,
                          "coverage_status": "partial" if missing or archive_incomplete else "complete" if archive_complete else "unverified",
                          "slices": list(self.slices)},
                "rows": rows, "volume_oi_disagree": disagreement,
                "directional_intent": "not_established", "actionability": "research_only"}
