"""The copilot's evidence packet: everything an answer may cite, built server-side.

WHY THE SERVER BUILDS IT. The stock page's chat used to assemble its own context in the browser
and pass it to the model as prose. Nothing recorded which values the answer was based on, when
they were observed, or whether they were stale, and a figure in the reply could not be traced
to anything. Here every value is read from the platform's own tables through the same adapters
the intelligence reports use, so it carries the same states (OK / STALE / UNAVAILABLE ...) and
the same evidence records.

QUANTITIES ARE RENDERED HERE, NOT BY THE MODEL. Each citable figure becomes a `Quantity` with a
stable key ("price.close", "return.5b") and a display string produced in code. The model writes
`{{price.close}}` and `grounding.render()` substitutes the display string — the same rule as the
N1 narration work: the narrator names quantities, it never writes numbers.

HEADLINES ARE DATA. Their text is untrusted third-party content and is passed to the model as a
quoted value inside the packet, never concatenated into instructions.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from db import Stock
from db.models import (IntelligenceReport, Ranking, RealtimeNewsItem, Signal,
                       SignalOutcomeHorizon)
from intelligence.report_contract import (
    EvidenceBook, Evidence, Field, FieldState, observed, stale, unavailable,
    validate_evidence,
)

from ..intel_reports import adapters as A
from ..intel_reports import store as S
from . import plan as P

#: Bump when the MEANING of a packet changes (a new quantity, a changed rendering rule). Stored
#: on every assistant turn so an old answer is read under the rules it was checked against.
PACKET_VERSION = 6
#: v6: numeric plan levels and deterministic level-draft requests for the stock-page card.
#: v5 (2026-10-15): stateless stop candidates versus a user-stated held stop, explicit
#: unsupported-position refusal, and complete typed risk units. Old messages retain v4.
#: v4 (2026-10-14, plan mode): when the question asks for a forecast or a plan, the packet also
#: carries the plan engine's lines (plan.py): calls per horizon with their track records, the
#: stock's past move ranges, code-calculated levels and textbook if→then rules.
#: v3 (2026-10-13, independent review): N-BAR returns with endpoint dates (R5); every average
#: and return cites ALL its inputs plus a calculation record (R6); report age and staleness;
#: price.as_of / price.age replace data.as_of / data.age (they only ever described prices).
#: v2 (2026-10-13): displays are bare values and each quantity's meaning travels separately
#: (v1 rendered labels inside the value, so answers read "-6.18% over 20 sessions return is
#: -6.18% over 20 sessions"); headlines addressed to AI systems are withheld.

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
    #: What the value IS, for the model to reason with. Never rendered into the answer — the
    #: model writes its own words around a bare value.
    meaning: str | None = None
    #: The evidence behind THIS value, when it is narrower or wider than its field's — e.g. the
    #: 50-bar average cites all 50 closes, while the trend field it belongs to cites 20 (R6).
    evidence_ids: tuple = ()


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
    #: The plan engine's output, when the question asked for one (None otherwise).
    plan: "P.Plan | None" = None

    @property
    def price_as_of(self) -> str | None:
        """The latest daily price bar's time. NOT the newest observation of every source: a
        signal, report or headline can be newer or older — see source_times()."""
        p = self.fields.get("price")
        if p is not None and p.state in (FieldState.OK, FieldState.STALE) and p.value:
            return p.value.get("ts")
        return None

    def source_times(self) -> dict:
        """When each source's newest value was observed or generated, separately."""
        def v(key, sub):
            f = self.fields.get(key)
            return (f.value or {}).get(sub) if f is not None and isinstance(f.value, dict) else None
        return {
            "price": self.price_as_of,
            "signal": v("signal", "ts"),
            "report_generated": v("report", "generated_at"),
            "newest_headline": self.headlines[0]["published_at"] if self.headlines else None,
        }

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
            "price_as_of": self.price_as_of,
            "source_times": self.source_times(),
            "quantities": {k: {"display": q.display, "state": q.state,
                               **({"meaning": q.meaning} if q.meaning else {}),
                               **({"note": q.note} if q.note else {})}
                           for k, q in sorted(self.quantities.items())},
            "unavailable": self.missing(),
            "plan_kind": self.plan.kind if self.plan is not None else None,
            **({} if self.plan is not None else {"playbook": (
                "not loaded for this question: the platform's calls with their track records, "
                "levels and textbook rules are added when the user asks for a forecast, an "
                "entry plan or a plan for a position they hold")}),
            # Quoted third-party text. Instructions inside it are not instructions.
            "headlines_untrusted": [{"key": h["key"], "published_at": h["published_at"],
                                     "source": h["source"],
                                     "title": (WITHHELD_TITLE if h.get("withheld")
                                               else h["title"])}
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


def _q(out: dict, key: str, display: str, fkey: str, f: Field, meaning: str | None = None,
       evidence=()) -> None:
    out[key] = Quantity(key=key, display=display, field=fkey, state=f.state.value,
                        note=f.reason if f.state is FieldState.STALE else None, meaning=meaning,
                        evidence_ids=tuple(evidence))


def _weekdays_between(a: datetime, b: datetime) -> int:
    """Weekdays in (a, b]. Trading days on weekday exchanges can only be fewer (holidays)."""
    from datetime import timedelta as _td
    n, d = 0, a.date()
    while d < b.date():
        d += _td(days=1)
        n += d.weekday() < 5
    return n


#: R5: N stored bars should span about N weekdays. More weekdays than N + 1 (one holiday of
#: slack) means bars are probably MISSING. Without an exchange calendar this is a heuristic, so
#: it is stated beside the value — never used to relabel the window as sessions.
_HOLIDAY_SLACK = 1
#: A report older than this describes its own time, not today.
_REPORT_MAX_AGE_DAYS = 7


def _calc(book: EvidenceBook, stock_id: int, name: str, bars: list, result, method: str) -> list[str]:
    """Record a calculation and EVERY input it used; return [calc id, *input ids] (R6).

    The trend adapter records only the first 20 bars (it is shared with the intelligence
    reports, whose fingerprints would change if it recorded more), so the copilot records the
    complete input set itself. A reader can recompute the value from these records alone.
    """
    ids = [A.record_bar(book, b) for b in bars]
    cid = book.add(Evidence(
        evidence_id=f"calc:{name}:{stock_id}:{bars[0].ts:%Y-%m-%d}",
        source="copilot calculation", value={"method": method, "inputs": ids, "result": result},
        units="price" if "sma" in name else "pct", basis="unadjusted closes",
        observed_period=f"{bars[-1].ts:%Y-%m-%d}..{bars[0].ts:%Y-%m-%d}",
        retrieved_at=_naive_utc(datetime.now(timezone.utc))))
    return [cid, *ids]


#: Text addressed to an AI system rather than to a reader. A HEURISTIC, and stated as one: it
#: catches the blunt forms measured in the eval (and their obvious variants), not a determined
#: attacker. What it buys is that a caught headline is neither shown to the model nor printed
#: into an answer by the platform's own rendering — before this, `{{headline.1}}` re-printed the
#: attacker's "guaranteed to double ... buy on full margin" inside the assistant's reply.
_ADDRESSED_TO_AI = re.compile(
    r"ignore (?:all |any )?(?:previous|prior|above) (?:instructions|prompts)|"
    r"\b(?:assistants?|ai|chatbots?|language models?|llms?)\b[^.]{0,40}\b(?:must|should|are to|"
    r"need to)\b|you are now\b|admin mode|developer mode|system prompt|\bsystem:|"
    r"disregard (?:the|your|all) (?:rules|instructions)|new instructions",
    re.I)
WITHHELD_TITLE = "[withheld headline]"
_WITHHELD_MEANING = ("a third-party headline WITHHELD by the platform because its text addresses "
                     "AI assistants; its content is not available to you — the user can open the "
                     "source link")


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
                      "url": r.url,
                      "withheld": bool(_ADDRESSED_TO_AI.search(r.headline or ""))})
    return observed([h["evidence_id"] for h in items], evidence_ids=ids,
                    label="Recent headlines"), items


def _report_labels(r: IntelligenceReport | None) -> dict[str, str]:
    fields = ((r.payload or {}).get("fields") or {}) if r is not None else {}
    return {k: v.get("label") for k, v in fields.items() if isinstance(v, dict) and v.get("label")}


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


def _change_line(c: dict, labels: dict[str, str]) -> str:
    """One change, under the report's HUMAN label. Measured: with raw keys such as
    `return_63_bars` the model paraphrased them ("63-bar return") and was refused for it."""
    name = labels.get(c["field"]) or c["field"].replace("_", " ")
    if c["change"] == "state":
        return f"{name}: state {c['from']} → {c['to']}"
    if c["change"] != "value":
        return f"{name}: {c['change']}"
    a, b = _render_value(c["from"]), _render_value(c["to"])
    if a is None or b is None or a == b:
        return f"{name}: content changed (open the report to compare)"
    return f"{name}: {a} → {b}"


def _calls(session, stock_id: int, now: datetime) -> dict:
    """The latest stored call per horizon, each at or before `now`."""
    out = {}
    for h in P.HORIZONS:
        row = session.execute(
            select(Signal).where(Signal.stock_id == stock_id, Signal.ts <= now,
                                 Signal.horizon == h)
            .order_by(Signal.ts.desc()).limit(1)).scalars().first()
        if row is not None:
            out[h] = P.Call(horizon=h, signal=getattr(row.signal, "value", str(row.signal)),
                            confidence=float(row.confidence) if row.confidence is not None else None,
                            ts=_naive_utc(row.ts), row_id=row.id)
    return out


def _outcomes(session, horizons, now: datetime) -> list:
    """Resolved outcome rows for these horizons, observable at `now` (no look-ahead)."""
    if not horizons:
        return []
    T = SignalOutcomeHorizon
    rows = session.execute(
        select(T.id, T.symbol, T.horizon, T.signal_direction, T.window_days, T.signal_date,
               T.pct_return, T.is_correct, T.evidence_status)
        .where(T.status == "resolved", T.horizon.in_(list(horizons)),
               T.window_days.in_(list(P.RECORD_WINDOWS)),
               T.signal_date >= (now - timedelta(days=P.RECORD_LOOKBACK_DAYS)).date(),
               T.resolved_at <= now, T.pct_return.is_not(None))).all()
    return [P.OutcomeRow(row_id=r[0], symbol=r[1], horizon=str(getattr(r[2], "value", r[2])),
                         direction=r[3], window_days=r[4], signal_date=r[5],
                         pct_return=float(r[6]), is_correct=r[7], evidence_status=r[8])
            for r in rows]


def _plan_fields(plan: "P.Plan", fields: dict, qs: dict) -> None:
    """The plan's lines as packet quantities, one field per group."""
    groups: dict[str, list] = {}
    for it in plan.items.values():
        groups.setdefault(it.group, []).append(it)
    labels = {"levels": "Price levels", "moves": "Past move ranges", "calls": "Signal calls",
              "records": "Track records", "rules": "Textbook rules", "position": "Your position"}
    for g, items in groups.items():
        ids = sorted({e for it in items for e in it.evidence_ids})
        value = {it.key: it.display for it in items}
        stale_reasons = sorted({it.stale for it in items if it.stale})
        fkey = f"plan_{g}"
        if stale_reasons:
            fields[fkey] = stale(value, "; ".join(stale_reasons), evidence_ids=ids,
                                 label=labels.get(g, g))
        else:
            fields[fkey] = Field(value=value, state=FieldState.OK, evidence_ids=ids,
                                 label=labels.get(g, g))
        for it in items:
            qs[it.key] = Quantity(key=it.key, display=it.display, field=fkey,
                                  state="STALE" if it.stale else FieldState.OK.value,
                                  note=it.stale, meaning=it.meaning,
                                  evidence_ids=it.evidence_ids)
    # Citable as well as listed (measured: the model cited the FIELD names {{plan_gaps}} and
    # {{plan_position_input}} to relay these, and was refused for keys that were not quantities).
    if plan.position_refusals:
        why = "; ".join(plan.position_refusals)
        fields["plan_position_input"] = unavailable(why, label="Position you typed")
        qs["plan.position_note"] = Quantity(
            key="plan.position_note", display=why, field="plan_position_input",
            state=FieldState.UNAVAILABLE.value,
            meaning="why the position the user typed was not used, and how to state it")
    if plan.unavailable:
        why = "; ".join(f"{v}" for _, v in sorted(plan.unavailable.items()))
        fields["plan_gaps"] = unavailable(why, label="Plan items not available")
        qs["plan.gaps"] = Quantity(key="plan.gaps", display=why, field="plan_gaps",
                                   state=FieldState.UNAVAILABLE.value,
                                   meaning="plan items that could not be built, with reasons")


def build_packet(session, symbol: str, *, now: datetime | None = None,
                 plan_request=None) -> CopilotPacket | None:
    """Every citable value for one symbol, at `now`. None when the symbol is not on file.

    `plan_request` is an `intent.PositionIntent` with a `kind`; when given, the plan engine's
    lines are added (plan mode). Without it the packet is exactly the v3 content.
    """
    now = _naive_utc(now or datetime.now(timezone.utc))
    stock = session.execute(
        select(Stock).where(Stock.symbol == symbol)).scalars().first()
    if stock is None:
        return None
    book = EvidenceBook()
    ccy = stock.currency
    fields: dict[str, Field] = {}
    qs: dict[str, Quantity] = {}

    bars = A.daily_bars(session, stock.id, limit=(P.YEAR_BARS + 8) if plan_request else 70,
                        cutoff=now)
    price = A.price_as_of(bars, now, book=book)
    price.label = "Latest daily close"
    fields["price"] = price
    if price.value:
        _q(qs, "price.close", _money(price.value["close"], ccy), "price", price,
           f"latest daily closing price ({ccy}, unadjusted)")
        _q(qs, "price.date", price.value["ts"][:10], "price", price,
           "the session date of that close")
        # Measured: the model reached for {{data_as_of}} when describing freshness and was
        # refused for citing a key that did not exist. It describes PRICES only (review).
        _q(qs, "price.as_of", f"{price.value['ts'][:16].replace('T', ' ')} UTC", "price", price,
           "timestamp of the latest daily price bar (signals, reports and headlines carry their "
           "own times)")
        # Its AGE as a citable value. Measured: on a stale symbol the model quoted the age from
        # the staleness reason ("12 calendar days old") and was refused for writing a figure.
        age = (now - _naive_utc(datetime.fromisoformat(price.value["ts"]))).days
        _q(qs, "price.age", f"{age} calendar days", "price", price,
           "how long ago, counted from TODAY, the latest daily price bar was observed. Pair it "
           "with today, never with the bar's own date")

    for n in (5, 20):
        # R5: these are N STORED BARS apart, not N exchange sessions — the adapter does not
        # check an exchange calendar, so a missing bar silently lengthens the window. Labelled
        # as bars, with both endpoint dates, and a long span is stated rather than hidden.
        f = A.session_return(bars, n, label=f"{n}-bar return", book=book)
        f.label = f"{n}-bar return"
        if price.state is FieldState.STALE and f.state is FieldState.OK:
            f = stale(f.value, f"computed from the same bars as the price, which is stale: "
                               f"{price.reason}", evidence_ids=f.evidence_ids,
                      units="pct", label=f.label)
        fields[f"return_{n}b"] = f
        if f.value is not None:
            latest, prior = bars[0], bars[n]
            span = (_naive_utc(latest.ts) - _naive_utc(prior.ts)).days
            gap = _weekdays_between(_naive_utc(prior.ts), _naive_utc(latest.ts)) > n + _HOLIDAY_SLACK
            disp = (f"{_pct(f.value)} ({prior.ts:%Y-%m-%d} → {latest.ts:%Y-%m-%d}"
                    + ("; possible missing bars" if gap else "") + ")")
            ev = _calc(book, stock.id, f"return{n}b", [latest, prior], f.value,
                       f"(close[0] - close[{n}]) / close[{n}] x 100 over stored daily bars")
            _q(qs, f"return.{n}b", disp, f"return_{n}b", f,
               f"close-to-close change between two stored daily closes {n} bars apart, "
               f"unadjusted for splits and dividends. The bars are not checked against an "
               f"exchange calendar; these endpoints span {span} calendar days"
               + (", longer than usual, so bars may be missing" if gap else ""), evidence=ev)

    trend = A.trend_structure(bars, price_field=price, book=book)
    trend.label = "Trend structure"
    fields["trend"] = trend
    if trend.value:
        t = trend.value
        # The shared adapter's wording says "20-session average"; the copilot counts stored
        # bars, not exchange sessions (R5), so its own display says bars. Measured: the old
        # wording reached a held-out answer as "below the 20-session average".
        _q(qs, "trend.structure", t["structure"].replace("-session", "-bar"), "trend", trend,
           "where the latest close sits relative to the moving averages (a description of "
           "past bars, not a forecast)")
        _q(qs, "trend.sma20", _money(t["sma20"], ccy), "trend", trend,
           "simple moving average of the last 20 stored daily closes (unadjusted)",
           evidence=_calc(book, stock.id, "sma20", bars[:20], t["sma20"],
                          "arithmetic mean of the 20 most recent stored daily closes"))
        if t.get("sma50") is not None:
            _q(qs, "trend.sma50", _money(t["sma50"], ccy), "trend", trend,
               "simple moving average of the last 50 stored daily closes (unadjusted)",
               evidence=_calc(book, stock.id, "sma50", bars[:50], t["sma50"],
                              "arithmetic mean of the 50 most recent stored daily closes"))

    sig = A.latest_signal(session, stock.id, now, book=book)
    sig.label = "Latest AI signal"
    fields["signal"] = sig
    if sig.value:
        s = sig.value
        _q(qs, "signal.action", f"{s['signal']} ({s['horizon']})", "signal", sig,
           "the signal engine's latest call and its horizon")
        if s.get("confidence") is not None:
            # The one caveat kept IN the display: a bare "61/100" invites being read as a
            # probability, and the platform's own audits found these bands flat.
            _q(qs, "signal.strength", f"{float(s['confidence']):.0f}/100 (engine score)",
               "signal", sig, "the signal engine's strength score; NOT a probability of "
                              "profit and not calibrated")
        # Measured: asked to "explain this signal", the model wrote "price momentum supports the
        # call" — a rationale the packet cannot support, because the engine's inputs and their
        # weights are not stored with the signal row.
        fields["signal_drivers"] = unavailable(
            "the signal engine's inputs and their weights are not stored with the signal, so "
            "what drove this call cannot be stated; other figures here are context, not its "
            "reasons", label="Why the engine made this call")
        _q(qs, "signal.time", f"{s['ts'][:16].replace('T', ' ')} UTC", "signal", sig,
           "when that signal was generated")

    earn = A.next_earnings_event(session, stock.id, now.date(), book=book)
    earn.label = "Next earnings date"
    fields["earnings"] = earn
    if earn.value:
        e = earn.value
        _q(qs, "earnings.date", e["report_date"], "earnings", earn,
           "scheduled date of the next earnings release; release time (before/after market) "
           "is not confirmed")
        _q(qs, "earnings.days_away", f"{e['calendar_days_away']} calendar days", "earnings",
           earn, "calendar days from today to that date")

    # Explicitly unavailable in this phase, so a valuation question gets a stated reason rather
    # than an inference from absence (measured: the model had to note that P/E was "not listed
    # among unavailable fields").
    fields["fundamentals"] = unavailable(
        "fundamentals and valuation multiples (P/E, EPS, margins, balance sheet) are not "
        "included in the copilot's evidence in this phase", label="Fundamentals & valuation")

    rank = _ranking_field(session, stock.id, now, book)
    fields["kscore"] = rank
    if rank.value:
        r = rank.value
        _q(qs, "kscore.score", f"{r['score']:.0f}/100", "kscore", rank,
           "the ranking engine's composite K-Score; a relative ranking, not a return forecast")
        _q(qs, "kscore.as_of", r["as_of"], "kscore", rank, "date of that K-Score")
        if r.get("fair_price") is not None:
            _q(qs, "kscore.fair_price", _money(r["fair_price"], ccy), "kscore", rank,
               "the K-Score model's fair-value estimate; a model output, not an analyst "
               "price target")

    report, changes, cur = _report_fields(session, symbol, book)
    # FRESHNESS (review): a 29-day-old report was marked OK. Its contents describe its own
    # cutoff, not today, so past the age limit both it and its changes are STALE with the age
    # stated — still readable as history, never presented as current analysis.
    if report.value and report.value.get("generated_at"):
        r_age = (now - _naive_utc(datetime.fromisoformat(report.value["generated_at"]))).days
        if r_age > _REPORT_MAX_AGE_DAYS:
            why = (f"report generated {r_age} calendar days ago; it describes that time, not "
                   f"today")
            report = stale(report.value, why, evidence_ids=report.evidence_ids,
                           label=report.label)
            if changes.value:
                changes = stale(changes.value, why, evidence_ids=changes.evidence_ids,
                                label=changes.label)
    fields["report"] = report
    fields["report_changes"] = changes
    if report.value:
        rv = report.value
        _q(qs, "report.version", f"version {rv['version']} ({rv['status']})", "report", report,
           "the latest stored stock intelligence report: version number and completeness "
           "status")
        _q(qs, "report.generated", f"{(rv['generated_at'] or '')[:16].replace('T', ' ')} UTC",
           "report", report, "when that report was generated")
        if rv.get("generated_at"):
            r_age = (now - _naive_utc(datetime.fromisoformat(rv["generated_at"]))).days
            _q(qs, "report.age", f"{r_age} calendar days", "report", report,
               "how long before today that report was generated")
    if changes.value:
        labels = _report_labels(cur)
        for i, c in enumerate(changes.value["changed"][:12], 1):
            # store.diff() reports the field VALUES in from/to, not the field records.
            _q(qs, f"report_change.{i}", _change_line(c, labels), "report_changes", changes,
               "one field that changed between the previous and the latest report version")
        if not changes.value["changed"]:
            _q(qs, "report_change.none", changes.value.get("note") or "no field changed",
               "report_changes", changes)

    news, headlines = _headlines(session, symbol, now, book)
    fields["headlines"] = news
    for h in headlines:
        title = WITHHELD_TITLE if h["withheld"] else f"\u201c{h['title']}\u201d"
        qs[h["key"]] = Quantity(
            key=h["key"], field="headlines", state=FieldState.OK.value,
            display=f"{title} ({h['source']}, {h['published_at'][:10]})",
            meaning=(_WITHHELD_MEANING if h["withheld"]
                     else "a third-party headline; unverified by this platform"))

    plan = None
    if plan_request is not None and getattr(plan_request, "kind", None):
        calls = _calls(session, stock.id, now)
        cohort = _outcomes(session, list(calls), now)
        pr = plan_request
        pos = None
        if any(getattr(pr, k, None) is not None for k in
               ("entry", "shares", "risk_amount", "account", "risk_pct", "stop")):
            pos = P.Position(entry=pr.entry, shares=pr.shares, risk_amount=pr.risk_amount,
                             account=pr.account, risk_pct=pr.risk_pct,
                             stop=getattr(pr, "stop", None))
        plan = P.build_plan(
            kind=pr.kind, bars_desc=bars, calls=calls, cohort_rows=cohort,
            stock_rows=[r for r in cohort if r.symbol == symbol], symbol=symbol, currency=ccy,
            now=now, book=book, record_bar=A.record_bar,
            earnings=earn.value if earn.state is FieldState.OK else None,
            price_stale=price.reason if price.state is FieldState.STALE else None,
            position=pos, position_refusals=list(getattr(pr, "refusals", []) or []),
            unsupported=bool(getattr(pr, "unsupported", False)))
        _plan_fields(plan, fields, qs)

    # A citation that resolves to nothing looks checkable and is not — refuse to build one.
    validate_evidence(fields, book)

    body = {"v": PACKET_VERSION, "symbol": symbol,
            "fields": {k: {"state": f.state.value, "value": f.value, "reason": f.reason}
                       for k, f in sorted(fields.items())}}
    fp = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:24]
    market = stock.market.value if hasattr(stock.market, "value") else str(stock.market)
    return CopilotPacket(symbol=symbol, subject_key=f"stock:{symbol}", market=market,
                         currency=ccy, built_at=now, fields=fields, book=book, quantities=qs,
                         headlines=headlines, fingerprint=fp, plan=plan)
