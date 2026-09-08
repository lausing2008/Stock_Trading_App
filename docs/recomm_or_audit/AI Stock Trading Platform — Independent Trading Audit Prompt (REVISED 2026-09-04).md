# AI STOCK TRADING PLATFORM
# INDEPENDENT QUANTITATIVE TRADING AUDIT — REVISED

**Revision date:** 2026-09-04
**Supersedes:** `AI Stock Trading Platform — Opus-Fable 5 Independent Trading Audit Prompt.md`
**Why revised:** the original prompt's *philosophy* is sound and is preserved almost entirely.
What changed is **scope calibration against the data that actually exists**. The original asked
for per-sector × per-regime expectancy, Sharpe/Sortino/Calmar per signal, 1,000-path Monte
Carlo, and walk-forward out-of-sample validation. Measured directly against production on
2026-09-04, several of those are not answerable — not because the work is hard, but because the
underlying sample does not exist. Running them anyway would produce numbers that *look*
rigorous and are actually noise, which is the exact failure mode the original §30 ("do not tell
me what I want to hear") exists to prevent.

Everything the original asked for is still here. Items that cannot currently be answered have
been moved to **§F — Future Work (Blocked on Data or Unbuilt Features)** with an explicit
unblock trigger, rather than silently dropped.

---

# 0. GROUND TRUTH — MEASURED, NOT ASSUMED

Every scope decision below follows from these figures, queried directly against production on
2026-09-04. Any future re-run of this audit should **re-measure these first**, because several
phase gates below are defined as thresholds on them.

| Dataset | Rows | Notes |
|---|---|---|
| `signal_outcomes` (resolved) | **16,732** | The large, statistically usable dataset. Carries `confidence`, `fused_prob`, `ta_score`, `ml_prob`, `ml_auc`, `market_regime`, and forward returns at 5d/10d. |
| `paper_trades` (closed) | **116** | Real simulated executions. First entry 2026-06-16 (~2.5 months). |
| `paper_trades` (open) | 8 | |
| `options_flow_alert_outcomes` | 1,511 | |
| `squeeze_alert_outcomes` | 317 | |
| `dark_pool_alert_outcomes` | 144 | |

**Regime distribution of `signal_outcomes` — the single most important constraint in this audit:**

| Regime | Rows | Share |
|---|---|---|
| bull | 16,277 | **97.3%** |
| choppy | 310 | 1.9% |
| unknown | 144 | 0.9% |
| bear | **1** | 0.006% |

**Read that carefully.** The platform has essentially never operated through a bear market. This
is not a "small sample" problem that more patience fixes — it is a *structural* absence. Any
claim this audit makes about regime robustness, drawdown behaviour in a downturn, or
bear-market strategy selection would be **fabricated**. The audit must say so plainly rather
than producing a regime matrix with one row of real bear data in it.

Signal outcomes by horizon/direction (all have workable sample):

| Horizon | BUY | SELL |
|---|---|---|
| GROWTH | 3,626 | 886 |
| SHORT | 3,416 | 1,052 |
| SWING | 3,219 | 1,089 |
| LONG | 2,328 | 1,116 |

---

# 0.1 THE CENTRAL DISTINCTION THIS AUDIT MUST MAINTAIN

The original prompt conflated two different questions. Keep them separate throughout:

**Question 1 — Do the SIGNALS predict returns?**
Answerable now. n=16,732, with the feature columns needed for attribution. This is where the
real analytical work should go.

**Question 2 — Does the SYSTEM make money when it trades?**
Barely answerable. n=116 closed trades, concentrated in one regime, over 2.5 months. Report
directionally, with explicit error bars, and refuse to compute ratios that need more data.

A finding of the form *"signals have edge but execution destroys it"* — or its inverse — is one
of the most valuable outcomes this audit could produce. Structure the work so that distinction
can actually emerge.

---

# 0.2 A CATEGORY THE ORIGINAL PROMPT MISSED ENTIRELY

The original has no section asking **"is the system actually running?"** It assumes the platform
executes as designed and asks only whether the design is profitable.

That assumption does not hold here. Confirmed in production on 2026-09-04 alone:

- Two HK paper portfolios had generated **zero trades for 2+ months** (one since 2026-06-25) —
  a self-calibrated R:R floor derived almost entirely from US trade volume was applied
  market-blind to HK, above what HK's own stop/target parameters could structurally reach.
- Three 1-minute scheduler jobs (options-flow alert, dark-pool alert, S/R watch) had **silently
  stopped firing** after their first execution post-restart, with no error and no visible symptom.
- `GET /options-game-plan/batch` had a wrong relative import and had **500'd on every request
  since it shipped** — the Options Game Plan screener column and email section had never once
  returned real data.
- The Options Game Plan EOD batch job had **never successfully written a row**, ever.

None of these appear in an expectancy calculation. They appear as *absence* — as trades that
never happened and features that silently returned nothing. **§7 (Liveness & Completeness
Audit) is new and is a first-class phase**, not an afterthought.

---

# PHASE A — DATA INTEGRITY & POINT-IN-TIME CORRECTNESS

*Preserves original §1, §2, §27. Fully executable now — this is a code-correctness question,
not a statistical one, so sample size is irrelevant.*

## A.1 System inspection

Inspect the repository before changing anything. Understand: architecture, database schema,
market-data pipeline, yfinance integration, Unusual Whales integration, signal engine, alert
engine, AI prompts, technical indicators, fundamental analysis, options analysis, paper-trading
engine, order simulation, position sizing, stop-loss logic, take-profit logic, portfolio
calculations, performance calculations, backtesting, frontend, APIs, scheduled jobs, caching,
and data timestamps.

Produce an audit report. Do not rewrite anything yet.

## A.2 Point-in-time correctness

Determine whether the system decides using data that was genuinely available at decision time.

Detect: look-ahead bias, survivorship bias, data leakage, future information, delayed data,
incorrect timestamps, incorrect timezone conversion, market-hours errors, earnings information
appearing before actual release, options-data timestamp problems, stale yfinance data, duplicate
records, missing data, incorrect adjusted prices, corporate-action problems, incorrect splits,
incorrect dividends, delayed fundamental data, delayed options flow, API failures.

For every signal path, establish: `SIGNAL_TIME`, `DATA_AVAILABLE_TIME`, `DATA_SOURCE`,
`DATA_TIMESTAMP`, `EXECUTION_TIME`. Flag any case where information post-dates the decision.

**Known live instance of this exact class, use as a template for what to hunt:** signal-engine
writes `reasons["last_price"]` at signal-compute time. For a stock that gapped overnight on an
8-K, that value is *already post-gap* — so the paper-trading engine's own gap filter, which
compares live price against it, measured a ~0% gap and let the entry through. Confirmed on a
real SNOW trade (entry $377.34 vs `last_price` $377.995, despite a real ~23% overnight spike);
the position then mean-reverted to a -19.0% stop-out. **The bug was not a wrong number — it was
a reference point that silently encoded future information relative to the event it was meant
to detect.** Assume there are more of these.

## A.3 Data provider audit

*(original §27)* Current providers: yfinance, Unusual Whales. Determine where delays or quality
limits could materially hurt trading. Identify where an additional provider would measurably
improve things — SEC EDGAR, FMP, Finnhub, Polygon, Benzinga, Estimize, FRED are candidates. Do
not recommend paid sources unless expected improvement justifies cost.

Note: a prior review already concluded UW **Basic** tier is sufficient and Advanced is not worth
it. Re-open that only with new evidence, and see §B.4 for the open question on real UW request
volume.

**Deliverable:** `DATA_QUALITY_AUDIT.md`

---

# PHASE B — SIGNAL EDGE ATTRIBUTION

*Preserves original §3, §4, §7, §8, §26 — scoped to `signal_outcomes` (n=16,732), which is
genuinely large enough to support this.*

## B.1 Signal inventory

*(original §3)* Complete inventory. For each: name, strategy, indicators, data sources, entry
condition, exit condition, stop loss, take profit, position sizing, time horizon, confidence,
AI score, market regime, sector, long/short, stock/option.

## B.2 Expectancy over win rate

*(original §4 — preserved in full, this framing is correct and important)*

A 70% win rate does not by itself mean a strategy is good. Compute win rate, loss rate, average
winner, average loser, expectancy, profit factor, payoff ratio, and trade count.

```
EXPECTANCY = (Win Rate × Average Win) − (Loss Rate × Average Loss)
```

**Scope constraint:** compute these on `signal_outcomes` (n=16,732), where they are meaningful.
Do **not** compute Sharpe / Sortino / Calmar / CAGR / max-drawdown per *signal* — those are
portfolio-path statistics requiring a return time series, not a bag of independent forward
returns, and on 116 closed trades they are not credible. See §F.1.

## B.3 Confidence calibration

*(original §8)* Build the confidence-vs-actual-win-rate table:

```
Confidence 50–60% → Actual Win Rate ?
Confidence 60–70% → Actual Win Rate ?
...
```

**Prior finding to verify, not assume:** a check during the 2026-09-04 session found live win
rate **flat at ~29-31% across every confidence quintile** — i.e. confidence carried essentially
no information about outcome. That was measured on the small paper-trade sample. Re-run it
properly against all 16,732 signal outcomes, per horizon. If it holds at that scale, it is the
single most important finding in this audit and calibration must be rebuilt.

Also assess: do AI explanations match the underlying data? Does it contradict its own technical
indicators? Chase extended stocks? Buy near resistance / sell near support? Enter late? Hold
losers?

## B.4 Options-flow predictive test

*(original §6 partial, §7)* For every options-flow signal, compute probability the stock moves
in the predicted direction at 1d / 3d / 5d / 10d / 20d, plus average return, median return, win
rate, MAE, MFE, profit factor, expectancy. n=1,511 supports this.

Compare against a random baseline, buy-and-hold, SPY, QQQ, and the sector ETF.

**Do not assume "large call purchase = bullish." Test it.**

Same treatment for squeeze alerts (n=317) and dark-pool alerts (n=144) — note the smaller
samples explicitly and widen the error bars accordingly.

**Open question worth resolving here:** real daily UW request volume is unmeasured. A code-derived
estimate put it at roughly 22k–34k/day (market-hours) or up to ~80k/day if truly 24/7 — against
a 30,000/day ceiling. Two contributors (`check_options_flow_alerts`, `check_dark_pool_alerts`)
had no market-hours gate and were also silently dead for an unknown period, so real volume is
likely lower than the worst case. A new `uw_rate_limit_events_48h` gauge now tracks real 429s.
Get a real metered number.

## B.5 Find the real edge

*(original §26 — one of the most valuable sections, preserved)*

Determine where predictive edge actually comes from, per input layer:

```
Technical signals:  +?% expectancy
News:               +?%
Options flow:       +?%
Fundamentals:       +?%
Macro filter:       +?%
Combined model:     +?%
```

`signal_outcomes` carries `ta_score`, `ml_prob`, `ml_auc`, and `fused_prob` — enough to separate
TA contribution from ML contribution from the fusion. If a layer contributes no measurable
alpha, recommend reducing its weight or removing it.

**Deliverables:** `SIGNAL_PERFORMANCE.md`, `AI_AUDIT.md`

---

# PHASE C — EXECUTION REALISM

*Preserves original §5, §11, §12 — but read §C.0 first.*

## C.0 Instrumentation gap (read before attempting §C.1)

The original §5 asks, per trade, for: bid, ask, spread, slippage, MFE, MAE, real market price at
fill. **The platform does not record most of these.** `paper_trades` stores entry/exit price,
`highest_price`, stop/target, and a flat `entry_slippage_pct: 0.001` assumption. There is no
bid/ask capture, no MAE, and no per-fill realism model.

So §C.1 is partly a **build**, not an audit. Split it:

- **C.0a (build, prerequisite):** add per-trade capture of bid/ask/spread at decision time, MFE,
  and MAE. Without this, execution realism cannot be audited now *or* later.
- **C.0b (audit, possible now):** the parts that ARE answerable from existing data — stop
  execution correctness, gap handling, exit-reason labelling accuracy, holding period,
  MFE-proxy via `highest_price`.

Reporting a slippage/spread analysis built on a flat 10bps constant would be presenting an
assumption as a measurement. Do not do it.

## C.1 Paper-trading realism (scoped to C.0b)

Investigate: unrealistic fills, midpoint fills, absent bid/ask spread, absent slippage, no
latency, perfect execution, impossible option fills, missing liquidity constraints, ignored
partial fills, ignored market gaps, incorrect stop execution.

**Verified non-issues — do not re-litigate without new evidence:**
- `breakeven_stop` showing a small negative average return is legitimate (slippage/commission on
  a near-breakeven fill), and the stop-hit/breakeven conflation was already fixed 2026-08-31.
- A GROWTH position stopping out at ~-12% is the *designed* behaviour: `stop_pct: 0.880` is an
  intentional 12% initial stop. AXON was verified to have never armed a trailing stop because it
  never ran up far enough (`highest_price` only +1.3% over entry) — the pre-defined risk limit
  worked exactly as intended.

## C.2 Entry timing

*(original §11)* Per trade, analyse entry relative to EMA, VWAP, RSI, volume, support,
resistance, ATR, market trend, sector trend, relative strength. Classify EARLY / OPTIMAL / LATE
/ CHASE. Build an Entry Quality Score 0–100.

**Note:** run this against `signal_outcomes` where possible (n=16,732) rather than only closed
trades (n=116). Entry quality is a signal-level property and deserves the larger sample.

## C.3 Exit analysis

*(original §12)* Compare actual exit vs optimal historical exit vs MFE vs MAE. Analyse stop
loss, take profit, trailing stop, time-based exit, technical exit, AI exit. MFE/MAE are
partially blocked on C.0a — use `highest_price` as an MFE proxy and state the limitation.

## C.4 Overtrading

*(original §25)* Trades per day/week, average holding period, turnover, transaction costs. Is
the system producing many low-quality signals? Prefer fewer high-quality trades.

**Deliverable:** `PAPER_TRADING_AUDIT.md`

---

# PHASE D — RISK, SIZING & PORTFOLIO

*Preserves original §13, §14, §22, §23. Note §9/§10/§16 moved to §F.*

## D.1 Position sizing audit

*(original §13)* Audit risk per trade, portfolio risk, correlation, sector concentration, max
exposure, volatility-adjusted size, ATR-based size.

Kelly is **not implemented** — see §F.4. If recommending it, do not recommend full Kelly;
conservative fractional Kelly only.

Recommend: max position %, max portfolio risk %, max sector exposure %, max daily loss %, max
weekly loss %, max drawdown before trading halts.

Note the platform already has most of these as live config (`max_position_pct`,
`max_open_risk_pct`, `max_daily_loss_pct`, `max_portfolio_drawdown_pct`, `heat_brake_*`,
`index_trend_gate_pct`). Audit whether the **values** are justified, not whether the mechanism
exists.

## D.2 Portfolio-level analysis

*(original §14)* Evaluate the portfolio, not only individual trades: CAGR, Sharpe, Sortino, max
drawdown, beta, alpha, correlation, sector exposure, factor exposure, concentration, turnover,
cash utilisation. Compare vs SPY, QQQ, IWM, sector ETFs.

**Scope constraint:** with 2.5 months of equity curve in a single regime, treat every
annualised figure (CAGR, Sharpe, alpha/beta) as *indicative only* and state the sample period
inline with every number. Do not annualise 2.5 months into a headline CAGR without a caveat
attached to the figure itself.

## D.3 Trade gating

*(original §22)* Before a trade is approved, run: data quality, market regime, liquidity,
technical, fundamental, options, news, risk, portfolio, execution checks. Critical failure → NO
TRADE. Audit whether the existing gate chain actually enforces this.

## D.4 Profitability gates

*(original §23 — preserved, this is high-value)*

The platform must not go live-autonomous merely because win rate ≥70% or return ≥10%. Require:
statistically meaningful sample, positive expectancy, positive profit factor, robust
out-of-sample performance, walk-forward stability, acceptable max drawdown, positive
Sharpe/Sortino, realistic transaction costs, realistic slippage, paper-trading confirmation, no
major data leakage, **and no dependence on a single market regime**.

**That last condition currently fails outright** (97.3% bull). State it as a hard blocker to
live automation, not a soft caveat.

**Deliverable:** `RISK_AUDIT.md`

---

# PHASE E — LIVENESS, COMPLETENESS & SYNTHESIS

*§E.1 is new (see §0.2). §E.2–E.5 preserve original §17, §18, §19, §24, §28, §29.*

## E.1 Liveness & completeness audit — NEW

For every scheduled job, alert path, and data-producing feature, verify it is **actually
running and actually producing output**, independent of whether its logic is correct.

Check, at minimum:
- Every scheduler job's real `last_run` is advancing at its declared cadence (not merely that
  the job is registered).
- Every outcome/snapshot table is receiving rows at its expected rate; flag any table with zero
  rows ever, or whose newest row is stale relative to its own job's cadence.
- Every API route backing a user-visible feature returns 200 with real data for a real input —
  **exercise it, don't just read the code.** The `/options-game-plan/batch` bug (a wrong
  relative import, 500 on every call since it shipped) was invisible to code review, invisible
  to a 21-test suite, and invisible in the UI, because a failed fetch renders as empty.
- Every paper portfolio is actually trading; a portfolio with zero entries over a multi-week
  window is a defect until proven otherwise.
- Cross-container consistency: confirm `shared/` is current in every backend container. A stale
  `shared/db/` on 9 of 10 containers was found live and would have crash-looped each on its next
  restart.

The platform now has a `_DQ_CHECKS` framework with job-liveness checks covering 17 minute-cadence
jobs plus a UW rate-limit gauge. Audit whether that coverage is *complete* — which jobs, tables,
and routes still have no liveness check at all.

## E.2 Strategy ranking & culling

*(original §17, §18)* Rank strategies. Suggested weighting — but note the components that need
Phase F data:

```
Strategy Score = 30% Expectancy + 20% Sharpe + 15% Sortino
               + 15% Profit Factor + 10% Drawdown + 10% Stability
```

Until Sharpe/Sortino/drawdown are credible (§F.1), rank on **expectancy, profit factor, and
sample size** alone, and say so. Classify each strategy: KEEP / IMPROVE / PAPER ONLY / DISABLE /
RETIRE. A negative-expectancy strategy must not generate live recommendations.

## E.3 Signal quality score

*(original §19)* Unified score across technical, fundamental, momentum, volume, options, news,
macro, market regime, risk, liquidity. Weights must be configurable **and backtestable**. Do not
hard-code arbitrary weights without validation.

## E.4 Profit maximisation

*(original §24)* Improve profitability, never by simply increasing risk. Investigate better
entries, better exits, better sizing, strategy selection, regime filtering, sector rotation,
relative strength, catalyst timing, options selection, volatility filtering, trade frequency,
capital allocation.

For each proposal show: current, proposed, historical performance, out-of-sample performance,
risk change, drawdown change, confidence.

## E.5 Final report

*(original §28, §29)* Produce prioritised findings: critical bugs, high/medium/low-impact
improvements, features to remove, features to add, strategies to disable, strategies to improve,
data-source changes, AI prompt improvements, risk-engine improvements, backtesting improvements.
Each with WHY / EXPECTED BENEFIT / RISK / COMPLEXITY / EXPECTED IMPACT.

Executive summary must answer: Is the system profitable? Is it statistically credible? Where is
the edge? Where does it lose money?

**On the original §29 scoring rubric (Data Quality /100, Signal Quality /100, …):** replace
numeric scores with **evidence-backed ratings** — e.g. `STRONG / ADEQUATE / WEAK / UNMEASURABLE`
— each with the specific measurement supporting it. A "Signal Quality: 62/100" has no principled
derivation and creates false precision. `UNMEASURABLE` must be an available and frequently-used
rating.

Still produce: Top 10 Problems (by financial impact), Top 10 Opportunities (by expected
improvement), best/worst strategies, best signals, best entry conditions, best exit conditions,
best options setups, recommended architecture, and a P0/P1/P2/P3 roadmap.

**Deliverables:** `RECOMMENDATIONS.md`, `IMPLEMENTATION_PLAN.md`, `TRADING_SYSTEM_AUDIT.md`

---

# §F — FUTURE WORK: BLOCKED ON DATA OR UNBUILT FEATURES

Everything here comes from the original prompt. None of it is dismissed — each item has a
concrete **unblock trigger**. Re-evaluate when triggers are met.

## F.1 Portfolio-path statistics per strategy
**Original:** §3, §4, §17 (Sharpe, Sortino, Calmar, CAGR, max/avg drawdown, recovery factor,
consecutive win/loss streaks — per strategy).
**Blocked because:** these require a return *time series* per strategy, not independent forward
returns. n=116 closed trades across 5 portfolios and 4 styles leaves ~20-40 per strategy — far
too few for a credible Sharpe.
**Unblock trigger:** ≥200 closed trades per strategy being ranked, or a walk-forward harness
(§F.3) generating synthetic per-strategy equity curves.

## F.2 Regime matrix and sector breakdown
**Original:** §9 (bull/bear/sideways/high-vol/low-vol/risk-on/risk-off/rates/recession), §10
(11 sectors).
**Blocked because:** **97.3% of all 16,732 outcomes are `bull`. `bear` has exactly 1 row.** This
is not a sample-size problem that time alone fixes at the current rate — it needs the market to
actually change. Slicing 116 trades across 11 sectors × 6 regimes yields cells of 0-2 trades.
**Unblock trigger:** ≥300 outcomes in at least two distinct regimes. Until then, any regime
claim must be labelled `UNMEASURABLE`. Sector analysis can begin partially at ~500 closed trades.
**Interim action:** ensure regime is being *recorded* correctly on every outcome so the data
accumulates cleanly — the platform's regime vocabulary has already needed one migration.

## F.3 Walk-forward and out-of-sample validation
**Original:** §15 (training / validation / out-of-sample / walk-forward / paper trading).
**Blocked because:** the walk-forward harness **does not exist** — CLAUDE.md lists it as a known
limitation ("deferred, 2+ weeks of work"). The original prompt asked to audit a capability that
was never built.
**Unblock trigger:** build it. This is arguably the highest-value item in §F, because it
partially unblocks F.1 and F.4 as well.
**Note:** existing backtests should still be audited *now* for look-ahead bias, survivorship
bias, data snooping, overfitting, parameter-optimisation bias, and multiple-testing problems —
that part is Phase A work and is not blocked.

## F.4 Monte Carlo / probability of ruin
**Original:** §16 (1,000+ simulated trade sequences, drawdown distribution, probability of ruin).
**Blocked because:** resampling a 116-trade sequence (or ~30 per strategy) produces confidence
intervals so wide the output is not decision-useful, and would imply precision that isn't there.
**Unblock trigger:** ≥300 closed trades for the portfolio being simulated.

## F.5 Execution-realism instrumentation
**Original:** §5 (bid, ask, spread, slippage, MFE, MAE, real fill price).
**Blocked because:** not recorded. Only a flat `entry_slippage_pct: 0.001` assumption exists.
**Unblock trigger:** implement §C.0a. This is a build task and should be scheduled — it is a
prerequisite for ever honestly answering "is paper trading unrealistically optimistic?"

## F.6 Multi-leg options strategies
**Original:** §6 (debit spreads, credit spreads, calendar spreads, vertical spreads, LEAPS).
**Blocked because:** not implemented. The platform supports protective puts and covered calls
(the Options Game Plan) — the rest do not exist, so there is nothing to audit.
**Unblock trigger:** build them, if desired. Note the Options Game Plan itself only *just* began
producing real data (its EOD job had never successfully run; its read route 500'd since it
shipped), so even the two existing strategies have effectively **zero** live track record. Let
them accumulate outcomes before extending the surface.

## F.7 Kelly-based position sizing
**Original:** §13.
**Blocked because:** not implemented, and Kelly needs a reliable win-rate/payoff estimate —
which depends on B.3 confirming that any of the platform's probability estimates are calibrated.
Current evidence suggests they may not be.
**Unblock trigger:** B.3 shows meaningfully calibrated probabilities. Then fractional Kelly only.

## F.8 AI CIO multi-agent architecture
**Original:** §20 (9 specialist agents + synthesising CIO).
**Deferred because:** the original prompt already flagged this as target architecture rather
than an immediate requirement — correctly. Adding nine LLM agents on top of a confidence signal
that may not correlate with outcomes at all would layer sophistication over an unvalidated base.
The session record shows the platform's real losses came from plumbing defects (dead schedulers,
wrong imports, market-blind thresholds), not from insufficient AI reasoning.
**Unblock trigger:** B.3 and B.5 demonstrate the *existing* single-model signal has measurable,
calibrated edge. Then evaluate whether multi-agent synthesis adds anything beyond it.
**Worth adopting independently of the architecture:** the original §21 (structured trade thesis
with explicit invalidation) and the rule that **"NO TRADE" is a valid and frequently-used
outcome**. Both are good discipline and neither requires the agent fleet.

---

# §G — OPERATING RULES

*(original §30, §31 — preserved essentially verbatim, they are the best part of the document)*

## G.1 Do not tell me what I want to hear

If the platform is losing money — **say it**. If a 70% win-rate target is unrealistic — **say
it**. If the AI signals have no statistical edge — **say it**. If Unusual Whales flow is not
improving results — **say it**. If paper trading is unrealistic — **say it**. If yfinance is too
delayed for a given strategy — **say it**. If a strategy looks excellent only because of
backtest bias — **say it**.

Add one rule the original lacked: **if something cannot be measured with the data available,
say that too, and label it `UNMEASURABLE`.** Do not substitute a plausible-looking number for a
missing measurement. An audit that reports six honest findings and twelve `UNMEASURABLE`s is
more useful than one that reports eighteen numbers, six of which are noise.

## G.2 Implementation principle

Improve based on evidence. Do not blindly add indicators, add AI agents, increase trading
frequency, increase leverage, or optimise for win rate alone.

Optimise for: **positive expectancy, risk-adjusted return, robustness, capital preservation,
consistency, and realistic execution.**

## G.3 Change discipline

Do not modify production code during Phase A. When implementing later: small commits, add tests,
preserve existing functionality, benchmark before and after, record every material change, and
**never remove a safety mechanism to increase apparent profitability**.

---

# §H — EXECUTION & OUTPUT

## H.1 Phase sequencing

Phases are **dependency-ordered, not calendar-ordered.** The original's "Day 1 / Day 2 / Day 3"
framing understated the work — Phase A alone (full-repo inspection plus a point-in-time audit
across every signal path, on a codebase with ~12 services and an 11,000-line scheduler) is
realistically a week or more. Complete each phase and review before starting the next.

```
Phase A — Data integrity            → DATA_QUALITY_AUDIT.md
Phase B — Signal edge attribution   → SIGNAL_PERFORMANCE.md, AI_AUDIT.md
Phase C — Execution realism         → PAPER_TRADING_AUDIT.md
Phase D — Risk, sizing, portfolio   → RISK_AUDIT.md
Phase E — Liveness & synthesis      → RECOMMENDATIONS.md, IMPLEMENTATION_PLAN.md,
                                       TRADING_SYSTEM_AUDIT.md
```

`BACKTEST_AUDIT.md` folds into Phase A (bias audit of existing backtests) with its
walk-forward portion deferred to §F.3. `OPTIONS_AUDIT.md` folds into Phase B (§B.4) — a
standalone options audit is not warranted while multi-leg strategies don't exist and the two
built strategies have no track record.

## H.2 Output location

Store all audit, analysis, research, test, and recommendation documents under a date-specific
directory: `/docs/YYYY-MM-DD/`, using the actual execution date.

## H.3 Re-measure before trusting this document

The ground-truth figures in §0 and the phase gates in §F are snapshots from 2026-09-04. Several
change over time — sample sizes grow, regimes shift. **Re-run the §0 queries at the start of any
re-execution** and update the gates accordingly. Do not inherit conclusions from a stale
measurement; that is the same error this revision exists to correct.

## H.4 The objective

Determine whether this system has a **repeatable trading edge** — not whether it looks
intelligent, and not whether it has a high historical win rate.
