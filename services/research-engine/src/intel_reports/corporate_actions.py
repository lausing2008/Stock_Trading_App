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


def as_dated_map(series) -> dict:
    """A provider series -> {ISO date: float}. PURE, and tested, because `fetch` is not.

    `series` is a pandas Series whose index is timestamps. The obvious `series or {}` raises
    `ValueError: The truth value of a Series is ambiguous` — which is exactly what the first
    pilot run hit, in the one function that had been left untested because it touches the
    network. The network call is now the ONLY thing in `fetch`; every shaping decision lives
    here where a test can reach it.
    """
    if series is None:
        return {}
    items = series.items() if hasattr(series, "items") else series
    out = {}
    for k, v in items:
        if v is None:
            continue
        key = k.date().isoformat() if hasattr(k, "date") else str(k)[:10]
        out[key] = float(v)
    return out


def fetch(symbol: str, *, ticker=None) -> tuple[dict, dict, dict]:
    """(splits, dividends, meta) from the provider. Network-bound; isolated for that reason.

    `ticker` is injectable so the shaping around the call can be exercised without a network.
    """
    if ticker is None:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
    return (as_dated_map(getattr(ticker, "splits", None)),
            as_dated_map(getattr(ticker, "dividends", None)),
            {"provider": SOURCE, "symbol": symbol})


#: What an empty response is allowed to establish. yfinance publishes no completeness guarantee,
#: so a response with no actions in it establishes "the source returned none for this span" and
#: NOT "none occurred". A source that documents exhaustiveness would justify the stronger value.
RESPONSE_ONLY = "response_only"
SOURCE_GUARANTEE = "source_guarantee"
COMPLETENESS_BASIS = {SOURCE_GUARANTEE: "the source guarantees an exhaustive list for this span",
                      RESPONSE_ONLY: "the source returned no further actions for this span; it "
                                     "publishes no completeness guarantee, so this is weaker "
                                     "than a statement that none occurred"}


def evidenced_span(requested_from: date, requested_to: date, retrieved_at: datetime) -> tuple:
    """What a response can actually speak for, which is never the future.

    AUD-OBS-COVERAGEFUTURE. A successful request through 2026-12-31 issued on 2026-10-08
    evidences nothing about November: those corporate actions have not happened, and no
    response can report them. Taking the REQUESTED span as the evidenced one would mark a
    future outcome window verified on the strength of a fetch that could not have seen it.
    """
    cap = retrieved_at.date() if isinstance(retrieved_at, datetime) else retrieved_at
    return requested_from, min(requested_to, cap)


def store(session, symbol: str, actions: list, *, covers_from: date, covers_to: date,
          retrieved_at: datetime, note: str | None = None,
          completeness_basis: str = RESPONSE_ONLY) -> dict:
    """Upsert the records and write the coverage claim. Idempotent on (symbol, type, date, source).

    TWO SPANS, STORED SEPARATELY. The REQUESTED span is what was asked for, kept so a later run
    can tell "never requested" from "requested and nothing came back". The EVIDENCED span is
    what the response can support as of retrieval, capped at the retrieval date. Verification
    reads the evidenced one.

    Within the evidenced span, an empty history covers its window as fully as a full one — but
    only to the strength `completeness_basis` allows, which for a source publishing no
    exhaustiveness guarantee is "none returned", not "none occurred".
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

    ev_from, ev_to = evidenced_span(covers_from, covers_to, retrieved_at)
    cov = session.execute(select(CorporateActionCoverage).where(
        CorporateActionCoverage.symbol == symbol,
        CorporateActionCoverage.source == SOURCE,
        CorporateActionCoverage.method == ADJUSTMENT_METHOD)).scalars().first()
    if cov is None:
        cov = CorporateActionCoverage(
            symbol=symbol, source=SOURCE, method=ADJUSTMENT_METHOD,
            requested_from=covers_from, requested_to=covers_to,
            evidenced_from=ev_from, evidenced_to=ev_to,
            completeness_basis=completeness_basis, retrieved_at=retrieved_at, note=note)
        session.add(cov)
    else:
        # WIDEN ONLY. Narrowing a coverage claim on a re-run would silently un-verify windows
        # that were legitimately verified under an earlier, wider fetch.
        cov.requested_from = min(cov.requested_from, covers_from)
        cov.requested_to = max(cov.requested_to, covers_to)
        cov.evidenced_from = min(cov.evidenced_from, ev_from)
        cov.evidenced_to = max(cov.evidenced_to, ev_to)
        cov.completeness_basis = completeness_basis
        cov.retrieved_at = retrieved_at
        cov.note = note or cov.note
    session.commit()
    return {"symbol": symbol, "actions_created": created, "actions_already_held": kept,
            "requested": [cov.requested_from.isoformat(), cov.requested_to.isoformat()],
            "evidenced": [cov.evidenced_from.isoformat(), cov.evidenced_to.isoformat()],
            "completeness_basis": cov.completeness_basis,
            "completeness_note": COMPLETENESS_BASIS[cov.completeness_basis],
            "source": SOURCE, "method": ADJUSTMENT_METHOD,
            "retrieved_at": retrieved_at.isoformat()}


def ingest(session, symbol: str, *, covers_from: date, covers_to: date,
           ticker=None) -> dict:
    """Fetch, shape and store one symbol's action history for a bounded span."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    splits, dividends, meta = fetch(symbol, ticker=ticker)
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
