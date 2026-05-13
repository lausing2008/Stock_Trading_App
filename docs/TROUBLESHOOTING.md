# Troubleshooting Guide

A collection of real issues encountered in development and production, with root causes and fixes.

---

## 1. Prod vs Local Score / Ranking Discrepancies

**Symptom:** K-Scores or rankings differ between prod and local despite identical data.

**Root cause:** Docker images bake source code at build time. Any service whose image predates a code change runs the old code silently — no error, just wrong results.

**Fix:** Rebuild the affected service image on prod and restart it.

```bash
# Rebuild a single service (e.g. ranking-engine)
docker compose -f docker/docker-compose.yml build ranking-engine
docker compose -f docker/docker-compose.yml up -d ranking-engine

# Or rebuild all Python services at once
docker compose -f docker/docker-compose.yml build ranking-engine signal-engine ml-prediction market-data api-gateway
docker compose -f docker/docker-compose.yml up -d
```

**Verify:** Hit the service directly (bypassing the browser and gateway) to confirm the new score:

```bash
docker exec $(docker ps --format "{{.Names}}" | grep rank) curl -s http://localhost:8004/rankings/KGS
```

**Rule of thumb:** Any time you change Python source and push to prod, rebuild *every* service whose files you touched. Quick host-side patches without rebuilding (via `docker cp`) also require deleting `.pyc` caches and restarting the container.

---

## 2. "Train All" / Long API Calls Return 500

**Symptom:** The browser shows a 500 error for slow API calls (ingest, train all). The call succeeds if you hit the backend directly with curl.

**Root cause:** Next.js has a hardcoded **30-second proxy timeout** for `/api/*` rewrites. Any upstream call that takes longer is killed and returns 500 to the browser.

**Fix:** Add `proxyTimeout` to `frontend/next.config.js`, then rebuild the frontend image:

```js
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    proxyTimeout: 120000, // 2 minutes
  },
  async rewrites() { ... },
};
```

```bash
docker compose -f docker/docker-compose.yml build frontend
docker compose -f docker/docker-compose.yml up -d frontend
```

**Diagnosis tip:** Run the exact curl the browser sends against `localhost:3000` (not `localhost:8000`) and time it. If it fails at exactly 30 seconds, this is the cause.

---

## 3. Gateway Returns 502 / "Response content longer than Content-Length"

**Symptom:** API gateway logs `RuntimeError: Response content longer than Content-Length`. Some endpoints return 502.

**Root cause:** The gateway was re-serializing upstream JSON with `JSONResponse(r.json(), ...)`, which can produce a different byte count than the upstream `Content-Length` header.

**Fix:** Pass raw bytes directly in `services/api-gateway/src/api/proxy.py`:

```python
content_type = r.headers.get("content-type", "application/json").split(";")[0].strip()
return Response(content=r.content, status_code=r.status_code, media_type=content_type)
```

---

## 4. Code Changes Not Taking Effect After `docker cp`

**Symptom:** You copied an updated `.py` file into a running container but the behaviour hasn't changed.

**Root cause:** Python caches compiled bytecode in `__pycache__/*.pyc`. The old bytecode is still loaded.

**Fix:** Delete the cache after copying, then restart:

```bash
docker exec <container> find /app -name "*.pyc" -delete
docker compose -f docker/docker-compose.yml restart <service>
```

**Better approach for larger changes:** Rebuild the image rather than patching live containers.

---

## 5. Stale K-Scores After Re-Ingesting Price Data

**Symptom:** You re-ingested prices for a symbol but the rankings page still shows the old score.

**Root cause:** `GET /rankings` computes scores live on every request — there is no cache. The stale score you see is usually:
- The **browser** caching a previous response, or
- The **ranking engine container** running old code (see issue 1).

**Fix:**
1. Hard-refresh the browser (Cmd+Shift+R / Ctrl+Shift+R).
2. If the score is still wrong, hit the ranking engine directly to bypass the browser:
   ```bash
   docker exec $(docker ps --format "{{.Names}}" | grep rank) curl -s http://localhost:8004/rankings/<SYMBOL>
   ```
3. If that also shows the wrong score, the container has old code — rebuild (see issue 1).

---

## 6. Historical Price Data Differs Between Prod and Local

**Symptom:** The same symbol has different OHLCV values or different bar counts on prod vs local.

**Root cause:** yfinance returns **adjusted closing prices** that change retroactively when dividends are paid or splits occur. A database ingested months apart will store different adjusted prices for the same historical dates.

**Fix:** Force a full re-ingest of the affected symbol to overwrite stale adjusted prices:

```bash
# Via the admin API
curl -s -X POST http://localhost:8000/admin/ingest \
  -H "Content-Type: application/json" \
  -d '{"symbols": ["KGS"], "force": true}'
```

**Verify data identity across environments** using an MD5 hash of the entire OHLCV dataset:

```sql
SELECT md5(string_agg(
  ts::text || open::text || high::text || low::text || close::text || volume::text,
  ',' ORDER BY ts
)) AS hash
FROM price
WHERE stock_id = (SELECT id FROM stock WHERE symbol = 'KGS')
  AND timeframe = '1d';
```

Run the same query on both prod and local — hashes must match before investigating scoring differences.

---

## 7. Wrong or Duplicate Symbols in the Database

**Symptom:** Rankings include garbage tickers (wrong format, duplicates) that distort the leaderboard.

**Fix:** Soft-delete (deactivate) bad symbols via the admin API — this preserves price history:

```bash
curl -s -X DELETE http://localhost:8000/admin/stocks/<SYMBOL>
```

Symbols to watch for: wrong format (e.g. `2476`, `992.HK`), duplicates (`GOOGL` vs `GOOG`), or stale aliases (`TSMC` for a stock traded as `TSM`).

---

## 8. `NEXT_PUBLIC_API_URL` Not Taking Effect at Runtime

**Symptom:** Changing `NEXT_PUBLIC_API_URL` in `.env` has no effect; browser requests still go to `/api`.

**Root cause:** `NEXT_PUBLIC_*` variables are inlined at **build time**, not read at runtime. If the variable wasn't set when the image was built, it won't be present in the compiled JS bundle — the browser always falls back to `'/api'` and routes through Next.js rewrites.

**Fix:** Set the variable before building the frontend image:

```bash
# Ensure it is in .env before building
docker compose -f docker/docker-compose.yml build frontend
docker compose -f docker/docker-compose.yml up -d frontend
```

**Note:** For this app, the browser always hits `/api/*` through the Next.js rewrite (`/api/:path*` → `http://api-gateway:8000/:path*`). Changing `NEXT_PUBLIC_API_URL` only affects the Next.js server-side rewrite destination, not the browser fetch base URL.

---

## 9. HK Stock Bars Stored with Wrong Timestamps

**Symptom:** Hong Kong daily bars appear with an incorrect UTC offset, causing date mismatches in charts or signals.

**Root cause:** HK market closes at 16:00 HKT (UTC+8). If ingestion naively converts to UTC without accounting for the offset, bars are stored at the wrong date.

**Fix:** Applied in `services/market-data/src/adapters/base.py` and `routes.py` with a DB migration to correct existing rows. If timestamps look wrong for HK symbols, check the ingestion adapter's timezone handling.

---

## Diagnostic Quick Reference

| Observation | First thing to check |
|---|---|
| Prod score ≠ local score, data confirmed identical | Rebuild prod service image |
| API call times out at exactly 30 s | Next.js `proxyTimeout` — add to `next.config.js` |
| Browser shows stale ranking after re-ingest | Hard-refresh; then hit ranking engine directly |
| `docker cp` change has no effect | Delete `__pycache__` and restart container |
| Different bar counts for same symbol | Force re-ingest with `"force": true` |
| 502 from gateway on large responses | Check gateway proxy uses `Response(content=r.content, ...)` |
