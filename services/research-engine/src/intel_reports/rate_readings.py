"""Turning stored cross-asset rows into rate readings. NO ORM IMPORT, so it is testable.

EVERY SERIES IS READ SEPARATELY. The daily row is not uniformly populated — in production on
2026-10-05 the 2026-10-02 row carried a term spread and nothing else, while the yields were
last observed on 2026-10-01. Taking one row's date as "the" observation date would stamp a
stale yield with a fresh timestamp, and comparing against a NULL would read as no change.

THESE ARE MARKET PRICES, NOT POLICY. A constant-maturity yield is what the market paid, and it
moves on policy expectations, term premium and flows together. Nothing here reports a policy
decision.
"""
from __future__ import annotations

#: FRED series behind each rate reading. The NAME is part of the evidence: "rates rose" is not
#: checkable, "DGS10 rose 5bp between two named observation dates" is.
_RATE_SERIES = {
    "yield_2y": ("DGS2", "2-year", "US Treasury constant-maturity yield", "pct"),
    "yield_10y": ("DGS10", "10-year", "US Treasury constant-maturity yield", "pct"),
    "yield_curve_2s10s": ("T10Y2Y", "10y minus 2y", "US Treasury term spread", "pct"),
    "hy_spread": ("BAMLH0A0HYM2", "n/a", "ICE BofA US high-yield option-adjusted spread",
                  "pct"),
    "dxy": ("DTWEXBGS", "n/a", "broad trade-weighted US dollar index", "index"),
}


def build_readings(rows, *, lookback_days: int) -> dict:
    """`rows` are newest-first; each series takes its own two most recent NON-NULL values."""
    out = {}
    for col, (series, tenor, desc, units) in _RATE_SERIES.items():
        obs = [(r.as_of, getattr(r, col), r.fetched_at) for r in rows
               if getattr(r, col) is not None]
        if not obs:
            out[col] = {"series": series, "status": "NOT OBSERVED",
                        "note": f"{series} has no non-null reading in the window; an absent "
                                f"value is not an unchanged one"}
            continue
        (d0, v0, f0) = obs[0]
        entry = {"series": series, "instrument": desc, "tenor": tenor, "units": units,
                 "latest": v0, "observation_date": d0.isoformat(),
                 "retrieved_at": f0.isoformat() if f0 else None}
        if len(obs) > 1:
            (d1, v1, _) = obs[1]
            entry["previous"] = v1
            entry["previous_observation_date"] = d1.isoformat()
            entry["change"] = round(v0 - v1, 4)
            entry["change_bp"] = round((v0 - v1) * 100, 1) if units == "pct" else None
        else:
            entry["change"] = None
            entry["note"] = "only one observation in the window, so no change is measured"
        out[col] = entry
    return {
        "readings": out,
        "source": "FRED",
        "window_days": lookback_days,
        "basis": ("each series is read independently and compared with its OWN previous "
                  "non-null observation; the daily row is not uniformly populated, so one "
                  "row's date does not describe every series"),
        "scope": ("MARKET-PRICED yields and spreads only. These move on policy expectations, "
                  "term premium and flows together, and a change here is NOT a policy "
                  "decision — no policy action is reported by this field"),
    }
