# Independent review — F04 publication fencing and S0-3

Date: **October 28, 2026, America/Los_Angeles**. Reviewed fencing `c9e023a0` and S0-3 `a9f21607`, common base `8526c95d`, combined in an isolated detached worktree based on `047b674f`. No production operations, model-folder writes, source fixes or merge into `prod` were performed. Existing valuation work is untouched.

## Verdict

**Hold the fencing branch pending the findings below.** Its default-off nightly path works, but the proposed control plane does not yet enforce its stated ownership, selection integrity and rollback contracts. These findings concern the new branch and future v2 use, not a claim that the deployed ML-unavailable fix failed.

**No implementation blocker found in S0-3.** Its eligible-contributor quality correction is narrow, combines cleanly with fencing, and has reproducible output evidence. It can proceed independently after the replay report distinguishes output changes from metadata-only changes and qualifies its gate calculation. Neither branch was deployed by this review.

## Findings

### PF-01 — P1: suppression resweep bypasses the publication lock

Location: fencing branch `trainer.py:2658–2677`. `resweep_oos_suppression()` reads ownership without `publication.locked()`, then calls `joblib.dump` and `os.replace` directly. The host-wide lock in `publish()` therefore does not serialize **every** model write as claimed.

**Reproduced:** pause the actual resweep in `joblib.dump`, after its policy check; enable v2 and authorize the cell; resume. Authorization completes first, then the resweep overwrites the now-owned live artifact. The existing wiring test only checks that the ownership check appears before the dump. The container test checks an already-owned cell, not a concurrent policy change. Both pass despite the race.

**Solution:** hold the same publication lock across policy check, current-artifact read, recomputation and atomic replacement, or provide a locked metadata-update operation with a checked artifact identity. Prevent a resweep loaded from old bytes overwriting a concurrently published newer model. Add a deterministic authorization/resweep race and a concurrent trainer/resweep replacement test using real files and synchronization barriers.

### PF-02 — P1: promotion accepts the wrong candidate identity and no qualification proof

Location: `publication.py:208–234`. `promote()` checks enabled/cohort/epoch, containment beneath `ml_candidates/v2` and a caller-provided hash. It never binds the candidate to the requested symbol, horizon or family, and never requires versioned qualification evidence. Cohort authorization itself is symbol/style-wide rather than the plan's exact symbol/style/family tuple.

**Reproduced:** publish arbitrary bytes under the BBB/LONG/random_forest candidate path, authorize AAA/SWING, and promote that file as AAA/SWING/xgboost with its correct hash. Selection succeeds. Even invalid/non-model bytes pass; predictors fail only when they subsequently load them. The current serving container test copies a v1 bundle into a v2 directory and successfully promotes it, so it does not establish v2 qualification.

**Solution:** register an immutable candidate manifest binding artifact hash, cell/family, evaluation protocol and qualification record. Promotion must validate that record and policy, not infer qualification from a directory or a supplied hash. Enforce exact family membership or explicitly review a whole-cell ownership policy. Until v2 qualification exists, keep promotion/selection unavailable rather than offering a working bypass. Test cross-cell/family substitution, legacy bundles renamed v2, invalid evaluations and non-model files as refusals.

### PF-03 — P1: unreadable or missing policy silently changes serving back to v1

Location: `publication.py:248–270`. A policy read failure returns the live path; a missing file also returns it. The writer refuses corrupt policy, but serving does not preserve the selected state or fail closed.

**Reproduced:** select a candidate, confirm it serves, corrupt `policy.json`, then resolve again. The resolver returns the v1 live path without a deselect. This violates the explicit-only serving rollback contract. The existing unit test deliberately requires this fallback for corrupt policy, but tests only an unselected cell.

**Solution:** retain a validated selection snapshot or return explicit ML-unavailable on policy failure. Distinguish initial no-policy legacy operation from loss of an established policy, including after restart; do not depend only on an in-memory cache. Test corruption, disappearance, permission/read errors and restart after a selection. Update the old test that pins silent fallback.

### PF-04 — P1: selected bytes are not verified when served

Location: `publication.py:225–228,248–270` and `trainer.py` serving-path loads. Promotion stores a SHA-256, but the resolver uses only the selected path. The serving cache tracks policy-file identity, not selected-artifact identity.

**Reproduced:** select a candidate, overwrite its file, and resolve again. Serving returns the changed bytes while the manifest still names the old hash. A replacement model can thus serve under another artifact's approval and evidence identity.

**Solution:** use immutable content-addressed artifacts and verify the exact bytes loaded against the selected hash. Verification and deserialization must share the same byte snapshot or file identity; hash-then-reopen leaves a race. Cache verified bundles by selection/hash with appropriate invalidation, and refuse corrupt/mismatching files. Test mutation/replacement with unchanged policy, including same-size/timestamp cases.

### PF-05 — P2: deselect is a moving fallback, not the planned pinned rollback

Location: `publication.py:156–157,184–185,237–242`. Disabling v2 or revoking ownership leaves serving selected, as intended, but re-enables nightly v1 writes to the old live path. `deselect()` then simply removes selection and serves whatever now occupies that path.

**Reproduced:** select v2, disable v2, publish a new nightly v1 bundle, then deselect. Serving returns the new v1 bytes rather than the artifact present before selection. Files have not been preserved as an independently selected rollback identity.

**Solution:** capture a hash-bound rollback selection and provide an explicit rollback operation to that pinned artifact or an explicitly declared unavailable fallback. If an operator deliberately chooses the current moving v1 path instead, name and record that choice separately. Exercise disable/revoke → nightly write → restart → rollback in the test.

## S0-3 replay verification and report correction

Independently verified all **11** manifest entries checked here: seven raw-output hashes, three gzip hashes and the replay-script hash. The old/control/new files contain **792 rows each**, and normalized control matches old in every row. There are **11 signal transitions**, matching the report.

The report's “54 cells changed” needs qualification:

- **54 cells** change signal/confidence/probability output; the same cells change weight.
- **15 additional cells** change only `reasons.ml_test_auc`, because their surviving contributor's corrected quality remains too low to receive weight.
- Thus **69 cells change somewhere in the recorded signal payload**. This is a reporting distinction, not another implementation defect.

The comparison script uses `_load_conviction_edges = lambda: {}`. Therefore “0 newly actionable” and the lost-actionability counts are results under the base soft-layer edges, not a verified replay of every stored production conviction override. The limitation is disclosed in the script but should accompany the release conclusion. Either evaluate with the frozen effective gate configuration, or label these counts as base-gate replay results. No need to repeat the expensive generator run just to correct this description.

## Checks performed

- Combined ML suite: **501 passed, 1 skipped** (the real-container test module skips in the local environment); six deprecation warnings. No claim of a fresh full-backend run by this reviewer.
- Separate network-disabled ML container: **5 existing real nightly-path tests + 5 independent defect witnesses = 10 passed**, 84.98 seconds. Witnesses assert the undesired behavior exists; their passing is **not** an acceptance pass for the fixes. Synthetic-price training writes only temporary container paths. Scikit-learn/scipy fixture warnings are retained in the log.
- Local targeted fencing/contributor suites: **64 passed**, included within the combined suite above, not additional coverage to add to its count.
- F12: loaded the actual signal-engine calibration router in a separate offline container and called all **8 registered guarded callables**. All returned research-only/applied-false/promoted-false; profiling confirmed **zero entries into their legacy writer bodies**. This supplements the AST-based tests. It checks the registered callables directly, not auth/middleware over HTTP or every possible non-registered writer.
- The first Docker bind-mount attempts failed to mount/find the temporary test files; they ran no tests. The successful runs streamed sources into fresh disposable containers. No host production volumes or credentials were mounted; both successful containers were removed automatically.

[Evidence and witness scripts](evidence/2026-10-28-f04-fencing-s03-independent-review/). Source and evidence identities are in `manifest.json`. The isolated combined worktree was retained for reproducibility; application changes exist only there and on the submitted branches.

## Recommended sequence

1. Address PF-01–PF-04 and make PF-05's rollback contract explicit; keep v2 off.
2. Replace spelling/order-only checks with behavioral regressions for these failures, then repeat the combined nightly path and affected suites.
3. Correct the S0-3 output-count/gate wording. Its source correction can release separately; do not hold it for baseline-B research.
4. Continue full US/HK monitoring independently. This review did not run or reschedule production session collection.


## Remediation re-review

The [independent re-review of `c30b380d`](2026-10-28-f04-fencing-remediation-review.md) verifies the original resweep/promotion/integrity/rollback repairs and documents residual fail-closed policy handling. This original report remains the historical finding record.
