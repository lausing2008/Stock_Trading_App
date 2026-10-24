# Options quote/event evidence

[Implementation and deployment report](../../2026-10-23-options-quote-event-evidence.md).

- `validation.json` / `validation-summary.txt`: 906 frontend tests, full typecheck, build and rollout boundary. Backend source unchanged; its full suite was not rerun.
- `production.json`: 15 healthy containers, 14 unchanged non-frontend start times, prepared/running frontend build and public HTTP checks.
- `source.json`: unchanged source/shared digest matches for all 12 backends.
- `served.json`: public TSM/tracker asset markers matched to the running build.
- `cache.json` / `read_calendar_cache.py`: Redis GET-only TSM calendar witness, without provider refresh or writes; dates remain reported rather than issuer confirmed.
- `classification.json` / `classify_cached_record.cjs`: exact TypeScript module replay on the saved cache record and expiration examples from the preceding TSM archive witness. Run the CJS script from `frontend`; it uses the installed TypeScript package and no network.
- Companion verification Python scripts run in the existing production/container environment.

Regression tests: `optionCalendarEvidence.test.ts`, `OptionStrategyMatrix.test.tsx`, `OptionsGamePlanCard.test.tsx`, `commonOptionPlans.test.ts`. Original evidence is untouched. This frontend-only release does not introduce live quote observations or confirm issuer event schedules. Timestamps are UTC; report date uses America/Los_Angeles.
