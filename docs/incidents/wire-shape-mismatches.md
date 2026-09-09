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

## AUD-ADMINPAGE-GUARDGAP + AUD-NAV-ROTATIONEXPLAINER-DEADLINK — Nav Visibility and Page Guards Disagreed, in Both Directions (Fixed 2026-09-08)

Not a wire shape, but the same underlying defect: **two declarations of one contract that drifted
apart.** Here the contract is "who may see this page", declared once in `_app.tsx`'s nav tree and
again in each page's own guard.

**Too loose.** Four pages in the `adminOnly: true` group had no admin check:

| Page | Guard |
|---|---|
| `horizon-compare.tsx` | **none at all** — no `getSession()`, no `useEffect`, no redirect |
| `conditional-orders.tsx` | login-only |
| `paper-gates.tsx` | login-only |
| `signal-quality.tsx` | login-only |

`horizon-compare` therefore rendered a full admin analytics page for any logged-in user **and for
a completely unauthenticated visitor**, until its API call happened to 401. Nav-hiding is a
**visibility** restriction, not an access one — the URL stays reachable. This is the same lesson
the Learning-section tier gating already recorded: *"nav-hiding alone left them URL-reachable."*

**Too strict.** `watchlist-rotation-explainer.tsx` sits in the Learning group
(`minTier: 'advanced'`) but required `role === 'admin'`, so `isGroupVisible()` advertised it to
every advanced-tier user and the page then bounced them to the dashboard with no explanation — **a
dead link the nav itself created.** Its two sibling Learning pages both use `hasAdvancedAccess`.

**Severity is bounded, and this was verified rather than assumed.** The *mutating* endpoint behind
`paper-gates` (`POST`/`DELETE /paper-portfolio/entry-gates-override`) correctly requires
`Depends(get_admin_user)`, so no non-admin could change anything. The exposure was read-only admin
analytics — a consistency defect, not a breach.

Fixed all five, and added `isAdmin()` to `lib/auth.ts` so the check has **one name** and cannot
drift into a tier comparison.

### The durable part: `frontend/src/lib/navGuardParity.test.ts`

81 tests that walk the **real `_app.tsx` nav tree** against the **real page sources**, so a *new*
gated page inherits the check automatically instead of relying on a reviewer noticing. Because the
bug appeared from both directions, it asserts both:

- every `adminOnly` page checks admin (and reads the session at all, and redirects);
- no `minTier: 'advanced'` page requires `role === 'admin'`.

It also **asserts its own non-vacuity** (`hrefs.length > 15` for Admin, `> 5` for Learning) —
because a nav-parsing regex that silently matched nothing would make every case pass, which is
exactly how this bug class survives a test suite.

**The dead-link half is currently latent** and worth saying plainly: prod has only an
`ADMIN`/`BASIC` and a `USER`/`BASIC` account, so no user is in the affected class today. It
activates the moment anyone is set to `ADVANCED` — reported as a real defect rather than dismissed
as unreachable, because the tier axis exists precisely to be used.

---

---

## AUD-ADMIN-PROVIDERKEY-NOCLEAR — Clearing a Provider Key in the UI Silently Did Nothing (Fixed 2026-09-09)

**Found while rotating a leaked credential** — the worst possible time to discover it. The Polygon
key had been exposed in container logs, and the Settings page **could not remove it**. It had to be
deleted by hand with `redis-cli`.

### The chain — all three links required

1. `settings.tsx` sent `polygon_api_key: s.polygonApiKey || undefined`. An empty string is falsy,
   so clearing the field produced `undefined`.
2. **`JSON.stringify` drops `undefined` values**, so the field vanished from the request body
   entirely — not sent as `null`, simply absent.
3. `admin.py` guards `if req.polygon_api_key is not None:`, so an absent field is skipped.

Net effect: emptying the box showed **"Saved"** and changed nothing. The old key stayed live in
Redis. A confident success message over a complete no-op.

This is the wire-shape family: **the frontend and backend each behaved reasonably in isolation, and
the contract between them lost the "clear this" intent.** `undefined` is not a value that survives
serialisation, so "field absent" had to carry two different meanings — *unchanged* and *cleared* —
and the backend could only honour one.

### Why it was only these two keys

Claude, DeepSeek, Alpaca and Unusual Whales have **all** had an `unshare_*` flag for exactly this
purpose. `polygon` and `alpha_vantage` — the two *data-provider* keys, routed through
`adapters/registry.py` rather than `ai_keys.py` — were simply never given one. **The pattern
existed; these two sat outside it**, and nothing recorded why.

### The fix

Follows the established convention rather than inventing a new one:

- `clear_runtime_key()` in `registry.py`, mirroring `set_runtime_key()`. It **deletes** rather than
  writing `""` — an empty-string entry still appears in `redis-cli --scan` while the code treats it
  as absent, so an operator auditing which providers are configured would see a key that isn't one.
- `unshare_polygon_key` / `unshare_alpha_vantage_key` on `ConfigRequest`.
- The frontend trims and sends the flag when the field is empty. **Whitespace counts as empty** —
  `get_runtime_key()` already strips, so a whitespace-only key reads back as `None` while still
  existing in Redis: exactly the misleading half-state this fix removes.

**Clear runs AFTER set, deliberately.** A request carrying both a new value and an unshare flag is
ambiguous; ending with **no credential** is the safe reading, ending with a live one is not.

### A parity test now covers the pattern

`test_every_provider_credential_has_a_removal_path` asserts all six credentials
(claude, deepseek, alpaca, unusual_whales, polygon, alpha_vantage) declare an `unshare_*` flag — so
the next provider key cannot be added without a way to remove it.

### A vacuous test of my own, again

`test_the_falsy_undefined_shortcut_is_gone_for_both_keys` first asserted the buggy string was
absent from the whole file — but **the fix's own explanatory comment quotes that string**, so the
test failed against correct code. Fixed by stripping `//` comment lines before matching.

**This is the fifth time this session** a source-text assertion matched prose rather than a
statement. The rule: assert on an imported value, or strip comments first, or match a form that
cannot appear in a comment.
