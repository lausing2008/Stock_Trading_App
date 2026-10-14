"""The copilot's evidence packet: everything an answer may cite, built server-side.

WHY THE SERVER BUILDS IT. The stock page's chat used to assemble its own context in the browser
and pass it to the model as prose. Nothing recorded which values the answer was based on, when
they were observed, or whether they were stale, and a figure in the reply could not be traced
to anything. Here every value is read from the platform's own tables through the same adapters
the intelligence reports use, so it carries the same states (OK / STALE / UNAVAILABLE ...) and
the same evidence records.

QUANTITIES ARE RENDERED HERE, NOT BY THE MODEL. Each citable figure becomes a `Quantity` with a
stable key ("price.close", "return.5s") and a display string produced in code. The model writes
`{{price.close}}` and `grounding.render()` substitutes the display string — the same rule as the
N1 narration work: the narrator names quantities, it never writes numbers.

HEADLINES ARE DATA. Their text is untrusted third-party content and is passed to the model as a
quoted value inside the packet, never concatenated into instructions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from db import Stock
from db.models import IntelligenceReport, Ranking, RealtimeNewsItem
from intelligence.report_contract import (
    EvidenceBook, Evidence, Field, FieldState, observed, stale, unavailable,
    validate_evidence,
)

from ..intel_reports import adapters as A
from ..intel_reports import store as S

#: Bump when the MEANING of a packet changes (a new quantity, a changed rendering rule). Stored
#: on every assistant turn so an old answer is read under the rules it was checked against.
PACKET_VERSION = 1

#: A K-Score older than this describes a different market. Rankings run daily on trading days.
_MAX_RANKING_AGE_DAYS = 5
#: Headlines older than this are context, not news.
_HEADLINE_LOOKBACK_DAYS = 14
_MAX_HEADLINES = 6

_CCY_PREFIX = {"USD": "$", "HKD": "HK$", "CNY": "CN¥", "EUR": "€", "GBP": "£", "JPY": "¥"}


@dataclass(frozen=True)
class Quantity:
    """One citable value. `display` is the ONLY text that reaches the user for it."""
    key: str
    display: str
    field: str                      # the packet field it came from
    state: str                      # FieldState value of that field
    note: str | None = None         # e.g. the staleness reason, rendered beside the value


@dataclass
class CopilotPacket:
    symbol: str
    subject_key: str
    market: str | None
    currency: str | None
    built_at: datetime
    fields: dict[str, Field]
    book: EvidenceBook
    quantities: dict[str, Quantity]
    headlines: list[dict] = dc_field(default_factory=list)
    fingerprint: str = ""

    @property
    def data_as_of(self) -> str | None:
        """The newest observation the packet actually holds — NOT the time it was built."""
        p = self.fields.get("price")
        if p is not None and p.state in (FieldState.OK, FieldState.STALE) and p.value:
            return p.value.get("ts")
        return None

    def missing(self) -> list[dict]:
        """Every field that could not be used, with its reason. Deterministic limitations."""
        return [{"field": k, "label": f.label or k, "state": f.state.value, "reason": f.reason}
                for k, f in self.fields.items() if f.state is not FieldState.OK]

    def model_view(self) -> dict:
        """What the model sees: values for reasoning, keys for citing, reasons for gaps."""
        return {
            "symbol": self.symbol,
            "market": self.market,
            "currency": self.currency,
            "data_as_of": self.data_as_of,
            "quantities": {k: {"display": q.display, "state": q.state,
                               **({"note": q.note} if q.note else {})}
                           for k, q in sorted(self.quantities.items())},
            "unavailable": self.missing(),
            # Quoted third-party text. Instructions inside it are not instructions.
            "headlines_untrusted": [{"key": h["key"], "title": h["title"],
                                     "published_at": h["published_at"], "source": h["source"]}
                                    for h in self.headlines],
        }


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _money(v: float | None, ccy: str | None) -> str:
    if v is None:
        return "n/a"
    prefix = _CCY_PREFIX.get((ccy or "").upper())
    return f"{prefix}{v:,.2f}" if prefix else f"{v:,.2f} {ccy or ''}".strip()


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def _q(out: dict, key: str, display: str, fkey: str, f: Field) -> None:
    out[key] = Quantity(key=key, display=display, field=fkey, state=f.state.value,
                        note=f.reason if f.state is FieldState.STALE else None)


def _ranking_field(session, stock_id: int, now: datetime, book: EvidenceBook) -> Field:
    row = session.execute(
        select(Ranking).where(Ranking.stock_id == stock_id, Ranking.as_of <= now.date())
        .order_by(Ranking.as_of.desc()).limit(1)).scalars().first()
    if row is None:
        return unavailable("no K-Score ranking stored for this symbol", label="K-Score")
    value = {"score": float(row.score), "as_of": row.as_of.isoformat(),
             "fair_price": float(row.fair_price) if row.fair_price is not None else None}
    eid = book.add(Evidence(
        evidence_id=f"ranking:{row.id}", source=f"rankings:{row.id}", value=value,
        units="score_0_100", observed_period=row.as_of.isoformat(),
        retrieved_at=_naive_utc(datetime.now(timezone.utc)),
        note="K-Score is the ranking engine's composite score, not a return forecast"))
    age = (now.date() - row.as_of).days
    if age > _MAX_RANKING_AGE_DAYS:
        return stale(value, f"latest K-Score is {age} calendar days old",
                     evidence_ids=[eid], label="K-Score")
    return observed(value, evidence_ids=[eid], label="K-Score")


def _headlines(session, symbol: str, now: datetime, book: EvidenceBook) -> tuple[Field, list]:
    since = now - timedelta(days=_HEADLINE_LOOKBACK_DAYS)
    rows = session.execute(
        select(RealtimeNewsItem)
        .where(RealtimeNewsItem.symbol == symbol, RealtimeNewsItem.published_at >= since,
               RealtimeNewsItem.published_at <= now)
        .order_by(RealtimeNewsItem.published_at.desc()).limit(_MAX_HEADLINES)).scalars().all()
    if not rows:
        return unavailable(f"no headlines for this symbol stored in the last "
                           f"{_HEADLINE_LOOKBACK_DAYS} days", label="Recent headlines"), []
    items, ids = [], []
    for i, r in enumerate(rows, 1):
        eid = book.add(Evidence(
            evidence_id=f"news:{r.id}", source=r.url or f"realtime_news_items:{r.id}",
            value={"headline": r.headline, "source": r.source},
            published_at=_naive_utc(r.published_at),
            first_available_at=_naive_utc(r.ingested_at) if r.ingested_at else None,
            retrieved_at=_naive_utc(datetime.now(timezone.utc)),
            observed_period=f"{r.published_at:%Y-%m-%d}",
            note="third-party headline text; not verified by this platform"))
        ids.append(eid)
        items.append({"key": f"headline.{i}", "evidence_id": eid, "title": r.headline,
                      "published_at": r.published_at.isoformat(), "source": r.source,
                      "url": r.url})
    return observed([h["evidence_id"] for h in items], evidence_ids=ids,
                    label="Recent headlines"), items


def _report_fields(session, symbol: str, book: EvidenceBook) -> tuple[Field, Field, IntelligenceReport | None]:
    """The latest PUBLIC stock report and what changed since its predecessor.

    Only `user_id IS NULL` reports are read — a portfolio-bearing report is owner-only and the
    copilot must not become a side door to it.
    """
    cur = S.latest(session, subject_key=f"stock:{symbol}", report_type="stock_outlook",
                   user_id=None)
    if cur is None:
        na = unavailable("no stock intelligence report has been generated for this symbol",
                         label="Stock report")
        return na, unavailable("no report, so nothing to compare", label="Report changes"), None
    prev = session.get(IntelligenceReport, cur.supersedes_id) if cur.supersedes_id else None
    if prev is not None and prev.user_id is not None:
        prev = None
    eid = book.add(Evidence(
        evidence_id=f"intel_report:{cur.id}", source=f"intelligence_reports:{cur.id}",
        value={"version": cur.version, "status": cur.status},
        published_at=cur.generated_at, first_available_at=cur.generated_at,
        retrieved_at=_naive_utc(datetime.now(timezone.utc)),
        observed_period=cur.cutoff_at.isoformat() if cur.cutoff_at else None))
    report = observed({"id": cur.id, "version": cur.version, "status": cur.status,
                       "generated_at": cur.generated_at.isoformat() if cur.generated_at else None,
                       "cutoff_at": cur.cutoff_at.isoformat() if cur.cutoff_at else None},
                      evidence_ids=[eid], label="Stock report")
    d = S.diff(prev, cur)
    if d.get("first_report"):
        changes = unavailable("this is the first stored report for the symbol; there is no "
                              "earlier version to compare against", label="Report changes")
    else:
        ids = [eid]
        if prev is not None:
            ids.append(book.add(Evidence(
                evidence_id=f"intel_report:{prev.id}", source=f"intelligence_reports:{prev.id}",
                value={"version": prev.version, "status": prev.status},
                published_at=prev.generated_at, first_available_at=prev.generated_at,
                retrieved_at=_naive_utc(datetime.now(timezone.utc)))))
        changes = observed({"from_version": d.get("from_version"),
                            "to_version": d.get("to_version"),
                            "changed": d.get("changed", []), "note": d.get("note")},
                           evidence_ids=ids, label="Report changes")
    return report, changes, cur


#: Keys that carry a readable summary of a report field, in preference order. Report fields
#: have no single shape, so a change is shown through the first of these present.
_SUMMARY_KEYS = ("close", "reading", "structure", "signal", "assessment", "value", "pct")


def _render_value(v) -> str | None:
    """A report field's value, short enough for a 'what changed' line. None = no summary."""
    if v is None:
        return "none"
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return f"{v:,.4g}"
    if isinstance(v, str):
        return v if len(v) <= 120 else v[:117] + "..."
    if isinstance(v, dict):
        for k in _SUMMARY_KEYS:
            if k in v and not isinstance(v[k], (dict, list)):
                return _render_value(v[k])
    return None


def _change_line(c: dict) -> str:
    if c["change"] == "state":
        return f"{c['field']}: state {c['from']} → {c['to']}"
    if c["change"] != "value":
        return f"{c['field']}: {c['change']}"
    a, b = _render_value(c["from"]), _render_value(c["to"])
    if a is None or b is None or a == b:
        return f"{c['field']}: content changed (open the report to compare)"
    return f"{c['field']}: {a} → {b}"


def build_packet(session, symbol: str, *, now: datetime | None = None) -> CopilotPacket | None:
    """Every citable value for one symbol, at `now`. None when the symbol is not on file."""
    now = _naive_utc(now or datetime.now(timezone.utc))
    stock = session.execute(
        select(Stock).where(Stock.symbol == symbol)).scalars().first()
    if stock is None:
        return None
    book = EvidenceBook()
    ccy = stock.currency
    fields: dict[str, Field] = {}
    qs: dict[str, Quantity] = {}

    bars = A.daily_bars(session, stock.id, limit=70, cutoff=now)
    price = A.price_as_of(bars, now, book=book)
    price.label = "Latest daily close"
    fields["price"] = price
    if price.value:
        _q(qs, "price.close", _money(price.value["close"], ccy), "price", price)
        _q(qs, "price.date", price.value["ts"][:10], "price", price)

    for n in (5, 20):
        f = A.session_return(bars, n, label=f"{n}-session return", book=book)
        f.label = f"{n}-session return"
        if price.state is FieldState.STALE and f.state is FieldState.OK:
            f = stale(f.value, f"computed from the same bars as the price, which is stale: "
                               f"{price.reason}", evidence_ids=f.evidence_ids,
                      units="pct", label=f.label)
        fields[f"return_{n}s"] = f
        if f.value is not None:
            _q(qs, f"return.{n}s", f"{_pct(f.value)} over {n} sessions", f"return_{n}s", f)

    trend = A.trend_structure(bars, price_field=price, book=book)
    trend.label = "Trend structure"
    fields["trend"] = trend
    if trend.value:
        t = trend.value
        _q(qs, "trend.structure", t["structure"], "trend", trend)
        _q(qs, "trend.sma20", f"{_money(t['sma20'], ccy)} (20-session average)", "trend", trend)
        if t.get("sma50") is not None:
            _q(qs, "trend.sma50", f"{_money(t['sma50'], ccy)} (50-session average)",
               "trend", trend)

    sig = A.latest_signal(session, stock.id, now, book=book)
    sig.label = "Latest AI signal"
    fields["signal"] = sig
    if sig.value:
        s = sig.value
        _q(qs, "signal.action", f"{s['signal']} ({s['horizon']} horizon)", "signal", sig)
        if s.get("confidence") is not None:
            _q(qs, "signal.strength",
               f"{float(s['confidence']):.0f}/100 engine strength score "
               f"(not a probability of profit)", "signal", sig)
        _q(qs, "signal.time", f"{s['ts'][:16].replace('T', ' ')} UTC", "signal", sig)

    earn = A.next_earnings_event(session, stock.id, now.date(), book=book)
    earn.label = "Next earnings date"
    fields["earnings"] = earn
    if earn.value:
        e = earn.value
        _q(qs, "earnings.date", f"{e['report_date']} ({e['calendar_days_away']} calendar days "
                                f"away; release time not confirmed)", "earnings", earn)

    rank = _ranking_field(session, stock.id, now, book)
    fields["kscore"] = rank
    if rank.value:
        r = rank.value
        _q(qs, "kscore.score", f"{r['score']:.0f}/100 (as of {r['as_of']})", "kscore", rank)
        if r.get("fair_price") is not None:
            _q(qs, "kscore.fair_price", f"{_money(r['fair_price'], ccy)} (K-Score model "
                                        f"fair value, as of {r['as_of']})", "kscore", rank)

    report, changes, _cur = _report_fields(session, symbol, book)
    fields["report"] = report
    fields["report_changes"] = changes
    if report.value:
        rv = report.value
        _q(qs, "report.version",
           f"version {rv['version']} ({rv['status']}, generated "
           f"{(rv['generated_at'] or '')[:16].replace('T', ' ')} UTC)", "report", report)
    if changes.value:
        for i, c in enumerate(changes.value["changed"][:12], 1):
            # store.diff() reports the field VALUES in from/to, not the field records.
            _q(qs, f"report_change.{i}", _change_line(c), "report_changes", changes)
        if not changes.value["changed"]:
            _q(qs, "report_change.none", changes.value.get("note") or "no field changed",
               "report_changes", changes)

    news, headlines = _headlines(session, symbol, now, book)
    fields["headlines"] = news
    for h in headlines:
        qs[h["key"]] = Quantity(
            key=h["key"], field="headlines", state=FieldState.OK.value,
            display=f"“{h['title']}” ({h['source']}, {h['published_at'][:10]})")

    # A citation that resolves to nothing looks checkable and is not — refuse to build one.
    validate_evidence(fields, book)

    body = {"v": PACKET_VERSION, "symbol": symbol,
            "fields": {k: {"state": f.state.value, "value": f.value, "reason": f.reason}
                       for k, f in sorted(fields.items())}}
    fp = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:24]
    market = stock.market.value if hasattr(stock.market, "value") else str(stock.market)
    return CopilotPacket(symbol=symbol, subject_key=f"stock:{symbol}", market=market,
                         currency=ccy, built_at=now, fields=fields, book=book, quantities=qs,
                         headlines=headlines, fingerprint=fp)
