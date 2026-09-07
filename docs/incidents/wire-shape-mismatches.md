## Recurring Issue: `/events/overview`'s Nested `top_buys` Is a DIFFERENT Shape Than the Standalone Leaderboard Endpoints — Reused the Wrong Type

**Symptom (found 2026-07-17):** After the Reports tab (`reports.tsx`) shipped, the News & Macro
tab threw a runtime crash — reported by the user as "News and Macro not working." Separately,
`intelligence.tsx`'s Overview tab silently showed blank/dash values for insider top-buy scores,
with no visible error at all.

**Root cause:** `GET /events/overview`'s `insider.top_buys` field is populated server-side by
`get_insider_leaderboard()` (`services/event-intelligence/src/api/routes.py`), which returns
`{stock_id, symbol, company, purchases, sales, net_value}` — confirmed directly against the
real live response. This is a genuinely DIFFERENT shape than `InsiderLeaderItem`
(`{symbol, score, buy_count, sell_count, net_value}`), the type used by the STANDALONE
`GET /events/insider/leaderboard` endpoint. `frontend/src/lib/api.ts`'s `EventIntelOverview`
type wrongly reused `InsiderLeaderItem` for the nested `/events/overview` field, even though
the two endpoints are backed by different code and return different fields. The congress side
happened to escape detection the same way — `CongressLeaderItem` has `net_amount`/
`unique_politicians` which don't exist on `/events/overview`'s actual congress rows either,
it just wasn't hit as hard because `intelligence.tsx`'s congress rendering only read the one
field (`net_amount`) that happens to also exist on the real congress shape by coincidence.

`reports.tsx`'s `NewsTab` called `b.score.toFixed(0)` directly — since the real data has no
`score` field, this threw `TypeError: Cannot read properties of undefined (reading 'toFixed')`
and crashed the whole tab. `intelligence.tsx`'s Overview tab called the same nonexistent
`item.score` but routed it through a null-safe `fmt()` helper first (`fmt(item.score)` returns
`'—'` for `undefined`) — same underlying type bug, but it degraded to a silently-wrong display
instead of a hard crash, which is why it went unnoticed until the Reports tab's less-defensive
code hit the exact same bug and actually crashed.

**Fix applied (2026-07-17):** Added distinct `OverviewInsiderTopBuy`/`OverviewCongressTopBuy`
types to `api.ts` matching the REAL `/events/overview` response shape, and corrected both
`reports.tsx` and `intelligence.tsx` to read the real fields (`purchases`/`net_value`/`company`)
instead of the wrong borrowed type's fields (`score`/`buy_count`).

**Design invariant:** never assume two endpoints that return "the same kind of data" (here,
"insider top buys") share a wire type just because the field names sound similar — a nested
field on an aggregate/overview endpoint is frequently built by different backend code than the
dedicated single-purpose endpoint for that same concept, and can have a genuinely different
shape. Verify the ACTUAL response shape (a live curl/query) before reusing an existing
TypeScript type for a new call site, especially for aggregate endpoints like `/events/overview`
that pull from multiple internal helper functions. Also: prefer failing loudly (direct field
access) over silently-safe helpers (`fmt()`-style null coalescing) when wiring up a NEW field
for the first time — the silent version can mask a real type mismatch for a long time, exactly
as it did here in `intelligence.tsx`.

**What to check if a similar "endpoint X and Y look like they return the same shape" bug is
suspected:**
```bash
# Query the real live response directly rather than trusting the TypeScript type on file:
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://api-gateway:8000/events/overview', headers={'Authorization': f'Bearer {tok}'}, timeout=15)
print(r.json()['insider']['top_buys'][0])
"
```

---

## Recurring Issue: A Reference Page Showing a HARDCODED Snapshot of Values the Backend Overrides at Runtime (AUD-HORIZONCOMPARE-BEARREGIME, 2026-09-07)

**Symptom:** none. That is what makes this class dangerous — the page renders cleanly, the
numbers look plausible and internally consistent, and nothing errors. It was found only because
the user asked directly whether the displayed thresholds still matched the system.

**Root cause:** `horizon-compare.tsx` presents `_STYLE_PROFILES` as a reference table. Every one
of its 76 static values was **correct** as a copy of the hardcoded defaults (mechanically diffed
against `signals.py` — zero mismatches). The bug was that four of those rows describe quantities
the running system *overrides in Redis*, so "faithful copy of the default" and "what the system
actually does" are different questions, and the page only answered the first.

A prior fix (`AUD-HORIZONCOMPARE-LIVEWIRE`) had already recognised this and live-wired 4 rows
from `GET /tune_status`. It missed the bear-regime BUY row because `/tune_status` exposes only
`buy_threshold_bull` — so bear *looked* un-tunable. It isn't:

```python
# signals.py _get_dynamic_buy_threshold(), ~line 1909
delta = dynamic - bull_base                                  # dynamic = the single Redis value
return float(np.clip(regime_base + delta, lo, hi))           # applied to WHICHEVER regime is live
```

The single calibrated number is applied as a **delta from the bull baseline to every regime**,
deliberately (T232-CAL2: the value is fit mostly on bull samples, so a flat override would
collapse the tiering that keeps bear tighter than bull). Consequence: **tuning bull silently
moves bear**, and every horizon's bear row was wrong — SHORT 68%→60%, SWING 76%→69%,
LONG 70%→65%, GROWTH 68%→**76%**. The page's own legend actively asserted the opposite
("currently only auto-tunes the bull-regime BUY threshold").

GROWTH is the instructive case: the page **understated** the real threshold by 8pp, presenting
the entry gate as *looser* than the system enforces — the direction that misleads toward
expecting more signals than will actually fire.

**Second bug, found while fixing the first:** `liveValueFor()` returned `fmtPct(...)` → `"55%"`
while the static cell holds `"> 63%"`, and the divergence test is `live !== row[h]`. Those can
never compare equal, so the bull BUY row rendered as green-bold "overridden" with a struck-through
default **permanently** — including when the live value equalled the default. A highlight that is
always on carries no information, and it had been masking the very drift it was meant to reveal.

**Fix:** derive bear from bull (the delta is defined against a *known constant*, so the live
bear value is recoverable from the live bull value alone), and emit the `> ` prefix so the
equality check becomes meaningful for the first time.

### What to check when adding or reviewing a "reference values" page

1. **For every displayed constant, ask whether anything writes an override for it at runtime** —
   Redis, DB config, an env var. A value being hardcoded in source does *not* mean it is what the
   system uses. Grep for the constant's name near `redis`/`_get_dynamic`/`_tuned`.
2. **A status endpoint exposing only *some* fields is not evidence the rest are static.** Here,
   bear was fully derivable from what `/tune_status` did expose; the missing field was a gap in
   the endpoint, not proof of immutability.
3. **Verify divergence/"changed" indicators can actually go both ways.** Construct the equal case
   and confirm the highlight turns *off*. An indicator stuck on is as broken as one stuck off, and
   is harder to notice.
4. **Frontend code that reimplements a backend formula must live in `lib/` with tests that pin
   real measured values** — not values recomputed from the same constants the implementation uses,
   which makes the test vacuous. This repo has no component-level React harness, so logic left
   inline in a `.tsx` gets no coverage at all and drifts silently.

**Still open (documented in the page legend, not fixed):** the SELL row shows a flat `< 35%`
fallback, but SELL *is* overridable and GROWTH currently has a live `0.30` override.
`/tune_status` does not expose SELL at all, so surfacing it needs a backend change.

---
