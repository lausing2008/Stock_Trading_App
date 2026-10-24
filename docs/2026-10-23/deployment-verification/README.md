# Independent deployment verification — October 23, 2026

User requested a 30-minute wait then deployment, followed by a check of everything deployed. The wait began at 14:01:31 UTC, with an earliest rollout time of 14:31:31 UTC. On the later continuation, another release process had completed the rollout. This verification did not repeat the completed deployment or restart containers.

Fresh read-only checks around 23:29 UTC confirm:

- EC2 checkout: `e0831bb999488772f9c38a47b8ef8b59ea6fe791`.
- Market-data and signal-engine image release label: `3f838758e09d6e84423c345569483eeb6f23a1d9`.
- All 15 application/database/cache containers running and healthy.
- All 12 backends' complete Python `src` and `shared` content hashes match a clean archive of committed `e0831bb9`. Later test/documentation commits do not require relabelling unchanged application code.
- Frontend build: `KbuB0rqZXxsd9TeuRUxhb`; no `localhost:8000` occurrence in `.next/static`.
- HTTP checks for `/`, `/stock/MU`, `/signal-tuning` return 200 and include the running build ID. These are HTTP checks, not interactive browser acceptance.

The deployed fixes include nightly full-list chain capture, options/dark-pool audit changes, identity-ledger work and calibration safeguards described in `docs/audits/2026-10-23-remaining-options-calibration-fixes-and-deployment.md`. This independently verifies code deployment; it does not close that document's research, provider-semantics or prospective evidence limitations.

Uncommitted chart/intraday-history, daily-cross alert, backup/deploy-lock and news work remains outside the committed release. “Everything deployed” refers to ready committed fixes, not unfinished working-tree edits.

Before the continuation, this review also ran isolated `7b1ae2f6` suites: market-data 5,753 passed / 26 skipped, signal-engine 940 passed / 1 skipped, frontend 809 passed and TypeScript clean. These are earlier-release checks, not represented as tests of later code. The other session's final release validation is recorded separately in its deployment report.

Raw current image IDs, timestamps, hashes and health are in `production.json`.
