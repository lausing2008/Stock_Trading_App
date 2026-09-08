# WEEKLY SYSTEM REVIEW — RECURRING PROMPT

**Purpose:** a fast, bounded, weekly health-and-drift check. **This is deliberately NOT a
re-run of the full audit** (`AI Stock Trading Platform — Independent Trading Audit Prompt
(REVISED 2026-09-04).md`). See §0 for why that distinction matters.

**Expected effort:** 30–60 minutes. If it is taking hours, something is wrong with the scope —
stop and re-read §0.

---

# 0. WHY THIS IS NOT THE FULL AUDIT

The full audit is a deep, multi-week, one-time (or quarterly) exercise. Running it weekly would
actively harm the system, for three reasons:

1. **The data barely moves.** At the current rate (~10 closed trades/week), re-running
   expectancy analysis on 116 → 126 trades measures noise, not improvement. The full audit's own
   unblock triggers (300 closed trades, two distinct regimes) are quarters away, not weeks.

2. **You would re-derive the same conclusions.** The expensive part — reading the codebase,
   mapping signal paths, tracing point-in-time correctness — does not change week to week.

3. **Weekly re-slicing of a near-static dataset is a multiple-testing machine.** Examine 116
   trades 52 different ways and you *will* find spurious "findings," then act on them. This is
   precisely the data-snooping failure the full audit's §15 warns against. Institutionalising it
   weekly would be worse than not auditing at all.

**So this weekly review asks a different question.** The full audit asks *"does this system have
an edge?"* This asks:

> **Is the system running as designed, and has anything drifted, broken, or silently stopped?**

That question genuinely changes week to week. The edge question does not.

---

# 1. LIVENESS — IS EVERYTHING ACTUALLY RUNNING?

*This section catches the failure class that produced the most real damage historically: things
that silently stop, with no error and no visible symptom.*

## 1.1 Scheduler job liveness

```bash
# Every job's real last_run — confirm each is advancing at its declared cadence.
# A 1-minute job whose last_run is 20 minutes stale is BROKEN, not slow.
for j in check_price_alerts check_signal_alerts check_volume_anomalies \
         check_conditional_orders check_short_squeeze_alerts check_squeeze_ignition_alerts \
         check_squeeze_watch_reverts check_options_flow_alerts check_dark_pool_alerts \
         check_sr_watch_reverts check_value_area_breakdown portfolio_drawdown_alert_check \
         check_early_earnings_news_alerts check_top3_conviction; do
  echo -n "$j: "
  docker exec stockai-redis-1 redis-cli get "scheduler:job:$j"
done
```

**Escalate if:** any 1-minute job's `last_run` is >5 minutes stale, or `status` is `error`.

**Known precedent:** three 1-minute jobs (options-flow, dark-pool, S/R watch) silently stopped
firing after their first post-restart execution — APScheduler's 1-second default
`misfire_grace_time` vs. their real ~15s execution time. Fixed 2026-09-04, but the *class*
recurs.

## 1.2 Data-quality check results

```bash
docker exec stockai-redis-1 redis-cli --scan --pattern 'dq_check:*' | sort | while read k; do
  echo -n "$k: "; docker exec stockai-redis-1 redis-cli get "$k"
done
```

**Escalate if:** any check reports `ok: false`, or a check that previously existed is now absent.

## 1.3 Table freshness

```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
SELECT 'signals' t, MAX(ts)::date d FROM signals
UNION ALL SELECT 'rankings', MAX(as_of) FROM rankings
UNION ALL SELECT 'signal_outcomes', MAX(ts_evaluated)::date FROM signal_outcomes
UNION ALL SELECT 'paper_trades', MAX(entry_date) FROM paper_trades
UNION ALL SELECT 'paper_equity_curve', MAX(date) FROM paper_equity_curve
UNION ALL SELECT 'options_game_plan_snapshots', MAX(as_of) FROM options_game_plan_snapshots
UNION ALL SELECT 'options_flow_snapshots', MAX(as_of) FROM options_flow_snapshots
UNION ALL SELECT 'gex_snapshots', MAX(as_of) FROM gex_snapshots
ORDER BY 1;"
```

**Escalate if:** any table's newest row is materially older than its own job's cadence.

**Known precedent:** `options_game_plan_snapshots` had **zero rows, ever** — its EOD job had
never once succeeded — while its two sibling EOD jobs ran fine daily. Nothing surfaced this
until a user asked why a feature was empty.

## 1.4 Portfolio activity

```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
SELECT pp.id, pp.name, pp.config->>'trading_style' style, pp.config->>'market' market,
       COUNT(*) FILTER (WHERE pt.entry_date >= CURRENT_DATE - 7) entries_7d,
       MAX(pt.entry_date) last_entry
FROM paper_portfolios pp LEFT JOIN paper_trades pt ON pt.portfolio_id = pp.id
WHERE pp.is_active GROUP BY 1,2,3,4 ORDER BY 1;"
```

**Escalate if:** any active portfolio has zero entries for >14 days. **A dormant portfolio is a
defect until proven otherwise** — do not assume "no good setups this week."

**Known precedent:** two HK portfolios were dormant **2+ months** because a self-calibrated R:R
floor, derived almost entirely from US trade volume, was applied market-blind to HK — above what
HK's own stop/target parameters could structurally reach. Nothing alerted; it looked like
ordinary selectivity.

## 1.5 User-visible feature spot-check

Pick **one** feature per week (rotate) and **actually exercise its API end-to-end** with a real
token and a real symbol — do not merely read the code or glance at the UI.

**Known precedent:** `GET /options-game-plan/batch` had a wrong relative import and **500'd on
every request since it shipped**. Invisible to code review, invisible to its own 21-test suite,
and invisible in the UI (a failed fetch renders as empty).

## 1.6 Container and infrastructure health

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}' | sort
df -h /
docker system df
```

**Escalate if:** any container unhealthy/restarting, disk >70%, or reclaimable space >20GB.

Also confirm `shared/` is current across containers — a stale `shared/db/` on 9 of 10 containers
was found live and would have crash-looped each on its next restart:

```bash
for c in $(docker ps --format '{{.Names}}' | grep stockai | grep -v postgres | grep -v redis | grep -v frontend); do
  echo -n "$c: "; docker exec "$c" grep -c 'class UserTier' /app/shared/db/models.py 2>&1
done
```

---

# 2. DRIFT — HAS ANYTHING CHANGED THAT SHOULDN'T HAVE?

## 2.1 Self-tuning changes made this week

```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
SELECT parameter_name, style, market, old_value, new_value, promoted,
       validation_ev_pct, baseline_validation_ev_pct, validation_n, triggered_by, created_at::date
FROM tune_history WHERE created_at >= CURRENT_DATE - 7 ORDER BY created_at DESC;"
```

For each promoted change, ask:
- Does the parameter move in a **direction that makes sense**, or did it drift somewhere extreme?
- Is `validation_n` large enough to trust?
- Is the value now **market-blind when it shouldn't be**, or vice versa?

**Known precedent:** `regime_min_rr_ratio` was auto-calibrated to 3.38 from a curve whose sample
collapsed to 5-6 trades at the winning threshold — and was then applied to a market that could
never reach it. **The tuning machinery was working exactly as designed; the design had a blind
spot.** Auto-tuning does not remove the need to eyeball what it decided.

## 2.2 Live config vs. defaults

```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
SELECT id, name, config->>'min_confidence' min_conf, config->>'min_kscore' min_ks,
       config->>'min_entry_score' min_score, config->>'min_rr_ratio' min_rr,
       config->>'regime_state' regime
FROM paper_portfolios WHERE is_active ORDER BY id;"
```

**Escalate if:** any threshold sits far below its documented default with no recorded reason.

**Known precedent:** four production portfolios were found running `min_confidence=15` /
`min_entry_score=3` — far below real defaults — silently admitting low-conviction trades for an
extended period.

## 2.3 Unusual Whales usage

```bash
docker exec stockai-redis-1 redis-cli get stockai:metric:uw_rate_limit_count_48h
docker exec stockai-redis-1 redis-cli get dq_check:uw_rate_limit_events_48h
```

**Escalate if:** rate-limit events are sustained rather than occasional bursts.

---

# 3. OUTCOMES — WHAT ACTUALLY HAPPENED

**Report only. Do NOT tune from one week of data.** See §4.

## 3.1 This week's closed trades

```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
SELECT portfolio_id, symbol, entry_date, exit_time::date, exit_reason,
       ROUND(pct_return::numeric,2) pct, ROUND(pnl::numeric,2) pnl,
       ROUND(confidence_at_entry::numeric,1) conf, ROUND(rr_ratio_at_entry::numeric,2) rr
FROM paper_trades WHERE stage='closed' AND exit_time >= CURRENT_DATE - 7
ORDER BY pnl;"
```

## 3.2 Any single trade worse than −8%

For each, answer three questions:
1. Did it exit at its **designed** stop, or did something fail?
2. Was the **entry** defensible given what was knowable at the time?
3. Is this a **recurring pattern** or a one-off?

Only a *pattern* justifies a change. A single bad trade almost never does.

**Worked example — how to reason about a large loss:**
- *AXON −12.4%*: exited exactly at its designed 12% GROWTH stop; `highest_price` was only +1.3%
  above entry, so trailing/breakeven never armed. **Working as designed. No action.**
- *SNOW −19.0%*: entered right after a ~23% overnight 8-K spike that the gap filter couldn't see,
  because the signal's own reference price was already post-gap. **A real, fixable defect.**

Same magnitude of loss; completely different conclusions. Make that distinction every time.

## 3.3 Portfolio equity

```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
SELECT portfolio_id, MAX(date) d, MAX(equity) FILTER (WHERE date=(SELECT MAX(date) FROM paper_equity_curve)) latest
FROM paper_equity_curve GROUP BY 1 ORDER BY 1;"
```

Note direction and magnitude. **Do not annualise. Do not compute Sharpe.** One week of equity
curve in a single regime supports neither.

---

# 4. THE WEEKLY DISCIPLINE RULES

These exist to stop this review from becoming the thing it's meant to prevent.

**4.1 — Fix breakage. Do not tune performance.**
Liveness bugs (§1) and unexplained drift (§2) → fix immediately.
Performance observations (§3) → **record only**. Strategy/threshold changes belong to the
quarterly deep audit, where sample size can support them.

**4.2 — One week of trades is never enough to justify a threshold change.**
At ~10 trades/week, any week-over-week change in win rate is noise. If a change feels urgent,
that urgency is the strongest signal it's noise.

**4.3 — Log observations; act on patterns.**
Keep a running observations file. When the same observation appears in **4+ consecutive weeks**,
promote it to a real investigation. Anything appearing once is noise.

**4.4 — `UNMEASURABLE` is a valid weekly finding.**
If a week's data can't answer something, say so. Do not substitute a plausible number.

**4.5 — Never remove a safety mechanism to improve apparent performance.**

---

# 5. OUTPUT

Append one dated entry to `docs/weekly-reviews/YYYY-MM-DD.md`. Keep it short:

```markdown
## Weekly Review — YYYY-MM-DD

### Liveness
- [ ] All scheduler jobs advancing        — PASS / FAIL (detail)
- [ ] All DQ checks green                 — PASS / FAIL
- [ ] All tables fresh                    — PASS / FAIL
- [ ] All active portfolios trading       — PASS / FAIL
- [ ] Feature spot-check (which): ______  — PASS / FAIL
- [ ] Containers/disk healthy             — PASS / FAIL

### Drift
- Tuning changes this week: (list, with a one-line sanity verdict each)
- Config anomalies: (or "none")

### Outcomes (record only — no action)
- Closed trades: N, W/L, notable losses
- Equity direction per portfolio

### Actions taken
- (Breakage fixes only. If a performance change was made, justify why it couldn't wait
   for the quarterly audit.)

### Observations log
- (Recurring items + week-count. Promote at 4+ consecutive weeks.)
```

---

# 6. WHEN TO RUN THE FULL AUDIT INSTEAD

Trigger a full re-run of `AI Stock Trading Platform — Independent Trading Audit Prompt (REVISED)`
when **any** of these is true:

- **Quarterly**, on schedule (default cadence).
- Closed paper trades cross **300** (unblocks §F.1 portfolio stats, §F.4 Monte Carlo).
- A **second market regime** accumulates ≥300 signal outcomes (unblocks §F.2 regime matrix — as
  of 2026-09-04, `bear` had exactly **1** row, so this is the binding constraint on most
  performance conclusions).
- The **walk-forward harness** is built (§F.3).
- **Execution instrumentation** (bid/ask, MFE, MAE) ships (§F.5) — unblocks real slippage analysis.
- An observation hits **4+ consecutive weeks** in the log.
- Any **structural change**: new data provider, new strategy family, new market.
