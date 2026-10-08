"""Ingest corporate actions as SOURCED EVIDENCE, and record what the history claims to cover.

AUD-OBS-ADJWINDOW (2026-10-08). Outcome windows could not establish an adjustment basis because
`adj_close` is present on only 1.61% of stored daily bars. A missing provider column is not the
end of the question: a sufficiently complete, sourced action history establishes the basis just
as well, and better — it says WHAT happened, which a single blended adjustment factor cannot,
because a split and a distribution move that factor the same way.

THREE THINGS THIS MODULE IS CAREFUL ABOUT:

  * RAW PRICES ARE NEVER TOUCHED. `prices.close` stays as fetched. Adjustment is derived at
    read time under a named method, so a methodology change re-derives rather than destroys and
    an earlier outcome stays reproducible from the evidence it cited.
  * A COVERAGE CLAIM IS WRITTEN SEPARATELY FROM THE ACTIONS. Without it, "no rows for this
    window" is ambiguous between "no actions occurred" and "nobody has looked", and those are
    the difference between a verified basis and an unverified one. The claim is written ONLY
    for the span actually requested and actually returned.
  * THE PROVIDER'S OWN ADJUSTED CLOSE IS NOT IMPORTED AS A BASIS. yfinance adjusts for splits
    AND dividends; calling that a split adjustment would fold distributions into a price return.
    Splits and dividends are stored as separate typed records and composed here.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

SOURCE = "yfinance"

#: What this module will claim coverage under. Bumping the method name is how a methodology
#: change becomes visible instead of silently restating earlier figures.
from .evidence_buckets import ADJUSTMENT_METHOD  # noqa: E402


def normalise(splits: dict, dividends: dict, *, symbol: str, retrieved_at: datetime) -> list:
    """Provider series -> typed action records. PURE, so the shaping is testable without a network.

    `splits` and `dividends` map an ISO date to a value, as the provider returns them: a split
    ratio (2.0 for a 2-for-1) and a cash amount per share respectively. They are kept as
    SEPARATE TYPES rather than merged, because only the first changes the share count.
    """
    out = []
    for d, ratio in sorted((splits or {}).items()):
        if not ratio or float(ratio) <= 0:
            continue  # a zero or absent ratio is not a usable record; it is not guessed at
        out.append({"symbol": symbol, "action_type": "split", "ex_date": d,
                    "split_ratio": float(ratio), "cash_amount": None,
                    "source": SOURCE, "retrieved_at": retrieved_at,
                    "source_ref": f"{SOURCE}:{symbol}:splits",
                    "raw": {"ratio": float(ratio)}})
    for d, amount in sorted((dividends or {}).items()):
        if amount is None:
            continue
        out.append({"symbol": symbol, "action_type": "cash_dividend", "ex_date": d,
                    "split_ratio": None, "cash_amount": float(amount),
                    "source": SOURCE, "retrieved_at": retrieved_at,
                    "source_ref": f"{SOURCE}:{symbol}:dividends",
                    "raw": {"amount": float(amount)}})
    return sorted(out, key=lambda a: (a["ex_date"], a["action_type"]))


def fetch(symbol: str) -> tuple[dict, dict, dict]:
    """(splits, dividends, meta) from the provider. Network-bound; isolated for that reason."""
    import yfinance as yf
    t = yf.Ticker(symbol)
    splits = {k.date().isoformat(): float(v) for k, v in (t.splits or {}).items()}
    dividends = {k.date().isoformat(): float(v) for k, v in (t.dividends or {}).items()}
    return splits, dividends, {"provider": SOURCE, "symbol": symbol}


def store(session, symbol: str, actions: list, *, covers_from: date, covers_to: date,
          retrieved_at: datetime, note: str | None = None) -> dict:
    """Upsert the records and write the coverage claim. Idempotent on (symbol, type, date, source).

    The coverage claim is written for the REQUESTED span, not for the span the returned actions
    happen to occupy — a history with no actions in it covers its window just as fully as one
    with ten, and inferring the span from the rows would make an empty result claim nothing.
    """
    from db import CorporateAction, CorporateActionCoverage
    from sqlalchemy import select

    created, kept = 0, 0
    for a in actions:
        existing = session.execute(select(CorporateAction).where(
            CorporateAction.symbol == a["symbol"],
            CorporateAction.action_type == a["action_type"],
            CorporateAction.ex_date == date.fromisoformat(a["ex_date"]),
            CorporateAction.source == a["source"])).scalars().first()
        if existing is not None:
            kept += 1
            continue
        session.add(CorporateAction(
            symbol=a["symbol"], action_type=a["action_type"],
            ex_date=date.fromisoformat(a["ex_date"]), split_ratio=a["split_ratio"],
            cash_amount=a["cash_amount"], currency=a.get("currency"), source=a["source"],
            source_ref=a.get("source_ref"), retrieved_at=a["retrieved_at"], raw=a.get("raw")))
        created += 1

    cov = session.execute(select(CorporateActionCoverage).where(
        CorporateActionCoverage.symbol == symbol,
        CorporateActionCoverage.source == SOURCE,
        CorporateActionCoverage.method == ADJUSTMENT_METHOD)).scalars().first()
    if cov is None:
        cov = CorporateActionCoverage(symbol=symbol, source=SOURCE, method=ADJUSTMENT_METHOD,
                                      covers_from=covers_from, covers_to=covers_to,
                                      retrieved_at=retrieved_at, note=note)
        session.add(cov)
    else:
        # WIDEN ONLY. Narrowing a coverage claim on a re-run would silently un-verify windows
        # that were legitimately verified under the earlier, wider fetch.
        cov.covers_from = min(cov.covers_from, covers_from)
        cov.covers_to = max(cov.covers_to, covers_to)
        cov.retrieved_at = retrieved_at
        cov.note = note or cov.note
    session.commit()
    return {"symbol": symbol, "actions_created": created, "actions_already_held": kept,
            "covers": [cov.covers_from.isoformat(), cov.covers_to.isoformat()],
            "source": SOURCE, "method": ADJUSTMENT_METHOD,
            "retrieved_at": retrieved_at.isoformat()}


def ingest(session, symbol: str, *, covers_from: date, covers_to: date) -> dict:
    """Fetch, shape and store one symbol's action history for a bounded span."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    splits, dividends, meta = fetch(symbol)
    actions = normalise(splits, dividends, symbol=symbol, retrieved_at=now)
    inside = [a for a in actions
              if covers_from.isoformat() <= a["ex_date"] <= covers_to.isoformat()]
    result = store(session, symbol, inside, covers_from=covers_from, covers_to=covers_to,
                   retrieved_at=now,
                   note=f"{len(actions)} action(s) returned by {SOURCE} for all time; "
                        f"{len(inside)} inside the requested span")
    result["provider_total_actions"] = len(actions)
    result["in_span"] = [{k: a[k] for k in ("action_type", "ex_date", "split_ratio",
                                            "cash_amount")} for a in inside]
    return result
