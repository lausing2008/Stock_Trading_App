# Option candidate comparison evidence

[Implementation and rollout report](../../2026-10-24-options-candidate-comparison.md).

- `validation.json` / `validation-summary.txt`: 936 frontend tests, full typecheck and release boundary. Backend code unchanged; backend tests not rerun.
- `production.json`: 15 healthy containers, unchanged starts for all 14 non-frontend containers, prepared/running build and public HTTP checks.
- `source.json`: source/shared matches for all 12 backends.
- `served.json`: public TSM/tracker markers for the comparison, sorting and rounded-spread wording.
- `tsm.json` / `read_only_tsm.py`: actual deployed-helper archive sample, 12 alternatives per strategy, with a pinned session, read-only transaction and statement timeout. No user impersonation or provider refresh.
- `replay.json` / `replay_comparison.cjs`: exact TypeScript sorting and React static rendering on the saved sample. Run from `frontend` with the installed dependencies; no network or DB. It verifies 20 strategy/sort-mode mappings and unchanged source arrays.
- Companion Python scripts reproduce deployment checks in the existing container environment.

Tests: `optionCandidateComparison.test.ts`, `OptionCandidateComparison.test.tsx`, integrated `OptionStrategyMatrix.test.tsx`. Original evidence is untouched. This is a view-only ordering of bounded returned research candidates, not a full-chain rank, optimal-return validation, fresh quote assertion or authenticated browser acceptance.
