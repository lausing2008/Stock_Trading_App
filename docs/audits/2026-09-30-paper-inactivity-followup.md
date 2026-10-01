# Paper activity after September 8: production follow-up

Snapshot: 2026-10-01 06:44 UTC (September 30, 23:44 PDT). User date `08092026` interpreted as September 8; August 9 alternative also counted in evidence. SELECT-only repeatable-read database snapshot plus Redis GET/TTL; no entries, configuration changes or marker resets.

## Finding

All 11 stock-paper portfolios are active. **Seven entries occurred after September 8**: portfolio 1 on September 11 and 30; portfolio 4 on September 17; portfolio 6 on September 23; portfolio 7 on September 15; portfolio 8 on September 9 and 18. Most recent entry: portfolio 1, September 30 18:36:39 UTC (14:36 EDT). This disproves a platform-wide stop on September 8 but does not disprove inactivity in the specific portfolio the user is viewing. This check covers `paper_portfolios` / `paper_trades`, not separate options-income ledgers or browser rendering.

| Portfolio | Current explanation from last 48 hours of retained scan evidence |
|---|---|
| 2, HK SWING | 139 consecutive-loss blocks; recovery marker value 4 still present, TTL 499,614 seconds (~5.8 days). No open positions; latest historical entry June 25. |
| 5, US SWING | 244 consecutive-loss blocks; recovery marker value 10 still present, TTL 543,092 seconds (~6.3 days). No open positions; last entry September 4. |
| 9, HK SWING | All 140 recorded scans saw zero candidates. No trades recorded. Candidate production/universe eligibility needs tracing upstream; this is not evidence of a stopped scheduler. |
| 4 and 10, HK GROWTH | Each had 130/140 scans with zero candidates; the remaining ten candidate checks were rejected for price drift. Portfolio 4 last entered September 17; portfolio 10 has no recorded trades. |
| 3, 6 and 891, US SWING | Main recorded checks: watchlist exclusion, `already_open_scale_in_only`, conviction. Last entries September 3, September 23 and none respectively. The `already_open` label is historical scan telemetry; current open positions are zero. Its scope and any cross-portfolio behavior require separate tracing, not inference from the label. |
| 1 and 7, US GROWTH | Main checks: watchlist, conviction and price drift. Portfolio 1 nevertheless entered September 30; portfolio 7 last entered September 15. |
| 8, US LONG | Main checks: watchlist, entry-qualifier rejection labeled `entry_score_below_threshold`, conviction. One position remains open. Label does not prove numeric score alone caused rejection. |

## Why fixes have not restored broad activity

Fixing premature recovery consumption does not delete the old consumed markers. Both markers remain live. Other fixes do not guarantee that portfolios receive candidates or that valid risk/selection gates pass. No attribution of the September 30 entry to a particular remediation is established here.

Logs are retained from September 22, so they explain recent inactivity, not every day since September 8. Existing no-entry telemetry is not a complete all-scan denominator. Counts repeat opportunities across scans; never interpret them as distinct lost trades. Portfolio-blocked scans can have NULL candidate counts: a blocked scan is not necessarily an evaluated zero-candidate universe. No full deployment verification or UI audit was performed.

## Recommended next actions

1. Resolve portfolio 2's suspected phantom marker through durable trade/order checks and the separately pending approval. Leave portfolio 5's recovery exception unchanged pending its loss/risk-policy review. No reset performed.
2. Trace HK candidate supply from fresh signals through horizon/market filters and watchlist/ranking membership, especially portfolio 9; for HK GROWTH trace why the ten available candidates had stale/drifted entry plans.
3. Trace one current rejected US candidate end-to-end: authoritative conviction, input identity, watchlist rationale, existing-position scope and final decision. Do not assume every logged gate is defective or lower them all.
4. Add per-portfolio last scan, last entry, candidates seen and binding reason to the UI, with a clear distinction between zero supply, rejected candidates and portfolio-level blocks.
5. If the UI shows no entries globally after September 8, verify its selected portfolio, date/horizon filters and API response against the recorded September 30 entry before diagnosing a rendering/data-fetch defect.

Evidence: [production query/results](evidence/2026-09-30-paper-inactivity-results.json), [probe](evidence/2026-09-30-paper-inactivity-check.py), [candidate supply](evidence/2026-09-30-paper-inactivity-supply.json). SQL is embedded in each result for reproducibility. This investigation changed no production state.
