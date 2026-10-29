# F04 fencing remediation — independent re-review

Date: **October 28, 2026, America/Los_Angeles**. Reviewed `c30b380df4d00c78b80207ac271f9aa2e10b3ea5` in an isolated detached worktree. Its history includes the deployed S0-3 change. Related: [first review](2026-10-28-f04-fencing-s03-independent-review.md), [implementation](../features/2026-10-28-f04-publication-fencing.md) on the submitted branch.

**Verdict: keep the fencing branch held for the residual policy failure handling below.** The submitted fixes address the original resweep, promotion, byte-integrity and rollback witnesses. The established-policy marker fixes the ordinary missing-policy case, and malformed JSON becomes ML-unavailable. Two remaining policy-error cases still break the declared fail-closed contract. No application code, production configuration, models, jobs or deployments were changed by this review; unrelated valuation work is untouched.

## Disposition of the original findings

| Finding | Review result |
|---|---|
| PF-01, resweep race | Addressed for managed writers. `rewrite_live()` holds the shared lock around policy check, fresh-byte read, recomputation and replacement. A newer artifact published between scan and rewrite is evaluated from its own bytes. The identity check additionally detects replacement before commit; it is not a synchronization guarantee against arbitrary writers that ignore the lock. |
| PF-02, unqualified/wrong candidate | Addressed for this stage by unconditional promotion refusal. The tests that exercise future selection explicitly stub qualification; they are not evidence that qualification is implemented. Symbol/style/family path checks exist behind the refusal. Exact family-level cohort authorization and the full candidate qualification manifest remain future v2 work. |
| PF-03, lost policy silently serves v1 | Partly addressed. Marker, restart behavior, corrupt JSON and normal missing-policy behavior are covered. Metadata-access errors and malformed selection entries remain below. |
| PF-04, selected bytes | Addressed: selected bytes are re-read and hashed on every load and deserialized from that same buffer. Cache lookup follows integrity verification. `ServingUnavailable` is re-raised by member handlers so an integrity refusal is not silently dropped from an ensemble. |
| PF-05, moving rollback | Addressed for the reviewed paths: a content-addressed copy is pinned at selection; explicit rollback selects it, and `serve-current-live` requires a reason and records the transition. Actual promotion remains disabled. |

## Residual policy findings

### RF-01 — P1: policy metadata errors escape as HTTP 500 and lose the unavailable contract

Source: `publication.py:117–128,409–434`; signal-engine `generators/signals.py:430–483`; market-data `scheduler.py:1287–1300` on the reviewed branch.

`_selections()` catches only `FileNotFoundError` around `pfile.stat()`. A permission error on the policy directory (or another metadata I/O error) escapes as raw `OSError`. `read_policy()` likewise calls `path.exists()` and marker `.exists()` outside the conversion to `PolicyUnreadable`. The prediction routes convert `ServingUnavailable`, not these raw errors.

**Witness:** inject `PermissionError(EACCES)` only for `policy.json`'s metadata access, leaving all other filesystem operations real. Each of the three actual prediction routes returns **500**, rather than the intended 200 response with `ml_available:false` and a serving-unavailable reason. All data is under temporary paths; no production permissions were changed.

The downstream fetch behavior was also reproduced offline through the real `_fetch_ml_data()` function with mocked HTTP: three 500 responses return `(None, 0.0, {})`; one normal 200 ML-unavailable response instead stops fallback and retains the soft-failure metadata. The debug log labels the 500 cascade as the no-model/TA-only path. This is not an observed production incident. The conviction gate then treats missing probability without `no_eligible_model` as a soft **pass**. That differs from the deliberately approved soft **failure** for unavailable ML; SA-27 suppression metadata is also absent. This review does not claim that a specific live trade or signal has changed.

**Required solution:** normalize non-absence filesystem errors on policy/marker discovery, stat and reads to `PolicyUnreadable`, and then `ServingUnavailable` for inference. Only positively established initial no-policy legacy operation may return the default; errors establishing that state must not infer absence. Ensure cached selections do not mask the error. Add actual-route tests for permission and generic I/O failures, before/after restart and with/without a warm cache. Verify the returned unavailable envelope reaches signal-engine with zero ML weight and the same soft-failure policy.

### RF-02 — P2: malformed nested selections can still silently serve current v1

Source: `publication.py:129–133,439–461`. Schema validation checks only that `selections` is a dict. A present key mapped to `{}`, `null` or another false-valued record is then treated as if there were no selection by `if not sel`. Other malformed records can fail with unconverted `KeyError`/`TypeError` instead of ML-unavailable.

**Witness:** start with an established valid policy, set `selections['AAA|SWING|xgboost'] = {}`, and call the real `load_serving()`. It deserializes the live v1 artifact. No explicit rollback occurred. Valid JSON therefore does not establish a valid selection policy.

**Required solution:** validate nested cohort/selection structures and required record types/fields, including nonempty artifact path, exact SHA-256 encoding and recognized record kind. Treat malformed present entries as unreadable policy rather than absent selections. Use key presence rather than truthiness to distinguish absent from invalid. Cache only fully validated policies; test empty/null/string records and missing or invalid fields at the loader and all served routes, with zero silent fallback. This can be completed alongside RF-01 before release.

## Coarse unavailable label

The stated limitation is real: the ML routes return `serving_unavailable` and a detailed reason, but signal-engine maps every explicit unavailable response to `ml_unavailable_reason: no_eligible_model`, sets suppression metadata, and discards that detailed reason. The ordinary response therefore retains zero weight, SA-27 behavior and the approved soft gate failure. **The coarse label alone is not a release blocker.**

Before v2 activation, preserve the detailed upstream reason in signal lineage and monitoring so missing/corrupt policy or hash failures are not counted as ordinary model suppression. Keep gate semantics backward compatible while adding this detail: the current gate recognizes the exact `no_eligible_model` label, so renaming it alone would accidentally soft-pass it. RF-01 differs because no unavailable response reaches that compatibility path at all.

## Independent validation

- Full local ML suite on the submitted branch: **525 passed, 1 skipped**, six existing deprecation warnings, 9.57 seconds. The skipped module is run separately in the ML image; no claim of a new full-backend run by this reviewer.
- Container: **16 passed in 161.87 seconds** — 12 submitted actual-nightly-path tests plus 4 independent residual witnesses; the synthetic-fit warnings are retained in the result log. The witnesses assert undesired behavior exists, so their passing is not acceptance of that behavior.
- Container uses local ML image `f9ff13606a52`, streamed reviewed source/shared files, synthetic prices and temporary model roots, `--network none`, no host volumes or production credentials, bounded CPU/memory and automatic removal. The source under review, not the image's original application files, supplies the tested logic. This is an independent local-image check, not another read of production.
- Tests using a stubbed qualification validator exercise dormant future selection/rollback code. Production has no stub and promotion must remain refused.

[Witnesses, result log and hashes](evidence/2026-10-28-f04-fencing-remediation-review/). The detached review checkout is retained at `/tmp/stockai-f04-fencing-rereview-20261028`.

## Next action

Fix RF-01/RF-02, add behavioral regressions and rerun the affected ML routes and publication tests. Keep v2 off and promotion disabled. Once these pass, the fencing code can release before the purged-baseline study; neither this review nor the guarded release qualifies any v2 model. No additional performance experiment is needed to correct the policy-error response contract.
