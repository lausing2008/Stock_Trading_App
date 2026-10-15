# Market, company and price-risk alerts

## Status (updated 2026-10-13)

| Part | State |
| --- | --- |
| Price & risk changes — detection | **Deployed in shadow.** First version 2026-10-12 (`8597f1b6`); the Step 1b rework and its review fixes (`0b07193a`, `2590aa20`) deployed 2026-10-13 ~20:00 ET. Collecting the 5-session shadow sample. |
| Price & risk changes — email/in-app delivery | **Built, not enabled.** The delivery flag is absent and no account is enrolled; nothing has been sent. |
| Market & sector developments | Proposed only (basket breadth is the first piece of it). |
| Company developments | Proposed only. |
| Morning brief / closing recap | Proposed only. |

The sections from "Production findings" to "Existing components" are the **original design**
(2026-10-12) and describe intent. Current behaviour is in the dated build sections at the end;
where they differ (for example, the pullback window is 5 sessions, not 15), the build sections
are correct.

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

---

# Step 1b: reliability fixes, slow declines, intraday shadow, delivery path (2026-10-13)

**Trigger.** The AI names fell for a week and the user heard nothing. Three causes, measured
against production rather than assumed. They explain the silence **of this detector only**;
the AI-signal and price-alert paths are separate and were not re-diagnosed here.

| Stock | Peak → Oct 13 | What the deployed detector did |
| --- | --- | --- |
| AMD | 649.42 → 588.32, −9.4% | Detected (pullback), withheld: shadow |
| MU, TSM | −9.1%, −8.5% | Detected, withheld: shadow |
| NVDA | 239.24 → 225.33, −5.8% | **Nothing.** No day worse than −1.3%; pullback rung starts at −7% |
| AVGO | 376.51 → 355.78, −5.5% | **"Unmeasurable"** — a $0.65 cash dividend in the window |

## What changed

**Cash dividends (AUD-PRICERISK-DIVIDEND).** `classify_observation_window` marked a window
`known_corrupt` for *any* recorded action whenever coverage was stale — including a cash
dividend, which `resolve_window` itself never treats as corrupting. Production holds 744 cash
dividends and 15 splits, so this silenced most "unmeasurable" subjects (17 → 0 in the dry run).
Now:

- **Cash dividends are the one exception, by allowlist** (`PRICE_NEUTRAL_ACTIONS`). Every
  other action in the window — split, stock dividend, spinoff, rights issue, or a type this
  module has never seen — still makes it corrupt unless it is actually adjusted. *(Corrected
  after review: the first cut blocklisted share-count types instead, so a spinoff window came
  back `usable`. The covered branch of `resolve_window` had the same gap before this work — it
  applied splits and passed any other type through as "adjusted" — and now refuses with
  `limiting_factor = unapplied_action`.)*
- Cash dividends are **named** in the result (`cash_dividends`), never silently dropped.
- Price-risk detection **adds them back** (`price_risk.dividend_adjust`, standard
  back-adjustment), so an ex-date drop is not reported as a holder's loss. The event records
  each dividend applied; the basis reads `...+cash_dividends_added_back`.
- A dividend with **no recorded amount** cannot be added back, so a window straddling it is
  `unmeasurable` with `limiting_factor = dividend_amount_unknown` — not assumed to be zero.
- **Side effect on AQ04 calibration** (signal-engine shares this function): windows that
  contained only a cash dividend under stale coverage were being dropped as corrupt; they are
  now kept, labelled `unverified`, like any other window without a share-count action. That is
  the population the function's own docstring describes.

**Slow decline (AUD-PRICERISK-SLOWBLEED).** New rule `slow_decline`: first-to-last change over
5 trading sessions, ladder −5 / −8 / −12 / −18%. **−5% is a starting value to evaluate, not a
validated one.** It is suppressed whenever a pullback already reports the move (the peak is
never below the first bar), and while a pullback episode for the same stock is still open —
otherwise a −7% pullback easing to −6.5% reappeared as a "new" slow decline.

Runtime-configurable without a deploy, Redis key `stockai:admin:price_risk:config`:

```json
{"slow_decline_sessions": 5, "slow_decline_steps": [5, 8, 12, 18], "basket_slow_member_pct": 4}
```

Invalid values fall back to the default **and are named** in the job's `config_errors`.

**Multi-day basket breadth.** New rule `basket_slow_decline`: ≥60% of a maintained basket each
down ≥4% over the same 5-session window. The one-day rule needs most of a basket down 2% *on
the same day*, which a week-long grind never satisfies.

**Recovery is persisted (review item 1).** `should_emit` read an `episode_closed` flag nobody
wrote, and checked it *after* the "same episode" return, so a decline → recovery → second
decline was silent forever. Now each run, for each open pullback/slow-decline episode, checks
whether any later close regained half the drop and **writes** `episode_closed`,
`episode_closed_on` and `episode_closed_by` (`price_recovery` / `age` / `breadth_receded`) to
the event that opened it. The flag is checked first in `should_emit`. Session rules
(`session_decline`, `basket_breadth`) are one episode per session: two −4% days are two moves.

**Basket evidence (review item 2).** Breadth read raw closes for any member without a
candidate, including members the evidence contract had just refused. Now breadth reads only
moves measured from adjusted prices; an unmeasurable member is **unobserved**, and so is a
member whose latest bar is older than the basket's newest session.

**Intraday scanning, shadow (review item 3).** The job now also fires 10:05, 12:05, 14:05 and
15:40 ET, and 11:30 / 15:30 HKT, besides 20:10 ET. Intraday runs append today's price from the
shared `stockai:live_prices` cache only when all four hold: the venue is in its regular session
(`is_regular_session`, so holidays fall out); the row's `fetched_at` is at most **10 minutes**
old (and not more than 2 minutes in the future); the row's provider `session_date` is the
venue's date today; and its previous close matches the last settled bar within 0.5%. Each
refusal is counted by reason in the job's `quote_refusals` (`quote_unproven`, `quote_stale`,
`quote_wrong_session`, `quote_mismatch`).

*Corrected after review:* the first cut used the previous-close match alone. That proves a
quote belongs to today's session, not that it is current — a row cached at 09:40 still matches
at 15:40. `_fetch_live_bulk` now stamps every row with `fetched_at` (taken *before* the
request, so it can understate freshness but never overstate it) and `session_date` (the
provider's bar date). Rows from the single-symbol fallback get `fetched_at` only, because that
path can read `fast_info`, which has no bar date — so they are refused for intraday use rather
than guessed. **Not proven:** the provider's own delay; Yahoo's daily download has no quote
time, so a fresh fetch can still be a ~15-minute-delayed price. Intraday events say
`[intraday quote, possibly delayed by the provider; the session is not over]`. Event keys now include the ladder **step** (a worse reading later the
same session is an escalation, not a duplicate) and the **mode** (a shadow row proves
detection, not that anyone was told).

## Delivery path — built, NOT enabled

- **Enrolment is per account.** Both `price_risk` (email) and `price_risk_in_app` default
  OFF; the delivery flag `stockai:admin:feature:price_risk_delivery_enabled` alone reaches
  nobody. An account is in the audience if either channel is on.
- **One digest per user per run**, never one email per symbol; one in-app notice alongside.
- **Duplicate suppression:** the outbox id is a hash of the event keys in the digest, so a
  re-run queues nothing new. Receipts carry the `outbox_event_id`.
- **Daily ceiling:** 6 price-risk emails per user per rolling 24h; over it the receipt says
  `email_capped` rather than dropping silently.
- **Retries, expiry (12h), opt-out recheck at send, the acceptance/crash gap:** the outbox's
  own (`common.outbox`, `earnings_outbox.deliver_batch`).
- **Split send:** detection only *queues* (non-alert side, so dev stacks keep measuring).
  `drain_price_risk_outbox`, every 2 minutes behind `_is_alerting_enabled()`, is the only
  sender — `price_risk_outbox_drain` is in the env-gate test's alert list.
- Content: measured facts only; says explicitly that it is not a trade signal, that the AI
  label may not have changed, and that the alert does not establish *why* the price moved.

## Validation

- 31 pure detector tests; 12 shared price-basis tests; the end-to-end probe (real models,
  real audience gate, real outbox, now with `autoflush=False` like production) covers
  dividends, slow decline, config, persisted recovery, stale/unmeasurable basket members,
  intraday freshness and escalation, digest, dedup, in-app-only, shadow-vs-live and the cap.
- **Nine sabotages, all caught**: dividend counted as corrupt; recovery not written; closed
  check behind the ladder; stale member counted; slow rule removed; step dropped from key;
  quote freshness check removed; cap removed; dividend not added back.
- **Review round (second commit), six more sabotages, all caught**: blocklist instead of
  allowlist; `resolve_window` passing spinoffs; quote age check removed; future-skew check
  removed; session-date check removed; missing stamps tolerated. Plus
  `test_live_price_provenance.py` for the cache writer. `make test`: all services passed.
- **Production dry run** (new code against live data and the live-price cache, delivery
  forced off, transaction rolled back — nothing written). Run *before* the quote-provenance
  fix; until the stamped cache writer is deployed, every intraday quote is `quote_unproven`:

  | | Before (deployed) | After |
  | --- | --- | --- |
  | Unmeasurable subjects | 17 | 0 |
  | NVDA | nothing | slow decline −5.6% |
  | AVGO | unmeasurable | slow decline −5.3% |
  | AI / GPU basket | nothing | basket slow decline, −7.1% avg |
  | Cold read, all open episodes | 93 | 97 (65 pullback, 23 session, **8 slow**, 1 basket) |
  | Increment over existing shadow history | — | 39 events |

  The slow rule adds 8 subjects after de-duplication; before it, 72 of 157 cleared −5%.

- **A defect the dry run found and the tests had missed:** a basket event wrote one receipt
  per declining member, de-duplicated by a SELECT that cannot see unflushed rows. Production
  sessions use `autoflush=False`, so a user watching NVDA, AVGO and TSM hit the unique
  constraint and the whole run would have aborted. Fixed (one receipt per user, matched
  symbols merged); the probe now runs `autoflush=False` so it cannot hide again.

## Go-live criteria (review item 4) — for enabling email

Measured over **at least 5 US sessions** of intraday shadow, from `/risk-alerts` and the
receipts table:

1. **Volume:** after the cold start, median new events per session that would reach the
   enrolled account ≤ ~10, and digests per day ≤ the cap (6) on a normal day.
2. **Freshness:** every intraday event has `price_source = live_quote` *or* settled bars;
   `quote_stale` counted, and investigated if above a few percent of subjects.
3. **Detection delay:** a move visible in the live cache is recorded by the next scheduled
   run (≤ ~2h during the session).
4. **Duplicates:** zero repeated (subject, rule, session, step) events; zero slow-decline
   events while a pullback episode is open.
5. **Re-alert:** at least one observed `episode_closed_by = price_recovery` followed by a
   correct new episode, or the probe case accepted as sufficient.
6. **Not over-suppressed:** compare episodes reported against a plain count of subjects that
   crossed each threshold; a falling event count alone could be excessive suppression.

Then: set the delivery flag and opt in **one** account (email + in-app), watch one session,
and only then consider anyone else.

## Deployment (2026-10-13, commit `2590aa20`)

`scripts/deploy.sh` (image rebuild + `--no-deps --force-recreate`, health-gated), in six
batches so no two-digit fleet restart could repeat AUD-DEPLOY-MIGRATIONCONVOY: market-data →
signal-engine → api-gateway → ml-prediction, research-engine, ranking-engine → strategy-engine,
technical-analysis, portfolio-optimizer → event-intelligence, news-intelligence,
decision-engine. Every batch exited 0 and healthy. Afterwards: **15/15 containers healthy,
`check_deploy_drift.sh` 12/12 OK (0 drifted)**, site 200, `/api/alerts/risk-history` 401
(routed, guarded). No schema change. Frontend not rebuilt (no frontend change).

Verified inside market-data: the new code is loaded; the delivery flag is **absent** (shadow);
`run_price_risk_detection` and `drain_price_risk_outbox` are registered — the drain has nothing
to send, because nothing can be queued while the flag is absent.

**First scheduled shadow run on the new code** (20:10 ET, settled bars): 157 subjects,
35 events (15 session decline, 14 pullback, 5 slow decline, 1 basket slow decline),
**0 unmeasurable**, 17 receipts all `shadow_suppressed`, **0 outbox rows**. The AI/GPU basket
fired at −5.97%; MU at pullback step 2 (−12.4%). NVDA and AVGO were correctly *not* flagged:
both bounced on Oct 13 and ended the 5-session window at −4.48% and −4.89%, just inside the
−5% starting threshold, though intraday they had been below it. AMD and TSM were correctly not
repeated (still step 1 of the pullback episodes opened Oct 12). Names hovering at the
threshold is exactly the evidence the shadow review exists to weigh.

**Not yet verifiable at deploy time:** the cache stamps. The deploy finished after the US close,
when `stockai:live_prices` is empty, so `fetched_at`/`session_date` on real rows and the first
`intraday_quotes` > 0 are checked at the next regular session (HK or US).

## Not done

- Benchmarks (SPY/QQQ/SOXX) are not yet subjects; only watchlist symbols and theme baskets.
- Company-event and macro channels, morning brief, closing recap, quiet hours.

## Audit 2026-10-14 remediation (F1–F5)

Five defects found by [the 2026-10-14 audit](../audits/2026-10-14-alerts-recommendations-audit.md),
all reproduced through the real detector despite green suites, fixed on 2026-10-15 and recorded
there in full: settled-session freshness and evidence-time expiry (F1), the drain pausing and
rechecking the delivery flag before each send (F2), same-session recovery with episode-aware
event keys (F3), an exclusion record for every subject that cannot be measured (F4), and
full-window baskets carrying member evidence and the caveat (F5). **Delivery remains disabled.**
These change shadow counts, so the Oct 21 review must treat pre- and post-deploy sessions as
separate samples.
