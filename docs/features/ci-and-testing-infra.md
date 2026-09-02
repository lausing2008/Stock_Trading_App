## Feature Reference: CI Coverage Gap Closed + T255-REPORTS-TAB Phase 2 (HK Breadth + Flow Leaderboard) (2026-07-28)

**Two unrelated fixes shipped together this pass — a CI/tracker correction, and a real
feature build.**

### CI gap: 3 services with real test suites weren't running in CI at all

**Found while correcting a stale tracker entry** (`tech-testing-framework`, which falsely
claimed "zero automated tests exist... every code change is deployed blindly" — badly stale;
136 backend pytest files across 12 services (~1,303 test functions) and 7 frontend Vitest
files (89 tests) already existed). Investigating the real state surfaced a genuine,
non-cosmetic gap underneath the stale claim: `decision-engine` (165 tests),
`event-intelligence` (159 tests), and `news-intelligence` (58 tests) — three services with
real, substantial, currently-passing test suites — were silently absent from BOTH the
`Makefile`'s `test` target and `.github/workflows/test.yml`'s dependency-install list. ~382
real test functions were never actually executed in CI; a regression in any of the three could
ship completely unnoticed. Separately, CI's frontend job only ran `tsc --noEmit` — the 7
existing Vitest files were typechecked but never actually executed in CI.

**Fix**: added all 3 services to `Makefile`'s `test` target and the workflow's install loop;
added an `npm test --prefix frontend` step to the typecheck job (renamed "Frontend typecheck +
unit tests" to reflect the addition). Verified all 3 previously-uncovered backend suites and
the frontend suite pass locally before wiring them into CI — this was not a blind addition.

**Tracker correction**: `tech-testing-framework` flipped to `defaultStatus: 'done'` with an
`implementedNote` citing the real current test counts and documenting the CI gap found/fixed —
remaining genuinely-open future work (API integration tests, ML accuracy regression gating,
Playwright E2E, scheduler smoke tests) is preserved in the note, not silently dropped.

### T255-REPORTS-TAB Phase 2 — HK market_breadth param + HK Stock-Connect top-N flow

**Gap closed**: `GET /stocks/market_breadth` was hardcoded to `Stock.market == Market.US` with
no `market` parameter at all — HK users on the Reports page's Trend tab got no breadth reading
(the UI even had an explicit "US-only data source — no HK breadth endpoint yet" callout).
Separately, `GET /stocks/hk-connect-flow/{symbol}` was per-symbol only — despite
`hk_connect_flows` being a real, populated table (verified live-fetching via Eastmoney since
MD-HKCONNECT2), there was no market-level top-N aggregation for the Money Flow tab's "where is
money flowing" ask.

**Implementation**:
1. `market_breadth()` gained a `market: str = Query("US", pattern="^(US|HK)$")` parameter,
   replacing the hardcoded `_Market.US` filter with `_Market(market.upper())`. The Redis cache
   key was namespaced per market (`f"{_MARKET_BREADTH_KEY}:{market.upper()}"`) — the ORIGINAL
   key was a single global entry regardless of market, which would have silently served a
   stale/wrong-market reading the moment the param existed without this fix.
2. New `hk_connect.py::build_flow_leaderboard(rows, limit)` — a pure function aggregating
   `(symbol, net_buy_hkd)` rows: sum per symbol across the window, filter to `net_value > 0`
   only (matching `AUD-INSIDERTOPBUYS-NETNEGATIVE`'s established "a top-buys list must never
   pad itself out with net sellers" precedent exactly), sort descending, cap at `limit`. New
   `get_flow_leaderboard(db, days=5, limit=20)` wraps it with the real DB query (no market
   join needed — `hk_connect_flows` is ingested HK-only via `_symbols_for("HK")`).
3. New route `GET /stocks/hk-connect-flow/leaderboard/top` — registered as a literal
   `/leaderboard/top` sub-path (not a bare `/{symbol}`), so it can never collide with the
   existing per-symbol route, matching the `BUG233-ROUTERORDER` lesson documented elsewhere in
   this file.
4. Frontend: `reports.tsx`'s Trend tab now passes the real `market` through to
   `api.marketBreadth(market)` (removing the stale UI callout); the Money Flow tab gained a
   new HK-only "Stock-Connect Southbound Flow — Top Net Buys" card.

**Tests**: 12 new cases — 7 in `test_hk_connect.py` for `build_flow_leaderboard()` (summing
across multiple rows per symbol, net-seller exclusion, exactly-zero exclusion, descending sort,
limit-after-filter, `None`-safe handling, empty input), 5 in `test_market_breadth_market_param.py`
(source-text extraction — `routes.py` can't be imported directly in this test environment) for
the market param, the cache-key namespacing, and the new route's registration/delegation.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted:
1. The market filter (reverted to hardcoded `_Market.US`) — caught by its dedicated test.
2. The net-buyer floor (`if total > 0]` removed) — caught by 2 dedicated tests (net-seller
   exclusion and exactly-zero exclusion).
3. (Verified, not sabotaged separately — covered by #2's exact-zero case) the strict `> 0`
   boundary, matching the insider leaderboard's own floor exactly.

Full 564-test market-data suite (up from 552) green after every revert; `pyflakes` clean
(confirmed via `git stash` that all 6 pre-existing warnings predate this change). Frontend
typecheck clean, all 89 Vitest tests pass, full `next build` clean.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/market_breadth?market=HK'
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/hk-connect-flow/leaderboard/top?days=5&limit=20'
```
If the HK breadth reading looks wrong, check the Redis key directly —
`docker exec stockai-redis-1 redis-cli get stockai:market_breadth:HK` — a stale
non-market-scoped key (`stockai:market_breadth`, no suffix) from before this fix could still
be cached under the old name but is no longer read by the new code, so this shouldn't recur,
but is worth ruling out if a reading looks frozen.

---

