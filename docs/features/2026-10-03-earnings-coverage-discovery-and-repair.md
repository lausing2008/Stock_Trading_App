# Earnings coverage: discovery, the bounded MU repair, and the ledger that made it findable

Built 2026-10-03. Closes the gap that made MU's October post-earnings report describe **June**.

## What was actually wrong

MU's `earnings_events` rows jumped from `2026-06-24` to `2026-12-23`. The **30 September release
— fiscal Q4, period ending 2026-08-31, adjusted EPS $33.42 — was never ingested.** The report
picked the newest released event on file, exactly as specified, and said nothing about the gap.

Two structural reasons it could not self-heal:

- `sync_todays_earnings()` selects **existing** pending rows, so an event absent from the table
  can never enter its candidate set. Polling it faster can never find what was never created.
- The history sync upserts whatever the provider returns and never compares that against what is
  stored, so a row that failed to map was indistinguishable from one the provider never sent.

## Discovery

`services/event-intelligence/src/services/earnings_discovery.py` inverts it: ask the provider
what it holds, compare against the event table, report every row present upstream and absent
locally with the stage it stopped at. **Read-only.**

**Matching is bounded by the next period, not by a day count.** The provider indexes by PERIOD
END; the table stores the ANNOUNCEMENT date. The gap between them is the announcement lag — the
one quantity that must not be guessed, since a 55-day lag is ordinary and would be a false
absence under any fixed window. A period therefore matches any stored event falling before the
next period begins.

**A released result cannot have been announced by an event that has not happened.** The newest
period has no next period, so its upper bound is open-ended — and on the first production run
that let MU's missing quarter match the **December scheduled event three months away**,
reporting the gap as present. A row carrying a reported EPS describes something that already
happened, so released rows match only past events. Scheduled rows still match future ones.

A retrieval failure reports `rows_returned: None` — not measured — rather than `0`. A provider
that could not be reached has not told us anything is missing.

## The bounded MU repair

`repair()` **previews by default** and must be told explicitly to commit: writing a historical
earnings row is not reversible by re-running a job, and a frozen report may already cite the
absence.

It refuses to invent facts. The provider gives a period end; the announcement date is a
different fact, so the row carries the period end **with that substitution recorded**. The
inferred fiscal columns (`period`, `fiscal_year`, `fiscal_quarter`) are left **NULL** rather
than computed from the period month, which is wrong for every non-calendar fiscal year. Rows
with no reported EPS are skipped as scheduled periods.

**Executed 2026-10-03 for MU**, after checking every path that could fire:

| Path | Could the repaired row trigger it? |
|---|---|
| Earnings impact email | No — 2-day window; the row is 34 days old |
| Beat screener | No — 14-day window **and** requires `surprise_pct`, left NULL |
| Post-earnings return backfill | Yes — a factual price calculation. No email, no trade. |

Result: one row written (`2026-08-31`, EPS 33.42 vs 31.82), zero failures, and a second run
plans nothing. MU's post-earnings report now describes 2026-08-31, and its coverage field moved
from `suspected_gap` to `coverage_unknown`.

## The coverage ledger

`EarningsCoverageAttempt` records **one row per stage** — history and calendar separately,
because a calendar success cannot evidence historical coverage and pooling their counts hides
exactly the failure the ledger exists to expose.

**The watermark is the newest period actually written, never today's date.** It previously
defaulted to `date.today()`, which records when the job RAN rather than what it covered — so a
pass that succeeded while writing nothing newer than last quarter still marked coverage current,
and a discovery step trusting it would skip precisely the periods nobody had fetched. An `ok`
pass that wrote nothing now advances nothing. `earnings_history` takes no window, so
`window_start`/`window_end` are NULL rather than a processing time dressed as a requested range.

## Two defects of my own, both found by running it rather than by testing it

**Discovery reported the missing quarter as PRESENT** on its first production run (above). My
fixture ended at 2026-06-24 and omitted MU's December scheduled event, so no future event existed
to be falsely matched. The fixture now carries MU's real data including that event.

**A test-isolation bug, fixed at the cause.** The new tests passed alone and failed under
`make test`, then the reverse — because this service's conftest stubs `sqlalchemy` itself, so the
file sees mocks in isolation and the real library in the full run. Anything built on `select()`
passes one way and fails the other. The two data accessors are now separate functions the tests
replace, so the comparison — the part with defect potential — is exercised without an ORM, a
session, or a view on which sqlalchemy happens to be loaded.

## Not done

- **Browser verification.** This machine has Chrome but no automation library, and the report
  views are behind authentication. A hollow check would be worse than naming the gap: rendering,
  mobile and the nav-overlay question remain unverified.
- Discovery runs on demand only; it is not scheduled.
- Only MU was repaired. Other issuers are not assumed complete — the absence of a warning is not
  evidence of coverage, which is what `coverage_unknown` says.
