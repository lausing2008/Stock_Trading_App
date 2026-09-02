## Recurring Issue: Congress Trading Data Silently Empty — Free Source Domains Permanently Dead

**Symptom:** `/congress/trades` (market-data) returns an empty list with no error to every real
user; `congress.tsx`/`insider.tsx` show a permanently empty page with zero indication anything is
broken. `congress_trades` table (shared, written by event-intelligence) has 0 rows no matter how
long the scheduler has been running. Catalyst scoring's congress component
(`compute_congress_score()`, `_compute_risk_score()`'s congress-selling check) silently operates
on zero real data — not fail-open-with-a-flag, just quietly always-zero.

**Root cause (found 2026-07-09):** Both free congress-trading data sources this app depended on —
`housestockwatcher.com/api/transactions` and `senatestockwatcher.com/api/transactions` — are
**permanently dead**: the domains fail to resolve via DNS at all (not a 403/301/timeout on a live
host — confirmed via direct `curl`/`nslookup` from inside the running market-data container). The
underlying project's maintainer has been inactive since March 2021 and never responded to a 2024
GitHub issue asking about a shutdown. This affected TWO independent call sites that both silently
degraded to empty results on fetch failure with no alerting: event-intelligence's
`sync_congress_trades()` (writes the shared `congress_trades` table) and market-data's
`/congress/trades` endpoint (`_fetch_house`/`_fetch_senate`, since replaced by `_fetch_kadoa`).

**Fix applied (2026-07-09):** Repointed both call sites to
`https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/trades.json`
— a live, unauthenticated, MIT-licensed GitHub JSON feed that updates via daily automated commits.
Covers House Clerk + Senate eFD + OGE executive-branch filings in one combined response (a rolling
~5000-row window, not full history — fine for keeping the feed current going forward, not a
substitute for deep historical backfill). Both call sites now filter to congress-only records
(`branch == "congress"` in event-intelligence; `chamber in ("house", "senate")` in market-data) —
executive-branch OGE filings are ~85% of the feed's rolling window and are NOT congress trades.
Verified live in production: triggered a real sync via `POST /events/sync/congress`, confirmed
441 real rows upserted into `congress_trades` with correct politician names, tickers, transaction
types, and dates.

**What to check if this recurs (either this source dies too, or a similar silent-empty-fetch
pattern shows up elsewhere):**
```bash
# Confirm the current source is actually reachable — DNS failure looks different from a 4xx/5xx:
docker exec stockai-market-data-1 curl -sv 'https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/trades.json' --max-time 15 2>&1 | head -20
docker exec stockai-market-data-1 nslookup raw.githubusercontent.com

# Check current row count / staleness in the shared table:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
print(s.execute(text('SELECT COUNT(*), MAX(trade_date) FROM congress_trades')).fetchone())
s.close()"

# Manually trigger a resync (uses the same _service_token() pattern as other scheduler jobs):
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.post('http://event-intelligence:8010/events/sync/congress', headers={'Authorization': f'Bearer {tok}'}, timeout=30)
print(r.status_code, r.text[:200])"
```

**Design invariant:** Any external free-tier data source this app depends on should have its
fetch failures surfaced somewhere visible (a log line grep, a staleness check) rather than
silently degrading to an empty result — the original bug went undetected for an unknown period
specifically because both call sites' `except: return []` pattern is indistinguishable from
"genuinely no trades today" at the API response level. When adding a new free external data
source, prefer one with committed, checkable update activity (this fix's replacement source
updates via visible daily commits) over an opaque scraped API with no way to verify liveness
without actually calling it.

---


## Recurring Issue: "It's Reachable" ≠ "It's Current" — Always Check Last-Modified, Not Just HTTP 200

**Symptom:** A recommended external data source returns `HTTP 200` and looks like a solid,
official choice, but is actually not being maintained anymore — the page/file is still served,
just frozen at some point in the past. Reachability alone gave false confidence.

**Root cause (found 2026-07-14, while sourcing data for the CAPE/AI-bubble-warning feature):**
An initial research pass recommended Robert Shiller's own Yale dataset
(`econ.yale.edu/~shiller/data/ie_data.xls`) as the primary CAPE data source, citing that it
returned `HTTP 200` as proof it was "verified live." A direct re-check before committing to
that architecture found the file's `Last-Modified` header was **October 2023** — ~2.75 years
stale at investigation time — and Shiller's own site had migrated to a new Yale SOM page with
no working direct CAPE download found there either. The file being downloadable said nothing
about whether its *contents* were still being updated.

**What to check before trusting any "the data source is live" claim** (from an agent, a web
search summary, or your own quick check):
```bash
curl -sI "<candidate-url>" -A "Mozilla/5.0" --max-time 15
# Look at Last-Modified, not just the status code. A 200 with a Last-Modified from
# months/years ago means the URL still resolves but the DATA behind it is frozen.
```
Also directly inspect a few of the most recent rows/values in the actual payload and compare
against today's date — a `.csv`/`.xls` ending "2 years ago" is a hard stop, not a caveat.

**Fix pattern applied:** Re-researched and found `multpl.com` publishes a genuine Atom feed per
indicator (`multpl.com/{indicator}/atom`) — confirmed as a real, intentional, site-wide feature
(identical structure across `shiller-pe`, `s-p-500-pe-ratio`, `s-p-500-dividend-yield`, not a
one-off scrape) and verified via its own `<updated>` timestamp matching the current date, not
just a `200` on the URL. See the CAPE feature reference below for the full source used.

**Design invariant:** Before adopting ANY new external data source (especially one an agent or
a web-search summary recommends), verify current-ness directly — `Last-Modified` header, or the
payload's own embedded timestamp/most-recent-row — not just that the URL responds. An
"official" or "authoritative" source that has gone stale is worse than a well-verified
secondary source, because it looks trustworthy while silently serving frozen data.

---

