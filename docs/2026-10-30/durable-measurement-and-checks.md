# Durable measurement, RZY rebuilds and Friday's checks, 2026-10-30

Requested on Fri Oct 30:
- persist price-risk scan records and start a fresh five-session trial, with email kept off;
- build durable receipts for paper entry attempts;
- investigate RZY rebuilds without relaxing checks or changing rules;
- capture Friday's service identities and cooldown deployment status;
- count the handled provider errors and measure data coverage.

| Item | Status |
|---|---|
| Price-risk scan records | Built and tested. Deployment: see [Deployment](#deployment) |
| Fresh five-session trial | Starts with the first complete US session after deployment. Oct 23–29 is exploratory evidence only |
| Paper entry receipts | Built and tested. First production attempts are expected Mon Nov 2 |
| RZY rebuild investigation | Done, read-only: [rzy-rebuild-investigation.md](rzy-rebuild-investigation.md) |
| Friday US check | Combined-release check: the UW cooldown commits shipped, and `49802ce5` was deployed mid-session. Open burst and post-close evaluation passed; the scheduler's call to the evaluation times out |
| Provider errors and coverage | Counted; fundamentals coverage measured, not inferred from HTTP 200 |

Price-risk email delivery stays off. Stage C stays deployed, with US daily-path verification
pending. Intraday generation stays disabled.

## 1. Price-risk scan records and the fresh trial

**What was missing.** Each scan's coverage, quote refusals and exclusions went only to a log line
and a last-run Redis key. Logs are lost on every container recreate, so the Oct 16–22 cohort could
not be re-checked ([review](../2026-10-29/deferred-checkpoints-review.md#2-price-risk-shadow-review-event-fri-oct-23)).

**What is stored now.** One `price_risk_scans` row per `detect()` call. A scheduled run writes two
rows, one for symbols and one for benchmarks, sharing a `run_id`. Each row holds:
- **Coverage per market.** Subjects measured from a live quote, measured from settled bars,
  excluded before measurement, or measured with an unusable window. Every subject is counted
  exactly once, and live-quote ages are summarised as n, min, median and max.
- **Exclusions.** Counts by factor, plus every excluded subject by name with its factor, reason
  and stage. Measured subjects are counted, not listed.
- **Quote refusals.** Counts by status and the symbols in each.
- **Events.** Every event created, with its delivery state (`shadow_suppressed`, `history_only`
  or `eligible`) and its receipt count.
- **Suppressions.** Every candidate that did not become an event, and why:
  - `same_episode`: the rule's own non-escalation reason;
  - `open_pullback_episode`;
  - `event_key_exists`.
- **Configuration.** Runtime overrides, freshness and delivery limits, rule constants and
  baskets, with a sha256 `config_hash` over all of it. A threshold change gives a different hash.
- **Release identity.** `release_sha` from the deploy, plus a `code_fingerprint`: a sha256 of the
  loaded detector, rules, baskets, price-basis and calendar modules. The fingerprint changes with
  the code even if the variable is stale.
- **Session and run state.** US and HK session dates, regular-session flags, and the delivery
  flag as read.

**Integrity.**
- The scan row is written in the same transaction as its events, so an event cannot exist
  without the scan that produced it.
- A scope that raises still leaves a `failed` row, written afterwards in a fresh session, so a
  missing scan is visible.
- On PostgreSQL a trigger refuses UPDATE. DELETE is left to a future retention policy.

**Trial rule** (`scripts/measurement_report.py`). A US session counts only if all five scheduled
US scans are present as completed symbol-scope records:
- the scans are 10:05, 12:05, 14:05 and 15:40 ET, plus the 20:10 ET settled-bar scan;
- every record carries one `config_hash` and one release;
- delivery is off throughout.

A session missing any scan is reported as incomplete, with the missing slots named. Thresholds
are unchanged for the trial. Oct 23–29 stays as exploratory evidence and is not counted.

**Size.** Only non-measured subjects are listed by name, so a row is a few kilobytes. That is
about 14 rows per weekday. No retention policy yet.

## 2. Paper entry receipts

One `paper_entry_receipts` row per `_open_paper_trade` attempt, opened or rejected, organic or
conditional order. Each row holds:
- the outcome and reason (`opened`, or the rejection reason such as `min_position`, `sector_cap`
  or `insufficient_cash`), plus the trade id when opened;
- the candidate's price, with its source (`live_cache`, `live_cache_last_snapshot`,
  `yf_download_2d_daily` or `unrecorded`), fetch time and session date;
- every open position valued for the sector check, with its mark, whether it was live or the
  `entry_price` substitute, and that mark's fetch time and session date;
- the freshness result, one of:
  - `not_evaluated` (stopped before the reservation);
  - `no_sector_positions`;
  - `all_marks_live`;
  - `fallback_marks`;
  - `unvaluable`;
- `would_block_on_fresh_marks`, `decision_would_change`, and `fresh_marks_enforced` (false:
  the policy stays off);
- committed, reserved and projected exposure, and the cap;
- the market session date and regular-session flag;
- release identity, as for scans.

**Writing never changes the trade.** The receipt is written in a savepoint of the entry's own
transaction. A failure is logged as `paper.entry_receipt_failed` and the entry proceeds. It uses
the same connection, never a second one ([pool exhaustion](../incidents/db-connection-pool-exhaustion.md)).

**Reporting rule.** If no attempt reached the reservation, the result is "no opportunity to
evaluate", never a pass. The report gives the rate as "N / M evaluated attempts".

**Still open.** Whether a 2-day daily close from the download fallback counts as a fresh mark is
a policy question. Receipts now name that source, so it can be decided on evidence.

## 3. Release identity

`scripts/deploy_remote.sh` exports `STOCKAI_RELEASE_SHA`: the commit, with `-dirty` when tracked
service, shared or docker files differ. `docker-compose.yml` passes it into every Python
container. A container recreated any other way reports `unrecorded`, never a guess. Only
market-data reads it today.

## 4. Tests

- **Price-risk probe:** 14 new scan-record checks. Three sabotages (an excluded subject with no
  status, dropped suppressions, dropped refusal symbols) each failed it.
- **Entry-receipt probe:** 19 checks. Three sabotages (fallback misclassified as live, marks
  dropped, insert skipped) each failed it.
- **PostgreSQL** (`make test-integration-pg-schema`): 11 of 11, including the new receipts test.
  Removing the trigger failed it.
- **Existing suites:** price-risk detector and probe (32) and portfolio concentration (23) pass.
- **`make test`** on the commit rebased onto `49802ce5` ([output](evidence/make_test.txt)): every
  backend suite and the scripts suite pass, including market-data's 6,083.
  - **Two tests updated.** `test_aud_pth03` now reads the sizing body in
    `_open_paper_trade_sized`. The host-lock test excludes the two read-only git calls and checks
    that they run after the pull, under the lock.
  - **The one failure is pre-existing:** research-engine's `test_candidate_qualification.py`
    cannot import `intelligence`. It fails the same way at `b345752a` and at `49802ce5`, came in
    with `55dae269`, and is not touched here. The other 794 research-engine tests pass with that
    file excluded.

## 5. Friday's US check: a combined-release check

Running identities, read at 13:32 UTC ([evidence](evidence/us-check/01-identities-and-open-burst.txt)):
- The host is at `b345752a`, and `check_deploy_drift.sh` from a clean `b345752a` worktree reports
  12 of 12 backends OK.
- **The UW cooldown commits shipped** (`1d85a240`, `ad55a635`).
- Also shipped:
  - per-market Yahoo live prices and cached price alerts (`851b6189`, `9772c2ab`);
  - the fundamentals failure cache and the paper engine reading the live cache (`5e2127a3`);
  - the closed-market close rule (`41d55f51`);
  - candidate qualification (`55dae269`).
- market-data was recreated at 04:57 UTC and research-engine at 00:58 UTC.

**A further release landed mid-session.** At 13:40–13:45 UTC another session deployed `49802ce5`
(SEC verification, valuation jobs and outcomes) to market-data, research-engine and the frontend,
through the locked script (`valuation-report`, exit 0). The host is now at `49802ce5`, and the
identities read at 13:32 describe only the session's first ten minutes.

Friday's US results therefore describe a **combined release** (Stage C, the UW cooldown, the
commits above and `49802ce5`) and cannot isolate Stage C's effect.

**Results:**
- **US open burst:** all four horizons wrote at 13:33 UTC, 127 rows each in 15 minutes, against
  156 rows over the previous 20 hours. No tracebacks.
- **Outcome evaluation:** HK's post-close evaluation ran at 08:30 UTC (26,986 rows vs a baseline
  of 26,881).
- **US post-close evaluation, read at 20:45 UTC: passed, with a caller-side timeout.**
  - signal-engine finished at 20:34:51: 234 outcomes evaluated, 376 horizon outcomes resolved,
    2,025 skipped as still open, 27 skipped for no price, 0 censored.
  - `signal_outcomes` holds 27,220 rows (+234), newest `ts_evaluated` 20:32:39.
  - market-data's scheduler call timed out on all three attempts (20:30:46, 20:31:04,
    20:31:27) while the first request was still running. The two retries evaluated 0 rows, so
    they were harmless, but the job is recorded as failed when it succeeded. Not changed here.
  - US signals were still writing at 20:45. signal-engine logged no tracebacks in the
    evaluation window.
- **Connection pool, not caused by this check:** market-data hit `QueuePool limit of size 5
  overflow 10 reached` three times since its 13:40 start: `ingest.symbol_failed` at 19:00:50,
  then `squeeze_watch.failed` at 19:00:59 and 19:35:57. There were none in signal-engine. That
  is the [pool exhaustion](../incidents/db-connection-pool-exhaustion.md) class; recorded here,
  not investigated ([evidence](evidence/us-check/02-post-close.txt)).

## 6. Handled provider errors and data coverage

**HK burst, 01:41–01:46 UTC:**
- **Errors.** No tracebacks; 334 error lines:
  - Yahoo `Invalid Crumb` 401: 52;
  - quote-not-found 404s for COIIN, ASTA, SKHYV and US10Y: 40, 40, 33 and 24;
  - "No fundamentals data found" 404s for ETFs: 7 each;
  - UW 422 for the BRK-A dark pool: 15.
- **Coverage.** An HTTP 200 says the request completed, not that data came back, so coverage was
  read from the cache the endpoint serves ([evidence](evidence/provider-coverage/)). 25 of 42 HK
  symbols hold fundamentals fetched between 01:30 and 01:50 UTC, and all 25 contain data. The
  crumb errors did not stop those fetches. Which requests hit a crumb error and then retried
  successfully is not recorded.

**US open, 13:20–13:50 UTC:**
- **Errors.** No tracebacks:
  - quote-not-found 404s: 34;
  - ETF "no fundamentals" 404s: 10;
  - UW 422 for BRK-A: 9;
  - Yahoo crumb errors: 0.

**Fundamentals coverage at 13:51 UTC:**
- **Overall:** 197 of 199 active symbols have usable fundamentals (market cap, trailing P/E or
  revenue) in the fresh 24-hour cache. None is in a remembered-failure state.
- **Without data:** AAOX and GLD. GLD is a commodity trust, so fundamentals do not apply; AAOX
  was not investigated.
- **ETFs:** despite their "No fundamentals data found" 404s, the ETFs still carry usable fields.
  The 404 comes from a sub-request, not from `.info`.

**Structural, not transient.** Four followed symbols have no Yahoo quote at all (COIIN, ASTA,
SKHYV, US10Y), and BRK-A is refused by UW's dark-pool endpoint. These recur on every burst and
are counted here, not fixed.

## 7. RZY rebuilds

See [rzy-rebuild-investigation.md](rzy-rebuild-investigation.md):
- **Rising without more revisions.** Rebuilds per 5m stream went 3.26 → 5.51 → 7.12 (Oct 27–29)
  while distinct revised 5m bars stayed flat (10,663 / 7,480 / 8,227).
- **Main trigger.** Material OHLC changes to just-completed 5m bars, by the same provider
  (mostly Unusual Whales revising its own bar), within 15 minutes.
- **Derived timeframes.** 15m and 1h rebuilds follow 5m revisions (900 of 915). No case of
  switching between native and 5m-derived bars was found.
- **Not established.** Why rebuilds per revised bar rose: scan consumption times and throttle
  state are not stored.
- **Code vs docstring.** `_ohlc_revised` rebuilds on any change above float noise, while
  `evaluate()`'s docstring says "material"; this is about 5% of triggers. Recorded, not changed.

## Deployment

**Deployed 2026-10-30 23:38 UTC**, market-data only, through `scripts/deploy.sh` under the host
lock (exit 0). That was after the US post-close evaluation had been observed, so the US check
reads the release the session started on.
- **Commits:** `76c170b8` (code) and `18f10963` (docs), rebased onto `49802ce5`. The rebase was
  clean; the only shared file, `main.py`, changed in separate hunks.
- **Rollback image:** `stockai-market-data:rollback-before-18f10963` = `cf9cb1f8…`, the image
  that was running.
- **Host checkout:** `18f10963`. market-data started 23:38:47 and is `running/healthy`, with 0
  tracebacks since.
- **Release identity:** `STOCKAI_RELEASE_SHA=18f10963a964…` in the container. The price-risk
  `code_fingerprint` is `2bc351c9bc98…`.
- **Schema:** both tables exist, empty, and both `*_no_update` triggers are enabled.
- **Drift:** `check_deploy_drift.sh` from a clean `18f10963` worktree reports 12 of 12 backends
  OK.
- **Unchanged:** no thresholds, flags or trading rules. Price-risk email stays off and Intraday
  generation stays disabled. The other 11 backends pick up `STOCKAI_RELEASE_SHA` on their next
  deploy; only market-data reads it today.

**First scheduled scan, 20:10 ET Oct 30 (00:10 UTC Oct 31)** ([evidence](evidence/first-scan-and-report.txt)):
- **Rows.** Two `completed` rows share one `run_id`, both with release `18f10963`, fingerprint
  `2bc351c9bc98`, one `config_hash` (`ab32a4d0…`) and delivery off.
- **Symbol scope.** 154 subjects: 151 measured from settled bars (US 114, HK 37), 3 excluded.
  - Excluded by name: SKHYV, SSNLF and US10Y, each `no_price_data` (no settled daily bar in 40
    days).
  - Events: SOUN session decline step 1 (−3.21%) and TTGT step 3 (−9.25%), both
    `shadow_suppressed`.
  - Suppressions: 160 `same_episode`, 4 `open_pullback_episode`.
  - Quote refusals: 0.
- **Benchmark scope.** 3 of 3 measured; 0 events; 3 `same_episode`.
- **Report** for Oct 30: `0 of 5 complete sessions`. Oct 30 is incomplete, missing 10:05, 12:05,
  14:05 and 15:40, as expected for a deploy after the close; it does not count. Entry receipts:
  0 attempts, so the result is "no opportunity to evaluate". The first counted session can be
  Mon Nov 2.
