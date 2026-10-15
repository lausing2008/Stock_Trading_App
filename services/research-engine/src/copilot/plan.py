"""Plan mode: the platform's calls with their track records, and a textbook if→then playbook.

Design: docs/features/2026-10-14-copilot-plan-mode-design.md. Operator decisions (2026-10-14):
conditional rules with code-calculated levels plus the platform's own calls and their measured
record; never a flat "buy now"; the position is TYPED by the user and nothing is read from
accounts; the stock page's Game Plan is rebuilt on these same rules.

NO MODEL IN HERE. Every level, every rule sentence and every record line is computed and worded
by this module. The copilot cites them by key ({{rule.e1}}); the Game Plan card renders them as
they are. A model can choose which rules to mention and in what order — it cannot change a
condition or a number, because it never writes digits.

WHAT THIS IS NOT. The platform stores no price forecast. "Next week" is answered with:
  - the signal engine's latest CALL per horizon (a direction, not a target),
  - how past calls of that kind TURNED OUT (with the count, the date span and the evidence
    status — measured 2026-10-14: none of the resolved outcomes is verified),
  - the range this stock's OWN past moves covered (history, not a forecast).

RULE PARAMETERS ARE TEXTBOOK CONVENTIONS, NOT TUNED. They are fixed below and documented as not
fitted to this platform's data — fitting them needs walk-forward machinery and outcome volume
the platform does not yet have.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timedelta, timezone

from intelligence.report_contract import Evidence, EvidenceBook

# ── Textbook parameters (NOT tuned on platform data) ─────────────────────────
ATR_BARS = 14
#: Bars fed to the Wilder ATR. Wilder smoothing converges within a few multiples of 14; every
#: input bar is cited.
ATR_INPUT_BARS = 100
STRUCTURE_BARS = 20              # 20-bar low / high
YEAR_BARS = 252                  # the 252-bar high / low ("52-week" in calendar terms)
MIN_YEAR_BARS = 200
STOP_BELOW_SUPPORT_ATR = 0.5     # pullback stop = 20-bar low - 0.5 x ATR
BREAKOUT_STOP_ATR = 1.0          # breakout stop = 20-bar high - 1 x ATR
ENTRY_STOP_ATR = 2.0             # initial stop from entry = entry - 2 x ATR (paper engine default)
TRAIL_ATR = 2.0                  # trailing stop = latest close - 2 x ATR
BREAKEVEN_ATR = 1.0              # move the stop to entry once a close is 1 x ATR above it
MIN_REWARD_RISK = 2.0
EARNINGS_CAUTION_DAYS = 10
BREAKOUT_VOLUME_MULT = 1.5
SLOPE_LAG_BARS = 5               # SMA50 now vs SMA50 five bars earlier
#: A single-bar move larger than this, in unadjusted closes, is probably a split or another
#: corporate action: every level computed across it is suspect and marked STALE.
JUMP_LIMIT = 0.35
MOVE_WINDOWS = (5, 10)           # past N-bar moves of THIS stock
MOVE_LOOKBACK_BARS = 250
MIN_MOVE_BARS = 120

# ── Track record ──────────────────────────────────────────────────────────────
RECORD_LOOKBACK_DAYS = 180
RECORD_WINDOWS = (5, 10)         # calendar days after the entry close (outcome table's unit)
#: The cohort floor `/signals/outcomes/summary` already uses for "reliable".
COHORT_MIN_N = 30
#: The per-symbol floor the signal email's badge uses.
STOCK_MIN_N = 8
#: The resolver's own definition of a correct call (signals_shared._OUTCOME_WIN_HURDLE_PCT).
WIN_HURDLE = 0.005
HORIZONS = ("SHORT", "SWING", "LONG", "GROWTH")
MAX_SIGNAL_AGE_HOURS = 36

#: Bounds on a typed entry relative to the latest close. Outside them the figure is more likely
#: a typo or a pre-split price than a real cost basis, and every holding rule would be wrong.
ENTRY_MIN_RATIO, ENTRY_MAX_RATIO = 0.2, 5.0

_CCY_PREFIX = {"USD": "$", "HKD": "HK$", "CNY": "CN¥", "EUR": "€", "GBP": "£", "JPY": "¥"}


def money(v: float | None, ccy: str | None) -> str:
    if v is None:
        return "n/a"
    prefix = _CCY_PREFIX.get((ccy or "").upper())
    if v < 0:
        return "-" + money(-v, ccy)
    return f"{prefix}{v:,.2f}" if prefix else f"{v:,.2f} {ccy or ''}".strip()


def pct(v: float, digits: int = 2) -> str:
    """A FRACTION rendered as a signed percent."""
    return f"{v * 100:+.{digits}f}%"


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


# ── Inputs ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Position:
    """What the user TYPED. Never read from an account."""
    entry: float | None = None
    shares: int | None = None
    risk_amount: float | None = None
    account: float | None = None
    risk_pct: float | None = None

    @property
    def risk_budget(self) -> float | None:
        if self.risk_amount is not None:
            return self.risk_amount
        if self.account is not None and self.risk_pct is not None:
            return self.account * self.risk_pct / 100.0
        return None


@dataclass(frozen=True)
class Call:
    horizon: str
    signal: str
    confidence: float | None
    ts: datetime
    row_id: int


@dataclass(frozen=True)
class OutcomeRow:
    row_id: int
    symbol: str
    horizon: str
    direction: str
    window_days: int
    signal_date: date
    pct_return: float            # FRACTION, raw price move (not sign-corrected for SELL)
    is_correct: bool | None
    evidence_status: str | None


# ── Output ────────────────────────────────────────────────────────────────────

@dataclass
class Item:
    """One citable line. `display` is the ONLY text that reaches the user for it."""
    key: str
    display: str
    meaning: str
    group: str                   # levels | moves | calls | records | rules | position
    stale: str | None = None     # reason, when STALE
    evidence_ids: tuple = ()


@dataclass
class Plan:
    kind: str                                      # forecast | entry | holding
    currency: str | None
    as_of: str | None
    items: dict[str, Item] = dc_field(default_factory=dict)
    #: group -> reason, for whatever could not be built. Deterministic limitations.
    unavailable: dict[str, str] = dc_field(default_factory=dict)
    position: Position | None = None
    position_refusals: list[str] = dc_field(default_factory=list)

    def add(self, item: Item) -> None:
        self.items[item.key] = item

    def to_json(self) -> dict:
        """The Game Plan card's payload: same lines the copilot cites, no model involved."""
        groups: dict[str, list] = {}
        for it in self.items.values():
            groups.setdefault(it.group, []).append(
                {"key": it.key, "display": it.display, "meaning": it.meaning,
                 "state": "STALE" if it.stale else "OK", "note": it.stale,
                 "evidence_ids": list(it.evidence_ids)})
        return {"kind": self.kind, "currency": self.currency, "price_as_of": self.as_of,
                "position": (None if self.position is None else
                             {k: getattr(self.position, k) for k in
                              ("entry", "shares", "risk_amount", "account", "risk_pct")}),
                "position_refusals": list(self.position_refusals),
                "groups": groups, "unavailable": dict(self.unavailable),
                "parameters": PARAMETERS}


PARAMETERS = {
    "note": "textbook conventions fixed in code; NOT tuned on this platform's data",
    "atr_bars": ATR_BARS, "structure_bars": STRUCTURE_BARS,
    "stop_below_support_atr": STOP_BELOW_SUPPORT_ATR, "breakout_stop_atr": BREAKOUT_STOP_ATR,
    "entry_stop_atr": ENTRY_STOP_ATR, "trail_atr": TRAIL_ATR, "breakeven_atr": BREAKEVEN_ATR,
    "min_reward_risk": MIN_REWARD_RISK, "earnings_caution_days": EARNINGS_CAUTION_DAYS,
    "breakout_volume_mult": BREAKOUT_VOLUME_MULT, "cohort_min_n": COHORT_MIN_N,
    "stock_min_n": STOCK_MIN_N, "record_lookback_days": RECORD_LOOKBACK_DAYS,
    "win_hurdle_pct": WIN_HURDLE * 100,
}


# ── Calculations (pure) ───────────────────────────────────────────────────────

def wilder_atr(bars_desc: list) -> float | None:
    """Wilder's ATR(14) over the given bars (newest first). None below 15 bars."""
    bars = list(reversed(bars_desc))
    if len(bars) < ATR_BARS + 1:
        return None
    trs = []
    for prev, cur in zip(bars, bars[1:]):
        h, l, pc = float(cur.high), float(cur.low), float(prev.close)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[:ATR_BARS]) / ATR_BARS
    for tr in trs[ATR_BARS:]:
        atr = (atr * (ATR_BARS - 1) + tr) / ATR_BARS
    return atr


def largest_jump(bars_desc: list) -> tuple[float, object | None]:
    """The largest single-bar close-to-close move (fraction) and the bar it lands on."""
    worst, at = 0.0, None
    for cur, prev in zip(bars_desc, bars_desc[1:]):
        if float(prev.close) > 0:
            mv = abs(float(cur.close) / float(prev.close) - 1)
            if mv > worst:
                worst, at = mv, cur
    return worst, at


def past_moves(bars_desc: list, n: int) -> dict | None:
    """Deciles of this stock's overlapping n-bar close-to-close moves (fractions)."""
    closes = [float(b.close) for b in reversed(bars_desc[:MOVE_LOOKBACK_BARS])]
    if len(closes) < MIN_MOVE_BARS:
        return None
    rets = [closes[i] / closes[i - n] - 1 for i in range(n, len(closes)) if closes[i - n] > 0]
    if len(rets) < 20:
        return None
    d = statistics.quantiles(rets, n=10, method="inclusive")
    return {"p10": d[0], "p50": statistics.median(rets), "p90": d[8], "n": len(rets)}


def sma(bars_desc: list, n: int, offset: int = 0) -> float | None:
    seg = bars_desc[offset:offset + n]
    if len(seg) < n:
        return None
    return sum(float(b.close) for b in seg) / n


@dataclass
class Levels:
    close: float
    atr: float
    low20: float
    high20: float
    avg_volume20: float
    sma50: float | None
    sma50_prev: float | None
    high252: float | None
    low252: float | None

    @property
    def pullback_stop(self) -> float:
        return self.low20 - STOP_BELOW_SUPPORT_ATR * self.atr

    @property
    def breakout_stop(self) -> float:
        return self.high20 - BREAKOUT_STOP_ATR * self.atr


def structure_bars(bars_desc: list) -> list:
    """The 20 bars BEFORE the latest one (the Donchian convention).

    Measured in the first probe run: with the latest bar inside the window, the close can never
    be below the 20-bar low or above the 20-bar high, so "support has broken" and "a close
    above the high" could not happen — the breakdown branch was dead code.
    """
    return bars_desc[1:STRUCTURE_BARS + 1]


def levels(bars_desc: list) -> Levels | None:
    if len(bars_desc) < max(STRUCTURE_BARS + 1, ATR_BARS + 1):
        return None
    atr = wilder_atr(bars_desc[:ATR_INPUT_BARS])
    if atr is None or atr <= 0:
        return None
    s = structure_bars(bars_desc)
    yr = bars_desc[:YEAR_BARS]
    has_year = len(bars_desc) >= MIN_YEAR_BARS
    return Levels(
        close=float(bars_desc[0].close), atr=atr,
        low20=min(float(b.low) for b in s), high20=max(float(b.high) for b in s),
        avg_volume20=sum(float(b.volume) for b in s) / len(s),
        sma50=sma(bars_desc, 50), sma50_prev=sma(bars_desc, 50, SLOPE_LAG_BARS),
        high252=max(float(b.high) for b in yr) if has_year else None,
        low252=min(float(b.low) for b in yr) if has_year else None)


@dataclass
class Record:
    n: int
    hits: int
    judged: int                   # rows with is_correct set
    median: float
    mean: float
    first: date
    last: date
    statuses: dict
    up_share: float               # share of rows above +hurdle (raw move)
    down_share: float
    row_ids: tuple = ()

    @property
    def hit_rate(self) -> float | None:
        return self.hits / self.judged if self.judged else None


def summarise(rows: list[OutcomeRow], keep_ids: bool = False) -> Record | None:
    rows = [r for r in rows if r.pct_return is not None and math.isfinite(r.pct_return)]
    if not rows:
        return None
    rets = [r.pct_return for r in rows]
    judged = [r for r in rows if r.is_correct is not None]
    statuses: dict[str, int] = {}
    for r in rows:
        k = r.evidence_status or "none"
        statuses[k] = statuses.get(k, 0) + 1
    return Record(n=len(rows), hits=sum(1 for r in judged if r.is_correct), judged=len(judged),
                  median=statistics.median(rets), mean=sum(rets) / len(rets),
                  first=min(r.signal_date for r in rows), last=max(r.signal_date for r in rows),
                  statuses=statuses,
                  up_share=sum(1 for x in rets if x > WIN_HURDLE) / len(rets),
                  down_share=sum(1 for x in rets if x < -WIN_HURDLE) / len(rets),
                  row_ids=tuple(r.row_id for r in rows) if keep_ids else ())


def status_suffix(statuses: dict) -> str:
    """Computed, never hard-coded: today it reads 'none verified'."""
    v = statuses.get("verified", 0)
    total = sum(statuses.values())
    if v == 0:
        return "none verified"
    if v == total:
        return "all verified"
    return f"{v} of {total} verified"


# ── Building the plan ─────────────────────────────────────────────────────────

_INHERIT = object()


class _Ctx:
    def __init__(self, plan: Plan, book: EvidenceBook, ccy: str | None, now: datetime,
                 stale_reason: str | None):
        self.plan, self.book, self.ccy, self.now, self.stale = plan, book, ccy, now, stale_reason

    def m(self, v):
        return money(v, self.ccy)

    def add(self, key, display, meaning, group, ev=(), stale=_INHERIT):
        """`stale` defaults to the PRICE's staleness (levels and rules are computed from the
        bars). Calls and records carry their own, so they pass it explicitly — None = fresh."""
        self.plan.add(Item(key=key, display=display, meaning=meaning, group=group,
                           stale=self.stale if stale is _INHERIT else stale,
                           # A rule built from two levels shares their input bars; cite each once.
                           evidence_ids=tuple(dict.fromkeys(ev))))


def _calc(ctx: _Ctx, name: str, inputs: list[str], result, method: str, period: str) -> str:
    return ctx.book.add(Evidence(
        evidence_id=f"plan:{name}:{period}", source="copilot plan calculation",
        value={"method": method, "inputs": inputs, "result": result},
        basis="unadjusted closes", observed_period=period,
        retrieved_at=_naive(datetime.now(timezone.utc)),
        note="textbook rule parameter values are fixed in code and not tuned on platform data"))


def build_plan(*, kind: str, bars_desc: list, calls: dict[str, Call | None],
               cohort_rows: list[OutcomeRow], stock_rows: list[OutcomeRow], symbol: str,
               currency: str | None, now: datetime, book: EvidenceBook, record_bar,
               earnings: dict | None, price_stale: str | None, position: Position | None = None,
               position_refusals: list[str] | None = None) -> Plan:
    """All plan lines for one symbol. `record_bar` mints a bar's evidence id (shared adapter)."""
    now = _naive(now)
    plan = Plan(kind=kind, currency=currency,
                as_of=bars_desc[0].ts.isoformat() if bars_desc else None,
                position_refusals=list(position_refusals or []))
    jump, jump_bar = largest_jump(bars_desc[:YEAR_BARS])
    stale = price_stale
    if jump > JUMP_LIMIT and jump_bar is not None:
        stale = ((stale + "; ") if stale else "") + (
            f"a {jump * 100:.0f}% single-bar move on {jump_bar.ts:%Y-%m-%d} in unadjusted closes "
            f"suggests a split or other corporate action; levels spanning it may be wrong")
    ctx = _Ctx(plan, book, currency, now, stale)

    lv = levels(bars_desc)
    lv_ids: dict[str, list[str]] = {}
    if lv is None:
        plan.unavailable["levels"] = (f"fewer than {max(STRUCTURE_BARS + 1, ATR_BARS + 1)} daily bars "
                                      f"stored, so no levels or rules can be computed")
    else:
        _level_items(ctx, lv, bars_desc, record_bar, lv_ids)
    _move_items(ctx, bars_desc, record_bar)
    _call_items(ctx, calls, cohort_rows, stock_rows, symbol)

    if kind in ("entry", "holding") and lv is not None:
        _entry_rules(ctx, lv, lv_ids, earnings, position)
        if kind == "holding":
            if position is not None and position.entry is not None:
                ratio = position.entry / lv.close if lv.close > 0 else 0
                if not (ENTRY_MIN_RATIO <= ratio <= ENTRY_MAX_RATIO):
                    plan.position_refusals.append(
                        f"the entry price you typed ({ctx.m(position.entry)}) is far from the "
                        f"latest close ({ctx.m(lv.close)}); a typo or a pre-split price would make "
                        f"every holding rule wrong, so none were built")
                    position = Position(shares=position.shares,
                                        risk_amount=position.risk_amount,
                                        account=position.account, risk_pct=position.risk_pct)
            if position is not None and position.entry is not None:
                plan.position = position
                _holding_rules(ctx, lv, lv_ids, earnings, position, calls)
            else:
                plan.unavailable["holding_rules"] = (
                    "no usable entry price was stated in the question, so the rules for a held "
                    "position were not built; say e.g. 'I bought 100 shares at 182.50'")
        elif position is not None:
            plan.position = position
    return plan


def _level_items(ctx: _Ctx, lv: Levels, bars: list, record_bar, ids: dict) -> None:
    m = ctx.m
    d0 = f"{bars[0].ts:%Y-%m-%d}"
    s_ids = [record_bar(ctx.book, b) for b in structure_bars(bars)]
    a_ids = [record_bar(ctx.book, b) for b in bars[:ATR_INPUT_BARS]]
    atr_id = _calc(ctx, "atr14", a_ids, lv.atr,
                   "Wilder ATR(14): true range = max(high-low, |high-prev close|, |low-prev "
                   "close|); first value the mean of 14, then (prev x 13 + TR) / 14", d0)
    lo_id = _calc(ctx, "low20", s_ids, lv.low20,
                  "lowest low of the 20 bars before the latest one", d0)
    hi_id = _calc(ctx, "high20", s_ids, lv.high20,
                  "highest high of the 20 bars before the latest one", d0)
    vol_id = _calc(ctx, "vol20", s_ids, lv.avg_volume20,
                   "mean volume of the 20 bars before the latest one", d0)
    ids.update(atr=[atr_id, *a_ids], low=[lo_id, *s_ids], high=[hi_id, *s_ids],
               vol=[vol_id, *s_ids])
    ctx.add("level.close", m(lv.close), f"latest daily close ({bars[0].ts:%Y-%m-%d})", "levels",
            [record_bar(ctx.book, bars[0])])
    ctx.add("level.atr", f"{m(lv.atr)} (14-bar ATR, {lv.atr / lv.close * 100:.1f}% of the close)",
            "average true range over 14 bars (Wilder): a typical day's range, used to set stop "
            "distances", "levels", ids["atr"])
    ctx.add("level.low20", m(lv.low20), "lowest low of the 20 stored daily bars before the "
            "latest one (a structural support level, not a judgement that it will hold)", "levels",
            ids["low"])
    ctx.add("level.high20", m(lv.high20), "highest high of the 20 stored daily bars before the "
            "latest one (a structural resistance level)", "levels", ids["high"])
    if lv.sma50 is not None:
        sm_ids = [record_bar(ctx.book, b) for b in bars[:50 + SLOPE_LAG_BARS]]
        sid = _calc(ctx, "sma50", sm_ids, [lv.sma50, lv.sma50_prev],
                    "mean of the 50 most recent closes, and the same 5 bars earlier", d0)
        ids["sma50"] = [sid, *sm_ids]
        slope = ""
        if lv.sma50_prev is not None:
            slope = (" rising" if lv.sma50 > lv.sma50_prev else
                     " falling" if lv.sma50 < lv.sma50_prev else " flat")
            slope = f",{slope} from {m(lv.sma50_prev)} five bars earlier"
        ctx.add("level.sma50", f"{m(lv.sma50)}{slope}", "50-bar simple moving average of "
                "unadjusted closes and its direction", "levels", ids["sma50"])
    if lv.high252 is not None:
        y_ids = [record_bar(ctx.book, b) for b in bars[:YEAR_BARS]]
        yid = _calc(ctx, "range252", y_ids, [lv.low252, lv.high252],
                    "lowest low and highest high of up to 252 most recent bars", d0)
        ids["year"] = [yid, *y_ids]
        ctx.add("level.high252", m(lv.high252), "highest high of the last 252 stored daily bars "
                "(about a year)", "levels", ids["year"])
        ctx.add("level.low252", m(lv.low252), "lowest low of the last 252 stored daily bars",
                "levels", ids["year"])
    else:
        ctx.plan.unavailable["level.high252"] = (
            f"fewer than {MIN_YEAR_BARS} daily bars stored; no year range computed")


def _move_items(ctx: _Ctx, bars: list, record_bar) -> None:
    got = False
    for n in MOVE_WINDOWS:
        mv = past_moves(bars, n)
        if mv is None:
            continue
        got = True
        used = bars[:MOVE_LOOKBACK_BARS]
        ids = [record_bar(ctx.book, b) for b in used]
        cid = _calc(ctx, f"moves{n}b", ids, mv,
                    f"deciles of overlapping {n}-bar close-to-close changes", f"{used[0].ts:%Y-%m-%d}")
        ctx.add(f"moves.{n}b",
                f"{pct(mv['p10'], 1)} to {pct(mv['p90'], 1)} in 8 of 10 past {n}-bar spans "
                f"(median {pct(mv['p50'], 1)}; {mv['n']} spans, {used[-1].ts:%Y-%m-%d} to "
                f"{used[0].ts:%Y-%m-%d})",
                f"the range this stock's OWN past {n}-bar moves covered (10th to 90th "
                f"percentile, unadjusted closes). History, not a forecast: it says how far the "
                f"stock has typically moved, not where it will go", "moves", [cid, *ids])
    if not got:
        ctx.plan.unavailable["moves"] = (f"fewer than {MIN_MOVE_BARS} daily bars stored; the "
                                         f"stock's past move ranges were not computed")


def _record_line(r: Record, direction: str, window: int) -> str:
    # Callers prefix the horizon and direction. Measured (tuned eval, MU): without them the
    # SWING and LONG SELL records rendered as two IDENTICAL lines in one answer.
    verb = "higher" if direction == "BUY" else "lower"
    rate = r.hit_rate
    rate_s = f"{rate * 100:.0f}%" if rate is not None else "n/a"
    return (f"{verb} by more than {WIN_HURDLE * 100:.1f}% after {window} calendar days in "
            f"{rate_s} of {r.judged} past {direction} calls (median move {pct(r.median, 1)}, mean "
            f"{pct(r.mean, 1)}; signals {r.first:%Y-%m-%d} to {r.last:%Y-%m-%d}; "
            f"{status_suffix(r.statuses)})")


def _call_items(ctx: _Ctx, calls: dict, cohort: list[OutcomeRow], stock: list[OutcomeRow],
                symbol: str) -> None:
    any_call = False
    for h in HORIZONS:
        c = calls.get(h)
        hk = h.lower()
        if c is None:
            ctx.plan.unavailable[f"call.{hk}"] = f"no {h} signal stored for this symbol"
            continue
        any_call = True
        age_h = (ctx.now - _naive(c.ts)).total_seconds() / 3600
        c_stale = (f"this {h} call is {age_h:.0f}h old" if age_h > MAX_SIGNAL_AGE_HOURS else None)
        cid = ctx.book.add(Evidence(
            evidence_id=f"signal:{c.row_id}", source=f"signals:{c.row_id}",
            value={"signal": c.signal, "horizon": h, "confidence": c.confidence,
                   "ts": c.ts.isoformat()},
            observed_period=f"{c.ts:%Y-%m-%d}", published_at=_naive(c.ts),
            first_available_at=_naive(c.ts), retrieved_at=_naive(datetime.now(timezone.utc))))
        strength = (f", strength {c.confidence:.0f}/100 engine score"
                    if c.confidence is not None else "")
        ctx.add(f"call.{hk}", f"{c.signal} ({h}, generated {c.ts:%Y-%m-%d %H:%M} UTC{strength})",
                f"the signal engine's latest {h} call: a direction, not a price target. The "
                f"strength is an engine score, not a probability", "calls", [cid],
                stale=c_stale)
        direction = c.signal if c.signal in ("BUY", "SELL") else None
        for w in RECORD_WINDOWS:
            base = summarise([r for r in cohort if r.horizon == h and r.window_days == w])
            if base is not None and base.n >= COHORT_MIN_N:
                bid = ctx.book.add(Evidence(
                    evidence_id=f"outcomes:{h}:all:{w}:{ctx.now:%Y-%m-%d}",
                    source="signal_outcome_horizons (resolved, both directions)",
                    value={"horizon": h, "window_days": w, "n": base.n,
                           "up_share": base.up_share, "down_share": base.down_share,
                           "median": base.median, "statuses": base.statuses},
                    observed_period=f"{base.first}..{base.last}",
                    retrieved_at=_naive(datetime.now(timezone.utc))))
                ctx.add(f"baseline.{hk}.{w}d",
                        f"{base.up_share * 100:.0f}% of {base.n} past {h} calls of either "
                        f"direction were followed by a rise of more than "
                        f"{WIN_HURDLE * 100:.1f}% after {w} calendar days (median move "
                        f"{pct(base.median, 1)}; {status_suffix(base.statuses)})",
                        f"a rough baseline for the period: how often stocks the engine had an "
                        f"opinion on went up at all. Not a market benchmark", "records", [bid],
                        stale=None)
            if direction is None:
                continue
            coh = summarise([r for r in cohort if r.horizon == h and r.window_days == w
                             and r.direction == direction])
            if coh is None or coh.n < COHORT_MIN_N:
                n = coh.n if coh else 0
                ctx.add(f"record.{hk}.{w}d",
                        f"too few resolved {direction} calls on the {h} horizon to judge "
                        f"({n} resolved; at least {COHORT_MIN_N} needed)",
                        "the cohort track record could not be stated", "records", stale=None)
            else:
                eid = ctx.book.add(Evidence(
                    evidence_id=f"outcomes:{h}:{direction}:{w}:{ctx.now:%Y-%m-%d}",
                    source="signal_outcome_horizons (resolved)",
                    value={"horizon": h, "direction": direction, "window_days": w,
                           "n": coh.n, "judged": coh.judged, "hits": coh.hits,
                           "median": coh.median, "mean": coh.mean, "statuses": coh.statuses,
                           "lookback_days": RECORD_LOOKBACK_DAYS},
                    observed_period=f"{coh.first}..{coh.last}",
                    retrieved_at=_naive(datetime.now(timezone.utc)),
                    note="raw price move from the close after the signal; not excess return "
                         "over a benchmark"))
                ctx.add(f"record.{hk}.{w}d", f"all stocks, {h} {direction}: {_record_line(coh, direction, w)}",
                        f"how past {direction} calls on the {h} horizon turned out across every "
                        f"stock: measured from the close after the signal, raw price move, no "
                        f"benchmark adjustment", "records", [eid], stale=None)
            own = summarise([r for r in stock if r.horizon == h and r.window_days == w
                             and r.direction == direction], keep_ids=True)
            n_own = own.n if own else 0
            if own is None or n_own < STOCK_MIN_N:
                ctx.add(f"record.{hk}.{w}d.stock",
                        f"{symbol}: {n_own} resolved {direction} calls on the {h} horizon, too "
                        f"few to judge this stock alone (at least {STOCK_MIN_N} needed)",
                        "this stock's own track record could not be stated", "records",
                        stale=None)
            else:
                oid = ctx.book.add(Evidence(
                    evidence_id=f"outcomes:{symbol}:{h}:{direction}:{w}:{ctx.now:%Y-%m-%d}",
                    source="signal_outcome_horizons (resolved)",
                    value={"symbol": symbol, "rows": list(own.row_ids), "n": own.n,
                           "hits": own.hits, "median": own.median},
                    observed_period=f"{own.first}..{own.last}",
                    retrieved_at=_naive(datetime.now(timezone.utc))))
                ctx.add(f"record.{hk}.{w}d.stock",
                        f"{symbol}, {h} {direction}: {_record_line(own, direction, w)}",
                        f"how past {direction} calls on the {h} horizon turned out for this "
                        f"stock alone", "records", [oid], stale=None)
    if not any_call:
        ctx.plan.unavailable["calls"] = "no signal of any horizon is stored for this symbol"


def _earnings_line(ctx: _Ctx, earnings: dict | None, holding: bool) -> tuple[str, str] | None:
    if not earnings:
        return None
    d, days = earnings["report_date"], earnings["calendar_days_away"]
    if days <= EARNINGS_CAUTION_DAYS:
        if holding:
            return ("rule.h7", f"Earnings are due on {d}, {days} calendar days away. Textbook "
                    f"practice for a held position is to decide BEFORE the release whether to "
                    f"reduce it or to accept the gap risk knowingly: a gap can jump past a stop, "
                    f"so the stop does not cap the loss.")
        return ("rule.e5", f"Earnings are due on {d}, {days} calendar days away. Textbook "
                f"practice is to wait until after the release or open a smaller position, "
                f"because an earnings gap can jump past a stop.")
    key = "rule.h7" if holding else "rule.e5"
    return (key, f"The next earnings date ({d}) is {days} calendar days away, beyond the "
            f"{EARNINGS_CAUTION_DAYS}-day caution window, so the earnings rule does not apply "
            f"yet.")


def _entry_rules(ctx: _Ctx, lv: Levels, ids: dict, earnings: dict | None,
                 position: Position | None) -> None:
    m, P = ctx.m, lv.close
    stop1 = lv.pullback_stop
    ctx.add("rule.e1",
            f"If the price pulls back toward the 20-bar low ({m(lv.low20)}) and a daily close "
            f"holds above it, a textbook pullback entry puts the stop below that low at "
            f"{m(stop1)} (low − {STOP_BELOW_SUPPORT_ATR} × ATR).",
            "textbook pullback-entry rule with its code-calculated stop", "rules",
            ids["low"] + ids["atr"])
    ctx.add("rule.e2",
            f"If a daily close is above the 20-bar high ({m(lv.high20)}) on volume above "
            f"{BREAKOUT_VOLUME_MULT} × its 20-bar average ({lv.avg_volume20:,.0f} shares), a "
            f"textbook breakout entry puts the stop at {m(lv.breakout_stop)} (high − "
            f"{BREAKOUT_STOP_ATR:g} × ATR).",
            "textbook breakout-entry rule with its code-calculated stop", "rules",
            ids["high"] + ids["atr"] + ids["vol"])
    if P <= stop1:
        e3 = (f"The latest close ({m(P)}) is already below the pullback stop level ({m(stop1)}): "
              f"the 20-bar low has broken, so the pullback setup does not apply. Textbook "
              f"practice waits for a new base before buying.")
    elif P >= lv.high20:
        e3 = (f"The latest close ({m(P)}) is at or above the 20-bar high, so this structure "
              f"gives no first target above it. That is a breakout case (the breakout rule), "
              f"not a pullback entry.")
    else:
        risk, reward = P - stop1, lv.high20 - P
        rr = reward / risk
        if rr >= MIN_REWARD_RISK:
            e3 = (f"Buying at the latest close ({m(P)}) with the stop at {m(stop1)} risks "
                  f"{m(risk)} per share for {m(reward)} to the 20-bar high: reward:risk "
                  f"{rr:.1f}, which meets the textbook minimum of {MIN_REWARD_RISK:g}.")
        else:
            better = (lv.high20 + MIN_REWARD_RISK * stop1) / (1 + MIN_REWARD_RISK)
            e3 = (f"Buying at the latest close ({m(P)}) with the stop at {m(stop1)} risks "
                  f"{m(risk)} per share for {m(reward)} to the 20-bar high: reward:risk "
                  f"{rr:.1f}, below the textbook minimum of {MIN_REWARD_RISK:g}. The rule says "
                  f"wait: an entry at or below {m(better)} would meet it, or a breakout would "
                  f"change the setup.")
    ctx.add("rule.e3", e3, "textbook reward-to-risk check at the latest close", "rules",
            ids["low"] + ids["high"] + ids["atr"])
    if lv.sma50 is not None and lv.sma50_prev is not None:
        falling = lv.sma50 < lv.sma50_prev
        if P < lv.sma50 and falling:
            e4 = (f"The latest close ({m(P)}) is below a falling 50-bar average ({m(lv.sma50)}, "
                  f"down from {m(lv.sma50_prev)} five bars earlier): textbook trend-following "
                  f"rules do not open a new long here.")
        elif P < lv.sma50:
            e4 = (f"The latest close ({m(P)}) is below the 50-bar average ({m(lv.sma50)}), which "
                  f"is not falling: the trend filter is a caution, not a block.")
        else:
            e4 = (f"The latest close ({m(P)}) is at or above the 50-bar average ({m(lv.sma50)}): "
                  f"the trend filter does not block an entry.")
        ctx.add("rule.e4", e4, "textbook trend filter", "rules", ids["sma50"])
    else:
        ctx.plan.unavailable["rule.e4"] = "fewer than 55 daily bars stored; no trend filter"
    el = _earnings_line(ctx, earnings, holding=False)
    if el:
        ctx.add(el[0], el[1], "textbook earnings rule", "rules", ())
    else:
        ctx.plan.unavailable["rule.e5"] = "no scheduled earnings date on file"
    # E6 sizing — at the latest close with the pullback stop.
    if P > stop1:
        per = P - stop1
        budget = position.risk_budget if position is not None else None
        if budget is not None:
            shares = math.floor(budget / per)
            account = position.account
            if account is not None and shares * P > account:
                shares = math.floor(account / P)
                cap = f", capped at what the stated account of {m(account)} can buy"
            else:
                cap = ""
            if shares <= 0:
                e6 = (f"Risking {m(budget)} with the stop at {m(stop1)} ({m(per)} per share) is "
                      f"not enough for one share.")
            else:
                e6 = (f"Risking {m(budget)} with the stop at {m(stop1)} ({m(per)} per share at "
                      f"the latest close) sizes the position at {shares:,} shares "
                      f"({m(shares * P)}){cap}.")
        else:
            e6 = (f"Each 100 shares bought at the latest close ({m(P)}) would lose {m(100 * per)} "
                  f"if the stop at {m(stop1)} is hit. State a risk budget (e.g. 'risk $500') to "
                  f"size the position.")
        ctx.add("rule.e6", e6, "textbook position sizing from a fixed risk to the stop", "rules",
                ids["low"] + ids["atr"])


def _gain(ctx: _Ctx, per_share: float, shares: int | None) -> str:
    word = "gain" if per_share >= 0 else "loss"
    s = f"a {word} of {ctx.m(abs(per_share))} per share"
    if shares:
        s += f" ({ctx.m(abs(per_share) * shares)} on {shares:,} shares)"
    return s


def _holding_rules(ctx: _Ctx, lv: Levels, ids: dict, earnings: dict | None, pos: Position,
                   calls: dict) -> None:
    m, P, E, A, Q = ctx.m, lv.close, pos.entry, lv.atr, pos.shares
    pid = ctx.book.add(Evidence(
        evidence_id=f"position:typed:{ctx.now:%Y%m%dT%H%M%S}", source="stated by you in this "
        "question", value={"entry": E, "shares": Q}, retrieved_at=ctx.now,
        note="typed by the user; not read from any account or portfolio"))
    ctx.add("position.entry", m(E), "the entry price the user typed", "position", [pid])
    if Q:
        ctx.add("position.shares", f"{Q:,} shares", "the share count the user typed", "position",
                [pid])
    base = [pid] + ids["atr"]
    ctx.add("rule.h1",
            f"At the latest close ({m(P)}) the position shows {_gain(ctx, P - E, Q)}, "
            f"{(P - E) / E * 100:+.1f}% from your entry ({m(E)}), which is "
            f"{(P - E) / A:+.1f} ATRs.", "where the typed position stands at the latest close",
            "rules", base)
    s_atr, s_sup = E - ENTRY_STOP_ATR * A, lv.pullback_stop
    tight = max(s_atr, s_sup)
    which = "entry-based" if s_atr >= s_sup else "structure-based"
    h2 = (f"Two textbook stops: entry − {ENTRY_STOP_ATR:g} × ATR = {m(s_atr)}, and below the "
          f"20-bar low = {m(s_sup)}. The higher one ({m(tight)}, {which}) is the tighter stop; "
          f"at it the position would show {_gain(ctx, tight - E, Q)}.")
    if P <= tight:
        h2 += f" The latest close is already at or below {m(tight)}: by this rule the stop has been reached."
    ctx.add("rule.h2", h2, "textbook protective stops for the typed position", "rules",
            base + ids["low"])
    active = tight
    if P - E > BREAKEVEN_ATR * A:
        active = max(active, E)
        h3 = (f"The latest close is more than {BREAKEVEN_ATR:g} ATR ({m(A)}) above your entry, so "
              f"a textbook rule moves the stop up to at least your entry ({m(E)}).")
    else:
        h3 = (f"The latest close is not yet {BREAKEVEN_ATR:g} ATR ({m(A)}) above your entry; the "
              f"breakeven rule applies once a close is above {m(E + BREAKEVEN_ATR * A)}.")
    ctx.add("rule.h3", h3, "textbook breakeven-stop rule", "rules", base)
    trail = P - TRAIL_ATR * A
    if trail > active:
        h4 = (f"A trailing stop at the latest close − {TRAIL_ATR:g} × ATR is {m(trail)}, above "
              f"the current stop ({m(active)}), so a textbook trailing rule raises the stop to "
              f"{m(trail)}.")
        active = trail
    else:
        h4 = (f"A trailing stop at the latest close − {TRAIL_ATR:g} × ATR is {m(trail)}, not above "
              f"the current stop ({m(active)}); a stop never moves down, so it stays at "
              f"{m(active)}.")
    ctx.add("rule.h4", h4, "textbook trailing-stop rule", "rules", base)
    if P < lv.high20:
        h5 = (f"If the price reaches the 20-bar high ({m(lv.high20)}), a textbook rule takes "
              f"partial profit there and raises the stop.")
        if lv.high252 is not None and lv.high252 > lv.high20:
            h5 += f" The 252-bar high ({m(lv.high252)}) is the next stored level above it."
        ev = base + ids["high"] + ids.get("year", [])
    elif lv.high252 is not None and P < lv.high252:
        h5 = (f"The latest close is at or above the 20-bar high; the next stored level above is "
              f"the 252-bar high ({m(lv.high252)}), where a textbook rule takes partial profit.")
        ev = base + ids.get("year", [])
    else:
        h5 = ("The latest close is at or above every stored high, so no level lies above it. "
              "Textbook practice trails the stop rather than setting a target.")
        ev = base + ids["high"]
    ctx.add("rule.h5", h5, "textbook profit-taking rule", "rules", ev)
    ctx.add("rule.h6",
            f"If a daily close is below the active stop ({m(active)}), the rule says exit. A stop "
            f"is not moved down to avoid it.", "textbook exit rule", "rules", base + ids["low"])
    el = _earnings_line(ctx, earnings, holding=True)
    if el:
        ctx.add(el[0], el[1], "textbook earnings rule for a held position", "rules", ())
    else:
        ctx.plan.unavailable["rule.h7"] = "no scheduled earnings date on file"
    parts = [f"{h} {calls[h].signal}" for h in HORIZONS if calls.get(h) is not None]
    if parts:
        ctx.add("rule.h8",
                f"The platform's current calls: {', '.join(parts)}. If the call on the horizon "
                f"you trade turns SELL, a textbook rule reviews the position.",
                "the platform's calls, as a review trigger", "rules", ())
