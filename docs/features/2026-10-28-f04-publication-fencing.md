# F04 nightly-publication fencing — deployed; v2 activation off

Date: **2026-10-28**. Branch: `f04-publication-fencing`.
- **Status:** implemented; the five findings of the
  [independent review](../audits/2026-10-28-f04-fencing-s03-independent-review.md) (PF-01..PF-05)
  are fixed and tested (see [Review remediation](#review-remediation-2026-10-29)); **deployed as `15e2abac`**; v2 activation **off**. Merged with `prod` at `fa1a3e22` (S0-3) and tested combined.
  The [re-review](../audits/2026-10-28-f04-fencing-remediation-review.md)'s two residual policy
  errors (RF-01, RF-02) are fixed too; see [Re-review remediation](#re-review-remediation-2026-10-29).
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
- **Promotion is refused outright** until v2 qualification records exist (`promotion_unavailable`).
  A directory and a caller-supplied hash are not qualification. Behind that gate `promote()` also
  requires v2 enabled, the cell in the cohort, an unchanged epoch, a candidate written for exactly
  this symbol/style/family, and matching bytes. It pins the current live artifact as a
  content-addressed rollback copy. The candidate manifest (hash, cell, family, evaluation
  protocol, qualification evidence) is built with the remaining v2 work.
- **Serving:** `load_serving()` loads a selection, or else the live path. With no policy ever
  written it is identical to today.
  - A selected artifact is read once, hashed, compared with the selection's SHA-256 and
    deserialised from that same buffer. The bundle cache is keyed by hash, and the file is
    re-read and re-hashed on every load, so a cache hit cannot hide a replaced file.
  - A mismatching or missing selected file, an unreadable policy, or a policy lost after one was
    established raises `ServingUnavailable`. The served routes answer **ML unavailable**
    (`ml_unavailable_reason: serving_unavailable`), never a fall-back to the live v1 file. In an
    ensemble, one refused member fails the whole cell; it is not dropped silently.
  - `<model_dir>/.publication_established` is written before the first policy. It is how a lost
    policy is told apart from first-time legacy operation, including after a restart.
- **Rollback is explicit.** Disabling v2 or revoking a cell does not change serving.
  - `rollback` serves the artifact pinned at selection, after re-verifying its hash. A nightly v1
    write since selection does not change what it serves.
  - `serve-current-live` is a separately named action. It needs a recorded reason and logs the
    hash of whatever the live path holds at that moment.
- **Isolation:** candidates and pinned rollbacks live in `<data>/ml_candidates/`, outside every
  live discovery path (`model_dir/*/*.joblib`).

## Enforcement points

| Point | Change |
|---|---|
| Scheduler (market-data) | Nightly `POST /ml/train_all_horizons` sends `protocol=v1` explicitly; never v2. |
| API (ml-prediction) | All 5 training routes call `_require_v1`: v2 → 409 while disabled, 501 otherwise (v2 training does not exist). Every queued task carries the checked protocol. |
| Worker (`train_model`) | Refuses any protocol but v1 before doing any work. Its only artifact write is `publication.publish` (incumbent-retention rule unchanged for v1). Tuning publishes through it as v1. |
| Writer | `publish()` as above. The suppression resweep writes only through `rewrite_live()`: policy check, read, recompute and replace all under the lock, deserialising from the bytes it guards, and refusing if the file changed since it was read. It skips owned cells and writes nothing on an unreadable policy. |
| Selection | `predict_latest` and `load_trained` load through `_load_serving_bundle` (verified); the ensembles probe existence through `_serving_artifact_path`. The three served routes turn `ServingUnavailable` into an ML-unavailable answer. |

The meta model (`meta_model.joblib`) is a global artifact outside per-cell selection and is not
fenced here.

## Tests

- [`test_f04_publication_fencing.py`](../../services/ml-prediction/tests/test_f04_publication_fencing.py)
  — 66 tests on the control plane: real files, the real lock and real threads (27 added for
  RF-01/RF-02).
- [`test_f04_publication_wiring.py`](../../services/ml-prediction/tests/test_f04_publication_wiring.py)
  — 9 tests checking that every entry point routes through it, including the scheduler's nightly
  call, the member re-raise and the served routes.
- [`test_f04_publication_nightly_path_container.py`](../../services/ml-prediction/tests/test_f04_publication_nightly_path_container.py)
  — the **actual nightly path**, run inside the ml-prediction image. The chain
  `POST /ml/train_all_horizons` → `BackgroundTasks` → the real `train_model` (XGBoost and
  RandomForest fitted on synthetic prices) → `publish` → disk is exercised. **20 passed**
  (production image, `--network none`, sources streamed in, no volumes). It covers:
  - v1 live publication and v2 refusal;
  - an owned cell fenced while others retrain live;
  - a job queued before authorization, run after it, then retried after revocation;
  - promotion refused without a qualification record;
  - selection, disable, a nightly write, restart and pinned rollback, then `serve-current-live`;
  - a corrupt or missing policy answered as ML-unavailable by all three served routes, across a
    restart;
  - a tampered selected member (xgboost, and random_forest inside a member try-block) failing
    the whole cell closed;
  - the review's resweep race, through the real trainer;
  - a model trained between the resweep's scan and its rewrite, which is kept;
  - the resweep skipping an owned cell;
  - a permission or I/O error on the policy (warm and cold cache, across a restart) and four
    malformed selection records, each answered 200 + ML-unavailable by all three routes.
- **Existing harnesses:** those that extract the ensembles now also stub `_serving_artifact_path`.
- **Sabotage checks** (each reverted in turn; tests fail as expected):
  - a direct write in `train_model`;
  - a dropped protocol on an enqueue;
  - the writer ignoring ownership;
  - the lock removed;
  - the cache keyed on mtime only;
  - from the review fixes: no hash check at serving; a bundle cache keyed by cell; a missing or a
    corrupt policy serving live; no establishment marker; qualification accepted; no cell/family
    binding; the rewrite unlocked; no identity re-check; rollback to the live file. **All 10
    caught.**
- **Full backend suites:** `make test` on the combined branch (fencing + review fixes + `prod`
  at `fa1a3e22`): every service passed, ml-prediction 525 (1 skipped: the container module).
  After the re-review fixes (merged with `prod` at `8f2a5be4`): every service passed again,
  ml-prediction 552 (1 skipped), signal-engine 1007.

## Review remediation (2026-10-29)

The user approved the defaults below for the [independent review](../audits/2026-10-28-f04-fencing-s03-independent-review.md).

| Finding | Fix |
|---|---|
| PF-01 resweep bypassed the lock | `rewrite_live()` holds the lock across check, read, recompute and replace, deserialises from the guarded bytes, and refuses if the file changed. Deterministic barrier tests: an authorization and a training publish both wait for an in-progress rewrite. A model trained after the scan is re-judged on its own metrics, not overwritten. |
| PF-02 promotion accepted any candidate | Every promotion is refused until qualification records exist. Behind the gate, the candidate must be the exact cell/family path. The manifest is v2 work. |
| PF-03 lost policy reverted serving to v1 | An establishment marker plus fail-closed reads: a corrupt, unreadable or missing-after-establishment policy is ML-unavailable for serving and refused for writing, across a restart. The old test that pinned the silent fallback was rewritten. |
| PF-04 served bytes unverified | Hash check and deserialisation on one buffer; bundle cache keyed by hash; the file re-read on every load. Same-size, same-timestamp replacement is refused. |
| PF-05 deselect served a moving file | A content-addressed rollback is pinned at selection; `rollback` serves it; `serve-current-live` is separate, reasoned and audited. `deselect` is removed. |

**Limitation.** signal-engine records any ML-unavailable answer as `no_eligible_model`
(`ml_model: none_served` distinguishes it). The gate effect is the approved one: missing ML is a
soft failure and never satisfies a new-entry gate. A distinct downstream label would be a
signal-engine change, not part of this branch.

## Re-review remediation (2026-10-29)

| Finding | Fix |
|---|---|
| RF-01 policy metadata errors returned HTTP 500 | Only `FileNotFoundError` means absent. Any other error on the policy or marker (stat or read) is `PolicyUnreadable`, so serving is `ServingUnavailable` and the routes answer 200 + `ml_available: false`. Writers refuse. A failure evicts the cache before raising, so a warm cache cannot hide it. |
| RF-02 malformed selection served live v1 | `read_policy` validates every cohort and selection entry: key shape, known family, non-empty path, lowercase 64-hex SHA-256, kind `candidate`/`rollback`, and a valid or explicitly unavailable rollback record. Any malformed entry makes the whole policy unreadable. Serving tests key presence, not truthiness. |

- **Downstream:** a signal-engine test sends the real `_serving_unavailable` response through the
  real fetch and fusion: zero ML weight, SA-27 flag set, `ml_unavailable_reason:
  no_eligible_model` (the label the conviction gate fails on). A negative control shows a 500 loses
  that metadata, which is why the route must never 500.
- **Review witnesses:** all four now fail, for the intended reasons (200 instead of 500;
  `ServingUnavailable` instead of loading v1).
- **Sabotage:** the pre-fix `publication.py` fails 25 of the 27 new unit tests (the other two check
  well-formed records are still accepted and a read error already handled). Five targeted reverts
  were each caught: no stat-error branch, no entry validation, an error read as absence, no
  rollback-record check, any kind accepted.
- **Before v2 activation (not a release blocker):** keep the detailed upstream reason in signal
  lineage and monitoring, without renaming `no_eligible_model`. The gate matches that exact label,
  so a rename alone would soft-pass it.

## Not done here

- Deployment is recorded below. The guarded release keeps legacy behavior while v2 is off and no policy has been written.
  New on disk: the policy directory and lock file are created at the first nightly write, and
  every write takes the lock. The establishment marker and `policy.json` appear only with the
  first operator policy change, after which a lost policy fails closed.
- v2 training, protocol/evaluation versions and baseline B are the next F04 pieces. Nothing can
  publish them live without a cohort authorization and an explicit promotion.


## Production deployment — October 28, 2026, 20:30 PDT

**Release `15e2abac` deployed to ml-prediction and market-data** through the standard `scripts/deploy.sh`, using the clean pinned release checkout and its host deployment/backup lock. The earlier permission block occurred in the other tool; this environment accepted the authorized deployment. No bypass or runtime file copy was used. The remote checkout/pulled revision was verified as exactly `15e2abac40926ea4ee135aa6c01fe4270ef56929`.

The backup completed at 02:13:52 UTC before deployment began at **03:30:20 UTC on October 29** (**20:30:20 PDT on October 28**). Both rollback images were verified before and after deployment. Old service logs and identities were saved at `/home/ec2-user/deploy-evidence/2026-10-28-f04-fencing-15e2abac/`.

Post-deploy verification:

- **15/15 containers healthy; all 12 backend source/shared trees match the clean release, zero drift.** Only ml-prediction and market-data restarted. Signal-engine retained its previous image/start time. Gateway health returned 200.
- **No `policy.json`, no establishment marker, v2 disabled, empty cohort and selections.** The v2 training boundary returns 409 without queuing a job; qualification validation refuses promotion with `promotion_unavailable:qualification_records_not_implemented`. These checks did not initialize or mutate policy.
- One actual authenticated **MSFT/SWING** ensemble prediction returned HTTP 200 and the ordinary `no_eligible_model` response, not `serving_unavailable`. Its zero-contributor response was handled normally. It was an inference read, not a persisted signal/alert/order.
- Rollbacks retained: `stockai-ml-prediction:rollback-before-15e2abac` (`4483fffe96e4`) and `stockai-market-data:rollback-before-15e2abac` (`685cedd5df2b`). New image IDs and exact starts are in the evidence.
- Host finished with **31 GB free, 3.3% sampled I/O wait**. No extra production training, resweep, policy change, signal refresh or test email was triggered. Normal scheduler activity remains governed by existing behavior.
- Nineteen pre-existing working files from the parallel valuation work were hash-checked before/after the local checkout fast-forward and remained identical. Their edits were not built or committed with this deployment.

[Deployment log, 12-service drift check, verification summary, scripts and hashes](../audits/evidence/2026-10-28-f04-fencing-deployment/). The merge's full-suite result is the submitter's pre-deploy evidence; this deployment did not repeat it after the clean reviewed source was pinned.

**Still open:** v2 qualification manifest/evaluation versions, the purged baseline B and controlled cohort activation. F03 receipts, causal RZY simulation and Intraday remain separate implementation work. Fencing deployment does not close the whole F04 finding or establish improved trading returns. Additive Intraday contracts can be developed alongside these tasks; producing actionable Intraday signals remains gated by causal timing/data/measurement work.
