"""What the USER asked for, parsed by a small bounded grammar — the reference a proposal must match.

WHY (independent phase 2 review, S2/S3). The first validators checked that a proposed number
appeared SOMEWHERE in the question and that a keyword was mentioned. That accepted:

  "K-Score below 70"                    -> min_score=70        (direction reversed)
  "Show 10 stocks with K-Score above 70" -> min_confidence=10   (a count used as a threshold)
  "Technology stocks, excluding BUY"     -> signal=BUY          (negation ignored)
  "Alert me if it drops 10%"             -> price below $10     (a percentage read as a price)
  "Alert me below 100"                   -> price ABOVE $100
  "below its 50-bar average"             -> the K-Score fair value instead

A bag of mentioned numbers cannot tell those apart. This module parses the question into TYPED
constraints — metric, comparator, value, polarity; or alert direction and price source — and a
proposal is accepted only when it matches one. It is deliberately small: a request it cannot
parse is reported as not understood or unsupported, never guessed into a filter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

_NUM = r"(\d+(?:\.\d+)?)"
#: A COMPLETE amount (re-review C1): grouped thousands are supported explicitly, and the
#: lookahead forbids stopping inside a longer token — "1,000" cannot yield "1", "1.5" cannot
#: yield "1" — while sentence punctuation after a number ("above 70, top 5") still ends it.
#: Units ("10%", "50-day") are inspected by the caller from what follows the amount.
_AMOUNT = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d|[.,]\d)"


def amount_value(text: str) -> float:
    return float(text.replace(",", ""))
_UP = (r"(?:above|over|greater than|more than|higher than|at least|no less than|exceeding|"
       r"exceeds|minimum of|min(?:imum)?|>=|>)")
_DOWN = (r"(?:below|under|less than|lower than|at most|no more than|maximum of|max(?:imum)?|"
         r"<=|<)")
_NEG = r"(?:excluding|exclude|except|without|not|no|non|other than|besides|apart from)"

#: Metric phrases, most specific first. "score" alone means the K-Score, the headline score.
_METRICS = [
    ("rs", r"relative[- ]strength|\brs\b(?:[- ]score)?"),
    ("confidence", r"signal[- ]strength|confidence|\bstrength\b"),
    ("momentum", r"momentum"),
    ("technical", r"technical(?:[- ]score)?"),
    ("growth", r"growth(?:[- ]score)?"),
    ("score", r"k[- ]?score|\bscor(?:e|es|ing)\b"),
]


@dataclass(frozen=True)
class Bound:
    metric: str
    op: str          # "ge" | "le"
    value: float


@dataclass
class ScreenIntent:
    bounds: list[Bound] = dc_field(default_factory=list)
    #: Comparisons the user NEGATED ("not above 70") — refused, never inverted or dropped
    #: silently (re-review C2).
    negated: list[Bound] = dc_field(default_factory=list)
    #: Asks the grammar cannot represent exactly; each becomes an "unsupported" line.
    refusals: list[str] = dc_field(default_factory=list)
    #: The question joins conditions with OR. The screen can only AND, so nothing is applied.
    disjunction: bool = False
    unattached: list[str] = dc_field(default_factory=list)   # a comparison with no metric
    count: int | None = None
    sort: str | None = None
    signals: dict[str, bool] = dc_field(default_factory=dict)   # value -> True=wanted, False=excluded
    markets: dict[str, bool] = dc_field(default_factory=dict)
    sector_words: dict[str, bool] = dc_field(default_factory=dict)


def _negated_before(text: str, start: int) -> bool:
    return bool(re.search(rf"\b{_NEG}\b[\w\s-]{{0,20}}$", text[max(0, start - 30):start], re.I))


def _metric_at(text: str, end: int) -> str | None:
    """The metric named closest BEFORE `end`, within the same clause."""
    clause = re.split(r"[,;.]|\band\b|\bwith\b(?=[^,;.]*\b(?:" + "|".join(p for _, p in _METRICS)
                      + r"))", text[:end], flags=re.I)[-1]
    # The phrase ENDING last wins, and the longest on a tie: in "technical score" both
    # "technical score" and "score" end together, and the longer one is the metric meant.
    best, key = None, (-1, -1)
    for name, pat in _METRICS:
        for m in re.finditer(pat, clause, re.I):
            k = (m.end(), m.end() - m.start())
            if k > key:
                best, key = name, k
    return best


def parse_screen(question: str) -> ScreenIntent:
    q = question
    it = ScreenIntent()
    taken: list[tuple[int, int]] = []
    for m in re.finditer(rf"\bbetween\s+{_NUM}\s+and\s+{_NUM}", q, re.I):
        metric = _metric_at(q, m.start())
        if metric:
            it.bounds += [Bound(metric, "ge", float(m.group(1))), Bound(metric, "le", float(m.group(2)))]
        else:
            it.unattached.append(m.group(0))
        taken.append(m.span())
    for m in re.finditer(rf"(?:\bof\s+)?({_UP}|{_DOWN})\s*\$?{_AMOUNT}(\s*(?:%|percent|per\s*cent))?",
                         q, re.I):
        if any(a <= m.start() < b for a, b in taken):
            continue
        op = "ge" if re.fullmatch(_UP, m.group(1), re.I) else "le"
        metric = _metric_at(q, m.start())
        value = amount_value(m.group(2))
        taken.append(m.span())
        if not metric:
            it.unattached.append(m.group(0))
            continue
        if m.group(3):
            it.refusals.append(f"'{m.group(0).strip()}' — scores are 0-100 points, not "
                               f"percentages; ask without the % sign")
            continue
        # NEGATION (C2): "not above 70" / "isn't below 40". The fixed comparators "no more
        # than"/"no less than" are part of _UP/_DOWN and are not negations of a comparison.
        if re.search(r"(?:\bnot|n't|\bnever)\s+$", q[max(0, m.start() - 12):m.start()], re.I):
            it.negated.append(Bound(metric, op, value))
            it.refusals.append(f"'not {m.group(1)} {m.group(2)}' — negated comparisons are not "
                               f"supported; ask for the bound you want (e.g. 'at most …')")
            continue
        it.bounds.append(Bound(metric, op, value))
    # OR (C2): the screen ANDs every filter, so "A or B" would silently become "A and B".
    if re.search(r"\bor\b|\beither\b", q, re.I):
        it.disjunction = True
    c = (re.search(rf"\b(?:top|first|show(?: me)?|list|give me|best)\s+{_NUM}\b", q, re.I)
         or re.search(rf"\b{_NUM}\s+(?:stocks|names|results|companies|tickers|picks)\b", q, re.I))
    if c:
        it.count = int(float(c.group(1)))
    s = re.search(r"\b(?:by|sorted by|ranked by|order(?:ed)? by)\s+(?:the\s+)?(?:highest\s+)?"
                  r"([a-z -]+)", q, re.I)
    if s:
        for name, pat in _METRICS:
            if re.match(pat, s.group(1).strip(), re.I):
                it.sort = {"score": "score", "rs": "rs_score"}.get(name, name)
                break
    for m in re.finditer(r"\b(BUY|SELL|HOLD|WAIT)(?:s|ing)?\b", q, re.I):
        it.signals[m.group(1).upper()] = not _negated_before(q, m.start())
    for code, pat in (("US", r"\b(?:US|U\.S\.?|USA|American|United States|NYSE|NASDAQ)\b"),
                      ("HK", r"\b(?:HK|Hong\s*Kong|HKEX)\b")):
        for m in re.finditer(pat, q, re.I):
            it.markets[code] = not _negated_before(q, m.start())
    for m in re.finditer(r"\b[a-z]{4,}\b", q, re.I):
        it.sector_words[m.group(0).lower()] = not _negated_before(q, m.start())
    return it


# ── Alerts ───────────────────────────────────────────────────────────────────

#: Where a threshold may come from, keyed by the phrase that names it.
_SOURCES = [
    ("trend.sma50", r"\b50[- ]?(?:day|bar|session|d)\b[\w\s-]{0,20}(?:average|sma|ma)\b|"
                    r"\bsma[- ]?50\b|\b50[- ]?(?:day|bar|session)\s+(?:moving\s+)?average"),
    ("trend.sma20", r"\b20[- ]?(?:day|bar|session|d)\b[\w\s-]{0,20}(?:average|sma|ma)\b|"
                    r"\bsma[- ]?20\b|\b20[- ]?(?:day|bar|session)\s+(?:moving\s+)?average"),
    ("kscore.fair_price", r"\bfair[- ](?:value|price)\b"),
    ("price.close", r"\b(?:current|today'?s|latest|last)\s+(?:price|close)\b"),
]
_ALERT_DOWN = r"(?:falls?|drops?|dips?|goes|trades|closes|moves|is)?\s*(?:below|under|beneath|less than)"
_ALERT_UP = r"(?:rises?|climbs?|goes|trades|closes|moves|breaks|is)?\s*(?:above|over|past|beyond|greater than)"
_AMBIGUOUS = r"\b(?:hits?|reach(?:es)?|touch(?:es)?|gets? to|crosses?)\b"
_PERCENT = rf"{_NUM}\s*(?:%|percent|per\s*cent)"
_NEG_ACTION = (r"\b(?:don'?t|do not|never|no need to|stop|cancel|without)\b[\w\s']{0,20}"
               r"\b(?:alert|notify|tell|add|watch|subscribe|remind)")


@dataclass(frozen=True)
class AlertTarget:
    condition: str               # "above" | "below"
    source: str                  # "typed" or a packet key
    value: float | None = None   # for typed


@dataclass
class AlertIntent:
    targets: list[AlertTarget] = dc_field(default_factory=list)
    refusals: list[str] = dc_field(default_factory=list)
    negated: bool = False


def parse_alert(question: str) -> AlertIntent:
    """Typed alert targets: (direction, named source | complete typed price).

    ORDER MATTERS (re-review C1): a named source is matched BEFORE any number, so the window
    in "50-day average" is never read as a price; a number must be a COMPLETE token; and a
    percentage anywhere refuses price alerts outright rather than letting a partial target
    through beside the refusal.
    """
    q = question
    it = AlertIntent()
    if re.search(_NEG_ACTION, q, re.I):
        it.negated = True
        return it
    if re.search(_PERCENT, q, re.I):
        it.refusals.append("percentage-change alerts are not supported — a price alert needs a "
                           "price, and a percentage is not read as one")
        return it
    for m in re.finditer(rf"({_ALERT_DOWN}|{_ALERT_UP})", q, re.I):
        cond = "below" if re.search(r"below|under|beneath|less", m.group(0), re.I) else "above"
        rest = q[m.end():m.end() + 80]
        src = None
        for key, pat in _SOURCES:
            if re.match(rf"\s*(?:its|the|their)?\s*(?:{pat})", rest, re.I):
                src = key
                break
        if src:
            it.targets.append(AlertTarget(cond, src))
            continue
        num = re.match(rf"\s*(?:HK)?\$?\s*{_AMOUNT}", rest, re.I)
        if not num:
            if re.match(r"\s*(?:HK)?\$?\s*\d", rest, re.I):
                # A number is there but is not a COMPLETE amount ("1,00"): never guess a prefix.
                it.refusals.append(f"the number after '{m.group(0).strip()}' was not understood; "
                                   f"write it as e.g. 1000 or 1,000")
            continue
        tail = rest[num.end():]
        if re.match(r"\s*(?:%|percent|per\s*cent)", tail, re.I):
            it.refusals.append("percentage-change alerts are not supported")
            continue
        if re.match(r"[- ]?(?:day|bar|session|d|week|month|year)s?\b", tail, re.I):
            it.refusals.append(f"'{m.group(0).strip()} {num.group(0).strip()}{tail.split()[0] if tail.split() else ''}' "
                               f"names a window, not a price — say which average (e.g. "
                               f"'its 50-day average')")
            continue
        if re.match(r"[A-Za-z]", tail):
            it.refusals.append(f"'{num.group(0).strip()}{tail.split()[0]}' is not a price this "
                               f"can read; write the full number")
            continue
        it.targets.append(AlertTarget(cond, "typed", amount_value(num.group(1))))
    if not it.targets and not it.refusals and re.search(_AMBIGUOUS, q, re.I):
        it.refusals.append("say whether the alert is for the price going above or below the "
                           "level; 'hits' or 'reaches' does not say which side")
    return it


def wants(question: str, pattern: str) -> bool:
    """An action is requested, and not negated."""
    return bool(re.search(pattern, question, re.I)) and not re.search(_NEG_ACTION, question, re.I)


_NOT_TICKERS = {"BUY", "SELL", "HOLD", "WAIT", "SMA", "EMA", "US", "HK", "USA", "ETF", "EPS", "CEO",
                "RS", "AI", "IPO", "PE", "THE", "AND", "OK", "HKD", "USD", "SWING", "LONG", "SHORT",
                "GROWTH", "RSI", "MACD", "ATR", "VWAP", "NYSE"}


def other_tickers(question: str, symbol: str) -> list[str]:
    """Upper-case ticker-like tokens in the question that are NOT this conversation's symbol."""
    base = symbol.split(".")[0].upper()
    toks = re.findall(r"\b[A-Z]{2,5}(?:\.[A-Z]{1,2})?\b|\b\d{4}\.HK\b", question)
    return [t for t in toks if t.upper() not in _NOT_TICKERS
            and t.upper() not in (symbol.upper(), base)]


# ── Plan mode: what kind of plan, and the position the user TYPED ────────────
# Design: docs/features/2026-10-14-copilot-plan-mode-design.md §2.3/§2.5. The position is never
# read from an account; it is parsed from the question by this bounded grammar, and anything it
# cannot read exactly is refused with a reason rather than guessed into a plan.

_HOLD_VERB = (r"\b(?:i|we)\s*(?:'ve|have|had|already)?\s*(?:bought|purchased|own|hold|got in|"
              r"entered|picked up|am holding|'m holding|am long|'m long)\b|\bi'?m\s+(?:in|long)\b|"
              r"\bmy\s+(?:position|shares|entry|cost(?: basis)?|average(?: cost| price)?|avg)\b|"
              r"\b(?:avg|average)\s+(?:cost|price)\b|\bcost\s+basis\b|\bholding\s+\d|"
              r"\b(?:i|we)\s+(?:have|hold)\s+\d[\d,]*\s+shares?\b|"
              # A sentence that STARTS with the verb: "Bought 100 at 182.5".
              r"(?:^|(?<=[.;!?]))\s*(?:just\s+)?(?:bought|purchased|got\s+in|entered)\b")
_HOLD_NEGATED = (r"\b(?:don'?t|do not|never|haven'?t|have not|didn'?t|did not|not)\s+"
                 r"(?:yet\s+)?(?:own|hold|bought|buy|purchased|in)\b")
_ENTRY_ASK = (r"\bshould\s+i\s+buy\b|\b(?:want|wanna|thinking|plan(?:ning)?|looking|considering)"
              r"\s+(?:to\s+|of\s+|about\s+)?buy(?:ing)?\b|\bbuy\s+(?:point|zone|level|here|now)\b|"
              r"\bwhere\s+(?:to|should\s+i)\s+buy\b|\bgood\s+(?:time|entry|price)\b|\bentry\b|"
              r"\bstop[- ]?loss\b|\bplaybook\b|\bgame\s*plan\b|\btrading\s+plan\b|\bplan\b|"
              r"\bwhat\s+(?:should|do|would)\s+i\s+do\b|\bwhat\s+to\s+do\b|\bnext\s+(?:step|move)\b|"
              r"\bposition\s+siz|\bhow\s+many\s+shares\b")
_HOLD_ASK = (r"\bshould\s+i\s+(?:sell|hold|exit|trim|take\s+profits?|add)\b|"
             r"\btake\s+profits?\b|\bwhen\s+(?:to|should\s+i)\s+sell\b|\bexit\b|\btrailing\b")
_FORECAST_ASK = (r"\bpredict(?:ion|ed|s)?\b|\bforecast\b|\boutlook\b|\bnext\s+(?:few\s+)?"
                 r"(?:days?|weeks?|sessions?|month)\b|\bcoming\s+(?:days|weeks?)\b|"
                 r"\bthis\s+week\b|\bwhere\s+(?:is|will|could)\s+(?:it|the\s+(?:stock|price))\s+"
                 r"go(?:ing)?\b|\bgo(?:ing)?\s+(?:up|down)\b|\bdirection\b|\bexpect(?:ation)?s?\b|"
                 r"\btrack\s+record\b|\bhow\s+(?:accurate|reliable|good)\b")
#: Long stock only. A position in options or a short position gets no stock rules at all.
_DERIVATIVE = (r"\b(?:bought|own|hold(?:ing)?|long|sold|wrote|writing|sell(?:ing)?)\s+(?:\d[\d,]*\s+)?"
               r"(?:\w+\s+)?(?:calls?|puts?|options?|contracts?|leaps)\b|"
               r"\b(?:call|put)\s+options?\b")
_SHORT_POS = r"\b(?:shorted|shorting|sold\s+(?:it\s+)?short|short\s+(?:position|sale)|am\s+short|'m\s+short)\b"

#: An amount introducing the ENTRY: "at 182.5", "@ $182.5", "avg cost 182.5", "entry 182.5".
_ENTRY_LEAD = (r"(?:\bat\b|@|\b(?:avg|average)\s+(?:cost|price)(?:\s+(?:is|of|was))?|"
               r"\bcost(?:\s+basis)?(?:\s+(?:is|of|was))?|\bentry(?:\s+price)?(?:\s+(?:is|of|was|at))?|"
               r"\bpaid)")
#: An amount after these words is a stop, a target or an order — never the entry.
_NOT_ENTRY_BEFORE = (r"(?:stop(?:[- ]loss)?|target|sell|exit|alert|limit|take\s+profit|trail\w*|"
                     r"support|resistance|average|sma|ma|low|high)\s*(?:is|of|at|@|near|around)?\s*$")
_SHARES = rf"\b{_AMOUNT}\s*(?:shares?|shs)\b"


@dataclass
class PositionIntent:
    kind: str | None = None                 # None | forecast | entry | holding
    entry: float | None = None
    shares: int | None = None
    risk_amount: float | None = None
    account: float | None = None
    risk_pct: float | None = None
    refusals: list[str] = dc_field(default_factory=list)


def _clauses_with_holding(q: str) -> list[tuple[int, int]]:
    """Sentence spans that contain a holding verb (commas do NOT split: '100 shares, avg cost
    182' is one statement)."""
    spans, start = [], 0
    for m in re.finditer(r"[.;!?](?=\s|$)|\n", q):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(q)))
    return [(a, b) for a, b in spans if re.search(_HOLD_VERB, q[a:b], re.I)]


def parse_position(question: str) -> PositionIntent:
    """The plan kind and any typed position. Refusals say why a figure was not read."""
    q = question
    it = PositionIntent()
    holding_said = bool(re.search(_HOLD_VERB, q, re.I)) and not re.search(_HOLD_NEGATED, q, re.I)
    if re.search(_DERIVATIVE, q, re.I) or re.search(_SHORT_POS, q, re.I):
        it.kind = "entry" if not holding_said else "holding"
        it.refusals.append("the playbook covers long stock positions only; options and short "
                           "positions get no rules here (see the Options Game Plan for options)")
        return it

    # ENTRY — only inside a sentence that states a holding, so "stop at 170" or "alert me at
    # 200" elsewhere is never read as a cost basis.
    entries: list[float] = []
    if holding_said:
        for a, b in _clauses_with_holding(q):
            clause = q[a:b]
            for m in re.finditer(rf"{_ENTRY_LEAD}\s*(?:HK)?\$?\s*(\d[\d,.]*)", clause, re.I):
                if re.search(_NOT_ENTRY_BEFORE, clause[:m.start()], re.I):
                    continue
                tok = m.group(1).rstrip(".,")
                full = re.fullmatch(_AMOUNT.replace("(?!\\d|[.,]\\d)", ""), tok)
                tail = clause[m.end():]
                if full is None:
                    it.refusals.append(f"the entry price '{tok}' was not understood; write it as "
                                       f"e.g. 182.50 or 1,250")
                    continue
                if re.match(r"\s*(?:%|percent|per\s*cent)", tail, re.I):
                    continue
                if re.match(r"\s*(?:shares?|shs)\b", tail, re.I):
                    continue
                if re.match(r"[- ]?(?:day|bar|session|week|month|year)s?\b", tail, re.I):
                    continue
                if re.match(r"\s*(?::\d|[ap]\.?m\b)", tail, re.I):
                    continue                     # a time of day ("at 9:30", "at 10am")
                if re.match(r"[kKmM]\b", tail):
                    it.refusals.append(f"'{tok}{tail[0]}' is not a price this can read; write "
                                       f"the full number")
                    continue
                entries.append(amount_value(tok))
    distinct = sorted(set(entries))
    if len(distinct) > 1:
        it.refusals.append("more than one entry price was given "
                           f"({', '.join(f'{x:g}' for x in distinct)}); give your average cost "
                           f"per share instead")
    elif distinct:
        if distinct[0] <= 0:
            it.refusals.append("an entry price must be above zero")
        else:
            it.entry = distinct[0]

    shares = {amount_value(m.group(1)) for m in re.finditer(_SHARES, q, re.I)}
    if len(shares) > 1:
        it.refusals.append("more than one share count was given; give the total you hold")
    elif shares:
        n = next(iter(shares))
        if n != int(n) or n <= 0:
            it.refusals.append("a share count must be a whole number above zero")
        else:
            it.shares = int(n)
    elif holding_said:
        # "bought 100 at 182.5": a bare count right after the verb.
        m = re.search(rf"\b(?:bought|purchased|own|hold|holding|picked up)\s+{_AMOUNT}\s+(?:at|@)",
                      q, re.I)
        if m and float(m.group(1).replace(",", "")) == int(float(m.group(1).replace(",", ""))):
            it.shares = int(amount_value(m.group(1)))

    # RISK — "risk $500", "can lose 500 dollars", "account 50,000 risk 1%".
    acct = re.search(rf"\b(?:account|portfolio|capital|equity)(?:\s+(?:size|is|of|=|value))*\s*"
                     rf"(?:HK)?\$?\s*{_AMOUNT}(?![kKmM]\b)", q, re.I)
    if acct:
        it.account = amount_value(acct.group(1))
    rp = re.search(rf"\brisk(?:ing)?\s+(?:of\s+)?{_AMOUNT}\s*(?:%|percent|per\s*cent)", q, re.I)
    ra = (re.search(rf"\brisk(?:ing)?\s+(?:of\s+|up\s+to\s+)?(?:HK)?\$\s*{_AMOUNT}(?!\s*%)", q, re.I)
          or re.search(rf"\brisk(?:ing)?\s+(?:of\s+|up\s+to\s+)?{_AMOUNT}\s*(?:dollars|usd|hkd)\b",
                       q, re.I)
          or re.search(rf"\b(?:can|could|willing\s+to|want\s+to|able\s+to)\s+(?:afford\s+to\s+)?"
                       rf"lose\s+(?:up\s+to\s+)?(?:HK)?\$?\s*{_AMOUNT}(?!\s*%)", q, re.I))
    if rp:
        if it.account is None:
            it.refusals.append("a percentage risk needs an account size (e.g. 'account 50,000, "
                               "risk 1%'); no position size was computed")
        elif not 0 < amount_value(rp.group(1)) <= 100:
            it.refusals.append("a risk percentage must be between 0 and 100")
        else:
            it.risk_pct = amount_value(rp.group(1))
    elif ra:
        it.risk_amount = amount_value(ra.group(1))

    if holding_said or (it.entry is not None) or re.search(_HOLD_ASK, q, re.I):
        it.kind = "holding"
        if it.entry is None and not any("entry price" in r for r in it.refusals):
            it.refusals.append("no entry price was found; say e.g. 'I bought 100 shares at "
                               "182.50' to get rules for your position")
    elif re.search(_ENTRY_ASK, q, re.I) or it.risk_amount is not None or it.risk_pct is not None:
        it.kind = "entry"
    elif re.search(_FORECAST_ASK, q, re.I):
        it.kind = "forecast"
    return it
