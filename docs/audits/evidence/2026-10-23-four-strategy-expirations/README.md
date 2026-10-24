# Four-strategy expiration comparison evidence

[Implementation and rollout report](../../2026-10-23-four-strategy-expirations.md).

- `validation.json` / `validation-summary.txt`: full-suite results and release boundary.
- `compare_original_payoffs.py`: 200 complete original matrices compared with deployed baseline `1b3aee91`; no network or DB.
- `production.json`: all 15 containers healthy, other 13 start times unchanged, image/build identity and public HTTP checks.
- `source.json`: source/shared digest matches for all 12 backends.
- `tsm.json`: all four strategy searches at 14/60/120 days, with four shared additional archive reads per period.
- `served.json`: public TSM/tracker asset markers matched to the running frontend build.
- Companion Python scripts reproduce the checks in the existing production/container environment. The TSM script enforces a read-only transaction and statement timeout and invokes calculation helpers rather than impersonating a user.

Regressions live in `test_covered_call_candidates.py`, `OptionStrategyMatrix.test.tsx` and `commonOptionPlans.test.ts`. Original evidence is untouched. Production archive witnesses are settled research comparisons, not executable quotes, proven optimal choices or authenticated browser acceptance. Timestamps are UTC; report date uses America/Los_Angeles.
