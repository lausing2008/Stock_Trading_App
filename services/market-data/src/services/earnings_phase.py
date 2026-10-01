"""MU-02: which STAGE of an earnings release a headline represents.

THE INCIDENT THIS EXISTS FOR (2026-09-30, MU). News ingestion was fast — the Q4 results headline
reached the database **5.08 seconds** after publication, and the Q1 guidance headline 5.18 seconds
after its own. The platform had the facts almost immediately and sent nothing, because
`check_early_earnings_news_alerts` deduplicated on
`stockai:early_earnings_news:{user}:{symbol}:{day}` — **one headline per symbol per calendar day**.

A "Micron Earnings Ahead" PREVIEW at 17:27 consumed that day's single slot. When the actual
result landed at 20:01, the key already existed, so the one notification a reader actually wanted
was suppressed by a story that told them nothing.

The phases are not interchangeable and must not share a slot:

    preview   — "earnings ahead", "to report", "what to expect". Says a release is coming.
    results   — the actual EPS/revenue print. The one that matters.
    guidance  — forward outlook, often a SEPARATE headline minutes after the result, and often
                the bigger price driver.
    call      — conference-call/transcript commentary, later again.

Deliberately NOT "one email per headline" — the review rejects that too. Dedup becomes
(recipient, symbol, PHASE, day), so each stage notifies at most once and duplicate provider
coverage of the same stage still collapses.

Classification is from headline text only. That is a real limit and is stated rather than hidden:
this module decides what to CALL a headline, never what a stock will do.
"""
from __future__ import annotations

import re

PHASE_PREVIEW = "preview"
PHASE_RESULTS = "results"
PHASE_GUIDANCE = "guidance"
PHASE_CALL = "call"
PHASE_OTHER = "other"

#: Phases that justify their own notification, in release order. `other` is excluded — an
#: unclassifiable earnings-category headline should not consume a slot a real stage needs.
NOTIFIABLE_PHASES = (PHASE_PREVIEW, PHASE_RESULTS, PHASE_GUIDANCE, PHASE_CALL)

# Order matters, and the first two rules carry the whole MU case.
#
# `call` first: "Micron Technology Q4 Earnings Call Transcript" contains "Q4" and "Earnings" and
# would otherwise read as a result.
#
# `guidance` before `results`: the two real MU headlines were
#     "Micron Technology Q4 Adj EPS $3.42 Beats $1.45 Estimate"            -> results
#     "Micron Technology Sees Q1 Adj EPS $7.15-$9.15 vs $5.07 Est"         -> guidance
# Both carry "Q<n>", "Adj EPS" and an estimate comparison; only the forward verb separates them.
_CALL = re.compile(r"\b(earnings call|conference call|transcript|call highlights|prepared remarks)\b", re.I)
_GUIDANCE = re.compile(
    r"\b(sees|guides|guidance|forecasts|outlook|raises (?:its )?(?:fy|full[- ]year|q\d)|"
    r"cuts (?:its )?(?:fy|full[- ]year|q\d)|expects q\d|issues .*guidance)\b", re.I)
_RESULTS = re.compile(
    r"\b(beats|misses|tops|reports q\d|posts q\d|q\d (?:adj\.? )?eps|eps of|"
    r"reports (?:fourth|third|second|first) quarter|announces .*results|results? (?:top|beat|miss))\b", re.I)
_PREVIEW = re.compile(
    r"\b(earnings ahead|ahead of earnings|to report|will report|set to report|preview|"
    r"what to expect|earnings preview|expected to report|reports? (?:tomorrow|today|after the bell)|"
    r"analysts? expect)\b", re.I)


_PERIOD_Q = re.compile(r"\bQ([1-4])\b", re.I)
_PERIOD_WORD = re.compile(r"\b(first|second|third|fourth)[- ]quarter\b", re.I)
_WORD_TO_Q = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}


def extract_period(headline: str | None) -> str | None:
    """The fiscal period a headline names, e.g. "Q4" — or None when it names none.

    Carried so a headline can be checked against the event it is being attached to, and so an
    ambiguous one can be kept out of the results phase. Handles both "Q4" and "fourth quarter".
    """
    if not headline:
        return None
    m = _PERIOD_Q.search(headline)
    if m:
        return f"Q{m.group(1)}"
    m = _PERIOD_WORD.search(headline)
    if m:
        return _WORD_TO_Q[m.group(1).lower()]
    return None


def classify_earnings_phase(headline: str | None) -> str:
    """Which release stage this headline represents.

    Returns `other` for anything unrecognised — explicitly a fifth value rather than defaulting
    to `results`. Guessing `results` would let a vague headline consume the slot the real print
    needs, which is the exact failure this module exists to prevent, one level along.
    """
    if not headline or not headline.strip():
        return PHASE_OTHER
    text = headline.strip()
    if _CALL.search(text):
        return PHASE_CALL
    if _GUIDANCE.search(text):
        return PHASE_GUIDANCE
    if _RESULTS.search(text):
        # AMBIGUOUS ARTICLES MUST NOT CONSUME THE RESULTS PHASE. The date window is a freshness
        # filter, not event binding: a nearby article can discuss a different fiscal period, or
        # another company's print, and still read as "results". A genuine result headline names
        # its quarter — MU's did ("Q4 Adj EPS…"). One that names none cannot be bound to an
        # event, so it is downgraded to `other`: no notification, and crucially no consumption
        # of the slot the real print needs.
        return PHASE_RESULTS if extract_period(text) else PHASE_OTHER
    if _PREVIEW.search(text):
        return PHASE_PREVIEW
    return PHASE_OTHER


def phase_is_notifiable(phase: str) -> bool:
    return phase in NOTIFIABLE_PHASES


def phase_subject(symbol: str, phase: str) -> str:
    """Subject line per stage. A reader must be able to tell a preview from a print in the inbox
    list, without opening anything — which is precisely what the single-slot bug destroyed."""
    return {
        PHASE_PREVIEW: f"📅 {symbol} — earnings expected",
        PHASE_RESULTS: f"📊 {symbol} — earnings RESULTS reported",
        PHASE_GUIDANCE: f"🔮 {symbol} — forward guidance issued",
        PHASE_CALL: f"🎙️ {symbol} — earnings call commentary",
    }.get(phase, f"📰 {symbol} — earnings news spotted")


def phase_body(symbol: str, phase: str, headline: str) -> str:
    """What each stage may honestly claim.

    None of these assert a NUMBER. This path exists precisely because the structured EPS has not
    landed — on the MU incident it was still NULL more than three hours after the release — so the
    alert reports that a stage occurred and quotes the source headline. Parsing figures out of
    headline text and presenting them as verified results is explicitly out of scope: the review
    requires that authoritative earnings rows are never overwritten by headline parsing.
    """
    quoted = f'"{headline}"'
    common = (" The structured EPS figures have not landed in our data yet, so this reports the "
              "news itself, not a verified result. A follow-up alert carries the full numbers "
              "once they arrive.")
    if phase == PHASE_RESULTS:
        return (f"{symbol} has REPORTED. A real-time headline classified as earnings results: "
                f"{quoted}.{common}")
    if phase == PHASE_GUIDANCE:
        return (f"{symbol} has issued forward guidance: {quoted}. Guidance often moves a stock "
                f"more than the quarter just reported.{common}")
    if phase == PHASE_CALL:
        return (f"Commentary from {symbol}'s earnings call: {quoted}. This is call colour, not "
                f"the reported figures themselves.")
    if phase == PHASE_PREVIEW:
        return (f"{symbol} is expected to report earnings. A preview headline: {quoted}. "
                f"Nothing has been reported yet — this is advance notice only.")
    return (f"We spotted a real-time news item classified as earnings-related for {symbol}: "
            f"{quoted}.{common}")
