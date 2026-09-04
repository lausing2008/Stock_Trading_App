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
