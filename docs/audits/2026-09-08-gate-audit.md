# Deep Audit (2026-09-08): ALL GATES — 12 findings, 6 FIXED (tier 368)

Run as **three parallel audits** (entry / exit+risk / signal+alert) because the gate surface is
**158 constants across 14 files** — one agent would have covered all three badly.

**Read this before touching any gate.** The single most valuable thing here is not a finding —
it is the pattern in the next section.

---

## THE PATTERN: a fix that lands on the shadow path, not the deciding path

**Three separate findings in one session were the same failure.**

| | Finding | Status |
|---|---|---|
| 1 | `AUD-CHASE-ROC10` — anti-chase filter ported to the fallback, never blocked a real entry | fixed 2026-09-07 |
| 2 | `AUD-ENTRY-BREAKOUTREF-DEONLY` — `breakout_ref` added to the fallback only | fixed 2026-09-08 |
| 3 | `AUD-SIGALERT-RRUNREACHABLE` — `expected_move` target added to the fallback only | fixed 2026-09-08 |

```
BEFORE the fixes:
  grep -rn breakout_ref   services/decision-engine/    ->  0 matches
  grep -rn expected_move  services/decision-engine/    ->  0 matches
```

`breakout_ref` now appears in both DE consumers. `expected_move` still does not — that one was
fixed differently (by deriving the R:R target from the calibrated floor rather than porting the
expected-move logic), so the grep stays 0 there by design.

**Why it keeps happening.** `paper_trading_engine._should_enter()` is the **shadow-logged
fallback** — `decision_engine_mode` defaults to `"primary"` — but it is the more readable, more
heavily-tested, more *obvious* place to put a fix. And a fix there produces perfectly plausible
shadow logs while changing no real decision.

> **THE CHECK TO RUN, every time you fix a gate:** grep the fixed symbol under
> `services/decision-engine/` and confirm it appears.

Note `test_entry4_chase_parity.py` **already encodes this lesson** for `roc_10` (it asserts
`"roc_10" in DE_SRC`) — and never applied the same assertion to `breakout_ref`, in the very
same commit.

---

## FIXED: AUD-SIGALERT-RRUNREACHABLE — CRITICAL

**BUY signal alerts were completely dead in production for four days.** Not degraded — zero.

Two independently *correct* mechanisms multiplied into a silent total outage:

```
SUPPLY   rr_target   = live_price + 2.0 * (live_price - stop)      # hardcoded 2.0
         take_profit = min(rr_target, live_price * target_pct)     # min() only LOWERS
         => rr_ratio pinned at <= 2.00 BY CONSTRUCTION, no market input

DEMAND   calibrate_min_rr_ratio raised the floor to 2.25 (3.38 choppy/risk_off)
         from real trade data — correct on its own terms

         2.00 < 2.25  ->  rejects EVERY candidate, always
```

It reaches the alert path because `check_signal_alerts()` POSTs `/decide` with only
`{style, market}` and no `game_plan`, and DE's `build_game_plan()` uses signal reasons only when
`entry2 + stop + take_profit` are **all** present — which **zero of 1,365** production BUY
signals carry (measured). So it always fell through to `_default_game_plan`.

**Production evidence:**

```
72h:  signal_alert.conviction_met = 123
      signal_alert.de_gate_passed = 0          <- 100% block rate

Last actually-sent alert, by state:
      WAIT 2026-09-08 | HOLD 2026-09-08 | SELL 2026-09-08 | BUY 2026-09-04
```

Exit warnings flowed. Buy signals did not. Every job reported `ok`, and 4,722 skip lines read as
ordinary selectivity.

**Fix:** the target multiple is derived from the **same calibrated `min_rr_ratio` the gate
demands** (+0.15 margin, since `rr_ratio` is `round(...,2)`), so supply and demand cannot drift
apart the next time the calibrator promotes a value. Fails **safe** to the historical 2.0 when
params are unavailable, and ignores a zero/negative floor as bad data.

### A correction to my own claim, caught by a test rather than by review

I spot-checked **one** ATR value in the container, saw `rr=2.4`, and reported the fix unblocked
**83%** of signals. **That was wrong.**

On the **fixed-stop** geometry every style caps at ≤ 2.00 (SWING/LONG/GROWTH exactly 2.00, SHORT
1.67), and **~62% of real BUY signals carry ATR above 3.5% of price**, where that floor binds.
Measured ATR sweep across 1.0–8.9%:

| style | passes |
|---|---|
| GROWTH | 80/80 |
| LONG | 80/80 |
| SWING | 17/80 |
| SHORT | 2/80 |

**Honest scope: GROWTH and LONG fully unblocked (877 of 1,365 = 64%), SWING only at low ATR,
SHORT essentially never.** A real improvement over a total outage, and **not** the whole fix —
the remaining ceiling is the per-style target cap, not this constant.

> **One spot-check is not a measurement.** The test caught it because it encoded the *geometry*
> rather than a single observed value.

### SHORT — deferred by explicit user decision (2026-09-08)

SHORT's own params (3% stop / 5% target) cap R:R at **1.67 by design**, and the style cap binds
before the multiple does. It stays blocked — which is now a **correct** answer rather than a
broken one.

Forcing it would fabricate a target its tuned parameters do not support: **the same error class
as the original bug.** Affects 234 of 1,365 BUY signals (17%). Options for later:

1. **Leave it** — SHORT never alerts. Honest, if 1.67:1 isn't worth taking.
2. **Widen SHORT's target** (5% → ~8%) — a real trading-parameter change, needs validation.
3. **Per-style R:R floors** — judge SHORT at ~1.6, its designed geometry. Most principled.

---

## FIXED after the initial writeup

All four were deployed durably (verified in the **image**, not just the container, and healthy
after `--force-recreate`).

### AUD-ENTRY-CONSECLOSS-DEADLOCKLOOP — HIGH — the only one actively costing money

A persisted Redis marker **keyed on the streak length**. Keying on the streak preserves the
escape the branch exists for: if the recovery entry also loses (4 → 5), one fresh attempt is
earned at the new level rather than a permanent lockout. Cleared on a **non-negative** close,
matching how `_consec_loss_streak` itself stops counting. Fails **open** on a Redis error — an
outage must not freeze a portfolio.

> **A correction to my own verification.** I first reported "0 `consecutive_loss_restart` events
> since deploy" as proof it worked. That was premature — **zero scan cycles had run**, because
> the market was closed. The 0 was meaningless. Real verification came from exercising the logic
> against portfolio 5's live streak of 10.

### AUD-EXIT-INDICATORSEMPTY — CRITICAL

Repointed at `signals.reasons->>'rsi'`, which `_monitor_positions` already reads twice in the
same function. Verified against production first: real values across **171 symbols**, and
**zero currently above 75** (max 71.9) — so this restores the gate's *capability* without an
immediate behaviour change, the right property for a control that closes live positions.

### AUD-ENTRY-BREAKOUTREF-DEONLY — HIGH — the wrong-path instance

Both DE consumers now prefer `breakout_ref`. Verified in the container: `breakout_ref=83` vs
live 100 is **+20.5% → REJECTS**, while a missing anchor computes −3.4% and passes, preserving
the old inert behaviour rather than becoming a new outage.

**Also fixed the test file that should have caught this.** `test_entry4_chase_parity.py` already
asserted `"roc_10" in DE_SRC` — the exactly-right lesson — and never applied it to
`breakout_ref`, in the same commit that fixed both. It now asserts both.

### AUD-ENTRY-CONFSIZEMULT-HKCONSTANT + AUD-ENTRY-SIZEEXCESS-STALEMINSCORE — MEDIUM

Both pushed risk capital **up** on the accounts measuring worst, compounding with the breaker
bug. Confidence bands now derive from the portfolio's own floor (HK discriminates at 72.2/43.3);
score excess now measures from DE's real returned floor.

> **Two things I got wrong and caught before shipping.** My first confidence fix used *rounded*
> ratios (1.235), giving 50.0175 at the US floor — silently demoting a US candidate at exactly
> 50.0 from 1.25× to 1.0×, precisely the side effect the fix claims to avoid. And threading
> `de_min_score` would have raised `NameError` on a **DE outage**, since both its assignments sit
> inside `if de_result is not None` arms.

---

## STILL OPEN — ranked

### AUD-EXIT-INDICATORSEMPTY — CRITICAL
Two exit gates read the `indicators` table: **0 rows, and no writer anywhere in the codebase**
(`Indicator` is defined in `models.py` and constructed nowhere). So `momentum_fade` (T207) and
PT-H5's RSI trail tightener are **unconditionally dead**, while `momentum_exit_enabled` defaults
to `True` so they read as working gates. Production: **0 `momentum_fade` exits** across 116
closed trades.

**The data isn't missing — it's in the wrong place.** `signals.reasons->>'rsi'` has **43,024
rows in 90 days, 388 with RSI > 75**, and `_monitor_positions` already reads `signals.reasons`
twice.

**Bounded:** I swept every table — only `indicators` and `stock_connect_flows` are truly empty,
and the latter is harmless (defined-but-unused model, no readers; the working HK flow gate reads
`signals.reasons->>'flow_5d_net_hkd'`). So this is two dead gates, not a systemic problem.

> **Methodology note:** my first sweep used `pg_stat_user_tables.n_live_tup` and returned **29**
> "empty" tables — including `users` and `price_alerts` (137 real rows). Stale autovacuum
> estimates. `COUNT(*)` is the answer.

### AUD-ENTRY-CONSECLOSS-DEADLOCKLOOP — HIGH
Found **independently by both** the entry and exit audits. The consecutive-loss circuit breaker
is permanently bypassed for any portfolio with **zero open positions**: `_consec_losses` is a
*local* recomputed from the DB every cycle, and nothing persists that a recovery grant was used,
so `_consec_losses = 0` re-grants on **every scan cycle, indefinitely**. The comment says "one
recovery entry"; nothing enforces it.

**Three portfolios are in that state right now.** Portfolio 5 entered **7 trades under this
branch and all 7 lost**, totalling **−$452.92** (DELL −$325.63, BRK-A −$58.59, and 5 more).
The breaker meant to *stop* trading during a losing streak licensed seven more losses.

### AUD-ENTRY-BREAKOUTREF-DEONLY — HIGH
See the pattern section. DE's extension guard computes a **constant −3.38%** against a +6%
threshold and cannot fire.

### The rest
`AUD-EXIT-HKENTRYDATE` (HIGH, latent — `entry_date` written UTC, `days_held` measured ET; 14 HK
trades store a date one day ahead, 0 US) · `AUD-CONVICTION-RSIDIV-NOWRITER` (MEDIUM — a
documented hard disqualifier reads a key nothing writes; the email renders "None detected" to
every user, a confidently false statement) · `AUD-ENTRY-CONFSIZEMULT-HKCONSTANT` and
`AUD-ENTRY-SIZEEXCESS-STALEMINSCORE` (MEDIUM — **both push risk capital *up* on the two
portfolios measuring worst**, compounding with the breaker bypass) · `AUD-EXIT-CORRSIGNMASK`
(MEDIUM) · `AUD-ENTRY-NYSEHOLIDAY-FOURTHCOPY` (LOW — a 4th holiday calendar hiding from the
coverage assertion added earlier the same day) · `AUD-EXIT-WEEKLYTZ`, `AUD-EXIT-CONDORDER-DRIFT`,
`AUD-CONVICTION-DEADSTOCHK`, `AUD-RESWEEP-UNSCHEDULED` (LOW/latent).

---

## CHECKED AND FOUND CLEAN — do not re-derive

**Stop machinery is genuinely well-built.** Monotonicity verified across **seven** independent
tighteners — `current_stop < stop_loss` returns **0 rows all-time**, `breakeven_stop` below entry
**0 rows**. Fills are within 0.8% of the stop (`stop_hit` −0.81% n=59, `breakeven_stop` −0.63%
n=33); the outliers are genuine gap-throughs, not late firing.

**The conviction gate is discriminating well.** Every layer fires at a real rate over 643
rejections: Uptrend 241, OBV 173, ADX ~320, Stoch 112, ML 122, MACD 74. `roc_10 >= 10` blocks
**241 of 1,365 (17.7%)**. None unreachable, none passing everything.

**Every config divergence from the 2026-09-07 audit is verified fixed and live in the
container** — SWING US `min_kscore=52.0`, HK `max_position_pct=0.07` / `risk_per_trade_pct=0.007`
(T222-F), `min_ta_score=0.65`, `max_entry_gap_pct=0.03`.

**The watchdog fix holds and is now correctly TIGHTENING** — SWING 0.78 vs bull base 0.72
(+0.06), GROWTH 0.72 vs 0.60 (+0.12).

**Also clean:** `min_ta_score` is the most binding gate (blocks 56% of SWING BUYs) · HK
Stock-Connect flow gate has real data and actively blocks (764 negative of 1,435) · dark-pool
gate sound with both bars live · gamma-unwind asymmetric thresholds empirically calibrated with
a genuine three-state `None`/`False`/`True` · scale consistency checked on both sides of every
comparison, no mismatch · `sizer.py` is preview-only as documented · no order-dependent
unreachability across 16 `_skip_tally` sites.

**Two things that LOOK broken and are NOT:** an `atr` variable that appears to leak across loop
iterations (traced all six guards — no `NameError` path, no stale-value path exists), and
`weekly_confidence`'s `.get(..., 1.0)` fail-open default (the no-data path short-circuits earlier
on `weekly_rsi is None`, so it never reaches the blend).

---

## The through-line

**Not one of the twelve findings is a crash.** Every one is a silent wrong answer — a gate that
cannot fire, a table nobody writes, a breaker whose escape hatch swallowed the breaker, a fix on
the wrong path. The critical one took down the platform's primary user-facing output for four
days while every job reported `ok`.


---

## Detail relocated from CLAUDE.md's index (2026-09-10)

**T382-CLAUDEMD-REINDEX.** The lines below lived in `.claude/CLAUDE.md`'s Topic File Index,
which is read at the start of EVERY session and re-paid on every prompt-cache rebuild. They
were verified to be **new content, not duplicates** of this file — a sampled check found only
1-2 of 6 claims from each oversized index entry already present here — so they are moved rather
than deleted, and the index keeps a short pointer.

Preserved verbatim. Formatting is unchanged from the index entry, including its emphasis, so
nothing is lost to a reflow.

**ALL-GATES audit (2026-09-08): 12 findings, 6 FIXED (tier 368), run as 3 parallel audits (entry / exit+risk / signal+alert) over 158 gate constants in 14 files.** **READ THE PATTERN SECTION FIRST — it is more valuable than any single finding.** THREE findings in one session were the SAME failure: **a fix that lands on the SHADOW path, not the deciding path.** `grep breakout_ref services/decision-engine/` → 0; `grep expected_move services/decision-engine/` → 0. It keeps happening because `paper_trading_engine._should_enter()` is the shadow-logged FALLBACK (`decision_engine_mode` defaults to `"primary"`) yet is the more readable, better-tested, more obvious place to fix — and a fix there produces plausible shadow logs while changing no real decision. **THE CHECK: after fixing any gate, grep the fixed symbol under `services/decision-engine/` and confirm it appears.** **FIXED — `AUD-SIGALERT-RRUNREACHABLE` (CRITICAL): BUY signal alerts were COMPLETELY DEAD for 4 days** (72h: 123 conviction passes, **0** DE-gate passes; WAIT/HOLD/SELL last sent 2026-09-08 vs BUY 2026-09-04) because a hardcoded 2.0 R:R target pinned `rr_ratio ≤ 2.00` while `calibrate_min_rr_ratio` raised the floor to 2.25 — two correct mechanisms, jointly lethal. **Scope corrected by my own test: GROWTH+LONG fully unblocked (64%), SWING only at low ATR, SHORT never** — I had claimed 83% from a single ATR spot-check; on fixed-stop geometry every style caps at ≤2.00 and ~62% of BUYs sit where that binds. **SHORT deferred by user decision** — its 3%/5% params cap R:R at 1.67 BY DESIGN, so blocking it is now CORRECT; forcing it would fabricate an unsupported target. **ALL SIX FIXED AND DEPLOYED (verified in the IMAGE, healthy after --force-recreate):** the R:R gate; `AUD-EXIT-INDICATORSEMPTY` (two exit gates read the `indicators` table — **0 rows, no writer anywhere**; `momentum_fade` had never fired once across 116 closed trades — repointed at `signals.reasons->>'rsi'`, 43,024 rows, and verified 0 symbols currently above 75 so it restores CAPABILITY without an immediate behaviour change); `AUD-ENTRY-CONSECLOSS-DEADLOCKLOOP` (the breaker's escape hatch swallowed the breaker — a LOCAL `_consec_losses = 0` with nothing persisted, so it re-granted every 5-min scan forever; **portfolio 5 took 7 trades under it and ALL 7 LOST, −$452.92**; fixed with a Redis marker keyed on the STREAK LENGTH so a worse streak still earns one fresh attempt, cleared on a non-negative close, fails OPEN on Redis error); `AUD-ENTRY-BREAKOUTREF-DEONLY`; and two sizing multipliers that both raised risk on the worst-performing accounts (`AUD-ENTRY-CONFSIZEMULT-HKCONSTANT` — HK's 58.5 floor made `>= 50` always true, collapsing a 3-way discriminator into a constant +25% upsize on the market T222-F exists to de-risk, 17 of 19 real HK entries at 1.25x; and `AUD-ENTRY-SIZEEXCESS-STALEMINSCORE`). **STILL OPEN:** `AUD-EXIT-HKENTRYDATE` (HIGH but LATENT — `entry_date` written UTC while `days_held` is measured ET, so 14 HK trades store a date one day ahead and every day-counting exit gate is one day late for HK only; no HK trade has yet reached a threshold), `AUD-CONVICTION-RSIDIV-NOWRITER` (MEDIUM — a documented hard disqualifier reads a key nothing writes, and the alert email renders "None detected" to every user, a confidently false statement), plus 4 LOW. **THREE OF MY OWN ERRORS, caught and worth knowing:** I reported "0 restarts since deploy" as proof the breaker fix worked when **zero scan cycles had run** (market closed — the 0 was meaningless); my first confidence-band fix used ROUNDED ratios (1.235) giving 50.0175 at the US floor, silently demoting a US candidate at exactly 50.0 — the precise side effect the fix claimed to avoid, caught by a boundary test; and threading `de_min_score` would have raised **NameError on a DE outage**, since both its assignments sit inside `if de_result is not None` arms. **METHODOLOGY:** `pg_stat_user_tables.n_live_tup` is a STALE ESTIMATE, not a count — a sweep using it called 29 tables empty including `price_alerts` (137 real rows). Only `COUNT(*)` answers "is this table empty", which mattered because an empty table WAS the CRITICAL finding. **CLEAN — do not re-derive:** stop machinery (monotonic across 7 tighteners, `current_stop < stop_loss` = 0 rows all-time, fills within 0.8%), the conviction gate (every layer fires; `roc_10` blocks 17.7%), all 2026-09-07 config divergences verified fixed and live, the watchdog now correctly TIGHTENING, and two things that look broken and are not (an `atr` that appears to leak across iterations; `weekly_confidence`'s 1.0 default, which the `weekly_rsi is None` short-circuit prevents from ever applying).
