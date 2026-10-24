# Options dominance and dark-pool identity evidence

October 23, 2026 (America/Los_Angeles). Feature changes are local and not deployed. See `docs/audits/2026-10-23-options-dominance-and-dark-pool-identity.md` for scope and interpretation.

- `dark-pool-identity-live.json`: one bounded read-only aggregate query; no trade IDs/payloads are returned. It compares retained histories of currently ambiguous IDs, not the exact origin capture. Quarantine is unchanged.
- `dark-pool-identity-pg-temp.json`: synthetic PostgreSQL temporary-table probe; DDL/data rolled back and table disappearance verified. No production event rows accessed.
- `identity-pg-temp-probe.py`: witness for the same diagnostic SQL on a development PostgreSQL connection. The application connection stays in-process; script output is counts/status only. The recorded production temporary-table check injected the same local diagnostic source into the container process over stdin rather than deploying it.
- `guard-removal-results.json`: regression failures after removing guards in temporary copies; real files unchanged.
- `validation.json`: final current-tree suite totals. Focused route/UI checks are subsets, not additional full-suite tests.
