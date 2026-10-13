# Market, company and price-risk alerts

Status: proposed design following an approved read-only production diagnostic. No new
channels have been implemented, enabled or sent by this investigation.

## Production findings

Checked October 12, 2026, approximately 10:00–10:02 p.m. America/Los_Angeles
(October 13, 05:00–05:02 UTC). These are application-wide observations; the diagnostic
did not identify which account belongs to the requester or verify inbox receipt.

- All 15 containers reported healthy. Signal, price, macro-reaction, earnings-impact and
  early-earnings jobs had recent `ok` completion records. A successful job can mean no
  eligible candidates; it does not prove every intended event was detected.
- There were 395 signal subscriptions across 195 symbols. Thirty-three subscription rows
  had a last-send timestamp within seven days; this is not a count of individual messages.
  The most recent timestamp was October 12 at 7:01 p.m. Pacific.
- No explicit alert preference rows were present. Effective defaults therefore matter;
  this does not mean every channel is enabled.
- The latest stored SWING model outputs included NVDA HOLD, AVGO HOLD, and AMD, MSFT,
  MU and PLTR BUY. A falling price does not necessarily change a model's direction label.
  Stored model output and subscription `last_signal` differ in some cases; this probe did
  not trace the served signal contract or every suppression decision, so those differences
  are not diagnosed as a delivery failure.
- Existing downside price alerts for NVDA, AVGO and PLTR were already triggered and
  nonrecurring. They do not provide continuing percentage-decline coverage. MU had an
  untriggered absolute-price threshold. No AMD signal subscription appeared in the focused
  query. These observations are across accounts, not a confirmed account-specific inventory.
- `theme_forecast_email_enabled` was unset. Its code requires `1`, so the weekly theme
  email is disabled even though its job reports `ok` when skipped.
- `macro_llm_reaction_enabled` was unset, which does not disable that existing channel.
  Five release-event rows within fourteen days had reaction and sent timestamps.
- The preceding 24-hour news window contained 279 macro-classified rows (140 marked
  material), 11 M&A rows (all marked material), and 22 earnings rows (16 material).
  These are stored rows, not unique verified events. Another 1,229 rows had no category;
  classification coverage needs its own visibility.
- The notification outbox was empty. Legacy direct-send paths remain in use, so an empty
  outbox does not establish that no messages were sent.

The existing signal job relies on direction transitions, sector rotation announces newly
emerging leaders, and portfolio drawdown monitors paper portfolios. None of these provides
the requested general AI-sector selloff briefing. The current product-impact watcher also
recognizes one reviewed launch rule, rather than arbitrary partnerships or acquisitions.

## Three user-facing channels

| Channel | Coverage | Suggested delivery |
| --- | --- | --- |
| Market & sector developments | Broad market and tech/chip/AI-theme moves; oil, yields and volatility; CPI/PCE/jobs; Fed decisions; tariffs, export controls, sanctions and geopolitical developments | Morning brief plus material intraday developments |
| Company developments | Earnings and guidance; major contracts, partnerships and acquisitions; product milestones; material regulatory actions, filings and financing | Material updates for followed companies; routine items in a digest |
| Price & risk changes | Percentage moves, multi-session pullbacks, support breaks, volatility/volume changes and deterioration across followed stocks | Threshold crossings and material escalation, independent of the AI direction label |

Keep three top-level controls with topic and ticker filters inside them. Existing AI signal
changes remain an identifiable event type. Earnings belongs under company developments;
an upcoming-event calendar belongs in the digest rather than a fourth alert stream.

Suggested first-version audience: market developments use selected markets/themes; company
and stock-risk notices use the user's watchlists and explicit symbol subscriptions. Include
holdings only where ownership is established; shared paper portfolios must not be presented
as a user's personal holdings. Do not require an unrelated untriggered price alert to enroll.

## Trigger and explanation rules

Thresholds below are proposed starting settings, not validated optimal values or trading
recommendations. Evaluate their alert volume against historical data before activation.

- Market moves: start with a 2% benchmark/sector daily decline, or a 4% daily oil move.
  Measure the same named instrument against its previous regular-session close. Separate
  overnight and regular-session changes. Do not substitute a leveraged ETF for its sector.
- AI-theme breadth: initially flag when at least 60% of a maintained basket falls 2% or
  more in the session, with at least five fresh observations and 80% basket coverage.
  Show the basket, coverage and sample size; AI spans multiple formal sectors.
- Stock risk: offer 3% daily and 7% five-session pullback presets, plus a volatility-adjusted
  option. Use adjusted, comparable prices for multi-session changes. Refresh only on an
  escalation or a new episode after recovery; do not repeat every polling cycle.
- Company news: distinguish rumor, announcement, signed agreement, approval and completed
  transaction. Separate an earnings reminder, published results and changed guidance.
  Source publication time and event time both matter; an old story ingested today is not
  a fresh event. Deduplicate the same event across publishers and affected tickers.
- Scheduled macro data: identify the reference period, units and revision status. Compare
  actual with sourced consensus only when available; previous is not consensus. FRED is
  a data service, not a policy decision or a substitute for the originating release.
- Explanations: show measured facts first, then sourced reporting, then explicitly labeled
  possible implications. Co-occurring oil increases and chip declines do not establish
  causation. If no supported catalyst is available, say so and still report the move.
- Relevance: list directly affected followed companies separately from possible supplier,
  customer or competitor effects. Do not invent revenue exposure or a price target.

Each notification should contain: what changed; magnitude and observation window; affected
symbols/themes; why it may matter; confirmed versus uncertain information; source links;
publication and price timestamps; and the next known event to watch when one exists.

## Delivery and observability

Provide an in-app history and independently controlled email delivery. Suggested default
cadence is a premarket brief, high-materiality intraday alerts, and an optional closing
recap. Routine news waits for the digest. Quiet hours, timezone and urgency overrides are
explicit settings. Follow-up updates require a material change, not a reworded headline.

Use a canonical event with per-recipient decisions and the existing durable outbox:
detected, ineligible with reason, queued, provider accepted, failed/retrying, or expired.
Provider acceptance is not inbox delivery; record delivery/bounce evidence separately if
the provider supports it. An email failure must not discard the in-app notice or advance
the event to a falsely delivered state. Recheck preferences at dispatch.

Expose source freshness, missing classifications, skipped jobs, last successful check and
last eligible event. A stale feed should appear as stale coverage, not a reassuring absence
of alerts. Do not suppress price-risk detection when news classification or an LLM fails.

## Implementation sequence

1. Add canonical event identity, evidence fields, per-channel preferences and per-recipient
   receipt states using the existing audience helpers, AppNotification and outbox. Migrate
   existing preferences without silently enabling new channels or duplicating old messages.
2. Add market/sector/basket and stock-risk detectors that use measured prices and do not
   depend on BUY/HOLD/WAIT/SELL changes. Reuse theme membership where suitable, with
   explicit coverage and fresh-price checks. This addresses the reported silence first.
3. Route material company and macro events from existing news/event ingestion into the same
   flow. Expand contracts, partnerships, guidance and policy classification beyond `other`.
   Reuse existing earnings and macro detection, with a common event ID to prevent duplicate
   legacy and new delivery. Broad geopolitics still needs source-coverage validation.
4. Add the three controls and a filterable alert history with evidence and delivery state.
   Compile briefs from the same events rather than running a separate untraceable narrative.
5. Validate locally, then run a production shadow period that records candidate decisions
   without delivering messages. Review volume, missed events and duplicate rate before
   enabling live delivery for an explicitly selected account.

Important checks: continued decline with unchanged AI signal; an already-consumed price
alert; sparse/stale basket data; split adjustments; market holidays and overnight moves;
unverified causation; rumor versus completed deal; recycled stories; absent consensus;
preference changes; no-email users; retries, concurrent workers and cross-source duplicates.
Exercise persistence and unique-event behavior on PostgreSQL, not only SQLite.

## Existing components and source references

- `services/market-data/src/services/scheduler.py`: signal/price rules, macro and earnings
  delivery, theme email feature gate, existing direct-send paths and job status.
- `services/market-data/src/services/theme_signals.py`: maintained themes and measured
  theme summaries.
- `services/news-intelligence/src/services/classify.py` and `storage.py`: current categories,
  materiality, source URLs, timestamps and classification deferrals.
- `services/event-intelligence/src/services/macro_reaction.py`: macro release detection.
- `services/market-data/src/services/news_impact_watch.py`: narrow product-event precedent.
- `shared/common/alert_prefs.py`, `shared/common/outbox.py`, and
  `services/market-data/src/services/alert_audience.py`: preference and delivery primitives.
- [BLS CPI release calendar](https://www.bls.gov/schedule/news_release/cpi.htm): originating
  CPI publication schedule.
- [FRED release-date documentation](https://fred.stlouisfed.org/docs/api/fred/release_dates.html):
  release-date API; availability on FRED may differ from the source release time.
- [SEC filing example](https://www.sec.gov/Archives/edgar/data/1977303/000095010325003078/000095010325003078-index.htm):
  distinct Form 8-K items for material agreements and completed acquisitions.

Existing unrelated working-tree edits were not changed by this investigation. Live work
consisted of reads only; no alerts, enrollment changes, deployment or trades were triggered.

---

# Step 1 built: price-risk detection and alert history (2026-10-12)

Scope is the first implementation choice from the plan — price-risk detection plus a clear
alert history — built to the design above and **running in shadow mode**. Company-event
coverage (step 2) is not started.

## What detects

`services/market-data/src/services/price_risk.py` is pure: no session, no network, no clock of
its own. The caller resolves prices and their adjustment evidence and passes them in. That is
deliberate — the comparable logic in this codebase was only reachable through an injected
stand-in that implemented a different rule, and the suite was green on behaviour the live
caller could not produce.

- **Session decline** and **pullback from the window peak**, on separate escalation ladders.
  Pullback measures from the peak close rather than the window's first bar, because a stock
  that rose and gave it all back has drawn down even though first-to-last is flat.
- **Overnight and regular-session changes are reported separately** when the open is known, and
  are `None` when it is not. A missing open is not a zero gap.
- **Basket breadth** across the maintained themes, carrying coverage and sample size with the
  fraction. An unmeasured member counts as *unobserved*, never as "did not fall" — treating it
  as a non-decliner biases every reading downward invisibly.

Thresholds are the design's proposed starting values. They are not validated optima, which is
precisely what the shadow period is for.

## The anti-noise rule

A move is reported at the highest ladder step it clears, and re-reported only on a **higher**
step — otherwise "still down" is true on every polling cycle and the channel becomes something
people mute. The third case is the one usually missed: a decline that **recovers and resumes**
is a new episode, because a high-water mark alone never resets, and without it a stock that
drops 9%, recovers, and drops 9% again is silent the second time.

## Two defects I introduced and caught before shipping

**The breadth rule was dead on arrival.** `BREADTH_MIN_OBSERVED = 5` looked like reasonable
rigour, but every maintained theme holds three or four symbols — so **0 of 8 baskets could ever
satisfy it**. Same shape as this repository's own BUG-SA33-UNREACHABLETHRESHOLD. The minimum is
now capped at the basket's own size, leaving the 80% coverage floor to do the real work (a
four-member basket needs all four, because three of four is 75%).
`test_every_maintained_basket_can_actually_trigger` computes the most favourable possible input
for each real basket and fails if any cannot fire.

**A silent fallback switched breadth off entirely.** Theme membership was read through
`theme_signals`, which drags an LLM client, Redis and the ORM, inside a
`try/except: return {}`. When that import failed, breadth detection was disabled while looking
exactly like "no theme fell". The baskets now live in `theme_baskets.py`, which imports nothing,
and the handler is gone — an import that cannot succeed is a deployment fault and should say so.

## Evidence, and what cannot be measured

Every event carries its price basis and evidence status through the AQ04 contract. A corporate
action inside the window that cannot be applied makes the number wrong — a 2:1 split reads as a
50% crash — so **no event is raised from it**. It produces an `unmeasurable` record instead,
because reporting nothing would be indistinguishable from "nothing happened", which is how a
broken feed looks exactly like a quiet market.

## Shadow mode, and what "shadow" means on the row

`detect()` writes `RiskEvent` and `RiskEventReceipt` rows and **never enqueues or sends** —
verified behaviourally, including with delivery enabled. The job therefore sits on the
non-alert side of the scheduler's gating, so the measurement accumulates even where mail is
switched off; if a send path is ever added it moves, and a test asserts the no-send property
rather than trusting that note.

`delivery_mode` is on the event because "detected but deliberately withheld" and "nobody was
eligible" are different facts that look identical from outside. The flag is read from Redis and
**absent, unreadable or missing client all mean shadow** — a channel that starts delivering
because a flag could not be read is the same failure as a preference defaulting to subscribed.

Both preference types default OFF, and `price_risk` / `price_risk_in_app` are separate keys
because the design asks for independent control and this table stores one boolean per type.

## History

`/risk-alerts`, with `GET /alerts/risk-history` and `/summary` under the already-routed
`/alerts` prefix. Events are LEFT-joined to the viewer's receipt, so a detection this account
was not an audience for still appears — dropping those would turn the page into "what I was
sent", a much less useful question while the channel is deliberately not sending. The summary
reports volume per day, distinct subjects, per-rule counts and what could not be measured,
which is what the shadow review needs.

## Alongside: options-history transactions shortened

`get_chain()` ran a `SELECT` — which opens a transaction that stays open until commit or
rollback — and then made Unusual Whales requests inside it, so the connection sat
`idle in transaction` for the 30–35 seconds observed in production, holding locks and an MVCC
snapshot on somebody else's network latency. The read transaction is now released before the
provider call, and **only when the session has nothing pending**: the session belongs to the
caller, and discarding their uncommitted work to tidy up a read would be a worse bug than the
one being fixed.

## Testing

Nine sabotages, each verified applied first, including reinstating both defects above. 20 pure
detector tests, an end-to-end SQLite probe using the real audience gate with nothing injected,
and a real-session transaction probe that reads `session.in_transaction()`.

The enforcement ratchet in `test_ea05_...` was reworked rather than worked around. It matched
alert types as string LITERALS inside three hand-listed files, so a named constant read as
unenforced and a fourth delivery path was invisible to it. It now resolves module-level
constants and scans the service directories, which removes a maintenance list that has to be
edited whenever the thing it guards grows.

**Not built, and deliberately:** company-event coverage, the brief/digest compilation, quiet
hours, and the live send path. The shadow review comes first.

---

## Deployment and the first shadow reading (commit `8597f1b6`)

15/15 healthy, **0/12 drifted**, `/risk-alerts` 200, both API routes 401 (routed and guarded),
tier 435 in the served chunk. `risk_events` and `risk_event_receipts` were created **with**
their unique constraints — `create_all()` builds a NEW table whole; it is adding to an existing
one that it silently skips.

One detection was run against production. Delivery flag absent, so shadow:

```
subjects 157 | events 93 | unmeasurable 17 | receipts 52
pullback         55 events  avg -14.99%  worst -42.47%
session_decline  38 events  avg  -6.33%  worst -23.70%
evidence_status  unverified: 110   verified: 0
receipts         shadow_suppressed: 52      (nothing sent)
```

**A second run produced 0 new events and 0 new receipts**, so session-scoped identity holds
against a real unique constraint, not only against SQLite.

### Three readings, and what each does and does not support

**93 events is NOT the steady-state rate, and must not be quoted as one.** It is a cold start:
with no prior events, `should_emit` answers "first observation" for every subject, so the
escalation ladder suppressed nothing. The rate that matters is the second and later sessions,
which needs more days before it means anything. Reporting the first-day number as a daily rate
would overstate volume by whatever the ladder turns out to suppress.

**Nothing is verified, and the cause is measured rather than guessed.** Corporate-action
coverage is evidenced through **2026-10-08** while the latest daily bar is **2026-10-13**, so
every price window runs five days past the evidence and `resolve_window` correctly declines to
call it verified. 203 symbols have coverage — this is a *freshness* gap, not a missing-data gap.
Until the action ingest keeps `evidenced_to` level with the price bars, the verified path is
unreachable in production and every figure here stays labelled unverified. The same applies to
the AQ04 calibration basis, which shares this contract. **Not fixed here:** the cadence is an
ingest-cost decision, not a code defect, and it is stated rather than quietly worked around.

**17 subjects could not be measured at all** — a recorded corporate action inside the window
with no applicable ratio. They are recorded as `unmeasurable`, which is the point: without
those rows a reader cannot tell a broken feed from a calm market.

### What the shadow review still needs

More sessions, for the post-ladder rate; a judgement on whether a 7% pullback floor over 15
sessions is the right first rung given 55 of 157 subjects cleared it; and the coverage-freshness
decision above. None of that is a code change yet, which is why delivery stays off.
