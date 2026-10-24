# Multi-expiration search evidence

[Implementation and deployment report](../../2026-10-23-options-multi-expiry.md).

- `validation.json` and `validation-summary.txt`: full checks and release boundary.
- `production.json`: all 15 container health states, unchanged start times for the other 13 containers, build identity and HTTP checks.
- `source.json`: all 12 backend source/shared digest comparisons.
- `tsm.json`: read-only production archive reproduction for 14/60/120 holding days; settled research references, not live executable quotes.
- `served.json`: public TSM and tracker asset markers matched to the running build.
- Companion Python scripts reproduce each check in the existing production/container environment. The TSM script enforces a read-only transaction and statement timeout; it uses calculation helpers rather than impersonating an authenticated user.

Synthetic boundary regressions live in `services/market-data/tests/test_covered_call_candidates.py` and `frontend/src/components/OptionStrategyMatrix.test.tsx`. Original October 22 evidence is untouched. Production timestamps are UTC; the report date uses America/Los_Angeles.
