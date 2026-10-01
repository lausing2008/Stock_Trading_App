# MU earnings: missing alerts and faster event intelligence

Date: 2026-09-30. Production snapshots: 23:24–23:28 UTC (19:24–19:28 EDT; 16:24–16:28 PDT).
Scope: September 30 incident, read-only production database/Redis/log inspection and local consumer-code review. No emails, forecast-generation calls, flag changes, deployments or production mutations. CLAUDE.md unchanged.
Local revision inspected: `fc9bc8096575ffb47a94a65bc082c558e857365c`.

## Answer

The news feed was fast: MU's results headline arrived in the database about **5.08 seconds** after its source publication. The alert pipeline did not turn that available information into a fresh result notification:

1. The structured earnings row still had **NULL actual EPS** more than three hours later. Both the numeric result alert and post-result LLM generation require this field.
2. The early-news fallback already had a daily MU deduplication marker, set before the release. It permits only one headline notification per recipient/symbol/calendar day, even when the subsequent headline is the actual result rather than a preview.
3. The pre-earnings forecast is generated on demand through the app/API; there is no scheduled forecast email in the reviewed path. Both earnings AI feature flags were enabled, and a forecast cache key existed. Neither proves a fresh forecast was delivered.

These are delivery/orchestration limitations, not evidence that the system correctly predicted MU's price direction. Faster facts and reliable predictions require separate validation.

## Measured timeline

Times below are UTC; subtract four hours for EDT and seven for PDT.

| Time | Observed event | Implication |
|---|---|---|
| Approximately 00:00:28 | Existing daily MU headline marker's inferred creation time | Predates the release; inference from remaining TTL, not a send receipt |
| 17:27:01 → 17:27:06 | “Micron Earnings Ahead” preview published → ingested, category `earnings` | A preview satisfies the current classifier-based fallback; this particular preview is not established as the original marker's cause |
| 20:01:49.928 → 20:01:55.008 | Q4 EPS/revenue result headline published → ingested | About 5.08 seconds; the platform already possessed the result headline |
| 20:04:30.993 → 20:04:36.177 | Q1 guidance headline published → ingested | About 5.18 seconds; guidance was also available promptly |
| 20:02:22 → 20:04:27.564 | SEC 8-K published → ingested | Additional filing evidence available; categorized `other` |
| 21:41:38.756 → 21:41:43.858 | Earnings-call transcript headline published → ingested | Coverage extended beyond the initial release |
| 23:15:00 | MU earnings row refreshed | Still NULL actual/estimated EPS and actual revenue at the 23:24 snapshot; no generated/sent impact |
| 23:28:01 | One eligible recipient with email; daily MU marker existed with 1,947 seconds remaining | Current headline fallback suppresses another MU notification |

The official announcement scheduled the conference call for September 30 at 2:30 p.m. Mountain time, or 4:30 p.m. EDT. **That is the call time, not the earnings publication time.** [Micron announcement](https://micron.gcs-web.com/news-releases/news-release-details/micron-technology-report-fiscal-fourth-quarter-results-5).

Production stored the result from [this Benzinga headline](https://www.benzinga.com/news/earnings/26/09/62092519/micron-technology-q4-adj-eps-33-42-beats-31-45-estimate-sales-54-229b-beat-50-751b-estimate) and guidance from [this headline](https://www.benzinga.com/news/26/09/62092737/micron-technology-sees-q1-adj-eps-37-15-39-15-vs-35-07-est-sees-sales-60-000b-63-000b-vs-56-553b-est). These establish what this platform received; this audit does not independently certify every reported financial number or consensus estimate.

## Findings and remedies

### MU-01 — Results delivery depends on a slower structured-data path

`services/market-data/src/services/scheduler.py:1947`, `check_earnings_reactions`, requires non-NULL EPS. `services/event-intelligence/src/services/earnings.py:736`, `check_earnings_impact_poll`, has the same prerequisite. `_fetch_earnings_for_symbol` at line 935 uses yfinance earnings history/calendar data; a fast news headline is not mapped into a verified release event that can independently trigger these paths.

The intraday structured sync runs every 15 minutes during its configured ET window; impact generation every five minutes; delivery checks every minute. Even when the provider is ready, these independent schedules add delay. Today, recently refreshed but empty structured fields show that reducing polling alone is insufficient. Provider delay versus a provider-to-row mapping defect remains unresolved: raw provider responses were not captured by this audit.

**Remedy:** emit a sourced release-fact event as soon as the release is identified. Deliver an immediate factual alert independently of complete EPS data or LLM availability. Reconcile structured fields asynchronously and publish a clearly labeled update. Do not overwrite authoritative earnings rows with unvalidated headline parsing.

### MU-02 — Daily headline deduplication suppresses meaningful release stages

`check_early_earnings_news_alerts`, scheduler line 2305 onward, uses `stockai:early_earnings_news:{user}:{symbol}:{day}`, with a 24-hour TTL. `_fetch_earnings_news_headline` at line 2404 accepts any of the latest 20 items categorized `earnings` in a rolling 24-hour window, returning only headline text. Publication time, article identity, fiscal period and release phase are discarded.

The marker existed before today's result. It establishes suppression at the inspected snapshot, not inbox delivery or the identity of the earlier story. A rolling 24-hour search with a new calendar-day key can also select yesterday's story shortly after midnight. The calendar candidate query has a lower date bound but no upper bound, despite its comment describing yesterday/today. Future earnings rows can therefore admit pre-release news too.

**Remedy:** represent `preview`, `results`, `guidance_update` and `call_update` as distinct phases. Deduplicate by recipient + issuer + fiscal period + phase + meaningful revision, merging duplicate provider coverage. Require event-time and release-phase checks; retain publication and receipt timestamps. A preview must never consume the result phase. Replacing daily dedup with an unbounded email per headline is not acceptable either.

### MU-03 — Subscription and forecast expectations are unclear

`_earnings_alert_recipient_symbols`, scheduler line 1909, merges untriggered price alerts and durable earnings subscriptions. Signal subscriptions alone do not qualify. MU had zero durable earnings subscriptions, two active price alerts and two signal subscriptions. The eligible-price-alert union resolved to one recipient with an email, so absence of a durable subscription does **not** explain this incident. Future triggering/deletion of those price alerts could nevertheless remove eligibility.

`generate_earnings_forecast`, earnings service line 586 onward, is on demand. The forecast GET route can invoke an LLM and is not a read-only health probe. Both earnings AI flags were `1`; a MU forecast cache key existed, but its age/content was not inspected.

**Remedy:** expose an explicit durable “earnings briefing + results + guidance” subscription with visible delivery status. Offer scheduled pre-release briefings and separately label on-demand analysis. Show source cutoff, forecast creation time, expected release window and whether the release has already occurred. Never present a cached pre-release forecast as fresh post-release analysis. Do not silently subscribe users through this audit.

## Proposed event-intelligence design

1. **Ingest and normalize:** retain licensed feed/issuer/SEC provenance, publication time, receipt time, source ID and revisions. Existing Alpaca delivery was fast; first fix the consumers. Add issuer-release/exhibit coverage where measured gaps justify it, rather than assuming another feed solves today's issue.
2. **Identify the event:** issuer, fiscal period, event phase, authoritative release time and revision. A calendar helps arm monitoring but a valid unexpected release must not require an existing calendar row. Distinguish previews, actual results, guidance and transcript commentary.
3. **Validate facts:** period, currency, scale, GAAP versus adjusted EPS, actual versus estimate, current versus next-quarter guidance. Keep consensus provider and pre-release timestamp explicit. Store conflicting values with provenance and reconcile them; missing values remain missing. Treat source text as data, never as instructions to the model.
4. **Publish facts immediately:** transactional event/outbox write, recipient preferences, idempotent delivery attempts and retries. Separate queued, provider-accepted, bounced and confirmed-delivery states; inbox arrival is not inferred from `send_email=True`. LLM failure must not suppress a factual notification.
5. **Analyze asynchronously:** compare results AND guidance against contemporaneous expectations; add fresh after-hours price, spread, volume, sector/index response and source uncertainty. Generate an explanation with supported claims. A numerical beat alone does not establish a bullish trade.
6. **Update rather than repeat:** release facts, verified financial summary, material guidance changes and call interpretation are versioned phases in one event timeline. Push/in-app delivery can be primary for speed; email provides a readable record.
7. **Keep trading eligibility separate:** a sourced event is not an executable buy/sell instruction. Apply quote-age, spread, session/liquidity, portfolio-risk and strategy checks. Missing analysis must not disable independent protective exits. For options, include implied volatility, event-related volatility contraction, expiry and executable bid/ask economics; stock direction alone does not determine option profitability.

## How to improve directional reliability

Define separate targets: post-release 5/30-minute reaction, next-session close and five-session benchmark-relative return. Record when each forecast was actually available; returns cannot start at a price from before the alert/forecast existed. Pre-release forecasts and post-release reaction models require separate cohorts.

For pre-release briefings, show scenarios, implied move where valid, and historical reactions. Do not label an LLM's confidence as a calibrated probability. For post-release forecasts, use point-in-time actuals, guidance surprise, expectations, price/volume response and market regime. Permit “insufficient evidence/no trade.” A useful update may arrive after initial price discovery; measure the remaining executable opportunity, not the move already missed.

Evaluate against simple baselines on rolling, temporally held-out earnings events. Group repeated headlines/contracts by issuer-quarter event, keep overlapping outcomes out of training/promotion boundaries, and report independent event counts. Assess probability calibration, directional accuracy, net expectancy, drawdown and coverage after realistic spreads/fees/slippage. Compare facts-only versus enhanced interpretation in shadow/paper mode on the same events. September is the incident cohort; it alone should not establish a durable trading edge. Do not claim improved profits until forward results support it.

## Implementation order and acceptance criteria

| Priority | Work | Acceptance evidence |
|---|---|---|
| P0 | Phase-aware fallback and release-fact alert independent of EPS/LLM | Preview then actual release produces distinct notifications; missing structured EPS does not suppress the actual result |
| P0 | Trace today's missing structured MU result | Capture sanitized provider payload and mapping outcome; explain NULL fields without guessing provider latency |
| P1 | Event/outbox identities, delivery observability and durable preferences | Duplicate feeds merge; failures retry without losing phase or resending accepted work unnecessarily; opt-outs enforced |
| P1 | Pre-release briefing schedule and stale-forecast guard | Scheduled opted-in briefing with source cutoff; post-release request cannot masquerade as an unchanged pre-release forecast |
| P1 | Release/guidance extraction and reconciliation | Behavioral fixtures cover units, revised results, conflicting sources, missing calendars, stale previews and LLM outage |
| P2 | Calibrated event-reaction model and controlled paper trial | Versioned forward predictions, identical event cohorts/baselines, realistic fills and uncertainty intervals |

Proposed initial service objectives: ingest-to-first-factual-alert provider acceptance p95 under 60 seconds; preliminary interpretation within 2–5 minutes after sufficient evidence is available. Measure publication-to-ingestion separately, plus provider-acceptance-to-delivery where observable. These are design targets, not demonstrated current performance or promises of a tradable price.

Track every phase: published, received, classified, validated, eligible, deduplicated, queued, attempted, accepted, delivered/bounced, analyzed. Alert on a confirmed release with no result notification after its target, even if cron jobs report `ok`. Include explicit skip reasons and affected recipient counts.

## Evidence and limits

- [Production snapshot](evidence/2026-09-30-mu-earnings-evidence.json), collected by [SELECT-only probe](evidence/2026-09-30-mu-fast-probe.py).
- [Recipient/dedup snapshot](evidence/2026-09-30-mu-recipient-evidence.json), collected by [read-only probe](evidence/2026-09-30-mu-recipient-probe.py). User identifiers/emails are omitted.
- Market-data runtime scheduler hash matched the reviewed local scheduler during the investigation. This is not a full deployment-drift audit; other service code paths above are local-code findings.
- Retained market-data logs did not reveal the earlier MU headline send. The marker cannot establish which story was emailed, whether it reached an inbox, or whether it belongs to the requesting user's account. No provider delivery receipt was inspected.
- The collected news query includes symbol MU or headlines mentioning Micron; duplicate macro headlines in that result are not automatically duplicate MU notifications.
- This pass performed production read-only measurements and source review, not an implementation or regression-test run. No forecast accuracy or profitable edge has been established.

---

# Implementation — 2026-09-30/10-01

Responding to the P0 row of the order above. P1/P2 are **not** built; see the end for what was
deliberately left.

## MU-02 — implemented: phase-aware release stages

**CORRECTED after review — three overclaims below are withdrawn; see "Qualifications" at the end.**

`stockai:early_earnings_news:{user}:{symbol}:{day}` now keys on the **earnings event and the
release phase**: `…:{user}:{symbol}:{report_date}:{phase}`. A preview can no longer consume the
slot the actual print needs, and an after-hours release that crosses UTC midnight keeps one
identity across the boundary. Deliberately *not* "one email per headline", which the review rejects — each stage still
dedups to one notification, so duplicate provider coverage of the same stage collapses.

Phases, classified from headline text in the new `services/earnings_phase.py`:

| Phase | Fixture it matches (reconstructed from the captured URL slugs, **not** verbatim headlines) |
|---|---|
| `preview` | "Micron Earnings Ahead" |
| `results` | "Micron Technology Q4 Adj EPS $33.42 Beats $31.45 Estimate…" |
| `guidance` | "Micron Technology Sees Q1 Adj EPS $37.15-$39.15 vs $35.07 Est…" |
| `call` | "…Q4 Earnings Call Transcript" |
| `other` | anything unrecognised — **does not notify** |

Ordering carries the case: `call` is tested before `results` (a transcript headline contains "Q4"
and "Earnings"), and `guidance` before `results` — both real MU headlines carry `Q<n>`, `Adj EPS`
and an estimate comparison, and only the forward verb separates them. An unrecognised headline
returns `other` rather than defaulting to `results`, because defaulting would let a vague story
consume the slot the print needs — the same failure one level along.

Each stage gets its own subject and body. No stage asserts a **number**: this path exists because
structured EPS has not landed, so it reports that a stage occurred and quotes the source. Parsing
figures out of headline text and presenting them as results is out of scope, per the review's
requirement that authoritative rows are never overwritten by headline parsing.

Two further defects fixed in the same path:

- **`_fetch_earnings_news_headlines()`** (plural) replaces a fetcher that returned only the first
  match. MU published its result and its guidance as separate headlines **three minutes apart**;
  the old singular fetcher could surface at most one of them regardless of dedup.
- **The candidate query had no upper date bound.** Its comment described `{yesterday, today}` and
  it enforced only the lower half, so a symbol reporting weeks ahead counted as "pending" and
  pre-release chatter could notify as though a release were under way. Now bounded.

23 tests; 6 sabotages, each caught — including reinstating the day-only dedup key and collapsing
guidance into results.

## MU-01 — traced: **not provider latency**

The review left this open: *"Provider delay versus a provider-to-row mapping defect remains
unresolved."* Measured read-only at **2026-10-01 00:04 UTC**:

| | |
|---|---|
| MU row `report_date` | 2026-09-30 |
| MU row `eps_actual` / `eps_estimate` | **NULL / NULL** |
| MU row `fetched_at` | **2026-09-30 23:45:00** — the sync touched it 19 minutes earlier |
| Provider `earnings_history`, latest row | period end **2026-08-31**, `epsActual` **33.42**, `epsEstimate` 31.818 |

`33.42` is the figure in the Benzinga result slug. **What this establishes is narrower than it
first appears**, and the original wording here claimed too much — see the qualifications below.
Observing `epsActual` at 00:04 does not prove it was present during the 23:45 sync, and
`fetched_at` does not prove the history branch ran at all, let alone succeeded.

The precise failing step is *not* established and is not guessed at here. The likeliest reading is
that `fetched_at` was advanced by the calendar path (which writes pending rows with NULL actuals)
while the history path either did not run for MU or returned nothing at that moment — but stored
state cannot distinguish those.

So the mapping outcome is now **recorded** rather than reconstructed. Two log lines in
`event-intelligence/src/services/earnings.py`:

- `earnings.history_fetch` — row count, latest period end, latest `epsActual` as fetched.
- `earnings.history_map` — per period: resolved report date, `eps_actual`, `eps_estimate`, and
  whether a pending row was matched. **A pending row matched with `eps_actual=None` is the
  signature of this failure** and is now visible as such.

That satisfies the P0 acceptance criterion — *capture the mapping outcome; explain NULL fields
without guessing provider latency* — for the **next** occurrence. **MU-01's root cause remains
open** and is not resolved by this pass.

## Qualifications — three claims withdrawn

**1. The 17:27 preview is not established as the culprit.** The production probe at 23:28 found a
marker whose remaining TTL implied creation near **00:00 UTC**, which *predates* the preview. The
daily-key defect is established and is fixed; **which earlier headline consumed the slot is not**,
and identifying it needs evidence this pass does not have. Text above that reads as though the
preview caused it should be read as illustrating the mechanism, not as attribution.

**2. "Not provider latency" is withdrawn as stated.** Seeing `epsActual=33.42` at 00:04 does not
prove it was available at 23:45, and `fetched_at` advancing does not prove the history branch
succeeded — the calendar path writes pending rows too. What is established: the row was touched 19
minutes before the observation and left NULL. **MU-01 stays unresolved until an instrumented sync
shows an available result either mapping or failing to map.**

**3. The headline examples are fixtures, not captures.** They are reconstructed from the article
URL slugs the review recorded. An earlier version called them verbatim *and had the figures wrong*
(3.42 / 1.45 rather than 33.42 / 31.45). The numbers now match the slugs, but the wording is still
a reconstruction; classification depends on the verbs and the `Q<n>`/EPS/estimate shape, not the
figures. Nothing here should be quoted as a captured headline.

## Pre-deployment checks — all four now covered by tests

| Check | Status |
|---|---|
| Dedup identity includes the earnings event **and** phase | Key is `{uid}:{sym}:{report_date}:{phase}`, taken from `EarningsEvent.report_date`, never `date.today()` |
| One fetch with results + guidance + transcript handles all phases regardless of order | Asserted across three orderings; the loop has no `break` |
| Phase advances only on the intended send outcome | Asserted there is **no** marker write anywhere before the send, and exactly one after |
| After-hours release crossing UTC midnight | Executed through the real candidate query against a real database, both sides of midnight; TTL raised 24h → 48h, asserted as the value written |

One of those checks caught a weak test of mine: the ordering assertion searched *forward* from
`if sent_ok:` and so could not see a second, earlier marker write — a sabotage that marked the
phase before sending passed it. Now asserted as an absence.

## Second round — behaviour, not statement order

The reviewer's point stands that "no marker write before the send" is still a claim about source
text. The lifecycle is now **executed** against a fake sender and a fake Redis:

| Case | Result |
|---|---|
| Send fails (returns False) | no marker — the phase stays retryable |
| Send raises | no marker |
| Retry after a failure | succeeds, marker written |
| Retry after success | **no second email** |
| One phase fails, others follow | results suppressed; guidance and call still delivered, each with its own marker |
| Recipient with no email | skipped, no marker |

Five sabotages against that suite, each caught — including marking before the send, marking
regardless of outcome, and removing the dedup check.

**The midnight case now runs through the real query against a real database.** The candidate
query was extracted into `_pending_earnings_events(session, symbols, today)` — `today` is a
parameter precisely so the boundary is executable rather than inferred. A subprocess builds a real
SQLite database and runs it on both sides:

```
before (2026-09-30): {"MU": "2026-09-30"}      key …:MU:2026-09-30:results
after  (2026-10-01): {"MU": "2026-09-30"}      key …:MU:2026-09-30:results   identical
future event (2026-10-20): excluded
already-reported event:    excluded
two days later:            {} — the window closes
```

Four sabotages against the query, each caught.

## Headline↔event binding — the gap the key alone did not close

Correct that `report_date` in the key prevents **collisions** without preventing an old headline
being **attached** to the wrong event. The fetch was a rolling 24-hour window with no binding at
all, so whatever it returned was implicitly assumed to belong to whichever pending event existed.

`_fetch_earnings_news_headlines()` now returns `(headline, published_at)`, and
`_headline_belongs_to_event()` requires publication within **one day before through two days
after** the report date — asymmetric because a preview legitimately precedes the report while
result, guidance and call coverage trail it. **A headline with no usable timestamp is rejected**,
not assumed to belong: an unbindable headline is exactly the case this exists to catch.

## 48 hours is not idempotency — stated as a limit, not a fix

A TTL is a cache, not a delivery identity. Within the window a replay cannot duplicate, and that
is tested. **Beyond it, an already-sent phase can become eligible again** through replay, delayed
ingestion or a changed timestamp — nothing in this design prevents that, and the 48-hour figure
only widens the window past a UTC-midnight crossing.

Durable per-event delivery identities remain the real answer and are part of the P1 outbox work,
which is **not built**.

## Third round — the window is a freshness filter, not a binding

Correct, and the fix now says so in its own terms. Two additions:

**Issuer identity is carried and checked.** `_fetch_earnings_news_headlines()` returns
`(headline, published_at, symbol)` and binding rejects an item whose own symbol differs from the
one being processed. news-intelligence's endpoint already filters `symbol == :sym` exactly, so
this is defence in depth rather than a fix for a known leak — but a date window cannot tell one
company's print from another's, and the code should not rely on an upstream filter it does not own.

**An ambiguous article can no longer consume the results phase.** A genuine result headline names
its quarter — MU's did ("Q4 Adj EPS…"). `extract_period()` reads both "Q4" and "fourth quarter",
and a `results`-shaped headline that names **no** period is downgraded to `other`: not notified,
and crucially not consuming the slot the real print needs.

**Overlapping windows are tested rather than assumed.** Windows are `[event-1, event+2]`, so two
events three days apart overlap on exactly one day. The tests pin that: a headline in the overlap
binds to either event, one outside binds to neither, and the caller's event choice — not the date
— decides. Writing that test corrected an error of mine: I first asserted the overlap was at
10-01 when it is at 10-02.

Four sabotages, each caught: removing the issuer check, letting a period-less result consume the
phase, making the period extractor always return a value, and widening the window to 30 days.

## MU-01 — step 1 answered: the next sync is not soon

`sync_todays_earnings` is `CronTrigger(minute="*/15", hour="7-20", day_of_week="mon-fri",
timezone="America/New_York")`. At the time of this pass it is **21:55 EDT**, outside that window.

So the last run was **20:45 EDT** — visible as the MU row's `fetched_at = 2026-10-01 00:45 UTC` —
and it ran on the **pre-rebuild code**, so it produced no instrumentation. The next run is
**07:00 EDT**, about nine hours out.

That matters for how this is read: there is currently **no instrumented sync to inspect**, and the
absence of `history_fetch`/`history_map` lines in the log is expected rather than evidence of
anything. Checking the scheduler window first, as advised, is what made that legible.

Also accepted: `history_map` showing `eps_actual=None` is **not** by itself proof of a mapping
defect — if the provider returned no actual, the same line is the correct and honest output. The
decisive evidence is the full chain: a provider row carrying a non-null actual, its mapping
destination, the attempted write, and the committed value. `history_fetch` supplies the first
link; the rest still needs reading together with the row.

## One timestamp correction

MU's captured result was published **20:01 UTC — 16:01 EDT**. The earlier "20:01 EDT → 00:01 UTC"
example is a valid *synthetic* boundary case and is retained as one, but it is **not this
release's actual time**, and the text above no longer implies it is.

**Status: MU-02 implemented and tested, awaiting deployment verification. MU-01 instrumentation
added, root cause still open. MU-03 and the larger delivery work pending.**

## Deliberately NOT built

The P1/P2 rows are real and remain open: event/outbox identities and delivery observability;
durable "earnings briefing + results + guidance" subscriptions with visible delivery status;
scheduled pre-release briefings and the stale-forecast guard; release/guidance extraction and
reconciliation; the calibrated event-reaction model and its paper trial.

**MU-03 is not addressed at all.** Note the review's own finding that it did not cause this
incident: MU had zero durable earnings subscriptions but the price-alert union still resolved to
one eligible recipient, so absence of a durable subscription does not explain the missing alert.

Nothing here establishes that the platform predicts MU's direction. Faster, correctly staged facts
and reliable predictions are separate problems, and only the first was worked on.
