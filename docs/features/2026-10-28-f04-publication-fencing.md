# F04 nightly-publication fencing — implemented, not deployed

Date: **2026-10-28**. Branch: `f04-publication-fencing`.
- **Status:** implemented and tested; **not deployed**; v2 activation **off**.
- **Prerequisite for** the remaining F04 trainer changes (Option 1 of the
  [F04 plan](2026-10-27-f04-evaluation-version-comparison-and-rollout-plan.md)).
- **Unchanged:** no trainer split, evaluation or artifact change is part of it.

## What it does

[`training/publication.py`](../../services/ml-prediction/src/training/publication.py) is the control
plane. Every artifact write, selection and serving lookup goes through it.

**Policy.**
- **Where:** `<data>/ml_publication/policy.json`, a sibling of the live `model_dir`.
- **Default:** a missing file means v2 is **off**.
- **Contents:**
  - `epoch` — bumped on every change;
  - `v2_enabled`;
  - `cohort` — the cells v2 owns;
  - `selections` — the explicit serving manifest;
  - a change log.
- **How it changes:** only through an operator CLI inside the container (`python -m src.training.publication …`).
  There is deliberately no HTTP endpoint; `GET /ml/publication_policy` is read-only.

**Write-time decision, under a host-wide `fcntl` lock.**

| Write | Cell not owned (v2 off, or not in cohort) | Cell owned (v2 on AND in cohort) |
|---|---|---|
| v1 (the nightly job, tuning, manual training) | **live path — unchanged behaviour** | candidate root `v1-fenced/`, never live |
| v2 | candidate root `v2/`, never live | candidate root `v2/`, never live |

Because the decision is made at write time, a job queued or retried across a policy change is judged
by the policy in force when it publishes. A write already in progress finishes under its policy; the
change waits for it. An unreadable policy refuses publication (fail closed).

**Promotion and serving.**
- **Promotion:** `promote()` selects only a v2 candidate. It requires v2 enabled, the cell in the
  cohort, the policy epoch unchanged since approval, and a matching SHA-256. Any revocation or other
  change since the approval refuses it.
- **Serving:** `serving_path()` serves a selection, or else the live path. With no selections it is
  identical to today.
  - Disabling v2 does **not** roll back serving; `deselect()` is the explicit rollback.
  - The serving cache keys on inode, mtime and size, because mtime alone is clock-tick coarse.
    This was found by the container test.
- **Isolation:** candidates live in `<data>/ml_candidates/`, outside every live discovery path
  (`model_dir/*/*.joblib`).

## Enforcement points

| Point | Change |
|---|---|
| Scheduler (market-data) | Nightly `POST /ml/train_all_horizons` sends `protocol=v1` explicitly; never v2. |
| API (ml-prediction) | All 5 training routes call `_require_v1`: v2 → 409 while disabled, 501 otherwise (v2 training does not exist). Every queued task carries the checked protocol. |
| Worker (`train_model`) | Refuses any protocol but v1 before doing any work. Its only artifact write is `publication.publish` (incumbent-retention rule unchanged for v1). Tuning publishes through it as v1. |
| Writer | `publish()` as above. The suppression resweep skips any cell an enabled v2 cohort owns (and writes nothing if the policy is unreadable). |
| Selection | `predict_latest`, `load_trained` and both ensembles resolve files through `_serving_artifact_path`. |

The meta model (`meta_model.joblib`) is a global artifact outside per-cell selection and is not
fenced here.

## Tests

- [`test_f04_publication_fencing.py`](../../services/ml-prediction/tests/test_f04_publication_fencing.py)
  — 17 tests on the control plane: real files, the real lock and real threads.
- [`test_f04_publication_wiring.py`](../../services/ml-prediction/tests/test_f04_publication_wiring.py)
  — 7 tests checking that every entry point routes through it, including the scheduler's nightly call.
- [`test_f04_publication_nightly_path_container.py`](../../services/ml-prediction/tests/test_f04_publication_nightly_path_container.py)
  — the **actual nightly path**, run inside the ml-prediction image. The chain
  `POST /ml/train_all_horizons` → `BackgroundTasks` → the real `train_model` (XGBoost and
  RandomForest fitted on synthetic prices) → `publish` → disk is exercised. **5 passed.** It covers:
  - v1 live publication and v2 refusal;
  - an owned cell fenced while others retrain live;
  - a job queued before authorization, run after it, then retried after revocation;
  - serving selection and deselection;
  - the resweep skipping an owned cell.
- **Existing harnesses:** those that extract the ensembles now also stub `_serving_artifact_path`.
- **Sabotage checks** (each reverted in turn; tests fail as expected):
  - a direct write in `train_model`;
  - a dropped protocol on an enqueue;
  - the writer ignoring ownership;
  - the lock removed;
  - the cache keyed on mtime only.
- **Full backend suites:** all services pass on the branch.

## Not done here

- No deployment. Deploying it is behaviour-neutral while v2 is off. Two things are new on disk: the
  policy directory and lock file are created at the first nightly write, and every write takes the lock.
- v2 training, protocol/evaluation versions and baseline B are the next F04 pieces. Nothing can
  publish them live without a cohort authorization and an explicit promotion.
