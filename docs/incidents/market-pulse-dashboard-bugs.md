## Recurring Issue: Market Pulse Dashboard's "Top Movers" Could Go Entirely One-Sided (Fixed 2026-08-25)

**Symptom**: user reported "why the top movers all are negatives" the same day the page shipped.
Live-checked the real `GET /stocks/sector_performance` data behind it — genuine, real market
moves: a broad down-day dominated by DFNS (-46.58%) and a cluster of quantum-computing/tech
names (RGTI/IONQ/QBTS/QUBT, -6% to -9%), with real gainers present too (V +3.06%, DIS +2.63%,
META +1.66%) but smaller in magnitude.

**Root cause**: the section's own sort — `[...withChange].sort((a, b) => Math.abs(b.change_pct!)
- Math.abs(a.change_pct!)).slice(0, 10)` — ranked purely by `|change_pct|`, with no split by
sign. On any day where losers are simply larger in magnitude than gainers (exactly today's real
data), the top-10-by-magnitude list can silently become 100% losers even when real gainers
exist elsewhere in the same dataset — the sort never excluded them on purpose, it's just an
inherent property of ranking by absolute value alone.

**Fix applied**: split into two explicit, independently-sorted lists — Top Gainers (positive
`change_pct` only, sorted descending) and Top Losers (negative `change_pct` only, sorted
ascending), 6 each, both rendered together under one "Top Movers" card with their own labeled
sub-headers (▲ green / ▼ red). This guarantees today's real top gainer is always visible
alongside today's real top loser, regardless of which side has larger absolute magnitudes.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/sector_performance' \
  -H "Authorization: Bearer <token>" | python3 -c "
import json, sys
data = json.load(sys.stdin)
all_stocks = [(st['symbol'], st['change_pct']) for sec in data for st in sec['stocks'] if st.get('change_pct') is not None]
print('gainers:', sorted([s for s in all_stocks if s[1] > 0], key=lambda x: -x[1])[:6])
print('losers:', sorted([s for s in all_stocks if s[1] < 0], key=lambda x: x[1])[:6])
"
```
If either list is empty, that's real: it means every tracked stock genuinely moved the same
direction that day — not a bug in the split logic.

**Same-day companion fix — Macro Events Today section could look ambiguous about whether a
result exists yet.** User separately asked "do we get any result or information about Housing
Starts Release today?" — investigated the real `economic_events` row directly: its `event_date`
was `2026-08-25T08:30:00 UTC`, ~10 hours in the future relative to the real current time, with
`actual_value`/`expected_value` correctly still empty (the release genuinely hasn't happened
yet). The poll mechanism itself is confirmed healthy and correctly wired (mapped to real FRED
series `HOUST`, armed 8:30-9:59am ET matching the release's own publish time) — this was not a
bug, just a real scheduled-but-not-yet-published event. Added a small subtitle to the "Macro
Events Today" section — *"scheduled release date — see Event Intelligence for the actual
result once published"* — pointing at `intelligence.tsx`'s own "Latest Macro Reaction" card
(confirmed it genuinely shows `actual_value` once a release's poll fills it in), so a future
user isn't left wondering whether a listed macro event has already happened or not.

---


## Recurring Issue: Market Pulse Dashboard's Top Movers/Sector Heat Map Silently Mixed HK Stocks Into the US View (Fixed 2026-08-25)

**Symptom**: user reported `.HK` symbols (`0117.HK`, `6951.HK`, `2513.HK`, `9868.HK`) appearing
in the Top Movers and Sector Heat Map sections while the dashboard's own market toggle was set
to **US**.

**Root cause**: `GET /stocks/sector_performance` (`services/market-data/src/api/routes.py`) has
**no market filter of its own at all** — every sector genuinely mixes US and HK stocks
together, with `avg_change_pct`/`stock_count` computed across BOTH markets combined. `MoversAnd
Sectors()` (`market-pulse-dashboard.tsx`) never filtered the flattened stock list by market
before this fix — despite only ever being rendered inside the page's own `market === 'US'`
branch — so it silently displayed both markets' movers/sectors regardless of which toggle
state the user had selected. `RegimeBanner` (the sibling section on the same page) already
correctly received `market` as a prop; `MoversAndSectors` simply never did.

**Fix applied**: threaded the page's real `market` state into `MoversAndSectors` as a prop.
Filters the flattened stock list to that market BEFORE computing gainers/losers, and
recomputes each sector's own `avg_change_pct`/`stock_count` from the market-filtered list
locally, rather than trusting the backend's mixed-market aggregate (the backend endpoint
itself was NOT changed — no new query parameter was added there; this is a client-side-only
fix, since the backend has no concept of a market-scoped sector performance response today).

**Live-verified against real production data, before and after**: the mixed list showed
`0117.HK +6.7%`/`6951.HK +3.2%`/`2513.HK +3.1%` among the "US" top gainers; the corrected,
market-filtered list instead correctly shows `CLBT +3.98%`/`MNDY +3.88%`/`V +3.06%`/
`WMT +2.69%`/`DIS +2.63%`/`UNH +2.22%` — every `.HK` symbol excluded, matching a direct
`market == "US"` filter run against the same live endpoint response.

**What to check if this recurs**:
```bash
# Confirm the backend still has no market param (a future backend change here would need the
# frontend fix re-checked against it):
docker exec stockai-market-data-1 grep -n "def sector_performance" -A2 /app/src/api/routes.py

# Spot-check the real market-filtered top-gainer/loser list directly:
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/sector_performance' \
  -H "Authorization: Bearer <token>" | python3 -c "
import json, sys
data = json.load(sys.stdin)
us = [(st['symbol'], st['change_pct']) for sec in data for st in sec['stocks'] if st.get('market') == 'US' and st.get('change_pct') is not None]
print('US gainers:', sorted(us, key=lambda x: -x[1])[:6])
print('US losers:', sorted(us, key=lambda x: x[1])[:6])
"
```

---

