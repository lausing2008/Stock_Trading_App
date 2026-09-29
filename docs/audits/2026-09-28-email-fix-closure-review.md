# Email fix closure review — September 28, 2026

**The specific EF-01, EF-02, EF-04 and EF-05 counterexamples are repaired. EF-03 is partially repaired, but its new legacy-row cleanup is not one-shot: a later database initialization can suppress genuinely undelivered alerts. Delayed-alert wording also still mixes historical triggers with current prices.**

Reviewed local commit `174192f9`, including implementation `0ec2ceba` and the generalized exception-scope test. Date is America/Los_Angeles. This follows the [previous review](2026-09-28-email-remediation-followup-review.md) and verifies the [remediation response](2026-09-28-email-followup-remediation.md).

Only new audit/evidence files were created. Application code, historical evidence, the existing roadmap, and `CLAUDE.md` were left unchanged. No production access, migration execution against production, email sending or deployment occurred.

## Confirmed improvements

| Finding / claim | Current assessment and evidence |
|---|---|
| EF-01: exception variable read outside its handler | **Specific defect closed.** A whole-job controlled run with two eligible subscriptions makes the first sender raise and the second succeed. Two sender calls occur, no outer job error is logged, the first transition remains BUY/pending and the second advances to SELL after success. Optional cohort enrichment also fails deliberately in this fixture without blocking sends. |
| EF-02: threshold substituted for current price | **Specific defect closed.** The retry symbol is now requested; a provider price of 123 is passed as 123 rather than threshold 90. With no usable quote, zero sends occur. The delayed note contains the original trigger time. Wording remains incomplete; see EC-02. |
| EF-03: successful technical alert reselected by price retry | **Cross-family selection corrected by source inspection.** Retry selection is limited to ABOVE/BELOW, and successful technical sends now write their timestamp. New migration risk is EC-01. Failed technical delivery remains a separate open limitation below. |
| EF-04: comment counted as enforcement | **Specific counterexample closed.** A comment-only helper call is stripped and `_is_enforced` rejects it. Structural checks and direct gate behavior tests are useful additions; they are not proof that every full sender path enforces preferences. |
| EF-05: absent premium side treated as zero | **Specific defect closed.** Actual classifier returns unknown for a missing side and ask for a genuine measured zero on the other side. Caller preserves missingness and skips unknown direction. |
| Breakout target validation | Validator now requires target above breakout. Targeted tests pass; the misleading reward/risk-floor comment was removed. This does not establish a profitable reward/risk policy. |
| Dark-pool side wording and flow footer | Source reflects inferred-side wording and the flow digest unsubscribe footer. |
| Sector rotation | Source now separates recording fade-outs from consuming newly emerging sectors on failed delivery; targeted regression tests pass. |
| General exception-scope guard | Scheduler-wide AST test passes and covers the reported outside-handler-name pattern. It supplements, rather than replaces, the whole-job failure-isolation test. |

**144 tests passed** across six files: EA/EF email tests, preference tests, dedup/isolation, sector rotation, options flow and the T401 source-assertion ratchet. This review did not rerun all backend/frontend suites, the author's sabotage campaign, browser rendering or production drift checks.

Evidence: [executable checks](evidence/2026-09-28-email-fix-closure-checks.py), [results and hashes](evidence/2026-09-28-email-fix-closure-checks.json).

The probes reuse the earlier audit's controlled fixtures with current acceptance assertions and mixed sender outcomes. Application function bodies are unchanged. Database/provider results for scheduler probes are controlled; they are not full provider or PostgreSQL integration tests. The migration probe executes the exact application UPDATE against a minimal SQLite table, while its repeated startup invocation is verified in source.

## EC-01 — P1: the legacy cleanup suppresses new failed notifications on later initialization

**Source:** `shared/db/session.py:33–38`, `init_db`; `:811–815`, legacy statement in `_apply_isolated_ddl`.

The migration executes:

```sql
UPDATE price_alerts SET last_sent_at = triggered_at
WHERE triggered IS TRUE
  AND last_sent_at IS NULL
  AND triggered_at IS NOT NULL
```

There is no historical cutoff, captured legacy ID set, schema-version marker, or persisted once-only guard. `init_db()` invokes `_apply_isolated_ddl()` again whenever that initialization path runs. The comment calling the UPDATE “one-shot” does not prevent later executions.

**Reproduction with the exact SQL:**

1. Insert a legacy triggered row with a null send timestamp and run the cleanup.
2. Insert a **new** triggered row representing a failed send, with trigger time September 29 at 12:00 and `last_sent_at=NULL`.
3. Execute the same cleanup, as a later initialization would.
4. The new row now has `last_sent_at=September 29 at 12:00`, despite **zero transport calls**.

That row no longer satisfies the retry predicate requiring `last_sent_at IS NULL`. A restart or another service invoking this shared initialization path can therefore consume a newly failed notification. The scenario is deterministic; no claim is made that it has already happened in production.

The cleanup also stores no distinct legacy-unknown status. A code comment saying delivery is unknown does not preserve that fact for data consumers. “Zero ambiguous rows remain” means null timestamps were eliminated; it does not establish historical delivery truth or durable future retry safety.

**Recommended repair:**

- Replace the repeated startup UPDATE with a versioned migration, atomically recording completion and preventing concurrent reruns.
- Bound the historical population by a reviewed immutable manifest or precise deployment watermark. Never reclassify post-migration failures merely because they are null.
- Represent legacy uncertainty explicitly, for example `delivery_status=legacy_unknown` or a separate delivery record. Keep successful send timestamps reserved for evidence of acceptance.
- Preserve genuinely pending rows across restart. Transition them only on an actual delivery result or an explicit recorded expiry/suppression policy.

**Acceptance:** migrate legacy rows, create a new failed notification, initialize twice and confirm that it remains pending and can subsequently deliver. Test concurrent initialization and already-delivered rows using PostgreSQL. Verify historical-unknown rows remain distinguishable from successful delivery.

Do not repair the deployed history by blindly clearing all timestamps equal to trigger time. Such rows may include legitimate or intentionally closed-out legacy data. Reconstruct a bounded affected manifest from logs and migration timing before any corrective production mutation.

## EC-02 — P2: delayed crossing text can contradict the current quote

**Source:** scheduler `check_price_alerts` retry payload; `email_service.py::send_price_alert_email` price-crossing branch.

The retry now fetches a real current quote and says that delivery was delayed. However, it still uses the original condition in a present-price statement. If the price has crossed back, the actual renderer produces:

```text
Subject: Price Alert: RETRY has risen above 90.0
RETRY is now 80.0000 (risen above your target of 90.0).
Note: Delayed notification — this alert triggered at ...
```

The price 80 is real in this controlled example; the historical crossing may also be real. Their combination in the current sentence is false. The explanatory note does not repair that sentence.

**Recommended repair:** give original event time/value and current quote their own fields and wording. For example: “Earlier alert: crossed above 90 at [time]. Current quote: 80 as of [time]; price is now below the threshold.” If the original observed value was not stored, say it is unavailable rather than reconstructing it from the threshold. Persist the original event payload for future alerts.

**Acceptance:** above/below alerts whose price remains beyond the threshold, returns inside it, or has no quote; verify HTML and plain text. A delayed notification must not imply the original entry remains valid or create a new executable signal.

## What remains open beyond the two new findings

**Technical failure retries.** The latest source still stamps recurring technical alerts' `last_sent_at` before send, while one-shot technical alerts become triggered before send. The price retry query now correctly excludes technical conditions, but no equivalent durable technical retry queue is added by this change. Recording technical successes fixes success-state ambiguity; it does not recover failed technical notifications. Keep that portion of EA-06/EF-03 open until there is a dedicated event/delivery lifecycle.

**Notification architecture.** The transactional outbox, checked lease ownership, independently recorded expiry/dead-letter state and provider-accepted-versus-delivered distinctions remain necessary. Some signal false-return failures can still reach the give-up rule; temporary transport failure is not evidence that a market event should be forgotten.

**Input and performance evidence.** Immutable source event IDs, strategy-specific watch/actionable TTLs, actual stored-signal freshness, and separate candidate/notification/execution outcomes remain open as acknowledged. Repairing these bugs does not establish profitable signals or authorize live automation. The [roadmap](../2026-09-28-signal-alert-reliability-and-automated-trading-roadmap.md) sets out those independent gates.

**Preference coverage.** The improved test correctly labels structural inventory limitations. Central dispatch-level enforcement and full-path opt-out tests remain preferable to relying solely on each caller remembering to gate delivery.

## Production claims and recovery disposition

The reported zero matched retry rows, zero fired retries, 60 legacy rows and zero ambiguous rows after migration were **not independently recounted in this continuation**. They can describe a valid momentary exposure check, but do not disprove EC-01's future-initialization counterexample.

The reported 69 consumed signal transitions remain a separate incident. Continue with a read-only recovery manifest and reviewed current-state digest, not bulk resetting `last_signal`. This review neither constructs that production manifest nor sends the digest. No delivery authorization has been inferred from the request to continue auditing.

**Next action:** fix and behaviorally verify EC-01 before relying on restart-safe retries; correct EC-02 and preserve technical failures as pending events. Keep the specific verified repairs closed rather than reopening the old counterexamples, while tracking these remaining defects under their own IDs.
