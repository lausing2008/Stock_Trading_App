## Recurring Issue: hk_connect_flows Logging TypeError (BUG-9)

**Symptom:** `hk_connect_flows` scheduler job shows `Error` status. Log entry: `Logger._log() got an
unexpected keyword argument 'processed'`. Job runs for ~5m 45s (processing all HK stocks) then fails
at the final log.info() call.

**Root cause (found 2026-06-30):** The module-level `log` proxy in `hk_connect.py` is a structlog
`BoundLoggerLazyProxy`. With `cache_logger_on_first_use=True`, the proxy doesn't cache until its first
real method call. All `log.debug()` calls inside the loop are no-ops (filtered at INFO level), so the
proxy hasn't cached before line 181. The proxy then caches at the final `log.info()` call in the
APScheduler thread context. In some production conditions, the logger resolves to a stdlib Logger
instead of structlog's PrintLogger, and `Logger._log()` rejects keyword args.

**Fix applied (2026-07-01):**
1. `hk_connect.py`: Added `configure_logging()` at the top of `ingest_southbound_flows()`. This ensures
   structlog is configured with `PrintLoggerFactory` before the first real log call at line 181.
2. `common/logging.py`: Added explicit `logger_factory=structlog.PrintLoggerFactory()` and
   `context_class=dict` to `structlog.configure()`, making the configuration complete.

**What to check if hk_connect_flows shows error:**
```bash
docker logs stockai-market-data-1 --since 24h | grep 'hk_connect'
# Confirm configure_logging present in hk_connect.py:
docker exec stockai-market-data-1 grep 'configure_logging' /app/src/services/hk_connect.py
```

**Recovery:** After a failed run, trigger manual HK data refresh. The hk_connect_flows table will
backfill on next successful run (job runs Mon-Fri 17:00 HKT = 09:00 UTC).

---

