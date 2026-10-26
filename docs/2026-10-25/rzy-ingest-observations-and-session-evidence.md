# RZY ingest observations and session evidence — 2026-10-25

Status: deployed and production-verified on October 26; full-session acceptance pending. Continues [the reliability and forward-journal release](rzy-reliability-and-forward-journal.md). RZY remains research only. No strategy settings, execution eligibility, alert enrolment or broker behavior change.

## Delivered scope

- **Per-session accounting:** new `bar_ingest_observations` rows distinguish post-validation received rows, duplicate keys dropped, off-grid rejections, authority rejections and committed insert/update operations. The groups identify batch, symbol, venue-local session date, regular/extended classification, timeframe, provider and force mode. Repeated writes of the same candle count as writes, not new/unique bars. These are not raw vendor-response or failed-fetch counts.
- **Observed post-close writes:** `bar_post_close_writes` retains the earliest observed accepted write after a regular US candle closes, including unchanged re-sends and shortened final hourly bars. It is not first-ever ingestion, provider finality, execution-grade verification or the transaction commit timestamp. Only writes accepted by existing ingestion/authority rules are recorded. No historical timestamps are invented.
- **Atomicity and isolation:** the hook executes after a successful price upsert, inside a savepoint within the same transaction. An outer rollback removes observations too. If observation storage fails, its savepoint rolls back and the actual price update can still commit. Structured logs and the cumulative Redis `stockai:metric:bar_ingest_observation_failures` counter record that failure; absence of telemetry is never proof of no ingestion or complete coverage. The all-protected early-return path records zero writes.
- **Latency dashboard:** `/little-rzy` exposes close → observed post-close write and observed write → detection alongside existing close → detection/queue/provider acceptance. These additional stages cover native 5m signals only; native-versus-derived 15m/1h source identity is insufficient to join them safely. Missing observations and timestamps after detection are excluded and counted, not zero-filled. Receipt acceptance remains distinct from inbox delivery.
- **Accounting dashboard:** a collapsed table reports the last 20 hours, labelled with the exact counting domain. It separates venue/date/session/provider/timeframe. Empty evidence is explicitly inconclusive.
- **Retention:** 35-day cleanup runs per ingested stock and in the existing RZY scan, including observations for symbols no longer ingested. Reporting timestamps have indexes. The durable paper journal's retention policy is unchanged.

## Session reporting and capture

`scripts/rzy_session_report.py` now exports `ingest_observations.csv` and `post_close_writes.csv`, grouped session accounting and the new latency stages, with hashes of the reporter and relevant ingestion/monitoring source. Its READ ONLY transaction has a 30-second statement timeout per query. The observation window is **04:00–20:00 America/New_York**, converted to UTC with DST: October 23 uses 08:00–00:00 UTC; November 2 uses 09:00–01:00 UTC the next day.

The report no longer reads large stream checkpoints to count pauses. It uses session-window current-configuration snapshot statuses, explicitly labelled as cached observations rather than a new evaluation at report time. No snapshots yields unknown (`null`), not zero. The legacy first-ingest metric is labelled as first storage write, which may precede candle completion.

Completed reports refuse overwriting an existing manifest. The capture wrapper reserves a fresh destination before waiting, accepts an optional second destination argument, and uses distinct container report/script paths. It takes the existing host lock only during capture, with bounded waiting, so it cannot intentionally overlap a backup/build. Artifact hashes, report/copy/daily-export exit codes and container identity before/after capture determine `capture_complete`. That flag is independent of `post_close_confirmed`; neither establishes RZY trading readiness. Failed or interrupted captures remain preserved and a retry needs a fresh destination.

Example after deployment:

```sh
bash scripts/capture_rzy_session_evidence.sh 2026-10-26 \
  /home/ec2-user/rzy-session-evidence/2026-10-26-<release>
```

The script waits for the target session's post-close job and per-symbol coverage rather than only a clock time. Review the capture flags, hashes and raw data before using a session as acceptance evidence. Use the matching released reporter and service code. This release does not reconstruct earlier sessions' missing telemetry.

## Why incremental loading remains open

The existing price-write trace is best effort: price writes intentionally continue if tracing fails. It cannot safely serve as a watermark for skipping older history. A cache or reduced query window based solely on that trace could miss a correction and retain an obsolete setup.

This continuation keeps the full bounded history/revision checks. A separately reviewed incremental design needs transactionally reliable change tracking covering **every** writer/deletion, or a mandatory source fingerprint check with bounded memory and full-read fallback. It must preserve adjustment-window selection, native/derived source choice, gaps, older corrections and rebuild identity against a full-read reference. No incremental loader or live speedup is claimed here. These new observations are diagnostic, not that reliable watermark.

## Validation

Full market-data: **5,928 passed / 27 skipped**. Final affected checks: **38 passed / 1 PostgreSQL skip**, with the actual PostgreSQL 16 ingest/report, existing RZY and trace gates separately **3 passed**, none skipped. Final capture-focused run **10 passed / 1 PostgreSQL skip** includes the subsequently added artifact/identity cases. Frontend **987 passed / 83 files**; TypeScript, shell syntax and whitespace checks passed. Later retention/report/capture edits are covered by the final affected/PostgreSQL/capture checks after the full backend run began. Six compiled sabotages in an isolated source copy were rejected; the unchanged isolated baseline passed. [Logs and reproducible mutation checks](../audits/evidence/2026-10-25-rzy-ingest-observations/).

Verified behaviors:

- SQLite/PostgreSQL counts conserve received = duplicate + off-grid + authority-rejected + written.
- An actual provider fixture runs through shipped OHLCV validation, authority checks, PostgreSQL upsert and ingestion hook. A fallback protected by authority records zero writes. A real SQL failure in the telemetry savepoint leaves a changed stock close committed.
- Forming bars produce no post-close observation; unchanged later writes do. Earlier observations win; US/HK date grouping and US early-close hours are explicit.
- The existing scan purges stale observations for an inactive symbol.
- The PostgreSQL server reports `transaction_read_only=on` during real report queries; exported file hashes match; a completed manifest cannot be overwritten; EDT/EST windows are correct.
- Executed capture-wrapper tests use fake Docker commands: an existing destination is untouched; copied-artifact failure and container identity change fail capture; valid artifacts can complete while post-close confirmation remains false.
- Existing RZY and trace PostgreSQL gates, backend suite, frontend suite, TypeScript and shell syntax checks.

The new real-ingest PostgreSQL test is included in `make test-integration-pg-schema`; a required run without its disposable database cannot skip green.

## Deployment and remaining acceptance

Released code **`a8316be3fa9e2646165270a3f640c1cf06d268d5`**, pushed to `origin/prod`. Only market-data and frontend restarted, under the host lock; prior logs/images were preserved. New service-owned tables registered through existing locked startup schema creation, without changes to existing columns.

Production verification on **October 26**: **15/15 healthy, all 12 backend source hashes match**, frontend build **`ToAm9HIZ96C87I3-JWCWq`**, four pages return 200 with that build, both inspected protected routes return 401, expected UI markers are present, and no compiled `localhost:8000` reference was found. Strategy config remains **`8ceb8cf780c3eb981983`**. Read-only checks found **5,056 ingestion observation rows**, **5,531 post-close write observations**, and **59 replay/imported simulator rows**; there were **zero scored forward trades**, with win rate and expectancy correctly unknown. Counts are point-in-time observations, not complete-session acceptance.

After the build, disk utilisation briefly reached about 90% with PostgreSQL readers active. Reporting was held until a later live sample showed approximately **1% disk utilisation, 0–0.5% I/O wait and no active swap traffic**. This does not establish the cause of the earlier host stall. Standard deployment cleanup reclaimed 1.6 GB; approximately 27 GB remained free.

The read-only **October 23 smoke capture**, performed October 26 at 13:29 UTC, completed with all **14 capture-file hashes** independently rechecked. Post-close daily coverage was **154/155**. Its new observation exports contain zero rows because instrumentation was not present during that historical session; this is a capture-integrity check, not retroactive latency/completeness evidence. Artifacts remain at `/home/ec2-user/rzy-session-evidence/2026-10-23-observation-smoke-a8316be3`.

**Metadata limitation:** the legacy `stockai.release` image/container label is absent, so the capture's release-label field is empty. Its image ID, container start time, repository commit and reporter hashes are retained. The separately saved production verifier establishes the deployed source hashes; the repository commit alone is not proof of running code. Observation timing joins by candle key, not an exact event-version availability timestamp.

A detached capture process for **Monday, October 26** was verified running (PID 3967964), waiting for the target post-close job and at least 95% per-symbol daily coverage. Destination: `/home/ec2-user/rzy-session-evidence/2026-10-26-a8316be3`; log: `/home/ec2-user/rzy-capture-2026-10-26-a8316be3.log`. It survives SSH disconnection, not an EC2 reboot. Review its completion flags, hashes and session evidence after it runs; no prospective result is claimed yet. Sanitized verification and capture metadata are preserved alongside the validation evidence.

Full-session latency/completeness evidence, safe incremental loading, verified execution/borrow data and genuinely unseen after-cost performance remain open. Collect the first complete US session after this deployment separately from earlier releases; warmup observations cannot retroactively establish first arrival. Tracker 457b is not closed solely because accounting exists, and tracker 458 is not closed by a healthy container or successful report export.
