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

#: UNRESOLVED CLASSIFICATION RISK, recorded rather than papered over.
#:
#: A newly PUBLISHED retrospective article — "Revisiting Acme's Q2 Results" posted today — passes
#: every check this module and its caller apply: the issuer matches, the publication date is
#: inside the freshness window, and it names a period. It would be classified `results` and could
#: occupy the confirmed-results slot for an event it is not about.
#:
#: Nothing here closes that. Freshness excludes OLD articles, not NEW articles ABOUT old events,
#: and period extraction is explicitly not identity. Closing it needs authoritative release
#: identity — an issuer/period/event key from the release or filing itself — which is the P1
#: outbox/event-identity work, not another heuristic.
KNOWN_UNRESOLVED_RISK = (
    "A newly published retrospective article about a PAST period passes issuer, freshness and "
    "period checks and can be classified as results. Requires authoritative release identity."
)

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
_PERIOD_FY = re.compile(r"\b(full[- ]year|fiscal[- ]year|FY\s?\d{2,4}|full[- ]year results)\b", re.I)
_PERIOD_YEAR = re.compile(r"\b(?:FY\s?|fiscal\s+)?(20\d{2})\b")
_PERIOD_WORD = re.compile(r"\b(first|second|third|fourth)[- ]quarter\b", re.I)
_WORD_TO_Q = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}


def extract_period(headline: str | None) -> str | None:
    """The fiscal period a headline names — "Q4", "FY", or None when it names none.

    PERIOD EXTRACTION FOR RECONCILIATION — not event identity, and the name matters. This
    records what a headline SAYS so it can be reconciled later against authoritative data. It
    does not bind the article to a fiscal year or a report event, and it never could: a
    retrospective piece names a quarter it is merely discussing, and "Q4" alone fixes no year.
    It narrows a class of headlines. That is the whole of its job.

    `FY` covers the case a quarter-only rule got wrong: a valid release can report **full-year
    results** without naming a quarter at all, and an earlier version of this function would have
    returned None for those and silently excluded them.
    """
    if not headline:
        return None
    m = _PERIOD_Q.search(headline)
    if m:
        return f"Q{m.group(1)}"
    m = _PERIOD_WORD.search(headline)
    if m:
        return _WORD_TO_Q[m.group(1).lower()]
    if _PERIOD_FY.search(headline):
        return "FY"
    return None


def extract_fiscal_year(headline: str | None) -> int | None:
    """Any four-digit year the headline names, for provenance — never for matching.

    **Deliberately not compared against `EarningsEvent.fiscal_year`.** Measured on production
    2026-10-01: MU's row for the 2026-09-30 release is labelled **"Q3 2026"** while the actual
    result headline says **Q4** — MU has an August fiscal year-end and the stored label is derived
    from the calendar month, which the model's own comment admits is "a best-effort calendar-month
    label". A rule matching headline period against that label would have rejected MU's own
    release: the exact event this whole fix exists for. Recorded for later reconciliation only.
    """
    if not headline:
        return None
    m = _PERIOD_YEAR.search(headline)
    return int(m.group(1)) if m else None


def results_binding(headline: str | None) -> tuple[bool, str]:
    """May this headline occupy the confirmed-RESULTS slot, and if not, why not?

    A NARROWING CHECK, NOT AN IDENTITY CHECK. It excludes headlines that name no period at all;
    it cannot exclude one that names a period for a different event. See
    `KNOWN_UNRESOLVED_RISK` below.

    Returns `(ok, reason)`. The reason exists so an excluded release does not disappear
    invisibly — the failure mode of a silent filter is indistinguishable from the bug it replaced.
    """
    period = extract_period(headline)
    if period is None:
        return False, "no_period_named"
    return True, "period_" + period


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
        # another company's print, and still read as "results".
        #
        # Requiring a NAMED PERIOD narrows that class — it does not establish event identity, and
        # is not treated as if it did. "Q4" binds nothing to a fiscal year or a report event; see
        # `extract_fiscal_year()` for why matching against the stored label would be worse than
        # not matching at all. A full-year release counts, so a legitimate "full-year results"
        # headline is not excluded for lacking a quarter.
        _ok, _ = results_binding(text)
        return PHASE_RESULTS if _ok else PHASE_OTHER
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
