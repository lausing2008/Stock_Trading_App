# Event impacts, alert receipts, model quality, and long-option review

Implemented locally October 12, 2026. This is a reviewed research-watch workflow. No production mail, order, trade, watchlist, or model was changed by development. Deploy the gateway, market-data, frontend, ML and news services together; apply [migration 005](../../shared/db/migrations/versions/005_news_impact_events.py) before or at service startup. The migration tolerates tables already created by `init_db()`.

## Surface RTX Spark impact watch

The reviewed event rule in `services/market-data/src/services/news_impact_watch.py` recognizes the October 7 Microsoft/NVIDIA RTX Spark shipping milestone in the existing Alpaca feed. It links to Microsoft's official device specification as corroboration. The three roles are explicit: MSFT product maker (positive watch), NVDA Grace CPU/Blackwell GPU supplier (positive watch), and INTC excluded from **this device's CPU slot** (negative competitive watch). None is a forecast return, proven revenue effect, or option recommendation. The October milestone is distinct from Microsoft's May/June processor-design announcement.

The scheduler checks the last 14 days each minute. Its Redis lease is released with a compare-and-delete token after each run, so the five-minute safety TTL does not silently reduce normal scan frequency. One event key and a unique event/user receipt prevent repeated mail. Any user following MSFT, NVDA, or INTC through a watchlist gets all three company roles in one watch; `product_impact` preference disables it. A discovered event over four hours old is stored as `historical_no_alert` and shown on News, never emailed as breaking. Fresh, eligible events create one in-app notice and, if the account has an email address, one frozen outbox message. The delivery worker leases only `product_impact` rows, separate from earnings' legacy cutover. The authenticated `GET /news-impact` endpoint shows source, company roles, matched watchlist symbols, the user's decision, and actual outbox state. `accepted` means provider acceptance, not inbox delivery. Users without an affected watchlist see `not_subscribed` for the recorded event.

This is a **reviewed rule for this event**, not an unrestricted LLM that invents competitor exposure for all news. A new product milestone requires a reviewed role rule and source. The official Microsoft blog RSS endpoint returned HTTP 403 from this environment, so the feature uses the already-running provider feed plus a reviewed official link; it does not claim automatic first-party feed ingestion. Future generic event extraction needs a reliable source and validation set.

News ticker matching now recognizes bounded Microsoft/NVIDIA/Intel name aliases, and removes the provider's false `RTX` stock tag on “RTX Spark” product headlines unless RTX Corporation, Raytheon or `$RTX` is explicitly mentioned. Historical rows are not rewritten.

## Per-user alert funnel

`options_flow_recipient_decisions` records, for each current recipient/contract/day, whether a candidate was deferred by cooldown, omitted by the email cap, rejected by the sender, or accepted. Later scans cannot downgrade `sender_accepted`. `GET /options-flow-delivery` displays recent decisions and whether the user currently has the active price-alert prerequisite for the legacy flow sender. Older rows remain unknown; the endpoint does not infer that 169 historical candidates were emails.

For product impacts, `news_impact_receipts` records user eligibility and in-app/email queue decisions; the outbox records pending, accepted, failed, unknown, expired or suppressed transport outcomes. The News page shows both layers.

## Model-strength explanation

`GET /ml/metrics` now returns the held-out test count and the reason a model's prediction is suppressed, derived with the trainer's own ordered suppression rule. The Model Accuracy table shows those values; the signal badge no longer incorrectly claims every suppression was caused by cross-validation AUC below 0.52. An `unrecorded_reason` state stays visible when an old artifact's saved flag cannot be explained from saved metrics. Signal strength remains distance from neutral and is not rescaled or presented as a win probability.

## First long-option playbook

Selecting a flow alert now opens a long-call or long-put research plan. The server chooses a canonical OCC contract from the stored Unusual Whales chain, verifies symbol/type/strike/expiry identity, selects a 14–60-day expiry near the observed underlying price, rejects missing or crossed archived quotes, and shows an archived bid/ask, premium illustration, expiry breakeven, confirmation, invalidation and exit policy. `GET /long-option-playbook` is authenticated and read-only.

**Actionable option opportunities remain blocked.** The provider's chain is a settled-session archive; it supplies no live ask size or verified deliverable. The broker adapter exposes no options permission level or options-specific buying power. The existing `actionable_gate` therefore cannot establish live quote freshness, contract terms, permissions and risk capacity. The review lists these blockers and emits no quantity or actionable email. The `option_opportunity` preference is still explicit opt-in, and no producer has been connected that would claim to satisfy missing evidence. Alpaca documents [OPRA latest option quotes](https://docs.alpaca.markets/us/v1.4.2/reference/optionlatestquotes), [option contracts](https://docs.alpaca.markets/us/v1.1/reference/get-options-contracts) and [account option-level/buying-power fields](https://docs.alpaca.markets/us/v1.1/changelog/v1-1), but access and entitlement for this app are unverified; its [free indicative option feed](https://docs.alpaca.markets/us/docs/historical-option-data) is not an executable OPRA quote. The next integration requires a licensed live contract quote/size source, verified deliverables, broker options permission and current account/position risk; only then should the producer call `enqueue_if_eligible` and capture the frozen contract in the prospective ledger. Multi-leg and short-option plans need separate payoff/assignment handling.

## Validation

The real-SQLite probe checks event detection, watchlist matching, preference opt-out, in-app/outbox writes, replay idempotency, stale-event no-mail behavior, and recipient-state monotonicity. Pure playbook tests cover long call/put selection, breakeven arithmetic and rejection of crossed quotes. The alert preference ratchet, gateway route coverage, legacy flow/earnings alert tests, news ticker tests, model suppression tests and frontend typecheck passed during development. Focused results: 80 market-data alert/playbook tests, 78 outbox tests, 14 gateway route tests, 33 ML suppression tests, 28 news ticker tests, and frontend typecheck. The SQLite probe also verifies that the product-impact worker cannot claim an earnings message. Migration 005 was smoke-tested on SQLite for additive reruns and autoincrement inserts. Deployment requires a fresh production read for table creation, event history state and outbox health before any channel can be called live.

## Remaining evidence and rollout work

The model-age column already existed in Model Accuracy; this change adds test counts and suppression reasons next to it. Versioned prospective cohorts by market, horizon and actual recipient delivery state cannot yet establish a tuning decision from the 169 historical flow candidates because their per-user delivery is unknown. Collect newly recorded decisions and outcomes prospectively, then compare a frozen baseline with sufficient held-out samples before changing fusion weights or thresholds. No threshold or model artifact was altered here.

The official Microsoft page is a reviewed source link, not an automated first-party ingestion path. A reliable licensed or accessible first-party feed and reviewed rules for future events remain integration work. The Surface rule covers this milestone only. New option action alerts similarly require a live option quote/size source, verified contract deliverable and broker options permission/account-risk facts; until those integrations exist, the playbook is research-only.

## Review before deployment — 2026-10-12

Reviewed against the full suites rather than the focused ones. Everything above held, and two
defects were found that only a repo-wide run could see. Both are in the enrolment path, which is
the part that reaches a person without being asked.

### A new scheduled job was unclassified

`product_impact_watches` runs every minute, enqueues to the outbox and drains its own channel
through `send_email` — an alerting job. `test_no_job_id_is_missing_from_either_known_list`
failed because it was declared in neither list.

The placement itself was correct: the registration sits inside `if _is_alerting_enabled():`,
verified by walking the enclosing block. So this was a missing declaration rather than a live
gating defect — but the guard exists precisely because the opposite mistake (a data job placed
behind the alerting switch, tier 425) is invisible until an environment with alerting disabled
silently stops recording. Declared, with the reasoning recorded beside it.

**This is why the focused runs were not enough.** The record above lists six targeted suites,
all of which passed; this failure sits in a seventh. The repo has a documented history of
exactly this — `AUD-T401-SHIPPED-RED` shipped to prod green under one service's pytest.

### A brand-new email channel enrolled everyone by omission

`product_impact` was added without a `default_enabled` key, and the consumer read
`a.get("default_enabled", True)`. **Absence of a row therefore meant subscribed.** On deploy,
every user holding MSFT, NVDA or INTC in a watchlist would have been enrolled in a channel they
had never seen.

Nothing would have been sent for *this* event — the October 7 milestone is older than the
four-hour window and records as `historical_no_alert` — so the exposure was the next fresh
event, not this one. That is a narrow escape, not a design.

The module's own docstring already stated the rule: *"Each type declares its default."* It was
true of **one type out of twenty-six**. Three changes:

| | |
|---|---|
| `product_impact` | declares `default_enabled: False`. A brand-new channel has no established subscription to preserve, which is the stated reason the operational alerts default on. A watchlist entry is consent to track a symbol, not to be emailed about it |
| The other 24 | now declare `True` explicitly — behaviour-preserving, and it makes the module's claim true |
| The consumer | fails **closed**: an undeclared default now enrols nobody rather than everybody |

A ratchet test refuses an undeclared default outright, so the next new type cannot inherit
enrolment by forgetting a key.

**This is a reversible product decision, not only a defect fix.** The author's text implies
default-on (*"`product_impact` preference disables it"*). It is one word to flip back if that
was intended; the defect — the undeclared default and the fail-open fallback — is fixed either
way.

### What was verified rather than taken on trust

- `make test` all twelve services, `make test-integration` 94, frontend 573 plus typecheck.
- The author's own SQLite probe passes: event detection, watchlist matching, opt-out, in-app
  and outbox writes, replay idempotency, stale-event no-mail, recipient-state monotonicity.
- All three new gateway prefixes (`news-impact`, `options-flow-delivery`,
  `long-option-playbook`) are registered in `proxy.py` — the documented 404 class.
- Migration 005 creates its three tables conditionally.
- `MAX_LIVE_AGE = 4 hours` and the `historical_no_alert` state both exist as described.
- `sender_accepted` is not downgraded by a later scan.

Four sabotages on the two fixes, each confirmed applied before being counted as caught.

### Unchanged by this review

Actionable option alerts stay blocked: no live quote or size, no verified deliverable, no
broker options permission or options buying power. The Alpaca endpoints named above are
documentation, not confirmed entitlement for this account. The playbook is research-only, and
`option_opportunity` remains explicit opt-in with no connected producer.
