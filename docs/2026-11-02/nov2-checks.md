# Mon Nov 2 checks: first EST session, trial day 1, monthly data check

Read on Tue Nov 3, 05:00–06:00 UTC, after the US session and before the HK close.

**How the checks were run.**
- Read-only throughout: `READ ONLY` transactions, Redis `GET`/`SCAN`, container logs and the
  read-only `scripts/measurement_report.py`.
- market-data has run since its 2026-11-01 18:09 UTC deploy (`6797e1b9`), so its log covers the
  whole session.
- Nothing on production was changed.

| Check | Result |
|---|---|
| Price-risk trial, day 1 | **Complete:** 5 of 5 slots, one config hash, release `6797e1b9`, delivery off |
| Outcome evaluation once per market | **Pass:** HK 08:30 UTC, US 21:32 UTC; one run each, no `http_failed` |
| Grid and reconciliation after DST | **Pass:** passes at 21:02/21:07 UTC; only thin names incomplete |
| HK lunch-pass record | **Defect (mine, `1e5249a3`):** the close pass overwrites it |
| Late US 5m bars after the UW 429 retry | **Better, one session:** 2.0% of bars first written >3 min after close, against 10–20% |
| DB connection pool | **Exhausted 11 times.** Cause located: ingest holds a connection during the provider request |
| Paper entry receipts | 0 attempts; no entry reached `_open_paper_trade`; 0 receipt failures |
| Snapshot job (`options_flow_eod`) | **Pass:** Monday's run describes Fri Oct 30; 35 of 39 written, 4 with reasons |
| RZY rebuilds | 912, against 1,516 (Oct 29) and 1,646 (Oct 30). Same table and measure |
| Six collecting items | Re-counted (§8). Nothing re-decided; the GEX control arm passed its review trigger |
| Monthly restore test | See §10 |
| Evidence for the fixes | Saved: full logs off-git, extracts in `evidence/` (§9) |
| Storage | 30 GB free (70 GB used of 100 GB), after Sunday's prune |

## 1. Price-risk trial, day 1

`measurement_report.py 2026-11-02 2026-11-02` reports:
- **Complete:** all five slots, no missing or failed slots.
- **Identity:** config hash `ab32a4d0…`, release `6797e1b9…/2bc351c9bc98`, delivery never
  enabled.
- **Content:** 124 events in the five US scans. 3 subjects excluded per scan, 1 quote refusal.
  Suppressions: 407 `same_episode`, 33 `open_pullback_episode`.

Two off-slot scans (Nov 2 07:30 UTC and Nov 3 03:30 UTC) are HK-session scans. They are not
counted for the US trial.

**Volume.** 124 events is far above the design target of about 10 a session. It follows the
market:

| | Fri Oct 30 | Mon Nov 2 |
|---|---|---|
| QQQ | −3.5% | −1.4% |
| SMH | −7.0% | −2.7% |
| SPY | −1.8% | −0.5% |
| Price-risk events (all rules, ET date) | 227 | 146 |

On Nov 2 the events break down as:
- 94 `session_decline` over 57 subjects;
- 45 `pullback` over 39 subjects;
- 3 `slow_decline`;
- 1 `basket_slow_decline` (AI / GPU semis);
- 3 `unmeasurable`, as on every day.

Many subjects escalate through steps within the day. This is evidence for the Nov 9 usefulness
review: per-symbol rules flood during a broad selloff. It is not a fault, and thresholds stay
unchanged.

## 2. Outcome evaluation (d96a87a6)

| Market | Time | Evaluated | Skipped open | Horizons resolved | Response |
|---|---|---|---|---|---|
| HK | 08:30:58 UTC | 142 | 2,498 | 299 | 200 |
| US | 21:32:24 UTC | 764 | 1,734 | 1,472 | 200 |

No `scheduler.http_failed`. `us_post_close` was `ok` in 236.6 s; `hk_post_close` was `ok` in
109.1 s. On Oct 30 each made three timed-out attempts. **Pass.**

## 3. Grid and reconciliation after the clock change

US passes ran at 21:02 and 21:07 UTC, which is 16:02 and 16:07 EST.

**Pass 1:**
- 162 symbols, 142 complete.
- Final candle missing only for AMADY and BRAZ.
- Interior gaps only in thin names: XSD, QUCY, DFNS, AMZA, FLBR, TTGT, BRK-A (30) and others.
- No session data for SSNLF, SKHYV and US10Y, which are not exchange-traded.

**Pass 2:** re-ingested the 17 incomplete symbols and recovered none. They are sparse traders,
the 457c class.

HK, Nov 2: 42 symbols, 35 complete. The gaps are in thin names; 0117.HK has no final candle.

**Defect (in my own change, `1e5249a3`).**
- The per-pass key is `…:detail:<session>:p<pass>`.
- HK runs a lunch pass and a close pass, and both are numbered 1 and 2.
- So the close pass overwrites the lunch pass's record. The HK record above is the close pass.
- Fix: add the phase (`lunch`/`close`, or the `until` time) to the key.
- **Not done now.** It is a market-data change, and deploying during Nov 2–6 splits a trial
  session. Do it after Fri Nov 6's 20:10 ET scan.
- **Superseded 2026-11-03:** fixed in `61ddccb7` and deployed with the pool fix at 08:40 UTC
  Nov 3, between sessions (see §5, Deployed).

## 4. Late US 5m bars, after the UW 429 retry (f5eabd6a)

**Measure.** For each US regular-session 5m bar in `bar_provenance`, minutes from the candle's
close to the bar's first write:
- negative means the bar was already stored while it was forming;
- a large value means the bar was skipped for whole cycles.

| Session | Bars | Median | >3 min | >8 min | >15 min |
|---|---|---|---|---|---|
| Oct 27 | 13,187 | −4.4 | 10.4% | 9.8% | 9.7% |
| Oct 28 | 13,478 | −4.1 | 19.5% | 13.4% | 12.1% |
| Oct 29 | 13,405 | −3.8 | 19.2% | 13.6% | 12.2% |
| Oct 30 | 12,168 | −4.0 | 17.6% | 7.7% | 2.3% |
| **Nov 2** | 12,131 | −4.5 | **2.0%** | **1.7%** | **1.6%** |

**UW refusals on Nov 2:**
- 145 first refusals were retried, and only 3 fetches still failed (`rate_limited`).
- The Oct 29–30 counters have already expired, so there is no counter baseline. The bar measure
  above is the comparison.

**Reading.** One session, and the market regime differs. It is consistent with the retry ending
the run-after-run skips. Re-read over Nov 2–6 before concluding.

## 5. DB connection pool: exhausted 11 times, cause located

The `pool_watch` holder log added on Oct 30 (`2609ff6c`) captured the event.

**Failures** were `QueuePool limit of size 5 overflow 10 reached` at:
- 16:45 `alert.check_error`;
- 18:00, 19:05 and 19:25: HTTP requests, each surfacing as an ASGI exception;
- 19:10 `ingest.symbol_failed`;
- 20:40 `squeeze_ignition_alert.failed`, `little_rzy.drain_failed` and `options_flow.error`.

**Pressure:**
- 117 `db.pool_pressure` lines over the session.
- 14 at 15 of 15 connections, 14 at 14, and 89 at 13.

**Who held the pool** across the 12 full-pool snapshots:

| Holder | Count | Max age |
|---|---|---|
| `ingestion.py:306 ingest_symbol ← ingestion.py:702 _fetch` | 94 | 18.5 s |
| `routes.py:1385 fundamentals_bulk` | 18 | 6.1 s |
| `rzy_workflow.py:136 load_bars ← … check_little_rzy_workflows` | 10 | 8.4 s |
| `uw_option_chain.py:76 latest_as_of ← get_chain ← get_session_expiries` | 10 | 20.0 s |
| `dark_pool_ledger.py:85 persist` | 6 | 5.8 s |

In each snapshot, 5 to 12 of the 15 connections were ingest workers.

**Cause, confirmed in the code.**
- `ingest_symbol` opens `with SessionLocal() as session` at `ingestion.py:305`.
- It queries the head bar, which checks out a connection, then calls
  `adapter.fetch_ohlcv(...)` (line 444, a provider HTTP request) inside the same session.
- It only commits at line 636.
- So every ingest worker holds a pooled connection for the whole network round trip.
- `ingest_universe` runs 6 workers. Two concurrent universes (the 5m ingest and the market
  refresh) take up to 12 of 15 connections, leaving 3 for alerts, RZY, the API and digests.

**Made worse by the Nov 1 retry, not caused by it.** The UW 429 retry now waits 1.5–5 s *inside*
that fetch. Pool exhaustion predates it: 3 times on Oct 30, 19:00–19:35 UTC.

**Proposed fix (not built).**
- Release the connection before the provider request: read the head, end the transaction, fetch,
  then open the write.
- Keep the `force=True` path as it is, because its DELETE and INSERT must stay in one
  transaction.
- Optionally cap combined ingest concurrency below the pool size.
- **Timing:** this changes ingestion. Building it during Nov 2–6 would confound the RZY trigger
  records, and deploying it would split a trial session. Build and test it now; deploy it after
  Fri Nov 6's 20:10 ET scan.

**Superseded 2026-11-03: built and deployed early.** The trial check showed a deploy between
sessions does not split one: a session counts as complete only when all of its own scans share
one release. So the fix shipped at a gap in the trading day instead of waiting until Nov 6.

**Deployed (2026-11-03 08:40–08:41 UTC, `92ae1f28`, market-data only).**
- Timing: after HK's close (08:00), its two close passes (08:12, 08:17) and post-close
  (`hk_post_close` ok at 08:32), and about six hours before the US open (14:30 UTC).
- Rollback image: `stockai-market-data:rollback-before-92ae1f28` (image `d150584a7b87`).
- Outgoing log saved by the deploy script:
  `~/deploy-evidence/container-logs/market-data-20261103T084025Z-before-92ae1f281edf.log.gz`.
- Verified: `STOCKAI_RELEASE_SHA=92ae1f28…`, healthy, fix present in `/app/src`, 0 tracebacks,
  0 QueuePool errors, 0 non-healthy containers, drift 0 of 12.
- **Not yet verified:** whether the fix works in production. Both markets were closed at deploy
  time, so no ingest had run. The first real test is the US session on Nov 3. That evening's
  17:35 PT (01:35 UTC next day) evidence save should show no `QueuePool limit` errors and fewer ingest holders
  in `db.pool_pressure`.
- Effect on reads: Nov 2 ran on `6797e1b9` and Nov 3–6 run on `92ae1f28`. The price-risk trial
  is unaffected; the RZY rebuild counts are read per release.

## 6. Paper entry receipts: no opportunity yet

- `paper_entry_receipts` has 0 rows for Nov 2, and `paper.entry_receipt_failed` appears 0 times.
- No candidate reached `_open_paper_trade`. The session's gates stopped them first:
  - `entry_skipped` 623;
  - `consecutive_loss_limit` 472;
  - `entry_gate_no_information` 461;
  - `skip_declining_confidence` 265;
  - `stop_cooldown_active` 149;
  - `regime_gate_risk_off` 70.
- So the answer is "no opportunity to evaluate", not a pass. The Nov 13 read stands.

## 7. RZY rebuilds

From `little_rzy_rebuilds`, the same measure as the page:

| US day | Oct 26 | Oct 27 | Oct 28 | Oct 29 | Oct 30 | **Nov 2** |
|---|---|---|---|---|---|---|
| Rebuilds | 615 | 661 | 1,021 | 1,516 | 1,646 | **912** |
| Of which `stored_completed_bar_changed` | 517 | 548 | 927 | 1,401 | 1,550 | 818 |

- The trigger records (`rzy_rebuild_triggers`) agree: 413 (5m), 331 (15m) and 168 (1h) rebuilt,
  plus 118 deferred by the throttle.
- Classes: mostly `material_ohlc`, then `gap_after_checkpoint`, then `rounding_ohlc`.
- Median bar age at rebuild: 5m 22 min, 15m 8 min, 1h 114 min.
- **Why Nov 2 fell is not attributed.** The UW retry and the market regime both changed. The
  Nov 9 read covers the whole week.

## 8. The six collecting items, re-counted

| Item | Oct 13 | Nov 2 | Trigger met? |
|---|---|---|---|
| M08 prebreakout | 8 names, AI 42.9% | 8 names, AI 42.9%; **0 fires since Oct 13** | No |
| M11 `short_squeeze` itself | 18 | 22 (+4) over 16 names | No |
| M10 squeeze ignition | 6 (Sep 30) | 13 over 8 names | Not to judge yet |
| M09 GEX control arm (uncorroborated, resolved 5d) | 20 | **38** | **Yes: past the ~30 review trigger** |
| M06 confidence feedback | stays off | unchanged | n/a |
| M07 options-flow direction | decided | unchanged | n/a |

- **Prebreakout's silence.** The job is alive (`check_prebreakout_alerts` `ok`; outcome
  evaluation still processing older fires). But nothing is logged per candidate. "No setups" and
  "every setup gated" cannot be told apart. Worth a line of instrumentation later.
- **GEX.** The review can run before its Dec 1 date. It stays a review trigger, not a promotion
  threshold, and the gate stays off. 309 older alerts have no corroboration value (pre-feature)
  and are excluded.

## 9. Evidence kept for the fixes

Saved 2026-11-03 05:16 UTC.

**Why it was saved now.** The market-data log is the only full record of the pool exhaustion, and
the next recreate deletes it. That recreate includes the fix's own deploy after Nov 6.
(Update: that deploy happened at 08:40 UTC Nov 3. The deploy script saved the full outgoing
log beforehand; see §5, Deployed.)

**Full logs.** Not in git: they can contain usernames and request details. sha256 files sit
beside them.
- On EC2: `~/evidence/2026-11-02/`. Locally: `~/stockai-evidence/2026-11-02/`.
- `market-data-since-20261101T1809Z.log.gz` (7.6 MB): the container's whole life, every line
  timestamped.
- `signal-engine-since-20261101T1800Z.log.gz` (1.4 MB).
- Plus the Redis records, the trial report and `identity.txt`. The host checkout and the
  container release are both `6797e1b9`.

**Extracts in [`evidence/`](evidence/).** Scanned for emails, tokens and keys; none found.

| File | Lines | Used for |
|---|---|---|
| `pool-pressure.jsonl` | 118 | every `db.pool_pressure` record, with holders, ages, threads and call sites (§5) |
| `pool-exhaustion-errors.txt` | 11 | each `QueuePool limit` failure, with its log line number in the full log (§5) |
| `uw-429-lines.txt` | 176 | UW refusals and retries (§4) |
| `outcome-evaluation-lines.txt` | 6 | the two evaluation runs (§2) |
| `paper-event-counts.txt` | 19 | paper gate counts (§6) |
| `redis-reconcile-keys.txt` | 48 | every per-pass reconciliation record still in Redis (§3) |
| `redis-job-status.txt`, `measurement-report-2026-11-02.json`, `identity.txt` | — | §1–3 |
| `queries/*.sql` and `*.out.txt` | — | the SQL behind §1, §4, §7 and §8, and its output, re-runnable read-only |

**Daily save, Nov 3–6.**
- `~/stockai-evidence/bin/save_session_evidence.sh` saves the same set after each US session.
- It runs from the workstation's crontab at 17:35 PT, which is 20:35 ET, after the 20:10 ET scan.
  The entry `35 17 3-6 11 *` fires only on Nov 3–6.
- Run log: `~/stockai-evidence/save.log`.
- Output: EC2 `~/evidence/<date>/` and local `~/stockai-evidence/<date>/`, hash-checked, with
  extracts under `extracts/`.
- Read-only on production. A self-test against Nov 2 reproduced this save (11 pool errors,
  release `6797e1b9`) and was then deleted.
- A missed run is covered by the next one, unless market-data is recreated in between.

**Correction made while saving.** The prebreakout re-count's first query labelled the number of
names as "fires". The saved query is fixed: 70 fires over 8 names, AI 30 of them (42.9%), and 0
since Oct 13.

## 10. Monthly restore test: pass

**Method.** Same as Oct 23 ([backup-restore-test.md](../2026-10-23/backup-restore-test.md)).
- **File:** `stockai-2026-11-03.sql.gz`, the 02:11 UTC Nov 3 dump (2,606,300,589 bytes).
- **Hash:** sha256 `6d7af2f0…0b452f`, computed on EC2 at idle priority and re-checked after the
  copy. Identical.
- **Target:** a throwaway local container, `stockai-restoretest-1103` (postgres:16-alpine), on a
  fresh volume. Production was only read.
- **Result:** `zcat | psql -v ON_ERROR_STOP=0` took **695 s** (Oct 23: 628 s), exit 0, **0 lines
  on stderr**.

| Check | Restored | Production |
|---|---|---|
| Tables / indexes / constraints / sequences | 153 / 583 / 1,419 / 121 | 153 / 583 / 1,419 / 121 |
| 14 row counts, cut off before the snapshot | 13 identical | — |

**The one difference is explained by the data model, not a restore defect.**
- `signals` before 02:00 UTC Nov 3: 82,286 restored against 82,118 in production, so the restore
  has 168 more.
- Signals are one row per stock, horizon and day, and later refreshes update a row's `ts`.
- The backup holds 168 HK rows (42 symbols × 4 horizons) written 01:45–01:46 UTC. In production
  the same 168 rows now carry `ts` 05:20 UTC, so they fall outside the cut-off.

The tables counted are `stocks`, `users`, `paper_trades`, `signals`, `signal_outcomes`, D1 and M5
`prices`, `price_risk_scans`, `risk_events`, `little_rzy_rebuilds`, `rzy_rebuild_triggers`,
`notification_outbox`, the Oct 30 chain archive (301,885 rows) and the exposure reservations.

**Still not tested:**
- restore time on the production host;
- starting the application against a restored copy;
- the older nightly files.

**Housekeeping.**
- The local copy of the dump was deleted after the restore.
- The container is stopped. Its volume `stockai-restoretest-1103-data` and the Oct 23 one,
  `stockai-restoretest-data` (15.2 GB), are kept for further checks.
- Remove both with `docker rm stockai-restoretest stockai-restoretest-1103 && docker volume rm
  stockai-restoretest-data stockai-restoretest-1103-data`.
