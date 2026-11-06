# UW 429 retry: first-session review (2026-11-03)

This reviews the first US session (Mon 2026-11-02) after market-data `f5eabd6a` + `8c5ca84c`
(AUD-UW-429-RETRY and AUD-DQ-NAMEONLY), plus the checks scheduled for the weekend. An independent
code review of both commits ran at the same time.

**Status:** written 2026-11-03 as documentation only. On 2026-11-05 P3 and P5 were built as a
frontend-only change (tracker tier 480); see §7. P1, P2, P4 and P6 still wait for approval.

Sources:
- Redis counters and the `market-data` container log. The container started 2026-11-01 18:09 UTC, recreated by another session's `deploy_remote.sh` at HEAD `6797e1b9`, a frontend-only commit, so the market-data code was unchanged. The log is capped at 5 × 20 MB.
- PostgreSQL `bar_provenance`, `option_chain_coverage` and `option_chain_history`.
- Times are UTC. US clocks changed on Nov 1, so the regular session is 14:30–21:00 UTC from Nov 2 and 13:30–20:00 UTC before that.

## 1. What UW's limit actually is (answered)

Every refusal body logged on Nov 2 says the same thing:

> "Too many requests in flight. Your account can have **3 API requests running at a time across
> all of its API tokens** … This is a temporary concurrency limit, not your daily request quota,
> and rejected requests do not count toward the quota."

Headers on the same responses: `x-uw-req-per-minute-remaining: 1000000`, reset 60000 ms.

So the limit is **3 concurrent requests per account**. It is not per minute and not per day. The
bar ingest alone runs 6 worker threads, and the 5m and daily jobs overlap. Every other UW caller in
the platform shares the same 3 slots: option chains, flow, dark pool, GEX, the screener and so on.

## 2. How the retry performed on Nov 2

| | Oct 29 | Oct 30 (120 s cooldown + skip) | Nov 2 (retry + 30 s cooldown) |
|---|---|---|---|
| First 429s on the bar endpoint | — | — | 133 (`attempt: 1` log lines; counter `rate_limited_retried` ≈ 145 across hours) |
| Fetches that failed on 429 (`rate_limited`) | 75 | 63 | **3** |
| Symbol-runs skipped during the cooldown (1d + 5m) | — | ≈ 7,200 | **628** |
| US 5m regular-session bars written by yfinance | 87 | 84 | 73 (UW: 12,058) |
| US 5m regular-session bars > 5 min late | ≈ 1,100 | ≈ 1,960 | **245** |
| Yahoo 429 / "Too Many Requests" (excluding UW lines) | not measured (log lost) | not measured (log lost) | **0** |

- **Late bars.** These count `first_written_at - ts - 300 s > 300 s` for regular-session, on-grid bars, using each day's correct UTC window.
  - The Oct 29/30 figures come from the 2026-11-01 query.
  - A later re-query used 14:30–20:55 UTC for every day. That is wrong for the EDT days: it pulls in post-close bars, giving p90 values of 40–60k s, and must not be quoted.
- **Live-price refresh.** It ran with `markets=["HK"]` overnight, for example `count=42 carried=160`. That matches the per-market design.
- **Caveat.** This is one session. Nov 2 may simply have been a quieter day for UW contention.

## 3. Weekend and scheduled checks

- **Tiered option-chain purge (Sun 23:31 UTC).**
  - Redis `scheduler:job:option_chain_history_purge` reports `ok` in 70.2 s.
  - The `opthist.purged` log line, which carries row counts, had already rotated out of the 100 MB log cap, so how many rows were deleted is **not known**.
  - Afterwards the oldest `as_of` is 2024-08-23, and 35 symbols still hold rows older than 100 days. That fits the 800-day LEAPS tier, but **the 35 have not been checked against the LEAPS list**.
  - The table still reports 16 GB on disk. A plain DELETE does not shrink the file, which is expected.
- **Weekend coverage markers.** `option_chain_coverage` has **no rows** for 2026-10-31 or 2026-11-01. The weekend skip held.
  - The latest coverage date is 2026-10-30 (161 symbols).
  - Nov 2 had not been captured at 05:13 UTC Nov 3. Whether that is on schedule has not been checked.
- **Data-quality checks.** Both repaired checks pass in production:
  - `capture_prospective_estimates`: age 28.2 h, limit 38;
  - `check_llm_daily_budget`: age 0.0 h, limit 2;
  - `dq_check.run_failed`: 0 since the container started.

## 4. Independent code review (f5eabd6a, 8c5ca84c)

The review found nothing blocking. It confirmed the following:
- No API key in the logs: only response `x-*` and `retry-after` headers are kept, plus the first 300 characters of the error body.
- Exceptions raised on the retry still reach the existing `timeout` and `other_*` handlers.
- The DQ `job_name` matches the key the job writes, on every exit path.
- The 38 h capture threshold is right: about 35 h is the healthy maximum, or 36 h if the run is late but within its misfire grace.

**Finding R1 (low, confirmed, not fixed): recovered retries show as errors on the admin page.**
- The adapter writes `rate_limited_retried` under the failure prefix `stockai:metric:uw_adapter_failures` (`unusual_whales_adapter.py`, retry branch).
- `services/market-data/src/api/admin.py` (~2194–2206) sums every reason under that prefix into the 24 h total.
- `frontend/src/pages/admin-health.tsx` (~1054–1075) shows that total as "N errors / 24h", described as bar-ingest failures.
- Effect on Nov 2: about 145 recovered refusals counted as errors, against 3 real failures.
- Fix: exclude `rate_limited_retried` from the total, or move it to its own prefix.

**Test weaknesses (not fixed):**
- In `tests/test_dq_check_shapes.py`, the assertion `"'llm_daily_budget_check'" not in keys` is vacuous, because `keys` holds key names, not values. The `job_name` value is never checked against `_record_job_status`.
- The shape tests read the AST and source text, and never run `run_data_quality_checks`.
- `_REQUIRED` does not cover `max_age_hours` on ratio and gauge entries, which the runner's `except` path reads. This gap predates these commits.

**Minor:**
- A thread already asleep before its retry does not re-check the cooldown, so up to 6 extra calls can go out after the cooldown is armed.
- The tracker text for `UW-COOLDOWN-YF-SPILL` (`improvements.tsx` ~239) still tells readers to watch `uw_bar_minute_remaining`, which has been removed.

## 5. Other findings from the Nov 2 log

1. **SKHYV and US10Y fail on every 5-minute run with both providers.** Each failed 157 times on Nov 2, with "All adapters failed … possibly delisted; no timezone found".
   - UW answers 422 for them, at about 24 an hour in session. Each of those requests still takes one of the account's 3 concurrency slots, and yfinance then makes a wasted call.
   - US10Y is a Treasury yield, not an equity. SKHYV looks like a placeholder ticker.
   - The same two symbols show up in the fundamentals work as "partial answers" (`docs/audits/2026-10-30-yfinance-usage-audit.md`).
   - Also seen: 1671.HK failed 3 times and SSNLF once.
2. **The DB connection pool ran out 3 times:**
   - `alert.check_error` at 16:45;
   - `squeeze_ignition_alert.failed` and `little_rzy.drain_failed`, both at 20:40;
   - plus 3 ASGI exceptions in `shared/common/service.py` dispatch.
   Another session's `pool_watch` (2609ff6c) now logs connection holders when the pool is within 2 of its limit. Read its output before acting; this belongs to that session's open pool investigation.
3. **`scheduler.http_failed`** at 22:30: `event-intelligence /events/sync/8k` timed out 3 times.

## 6. Proposed (awaiting approval; nothing built)

| # | Change | Why | Size |
|---|---|---|---|
| P1 | Cap concurrent UW bar requests (for example a Redis-backed semaphore of 2, leaving 1 slot for the other UW callers), or cut the ingest pool to 2 workers for US/UW | The limit is now known to be 3 in flight per account. The retry absorbs most refusals, but each still costs at least 1.5 s and blocks other UW callers. | S–M |
| P2 | Stop intraday/daily ingest of SKHYV and US10Y, by marking them non-ingestable or routing US10Y to its index source | About 314 doomed requests a day, half of them taking UW concurrency slots | S |
| P3 (**done**, §7) | Exclude `rate_limited_retried` from the admin error total (R1) | The admin badge reports about 50× more errors than actually happened | XS |
| P4 | Make the DQ test real: check that `job_name` is a key `_record_job_status` writes, and run the runner on synthetic ages | Test weaknesses above | S |
| P5 (**done**, §7) | Update the stale tracker text that mentions `uw_bar_minute_remaining` | Docs accuracy | XS |
| P6 | Compare the 35 symbols with old option-chain history against the LEAPS list, and confirm the Nov 2 chain capture runs | Purge and capture verification gaps | XS (read-only) |

The incident file (`docs/incidents/yfinance-rate-limit-amplification.md`, AUD-UW-429-RETRY) and
`docs/2026-10-28/open-items-status.md` should point here. That was not done, to keep this change
to one new file. Their copies in the main checkout are behind `origin/prod`.

## 7. Built 2026-11-05 (frontend only)

Asked for fixes that could ship without touching the market-data service, so only P3 and P5:

- **P3.** `frontend/src/lib/uwAdapterFailures.ts` splits `adapter_failures_24h` into fetches
  that failed and refusals recovered on the retry. Admin Health's UW badge, panel border, total
  and cause table now use the failed part; recovered retries show on their own grey line.
  The API still returns `adapter_failures_24h_total` including retries; it was left alone so
  that market-data does not change.
- **P5.** The `UW-COOLDOWN-YF-SPILL` note no longer tells readers to watch
  `uw_bar_minute_remaining`. `UW-BAR-REQUEST-SPACING` now states the answer (3 in flight per
  account) and P1 as its fix, still to do.
- **Tests.** 4 new vitest cases; removing `rate_limited_retried` from the recovered set fails 3
  of them. `tsc` clean, full frontend suite 1,007 passed.

