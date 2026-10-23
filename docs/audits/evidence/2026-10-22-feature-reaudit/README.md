# Re-audit evidence, October 22, 2026

Report: [Options, off-exchange activity and AI Signals](../../2026-10-22-options-dark-pool-ai-signals-reaudit.md).

Scope: local checkout `994e967be802ee17c9d0e6046ef8e74129974d5e`. No fresh production capture. Source hashes are in `probe-results.json` and `threshold-inventory.json`; existing deployment/backup work was not modified.

## Reproduce the Python witnesses

From the repository root:

```bash
python3 docs/audits/evidence/2026-10-22-feature-reaudit/probe.py
```

Requires the existing Python environment's numpy/pandas. The script reads source with AST, executes selected real function bodies with explicit fake external inputs, and writes only this evidence directory. No application imports, network requests, Redis writes, database calls or calibration POSTs. The synthetic first-tune case uses the real pure sweep and production weight function, with a supplied policy and action-coverage verdict. A passing assertion confirms the current finding, not that it is fixed.

`threshold-inventory.json` contains current module-level literal constants, not dynamically resolved settings or an exhaustive extraction of numeric expressions inside functions. Consult the report and the previous detailed horizon inventory for the full decision path.

## UI/calculator witnesses

Copy `ui-witness.test.tsx` into `frontend/src/__tests__/feature-reaudit-witness.test.tsx`, run that file using the normal frontend Vitest configuration, and remove the temporary copy afterward. The saved file uses the repository alias `@` and renders the real card. During this audit the temporary copy was removed; no maintained application/test file was changed.

The three passing witnesses show cached-data refresh error suppression, disappearance of a valid held fill when its quote is missing, and display of a frozen response age. Convert them to desired-behaviour regression tests when implementing fixes.

## Fresh validation record

Commands ran in the named service directories. Results below are a compact execution record, not archived raw logs. No full-suite or production verification is implied.

Market-data, 133 passed:

```bash
python3 -m pytest -q tests/test_options_scope.py tests/test_options_flow_null_premium.py tests/test_o02_premium_availability.py tests/test_options_pressure_score.py tests/test_dark_pool_evidence.py tests/test_dark_pool_route.py tests/test_dark_pool_relative_threshold.py tests/test_options_flow_snapshot.py tests/test_premarket_options_flow_note.py
```

Market-data, 125 passed:

```bash
python3 -m pytest -q tests/test_options_game_plan.py tests/test_options_game_plan_snapshot.py tests/test_t402_options_strategies.py tests/test_options_game_plan_greeks.py tests/test_options_game_plan_batch_route.py tests/test_options_flow_alert.py tests/test_options_flow_alert_backtest.py tests/test_options_flow_alert_backtest_signadjust.py tests/test_options_flow_alert_outcomes.py
```

Signal-engine, 429 passed:

```bash
python3 -m pytest -q tests/test_signal_generator.py tests/test_ml_weight_runtime.py tests/test_calibrate_ml_weight_ratchet.py tests/test_aq05_calibration_eligibility.py tests/test_sig3_watchdog_and_cap_sign.py tests/test_watchdog_override_clear.py tests/test_watchdog_self_tuning_report.py tests/test_tune_strategy.py tests/test_tune_style_profiles_baseline.py
```

Frontend, 40 passed:

```bash
npm test -- src/components/OptionsFlowCard.test.tsx src/components/MarketPressurePanel.test.tsx src/components/OptionsGamePlanCard.test.tsx src/lib/optionsFlowStatus.test.ts src/__tests__/options-flow-review.test.tsx src/__tests__/options-flow-direction-card.test.tsx
```

Frontend, 137 passed (134 existing plus 3 witnesses):

```bash
npm test -- src/__tests__/feature-reaudit-witness.test.tsx src/lib/optionsGainLoss.test.ts src/lib/optionsChainChart.test.ts src/lib/optionsTabCharts.test.ts
```

Documentation checks: all relative links resolve; saved source hashes still match; JSON parses; `git diff --check` passes. No application build/typecheck or new PostgreSQL integration suite was run for this documentation-only change.
