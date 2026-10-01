# September-only production checkpoint

**This is the current requested scope.** It supersedes the broader historical measurements in the [previous production report](2026-09-29-production-checkpoint-verification.md) for this month's assessment. Earlier evidence is retained for provenance, not pooled into these results.

## Period and execution

- Signal cohorts: `signal_date >= 2026-09-01 AND signal_date < 2026-10-01`.
- Flow, squeeze, prebreakout and dark-pool alert cohorts: the same bounds on `fired_date`.
- Dark-pool prints: September execution dates in America/New_York, applying bounds of September 1 04:00 UTC through October 1 04:00 UTC to the stored naive-UTC execution timestamp.
- Benchmark prices: September daily bars only. No August observations or October outcomes are included.
- Outcomes: values available at the live snapshot, **2026-09-30 06:56:07 UTC** (September 29 evening Pacific). This is September-to-date, not a completed September month-end evaluation. The benchmark retry uses a separate snapshot at 06:57:49 UTC.
- Current portfolio configuration is reported separately; it is a current-state read rather than an outcome cohort.

Queries ran in read-only, repeatable-read transactions with 25-second statement and two-second lock limits. No deployment, trading settings, database rows, alerts or provider calls were changed. The probes refuse execution after the September audit window instead of silently incorporating later-resolved outcomes into a replay of this report.

[September probe](evidence/2026-09-29-september-only-production-probe.py) · [September results including exact SQL](evidence/2026-09-29-september-only-production-results.json) · [benchmark retry](evidence/2026-09-29-september-only-benchmark-retry.py) · [benchmark retry results](evidence/2026-09-29-september-only-benchmark-retry.json).

The initial two SPY queries reached their 25-second limits. The retry preserved the filters and timeout but used a non-materialized prices CTE so predicates could reach the underlying table efficiently. Both retries succeeded. The timeouts remain visible in the original evidence, not counted as successful measurements.

## Confidence: September alone has much less evidence

The five-day pooled histogram contains **2,524 resolved September-origin outcomes**, not 19,256. High-confidence bands are especially small:

| Confidence bucket | Resolved n | Five-day success |
|---|---:|---:|
| 0–9 | 25 | 48.0% |
| 10–19 | 175 | 36.0% |
| 20–29 | 185 | 36.2% |
| 30–39 | 937 | 41.0% |
| 40–49 | 722 | 41.6% |
| 50–59 | 261 | 39.8% |
| 60–69 | 126 | 30.2% |
| 70–79 | 60 | 23.3% |
| 80–89 | 25 | 28.0% |
| 90–99 | 6 | 16.7% |
| 100–109 | 2 | 50.0% |

Confidence values range from 4.04 to exactly 100.0, with **zero outside 0–100**. The 100–109 bucket is a bin label, not evidence of an invalid probability above 100.

Using the production band definitions, primary outcome, horizon, direction and market splits—but **restricting observations to September instead of the live consumer's 180-day window**—produces 48 populated slices. Only **23 reach n≥30**. Four supported slices reach the +1 threshold (≥55%); two reach the −1 threshold (≤35%). These are slice counts, not affected trades or evidence for promotion. Pooled-market fallback slices are saved separately.

The current `signal_outcomes` September extract has 2,524 rows and all have primary outcomes. This does **not** imply all September signals have resolved: the outcome table does not represent every outstanding/generated signal. For example, there are no resolved LONG BUY rows in this extract. Later fire dates and long holding periods are underrepresented. A complete maturity denominator requires the original signal cohort and its historical versions; this probe does not reconstruct it.

**Conclusion:** keep calibration feedback unpromoted. The earlier claim that every confidence band has adequate counts does not hold for September. The table is non-monotonic, but tiny upper buckets cannot establish a stable inverse relationship. Current-state verification again found all 11 active paper portfolios with feedback absent/null, which means off under the default-false consumer. No configuration changed.

## Options flow: unchanged because its records already start in September

| Horizon | Direction | Contract rows resolved | Contract success | Raw underlying return | Symbol/date groups | Equal-group success | Equal-group return |
|---|---|---:|---:|---:|---:|---:|---:|
| 5d | bearish | 984 | 29.1% | +4.00% | 174 | 47.1% | +1.08% |
| 5d | bullish | 957 | 55.2% | +3.05% | 156 | 42.3% | +1.01% |
| 10d | bearish | 891 | 43.2% | +2.41% | 136 | 35.3% | +2.65% |
| 10d | bullish | 818 | 59.8% | +2.97% | 122 | 60.7% | +3.41% |

Equal-group success means averaging contract success within each direction/symbol/fire-date, then weighting each group equally. It does not eliminate cross-symbol or overlapping-horizon dependence. Five-day resolved events span 18 fire dates per direction; ten-day events span 14. Labels can coexist for the same underlying/session.

Of 984 resolved bearish five-day contract rows, **598 rose >0.5%, 286 fell >0.5%, and 100 were neutral**. Inversion therefore does not imply 70.9% success. All these returns describe the underlying, not executable options P&L.

The September-only SPY retry reproduced the prior partial matches: at five days, excess returns are approximately +1.85 percentage points for bullish groups (125/156 matched) and +1.84 for bearish groups (135/174). At ten days, only 48/122 bullish and 53/136 bearish groups match. Missing/mismatched price reconciliation, selection effects, market/sector exposures, execution costs and dependence still prevent an alpha claim. No full matched-universe or prospective trading experiment was run.

**Conclusion:** retain the caution against automatic inversion. September-only filtering does not repair the direction/cohort interpretation or contract duplication.

## Prebreakout: smaller and more concentrated in September

**36 events across six names**, versus 52 events/eight names in the broader checkpoint. AI contributes **18/36 = 50%** of September fires. Five-day outcomes cover only five names; one name has no resolved five-day outcome yet.

| Horizon | Resolved | Success | Mean raw return |
|---|---:|---:|---:|
| 1 calendar days | 31 | 35.5% | -0.02% |
| 3 calendar days | 29 | 34.5% | +0.70% |
| 5 calendar days | 27 | 33.3% | -0.43% |
| 10 calendar days | 20 | 55.0% | +1.33% |
| 20 calendar days | 12 | 33.3% | -2.15% |

The positive ten-day mean is relevant: do not describe every horizon beyond three days as negative. It is still a tiny correlated cohort, with only 12 resolved twenty-day outcomes across four names. Keep this experimental; do not loosen entry criteria or choose the best-looking horizon from this table and call it validated.

## GEX and squeeze

Non-null GEX measurements are unchanged by the September restriction. Within the observed five-day groups, thesis-signed mean return remains approximately **−1.87% corroborated versus +2.28% uncorroborated**. This corrects a raw-return comparison that mixed bullish and bearish theses; the 13-outcome uncorroborated arm remains too small for promotion or inversion. NULL/pre-instrumentation records are excluded from that comparison, not treated as controls.

The **September squeeze family totals 247 events**, including only **five short-squeeze events** (four resolved, three names, two resolved fire dates). Its resolved mean raw return is −2.23%. Ignition remains six events across five names, mean −5.26%. The previous 16-event short-squeeze count included earlier months and must not be used as September support.

## Dark pool

All **375,411 persisted prints across 78 names** fall within September, so those counts are unchanged. The daily output now covers the full month-to-date rather than just a trailing 21-day interval.

The expanded daily history contains alert outcomes on dates with no same-session persisted prints, including September 2–3 and some non-session dates. These are historical outcome records, not proof of delivery or proof of a current ingestion failure. They reinforce why missing coverage cannot be treated as zero activity, and why old/new behavior should be split by a verified fix/version boundary before attributing improvements.

Recent daily ratios of alerting names to names with observed prints remain roughly 81–88%. Neither historical 78-name coverage nor the full tracked universe establishes each scan's eligible population. Record scan eligibility/freshness separately before changing the selectivity threshold.

## Decisions under the September-only scope

1. Use this month's results as the main assessment; retain older measurements only as explicitly labeled historical context.
2. Keep calibration feedback off. September high-confidence and horizon-specific support is weaker than the earlier report implied.
3. Do not invert bearish flow or GEX. Reconcile price matches and test direction contribution against a contemporaneous eligible-universe baseline.
4. Keep prebreakout/short-squeeze/ignition experimental. Do not pool August observations to satisfy September support requirements.
5. Continue distinguishing event outcomes, delivered alerts and executed trades. September restriction alone does not establish which fixes caused improvement; use verified deployment/version boundaries inside the month for that question.

Anti-chase realized rejection remains unmeasured; no instrumentation or strategy changes were introduced in this run.
