## Recurring Issue: AUD-GAMEPLAN-NONERECOMMENDATION — `_build_game_plan()` Crashed on Every ETF, Silently Dropping the Game Plan From the Signal Alert Email (Fixed 2026-09-04)

**Symptom:** user reported receiving a real AI Signal alert email for GDX (a gold-miners ETF)
with no game plan section at all, despite the alert being a genuine BUY transition. Confirmed
directly in production logs:

```
{"symbol": "GDX", "de_verdict": "BUY", "event": "signal_alert.de_gate_passed", ...}
{"symbol": "GDX", "error": "'NoneType' object has no attribute 'lower'", "event": "game_plan.build_failed", ...}
{"symbol": "GDX", "prev": "SELL", "current": "BUY", "style": "GROWTH", "event": "signal_alert.fired", ...}
```

The alert fired correctly — the game plan build failed silently in between, caught by
`_build_game_plan()`'s own outer `except`, which logs the failure and returns `None`.
`send_signal_alert_email()` then renders no game plan section at all whenever `game_plan` is
`None` (its own, correct, pre-existing behavior) — a real bug upstream of the email renderer,
not in it.

**Root cause:** `_build_game_plan()` (`services/market-data/src/services/scheduler.py`) computed
its "bullish analyst consensus" catalyst line via:
```python
"...Analyst consensus bullish..." if (fundamentals or {}).get("recommendation", "").lower() in ("buy", "strong_buy") else None,
```
`.get("recommendation", "")`'s `""` default only ever substitutes when the key is **missing**
from the dict — it does nothing when the key is **present** with a value of `None`. Confirmed
live: `GET /stocks/GDX/fundamentals` really does return `"recommendation": null` — every ETF
(GDX, SPY, QQQ, sector/index funds generally) genuinely has no individual analyst BUY/SELL
rating the way a single stock does, so `recommendation` is a real, always-present key whose
value is legitimately `None` for the entire ETF asset class, not an occasional data gap. Calling
`.lower()` on that `None` crashed the whole function.

This is the exact same bug SHAPE as this codebase's own established "falsy-zero" class of bugs
(a default argument that only covers a missing key, not a present-but-falsy/None value) — just
manifesting as a hard crash here (`.lower()` on `None`) rather than a silently-wrong number. A
correct, already-used idiom exists elsewhere in the same file
(`analyst_ratings[sym] = (payload.get("recommendation") or "").lower()`,
`check_signal_alerts()`) — this call site just hadn't been written to match it.

**Fixed:** `_recommendation = ((fundamentals or {}).get("recommendation") or "").lower()` — the
`or ""` after the `.get()` (not inside its default argument) catches the value being `None`
even when the key exists, which the `.get()` default alone structurally cannot. Every other
catalyst-line condition in the same function was checked and confirmed NOT to share this
pattern (they all use `.get(key)` with no `.lower()`/`.upper()` call, or already guard for
`None` via an `is not None` check).

New `test_build_game_plan_none_recommendation.py` (5 tests, source-text extraction —
`scheduler.py` can't be imported directly in this test environment; `_build_game_plan()` is
pure aside from its own `yfinance.Ticker` call, mocked out entirely): the exact bug scenario
(a present `recommendation: None` key) no longer crashes and returns a real game plan; a
genuinely missing `recommendation` key still works (the pre-existing, already-correct path);
`fundamentals=None` entirely still works; a real bullish rating (`"buy"`) still adds its
catalyst line (confirms the fix didn't accidentally suppress the working case); a bearish
rating correctly does NOT add the bullish catalyst line. 1 adversarial sabotage cycle (reverted
to the original `.get("recommendation", "")` form) — reproduced the exact original crash
(caught by the targeted test), restored + confirmed byte-identical via `md5sum`. Full
2726-test market-data suite green (up from 2721).

**Separately investigated in the same session, found NOT to be a bug:** the user also asked
about US GROWTH Paper Portfolio's return dropping from ~5% (2026-08-13/17) to ~1.4% (2026-09-04)
while US SWING moved to ~1.8%. Traced directly against `paper_equity_curve`/closed
`paper_trades`: real winners in that window (NET +$527, ARMK +$208, JPM +$194, DIVO +$187, NOW
+$334 — roughly +$1450 combined) were more than offset by 2 large losses — AXON (-12.4%,
-$647, `stop_hit`) and SNOW (-19.0%, -$986, `stop_hit`, the exact earnings/8-K gap-chase pattern
`AUD-MINRR-MARKETBLIND`'s companion fix, tier 342, already addresses going forward). AXON's own
`highest_price` (635.26) never meaningfully exceeded its `entry_price` (626.93, +1.3%) — nowhere
near GROWTH's own `trail_trigger_pct`/`breakeven_trigger_pct` thresholds (5-7%/3-4%) — so its
trailing-stop/breakeven mechanisms never armed at all; `current_stop` stayed exactly at the
initial `stop_loss` (551.00) the entire time, and the exit price (548.94) landed almost exactly
there. GROWTH style's own `stop_pct: 0.880` (a documented, intentional 12% initial stop,
matching the style's higher-volatility/longer-hold philosophy) accounts for the loss size
directly — the position simply never confirmed and the pre-defined risk limit did exactly what
it's designed to do. No bug found in the exit-management logic itself.

**What to check if this looks wrong**:
```bash
# Confirm a specific symbol's fundamentals recommendation field before assuming a game-plan
# failure is unrelated to this bug class:
docker exec stockai-market-data-1 curl -s http://localhost:8001/stocks/<SYMBOL>/fundamentals | python3 -m json.tool | grep recommendation

# Confirm the fix is live:
docker exec stockai-market-data-1 grep -n "_recommendation = ((fundamentals or {}).get" /app/src/services/scheduler.py

# Check for any OTHER game_plan.build_failed events (a different root cause):
docker logs stockai-market-data-1 --since 24h | grep 'game_plan.build_failed'
```

---

## Recurring Issue: AUD-GAMEPLANBATCH-WRONGIMPORT — `GET /options-game-plan/batch` Had Never Returned Real Data Since It Shipped, a Wrong Relative Import Path (Fixed 2026-09-04)

**Symptom:** after confirming (same session, above) that the Options Game Plan EOD batch job
had never successfully written a snapshot, it was manually triggered and confirmed to populate
54 real rows, including GDX. User then reported the screener's "Options Plan" column still
showed no data for any BUY-signal stock, despite the underlying DB rows now genuinely existing
(confirmed directly: NVDA and SNDK, both live BUY signals at the time, both had real
`options_game_plan_snapshots` rows dated that same day).

**Root cause:** minting a real JWT and hitting the actual `GET /stocks/options-game-plan/batch`
route directly (not through the frontend) reproduced a hard `500 Internal Server Error`.
Production logs showed the real exception:
```
File "/app/src/api/routes.py", line 4573, in get_options_game_plan_batch
    from .options_game_plan_snapshot import get_latest_options_game_plan
ModuleNotFoundError: No module named 'src.api.options_game_plan_snapshot'
```
`get_options_game_plan_batch()` lives in `services/market-data/src/api/routes.py`, but
`options_game_plan_snapshot.py` actually lives in `services/market-data/src/services/` — one
package over. `from .options_game_plan_snapshot import ...` resolves relative to the CALLING
module's own package (`src.api`), which has no such file, so the import raised on every single
real request to this route, 500ing it unconditionally. This means the Options Game Plan
screener column and BUY-alert email section had **never actually returned real data through
this specific route since it shipped** (tier 332, a prior session) — not a timing/scheduling
issue like the sibling `AUD-OPTIONS4-GAMEPLANBATCH` EOD-job finding earlier in this same file,
but a second, independent bug in the READ path that the WRITE path's own success could never
have surfaced.

**Why this went undetected through the original build's own test suite**: `scheduler.py` (a
different file) has 2 of its own imports of the same module
(`compute_options_game_plan_snapshots_eod()`'s and `check_signal_alerts()`'s), both correctly
written as `from .options_game_plan_snapshot import ...` — correct there because `scheduler.py`
itself lives in `src/services/`, the same package `options_game_plan_snapshot.py` is actually
in. The identical-looking import string is right in one file and wrong in the other purely
because of which package each file lives in — and the original 21-test batch
(`test_options_game_plan_batch_route.py` et al.) checked the route's own logic (tier gating,
never-live-fetch, fail-open, field surfacing) exhaustively via source-text extraction, but never
asserted on the actual import *path string* itself, so a `.` vs `..services.` typo passed every
existing test while still being fatal at real request time.

**Fixed:** `from ..services.options_game_plan_snapshot import get_latest_options_game_plan` —
matches every other `src/api/routes.py` cross-package import into `src/services/` (e.g.
`from ..services.paper_trading_engine import ...`, `from ..services.ingestion import ...`).
New `test_imports_options_game_plan_snapshot_via_the_correct_relative_path` in the existing
`test_options_game_plan_batch_route.py` — asserts the exact corrected import line is present
and the old broken form is absent, closing the exact gap that let this ship undetected. 1
adversarial sabotage cycle (reverted to the original broken `.` form) — caught cleanly by
exactly the new targeted test, restored + confirmed byte-identical via `md5sum`. Full 2727-test
market-data suite green.

**What to check if this looks wrong**:
```bash
# Confirm the fix is live in the running container:
docker exec stockai-market-data-1 grep -n "from ..services.options_game_plan_snapshot import" /app/src/api/routes.py

# Hit the real route directly with a real admin/advanced token and confirm a 200, not a 500:
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/options-game-plan/batch?symbols=<REAL_BUY_SYMBOL>' \
  -H 'Authorization: Bearer <token>'

# Confirm real snapshot data exists for the symbol you're testing:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT s.symbol, ogps.as_of FROM options_game_plan_snapshots ogps JOIN stocks s ON s.id=ogps.stock_id WHERE s.symbol='<SYMBOL>';"
```

If a future refactor moves either file, grep both `src/api/routes.py` and `src/services/
scheduler.py` for `options_game_plan_snapshot` imports and re-verify each one's relative path
is still correct for its own file's actual location — this bug class (a relative import that's
correct in one file and silently wrong in another, purely because of which package each lives
in) can recur for ANY shared helper module, not just this one.

---
