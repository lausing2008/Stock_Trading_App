# Scoping: AUD-C02 / AUD-C03 — independent horizon resolution

**Written 2026-09-18.** Deliberately a scoping document, not an implementation. The rest of
Tier 384 (C04, A07, A10, A11, T401) shipped the same day; this one is stopped on purpose and
this records exactly why, so the next session starts from evidence rather than re-deriving it.

## The findings

**C02 — a 5-day outcome that is already observable waits for the 28-day primary.**
`evaluate_signal_outcomes()` creates a `SignalOutcome` row only once the PRIMARY exit target has
matured, then computes the auxiliary 5/10/20-day windows on it. BUY primary windows are
7/14/28/14 calendar days for SHORT/SWING/LONG/GROWTH, so a LONG BUY's 5-day result — fully
determined and sitting in the price table — is withheld for 28 days. Measured coverage since
2026-09-03:

| Frozen BUY cohort | Actionable | Resolved 5-day |
|---|---:|---:|
| SHORT | 520 | 248 |
| SWING | 279 | **0** |
| LONG | 880 | **0** |
| GROWTH | 369 | **0** |

**C03 — `return_5d` means 5 CALENDAR days; the style/ML vocabulary around it means trading
sessions.** A probability calibrated against one target is not calibrated for the other, and
nothing in the schema records which was meant.

Both block real work: AUD-A11 cannot calibrate confidence into a probability without matured
outcomes per market/style/direction, and three of the four BUY styles currently supply none.

## Why this was not implemented today

The fix is to create a pending row at the signal event and resolve each horizon independently.
That changes what **"a `SignalOutcome` row exists"** means — and the codebase leans on that
meaning in 201 non-test references across 12 modules:

```
ml-prediction/src/training/trainer.py         ml-prediction/src/training/meta_trainer.py
ml-prediction/src/training/ev_gate.py         ml-prediction/src/features/builder.py
signal-engine/src/api/calibration.py          signal-engine/src/api/analytics.py
signal-engine/src/api/fix_effectiveness.py    signal-engine/src/api/signals_shared.py
signal-engine/src/generators/signals.py       signal-engine/src/api/outcomes.py
decision-engine/src/api/core/models.py        market-data/src/services/conditional_orders.py
```

**The ML training path is the reason to be careful.** If a consumer selects training rows without
filtering on maturity, pending rows silently enter the training set as unresolved outcomes. That
would not raise — it would quietly degrade every model, and the degradation would be discovered
weeks later as "the models got worse", with this change long out of sight. This project has
already spent a session tracing a confidence inversion to exactly that kind of invisible
data-shape change (`docs/2026-09-05/`).

45 of the references do filter on `is_correct` / `pct_return` / `return_5d`, which NULL-excludes
correctly. **The remaining ~156 have not been audited.** That audit is the actual work, and it is
not a thing to do quickly at the end of a long session.

## What the implementation must do

1. **Audit all 201 references first.** Classify each as (a) already maturity-filtered, (b) needs a
   filter added, (c) genuinely wants pending rows. Ship nothing until (b) is empty.
2. **Add an explicit state column** rather than inferring maturity from NULLs — `pending`,
   `resolved`, `missing_price`, `skipped`. Inferring from NULL is how the ambiguity arose.
3. **Resolve each horizon independently**, keeping primary-horizon maturity as its own field.
4. **Make re-evaluation idempotent** — a completed horizon must never be rewritten without an
   explicit correction record.
5. **C03: persist `horizon_unit`**, the target session/date, the actual entry/exit sessions, and a
   label version. Introduce session-based metrics as NEW fields; never silently relabel the
   existing calendar-based ones, because every stored calibration was fitted against them.
6. **Tests must cover weekends, US and HK holidays, and missing sessions**, and must assert that a
   LONG BUY acquires a 5-day outcome while its 28-day primary stays pending.

## Acceptance

A LONG BUY has a resolved 5-day outcome and a pending primary, simultaneously, with no
fabrication of the later result. Every existing win-rate, calibration and ML-training query
returns **the same numbers as before the change** on the same data — the change must add rows
that are visibly pending, never alter the meaning of a query nobody revisited.

## Estimate

Not a patch. The reference audit alone is the larger half, and it gates everything else.
