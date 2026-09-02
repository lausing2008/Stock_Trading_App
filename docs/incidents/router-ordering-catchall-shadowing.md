## Recurring Issue: BUG233-ROUTERORDER — Catch-All `/{symbol}` Route Silently Shadowed Literal Paths From Sibling Routers (Fixed 2026-07-22)

**Symptom (found immediately on first deploy of the signal-engine routes.py split above):**
`GET /signals/confidence-calibration` returned `500 Internal Server Error`. Logs showed
`signal_for()` (the catch-all `GET /{symbol}` handler in `routes.py`) was invoked with
`symbol="confidence-calibration"`, which called `generate_all_signals("confidence-calibration")`
→ `_fetch_prices("confidence-calibration")` → a 404 from market-data trying to fetch price
history for a "symbol" that's actually a route name.

**Root cause:** `main.py`'s `create_app("signal-engine", routers=[router, calibration_router,
outcomes_router])` registered `router` (containing the catch-all `GET /{symbol}` at the end of
`routes.py`) FIRST. FastAPI/Starlette matches routes across ALL mounted routers in registration
order — so `router`'s `/{symbol}` matched `/confidence-calibration` before
`calibration_router`'s own dedicated `@router.get("/confidence-calibration")` (registered
second) ever got a chance. In the original single-file `routes.py`, this was never an issue —
`/confidence-calibration` and every other literal path were declared BEFORE `/{symbol}` in the
same file's route-declaration order, which Starlette respects within one router. The split
correctly preserved declaration order WITHIN each file, but never accounted for order ACROSS
the 3 files once they became 3 separate routers passed to `create_app()`.

**Fix applied:** reordered `routers=[...]` to `[calibration_router, outcomes_router, router]` —
router order across files makes `router`'s catch-all `/{symbol}` register LAST, so every literal
path in the other two routers is matched first, exactly restoring the original single-file
file's effective route-matching order.

**Tests:** `services/signal-engine/tests/test_main_router_order.py` (3 cases) — source-text
regression checks (`main.py` can't be imported in this test environment; `common.service` is
part of the wholesale `common` stub) confirming `router` is last in the `routers=[...]` list,
`calibration_router`/`outcomes_router` are both imported and precede it, and that `routes.py`
still contains the catch-all this test guards against (so the test's own premise stays valid if
that route is ever removed). Adversarially verified by reverting to the original buggy order
and confirming the test failed correctly before restoring.

**Design invariant — applies to ANY future split of a single-router FastAPI file into multiple
routers mounted via `create_app(routers=[...])`:** if the original file had a catch-all
dynamic-path route (`/{param}`) declared after all its literal-path routes (the normal,
correct pattern for avoiding exactly this shadowing within one router), splitting that file
into multiple routers must preserve the EFFECTIVE global order — every router containing ONLY
literal paths must be listed before any router containing a catch-all, regardless of which
file the catch-all's OWN literal-path siblings live in. Grep for `@router.get("/{` (or `.post`)
in every router being combined, and make sure any such router sorts last in the `routers=[...]`
list passed to `create_app()`.

**What to check if this looks wrong again:**
```bash
docker exec stockai-signal-engine-1 curl -s -o /dev/null -w '%{http_code}\n' \
  'http://localhost:8005/signals/confidence-calibration' -H "Authorization: Bearer <token>"
# Should be 200, not 500. A 500 here with a downstream 404 fetching prices for a
# non-ticker-looking "symbol" is the exact signature of this bug class recurring.
grep -n "routers=\[" services/signal-engine/src/main.py
# The router containing any catch-all @router.get("/{...}") must be listed LAST.
```

---

