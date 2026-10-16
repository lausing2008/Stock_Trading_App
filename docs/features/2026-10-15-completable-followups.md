# Completable follow-ups — 2026-10-15

This release implements the remaining independent engineering work from the alert and copilot reviews. It does not complete evidence gates, enable price-risk delivery, enroll users, place trades, or promote a calibration model. Tracker entries 446–449 distinguish shipped engineering from those open decisions.

## 446: deterministic Game Plan card

The stock page now uses the authenticated copilot plan endpoint instead of asking a browser model to invent a JSON plan. It works without an AI key and regardless of the current BUY/SELL/HOLD call. Entry, held-position and calls/history modes render the same code-written rules, refusals, source evidence and caveats as the copilot. A held stop remains user-stated; a calculated stop remains a candidate, never a maintained trailing stop.

Packet v6 adds numeric, citable levels. Fresh levels can be drawn on the daily chart; stale and unsupported levels are excluded. Trade Board snapshots preserve source fingerprint, observation time, currency, rules and explicit above/below entry conditions. They place no orders. Stops and targets can create ordinary alert **drafts** through `POST /copilot/plan/{symbol}/draft`; the server recalculates the packet, rejects changed fingerprints and stale levels, and chooses the price and direction. Drafting does not create an alert. The existing owned, expiring, idempotent confirmation flow performs the eventual save. Fixed-price intraday alerts explicitly do not evaluate a daily-close rule or follow a recalculated stop. Replays cannot change the payload, and a separate daily deterministic-draft limit bounds storage without consuming paid answer quota.

Requests are authenticated, symbol-scoped and cancellable in the card. Old-symbol responses cannot update the current chart. No paid model call is required.

## 447: price-risk quiet hours

An account-owned policy records enabled/start/end/IANA timezone in Redis and is edited on Risk Alerts. It does not subscribe the user or enable global delivery. Local-time intervals include start and exclude end; overnight and daylight-saving repeated hours are supported. The sender rechecks the policy for each eligible queued item. Quiet or unreadable policy means **defer**, not send or suppress. Existing observed-price expiry still applies, so a message can expire before quiet hours end. The global delivery switch remains off.

These changes affect delivery scheduling, not risk detection. The Oct 16, 19, 20, 21 and 22 observation cohort and Oct 23 review remain unchanged. Earlier detections must not be combined with that cohort.

## 448: company-event shadow coverage

A locked scheduler job classifies existing stored headlines for guidance, regulation, contracts, partnerships, products, leadership and earnings. It records existing news-impact events and watchlist receipts only; there is no notification, outbox or email path in this producer. Provider association and explicit headline wording are unverified evidence, not established business effects. Supplier/displacement wording yields a directional **watch**, never a price prediction. Revenue magnitude, stock reaction and causal attribution remain unmeasured.

Negated or speculative supplier wording retains an unmeasured association rather than a directional role. Events require a known tagged symbol and an HTTP(S) source URL; malformed URLs and unbound symbols are counted as exclusions. Headline/date identity deduplicates provider copies. Unknown company names, short symbols, untagged news, ambiguous language and information absent from the stored feed can still be missed. This is bounded heuristic coverage, not complete news intelligence or a substitute for source review. Fresh receipts are shadow-suppressed; older than four hours are historical. Followed-only reads filter before the result limit, so unrelated global events cannot crowd out a user's history. The News page can also show global coverage with explicit evidence and delivery state.

## 449: chronological calibration research

Signal Accuracy exposes a manually requested, authenticated comparison of 30-day market, 180-day market and 180-day pooled candidates. The default uses verified outcomes only; provisional research requires an explicit checkbox. Unknown/unverified outcomes remain excluded. Nothing changes production scores, weights, thresholds or recommendations.

The database adapter requires the frozen first BUY/SELL confidence, direction and timestamp to match the outcome's signal date. Mutable current signal scores are never substituted. Missing/mismatched snapshots are counted. Training labels must have resolved before each held-out prediction; horizon, direction and 5/10-calendar-day windows remain separate. The last fifth of signal dates is held out. A minimum of 30 preceding labels is only an availability floor. Shrunk score-bin probabilities are compared by Brier score on the **same common test rows**; unavailable metrics remain null, never zero. No winner is selected.

The read is bounded to 180 days and 30,000 observations. Overlapping outcomes are correlated, cross-market pooling mixes regimes, and diagnostic error does not establish improved returns. More prospective, independently verified evidence and a separate promotion decision are still required.

## Remaining work and decisions

- Price-risk: accumulate five complete post-fix sessions, then review Oct 23 volume, exclusions, repeated episodes, adjusted evidence and the initial 7% pullback rung. Do not enable delivery before that review.
- Corporate actions / AQ04: verification remains constrained by coverage freshness; approve and fund the ingest cadence before calling outcomes verified.
- MU-02: actual earnings-release delivery-stage evidence is still required. Broker fills need multiple real polling cycles. Unit tests cannot close either checkpoint.
- Options / AQ11: live sized bid/ask, contract deliverable, account permission and buying-power sources remain missing integration work; do not synthesize these facts or bypass the gate.
- Company events: review source quality and false positives/negatives before defining a consented digest or send path. Current coverage stays shadow-only.
- Copilot: chart commands are a separate future interaction. Current release provides level overlays and explicitly confirmed alert drafts.
- Calibration: inspect eligibility/exclusions first; no confidence uplift is justified by this engineering release alone.

## Validation and deployment

Completed checks: research-engine 694 passed / 8 skipped; signal-engine 610 passed / 1 skipped; frontend 599 passed and TypeScript checking passed; copilot plan integration on disposable PostgreSQL 73 passed. Market-data: 5,401 passed / 20 skipped; required PostgreSQL schema/DDL checks: 2 passed. The final company-producer regression (malformed URLs and negated/speculative wording) and six Game Plan UI tests were rerun successfully after the last refinements. Skips are reported, not counted as passes. Source commit and production checks follow after deployment. Release scope is market-data, research-engine, signal-engine and frontend; shared sources and schema are unchanged. Build/restart backends sequentially under the deployment lock. Verify all services, backend drift, guarded routes, served tracker bundle and the disabled delivery flag. Production smoke checks must not create alerts, messages or trades.
