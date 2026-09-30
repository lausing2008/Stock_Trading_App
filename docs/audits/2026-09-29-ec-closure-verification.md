# EC-01 / EC-02 verification — September 29, 2026

**Close the two specific defects from the previous review: EC-01's repeated startup cleanup and EC-02's contradictory delayed-crossing sentence. Independent behavioral checks now pass against local commit `01bf6955`, including implementation `12094b0d`.** This is local verification, not a new production deployment attestation or approval for live trading.

References: [previous closure review](2026-09-28-email-fix-closure-review.md), [new acceptance checks](evidence/2026-09-29-ec-closure-verification.py), [recorded results and hashes](evidence/2026-09-29-ec-closure-verification.json).

## EC-01: specific restart defect closed

The unbounded row UPDATE is removed from `_apply_isolated_ddl`. `_apply_one_shot_migrations` now invokes `_apply_once`, which claims a unique migration name and runs its statement in the same transaction. A separate watermark restricts the historical population. The configured watermark is `2026-09-28 00:00:00+00`.

The independent probe executes these actual functions against temporary SQLite tables and verifies:

| Scenario | Result |
|---|---|
| Pre-watermark legacy row on first run | Closed out as intended |
| Post-watermark pending row over three subsequent runs | Send timestamp remains null |
| Another pre-watermark row inserted after the first run | Left unchanged; isolates the ledger guard from the watermark |
| Ledger table removed, then migration rerun | Post-watermark pending row remains null; isolates the watermark guard |
| Claimed migration statement deliberately fails | Ledger claim rolls back |
| Same migration name retried with a valid statement | Statement runs and ledger claim commits |

This verifies the important behavior directly. A `KeyError` from the old defect witness proves only that its expected statement-list entry disappeared. It does not prove ledger, transaction or watermark correctness by itself.

Limits: SQLite verifies the tested transaction/restart behavior but does not certify simultaneous PostgreSQL service startup. The unique-key claim and shared transaction are the right structure; PostgreSQL concurrency/readiness tests remain appropriate before treating the helper as generally certified migration infrastructure. The selected watermark's relationship to actual deployment history was not independently verified here.

## EC-02: specific contradictory sentence closed

The independent probe executes the current price retry job with a current quote of 80 and an earlier ABOVE-90 trigger, then passes its captured payload directly to the actual renderer. It verifies:

- The subject is marked delayed and uses “had risen above.”
- The original event timestamp reaches the rendered text through the scheduler's `event_at` argument.
- The current price is explicitly 80, back below the threshold.
- The old false sentence, `is now 80.0000 (risen above ...`, is absent.

Observed subject:

```text
Price Alert (delayed): RETRY had risen above 90.0
```

The distinction Claude identified is correct: the old witness's substring assertions were too weak to distinguish the corrected wording. The new acceptance checks assert historical tense, separate current-state text, timestamp propagation and absence of the false sentence. The historical evidence script is preserved unchanged.

## Validation scope

**87 tests passed** across `test_ec01_one_shot_migration.py`, `test_ec02_delayed_alert_wording.py`, `test_ea01_to_ea12_email_audit.py`, and `test_ea05_every_alert_type_is_enforced.py`.

Additional independent checks used actual migration function bodies with a temporary real database, and actual scheduler/renderer bodies with controlled provider/database dependencies. No live provider requests, email sends, production queries or writes were performed. Full backend/frontend suites, production deployment checks, the author's mutation campaign and inbox rendering were not rerun.

## Remaining work is separate from these closures

1. **Explicit delivery evidence:** equality of `last_sent_at` and `triggered_at` is a migration convention, not a complete delivery-state model. Recurring technical paths have also stamped trigger-time values before delivery. Consumers must not treat equality globally as unambiguous proof of legacy uncertainty, or a non-null timestamp as universal proof of delivery. Preserve explicit provenance/status in the outbox design.
2. **Technical failure retries:** successful technical send timestamps do not provide a durable retry queue for failed one-shot and recurring technical events. Keep that earlier limitation open pending its own implementation and acceptance evidence.
3. **Migration readiness:** the helper logs migration failures and returns. A successful process startup is not proof that all prerequisites were applied. Workers should verify the schema/state they require before enabling dependent behavior.
4. **Decision quality and automation:** immutable event IDs, per-input freshness, separate candidate/notification/trade outcomes, realistic execution evidence, broker reconciliation and controlled strategy promotion remain independent requirements. These two fixes do not establish a profitable trading edge.
5. **Incident recovery:** the reported 69 consumed transitions remain outside this verification. Recovery still requires a read-only manifest and reviewed digest; no bulk state reset or send was performed.

The original findings should remain as historical records, linked to this closure rather than rewritten to appear never to have existed. Only new audit/evidence files were added; application code and `CLAUDE.md` were not modified.
