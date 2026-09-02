## Deep Audit: Everything Shipped in the T258 Session (2026-07-22) — 6 Confirmed Findings, All Fixed

**What this was**: after both T258 features (Sector Rotation Trajectory, Accum/Dist +
Breakout Quality), the tracker correction, and the earnings-digest fix all shipped, a
multi-agent audit reviewed every real diff from that session (5 independent review agents,
one per functional area, each followed by an independent adversarial-verify pass that
re-checked every finding against the actual current code before it was trusted). All 6
concrete findings survived verification; 3 were fixed same-day as HIGH/CRITICAL, 3 as
MEDIUM/LOW. The T171 tracker correction and the earnings-digest incident repair were BOTH
independently re-confirmed accurate by the audit (all 5 T171 sub-claims verified true; the
`check_signal_alerts()` consolidated-digest structure confirmed intact, `_earnings_reminder_
body` confirmed absent from the file).

### CRITICAL — `updown_vol_ratio = float("inf")` broke the ENTIRE `/ta/{symbol}/levels` response

**Symptom (would have been, if not caught pre-production)**: any stock whose last 20 daily
bars had positive up-close volume and ZERO down-close volume (a strong uninterrupted run, or a
thin/low-liquidity name) would make `detect_accumulation_distribution()` return a real Python
`float("inf")` for `updown_vol_ratio`. `json.dumps(float('inf'))` emits the bare, non-standard
token `Infinity` (Python's `allow_nan=True` default) — which browser `JSON.parse` **rejects
outright** with a `SyntaxError`. Independently reproduced end-to-end: `json.dumps({"x":
float("inf")})` → `'{"x": Infinity}'` → `node -e "JSON.parse(...)"` → real `SyntaxError:
Unexpected token I in JSON`. This would have broken not just the new accumulation/distribution
field but the ENTIRE `GET /ta/{symbol}/levels` fetch — support/resistance lines, trendlines,
Fair Value Gaps, and the new Volume Pattern Read card would all fail to load together for that
symbol, since they all come from the same response body.

**Root cause**: `trendlines.py`'s `detect_accumulation_distribution()` computed `updown_vol_
ratio = float("inf") if up_vol > 0 else None` when `down_vol == 0`, and its own `round()` guard
deliberately passed `inf` through unrounded (`round(updown_vol_ratio, 2) if updown_vol_ratio
not in (None, float("inf")) else updown_vol_ratio`) — the very code meant to be a safety guard
was the thing preserving the unsafe value. The one test covering this case (`test_flat_price_
no_down_days_...`) only asserted `== float("inf")`, checking the function's raw return value,
never the JSON-serialization path — so it actively certified the bug as correct behavior.

**Fix applied**: replaced the real `inf` with a large-but-finite sentinel (`999.0`) —
still unambiguously "overwhelmingly up-volume" to any reader of the number, but always
JSON-safe. Rewrote the test to assert the new sentinel AND to actually call `json.dumps()` on
the result (catching this exact class of bug at the point it would have surfaced, not just at
the function's own return value).

### HIGH — `assess_breakout_quality()` could report `'real'` for a fully-reversed breakout (reuse hazard)

**Root cause**: the function only ever checked the SINGLE bar immediately after the breakout
bar to decide `'real'` vs `'failed'` — it never checked whether the move was still intact as
of the actual current (most recent) bar. A breakout that held for exactly one bar (satisfying
that single check) but then fully reversed and stayed reversed for many bars afterward would
still be reported `'real'`. Verified with a concrete fixture: `closes=[95,96,97,105,106,107,
104,99,95,92,90]`, `level=100`, `direction="up"` — a breakout bar at idx 3 with 5× volume
still returned `{'quality':'real', 'close':105.0}` even though the actual current close is 90,
well below the level. **Not reachable through the current live call site** — `routes.py` only
ever feeds `sr_cleared_resistance`/`sr_cleared_support`, both of which are `<= current`/`>=
current` by construction, so price is always still beyond them today. But the function's own
docstring explicitly invites reuse with an arbitrary externally-supplied level ("or the
game-plan breakout level"), and any future caller passing a level not guaranteed on the correct
side of current price would hit this exact stale-verdict trap.

**Fix applied**: added a guard — `if quality == "real" and not _beyond(n - 1): return None` —
applied ONLY to the `'real'` branch, not unconditionally up front. This distinction matters: a
naive unconditional guard (`if not _beyond(n-1): return None` before classification) would have
made the `'failed'` case unreachable, since `'failed'` by definition means price is NOT beyond
the level anymore — that unconditional version was tried first, broke the pre-existing
"reverses next bar → failed" test, and was corrected to gate only the `'real'` path.

### LOW — `detect_accumulation_distribution()` had a silent 21–29-bar dead zone

**Root cause**: the entry guard admitted any input with `len(close) >= window + 1` (21 bars by
default), but `obv_trend_bullish` was only computed when `len(obv) >= 30` — and both the
accumulation and distribution branches require a non-`None` `obv_trend_bullish`. So any input
in the 21–29-bar range was silently admitted by the guard but could NEVER produce anything but
`'neutral'`, regardless of how decisive the volume-ratio evidence was. Verified: 25 monotonic-
up bars with heavy volume returned `state:'neutral'` while the identical construction at 30
bars correctly returned `state:'accumulation'`.

**Fix applied**: raised the entry-guard floor to `max(window + 1, 30)` — the guard now honestly
reflects the function's real minimum, degrading explicitly (all fields `None`) below 30 bars
rather than silently admitting inputs it can never classify.

### HIGH — Sector rotation's insufficient-data branch wrote the WRONG dict keys, silently dropping real, rankable sectors

**Root cause**: `_compute_sector_rotation()`'s branch for `recent_kscore is None or prior_
kscore is None` wrote `{"momentum": 0, "recent": None, "prior": None}` — using keys `"recent"`/
`"prior"`, not `"recent_kscore"`/`"prior_kscore"`, contradicting both the full-data branch
right below it and the `get_sector_rotation()` docstring. `rank_sectors()` reads `data.get
("recent_kscore")` — so a sector with a REAL current K-score but no 4-weeks-prior average
(e.g. a sector newly clearing the ≥3-ranked-stocks floor this week) got `rank=None`, was
silently excluded from ranking entirely, excluded from the half-cutoff `total`, AND persisted
to `sector_rotation_snapshots` with `recent_kscore=None` — permanently losing that data point
even for FUTURE weeks' trajectory comparisons. This is exactly the "newly rankable sector"
case the module's own docstrings claim degrades gracefully to `trajectory=None` — instead it
vanished entirely, with no trajectory AND no rank AND no persisted K-score.

**Fix applied**: the branch now still writes `recent_kscore`/`prior_kscore` (using `None` only
for whichever one is genuinely missing) — `momentum`/`delta` remain unset/`None` since a
real momentum can't be computed without both values, but the real current K-score is preserved
and the sector is no longer silently dropped from ranking.

### HIGH — Sector trajectory compared absolute rank across different-sized fields, producing misleading labels

**Root cause**: `classify_trajectory()` computed `delta = prior_rank - current_rank` on raw
rank NUMBERS with no adjustment for the fact that the rankable-sector count can genuinely
differ between snapshots (more/fewer US sectors clearing the ≥3-ranked-stocks floor week to
week). Traced a concrete misleading case: a sector at rank 4 of a 4-sector field (dead last,
worst possible standing) a month ago, now at rank 4 of an 8-sector field (top half) — raw
`delta = 4 - 4 = 0` reads as "flat," and `current_rank=4 <= half=4.5` reads as "top half,"
producing **"Established Leader"** — labeling a sector that was last place a month ago as a
steady, unremarkable leader, purely because the field happened to grow.

**Fix applied**: both ranks are now normalized to a 0-1 percentile within their OWN
snapshot's field size (`(rank - 1) / (total - 1)`, so rank 1 is always exactly 0 and the worst
rank is always exactly 1, regardless of field size) before computing the up/flat/down
direction — comparing standing-relative-to-peers, not raw position. The same reported case now
correctly produces **"Emerging Leader"** (percentile improved from 1.0 to 0.43 — a genuine rise
in relative standing). `classify_trajectory()` gained an optional `prior_total_sectors`
parameter (defaults to `total_sectors` when omitted, preserving the exact pre-fix behavior for
any same-field-size comparison); `build_trajectories()` now computes and passes the real prior
snapshot's own rankable count instead of silently reusing the current snapshot's count for both
sides.

### MEDIUM — No lower bound on "the ~4-week-ago" prior-snapshot lookup

**Root cause**: the prior-snapshot query (`SELECT MAX(as_of) FROM sector_rotation_snapshots
WHERE as_of <= :cutoff`, cutoff = today − 28 days) had only an UPPER bound. After any gap in
the weekly job (a missed run, a multi-week outage), it would silently pick the newest snapshot
still ≤28 days old — which could be months stale — and present the resulting trajectory
comparison as if it were a genuine "~4 weeks ago" read, exactly as both this function's own
docstring and the public API's docstring advertise.

**Fix applied**: added a lower bound (`AND as_of >= :floor`, floor = today − 56 days). A gap
larger than the 28–56-day window now correctly yields no comparable prior snapshot
(`trajectory=None`) rather than a misleadingly-labeled comparison against a much-older week.

### Verification discipline applied throughout

All 6 fixes were adversarially verified via sabotage-and-confirm-failure before being trusted:
reverting the `inf`→`999.0` fix and confirming the JSON-safety test failed; reverting the
`assess_breakout_quality()` reversal guard and confirming the stale-breakout test failed (and,
separately, confirming the guard's OWN placement — gating only the `'real'` branch, not
unconditionally — by first trying the naive unconditional version and watching it break the
pre-existing "reverses next bar → failed" test, which is exactly why the final version gates
only `'real'`); reverting the 21-29-bar guard fix and confirming the dead-zone regression test
failed; reverting the sector-rotation key-naming fix, the rank-normalization fix, and the
prior-snapshot lower-bound fix each independently and confirming their own dedicated tests
failed for the right reason, before restoring all three. Full 419-test market-data suite (up
from 412), 49-test technical-analysis suite (up from 44), 89-test frontend suite, and frontend
typecheck all green after every fix.

**What to check if any of these regress**:
```bash
# Infinity-safety regression check — must never see a bare Infinity token in a real response:
docker exec stockai-technical-analysis-1 curl -s 'http://localhost:8002/ta/<SYMBOL>/levels?timeframe=1d' \
  | grep -o '"updown_vol_ratio":[^,}]*'
# Should always show a finite number or null — never the literal word Infinity.

# Sector rotation key-naming / rank-normalization regression check:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT sector, as_of, recent_kscore, rank FROM sector_rotation_snapshots ORDER BY as_of DESC LIMIT 20;"
# A sector with a real recent K-Score should never show recent_kscore=NULL just because it
# lacked prior-week data.
```

---

