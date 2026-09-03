## Watchlist Style Assignment — Untagged Sector Watchlists Made Tradeable (2026-09-03)

### The gap this closes

`Watchlist.trading_style` (`GROWTH|SWING|SHORT|LONG`, nullable) gates which stocks a
paper-trading portfolio's `_scan_for_entries()` will ever consider — the candidate query joins
`Watchlist.trading_style == style` (`services/market-data/src/services/paper_trading_engine.py`,
~line 4483-4537). A watchlist with `trading_style IS NULL` can never match any portfolio's scan,
regardless of the model's own stale comment claiming `None=global`. Found live during the
Paper Trading deep audit (Domain 3 of the 6-part platform audit series,
`docs/audits/2026-09-03-six-part-platform-audit-3-paper-trading.md`) while investigating why
P2 (HK SWING) had traded only 4 times in 2+ months: only 6 HK stocks total existed across any
`trading_style='SWING'` watchlist. Broadening the investigation surfaced a bigger version of the
same gap — 8 sector-themed watchlists (AI & AGI, Semiconductors, Cloud & Cybersecurity, HK Tech
& Consumer, Fintech & Financial, Aerospace & Industrials, plus 2 non-sector lists) held 64
distinct stocks (21 of them HK) with **no `trading_style` at all**, invisible to every portfolio,
not just SWING.

### Why "just copy stocks into every watchlist" isn't the right frame

`GROWTH`/`SWING`/`LONG`/`SHORT` are not labels on the same signal — each is an independently
computed `Signal` row per stock, generated with genuinely different parameters
(`_STYLE_PROFILES`, `services/signal-engine/src/generators/signals.py:1576` — different
`buy_threshold`, `ml_weight_cap`, `adx_min` per style; e.g. `adx_min` 27/15/12/None for
SHORT/SWING/GROWTH/LONG respectively). Signal generation itself is NOT watchlist-scoped — a
signal is computed for every active stock under every horizon regardless of which watchlist (if
any) it sits on. This means:
- It's always safe to add a stock to more than one style-tagged watchlist — the signal-engine
  computes a real, appropriately-parameterized signal for each style independently; there is no
  risk of "trading a GROWTH pick under SWING rules" by mistake.
- But it also means **which style actually fits a given stock is a real empirical question**,
  not something to guess from a sector label — some stocks probably do genuinely suit one style's
  parameters (trend strength, holding period) better than another's.

### Methodology used (Pass 1 — win-rate classification)

1. Pulled each stock's real historical BUY-signal win rate per horizon from `signal_outcomes`
   (`signal_direction='BUY'`, grouped by `stock_id, horizon`) — this data already existed for
   every stock regardless of watchlist membership.
2. **Rejected raw win rate as the ranking metric** after finding each horizon has a
   substantially different BASELINE win rate even before conditioning on any stock (SHORT
   41.3%/n=3259, SWING 38.4%/n=3052, LONG 46.2%/n=2231, GROWTH 38.9%/n=3472) — driven mostly by
   LONG's much longer average hold window (29.5 days vs. SHORT's 8.5), not genuine per-stock
   fit. An early attempt using raw win rate produced a lopsided, likely-misleading result (LONG
   "winning" ~40% of stocks at 80-100% win rates) purely from this base-rate confound.
3. **Corrected metric**: `outperformance = stock's own win_rate_under_style − that style's own
   baseline win_rate`. Best style = highest outperformance, restricted to horizons where the
   stock has ≥15 of its own BUY signals (a floor chosen to keep individual small samples from
   dominating the pick).
4. For stocks below that 15-signal floor in every horizon (8 of 64), fell back to the majority
   best-style among the OTHER, well-sampled stocks sharing the same original sector watchlist,
   rather than trusting a noisy individual number.
5. Published the full assignment (all 64 stocks, win rate, sample size, outperformance, and an
   explicit `WEAK` flag on the 24 stocks whose "best" style still had negative outperformance —
   i.e., the least-bad of four imperfect options, not a real edge) as a reviewable artifact
   before writing anything to the database. User approved as-is.
6. Applied via 64 idempotent `INSERT ... WHERE NOT EXISTS` statements adding each stock to ONE of
   the 4 primary style watchlists (`183 Growth / Momentum`, `181 Swing Trade`, `180 Short Term`,
   `182 Long Term`) — purely additive, no existing watchlist membership removed or changed.
   Counts after Pass 1: SHORT 180 (12→44), SWING 181 (49→55), LONG 182 (25→33), GROWTH 183
   (58→66). Counts after Pass 2's 6 additional reassignments (below): SHORT 180 (44→45), SWING
   181 (55→59), LONG 182 (33→34), GROWTH 183 (66→66, unchanged). HK-tagged SWING coverage grew
   from 6 to 8 (Pass 1) to 10 (Pass 2's 6082.HK and 9903.HK additions) — a real but still modest
   gain, not a full fix of the original P2 breadth gap (most of the 21 untagged HK stocks
   classified to a non-SWING style either way).

### Methodology used (Pass 2 — backtest cross-check)

Win-rate-from-`signal_outcomes` (Pass 1) counts every historically-fired BUY signal as if it
were tradeable, with no portfolio-level admission filter. `GET /paper-portfolio/backtest/
portfolio` (T230-BACKTESTING-MULTISYMBOL, already-built MVP —
`docs/features/self-tuning-walk-forward-harness.md` §T230) instead day-steps a real simulated
single-symbol position book using the SAME `SignalOutcome` ground truth, giving win_rate/
Sharpe/avg_return/max_drawdown under real position-sizing math. Re-ran each of the 64 stocks
under each of the 4 styles (`window_days=180`, single-symbol calls — a single symbol has no
`max_positions`/sector-cap contention, so every signal in the window is entered, giving a clean
per-stock-per-style read) as a second, independent opinion on Pass 1's classification.

**Real result (2026-09-03, 256 calls, 0 errors)**: only 31 of 64 (48%) agreed with Pass 1's
win-rate classification. This is a genuinely low agreement rate, and it needed to be reported
honestly rather than silently accepted or silently used to trigger a full re-classification —
most of the 33 disagreements involve very small samples (`n_entered` 3-6 trades) on one or both
sides of the comparison, i.e. noise on either side, not one method being clearly right.
Restricting to disagreements where BOTH Pass 1's own-data win rate AND Pass 2's backtest
`n_entered` were reasonably sized (≥8-10) narrowed this to exactly **6 stocks worth acting on**:

| Symbol | Pass 1 assigned | Pass 2 preferred | Win-rate gap |
|---|---|---|---|
| SOFI | SHORT (25%, n=12) | SWING (56%, n=9) | +31pp |
| 1347.HK | GROWTH (30%, n=10) | LONG (50%, n=8) | +20pp |
| INTC | SHORT (7%, n=14) | SWING (25%, n=8) | +18pp |
| 9903.HK | SHORT (20%, n=15) | SWING (33%, n=12) | +13pp |
| CM | GROWTH (70%, n=10) | SHORT (82%, n=11) | +12pp |
| 6082.HK | SHORT (33%, n=9) | SWING (38%, n=8) | +4pp |

All 6 were additively reassigned to their Pass-2-preferred style (still additive-only — none
removed from their Pass-1 placement). The other 58 stocks were left as Pass 1 assigned them.
**This low agreement rate is itself the headline finding of Pass 2**, not a footnote — it means
the two methods (raw historical win rate over all fired signals vs. a real position-sized
backtest simulation) are genuinely measuring different things for many of these stocks, and
neither should be treated as fully authoritative on a small sample. A future re-run with more
accumulated `signal_outcomes` data should re-check whether the agreement rate improves as
sample sizes grow.

### What to check if this needs re-running later

The user has asked for this to become a repeatable exercise (re-run periodically as more
`signal_outcomes` data accumulates, and/or extended to re-validate the ORIGINAL 4 style
watchlists' existing members, not just the 64 newly-tagged ones). Re-run steps:

```bash
# 1. Find current untagged watchlists (repeat Pass 1 step 1's grounding query):
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
   \"SELECT id, name, trading_style FROM watchlists WHERE trading_style IS NULL OR trading_style='';\""

# 2. Re-pull each horizon's CURRENT baseline win rate (changes as more outcomes accumulate):
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
   \"SELECT horizon, COUNT(*) FILTER (WHERE signal_direction='BUY') as buy_n, \
     ROUND(100.0*COUNT(*) FILTER (WHERE signal_direction='BUY' AND is_correct)/NULLIF(COUNT(*) FILTER (WHERE signal_direction='BUY'),0),1) as baseline_win_pct \
     FROM signal_outcomes GROUP BY horizon ORDER BY horizon;\""

# 3. Re-pull each stock's per-horizon win rate (Pass 1 step 1), recompute outperformance against
#    the FRESH baselines from step 2 — never reuse the 2026-09-03 baseline numbers hardcoded above.

# 4. Cross-check via the real backtest endpoint (Pass 2) — one call per stock per candidate style:
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-market-data-1 curl -s \
   'http://localhost:8001/paper-portfolio/backtest/portfolio?symbols=<SYM>&style=<STYLE>&market=<US|HK>&window_days=180' \
   -H 'Authorization: Bearer <admin token>'"

# 5. Build a review artifact (win rate, sample size, outperformance, WEAK/fallback flags) and
#    get explicit approval BEFORE writing any INSERT — never auto-apply a re-classification.
```

**Known limitations of this method, still true on any re-run**: (1) the 15-signal floor is a
judgment call, not a statistically rigorous confidence threshold — no real confidence intervals
are computed; (2) `signal_outcomes`-based win rate and `backtest/portfolio`-based win rate can
disagree materially for the same stock/style (observed live, e.g. 0700.HK/GROWTH: 25.0% via
Pass 1's raw outcome count over a longer window vs. 57.1% via Pass 2's real position-sized
180-day backtest) — the two methods are answering related but not identical questions (every
historical BUY signal vs. an admission-filtered simulated portfolio), and a real disagreement
between them should be treated as "look more closely at this specific stock," not silently
averaged away.

### Related, already-existing config-tuning infrastructure (do not re-build)

Separately from stock→style ASSIGNMENT (this doc), per-style GATE PARAMETER tuning already
exists and runs automatically — `POST /signals/tune_strategy` (T255-STRATEGY-TUNER-PER-HORIZON,
`docs/features/self-tuning-walk-forward-harness.md` §T255) grid-searches `buy_threshold` ×
`ml_weight_cap` jointly per style (SHORT/SWING/LONG/GROWTH), walk-forward validated (70/30
chronological split, promotes only if the winning combination ALSO beats the current live
baseline on held-out data), scheduled weekly (`_weekly_full_refresh()`, Sunday 14:00 PT). A
`GET /signals/tune_status` endpoint already reports the effective/overridden value per horizon.
No new tuning mechanism is needed for "how to tune the config for each style" — it already runs;
check `tune_status` and `TuneHistory` rows (`parameter_class='joint_strategy'`) before assuming
this needs building.
