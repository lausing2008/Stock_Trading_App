## Feature Reference: CAPE (Shiller PE) — AI Bubble Warning Indicator

**Added 2026-07-14.** A macro valuation indicator (CAPE, the cyclically-adjusted P/E ratio for
the S&P 500) surfaced as a "Bubble Warning" tab on `frontend/src/pages/intelligence.tsx`.
Historically elevated CAPE readings have preceded major market corrections, but CAPE is a
slow-moving signal — it can stay "elevated"/"extreme" for years before any correction, so this
is framed as macro context, not a trade trigger.

**Also surfaced on the Reports page (2026-07-17):** `frontend/src/pages/reports.tsx` has its
own dedicated "CAPE / Bubble Warning" tab (`?tab=cape`), promoted from a card that had
originally been buried inside the Trend tab — a user asked "where is the CAPE tab?" expecting
a distinct tab like `intelligence.tsx`'s, not a card nested inside another tab. The Reports
version adds a warning-bands reference table (Normal/Elevated/High/Extreme with the same
thresholds documented below) alongside the live reading. Both pages read the same
`api.eventsCape()` endpoint; there is no second CAPE data path.

**Data source:** `multpl.com`, NOT Yale's own `ie_data.xls` (see the Recurring Issue above for
why that source was rejected — found stale, 2.75 years old, at investigation time). Two
multpl.com endpoints are used:
- `multpl.com/shiller-pe/atom` — daily-updated Atom feed, current value. Confirmed as a
  genuine, site-wide feed pattern (same structure across every multpl indicator page).
- `multpl.com/shiller-pe/table/by-month` — stable `id="datatable"` HTML table, full history
  back to 1871, used for backfill/refresh of recent months.

Still an **unofficial third-party source** — same fragility class as the dead
housestockwatcher/senatestockwatcher congress-data incident, just a more stable access pattern
(a real Atom feed + a stable table ID, vs. an arbitrary scraped `<div>`). Monitor staleness the
same way as every other external feed in this app — see below.

**Architecture:**
- `shared/db/models.py` — `CapeReading` model, `cape_readings` table (new table; `create_all()`
  handles this automatically, no manual migration needed — see the `create_all()`-gap Recurring
  Issue above for when that ISN'T true).
- `services/event-intelligence/src/services/valuation.py` — `sync_cape_current()` (Atom feed),
  `sync_cape_history()` (by-month table), `cape_band()` (threshold classifier),
  `get_latest_cape()`/`get_cape_history()` (read side). `_parse_atom()`/`_parse_table()` are
  pure functions extracted specifically so they're testable against real captured fixture data
  without needing live network access in tests.
- `GET /events/valuation/cape` / `POST /events/sync/cape` in
  `services/event-intelligence/src/api/routes.py`.
- Scheduled job `sync_cape` at 08:45 UTC daily in `services/event-intelligence/src/scheduler.py`.
- `dq_check:cape_reading` entry in market-data's `_DQ_CHECKS` (`scheduler.py`) — 1080h/45-day
  staleness threshold, matching `valuation.py`'s own `stale` flag on the read side.

**Warning bands** (sourced from real historical CAPE peaks, not guessed):

| Band | CAPE range | Basis |
|---|---|---|
| Normal | < 30 | Long-run mean/median (1871–present) is ~16-17 |
| Elevated | 30–35 | Above historical norm |
| High | 35–40 | 1929 pre-crash peak was ~32-33 |
| Extreme | ≥ 40 | 2021 post-COVID peak ~38.6; Dec 1999 dot-com peak (all-time high) 44.19 |

**A real parsing bug this caught before production:** the by-month table's value cells contain
a leading `&#x2002;` (Unicode en-space) HTML entity before the actual number. A naive
`float(cells[1])` on the stripped cell text raises `ValueError`, which the per-row
`except (ValueError, IndexError): continue` swallows — silently producing **zero** synced rows
on every history-backfill run, with no error surfaced anywhere. Caught because
`tests/test_valuation.py` was written against real fixture data captured directly from
`multpl.com` (not hand-authored idealized HTML), which reproduced the bug immediately. Fixed by
stripping to `[^\d.]` before calling `float()`. **Lesson:** when writing a parser test for a
scraped/fed external source, capture and use a REAL response as the fixture — a hand-written
"clean" HTML sample will not surface the actual whitespace/entity quirks the real site emits.

**What to check if this goes stale or breaks:**
```bash
# Confirm both multpl endpoints are still live and current (check the date in the response, not
# just the status code — see the Recurring Issue above):
curl -sI "https://www.multpl.com/shiller-pe/atom" -A "Mozilla/5.0" --max-time 15
curl -s "https://www.multpl.com/shiller-pe/atom" -A "Mozilla/5.0" --max-time 15 | grep -o '<updated>[^<]*'

# Check current row count / staleness in the DB:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT COUNT(*), MAX(reading_date) FROM cape_readings;"

# Check the dq_check Redis key:
docker exec stockai-redis-1 redis-cli get dq_check:cape_reading

# Manually trigger a resync:
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from common.config import get_settings
from jose import jwt as _jwt
import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.post('http://event-intelligence:8010/events/sync/cape', headers={'Authorization': f'Bearer {tok}'}, timeout=30)
print(r.status_code, r.text[:400])
"
```

---

