## Recurring Issue: Research Generation "NetworkError" in Browser Despite Server Success

**Symptom:** Clicking "Generate Report" (or the research page auto-triggering a report) shows
"NetworkError when attempting to fetch resource" in the browser, but refreshing the page shows
the report loaded fine — the generation actually succeeded server-side, only the client-side
fetch that triggered it failed.

**Root cause (found 2026-07-06):** `/api/research/*` was still proxied browser → Nginx →
Next.js (port 3000) → api-gateway (port 8000) → research-engine — a "double hop" through the
Next.js rewrite layer. Research report generation legitimately takes 2-3 minutes (LLM call),
and long-lived connections through the extra Next.js hop are fragile — this is the EXACT same
failure mode that was already fixed for AI chat (`/api/ai/`) on an earlier date, per the comment
already in `stockai.conf`: "AI chat routes directly to the API gateway — bypasses Next.js proxy
to eliminate the double-hop that caused NetworkError in Firefox". The 2026-06-14 fix
(`e419775`) only raised timeouts for research (`proxy_read_timeout 200s` + Next.js
`proxyTimeout: 200000`) — it did NOT apply the same direct-bypass fix later used for chat, so
research kept the fragile extra hop even after chat was fixed.

**Fix applied (2026-07-06):** Changed `/etc/nginx/conf.d/stockai.conf`'s `location
/api/research/` block to `proxy_pass http://127.0.0.1:8000/research/;` (was
`http://127.0.0.1:3000;`), with the same header-forwarding lines as the `/api/ai/` block
(`Host`, `X-Real-IP`, `Authorization`, `Content-Type`). This is an EC2-only config file, not
tracked in git — there is no local copy of `stockai.conf` in the repo, so this fix must be
re-applied by hand if the EC2 instance is ever rebuilt. A backup of the pre-fix config was left
at `/etc/nginx/conf.d/stockai.conf.bak-<date>` on the instance.

**What to check if this recurs (or a similar NetworkError shows up on a new long-running
endpoint):**
```bash
# On EC2 — confirm the research block bypasses Next.js directly
sudo grep -A6 "location /api/research/" /etc/nginx/conf.d/stockai.conf
# Should show proxy_pass http://127.0.0.1:8000/research/ (NOT :3000)

# Test it responds through the direct path (401 without a token is expected/correct):
curl -s -D - -o /dev/null https://lausing.com/api/research/AAPL | head -5
```

**Design invariant:** Any endpoint whose real work can run longer than ~30-60s (LLM calls,
batch backtests, tuning sweeps) should get its own Nginx `location` block that proxies straight
to `api-gateway:8000`, bypassing the Next.js rewrite hop entirely — matching the `/api/ai/` and
now `/api/research/` pattern. Do not just raise timeouts on the existing Next.js-hop block;
that was tried once for research and the underlying double-hop fragility remained.

---


## Recurring Issue: Research Reports Vanished on Every research-engine Restart — No DB Persistence At All (Fixed 2026-07-29)

**Symptom:** a user asked "where can I see auto research reports?" and reported that visiting
`/research/RXT` (a symbol confirmed auto-triggered multiple times earlier the same day, per
`scheduler.auto_research_triggered` logs) showed "Generate Report" instead of the actual
report — as if it had never been generated at all.

**Root cause:** `services/research-engine/src/api/routes.py`'s `_cache` was a **plain
in-memory Python dict** (`_cache: dict[str, tuple[dict, datetime]] = {}`) with **zero database
persistence** — confirmed via grep, the ENTIRE research report cache lived only in process
memory. `stockai-research-engine-1` had been restarted at `2026-07-29T00:01:48Z` (to deploy
this session's own earlier `trigger_research()` in-flight-check fix, see the
CLAUDE-API-COST-AUDIT entry above) — which silently wiped every report generated earlier that
day (RXT/SMTC/MU/UNH), with zero indication to the user that a report had ever existed. This
is a genuinely separate, previously-undocumented architectural gap from the auto-trigger cost
bug fixed earlier the same session — that fix was about HOW OFTEN reports get generated; this
gap is about reports having NO durability at all once generated, by either the auto-trigger or
a real user clicking "Generate Report" themselves.

**Fix**: new `ResearchReportCache` DB table (`shared/db/models.py`) — one row per symbol
(upserted via `on_conflict_do_update` on the `symbol` unique index, matching
`VolumeAreaLevel`'s established upsert pattern), storing the full report JSON blob plus the
`portfolio_size`/`max_risk_pct` params it was generated with (needed to preserve
`T247-RESEARCHENGINE-CACHEKEY`'s existing cache-key-matching semantics across the DB
boundary too). research-engine had **zero DB access of any kind** before this — confirmed via
grep (`from db import`/`SessionLocal` appeared nowhere in the service). This required no new
dependency (`sqlalchemy`/`psycopg2-binary` were already in `requirements.txt`, unused) and no
docker-compose change (the service already `depends_on: postgres` via the shared `py-common`
YAML anchor, and already gets DB credentials via the shared `env_file`) — purely a matter of
actually using infrastructure that was already wired at the compose level but never called
from code.

**Implementation**:
- `main.py` gained an `on_startup` callback calling `init_db()` — the same idempotent
  `create_all()` + migrations + admin-seeding call every other service already makes at
  startup, now run here for the first time.
- New helper functions in `routes.py`: `_report_ttl(report)` (extracted from the 5 places that
  independently re-derived the same quality→TTL mapping), `_db_save_report()`/
  `_db_load_report()`/`_db_clear_report()` (all fail-open/best-effort — a DB hiccup must never
  break a real report generation the caller is waiting on), and `_get_cached_report(sym)` — the
  in-memory `_cache` first (fast path, zero DB round-trip on the common case), falling back to
  the DB second (survives a restart), writing a DB hit back into `_cache` so a second request
  in the same process doesn't re-hit the DB.
- Every read site now goes through `_get_cached_report()` instead of a bare `_cache.get()`:
  `GET /{symbol}`, `/summary`, `/batch`, `/{symbol}/chat`, `trigger_research()`'s 6h cooldown
  check, and `generate_research()`'s own fast-path cache check.
- `generate_research()` now calls `_db_save_report(sym, report, req)` immediately after its
  existing `_cache[sym] = (report, ...)` write — every real generation (manual or
  auto-triggered) is now durable, not just cached in memory.
- `clear_research()` (the "Regenerate" button, `DELETE /{symbol}`) now also calls
  `_db_clear_report(sym)` — a real gap that would otherwise have defeated the button entirely:
  without this, clicking Regenerate would only clear the in-memory entry, and the very next
  read would silently serve the stale row straight back out of the DB via
  `_get_cached_report()`'s own fallback.

**Tests**: `services/research-engine/tests/test_report_persistence.py` (18 cases) —
`_report_ttl()` tested directly (a pure, DB-independent function); `_get_cached_report()`'s
fast-path/fallback/write-back/miss behavior tested via monkeypatching `_db_load_report`
directly (mocking the DB boundary, not the SQL — `db` is stubbed as a bare `MagicMock()` in
`conftest.py`); every read/write/clear site's wiring confirmed via source-text regression
checks, matching this repo's established technique for this exact DB-dependency constraint.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted: removing the
`_db_save_report()` write-through call (caught by the write-through test); reverting `GET
/{symbol}` to a bare `_cache.get()` (caught by its dedicated wiring test); removing
`clear_research()`'s `_db_clear_report()` call (caught by its dedicated test — the exact "the
Regenerate button silently doesn't work" regression this fix closes).

Full 76-test research-engine suite (up from 58, modulo the 3 pre-existing, unrelated
`test_scoring.py` failures already documented elsewhere in this file) green.

**What to check if this looks wrong**:
```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, generated_at FROM research_report_cache ORDER BY generated_at DESC LIMIT 10;"
# A symbol you know was generated recently should show up here — if not, _db_save_report()
# is failing silently; check:
docker logs stockai-research-engine-1 --since 1h | grep 'research.db_save_failed\|research.db_load_failed'
```

---

