# Email remediation follow-up — September 28, 2026

**The original AI Signal NameError is repaired, and optional badge failure no longer blocks the normal send path. The remediation is not fully closed: a new exception-handler bug aborts the signal batch, price retries can fabricate the displayed current price, and technical notifications are not reliably distinguished from pending retries.**

Reviewed `f55b7f93`, implementation `c2703484`, against the [email audit](2026-09-28-email-alert-system-audit.md) and [remediation report](2026-09-28-email-audit-remediation.md). Date is America/Los_Angeles. Application and `CLAUDE.md` remain unchanged. No emails, production mutations, or deployments were performed.

## Verification and limits

- Six targeted test files: **110 passed**. These cover the new email tests, preference ratchet, dedup/isolation, decision gate, options flow, and game-plan handling.
- New offline probes execute the actual signal and price scheduler functions with controlled provider/database results. Two eligible signal transitions successfully reach the sender despite a deliberately failing cohort badge, confirming that specific fix.
- Four defect probes confirm the new batch exception, retry price fallback, ratchet false positive, and missing flow side classified as complete dominance.
- The 69 consumed transitions, 812 processed signal events, zero deployed NameErrors and latest deployment drift are **Claude-reported**, not independently recounted in this continuation. Processed signal events are not interchangeable with provider-accepted emails.
- These checks are not an SMTP, inbox, PostgreSQL concurrency, or browser integration test. The old defect script is preserved; assertions that intentionally require the old bugs should fail after repair and cannot serve as a release gate.

Evidence: [executable follow-up](evidence/2026-09-28-email-remediation-followup.py), [results and source hashes](evidence/2026-09-28-email-remediation-followup.json).

## EF-01 — P1: the new signal exception handler raises another exception

**Source:** `services/market-data/src/services/scheduler.py:7890` and `:7968`, `check_signal_alerts`.

The sender's exception is bound by `except Exception as _send_exc`. After that handler exits, Python clears the exception target. The later `_send_raised` branch tries to log `str(_send_exc)`, raising `UnboundLocalError` before it can complete the per-recipient handling. The outer job handler catches it and stops the batch.

**Reproduction:** two eligible BUY→SELL subscriptions, first sender deliberately raises. Only one sender invocation occurs, and the outer job reports:

```text
cannot access local variable '_send_exc' where it is not associated with a value
```

Neither transition is consumed in this fixture, so the original five-error discard is improved. However, the second recipient is never attempted. A persistent error in an early subscription can repeatedly starve later ones. If earlier sends succeeded, aborting before the final commit also complicates durable deduplication.

**Fix:** capture an ordinary error string inside the exception handler, initialized before the try, and log that string later; alternatively complete exception handling inside the handler. Keep each recipient isolated. Use typed delivery outcomes rather than infer permanent/transient/programming failure solely from a boolean versus exception.

**Acceptance:** first recipient raises, second succeeds; job completes its loop, first transition stays pending, second delivery is recorded. Repeat over multiple scans. Also test mixed raised errors and false returns: the shared counter currently accumulates both, and a later false return can still reach the five-failure discard branch. A transient provider failure should not erase the event after a fixed number of attempts.

## EF-02 — P1: a price retry substitutes the threshold for the current price

**Source:** scheduler `check_price_alerts`, lines 8258 and 8386.

The new `_undelivered` query loads triggered alerts with no send timestamp. Price fetching still includes only **untriggered** alerts. For a retry-only symbol, `prices.get(_ua.symbol, _ua.threshold)` then supplies the configured threshold as the current price.

**Reproduction:** a pending alert on RETRY has threshold 90; the controlled provider has price 123. The scheduler requests an empty symbol list, then sends `price=90`. This is not merely a stale quote: no quote supports that displayed value.

**Fix:** persist original trigger time, observed value, condition description and units. A delayed message should say when the event occurred and display that observed value. Fetch a separately timestamped current quote if useful, including retry symbols in the request; otherwise show current price unavailable. Never use a configured threshold as an observation.

**Acceptance:** retries with no newly active alerts, absent provider quote, a price that crossed back, and a delayed indicator event. Verify the rendered body and provenance, not just a second sender call.

## EF-03 — P1: technical and historical sends are misidentified as undelivered

**Source:** scheduler `_undelivered` selection in `check_price_alerts`, lines 8240–8250; `check_technical_alerts`, around 8750–8800.

This is a source-traced residual/new interaction, not a separate whole-technical-job reproduction:

1. The retry query does not restrict the condition family. One-shot technical alerts also set `triggered=True` and `triggered_at`.
2. Successful technical sends still do **not** set `last_sent_at`. A successfully sent one-shot MACD/EMA alert therefore satisfies the new price retry query and can be emailed again by another job.
3. The retry does not preserve the technical trigger's computed description or value. It supplies the raw enum, original threshold and possibly the threshold-as-price fallback from EF-02.
4. Recurring technical alerts still stamp `last_sent_at` before delivery and stay untriggered. A failure is consequently excluded from this retry mechanism and can be suppressed until the next daily allowance.
5. Before this deployment, successful ordinary price sends also left `last_sent_at` null. Recent legacy rows are ambiguous: null does not establish that they were never sent. The 24-hour window bounds the ambiguity but does not resolve it.

**Fix:** persist an immutable notification event with explicit delivery state and payload. As a narrower interim repair, align all participating senders around one success-marking contract, restrict retries to correctly represented event families, and define migration handling for historical rows with unknown delivery. Do not automatically interpret every legacy null as failed delivery. After the retry TTL, record expiration explicitly rather than silently dropping the event.

**Acceptance:** a successful one-shot technical send is not repeated by the price job; a failed recurring send retries without regenerating the trigger; pre-deploy successful sends do not become pending solely because their timestamp was never populated. Preserve the event's exact condition and value through retry.

## EF-04 — P2: the preference ratchet can pass without executable enforcement

**Source:** `services/market-data/tests/test_ea05_every_alert_type_is_enforced.py:44`, `_is_enforced`.

The ratchet uses regexes across raw source and accepts a matching helper call **anywhere** in either file. It does not require executable code, a particular sender path, control flow before delivery, or use of the helper's result.

**Reproduction:** in an isolated in-memory copy of the test module, replace scheduler source with only:

```python
# _may_send(session, user, "brand_new_type")
```

`_is_enforced("brand_new_type")` returns true. No repository mutation is involved. Even stripping comments would still allow an unused call or an unreachable branch to satisfy the check. The registry now includes flow digest, so it already contains 24 manageable types; counts do not establish coverage.

**Fix:** retain a structural inventory as a secondary check, but add parameterized dispatch behavior per type: an opted-out user must result in zero transport calls; an opted-in active user must remain deliverable. Prefer a common typed dispatch boundary so new families inherit the same enforcement instead of needing another per-job convention. Test a second sender for an existing type, not only newly registered types.

**Remaining policy gaps:** `_may_send` and the dict helper deliberately fail open on lookup errors. The dict helper still does not enforce active-account state. Several direct earnings/macro paths remain outside the manageable registry, and the flow digest still calls raw `send_email` without adding its new type's unsubscribe footer. These are not closed by the ratchet. Essential messages can have explicit exceptions; optional messages should defer when eligibility cannot be established.

## EF-05 — P2: flow-side fixes are partial, not complete EA-09 closure

**Source:** scheduler `_classify_flow_side` and options candidate construction around line 5327; `email_service.py:2277`.

Balanced premium is now neutral, and options wording no longer claims aggressive intent. Those changes are useful. Missing ask/bid values still become zero, however: the actual helper returns **ask, 100%** for ask premium 100 and missing bid premium. Classified coverage relative to total premium is not carried into the candidate or displayed here. The helper's comment says that coverage is reported separately, but this path does not do so.

The dark-pool template remains unchanged and still calls inferred BUY/SELL aggressor side a “measured fact.” That was part of EA-09, not EA-10/11.

**Fix:** preserve missingness through parsing/classification; return unknown when the required split is unavailable, distinguish known zero from missing, and display classified coverage separately from within-classified imbalance. Label dark-pool side as quote-based inference. Treat the new 60% dominance rule as a versioned heuristic pending forward validation, not a threshold proven to improve returns.

## Disposition of the original twelve findings

| Finding | Current assessment |
|---|---|
| EA-01 | Original undefined name and badge coupling fixed; successful path behavior verified. Exception isolation has EF-01; false-result give-up still exists. Historical loss remains a recovery task. |
| EA-02 | Original below-stop target fixed; finite/positive output validation added. Validator still does not require target above the breakout entry or enforce minimum reward/risk, despite a comment suggesting a reward/risk check. Fixed-percentage “support/resistance” remains a labeling limitation. |
| EA-03 | Specific zero-day earnings bug and invented fallback catalysts repaired; targeted tests pass. Timing/source provenance remains important. |
| EA-04 | Technical wording and recurring HTML footer corrected. Retry payload corruption is separately EF-02/03. |
| EA-05 | Missing advertised-type calls largely wired; flow digest now registered and excludes inactive users. Ratchet/policy/alternate-path limitations remain EF-04. |
| EA-06 | Ordinary price retry state added, with material defects EF-02/03. Not closed. |
| EA-07 | Top-3 global state now waits for successful recipient processing. Sector rotation still advances its global set before sending, so that original sibling finding remains open. |
| EA-08 | Missing/query-failed freshness and unavailable DE now defer BUYs. Freshness still tests price availability rather than the actual stored signal and all decision inputs. Do not characterize every stale-signal path as closed. |
| EA-09 | Balanced split and options wording improved. Missing coverage/side and dark-pool wording remain EF-05. |
| EA-10 | Explicitly deferred: immutable events and observation/actionable freshness. Agree with retaining it open. |
| EA-11 | Explicitly deferred: candidate, delivered-alert and executed-trade outcomes. Agree with retaining it open. |
| EA-12 | SMTP timeout and truthful paper-exit result logging added. Outbox, lease ownership and delivered-versus-accepted state remain open, as the remediation document acknowledges. |

The 30-second SMTP setting bounds socket operations, not the duration of a complete batch of recipients. It does not itself prove a scheduler cannot outlive its lease.

## Recommendation for the reported 69 consumed transitions

**Do not bulk-clear `last_signal`. Prepare one current-state recovery digest per affected recipient after the new delivery bugs are repaired.** The message should explain the delivery outage and distinguish historical missed transitions from present observations. It must not imply a historical entry was available now.

There is also a correctness issue with the proposed reset: the current transition logic specially recognizes **None→BUY**, but not None→SELL/HOLD/WAIT. Resetting a subscription to null therefore does not guarantee it receives a current alert; a non-BUY state can simply be recorded without an email. BUYs can also be legitimately suppressed by today's preference, freshness, consensus or conviction gates.

A concrete recovery procedure:

1. Reconstruct a read-only manifest from available give-up logs/evidence: subscription ID, recipient ID, symbol, horizon, missed transition/time, current signal/time, current `last_signal` and `last_sent_at`, and preference/account status. Verify whether “69” counts events, unique subscriptions, or unique symbols. Current rows alone may not recover the original transition.
2. Compare with subsequent successful notifications; remove already-covered entries and deactivated or opted-out recipients. Retain source uncertainty rather than invent an old state.
3. Produce a preview grouped by recipient, with current risk/exit changes first and current BUY setups only after today's normal gates. Include observation timestamps and an outage explanation. If historical evidence is incomplete, label that limitation.
4. Queue a dedicated recovery notification with a unique incident/recipient key. Keep its state separate from `last_signal`; do not manufacture a fresh signal transition to force delivery.
5. Review the exact preview, recipient count and send budget before authorizing its outward delivery. Record attempts/acceptance and retry failed recipients without re-running trading actions.

This review does not authorize or perform the recovery send. The manifest and preview have not been built because this continuation did not independently retrieve the reported 69-event production evidence. The recommendation is a recovery design, not a claim that the affected recipients have already been recovered.

## Next work

Fix EF-01 and EF-02/03 before replay or recovery. Replace the ratchet's claimed enforcement guarantee with dispatch-level tests. Close the remaining EA-07 and EA-09 subcases under their existing IDs, and keep EA-10/11 plus the outbox/lease work explicitly open. A green suite and an absence of NameErrors on the normal path do not exercise the failed-render and delayed-notification paths reproduced here.
