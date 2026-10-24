# Delta/DTE preference evidence

[Implementation and deployment report](../../2026-10-23-options-delta-dte-preferences.md). Work began October 23 and completed October 24 in America/Los_Angeles; filenames retain the start date.

- `validation.json` / `validation-summary.txt`: 5,849 backend passed / 26 skipped; 928 frontend passed, full typecheck and release boundary.
- `production.json`: 15 healthy containers, 13 unchanged other-container start times, prepared/running frontend identity and public HTTP checks.
- `source.json`: source/shared digest matches for all 12 backends.
- `served.json`: public TSM/tracker markers for preferences, coverage, acknowledgement and rejection wording.
- `tsm.json` / `read_only_tsm.py`: source-session-pinned, read-only deployed calculation witness for blank, bounded, zero-delta and empty-DTE searches. It enforces repeatable-read isolation and a per-statement timeout; it does not impersonate an authenticated user or refresh quotes.
- Companion Python scripts reproduce production checks in the existing container environment.

Regressions: backend `test_option_search_preferences.py`, `test_covered_call_candidates.py`; frontend `optionSearchPreferences.test.ts`, `OptionStrategyMatrix.test.tsx`, `commonOptionPlans.test.ts`. The 200-case original-matrix equivalence script remains in the four-strategy evidence folder. Original evidence is untouched. Bounds are explicit research choices, not optimized defaults, current quotes or return forecasts.
