"""Congress Trading — House and Senate STOCK Act disclosures."""
from __future__ import annotations

import asyncio
import math
import re
from datetime import date, timedelta

import httpx
import structlog
from sqlalchemy import func as _func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import get_session, SessionLocal, CongressTrade, Stock
from common.uw_congress import (
    canonicalize_politician_name as _canon_name,
    get_congress_roster as _uw_get_roster,
    get_congress_trades as _uw_get_congress_trades,
    is_available as _uw_available,
)

log = structlog.get_logger()

# EI-CONGRESS1: house-stock-watcher / senate-stock-watcher (both the old S3 dumps below AND
# their replacement REST APIs at housestockwatcher.com/senatestockwatcher.com) are permanently
# dead — the maintainer has been inactive since March 2021 and never responded to a 2024
# shutdown inquiry; both domains fail to resolve as of 2026-07-09. This left congress_trades
# with 0 rows in production (confirmed) and market-data's /congress/trades endpoint silently
# returning an empty list to every real user with no Quiver key configured as a fallback.
# Replaced with kadoa-org/congress-trading-monitor's live, unauthenticated GitHub JSON feed —
# MIT-licensed, updates via daily automated commits, covers House Clerk + Senate eFD + OGE
# (executive branch) filings in one combined response. Rolling ~5000-row window (not full
# history), which is fine for keeping the feed current going forward.
_HOUSE_URL = "https://house-stock-watcher-data.s3-us-east-2.amazonaws.com/data/all_transactions.json"
_SENATE_URL = "https://senate-stock-watcher-data.s3-us-east-2.amazonaws.com/data/all_transactions.json"
_KADOA_URL = "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/trades.json"

_AMOUNT_RANGES = {
    "$1,001 - $15,000": (1001, 15000),
    "$15,001 - $50,000": (15001, 50000),
    "$50,001 - $100,000": (50001, 100000),
    "$100,001 - $250,000": (100001, 250000),
    "$250,001 - $500,000": (250001, 500000),
    "$500,001 - $1,000,000": (500001, 1000000),
    "$1,000,001 - $5,000,000": (1000001, 5000000),
    "$5,000,001 - $25,000,000": (5000001, 25000000),
}


def _parse_amount(amount_str: str | None) -> tuple[float | None, float | None]:
    if not amount_str:
        return None, None
    for key, (lo, hi) in _AMOUNT_RANGES.items():
        if key in amount_str:
            return float(lo), float(hi)
    # Try to parse a dollar value directly
    nums = re.findall(r"[\d,]+", amount_str.replace("$", ""))
    if nums:
        try:
            val = float(nums[0].replace(",", ""))
            return val, val
        except ValueError:
            pass
    return None, None


def _normalize_txn_type(raw: str | None) -> str:
    if not raw:
        return "unknown"
    raw = raw.lower()
    if "purchase" in raw or "buy" in raw:
        return "purchase"
    if "sale" in raw or "sell" in raw:
        return "sale"
    if "exchange" in raw:
        return "exchange"
    return raw[:32]


def _ticker_to_stock_id(ticker: str, ticker_map: dict[str, int]) -> int | None:
    if not ticker or ticker in ("N/A", "--", "NONE"):
        return None
    return ticker_map.get(ticker.upper())


def _uw_rows_to_kadoa_shape(rows: list, roster: dict | None = None) -> list[dict]:
    """Translate shared/common/uw_congress.py's CongressTradeRow list into the same dict shape
    the kadoa feed's own raw JSON rows already have (branch/filer_name/transaction_date/etc),
    so the single upsert loop below stays source-agnostic rather than needing two parallel
    branches for "what a row looks like."

    AUD-UWCONGRESS-FIELDNAMES: `roster` is UW's /api/congress/politicians keyed by lower-cased
    name. UW's TRADE rows carry no party at all — that field lives only on the roster — so
    without this join every UW-sourced row stores a NULL party, which is exactly what all 7,691
    of them did. The join is by name because CongressTrade has no politician_id column yet;
    a miss leaves party NULL rather than guessing, so an unmatched or renamed member degrades to
    today's behaviour instead of being assigned someone else's party."""
    roster = roster or {}
    out = []
    for r in rows:
        ident = roster.get((r.politician_name or "").strip().lower()) or {}
        chamber = r.chamber or ident.get("chamber")
        out.append({
            "branch": "congress",  # UW's feed is congress-only already, unlike kadoa's mixed feed
            # No roster (outage, or UW unconfigured) means no authority to canonicalise
            # against, so the name passes through exactly as the old code left it.
            "filer_name": _canon_name(r.politician_name, chamber, roster) if roster else r.politician_name,
            "party": r.party or ident.get("party"),
            "chamber": chamber,
            "ticker": r.ticker,
            "transaction_type": r.transaction_type,
            "amount_range_label": r.amount_range_label,
            "amount_range_low": r.amount_min,
            "amount_range_high": r.amount_max,
            "transaction_date": r.trade_date,
            "filing_date": r.disclosure_date,
        })
    return out


async def sync_congress_trades(lookback_days: int = 365) -> dict:
    """Download congress trading disclosures and upsert recent trades to DB.

    T323-DARKPOOL: tries Unusual Whales' real `/api/congress/recent-trades` first (a live,
    dedicated Congressional feed — see shared/common/uw_congress.py) when a subscription is
    configured and enabled, one day at a time back to `lookback_days` (UW's own endpoint is
    date-filtered, not a single "give me everything" call the way kadoa's static JSON dump is).
    Falls back to EI-CONGRESS1's own kadoa-org/congress-trading-monitor feed (see module
    docstring comment above _KADOA_URL) whenever UW is unconfigured/disabled/erroring — the
    exact same graceful-degradation contract every other Unusual Whales integration in this app
    already uses (gamma exposure, short interest, options-flow alerts all fall back to a free
    proxy the same way; this is the same pattern applied to a genuinely different data type).
    Rows from either source flow through the SAME upsert loop below via
    _uw_rows_to_kadoa_shape() — one write path, not two independently-drifting ones.

    Non-congress (branch != "congress") records — mostly OGE executive-branch filings from the
    kadoa feed specifically, ~85% of its rolling 5000-row window — are filtered out here since
    this function is specifically congress trades.
    """
    cutoff = date.today() - timedelta(days=lookback_days)

    # Build ticker → stock_id lookup
    with SessionLocal() as s:
        ticker_map: dict[str, int] = {sym: sid for sid, sym in s.execute(select(Stock.id, Stock.symbol)).all()}

    trades: list[dict] = []
    source_label = "kadoa"
    # Fetched once per sync (Redis-cached 24h). Used for BOTH the UW party join and the
    # cross-feed name canonicalisation below; empty on any roster outage, which degrades to
    # today's behaviour rather than dropping rows.
    _roster = _uw_get_roster() if _uw_available() else {}
    if _uw_available():
        try:
            uw_rows = []
            day = date.today()
            while day >= cutoff:
                day_rows = _uw_get_congress_trades(since=day.isoformat())
                uw_rows.extend(day_rows)
                day -= timedelta(days=1)
            if uw_rows:
                trades = _uw_rows_to_kadoa_shape(uw_rows, _roster)
                source_label = "unusual_whales"
        except Exception as exc:
            log.warning("congress.uw_fetch_error", error=str(exc))
            trades = []

    if not trades:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            try:
                r = await client.get(_KADOA_URL)
                if r.status_code != 200:
                    log.warning("congress.fetch_fail", status=r.status_code, url=_KADOA_URL)
                    return {"rows_upserted": 0}
                trades = r.json()
                if isinstance(trades, dict):
                    trades = trades.get("data") or trades.get("trades") or []
            except Exception as exc:
                log.warning("congress.fetch_error", error=str(exc), url=_KADOA_URL)
                return {"rows_upserted": 0}

    total = 0
    with SessionLocal() as s:
        for t in trades:
            try:
                if t.get("branch") != "congress":
                    continue

                trade_date_str = t.get("transaction_date") or ""
                if not trade_date_str:
                    continue
                trade_date = date.fromisoformat(trade_date_str[:10])
                if trade_date < cutoff:
                    continue

                ticker = (t.get("ticker") or "").upper()[:16]
                if not ticker or len(ticker) > 8:
                    continue

                chamber = (t.get("chamber") or "").capitalize() or "Unknown"
                # AUD-UWCONGRESS-NAMEMERGE: both feeds write to one table and spell the same
                # member differently ("Ro Khanna" / "Rohit Khanna" / "Hon. David J. Taylor").
                # Canonicalising HERE rather than per-feed means one rule governs every row,
                # and the conflict key (politician_name, ...) stops splitting one person's
                # history across two ranked rows with contradictory returns.
                politician = (t.get("filer_name") or "Unknown")[:255]
                if _roster:
                    politician = _canon_name(politician, chamber, _roster)[:255]
                party = (t.get("party") or "")[:32]
                state = (t.get("state") or "")[:8]
                txn_type = _normalize_txn_type(t.get("transaction_type"))
                amount_str = t.get("amount_range_label") or ""
                amount_min = t.get("amount_range_low")
                amount_max = t.get("amount_range_high")
                disc_date_str = t.get("filing_date") or ""
                disc_date = date.fromisoformat(disc_date_str[:10]) if disc_date_str else None
                stock_id = _ticker_to_stock_id(ticker, ticker_map)

                # T323-DARKPOOL: row_source distinguishes a real UW-fed row from a kadoa-fed
                # one — kept per-CHAMBER for kadoa (as before, "kadoa_house"/"kadoa_senate")
                # since that feed's own per-row chamber field is meaningful provenance, but a
                # single flat "unusual_whales" for UW rows since that feed already scopes
                # itself to congress-only and doesn't need the same chamber-in-source
                # disambiguation kadoa's mixed House+Senate+executive-branch feed benefits from.
                row_source = source_label if source_label == "unusual_whales" else "kadoa_" + chamber.lower()

                insert_stmt = pg_insert(CongressTrade).values(
                    politician_name=politician,
                    party=party,
                    chamber=chamber,
                    state=state,
                    ticker=ticker,
                    stock_id=stock_id,
                    transaction_type=txn_type,
                    amount_range=amount_str[:64] if amount_str else None,
                    amount_min=amount_min,
                    amount_max=amount_max,
                    trade_date=trade_date,
                    disclosure_date=disc_date,
                    source=row_source,
                )
                # T247-EVENTINTELLIGENCE-CONGRESSAMENDMENT: on_conflict_do_nothing silently
                # dropped amendments — a politician correcting a previously-filed
                # disclosure's amount range or disclosure date (same politician/ticker/
                # trade_date/transaction_type, the uq_congress_trade key) never updated the
                # stale original row. Use do_update for the fields a real amendment can
                # correct; leave the identity columns (politician_name, ticker, trade_date,
                # transaction_type) alone since those ARE the conflict key.
                # AUD-EI-CONGRESS-STOCKID-NULL: don't let a failed re-resolution (stock_id
                # None this run) overwrite a previously-resolved stock_id — coalesce to the
                # existing row's value so a transient ticker-lookup miss can't regress a
                # trade that was correctly linked on an earlier sync.
                stmt = insert_stmt.on_conflict_do_update(
                    constraint="uq_congress_trade",
                    set_={
                        "party": insert_stmt.excluded.party,
                        "chamber": insert_stmt.excluded.chamber,
                        "state": insert_stmt.excluded.state,
                        "stock_id": _func.coalesce(insert_stmt.excluded.stock_id, CongressTrade.stock_id),
                        "amount_range": insert_stmt.excluded.amount_range,
                        "amount_min": insert_stmt.excluded.amount_min,
                        "amount_max": insert_stmt.excluded.amount_max,
                        "disclosure_date": insert_stmt.excluded.disclosure_date,
                        "source": insert_stmt.excluded.source,
                    },
                )
                result = s.execute(stmt)
                total += result.rowcount
            except Exception:
                continue
        s.commit()

    return {"rows_upserted": total}


def get_congress_for_symbol(stock_id: int, days: int = 90) -> list[dict]:
    since = date.today() - timedelta(days=days)
    with SessionLocal() as s:
        rows = s.execute(
            select(CongressTrade)
            .where(CongressTrade.stock_id == stock_id, CongressTrade.trade_date >= since)
            .order_by(CongressTrade.trade_date.desc())
        ).scalars().all()
        return [_trade_to_dict(t) for t in rows]


def _build_congress_leaderboard(rows: list[dict], limit: int) -> list[dict]:
    """Pure aggregation: given already-fetched per-trade dicts (stock_id/symbol/company/
    transaction_type/amount_min/amount_max/politician_name), return the top `limit` stocks by
    net congress buying.

    AUD-INSIDERTOPBUYS-NETNEGATIVE: same bug class as insider.py's leaderboard (see that
    function's docstring) — this is named/consumed everywhere as a "Top Buys" leaderboard
    (route name /events/congress/leaderboard, reports.tsx's "Congress Top Buys" card) but
    previously had no floor at zero, so a stock with heavy net SELLING by politicians could
    still appear under a "Top Buys" heading. Filtering to net_amount > 0 before truncating
    means every returned row is a real net buyer.
    """
    result: dict[int, dict] = {}
    for row in rows:
        sid = row["stock_id"]
        if sid not in result:
            result[sid] = {
                "stock_id": sid, "symbol": row["symbol"], "company": row["company"],
                "purchases": 0, "sales": 0, "net_amount": 0.0,
                "politicians": set(),
            }
        mid = ((row["amount_min"] or 0) + (row["amount_max"] or 0)) / 2
        if row["transaction_type"] == "purchase":
            result[sid]["purchases"] += 1
            result[sid]["net_amount"] += mid
        elif row["transaction_type"] == "sale":
            result[sid]["sales"] += 1
            result[sid]["net_amount"] -= mid
        result[sid]["politicians"].add(row["politician_name"])

    for v in result.values():
        v["unique_politicians"] = len(v["politicians"])
        del v["politicians"]

    net_buyers = [v for v in result.values() if v["net_amount"] > 0]
    sorted_result = sorted(net_buyers, key=lambda x: x["net_amount"], reverse=True)
    return sorted_result[:limit]


def get_congress_leaderboard(days: int = 90, limit: int = 20) -> list[dict]:
    """Stocks with most net congress buying in last N days — every returned row is a genuine
    net buyer (net_amount > 0); see _build_congress_leaderboard()'s own docstring."""
    since = date.today() - timedelta(days=days)
    with SessionLocal() as s:
        all_rows = s.execute(
            select(CongressTrade, Stock.symbol, Stock.name)
            .join(Stock, CongressTrade.stock_id == Stock.id)
            .where(
                CongressTrade.stock_id.isnot(None),
                CongressTrade.trade_date >= since,
            )
            .order_by(CongressTrade.trade_date.desc())
        ).all()
        rows = [
            {
                "stock_id": trade.stock_id, "symbol": symbol, "company": name,
                "transaction_type": trade.transaction_type,
                "amount_min": trade.amount_min, "amount_max": trade.amount_max,
                "politician_name": trade.politician_name,
            }
            for trade, symbol, name in all_rows
        ]
        return _build_congress_leaderboard(rows, limit)


def get_recent_congress_trades(
    days: int = 30, limit: int = 50, ticker: str | None = None, politician: str | None = None,
) -> list[dict]:
    """T233-ARCH-CONGRESS-DEDUP: ticker/politician filters added so this endpoint can serve
    congress.tsx/insider.tsx directly, replacing market-data's now-deleted /congress/trades
    (which supported the same two filters server-side, though neither frontend page actually
    wired them into its API call — both filtered client-side instead)."""
    since = date.today() - timedelta(days=days)
    with SessionLocal() as s:
        stmt = select(CongressTrade).where(CongressTrade.trade_date >= since)
        if ticker:
            stmt = stmt.where(CongressTrade.ticker == ticker.upper())
        if politician:
            stmt = stmt.where(CongressTrade.politician_name.ilike(f"%{politician}%"))
        rows = s.execute(
            stmt.order_by(CongressTrade.trade_date.desc()).limit(limit)
        ).scalars().all()
        return [_trade_to_dict(t) for t in rows]


_CONGRESS_SCORE_HALF_LIFE_DAYS = 30.0  # a trade's weight halves every 30 days — no hard cliff
_CONGRESS_SCORE_DOLLAR_REF = 15000.0   # log-scale reference — the median real disclosure band
                                        # ($1,001-$15,000) maps to weight ~1.0; a $250K+ trade
                                        # (a real but much rarer band) maps to ~2-3x, not
                                        # thousands of x, since disclosure bands span
                                        # $1,001-$25M+ and a raw linear dollar weight would let
                                        # one filing swamp the whole score.


def _congress_score_from_trades(trades: list[dict], today: date | None = None) -> float:
    """-100 to 100 congress activity score (negative = net selling pressure), pure function
    of already-fetched trade dicts — split out from compute_congress_score() so the scoring
    math is directly testable without a DB round-trip.

    AUD264-CATALYST-NO-TIME-DECAY: the original scoring had 3 real gaps, all fixed here
    together (fixing recency decay alone would still leave trade-count-only weighting able to
    saturate the score identically for one filer's split position as for many independent
    filers'):
    1. NO recency decay — an 89-day-old purchase scored identically to yesterday's, inside a
       flat window that then fell off a cliff to 0 on day 91 (the window itself, still passed
       in via get_congress_for_symbol(stock_id, days), is now just the outer bound past which
       a trade's decayed weight would be negligible anyway — the decay itself is the real
       fix, not a wider or narrower window).
    2. Scored by raw TRADE COUNT, not dollar amount — a $1,001 purchase and a $25M purchase
       both counted as a flat +12, and the "clustered buying" bonus counted trades, not
       distinct filers, so one politician splitting a single position across 9 same-day
       filings saturated the score identically to 9 independent politicians actually agreeing.
    3. amount_min/amount_max were parsed and stored but never read in scoring (only in the
       leaderboard) — real information about position size was being computed and discarded.

    Each trade's contribution = direction_sign * dollar_weight * recency_weight, where
    dollar_weight = max(0.5, log10(amount_mid / _CONGRESS_SCORE_DOLLAR_REF) + 1) — floored at
    0.5 (not 0) so a trade with no amount data at all, or a genuinely tiny disclosed amount,
    still counts as real activity rather than vanishing entirely; log-scaled so the $1K-$25M+
    real range (confirmed in production) doesn't let one large filing swamp the score. The
    clustering bonus now counts DISTINCT POLITICIANS who purchased, not raw purchase-trade
    count, directly closing the one-filer-many-filings saturation gap.
    """
    if not trades:
        return 0.0
    today = today or date.today()

    score = 0.0
    buying_politicians: set[str] = set()
    for t in trades:
        trade_date_str = t.get("trade_date")
        if not trade_date_str:
            continue
        try:
            trade_date = date.fromisoformat(trade_date_str)
        except ValueError:
            continue
        age_days = max(0, (today - trade_date).days)
        recency_weight = 0.5 ** (age_days / _CONGRESS_SCORE_HALF_LIFE_DAYS)

        amount_min, amount_max = t.get("amount_min"), t.get("amount_max")
        amount_mid = ((amount_min or 0) + (amount_max or 0)) / 2
        if amount_mid > 0:
            dollar_weight = max(0.5, math.log10(amount_mid / _CONGRESS_SCORE_DOLLAR_REF) + 1)
        else:
            dollar_weight = 0.5  # no disclosed amount — still real activity, floor weight only

        if t["transaction_type"] == "purchase":
            score += 12 * dollar_weight * recency_weight
            politician = t.get("politician_name")
            if politician:
                buying_politicians.add(politician)
        elif t["transaction_type"] == "sale":
            score -= 5 * dollar_weight * recency_weight

    # Bonus for clustered buying — DISTINCT politicians, not raw purchase-trade count, so one
    # filer splitting a position across many filings can't saturate this the way many
    # independent filers agreeing legitimately should.
    if len(buying_politicians) > 5:
        score += 20
    elif len(buying_politicians) > 2:
        score += 10

    return min(100.0, max(-100.0, score))


def compute_congress_score(stock_id: int, days: int = 90) -> float:
    """-100 to 100 congress activity score (negative = net selling pressure).

    EI-DOC1: docstring previously said "0-100", contradicting the actual
    min(100.0, max(-100.0, score)) clamp below — sales subtract from score,
    so a sell-heavy trade history legitimately produces a negative value.
    catalyst.py already correctly documents and relies on this real range
    (see its T237-EI1 comment); this docstring was simply out of date.

    See _congress_score_from_trades() for the actual scoring math (recency decay,
    dollar-weighting, distinct-filer clustering bonus — AUD264-CATALYST-NO-TIME-DECAY).
    """
    trades = get_congress_for_symbol(stock_id, days)
    return _congress_score_from_trades(trades)


def days_since_last_congress_buy(stock_id: int) -> int | None:
    today = date.today()
    with SessionLocal() as s:
        row = s.execute(
            select(CongressTrade.trade_date)
            .where(
                CongressTrade.stock_id == stock_id,
                CongressTrade.transaction_type == "purchase",
            )
            .order_by(CongressTrade.trade_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return (today - row).days


def _trade_to_dict(t: CongressTrade) -> dict:
    return {
        "id": t.id,
        "politician_name": t.politician_name,
        "party": t.party,
        "chamber": t.chamber,
        "state": t.state,
        "ticker": t.ticker,
        "transaction_type": t.transaction_type,
        "amount_range": t.amount_range,
        "amount_min": t.amount_min,
        "amount_max": t.amount_max,
        "trade_date": t.trade_date.isoformat() if t.trade_date else None,
        "disclosure_date": t.disclosure_date.isoformat() if t.disclosure_date else None,
        "source": t.source,
    }


# ── AUD-SMARTMONEY: who is worth following, measured from DISCLOSURE not trade date ─────────

_SMART_MONEY_MIN_TRADES = 8      # below this a per-trader rate is anecdote, not evidence
_SMART_MONEY_HORIZON_BARS = 21   # ~1 trading month


def get_smart_money_leaderboard(min_trades: int = _SMART_MONEY_MIN_TRADES) -> dict:
    """Which disclosed traders have actually been worth following — entered at DISCLOSURE.

    TWO FACTS THAT DECIDE WHETHER THIS REPORT IS HONEST OR A GIMMICK.

    1. **Entry must be the disclosure date, never the trade date.** Congressional filings lag
       the trade by a MEDIAN OF 40 DAYS (mean 76, worst 323). Measured on this platform's own
       data, purchases return **+4.70% over 21 days from the trade date but +2.89% from the
       disclosure date** — roughly 1.8pp of the apparent edge has already happened by the time
       anyone outside could know. Quoting the trade-date figure would advertise a return the
       reader cannot reach. Same class of error as quoting a post-earnings drift that includes
       the overnight gap.

    2. **Most of the dataset has no buy/sell direction.** 7,691 of 9,453 rows come from the
       `unusual_whales` feed with `transaction_type = 'unknown'` — you cannot follow a trade
       when you do not know which way it went. Only the `kadoa_house` / `kadoa_senate` feeds
       carry real purchase/sale. Those rows are surfaced separately as `direction_unknown`
       rather than silently dropped, because "we track this person but cannot act on it" is a
       different and more useful statement than their absence.

    Every rate carries `n` and `sample_is_adequate`, matching this service's own convention in
    get_impact_direction_accuracy() — three findings in docs/2026-09-05 reversed once their
    samples widened.
    """
    # AUD-SMARTMONEY-PERF: this ran two CORRELATED SUBQUERIES per qualifying trade, each
    # scanning the price CTE. That was tolerable when only the 1,762 kadoa rows carried a
    # disclosure date; after AUD-UWCONGRESS-FIELDNAMES repaired the other ~7,000 it took 28.8s
    # and the page rendered "Failed to load" — the fix that made the data usable is what made
    # the query unusable. DISTINCT ON resolves every trade's entry/exit in ONE ordered pass.
    sql = text("""
        WITH px AS (
          SELECT stock_id, ts::date AS d, close,
                 LEAD(close, :horizon) OVER (PARTITION BY stock_id ORDER BY ts) AS fwd
          FROM prices WHERE timeframe = 'D1' AND ts >= :since
        ),
        cand AS (
          SELECT c.id, c.stock_id, c.politician_name, c.party, c.chamber, c.disclosure_date
          FROM congress_trades c
          WHERE c.stock_id IS NOT NULL
            AND c.transaction_type ILIKE '%purchase%'
            AND c.disclosure_date IS NOT NULL
        ),
        buys AS (
          SELECT DISTINCT ON (cd.id)
                 cd.politician_name, cd.party, cd.chamber, cd.disclosure_date,
                 p.close AS entry_px, p.fwd AS exit_px
          FROM cand cd
          JOIN px p ON p.stock_id = cd.stock_id AND p.d >= cd.disclosure_date
          ORDER BY cd.id, p.d
        )
        SELECT politician_name, party, chamber, count(*) AS n,
               avg(100.0 * (exit_px - entry_px) / entry_px) AS avg_pct,
               100.0 * count(*) FILTER (WHERE exit_px > entry_px) / count(*) AS pct_up,
               max(disclosure_date) AS latest_disclosure
        FROM buys
        WHERE entry_px IS NOT NULL AND exit_px IS NOT NULL AND entry_px > 0
        GROUP BY 1, 2, 3
        ORDER BY avg_pct DESC
    """)
    since = date.today() - timedelta(days=500)
    with SessionLocal() as s:
        rows = s.execute(sql, {"horizon": _SMART_MONEY_HORIZON_BARS, "since": since}).all()
        n_unknown_rows = s.execute(text(
            "SELECT count(*) FROM congress_trades WHERE transaction_type NOT ILIKE '%purchase%' "
            "AND transaction_type NOT ILIKE '%sale%'"
        )).scalar() or 0
        unknown = s.execute(text("""
            SELECT politician_name, count(*) AS n,
                   count(*) FILTER (WHERE stock_id IS NOT NULL) AS on_tracked,
                   max(trade_date) AS latest
            FROM congress_trades
            WHERE transaction_type NOT ILIKE '%purchase%'
              AND transaction_type NOT ILIKE '%sale%'
            GROUP BY 1 ORDER BY n DESC LIMIT 10
        """)).all()

    traders = [{
        "name": r.politician_name,
        "party": r.party or None,
        "chamber": r.chamber or None,
        "n_buys": r.n,
        "avg_21d_pct": round(float(r.avg_pct), 2),
        "pct_up": round(float(r.pct_up), 0),
        "latest_disclosure": r.latest_disclosure.isoformat() if r.latest_disclosure else None,
        # Below the floor this is an anecdote. Reported, never ranked on.
        "sample_is_adequate": r.n >= min_trades,
    } for r in rows]
    followable = [t for t in traders if t["sample_is_adequate"]]

    return {
        "horizon_days": _SMART_MONEY_HORIZON_BARS,
        "min_trades_for_adequacy": min_trades,
        "entry_basis": "disclosure_date",
        "traders": traders,
        "n_followable": len(followable),
        "direction_unknown": [{
            "name": u.politician_name,
            "n_records": u.n,
            "on_tracked_stock": u.on_tracked,
            "latest_trade": u.latest.isoformat() if u.latest else None,
        } for u in unknown],
        "caveats": [
            # Measured 2026-09-23 on the repaired dataset. An earlier version of this text
            # quoted a 1.81pp gap (+4.70% vs +2.89%) computed before AUD-UWCONGRESS-FIELDNAMES
            # — i.e. on the 19% of rows that then had a usable disclosure date. The direction
            # of the effect survived the correction; its size did not.
            "Returns are measured from the DISCLOSURE date, not the trade date. Congressional "
            "filings lag the trade by a median of 33 days (mean 45, worst 323). The same "
            "purchases return +3.73% over 21 days from the trade date but +3.15% from "
            "disclosure. Only the latter was ever reachable.",
            "Traders under the sample floor are shown but must not be ranked on.",
            f"{n_unknown_rows} rows still carry no buy/sell direction and cannot be followed at "
            "all — a disclosure there may be a SALE. They are listed rather than dropped.",
            "This is a historical base rate, not a recommendation. It says nothing about why "
            "any individual trade was made.",
        ],
    }
