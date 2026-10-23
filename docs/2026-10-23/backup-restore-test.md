# Isolated restore test of the nightly backup (2026-10-23)

**Result: recoverable.** The 2026-10-23 02:00 UTC backup restores cleanly into an empty PostgreSQL 16 and matches production as of the dump's snapshot. `gzip -t` alone showed only that the compressed file was intact; this test shows the database can be rebuilt from it.

## Method

- **File:** `stockai-2026-10-23.sql.gz` (2,459,531,759 bytes), sha256 `c5bb34ee…1423273`. Hashed on EC2 at idle priority, copied to the workstation, and the hash re-checked: identical.
- **Restore target:** a throwaway local container (`stockai-restoretest`, postgres:16-alpine 16.15; production runs 16.14) on a fresh volume. **Nothing on the production host was touched** besides reading the file and running read-only count queries.
- **Command:** `zcat … | psql -v ON_ERROR_STOP=0` in **628 s**; exit 0, **0 errors** in stderr.

## Checks

| Check | Restored | Production | Result |
|---|---|---|---|
| Tables / indexes / constraints / sequences | 128 / 518 / 292 / 112 | 128 / 518 / 292 / 112 | identical |
| Exact row counts, 127 tables (all but the chain archive) | — | — | **117 identical**; 10 higher in production, all accounted for below |
| Chain archive, two session slices (Oct 15, Oct 20) | 295,924 / 139,783 | 295,924 / 139,783 | identical (51.7M rows in total restored) |
| Rows written around the snapshot (`bar_session_authority`) | newest 02:00:02.27 | 12 extra rows, all 02:00:02.34–02:00:03.66 | snapshot taken at ~02:00:02.3; nothing before it is missing |
| Sequences at or above their column's maximum | 111 / 112 | — | the one exception (`daily_ingest_records_stock_id_seq`, never called) is identical in production: the column is always set explicitly, so a schema quirk, not a restore defect |

The 10 tables that differ are higher in production by rows written after the snapshot. These include bar revisions and provenance, prices, scan logs, fundamentals, news, analyst targets and LLM logs. The `bar_session_authority` boundary above was checked row by row.

## Not established here

- **Restore time on the production host,** which has gp3 throughput limits. 628 s is the workstation figure.
- **Application start against a restored database.**
- **The other nightly files:** only the latest was tested. The Oct 22 file (re-run at 13:36 UTC) passed `gzip -t` only.

## Backup protection (open)

The truncated Oct 22 02:00 dump came from a frontend build running concurrently, which filled the disk. Another session has proposed one host lock for builds, recreates and dumps (`docs/audits/2026-10-22-backup-build-lock-proposal.md`, implemented locally, not committed). Until that lands:
- no build or deploy should run 01:55–02:15 UTC;
- this session's deploys used the host lock and avoided that window.

The restored volume `stockai-restoretest-data` is kept, with the container stopped, for further checks; delete it with `docker rm stockai-restoretest && docker volume rm stockai-restoretest-data`.
