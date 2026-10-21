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
BREAKOUT_RETEST_SESSIONS = 3      # a post-confirmation retest fill must occur within this many sessions
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
    #: The stop the user says they currently hold. The platform keeps no stop state (C1).
    stop: float | None = None

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
    #: The question is about an options or short position (review C2). No stock entry, sizing
    #: or holding rule is built — the calls and records may still be shown.
    unsupported: bool = False
    level_prices: dict[str, dict] = dc_field(default_factory=dict)
    setups: list[dict] = dc_field(default_factory=list)
    #: Raw evidence levels an LLM proposal may anchor to (key -> price), the ATR its offsets are
    #: measured in, and the trend gate the rules apply. Not serialised: proposals are rebuilt
    #: from the same packet by code (copilot/proposal.py).
    anchors: dict[str, float] = dc_field(default_factory=dict)
    atr: float | None = None
    avg_volume20: float | None = None
    trend_blocked: bool | None = None

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
                "setups": [{**s, "state": "stale" if self.items[s["key"]].stale else s["state"],
                            # F04: stale prices give no current guidance, only the reason.
                            **({"playbook": [{"case": "stale", "kind": "wait",
                                              "title": "Prices are stale — no current guidance",
                                              "text": f"This plan's prices are not current "
                                                      f"({self.items[s['key']].stale}). No branch "
                                                      f"applies until the data catches up; refresh "
                                                      f"the plan then."}]}
                               if self.items[s["key"]].stale else {}),
                            "evidence_ids": list(self.items[s["key"]].evidence_ids)} for s in self.setups],
                "position": (None if self.position is None else
                             {k: getattr(self.position, k) for k in
                              ("entry", "shares", "risk_amount", "account", "risk_pct", "stop")}),
                "unsupported": self.unsupported,
                "levels": [{**v, "key": k, "state": "STALE" if self.items[k].stale else "OK",
                            "evidence_ids": list(self.items[k].evidence_ids)}
                           for k, v in self.level_prices.items()],
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


    def level(self, key, price, label, role):
        if math.isfinite(price) and price > 0:
            self.plan.level_prices[key] = {"price": price, "label": label, "role": role}


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
               position_refusals: list[str] | None = None, unsupported: bool = False) -> Plan:
    """All plan lines for one symbol. `record_bar` mints a bar's evidence id (shared adapter)."""
    now = _naive(now)
    plan = Plan(kind=kind, currency=currency,
                as_of=bars_desc[0].ts.isoformat() if bars_desc else None,
                position_refusals=list(position_refusals or []), unsupported=unsupported)
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
        plan.atr, plan.avg_volume20 = lv.atr, lv.avg_volume20
        plan.anchors = {k: v for k, v in (
            ("level.close", lv.close), ("level.low20", lv.low20), ("level.high20", lv.high20),
            ("level.sma50", lv.sma50), ("level.high252", lv.high252), ("level.low252", lv.low252))
            if v is not None}
        plan.trend_blocked = (None if lv.sma50 is None or lv.sma50_prev is None
                              else lv.close < lv.sma50 and lv.sma50 < lv.sma50_prev)
    _move_items(ctx, bars_desc, record_bar)
    _call_items(ctx, calls, cohort_rows, stock_rows, symbol)

    if unsupported and kind in ("entry", "holding"):
        plan.unavailable["rules"] = ("the question is about an options or short position; the "
                                     "playbook's rules are for long stock only, so none were built")
    elif kind in ("entry", "holding") and lv is not None:
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
            if position is not None and position.entry is not None and position.stop is not None:
                if not (0 < position.stop < max(position.entry, lv.close) * ENTRY_MAX_RATIO):
                    plan.position_refusals.append(
                        f"the stop you typed ({ctx.m(position.stop)}) is not usable for a long "
                        f"position; the rules below use today's candidates instead")
                    position = Position(entry=position.entry, shares=position.shares,
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
    if not unsupported and not plan.position_refusals and lv is not None:
        _trade_setups(ctx, lv, lv_ids)
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
    ctx.level("level.low20", lv.low20, "20-bar low", "support")
    ctx.level("level.high20", lv.high20, "20-bar high", "resistance")
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
        ctx.level("level.high252", lv.high252, "Year high", "resistance")
        ctx.level("level.low252", lv.low252, "Year low", "support")
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
    for key, price, label in (("level.pullback_stop", stop1, "Pullback stop candidate"),
                              ("level.breakout_stop", lv.breakout_stop, "Breakout stop candidate")):
        ctx.add(key, m(price), label + "; calculated today, not a maintained stop", "levels",
                ids["low" if key.endswith("pullback_stop") else "high"] + ids["atr"])
        ctx.level(key, price, label, "stop")
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
    # THE STOP CONTRACT (independent review C1, 2026-10-15). The platform keeps no position
    # state between requests, so it cannot know the stop the user is actually holding. Every
    # stop here is a CANDIDATE computed from today's data. "A stop never moves down" is only
    # asserted against a stop the user TYPED ("my stop is 222"); without one, the answer says the
    # current stop is unknown and states the rule for combining the two. The first build said a
    # recalculated (lower) stop was "the active stop" that "never moves down" — measured falling
    # from $226 to $220.50 between two snapshots of the same position.
    s_atr, s_sup = E - ENTRY_STOP_ATR * A, lv.pullback_stop
    initial = max(s_atr, s_sup)
    which = "entry-based" if s_atr >= s_sup else "structure-based"
    ctx.add("rule.h2",
            f"Two textbook initial-stop candidates from today's data: entry − {ENTRY_STOP_ATR:g} × "
            f"ATR = {m(s_atr)}, and below the 20-bar low = {m(s_sup)}. The higher one "
            f"({m(initial)}, {which}) is the tighter; at it the position would show "
            f"{_gain(ctx, initial - E, Q)}.",
            "textbook initial-stop candidates for the typed position (calculated today, not a "
            "stop the platform maintains)", "rules", base + ids["low"])
    candidate = initial
    if P - E > BREAKEVEN_ATR * A:
        candidate = max(candidate, E)
        h3 = (f"The latest close is more than {BREAKEVEN_ATR:g} ATR ({m(A)}) above your entry, so "
              f"a textbook rule raises the stop to at least your entry ({m(E)}).")
    else:
        h3 = (f"The latest close is not yet {BREAKEVEN_ATR:g} ATR ({m(A)}) above your entry; the "
              f"breakeven rule applies once a close is above {m(E + BREAKEVEN_ATR * A)}.")
    ctx.add("rule.h3", h3, "textbook breakeven-stop rule", "rules", base)
    trail = P - TRAIL_ATR * A
    if trail > candidate:
        h4 = (f"Today's trailing candidate (latest close − {TRAIL_ATR:g} × ATR) is {m(trail)}, "
              f"above the other candidates, so today's stop candidate is {m(trail)}.")
        candidate = trail
    else:
        h4 = (f"Today's trailing candidate (latest close − {TRAIL_ATR:g} × ATR) is {m(trail)}, "
              f"not above the other candidates, so today's stop candidate stays {m(candidate)}.")
    ctx.add("rule.h4", h4, "textbook trailing-stop candidate, calculated from today's data",
            "rules", base)
    ctx.add("level.stop_candidate", m(candidate), "Today's holding stop candidate; not maintained",
            "levels", base + ids["low"])
    ctx.level("level.stop_candidate", candidate, "Holding stop candidate", "stop")
    held = pos.stop
    if held is not None:
        sid = ctx.book.add(Evidence(
            evidence_id=f"position:typed-stop:{ctx.now:%Y%m%dT%H%M%S}",
            source="stated by you in this question", value={"stop": held},
            retrieved_at=ctx.now, note="typed by the user; the platform keeps no stop state"))
        ctx.add("position.stop", m(held), "the stop the user typed as currently held",
                "position", [sid])
        ctx.level("position.stop", held, "Your stated stop", "stop")
        active = max(held, candidate)
        if candidate > held:
            h6 = (f"Your stated stop is {m(held)} and today's candidate is {m(candidate)}: "
                  f"textbook practice raises the stop to {m(candidate)}. If a daily close is "
                  f"below {m(active)}, the rule says exit.")
        else:
            h6 = (f"Your stated stop is {m(held)}; today's candidate ({m(candidate)}) is not above "
                  f"it, and a stop is never lowered, so it stays at {m(held)}. If a daily close is "
                  f"below {m(held)}, the rule says exit.")
        if P < active:
            h6 += f" The latest close ({m(P)}) is already below {m(active)}: the exit rule has been met."
        ev6 = base + ids["low"] + [sid]
    else:
        h6 = (f"The platform does not keep your stop between requests, so your current stop is "
              f"unknown. Today's candidate is {m(candidate)}. Textbook practice keeps the higher "
              f"of your existing stop and this candidate and never lowers a stop; if a daily "
              f"close is below the stop you hold, the rule says exit. Tell me your stop (e.g. "
              f"'my stop is 95') to apply this to it.")
        if P < candidate:
            h6 += (f" The latest close ({m(P)}) is already below today's candidate "
                   f"({m(candidate)}).")
        ev6 = base + ids["low"]
    ctx.add("rule.h6", h6, "textbook exit rule against the stop the user holds", "rules", ev6)
    # TARGETS vs the typed cost basis (review C5): a level below the entry is a LOSS reduction,
    # never "partial profit". Measured: entry 104, 20-bar high 101 -> "take partial profit at 101".
    targets = [(lbl, lvl, idl) for lbl, lvl, idl in
               (("20-bar high", lv.high20, ids["high"]),
                ("252-bar high", lv.high252, ids.get("year", [])))
               if lvl is not None and lvl > P]
    targets = [t for i, t in enumerate(targets) if not any(t[1] == u[1] for u in targets[:i])]
    if not targets:
        h5 = ("The latest close is at or above every stored high, so no level lies above it. "
              "Textbook practice trails the stop rather than setting a target.")
        ev = base + ids["high"]
    else:
        parts, ev = [], list(base)
        for lbl, lvl, idl in targets:
            ev += idl
            if lvl > E:
                parts.append(f"at the {lbl} ({m(lvl)}), above your entry, a textbook rule takes "
                             f"partial profit and raises the stop")
            else:
                parts.append(f"the {lbl} ({m(lvl)}) is at or below your entry ({m(E)}): reaching "
                             f"it would at best get back to your cost, not take a profit, so "
                             f"textbook practice reviews the position there as resistance")
        h5 = "If the price rises: " + "; ".join(parts) + "."
    ctx.add("rule.h5", h5, "textbook targets compared with the typed entry", "rules", ev)
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


def _trade_setups(ctx, lv, ids):
    """Explicit reference-price setups. Targets are conditional objectives, not forecasts."""
    plan, pos, m = ctx.plan, ctx.plan.position, ctx.m
    if plan.kind == 'entry':
        specs = [('pullback', 'Pullback entry', lv.low20, lv.pullback_stop, 'rule.e1'),
                 ('breakout', 'Breakout entry', lv.high20, lv.breakout_stop, 'rule.e2')]
    elif plan.kind == 'holding' and pos is not None and 'position.entry' in plan.items:
        candidate = plan.level_prices.get('level.stop_candidate', {}).get('price')
        if candidate is None and pos.stop is None:
            plan.unavailable['setups'] = 'No positive holding stop candidate can be calculated from these inputs.'
            return
        stop = candidate if candidate is not None else pos.stop
        specs = [('holding', 'Held-position management', pos.entry, max(stop, pos.stop or stop), 'rule.h6')]
    else:
        return
    resistance = sorted(set(x for x in (lv.high20, lv.high252) if x is not None and x > 0))
    for name, title, entry, stop, trigger_key in specs:
        held = name == 'holding'
        reasons, targets, needs = [], [], []
        valid = math.isfinite(entry) and math.isfinite(stop) and entry > 0 and stop > 0 and (held or stop < entry)
        per = entry-stop if valid and entry > stop else None
        budget = pos.risk_budget if pos else None
        shares = pos.shares if held and pos else (math.floor(budget/per) if budget is not None and per else None)
        # F05: ONE sizing rule. A stated cash/account amount caps the proposed size here exactly as
        # it does in the narrative (rule.e6); still not a broker buying-power check.
        account = pos.account if pos is not None and not held else None
        capped = False
        if shares is not None and account is not None and valid and entry > 0 and shares * entry > account:
            shares, capped = math.floor(account / entry), True
        state = 'conditional'
        if not valid or (not held and per is None):
            state = 'unavailable'; reasons.append('No positive entry/stop risk distance can be calculated.')
        if valid:
            # Respect nearer resistance. If absent, use explicitly labeled mechanical objectives.
            floor = max(entry, lv.close) if held else entry
            above = [x for x in resistance if x > floor]
            first = above[0] if above else (entry+2*per if per else floor+2*lv.atr)
            if first <= floor:
                first = floor+2*lv.atr
            targets.append({'price': first, 'label': 'Structural resistance' if above else
                            ('2R objective' if per and first == entry+2*per else 'ATR-based objective'),
                            'reward_risk': (first-entry)/per if per else None})
            second = next((x for x in above if x > first), None)
            if second is None:
                second = max(entry+3*per, first+per) if per else max(floor+3*lv.atr, first+lv.atr)
            targets.append({'price': second, 'label': 'Structural resistance' if second in above else
                            'Calculated stretch objective', 'reward_risk': (second-entry)/per if per else None})
            if not held and targets[0]['reward_risk'] + 1e-9 < MIN_REWARD_RISK:
                state = 'blocked'; reasons.append('First target is below the minimum reward-to-risk; wait for a different setup.')
                needs.append(f'a first target at least {MIN_REWARD_RISK:g}R away: an entry at or below '
                             f'{m(_no_chase_limit(targets[0]["price"], stop))} with this stop, or new levels after the 20-bar window rolls')
            if not held and lv.close <= stop and name == 'pullback':
                state = 'blocked'; reasons.append('Latest close is already at or below the pullback stop; wait for a new base.')
                needs.append('a new 20-bar base: daily closes holding above a fresh 20-bar low (the levels recalculate each day)')
            if held and lv.close < stop:
                state = 'exit_triggered'; reasons.append('Latest daily close is already below the assessed stop; the exit rule is met.')
        if not held:
            if lv.sma50 is None or lv.sma50_prev is None:
                state = 'unavailable'; reasons.append('Insufficient history to assess the trend filter.')
            elif lv.close < lv.sma50 and lv.sma50 < lv.sma50_prev:
                state = 'blocked'; reasons.append('Latest close is below a falling 50-bar average; no new long under the trend rule.')
                needs.append(f'the trend filter to clear: a daily close above the 50-bar average ({m(lv.sma50)}) '
                             f'or that average to stop falling')
            if name == 'breakout' and lv.avg_volume20 <= 0:
                state = 'unavailable'; reasons.append('Average volume is unavailable for breakout confirmation.')
            if shares == 0:
                state = 'blocked'
                if capped:
                    reasons.append('The stated account cannot buy one whole share at the entry reference.')
                    needs.append(f'an account amount of at least {m(entry)} (one share at the entry reference)')
                else:
                    reasons.append('The risk budget cannot cover one whole share at this stop distance.')
                    needs.append(f'a risk budget of at least {m(per)} (one share at this stop distance)')
        risk_note = ('Stop gaps, slippage and fees can exceed the calculated loss. Sizing does not check buying power.'
                     if per else 'Stop is at or above cost; original risk and R-multiples are unknown. Gap risk remains.')
        if not valid:
            risk_note = 'Entry/stop inputs are unusable; sizing and reward-to-risk are unavailable.'
        recovery = [{'price': x, 'label': 'Resistance / loss reduction, not profit'} for x in resistance
                    if held and lv.close < x <= entry]
        # F06: the confirmation level is not an acceptable fill. max_fill is the highest fill whose
        # first target is still MIN_REWARD_RISK away with this stop; above it the setup fails.
        max_fill = _no_chase_limit(targets[0]['price'], stop) if valid and per and not held and targets else None
        sizing_note = (f'Capped at what the stated account of {m(account)} can buy at the entry reference; '
                       f'not a broker buying-power check.' if capped else None)
        setup = dict(id=name, key=f'setup.{name}', title=title, entry=entry, stop=stop if valid else None,
            targets=targets, risk_per_share=per, risk_budget=budget, shares=shares, max_fill=max_fill,
            sizing_note=sizing_note, eligibility_needs=needs,
            shares_basis='held' if held else 'risk_budget',
            capital=entry*shares if shares is not None else None,
            cash_at_stop=(stop-entry)*shares if valid and shares is not None else None,
            trigger=plan.items[trigger_key].display if valid else 'No usable long setup; recompute with valid market inputs.',
            state=state, reasons=reasons, risk_note=risk_note,
            invalidation=('If a daily close is below the assessed stop, the exit condition is met.' if held else
                          'No entry until the daily-close rule confirms; after entry, a daily close below the stop invalidates the setup.'),
            earnings=plan.items.get('rule.h7' if held else 'rule.e5').display
                     if ('rule.h7' if held else 'rule.e5') in plan.items else 'Next earnings date is unavailable.',
            recovery_levels=recovery)
        inputs = list(dict.fromkeys(ids['low']+ids['high']+ids['atr']+ids.get('year', [])+
                                    ids.get('sma50', [])+ids['vol']))
        for k in ('position.entry', 'position.stop', 'position.shares'):
            if held and k in plan.items: inputs.extend(plan.items[k].evidence_ids)
        if budget is not None:
            inputs.append(ctx.book.add(Evidence(evidence_id=f'position:risk-budget:{name}',
                source='stated position risk budget', value={'risk_budget': budget, 'currency': plan.currency},
                retrieved_at=ctx.now, note='Typed cash or account × risk percentage; not broker buying power.')))
        eid = _calc(ctx, f'setup_{name}', inputs, setup,
                    'Entry and stop from existing structural/ATR rules; first target is nearest higher resistance '
                    'or a 2R objective, second is next resistance or a stretch objective. '
                    'R=(target-entry)/(entry-stop) only for positive risk; whole-share sizing=floor(budget/risk).',
                    str(plan.as_of))
        target_line = '; '.join(f'take profit {i+1}: {m(t["price"])} ({t["label"]}, '
                               + (f'{t["reward_risk"]:.2f}R)' if t['reward_risk'] is not None else 'R unknown)')
                               for i,t in enumerate(targets)) or 'targets unavailable'
        display = (f'{title}: {"original entry cost" if held else "entry reference"} {m(entry)}; '
                   f'stop {m(setup["stop"])}; {target_line}; '
                   f'{"held" if held else "risk-sized"} shares {shares if shares is not None else "unknown"}. '
                   f'Status: {state}. {setup["trigger"]} {setup["invalidation"]} '
                   + ' '.join(reasons) + ' ' + risk_note)
        ctx.add(setup['key'], display, 'code-calculated conditional trade setup, not a price forecast', 'setups', [eid]+inputs)
        if valid:
            for i, target in enumerate(targets, 1):
                key = f'level.{name}_target{i}'
                label = f'{title} take profit {i} — {target["label"]}'
                ctx.add(key, m(target['price']), label + '; conditional objective, not forecast', 'levels', [eid]+inputs)
                ctx.level(key, target['price'], label, 'target')
            if held:
                ctx.add('level.holding_assessed_stop', m(stop), 'Higher of stated stop and calculated candidate; not maintained', 'levels', [eid]+inputs)
                ctx.level('level.holding_assessed_stop', stop, 'Holding stop to assess', 'stop')
        if valid:
            setup['playbook'] = _state_playbook(ctx, lv, setup, name)
            for branch in setup['playbook']:
                ctx.add(f"playbook.{name}.{branch['case']}", branch['text'],
                        f"{title} playbook — {branch['title']}; code-written conditional rule, "
                        f"not a forecast", 'playbook', [eid]+inputs)
        else:
            setup['playbook'] = []
        plan.setups.append(setup)


def _no_chase_limit(target1: float, stop: float) -> float:
    """Highest entry whose first target still meets MIN_REWARD_RISK: (T1 - e) / (e - stop) >= R."""
    return (target1 + MIN_REWARD_RISK * stop) / (1 + MIN_REWARD_RISK)


def _state_playbook(ctx, lv, setup, name):
    """F04: guidance follows the setup's state; no entry branch is shown while a gate forbids it."""
    if setup['state'] == 'blocked':
        needs = '; '.join(setup['eligibility_needs']) or 'the blocking condition above to clear'
        return [{'case': 'wait', 'kind': 'wait', 'title': 'Now: wait — not eligible',
                 'text': (f"No entry from this setup now: {' '.join(setup['reasons'])} It becomes eligible "
                          f"only with {needs}. Refresh the plan after each daily close; until then no buy "
                          f"branch applies.")}]
    if setup['state'] == 'unavailable':
        return []
    book = _playbook(ctx, lv, setup, name)
    if setup['state'] == 'exit_triggered':
        return [{'case': 'now', 'kind': 'exit', 'title': 'Now: exit rule met',
                 'text': (f"The latest daily close ({ctx.m(lv.close)}) is below the assessed stop "
                          f"({ctx.m(setup['stop'])}): the exit rule is met. Waiting for a recovery is not part "
                          f"of this plan.")}]
    return book


def _playbook(ctx, lv, setup, name):
    """What to do if the price rises, falls or goes sideways, and how to manage a filled entry.

    Every branch is a CONDITIONAL rule built from the setup's own code-calculated levels and the
    fixed textbook constants above — never a statement about which branch will happen.
    """
    m, A = ctx.m, lv.atr
    entry, stop = setup['entry'], setup['stop']
    t1, t2 = setup['targets'][0]['price'], setup['targets'][1]['price']
    vol_line = f"{BREAKOUT_VOLUME_MULT:g} × the 20-bar average volume ({lv.avg_volume20 * BREAKOUT_VOLUME_MULT:,.0f} shares)"
    below = [x for x in (lv.low252,) if x is not None and x < min(stop, lv.low20)]
    next_support = (f" The next stored support below is the year low ({m(below[0])})." if below else "")
    sideways = (f"While daily closes stay between the 20-bar low ({m(lv.low20)}) and the 20-bar high "
                f"({m(lv.high20)}) without a trigger, there is no trade. The 20-bar window rolls forward "
                f"each day, so these levels are recalculated: refresh the plan after each close. Price "
                f"alerts at the two edges tell you when either case starts.")
    if name == 'holding':
        trail = lv.close - TRAIL_ATR * A
        return [
            {'case': 'rises', 'kind': 'manage', 'title': 'If it rises',
             'text': (f"Keep holding while daily closes stay above the stop. As closes rise, a textbook "
                      f"trailing stop follows the latest close − {TRAIL_ATR:g} × ATR (today {m(trail)}); "
                      f"raise the stop to it only when that is higher — never lower a stop. Take profit 1 "
                      f"is {m(t1)}; a common approach takes part of the position there and keeps the "
                      f"rest toward take profit 2 ({m(t2)}) with the trailing stop.")},
            {'case': 'falls', 'kind': 'exit', 'title': 'If it falls',
             'text': (f"A daily close below the assessed stop ({m(stop)}) meets the exit rule: sell rather "
                      f"than wait for a recovery. An overnight gap, especially around earnings, can fill "
                      f"below the stop.{next_support}")},
            {'case': 'sideways', 'kind': 'manage', 'title': 'If it goes sideways',
             'text': (f"Hold while daily closes stay above the stop ({m(stop)}); a sideways market does not "
                      f"move the stop. {sideways}")},
        ]
    chase = setup['max_fill']
    breakeven = entry + BREAKEVEN_ATR * A
    after = {'case': 'after_entry', 'kind': 'manage', 'title': 'Only after an eligible fill (hypothetical)',
             'text': (f"Applies only once an entry above has actually filled; recalculate risk at that fill. Once a daily close is {BREAKEVEN_ATR:g} ATR "
                      f"above the entry (above {m(breakeven)} from the {m(entry)} reference), move the stop "
                      f"up to the entry. At take profit 1 ({m(t1)}) a common approach takes part of the "
                      f"position and trails the rest at the latest close − {TRAIL_ATR:g} × ATR toward take "
                      f"profit 2 ({m(t2)}). A daily close below the stop exits.")}
    if name == 'pullback':
        return [
            {'case': 'falls', 'kind': 'entry', 'title': 'If it falls',
             'text': (f"A pullback to the 20-bar low ({m(lv.low20)}) is this setup's entry zone: buy only "
                      f"after a daily close holds above it, with the stop at {m(stop)}. A daily close "
                      f"below {m(stop)} instead cancels the setup — do not buy; wait for a new 20-bar base "
                      f"to form.{next_support}")},
            {'case': 'rises', 'kind': 'entry', 'title': 'If it rises',
             'text': (f"If the price rises without pulling back, this entry does not trigger — do not chase. "
                      f"Above {m(chase)}, the first target ({m(t1)}) is less than {MIN_REWARD_RISK:g}R away "
                      f"with this stop. A daily close above the 20-bar high ({m(lv.high20)}) on volume above "
                      f"{vol_line} switches to the breakout setup instead.")},
            {'case': 'sideways', 'kind': 'wait', 'title': 'If it goes sideways', 'text': sideways},
            after,
        ]
    if chase <= lv.high20 * (1 + 1e-9):
        # F06: the trigger (a close ABOVE the high) is already past the highest acceptable fill.
        fill = (f"A confirming close does not by itself allow a buy: any fill above {m(chase)} leaves the "
                f"first target ({m(t1)}) less than {MIN_REWARD_RISK:g}R away, and {m(chase)} is at or below "
                f"the breakout level. After confirmation, only a limit order at or below {m(chase)} on a "
                f"retest qualifies; if it has not filled within {BREAKOUT_RETEST_SESSIONS} sessions, the setup "
                f"lapses — refresh the plan.")
    else:
        fill = (f"Fill at or below {m(chase)}: above it the first target ({m(t1)}) is less than "
                f"{MIN_REWARD_RISK:g}R away. If the confirming day closes or the next day opens above "
                f"{m(chase)}, wait for a retest to {m(chase)} or below; if none within "
                f"{BREAKOUT_RETEST_SESSIONS} sessions, the setup lapses.")
    return [
        {'case': 'rises', 'kind': 'entry', 'title': 'If it rises',
         'text': (f"A daily close above the 20-bar high ({m(lv.high20)}) on volume above {vol_line} "
                  f"confirms this setup, with the stop at {m(stop)}. A close above the high on lighter "
                  f"volume is unconfirmed: wait for a confirming close. {fill}")},
        {'case': 'falls', 'kind': 'wait', 'title': 'If it falls',
         'text': (f"If the price falls back toward the 20-bar low ({m(lv.low20)}), the breakout does not "
                  f"apply and the pullback setup is the relevant one. After a breakout entry, a daily close "
                  f"back below {m(stop)} is a failed breakout: exit.{next_support}")},
        {'case': 'sideways', 'kind': 'wait', 'title': 'If it goes sideways', 'text': sideways},
        after,
    ]
