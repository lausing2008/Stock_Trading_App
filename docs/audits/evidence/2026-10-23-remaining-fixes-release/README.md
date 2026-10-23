# Remaining fixes release evidence — October 23, 2026

The PostgreSQL ledger probe runs against session-temporary tables only, under an outer transaction that is rolled back, including its DDL. It verifies that the tables disappear afterwards. It does not read or mutate production event rows. The checked aggregate result is in `ledger-postgresql-probe.json`; the executable witness is `services/market-data/tests/_dark_pool_ledger_probe.py`.

The first image invocation failed before connecting because the test harness assumed repository path depth. The harness now supports the container layout; the unchanged application ledger passed via stdin in the release image. SQLite regression tests cover the same state transitions. Deployment status and suite totals are recorded in the linked release report.
