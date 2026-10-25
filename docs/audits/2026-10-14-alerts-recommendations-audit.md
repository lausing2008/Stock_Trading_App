# Recommendations implementation audit — 2026-10-14

> **Completion review — 2026-10-24:** Engineering F1–F5 is fixed; quiet hours and company shadow coverage are implemented. Five-session evidence exists, but activation/recall acceptance and actionable option integration remain open. See [current review and remaining work](2026-10-24-alerts-plan-workspace-completion.md). The dated record below is preserved.

Reviewed checkout `aa195ddf`. This is a local code and behavioral audit, not a new production
verification. No alert settings, application code, production data, or deployment changed.
Earlier deployment counts are historical evidence, not measurements made by this review.

## Conclusion

The recommendations are partially implemented. Price-risk detection and opt-in digest code
exist, but delivery should remain disabled pending the findings below and the prospective
shadow review. The prior five enrollment/schema/startup fixes remain present. General company
events and actionable options production are not complete. Better confidence reporting is not
evidence that predictive quality has improved.

## Findings

### F1 — High: live delivery has no settled-price freshness gate

`services/market-data/src/services/price_risk_watch.py:350` accepts daily bars whenever at least
two exist. Live-quote validation at line 191 returns the original series on refusal; that
series can include today's unfinished daily bar. Detection proceeds with those prices, and
the digest expiry is calculated from queue time rather than observation time.

Executed the real detector/models/audience code against local SQLite fixtures:

- October 12 run, last prices October 5, opted-in email user: two events and **one queued
  digest**, both observations dated October 5.
- Today's daily close at 90 after 100, afternoon quote rejected as `quote_stale`: **two -10%
  events** still created from the daily row. Refusing the quote does not establish freshness
  of its fallback. The fixture proves acceptance, not that a production daily row was stale.

Required: distinguish completed exchange sessions from partial daily bars; validate the
latest usable observation against the exchange calendar; retain historical detections as
history only. Give every refused input a reason. Bound delivery age by the evidence timestamp,
including at drain time. Test holidays/weekends and HK/US sessions rather than using a simple
calendar-day cutoff.

### F2 — High: removing the delivery flag does not stop queued emails

`services/market-data/src/services/scheduler.py:2695`, `drain_price_risk_outbox()`, checks user
preferences but never reads `price_risk_delivery_enabled`. The flag controls detection and
queueing only. Existing eligible rows can still be delivered after it is removed.

Executed the shipped function body with a fake Redis returning no flag and a mocked delivery
boundary: **deliver_batch called once; flag reads zero**. No provider was called. This does not
show a current production send: it requires previously queued rows and the alerting scheduler.

Required: check the delivery flag in the drain and immediately before each send. Specify
whether disabled queues are paused or cancelled and how re-enabling treats old rows. Add a
queue → disable → drain behavioral test with a provider spy and zero expected sends.

### F3 — Medium: same-session recovery and renewed decline remain suppressed

`price_risk_watch.py:270` excludes today's events when finding an open episode. Recovery state
is keyed by dates, while `_persist()` at line 492 uses subject/rule/date/step/mode identity,
with no episode discriminator.

Real detector reproduction, fresh same-session quotes after a 100 baseline:

| Quote | New events | Episodes closed | Total pullback events |
| --- | ---: | ---: | ---: |
| 91 | 2 | 0 | 1 |
| 99 | 0 | 0 | 1 |
| 91 | 0 | 0 | 1 |

The existing recovery probe tests different dates, and therefore misses this case.
Required: persist observed intraday recovery and identify episodes separately from session
deduplication. Preserve suppression of repeated readings within an episode. Test the full
sequence through `detect()`, including repeated scans and database uniqueness.

### F4 — Medium: insufficient price history silently disappears

`price_risk_watch.py:358` continues when fewer than two bars exist without persisting an
exclusion or incrementing `unmeasurable`. `/alerts/risk-history/exclusions` reads persisted
unmeasurable events, so it cannot explain this gap.

Real detector reproduction: one watched symbol with one bar gives **subjects=1, events=0,
unmeasurable=0**, with no event record. A broken or missing feed can resemble a quiet market.

Required: record missing stock/bars, insufficient history, and stale observations explicitly;
account for each scanned subject. Test the exclusions endpoint against these real records.

### F5 — Medium: basket horizon and evidence labels overstate their inputs

`services/market-data/src/services/price_risk.py:158`, `window_change()`, accepts two bars for a
five-session window. `price_risk_watch.py:450` nevertheless labels the basket with the configured
five-session horizon. It also replaces member evidence statuses with
`measured_from_member_prices`; `render_digest()` at line 686 checks only symbol events when
deciding whether to include the unverified-data note.

Real detector reproduction: NVDA/AMD/TSM/AVGO each with only `[100, 94]` produce a
`basket_slow_decline` saying **“have fallen together over 5 sessions”**. A digest rendered with
only those theme events contains no unverified-data note, despite provisional source coverage.

Required: require complete, comparable member windows or report their actual horizons; carry
member basis, coverage and quote freshness into basket evidence and rendering. Test theme-only
digests. Remove “unlikely to be wrong” unless a measured error rate supports that assurance.

## Implementation against the recommendations

| Recommendation | Audit status | Remaining work |
| --- | --- | --- |
| Central enrollment default; separate audience/email checks | Implemented in `effective_enabled()` and `alert_audience`; scheduler delegates | Keep regression coverage across settings, sender and bulk audiences |
| Event evidence column and startup DDL safeguards | Widening migration and guarded `create_all()` present | Continue real PostgreSQL regression checks |
| AQ04 price basis; AQ06 delivered cohort; AQ07 selected-signal clock | Implemented; earlier “unstarted” backlog is stale | Monitor resulting eligible cohorts; do not infer model improvement |
| Independent price-risk detection | Implemented: session, pullback, slow decline, theme breadth, intraday overlay, recovery across dates | F1/F3/F4/F5; benchmark subjects are still not general market coverage |
| Corporate-action cadence | Daily followed-symbol refresh implemented | Freshness does not supply a source completeness guarantee; response-only coverage remains provisional |
| Digest, opt-in, outbox, expiry, daily cap | Built; documented as disabled | F1/F2; quiet hours and prospective volume/latency review before activation |
| General company impacts | Not implemented; `news_impact_watch.py` has one fixed launch event key and URL | General ingestion, source verification, novelty/materiality, direct/indirect issuer roles and causal uncertainty |
| Market/sector/macro developments | Partial theme breadth only | Benchmarks and broader event coverage; morning/closing brief remain proposed |
| Options actions/playbooks | Research playbook and gating infrastructure exist; actionable production incomplete | Source live bid/ask and size, verified deliverable, account permissions and buying power; wire a production producer and accumulate prospective evidence |
| Improve AI confidence/quality | Measurement and explanation safeguards implemented | Demonstrate improvement with a baseline, chronological holdout and prospective comparison; no improved-quality result established by this audit |

AQ11 is an integration dependency, not simply a wait for more trading sessions:
`option_opportunity_notifications.enqueue_if_eligible()` exists but has no production caller.
The recorded capability matrix correctly names missing evidence. Do not relax its gate to
manufacture actions. Similarly, a supplier change is an event hypothesis; automatic positive
or negative stock impact should not be asserted merely from issuer roles.

## Next steps in order

1. Fix F1–F5 while retaining shadow mode. Add regression cases at the actual detector/drain
   boundaries, not replacement implementations of their rules.
2. Complete the five-session review already scheduled for October 21: per-session quote
   freshness and refusal reasons, per-user projected digest volume, threshold-crossing recall,
   recovery/escalation behavior, duplicates, exclusions and zero unintended outbox activity.
   As of October 14, that prospective sample cannot yet be complete. Keep the 7% rung in shadow;
   cold-start event volume alone does not justify changing it.
3. Finish quiet hours and the disable/re-enable contract; validate opted-in delivery end to end
   before enabling it. No activation or user enrollment was performed by this audit.
4. Build general company-event coverage next, preserving factual source evidence separately
   from inferred supplier/customer/competitor effects. This addresses the original missed-news
   request more directly than adding another hardcoded product rule.
5. For options, establish the missing data/account sources and one prospective long-call/put
   workflow before broader strategy activation. For AI, compare longer-history/pooled candidates
   against the current baseline with chronological evaluation and then prospective evidence.

## Validation

The focused price-risk suite passed **32 tests**. Additional local adversarial executions
reproduced all five findings; the drain check used its shipped function body with mocked
external boundaries. No real recipients, brokers or production databases were used.

Required disposable PostgreSQL/Redis integration tests passed: **94 + 2** (including the
real schema migration and startup DDL-lock regressions). Full `make test-all` passed: **8,758 backend tests**, **29 skipped**, and **588 frontend
tests**, with TypeScript typecheck passing. Existing suites remain green despite the additional
adversarial cases above reproducing the findings. The first focused invocation from the
repository root failed import collection (`src`); rerunning from the service with its correct
Python path passed. This was a test invocation issue, not a product failure.

---

## Remediation of F1–F5 (2026-10-15) — committed, NOT deployed; delivery still disabled

**Independent closure check of `d8aa9b33` (2026-10-15): F3 residual resolved.** Episode identity
is inherited by subsequent escalations and severity is scoped to the current episode. The
independent `91 → 85 → 99 → 91 → 85 → 85` detector reproduction now creates two events at the
second 85 and zero at the repeated 85. The focused suites pass **32 tests**. This closes the
code finding; production deployment, disabled delivery and scan counts below are supplied
operator evidence, not independently remeasured here. The measurement cohort is **Friday
October 16 through Thursday October 22**, with review **Friday October 23**. Earlier request-
changes/deployment advice below is retained as the historical review record.

**Independent re-review of `ebed80d3` (2026-10-15): request one further change before deploying.**
F1/F2/F4/F5 are addressed in the reviewed paths; F3 is only partially resolved. The existing
focused detector/end-to-end suites pass **32 tests**, including the added remediation probe.
The full 5,388-test run is the implementer's supplied result and was not repeated here.

**F3 residual: escalation loses the new episode's identity.** Real detector reproduction with
fresh same-session quotes and a 100 baseline:

| Quote | Behavior |
| --- | --- |
| 91 | First-episode session decline and pullback events |
| 85 | First-episode escalation events |
| 99 | Both recoveries recorded |
| 91 | Second-episode events, correctly keyed with `:after<closed-event-id>` |
| 85 | **Zero new events: second-episode escalations silently collide with first-episode keys** |
| 85 again | Zero new events |

`price_risk_watch.detect()` copies `episode_after` only when `prior.episode_closed` is true.
On a later escalation in the new episode it is false, so `_persist()` receives no discriminator
and generates the original session/step key. The suffix must remain stable for **every event
in an episode**, not only its first event. Carry that identity through `_prior_state()` and
through later escalations; keep severity/recovery comparisons scoped to the current episode.
Add the entire above sequence as a behavioral regression for session declines and pullbacks,
asserting that the second 85 emits and its repetition does not. The local independent
reproduction is `/tmp/risk-ebed80-review.py`; its durable input and result are recorded here.

**Deployment/review advice:** fix this residual, then deploy market-data in shadow so the
corrected population can accumulate. Do not combine pre-fix and post-fix measurements. Review
on October 22 only if five complete US sessions, October 15–21, were actually captured; if the
first complete session is October 16, collect through October 22 and review October 23. Calendar
date alone is insufficient if deployment occurs during a session. Delivery remains disabled.
No push, deploy, calendar edit, or production check was performed by this re-review.

The October 15 MU-02 and broker-fill checkpoint is separate and occurrence-gated. Completion
requires actual earnings-stage/dedup evidence and multi-cycle fill reconciliation evidence;
these code/test results do not close either production checkpoint.

| Finding | Fix | Regression evidence (end-to-end probe, real detector/models/outbox) |
| --- | --- | --- |
| **F1** stale prices | Only **completed exchange sessions** (`last_completed_session`, from `session_bounds` and the holiday calendars) are read; a daily row dated after the last completed session is a partial bar and is dropped, so a refused quote no longer falls back to it. Settled bars more than **1 session** behind the calendar are excluded as `stale_observation` with both dates. An event about an older session, or whose observation is older than **18h** (`MAX_EVIDENCE_AGE`, the shortest span that reaches an HK close from the 20:10 ET run), gets receipt `historical_no_alert` and is never queued. Digest `expires_at` = oldest **observation time** + 18h, so the outbox also refuses it at send time. Each event records `observed_at` and, when a quote was refused, `quote_refused`. | Oct 5 prices on an Oct 12 run: stale exclusion, no event, no digest. Partial daily 90 + stale quote: no event. One-session lag: measured, history only, nothing queued. Expiry equals close + 18h. Session-age and evidence-age guards each tested on their own. |
| **F2** queued emails after disable | `drain_outbox()` (moved out of the scheduler so it is testable) **pauses** when the delivery flag is absent — nothing claimed, rows stay pending and still expire on evidence time — and **rechecks the flag immediately before each send**; a row reached after a mid-drain disable is suppressed. Re-enabling resumes only unexpired rows. | Queue → flag absent → drain: 0 sends, row pending. Flag removed mid-drain: 0 sends, row suppressed. Flag present: exactly 1 send. |
| **F3** same-session recovery | Open-episode lookup now includes the current session, and the current reading counts as recovery evidence for an event from the same session; session declines can now close too. A new episode's key carries `:after<id of the closed event>`, so it no longer collides with the first episode, while repeated readings inside an episode still collide. | 100 → 91 → 99 → 91 → 91: recovery recorded; 2 pullback and 2 session-decline events (was 1 each); the repeat adds nothing; second event labelled "new episode after recovery". |
| **F4** silent disappearance | Every scanned subject is accounted for: `no_stock_record`, `no_price_data`, `stale_observation`, `insufficient_history` are persisted as `unmeasurable` with a reason (the fields the exclusions endpoint reads) and counted in the run's `exclusions`. | One bar → `insufficient_history`; unknown symbol → `no_stock_record`; both with reasons; run counts 2. |
| **F5** basket labels | `window_change()` requires the full window. Baskets carry `member_evidence` (status, basis, price source) and the weakest member status instead of `measured_from_member_prices`. The digest caveat now applies to theme events, and "unlikely to be wrong" is replaced by "No error rate has been measured for them". | `[100, 94]` members: no five-session basket event. Theme events `provisional` with member evidence; a theme-only digest includes the caveat. |

**Validation:** 10 sabotages, all caught (one initially masked by the evidence-age guard; a direct check of the session guard was added). Full market-data suite: **5,388 passed, 20 skipped**. Focused: detector, probe, env-gate and alert-enforcement tests 61 passed.

**Not changed:** delivery flag (absent), enrolment (none), deployment (none). Behaviour change for the shadow review: intraday runs no longer read today's partial daily row, and stale/insufficient symbols now appear as exclusions — so shadow counts collected before this deploys are not comparable with those after it.

**F3 residual — resolved (2026-10-15).** `_episode_for()` names a new episode after the closed
event it follows and makes every escalation inherit its episode's discriminator, so
`episode_after` is stable for every event in an episode. `_prior_state()` returns it, and
severity (`worst_pct`) is now computed only over events in the latest event's episode. Probe:
91 → 85 → 99 → 91 → 85 → 85 gives (pullback, session decline) counts (1,1) (2,2) (2,2) (3,3)
(4,4) (4,4): the second 85 emits for both rules, the repeat emits nothing, and both
second-episode pullback events carry the same `episode_after`. Sabotage (escalations not
inheriting) caught. Focused 32 + 61 passed; full market-data 5,388 passed, 20 skipped.
