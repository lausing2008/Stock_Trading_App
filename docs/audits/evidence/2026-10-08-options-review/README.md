# Evidence bundle: options / squeeze / dark-pool review

Collected 2026-10-08, production query timestamp 07:11 UTC in `production-followup.txt`. This is read-only audit evidence, not a deployment or strategy performance certification.

## Files

- `production-core.sql` / `.txt`: family counts and legacy 5d metrics; snapshot coverage. The all-history chain aggregate hit the 30-second statement timeout. PostgreSQL aborted that transaction, subsequent statements did not run, and the output ends in ROLLBACK. Earlier SELECT outputs remain valid observations. The timeout errors were on stderr and are recorded here rather than hidden.
- `production-followup.sql` / `.txt`: expired-contract counts, income positions, approximate table statistics and bounded recent chain coverage. Read-only defaults and 12-second statement timeouts.
- `production-recent.sql` / `.txt`: October alert validity, GEX nulls, chain-date/500-row distribution and open-position expiry checks.
- `production-calibration.sql` / `.txt`: exact overlap between expired-before-alert candidates and non-null 10d flags selected by the calibration query (1,102 of 1,998).
- `strategy-witness.py` / `.txt`: imports and calls the **actual** pure strategy module, without stubs, network or orders. Records the bearish/no-shares→CSP behavior and acceptance of an undated last-trade leg. This is a witness, not a test endorsing the behavior.
- `source-digests.json`: SHA-256 of the reviewed source files. Local HEAD was `4d3f4997` when recorded; another worker changed HEAD during the audit. The digests identify the source actually reviewed. Other workers' dirty files were not modified by this task.

The SQL files contain no credentials or recipients. Execute only using an authorized read-only production connection. Do not rerun a slow whole-history aggregate merely to obtain an exact count when a bounded query or labelled catalog estimate answers the question.

## Test runs

Working directory: `services/market-data`.

```sh
python -m pytest -q tests/test*option* tests/test*squeeze* tests/test*dark* tests/test*gamma* tests/test*leaps*
```

Observed: **1,050 passed, 3 warnings in 17.19s**. Warnings: two Pydantic class-config deprecations and one FastAPI `regex` deprecation. This selects filenames, not every dependency/service or every relevant test in the repository.

An earlier focused run:

```sh
python -m pytest -q tests/test_sr06_sr07_options_payoff_validity.py tests/test_options_flow_snapshot.py tests/test_options_flow_alert_outcomes.py tests/test_squeeze_alert_outcomes.py tests/test_dark_pool_relative_threshold.py tests/test_gamma_exposure_route.py tests/test_t398_options_income_engine.py tests/test_sr03_sr04_options_flow_accounting.py
```

Observed: **205 passed in 6.28s**. Overlaps the broad run; do not sum these into a claim of 1,255 distinct tests. The full multi-service suite and authenticated browser were not run for this documentation task. Market-data tests use substantial infrastructure mocking; a green suite is not an end-to-end live alert demonstration.

## Interpretation limits

1. Stored candidate rows are not recipient deliveries or executed orders.
2. Stored `is_correct_5d` uses existing legacy conventions, not a new audit definition. Flow/squeeze use directional underlying movement; dark pool uses absolute movement. Neither is actual option P&L.
3. Reported stock returns and model P&L were aggregated, not independently recomputed against exchange/clearing records. This audit found reasons not to promote them to performance claims.
4. Recent chain queries exclude symbols with no row in the selected window. “140 symbols” is coverage in that window, not the entire listed options universe.
5. Repeated exact 500-row slices are a completeness warning; provider pagination/truncation remains an unconfirmed hypothesis.
6. Recent October records show no already-expired contracts at alert date, whereas older records do. Do not portray the old population as proof the current feed is still emitting expired contracts.
7. Snapshot computation dates are not necessarily underlying quote observation dates.
8. Account equity, holdings, permissions and personal risk constraints were not accessed; proposed sizes/limits in the playbooks remain a policy design until account context is supplied.
