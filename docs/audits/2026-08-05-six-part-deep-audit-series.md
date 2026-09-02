## Deep Audit #1 of 6: AI Signal Performance / Accuracy / Win Rate / Return (2026-08-05)

**Scope**: documentation-only audit (explicit user instruction: "don't fix it just document and
update improvement tracker"). Tracked as **Tier 261** in `improvements.tsx`, 12 entries
(11 findings + 1 CLEAN reference entry).

**The question asked was NOT "is the strategy good"** — real production data already answers
that, and the answer is no. It was: **can the reported numbers be trusted?** The answer is also
no, and critically, **every significant defect biases in the same direction: it makes a losing
system look less bad.**

### Ground truth from production (9,001 rows, signal_date 2026-05-25 → 2026-07-29)

| horizon | direction | n | win_rate | avg_ret (raw, unsigned) |
|---|---|---|---|---|
| SHORT | BUY | 1279 | 40.9% | -2.060% |
| SHORT | SELL | 913 | 39.6% | +0.804% |
| SWING | BUY | 1423 | 37.9% | -3.802% |
| SWING | SELL | 884 | 46.0% | -0.621% |
| LONG | BUY | 1095 | 34.5% | -9.136% |
| LONG | SELL | 847 | 44.0% | +0.498% |
| GROWTH | BUY | 1851 | 37.4% | -4.068% |
| GROWTH | SELL | 709 | 41.2% | +1.024% |

**Reading these correctly**: `pct_return` is stored UNSIGNED (raw price change), so a SELL wins
when it is NEGATIVE. Therefore SELL rows showing a POSITIVE average are LOSING. **SWING/SELL at
-0.621% is the only genuinely profitable direction in the entire system.** No direction on any
horizon clears a 50% win rate.

### The four critical/high findings, all biasing the same way

1. **`outcomes_summary()` pools unsigned SELL into 8 aggregates** — verified against real data:
   the UI shows **-2.693%** Overall; sign-corrected is **-2.990%**; BUY-only reality is
   **-4.529%**. The headline is pulled **1.84pp toward zero**. `_retro_ev_for()` was already
   fixed for exactly this bug (with an explicit comment); the fix was never propagated to the
   endpoint that feeds the UI. `by_direction` is the only safe aggregate. `by_symbol` sorts by
   `-avg_return_pct`, so a symbol whose SELLs lost badly ranks as a **top performer**.
2. **`/signals/accuracy` is mark-to-TODAY but the UI labels it "5-day"** — `most_recent_close()`
   returns `_pclose[sid][-1]` for every signal regardless of age, so at the 90d default an
   89-day-old signal is held 89 days and a 2-day-old one 2 days, pooled into one number. The
   backend's own comment says *"shows running P&L from entry to today"*; the stat card beneath
   says *"5-day avg after BUY"*. This is the **page headline metric**.
3. **`profit_factor` decouples magnitude from real P&L** — `abs()` bucketed by the `correct`
   label, displayed with the explicit reading *"Above 1.5 = good; below 1.0 = signals losing
   money"*, and it is the one card colored green on a fixed threshold.
4. **"Paper Trade Results · actual closed trades written back by PT-J1"** — confirmed by grep
   that `paper_trading_engine.py` **never writes** `return_5d`/`is_correct_5d`. These are
   hypothetical forward returns from `evaluate_signal_outcomes()`, with no stops, no sizing, no
   real exits. The attribution in the UI is simply false, and the count label says "trades".

### One finding where the headline observation was NOT the bug

`skip_reason` is NULL for all 9,001 rows (zero censored, zero `delisted_loss`). Traced fully:
the branch **is** reachable and has simply never triggered, because no symbol stopped producing
D1 bars for >10 days past an exit target in this window. **The real defect underneath it**:
`_lookup_outcome_price` uses `bisect_left` (on-or-after) with **no upper staleness bound**, so a
symbol with a long ingestion gap that later resumes gets scored against a far-future price as a
clean exit instead of being censored. "Zero censored rows" is consistent with correct behavior
but is **not evidence** of it.

### The most dangerous property, stated plainly

A user opening `/signal-accuracy` sees: a "5-day" return that is really a 90-day drift, an
Overall return inflated 1.84pp by counting SELL losses as gains, a Profit Factor that treats
short-side losses as wins, and a "Paper Trade Results" panel that never touched the trading
engine — **all colored green above fixed thresholds**. The one honest panel (`by_direction`,
showing the real 34-46% win rates) is a small sub-table two scrolls down, sitting next to a
`rolling_accuracy` drift alarm that is **permanently red** (its 55% threshold vs. a real 34-41%
BUY win rate), which trains the user to read the one truthful indicator as broken.

### Verified CLEAN (do not "fix" these)

`evaluate_signal_outcomes()` core labeling (both `is_correct` and `is_correct_Nd` apply
direction-aware hurdle logic correctly); T+1 entry genuinely avoids same-day look-ahead;
per-row `begin_nested()` savepoints; `_retro_ev_for()`'s sign fix; `by_direction`;
`alpha_decay`/`information_coefficient`/`factor_attribution` all correctly BUY-scoped at the
query level; `_build_confidence_calibration()` (keyed by horizon+direction+market, 30-sample
floor, uses `is_correct` only); the `is_correct_5d/10d/20d` write path; `censored_count`'s
specific `no_exit_price` filter; hold-window constant consistency.

**Checked and explicitly NOT reported as a finding**: the 7-day gap between the newest outcome
`signal_date` (07-29) and the newest signal (08-05) is the expected hold-window resolution lag,
and the evaluation job IS running (last write 2026-08-04 20:30 UTC) — verified before filing,
rather than reported as a stalled job.

---


## Deep Audit #2 of 6: Prediction / Decision-Making / Paper Trading (2026-08-05)

**Scope**: documentation-only (per standing instruction). Tracked as **Tier 262** in
`improvements.tsx`, 13 entries (12 findings + 1 CLEAN reference).

### Production ground truth (5 portfolios, 82 closed trades)

| id | name | closed | avg_ret% | win_rate | total_pnl |
|---|---|---|---|---|---|
| 1 | GROWTH | 29 | -0.40 | 27.6% | -1,115 |
| 2 | HK SWING | 4 | -5.66 | 0.0% | -6,611 |
| 3 | US SWING | 31 | +0.26 | 35.5% | -1,736 |
| 4 | HK GROWTH | 11 | -0.44 | 36.4% | +1,058 |
| 5 | ETrade Sandbox | 7 | -2.27 | 14.3% | -764 |

**`pct_return` is stored as a PERCENTAGE** (`-16.77` = -16.77%), not a fraction. My first query
multiplied by 100 and produced impossible values (-566%); caught by noticing you cannot lose
>100% on an unlevered long. Worth remembering before reporting any P&L "anomaly" from this table.

### The root finding: two economically opposite events share one exit label

| exit_reason | n | profitable | losing | best | worst |
|---|---|---|---|---|---|
| stop_hit | 49 | **14** | 35 | **+13.96%** | -16.77% |
| breakeven_stop | 26 | 4 | **22** | +2.36% | **-5.18%** |
| target_reached | 6 | 6 | 0 | +13.99% | +11.66% |
| signal_exit | 1 | 0 | 1 | -4.46% | -4.46% |

`stop_hit` is the catch-all `else` at `paper_trading_engine.py:2394-2409` for any stop breach
where `abs(stop - entry) > entry*0.005`. Trailing stops ratchet **up**, so a stop that trailed to
well above entry and then fired is a **profitable exit wearing the same label as a loss-cut**.
Separately, `breakeven_stop` is assigned by comparing the **stop level** to entry, never the
actual **fill** — so a gap-down through the stop keeps a label asserting it exited flat.

The trailing-stop mechanism itself is **correct** (floor `max(new_trail, stop_loss)`,
monotonic-raise-only — verified). This is purely a labeling defect. But six consumers key on
`exit_reason`, and two of them actively harm trading:

- **The heat brake halts ALL entries when the engine performs best** (`:3946-3960`) — three
  profitable trailing exits in 48h (+8%, +11%, +14%) count as 3 "stops hit", logged as *"adverse
  conditions, pausing entries"*, and suspend every new entry portfolio-wide. No P&L filter.
- **The 5-day cooldown blocks re-entry into winners** (`:3725-3737`) — a symbol that exited at
  +13.96% is banned for 120h *because it made money*. ~29% of cooldowns are misapplied.
- **The breakeven cooldown is 60× shorter (2h vs 120h)** on a premise production contradicts —
  22 of 26 `breakeven_stop` trades **lost** money, so the engine can re-enter a genuinely
  declining stock the same session.

### The most damaging finding: ML ground truth records winners as losses

`paper_trading_engine.py:2562-2563`. T232-PT6 correctly added blended scale-out accounting
(`total_pnl_dollar = realized_pnl + pnl_dollar`, `total_pnl_pct` against the **original** cost
basis) *specifically* so "a trade that took profit on the way up and trailed the remainder to
breakeven is scored as the winner it actually was". **That fix was applied to `trade.pnl` but
never propagated to the `SignalOutcome` writeback 30 lines later**, which still uses the
unblended final-tranche `pnl_pct` / `pnl_dollar`.

Because scale-outs only fire on winners, this **only ever corrupts winning trades, always
downward**. 9 of 82 closed trades (11%) already have `realized_pnl != 0`. It feeds
`evaluate_signal_outcomes`, confidence calibration, `_build_confidence_calibration` (which gates
the T257-TOP3 alert users act on), and `gate_harness.py` validation slices.

### Answering the HK 0%-win-rate question honestly

HK SWING is 0/4 (-$6,611 on 300k). Investigated for a code-level cause and **did not find one**:
the HK overrides are all *tighter* (min_entry_score 4→6, min_confidence 45→65, min_ta_score 0.65,
risk_per_trade 1%→0.7%, max_position 10%→7%), and sizing is currency-agnostic
(`risk_dollar / stop_distance`, same unit both sides) so there is **no scale bug**. With n=4 this
is not statistically distinguishable from noise; combined with a prior 0/9 it is 0/13 —
suggestive, but more likely signal quality (already tracked as T222-A/T224-C). Reported the one
concrete HK defect actually found (**no board-lot handling** — `round(shares, 4)` yields
fractional quantities unfillable on HKEX) rather than manufacturing a cause.

### Explicitly investigated and found NOT to be bugs

- **US SWING: +0.26% average return but -$1,736 total P&L.** Not an accounting error —
  `pct_return` is size-independent, `pnl` is size-dependent, so a positive mean with a negative
  sum only requires losers carrying larger cost bases. Reproduced arithmetically before
  dismissing.
- **`calibrate_entry_weights`** targets `pnl > 0` and never reads `exit_reason` — so the
  entry-weight fit is **not** corrupted by the label conflation.

### Verified CLEAN (do not "fix" these)

Scale-out blending on the trade itself; `pct_return` scale consistency; stop-sign/R:R structural
guarantees; position-size-vs-cash (the `_slipped_position_value` gate uses the *same* slipped
value later deducted, per the T247 fix — cannot overdraw); equity recomputation between entries;
`calibrate_entry_weights`' 70/30 chronological split + `_SCORE_SCALE_CUTOFF` + validation gate;
DE hard-reject parity across all recently-ported gates (matching thresholds, correct signs);
`_recent_win_rate`/`_consec_loss_streak` keying on `pnl` not `exit_reason`; delisted auto-exit
ordering; trailing-stop floor.

---


## Deep Audit #3 of 6: Model Training / Self-Tuning / Self-Improving (2026-08-05)

**Scope**: documentation-only. Tracked as **Tier 263**, 12 entries (10 findings + 1 REFUTED/CLEAN
reference + this section).

### Production ground truth: `tune_history`

| parameter_class | n | promoted | rejected | window |
|---|---|---|---|---|
| signal_threshold | 104 | 37 | 67 | 07-07 → 08-02 |
| watchlist_rotation | 103 | 103 | 0 | 07-16 → 08-02 |
| gate_threshold | 67 | 3 | 64 | 07-05 → 08-02 |
| ml_hyperparams | 61 | 25 | 36 | **all on 08-02** |
| joint_strategy | 20 | 3 | 17 | 07-18 → 08-02 |
| ml_fusion_weight | 4 | 3 | 1 | 07-07 → 08-02 |
| signal_gate | 4 | 0 | 4 | 07-31 |
| ta_weights | 1 | 0 | 1 | 08-02 |

**`conviction_weights` is absent from this table entirely — that absence is the headline finding.**

### The headline: an ungated weekly mutation of live weights

`calibrate_conviction_weights` (`calibration.py:759-870`, scheduled `scheduler.py:4296`) runs
**every Sunday**, fits on the full sample, and `setex`'s straight to disk + Redis + the live
in-process global. Verified by grep: **zero** `_record_tune_history` calls in its body, no
train/validation split, no baseline comparison, no rejection branch. Its only guard is a
30-outcome floor.

This is the *identical* defect `BUG233-TAWEIGHTS-NOVALIDATION` fixed for its sibling
`calibrate_ta_weights` on 2026-07-31 — conviction_weights was never given the same treatment.
Confirmed two ways: the grep, and a direct production query returning **0 rows** for
`parameter_class LIKE '%conviction%'`. Every weight change it has ever made is invisible.

### Audit #2's corruption propagates directly into tuning

`gate_harness.py`'s `_HORIZON_BUCKET` reads `return_5d/10d/20d` and `is_correct_5d/10d/20d` —
exactly the fields Audit #2 found written from unblended last-tranche P&L. So **all 67
`gate_threshold` attempts were scored against corrupted labels**, and the bias selectively hits
high-conviction setups (those are the ones that scale out). Two ML features
(`sig_acc_30d`, `sig_avg_ret_30d`, `builder.py:238-264`) are built from the same corrupted
`pct_return`, creating a self-reinforcing loop: the model learns to avoid the setups that were
actually working.

**Fix ordering matters**: fixing the Audit #2 writeback resolves both of these for free — but
every `gate_threshold` result to date then needs re-running.

### Other significant findings

- **`ev_gate` never checks direction** (`ev_gate.py:60-71`) — `mean(y_ret[probs >= 0.60])` is
  only meaningful if high probability means "go long", and nothing enforces that. No sign check,
  no monotonicity check, no win-rate floor. A passing candidate immediately retrains the live
  model.
- **`tune_all` is fire-and-forget** — `_record_job_status("tune_all_sent","ok")` records that the
  POST succeeded, not that tuning finished. All 61 `ml_hyperparams` rows on one day is the
  signature of the 21-day *stale guard* firing, not a weekly cadence.
- **403-cell grid, n=15/cell, no multiple-comparisons correction** (`tune_strategy`). At ~10pp
  return SD the SE of a cell mean is ~2.6pp; the max of ~403 correlated draws sits 2-3 SE high,
  implying 5-8pp of EV bias from noise alone. `gate_harness` already has the stricter margin
  (`_MIN_PROMOTION_EV_LIFT_PCT` + `_MIN_PROMOTION_LIFT_SD_RATIO`) — **it was built for a smaller
  search space and never applied to the largest grid.**
- **The watchdog shadows validated thresholds** — its 7-day key is read *first*, so a
  freshly-validated `tune_strategy` value can be recorded `promoted=True` while never taking
  effect.
- **Every tuned param silently reverts on TTL expiry**, and expiry is indistinguishable from
  never-tuned or from a skipped run. No alert on any of them.

### Two candidate findings REFUTED by checking real data

1. *"`watchlist_rotation` 103/103 promoted = a gate that never rejects."* **False.** Those rows
   are an audit trail of add/drop *actions*. And for drops, `validation_ev_pct` holds the
   symbol's **win rate** while `baseline_validation_ev_pct` holds `_WIN_RATE_FLOOR` — so
   "candidate worse than baseline" on all 49 drops is the **intended trigger**.
2. *"`ta_weights` 1/0 promoted = a degenerate median-split gate that can only tie."* **False.**
   Queried the row: candidate −0.04 vs baseline −0.03 — a genuine (tiny) negative lift, not a
   tie. The gate worked correctly.

A third was **corrected mid-audit**: `signal_gate`'s 0-promotion record was initially attributed
to a sample-floor lockout; the real rows show `validation_n` of 238/222/180/56 and failures of
`candidate_unmeasurable_on_validation` (×3) and `ev_lift_not_positive` (×1) — a grid/selection
defect, not a data shortage.

**Note the semantic trap this exposed**: `validation_ev_pct` / `baseline_validation_ev_pct`
carry *different meanings per `parameter_class`* (EV for sweeps, win-rate-vs-floor for
rotations). Any cross-class query over those columns produces nonsense.

### Verified CLEAN

`outcomes_calibrate_apply` (both BUY and SELL sweeps — chronological split, live baseline,
negative-lift rejection, TuneHistory on all 6 exit paths); **Audit #1's unsigned-SELL bug does
NOT reach any tuning mechanism** (all filter `signal_direction == "BUY"`) — it is confined to
reporting; Optuna's CV purge/embargo (`TimeSeriesSplit(gap=horizon)`, T232-ML4); EV-gate holdout
isolation (last 15% sliced before Optuna sees anything); macro feature merge (trailing windows +
forward-fill only, no leakage); `fetch_signal_outcome_features`' `.shift(10)` keyed on
`exit_date` (look-ahead-safe construction, corrupt input notwithstanding); the symmetric
dead-zone label filter; `gate_harness`'s promotion margin (structurally the strongest gate here —
its problem is corrupted input, not design).

---


## Deep Audit #4 of 6: Market Regime / Trend / Earnings / News / Events (2026-08-05)

**Scope**: documentation-only. Tracked as **Tier 264**, 13 entries (12 findings + 1 CLEAN
reference).

### Two structural findings dominate

**1. The T249-P0 macro release-date alerting has been dead for 6 of 10 indicators since it
shipped.** `sync_fred_release_dates()` writes 10 release event types; `check_release_day_fast_poll()`
— verified to be the *only* writer of `actual_value` to `*_release` rows anywhere — filters on
`_RELEASE_TO_FRED_SERIES.keys()`, which holds only 5 keys, one of them mapping to `None`. So
`fed_funds_release`, `retail_sales_release`, `consumer_conf_release`, `housing_starts_release`
and `jobless_claims_release` are **never even SELECTed**.

Production measurement:

| | rows (past-dated) | with `actual_value` |
|---|---|---|
| release-date family | 202 | **2 (1%)** |
| reference-period family | 153 | 138 (90%) |

`fed_funds_release` alone is 113 of those empty rows. Only **2 of 433** `economic_events` have
ever had a `reaction_text` generated. A second root cause affects even the 4 "pollable" types:
the poll fetches the *reference-period* series and writes `obs[0].value`, which `sync_fred()`
already wrote to the plain `cpi` row — so the release row duplicates the reference row, and if
FRED hasn't attached the new observation during the poll window, **last month's value is
silently written as this release's actual**. Explicitly ruled out the cron window as a cause.

**2. signal-engine runs a second, independent regime classifier with an incompatible
vocabulary.** Grep confirms **zero** references to `/stocks/regime`, `get_last_regime`, or
`get_last_hk_regime` anywhere in `services/signal-engine/src/`. It derives US regime from
`/stocks/fear_greed` and HK regime from its own yfinance HSI-vs-SMA20 fetch.

| classifier | vocabulary |
|---|---|
| market-data (canonical) | bull / neutral / **choppy** / **risk_off** / bear |
| signal-engine US | bull / **high_vol** / bear / unknown |

`choppy` and `risk_off` **cannot be emitted** by signal-engine. Concrete divergence: SPY +3%
over its 200d MA with VIX 27 → market-data says `risk_off` and blocks entries via
`hard_rejects.py`; signal-engine simultaneously says `bull` and applies bull-tier buy thresholds.
The T232-DL-REGIME5X consolidation succeeded for decision-engine (verified CLEAN) — signal-engine
was left behind, which is what makes it the outlier rather than a known-accepted split.

Related: `_REGIME_ML_THRESH` has **no `choppy` or `risk_off` keys**, so via `.get(regime, 0.70)`
the two most defensive canonical regimes fall through to a threshold *looser* than `bear`'s 0.78.

### Other notable findings

- **Fiscal quarter is inferred from the announcement month** (`earnings.py:201`, comment: *"Infer
  quarter from month"*). A company announcing 2026-07-14 reports **Q2** but is stored as Q3 —
  confirmed in production (FY2026 Q3 spans report dates 07-14 → 09-23). Because it's part of the
  uniqueness key, two reports in one calendar quarter **overwrite each other**, destroying history.
- **Company-name news matching is an unbounded substring test** (`tickers.py:105`). The ticker
  rule 25 lines above correctly uses `(?<![A-Za-z0-9])...(?![A-Za-z0-9])`; the name rule is a bare
  `name_upper in upper`. "Target" matches *"Fed officials target 2% inflation"*.
- **The LLM already classifies headlines as `"macro"` and the value is stored** (`storage.py:122`)
  **but the hot-flag condition never consults it** (`:130` checks only `is_material`). One
  index-level headline naming three megacaps sets three separate 2-hour BUY-compression flags.
  Nearly-free fix — the data is already there.
- **Regime failure defaults disagree**: market-data falls back to `choppy` (conservative,
  deliberate) but `get_last_regime()`'s bare-except and decision-engine both resolve to
  `neutral` — the *most permissive* state. Failure loosens gating exactly when visibility is lost.
- **CAPE's staleness flag is structurally unreachable**: `reading_date` is taken from the Atom
  feed's daily-refreshed `<updated>` on a *monthly* series, so `age_days` stays ~0 forever and
  `stale = age_days > 45` can never fire.

### Verified CLEAN — several were specifically hypothesised as bugs and traced correct

1. **Earnings surprise sign flip: NO BUG.** `(act − est)/abs(est)*100` handles loss-makers
   correctly — est −0.50 → act −0.20 gives **+60% (beat)**; −0.50 → −0.80 gives **−60% (miss)**.
   A naive `/est` would invert both. Zero-estimate guarded.
2. **The hot-news gate IS direction-aware** — compression fires only on `sentiment_label ==
   "negative"` AND `fused > 0.5`. The "any material news compresses" hypothesis is false.
3. **LLM classification fails SAFE** — failure → `is_material=False` → no flag. URL dedup runs
   *before* classification (the already-fixed `BUG-NEWSCLASSIFY-REPEATCOST`).
4. **The OGE executive-branch filter is correct** (`congress.py:111`) — properly excludes the
   ~85% of the feed that isn't congress.
5. **No DateTime-vs-bare-date bug** in these subsystems (all construct explicit UTC bounds).
6. **congress/insider date lag is genuine filing delay, not a broken sync** — both wrote rows
   within 3 days. Checked before flagging.
7. **decision-engine ↔ market-data regime agreement is correct.**
8. **39 `cape_readings` rows is expected, not thin** — monthly series with daily-stamped upserts.
9. **EDGAR and Alpaca news paths are exempt from ticker over-matching** (CIK exact-lookup and
   source-native tags) — only PR Newswire and Business Wire use `extract_symbols`.

---


## Deep Audit #5 of 6: Short Squeeze / Option Expiry (2026-08-05)

**Scope**: documentation-only. Tracked as **Tier 265**, 12 entries (11 findings + 1 CLEAN
reference).

### Headline: a data outage silently mass-reverts every bearish_puts watch

Three links, all confirmed by direct code read:

1. `check_gamma_unwind_alerts()` has `if not candidates: ... return` at `scheduler.py:2415-2417`
   — which fires **before** the `setex` write of `stockai:bearish_puts_watch`. A no-candidate
   cycle never refreshes the cache, so it simply expires on its 6h TTL.
2. `check_squeeze_watch_reverts()` builds `_bearish_by_symbol` from
   `_json.loads(_rc.get("stockai:bearish_puts_watch") or "[]")` inside a `try/except` that sets
   `{}` — so a missing/expired/unparseable key is **indistinguishable from "the scan ran and
   found nothing."**
3. The revert branch does `if bp is None or bp.get("dominant_side") != "puts": metric_faded = True`.

Net: the gamma job's yfinance calls all failing (rate limits — its own most fragile endpoint)
causes the next 60-second revert cycle to mark **every** un-reverted `bearish_puts` watch as
faded, email every owner, and set `reverted=True`. Revert is **one-shot**, so the user must
manually re-add. This fires on infrastructure failure, not a market event. Separately, the scan
is a *narrowing* 3–5 DTE window filter, so a watch added at 5 DTE naturally exits the window in
two days and auto-reverts as "faded" purely from calendar drift.

Verified against production Redis: the key currently **exists** with a 56-minute TTL, so this
hasn't fired yet — it is a live latent risk, not a past incident.

### Short-interest age is never captured or checked anywhere

Grep across `services/`, `shared/` and `frontend/src` for `dateShortInterest` /
`short_interest_date` / `shortInterestDate` returns **zero hits**. No settlement date is captured
at ingest, and none of the four consumption sites (alert path, Redis screener, DB screener,
fundamentals endpoint) applies any age bound.

Exchange short interest settles ~2×/month with a 1–2 week reporting lag, so a reading can
legitimately be ~6 weeks old. Concrete failure: a stock's real short interest collapses 22% → 6%
after a covering wave; yfinance still reports 22% for up to ~3 weeks; the stock rallies 4%
intraday; the alert fires *"22% of float short, shorts may be forced to cover"* with a game plan
— when the squeeze fuel is already gone. This is the **one place** the subsystem's otherwise
excellent honesty framing is undermined — not by wording, but by unstated data age.

### Also found

- **An 11th instance of `BUG-DELISTED-GENERATION-BLIND`** — the squeeze screener
  (`routes.py:1982`) uses `Stock.active.is_(True)` with no `Stock.delisted` filter, so a
  delisted heavily-shorted name stays pinned at the *top* (results sort by short % descending).
  The prior 10-site sweep missed it. Reinforces the standing lesson that these sweeps must be
  re-run across every service rather than assumed complete.
- **A lapsed ranking refresh silently empties the "Prime Candidates" banner** — the screener
  joins rankings only within 7 days, and this repo has documented rankings going stale 7+ days.
  Fails closed and silently.
- **The 55% OI-concentration threshold is asymmetrically calibrated** — equity options carry a
  structural call skew, so 55% is near baseline for calls but genuinely selective for puts. One
  shared constant applied to two very different base rates.

### The production anomaly that traced to CORRECT code

`options_flow_snapshots` showed `neutral` sentiment with `max_cp = 10.00` — the *same* cap as
`strongly_bullish`, which looked like a broken classification. Traced numerically and it is
**correct**: `cp_ratio = min(call_vol / max(put_vol, 1), 10.0)` is computed independently of
`sufficient_put_vol = total_put_vol >= 100`, and every directional tier requires that guard.

| call_vol | put_vol | cp_ratio | sufficient | sentiment |
|---|---|---|---|---|
| 1000 | 99 | 10.00 | False | **neutral** |
| 1000 | 100 | 10.00 | True | strongly_bullish |

Those production rows are illiquid-options symbols correctly refusing to declare extreme
sentiment. Also killed before filing: `_SQUEEZE_MIN_SHORT_FLOAT = 15.0` was hypothesised as
possibly unreachable given short data is stored as a *fraction* — verified that **19 real stocks
clear the 15% bar** (31 clear 10%), so the threshold is well-calibrated.

### Verified CLEAN

The two independent sentiment-ladder ports (`options_flow_snapshot.py` and `routes.py`) are
numerically identical and have **not drifted**; no NaN/Infinity can reach `json.dumps` anywhere
in the options math (`max(put_vol,1)`, `max(openInterest,1)`, `df.fillna(0)` on every chain
read) so the `updown_vol_ratio` bug class does not recur; the squeeze alert **does** correctly
implement the `BUG-VOLANOM-STALEMARKET` market-hours fix (whole-scan short-circuit + per-row
HK/US split); its dedup is genuine transition-only firing via a per-user Redis set diff, so it
cannot re-fire every minute on a sustained move; fraction-vs-percent unit handling is consistent
at every site checked; revert logic **is** genuinely OR and genuinely one-shot with
`reverted=True` set only when `sent_ok`; the bearish-puts 3-of-3 checks are genuinely
independent and correctly use `is False` identity checks; rate-limit discipline is sound;
`SqueezeWatch` routes are correctly user-scoped.

**The honesty framing across this subsystem is consistently excellent** — the gamma email
explicitly disclaims being a real GEX calculation, the frontend states in bold *"This is never a
guarantee the stock won't recover"*, and the squeeze email reports a *"MEASURED setup, not a
prediction the move continues."* The unstated short-interest age is the sole exception.

---


## Deep Audit #6 of 6: Recommendations and Alerts (2026-08-05) — SERIES COMPLETE

**Scope**: documentation-only. Tracked as **Tier 266**, 12 entries (10 findings + 2 reference).

### Headline 1: a dead whitelist entry is the dominant suppressor of the entire alert system

`scheduler.py:3405` — `if de_verdict not in ("BUY", "SCALE"):`

Verified by grep: decision-engine returns exactly **BUY / HOLD / SKIP / BLOCKED**. The string
`"SCALE"` appears **zero times** anywhere in `services/decision-engine/src/`. So the whitelist's
second entry is dead and the gate reduces to `verdict == "BUY"` — which silently makes **HOLD** a
rejection. But DE assigns HOLD deliberately as a *near-miss* (score ≥ min_score − 2), i.e.
exactly the marginal-but-real candidates a whitelist shaped like this was evidently meant to
admit.

Production, 48h: **4,824** alerts passed the full 5-layer conviction gate → **4,782** rejected by
DE → **27** fired. Two independently-tuned gates stacked with contradictory bars and nothing
asserting their intersection is non-empty.

**A framing correction of my own, worth recording**: the raw `signal_alert.skipped` count of
**61,863** initially read as a runaway per-minute loop. It isn't — `check_signal_alerts()` is
called from `_run_market_refresh()` (`scheduler.py:527`) on cron refresh bursts, ~240 runs in
48h ≈ 258 skips/run across all subscribed (symbol, horizon) pairs, dominated by the benign
`prev == current` no-op. The real finding was *underneath* that number, not the number itself.

### Headline 2: the health page asserts health it cannot verify

Five alert functions make **zero** `_record_job_status()` calls (verified by AWK per function):
`check_price_alerts`, `check_signal_alerts`, `check_earnings_reactions`,
`check_earnings_impact_alerts`, `check_macro_reaction_alerts`. `check_top3_conviction` makes 7 —
the correct pattern exists in the same file.

The health page compounds it: `admin-health.tsx:36` **declares** `price_alert_check` with
`maxAgeDays: 1`, promising staleness detection it structurally cannot deliver; and both
`errorCount` and `staleCount` (`:185-186`) filter over jobs that *reported*, so a never-reporting
job contributes 0 to each and `:213` renders **"All healthy."** A total price-alert outage would
show green indefinitely. There is no second detection path either — the DQ family derives from
the same rows.

### Other findings

- **Dedup keys burned before the send** — `check_top3_conviction` sets its 6h key at `:177` then
  sends at `:180`, so one SMTP hiccup suppresses the alert for 6 hours. Only 2 of 9 alert jobs
  get this ordering right, despite the correct pattern existing in the same file.
- **Global `any_sent` flag causes cross-user suppression** — the flag is global across the
  recipient loop while the sent-marker is per-*event*, so once any one recipient succeeds the
  remaining users are never retried.
- **The budget cutoff is a deterministic starve** — both loops iterate a `set`, whose order is
  fixed *within* a process but varies *across* processes. The same ~80 symbols get zero coverage
  every cycle until restart, then a different 80. Worst diagnostic shape: it moves on deploy
  instead of reproducing.
- **The per-recipient isolation fix never propagated** — AUD256 fixed `send_morning_digest` and
  `send_premarket_brief`; the other 7 alert loops still lack it. (Mitigating: `send_email()`
  catches broadly, so plain SMTP failures don't abort — the exposure is *builder*-level errors
  that run before that guard.)
- **The guide omits the dominant suppressor** — `alerts-guide.tsx` documents the 5-layer
  conviction gate accurately but has **zero** mentions of the DE gate that rejects 99.4%.

### The one genuinely strong area, verified rather than assumed

Given Audit #1 established BUY signals lose money on every horizon, I specifically checked
whether any alert email implies a BUY is likely profitable. **None do.**
`send_top3_conviction_email` reports the real measured win rate *and* sample count, explicitly
says "NOT a prediction", and warns that an empty scan means the bar is working. The morning
digest shows real 30-day win rates with red colouring below 38%. Every builder carries "Not
financial advice"; ~15 carry explicit "not a prediction" framing. Sign handling is correct
throughout the email layer.

**This is a deliberate contrast with Audit #1**, where the signal-accuracy *page* was found to
flatter a losing system. The email layer does not. The Top-3 alert's only defect is *inherited* —
it gates on a measured win rate computed from the outcome fields Audit #2 corrupts — and the
email code itself should not be changed.

---


## Audit Series Summary (Tiers 261–266, all 2026-08-05)

Six sequential documentation-only audits, 74 tracker entries total. Recurring themes:

1. **A correct pattern exists in the same file and was not propagated to its siblings.** Seen at
   least four times: `calibrate_ta_weights` gated but `calibrate_conviction_weights` not (263);
   `gate_harness`'s promotion margin not applied to the 403-cell grid (263); dedup-after-send in
   2 of 9 alert jobs (266); per-recipient isolation in 2 of 9 (266).
2. **Bug-class sweeps declared complete were not.** The delisted-filter sweep covered 10 sites;
   an 11th was found in the squeeze screener (265).
3. **Absence of data is repeatedly treated as evidence.** A missing Redis key reads as "the setup
   faded" (265); a never-reporting job reads as "healthy" (266); zero censored rows read as
   "censoring works" (261).
4. **The corrupted `SignalOutcome` writeback (262) propagates furthest** — into entry-gate tuning
   and ML features (263), and into the Top-3 alert's win-rate gate (266). Fixing it resolves
   several downstream findings for free, but every `gate_threshold` result then needs re-running.

---


## Design Review (forward-looking) 2026-08-05 — and a Correction About Its Evidence Base

A forward-looking design review against 5 user goals (better signals; "don't buy from the top but
when it starts to rally"; better prediction; confidence/trust; better return) was produced at
`docs/DESIGN_REVIEW_FORWARD_2026-08-05.md`.

**Important caveat on that document — several of its headline claims were REFUTED against
production and must not be trusted:**

The review was produced without SSH access and read a **stale local** environment, then reported
those observations as facts about the live system. Refuted by direct production query:

| review claim | production reality |
|---|---|
| "`bearish_pillars_active` doesn't exist as a DB column" | **column EXISTS** in `signal_outcomes` |
| "`calibration.py`/`outcomes.py` don't exist in the container" | **both present** (pre-reboot) |
| "`tune_history` has zero rows for `min_pillars_for_sell`" | **4 rows exist** (validation_n 238/222/180/56) — matching Audit #3 exactly |
| "the SELL gate attempts never ran" | they **ran and legitimately failed** validation |
| "`news-intelligence` was never built; `ml-prediction` unhealthy" | **both healthy** |

**Its analytical work on stored outcome data is a separate matter and may still hold** — that
analysis is environment-independent. The most actionable hypothesis it produced, **not yet
verified against production** (the instance went down mid-verification):

> Bucketing resolved BUY outcomes by entry distance below the prior 20-day high, **"within 5% of
> the high" was the best bucket in all four horizons independently** and the only one near
> positive EV — while `sr_context='breakout'` was among the *worst*. If true, this materially
> reframes goal 2: the winning entry is **not** a deep dip, it is a shallow pullback in an intact
> uptrend, and chasing a confirmed breakout is the losing trade.

It also reported `volume_z >= 1` as the **worst** BUY bucket (contradicting the VOLUME pillar and
RVOL's design intent). **Both need re-running against production before being acted on.**

**Lesson (a repeat of the one already recorded for the closing-sweep pass in the duplicate-code
audit):** a subagent without production access will confidently report local-environment
observations as live-system facts. Always re-verify any environment-dependent claim directly
before recording or acting on it — and prefer having the agent state what it could NOT verify.

---

