## Recurring Issue: BUG-DECIDE-GAMEPLAN-STYLEFLOAT — decision-engine Crashed on Every Real
## Game-Plan-Bearing BUY Candidate, Silently Falling Back to the DE-Outage Scorer (Fixed
## 2026-08-20)

**Found while sweeping logs across all 12 backend services during EC2 reboot recovery** —
unrelated to the reboot itself, a pre-existing production bug the sweep happened to surface.

**Symptom**: `POST /decide/{symbol}` returned a raw 500 for real BUY candidates. Confirmed
live in production over a 24h window: 3 real symbols (AXON, DIVO, NET) hit this, against a
background of ~660 real 200s in the same window — rare, but real, and each occurrence meant a
live trading decision was made by the FALLBACK scorer instead of decision-engine's own
primary scoring.

**Root cause**: `_decide()`'s game-plan resolution (`routes.py:85`) did
`game_plan = {k: float(v) for k, v in req.game_plan.items()}` — a blanket conversion over
EVERY key in the incoming dict. `paper_trading_engine.py`'s `_build_game_plan_for_style()`
(the ONLY real production caller with a non-empty `game_plan` — `decide.tsx` never sends one)
legitimately returns a dict including `"style": style` (e.g. `"GROWTH"`) alongside the numeric
`entry1`/`entry2`/`breakout`/`stop`/`take_profit`/`current_price` fields. `float("GROWTH")`
raises a raw, unhandled `ValueError`. This bug has existed since decision-engine was FIRST
built (Tier 57, `5871ebc`) — it went unnoticed this long because most real candidates get
filtered out by earlier gates before ever reaching this call site; only a small fraction
actually trigger it on any given day.

**Why the failure was invisible**: `_call_decision_engine()` (the caller,
`paper_trading_engine.py`) catches the non-200 response and logs
`log.warning("decision_engine.bad_status", ...)` — a real log line, but easy to miss amid
routine traffic, and decision-engine's own side shows nothing beyond a bare 500 with no
structured logging of the underlying exception at all.

**Fix applied**: convert only values that are actually numeric-convertible
(`try: float(v) except (TypeError, ValueError): pass through unchanged`) — confirmed via grep
that nothing in decision-engine ever reads a non-numeric `game_plan` key
(`scorer.py`/`sizer.py`/`hard_rejects.py` all use `.get("stop"/"take_profit"/etc.)` with a
numeric default; `"style"` is never read anywhere in this service), so passing it through
unconverted is safe.

**Tests**: `services/decision-engine/tests/test_decide_gameplan_style_float.py` (5 cases) —
source-text extraction of just the fixed block (routes.py is directly importable, but
`_decide()` itself is `async` with too many other dependencies to drive whole). Covers the
exact reported bug, all 4 real trading styles, a `None` value (a different exception type
than the string case), and confirms the fix produces IDENTICAL output to the original
behavior for the common, already-correct case (no `"style"` key at all). Adversarially
verified: reverted to the original blanket-float bug and confirmed 4 of 5 tests failed with
the exact real production `ValueError`, then restored and confirmed byte-identical via `diff`.
Full 239-test decision-engine suite green; `pyflakes` clean.

**What to check if this recurs**:
```bash
docker exec stockai-decision-engine-1 grep -n "except (TypeError, ValueError)" /app/src/api/routes.py
docker logs stockai-decision-engine-1 --since 24h 2>&1 | grep "500 Internal Server Error" | grep "POST /decide"
```

---

