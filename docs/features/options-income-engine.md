# Options Income Engine (T398)

Systematic covered-call / cash-secured-put engine — screening + autonomous paper trading.
Built 2026-09-15/16. Page: `/options-income`. Guide: `/options-income-guide`.

---

## T398-OPTIONS-INCOME-ENGINE — the engine (Built 2026-09-15)

**Scoping.** The user asked for "both" when offered a screening tool *or* an autonomous
portfolio, so it is two halves sharing ONE source of truth for "what is a good income trade":
`rank_income_candidates()` is used identically by the research endpoint a human reads and by
the engine that opens real (paper) positions. There is deliberately no second, independently
drifting copy of the selection logic.

**Data source.** Reads each symbol's own latest archived EOD chain from `option_chain_history`
(the OPTHIST-1 archive) — never a live per-symbol fetch, matching this app's own
rate-limit-amplification guard (`docs/incidents/yfinance-rate-limit-amplification.md`).

**Strategy modelling — both close AT EXPIRY, never rolled:**
- `COVERED_CALL` is a synthetic buy-write: shares assumed bought at entry
  (`collateral_reserved = underlying_entry_price * 100 * contracts`), sold via assignment at
  strike if the close is above it, otherwise liquidated at the expiry-day close.
- `CASH_SECURED_PUT` reserves `strike * 100 * contracts`; if assigned, the shares are marked to
  market and liquidated immediately rather than held.

This keeps exposure bounded — no open-ended stock inventory drifting across future expiry
cycles — while still producing each strategy's real economics. It also means this is NOT
equivalent to an actively-managed options desk, and the guide says so explicitly.

**Separate tables, deliberately.** `OptionsIncomePortfolio` / `OptionsIncomePosition` /
`OptionsIncomeEquityCurve` are NOT a new `trading_style` on `PaperPortfolio`. That table's
style axis (SHORT/SWING/LONG/GROWTH) describes stock-entry aggressiveness and is validated
against that closed set in ~8 places across the codebase; options selling is not a stock entry
style (no ATR game plan, no shares, no directional stop/target), so reusing it would mean
either loosening every one of those checks or silently mis-modelling the strategy.

**Testing.** The settlement/yield math (`settle_position_economics`, `_score_contract`,
`quality_score`, `leverage_factor`) is PURE — no DB access — matching
`compute_options_game_plan()`'s own established precedent, so it gets real behavioural tests
rather than MagicMock theatre. Sabotage-verified: removing the covered-call assignment cap at
strike is caught immediately.

**Two defects caught by deploying it, not by review:**
1. `OptionsIncomePosition` declared its `expiry` index twice (column `index=True` PLUS an
   identical explicit `Index()` in `__table_args__` under the same auto-derived name), which
   crashed `create_all()` with `DuplicateTable` and put market-data in a restart crash loop.
   The whole DDL transaction rolled back each time, so the table never actually persisted.
2. No per-position concentration cap existed. Measured live on a $50k portfolio: ONE AMD
   cash-secured put needed $50,000 of collateral (one contract on a $500 stock) and consumed
   the entire account, capping the book at a single position regardless of `max_positions`.
   Added `max_collateral_pct_per_position` (default 25% of initial capital).

---

## T398 AUDIT — six candidate-selection defects found by auditing live output (2026-09-16)

The user asked whether the engine was "accurate and trustful". Auditing its REAL production
output against live prices (not reading the code) found the mechanics sound but candidate
SELECTION broken in six ways. Every finding below was confirmed against real data.

**1. AUD-T398-DTEFROMSTALE — the DTE filter silently stopped enforcing itself.**
`dte = (r.expiry - as_of).days` measured days-to-expiry from the CHAIN's date, not today. A
position is opened NOW, so the real holding period is today→expiry. On a 5-day-stale chain
every candidate advertised as "14 DTE" was really a **9-day trade** — below the engine's own
documented floor. Now measured from today.

**2. AUD-T398-STALEMONEYNESS — contracts selected as OTM were already ITM.**
Strike and delta come from the `as_of` chain; if the underlying moved since, a "0.30 delta OTM"
pick can already be in the money. **This already happened in production**: a 500-strike AMD put
was opened as a "-0.35 delta" trade with the stock at **493.41** — ITM at entry, carrying
roughly double its recorded risk. 5 of 26 candidates were already ITM at audit time. Moneyness
is now re-checked against the CURRENT price.

**3. AUD-T398-EARNINGSWINDOW — no earnings awareness at all.**
Selling premium across an earnings report inverts the "assignment is the minority outcome"
premise these strategies rest on, and is often exactly WHY the richest premium is rich. The
audit window happened to be clean (zero earnings for the 13 names through 2026-10-20) — that
was timing luck, not design. Contracts whose life spans a known report are now skipped, via ONE
universe-wide query, not one per symbol.

**4. AUD-T398-THINCUSHION — "OTM" by a rounding error.**
The engine surfaced a QQQ put **0.08%** out of the money and an SPY put **0.18%** out as though
they were ~0.33-delta trades. Those are at-the-money in all but name. Added a 2% minimum
cushion floor.

**5. Ranking by raw yield is adverse selection BY CONSTRUCTION.**
The highest-yielding contract in the universe is highest-yielding precisely because it carries
the most risk. Measured: the top-ranked candidates were simply the highest-IV names (TQQQ 54%,
AMD 48%, PLTR 45%). Replaced with `quality_score()` — a transparent 0-100 blend of yield (40%),
OTM cushion (40%) and open interest (20%), each saturating at a documented point so no single
component runs away. **Cushion is weighted equal to yield on purpose**: for a premium SELLER,
distance-to-strike is what actually prevents the bad outcome.

Critically it drives the **per-symbol pick too**, not just the final sort — using yield for
either re-introduces the bias one level down. That single change materially improved selection:

| Symbol | Before (yield-picked) | After (score-picked) |
|---|---|---|
| AMD | CSP $495 — 1.8% cushion, OI 66 | CC $560 — **11.1%** cushion, OI 1,721 |
| TSLA | CSP $350 — 1.9% cushion, OI 1,073 | CC $390 — **9.4%** cushion, OI 1,897 |
| META | CSP $630 — 6.0% cushion, OI 100 | CSP $600 — **10.5%** cushion, OI 5,449 |

**6. AUD-T398-LEVERAGEPENALTY — leveraged ETFs win any yield measure for free.**
A 2-3x ETF carries structurally richer premium because the underlying moves 2-3x as hard, so
TQQQ took the **#1 and #5** slots purely on that effect. The cushion term does not correct for
it either: a 10% cushion on a 3x product is roughly a 3.3% move in the underlying index. The
final score is now scaled by the reciprocal of the leverage multiple (TQQQ ×0.33, QLD ×0.50),
so a 3x product needs ~3x the headline yield to rank alongside an unleveraged name. Applied to
the FINAL score because leverage inflates yield and deflates the meaning of cushion
simultaneously. **Effect: TQQQ's top candidate fell from rank #1 (score 100.0) to #22 (33.3)**,
and all four leveraged candidates now sit at ranks 22/23/24/26.

**What the scores are NOT.** `quality_score` is a transparent heuristic for ORDERING candidates,
not a validated predictive edge — the engine has zero resolved outcomes so far, and the guide
says so. The normalisation points (`_Q_FULL_*`) are documented judgement calls.

---

## T398 — information & alerting surfaces (2026-09-16)

Three things were computed correctly but invisible, which is its own failure mode:

- **`★ Top Picks` cards** above the candidates table, ranked by quality score.
- **Staleness banner** (amber) — `/candidates` now returns `data_as_of` / `days_stale` /
  `is_stale` as first-class response metadata, because a stale chain degrades EVERY candidate
  at once and therefore belongs on the page, not buried per-row. States plainly that premiums
  are as-of an older date while strikes/cushions ARE re-checked live.
- **Assignment-risk banner** (red) — `/positions` now prices OPEN positions against the LIVE
  underlying and returns `live_price` / `is_itm` / `cushion_pct` / `days_to_expiry`. Previously
  the page showed entry-time figures only, so a put sitting well below its strike looked
  identical to a safe one.

Backend-generated in-app notifications were considered and NOT built: `AppNotification` is
currently a frontend-driven feature with no backend writer anywhere in the codebase, so the
alerts live on the page rather than inventing a parallel delivery path.

---

## Refresh cadences (measured 2026-09-16)

| Layer | Cadence | Drives |
|---|---|---|
| Option chain archive | Daily 18:45 ET (captures *yesterday*) | premium, delta, IV, OI |
| Options income step | Daily 19:00 ET | settlement + new entries |
| Live price cache | Every 1 min, market hours only | current price, cushion, ITM checks |
| Page polling | 60s positions / 5 min candidates | display |

**The asymmetry matters**: prices are near-real-time, premiums are up to a day old. That is why
the moneyness re-check exists — cushions are honest even when premiums are not.

**Quota is not the constraint.** UW's own authoritative headers (2026-09-16):
`daily_limit 120,000`, steady-state usage **~9-10k/day (~8%)**. A full 29-symbol chain refresh
costs **29 requests**; hourly through market hours would be ~189/day = **0.16% of quota**. The
daily cadence exists because the archive was built for historical backtesting (where EOD is
exactly right) and the income engine inherited it — not because of any rate limit.

An intraday refresh was scoped and deliberately deferred: writing intraday partial chains into
`option_chain_history` would contaminate the LEAPS backtest tooling, which assumes settled EOD
data. Doing it safely needs a separate live-fetch path with its own cache.

---

## Guides

- `/options-income-guide` (Learning, advanced-tier) — covered calls and cash-secured puts from
  first principles with worked dollar examples, how the engine picks trades, a step-by-step
  page walkthrough, risks, and a pre-creation checklist. Includes an "is this actually passive
  income?" callout: hands-off is not the same claim as risk-free.
- **Fidelity execution steps** were CORRECTED after real user feedback. The first version
  assumed an "Action: Sell to Open" dropdown and a "Buy Write" strategy selector inside the
  chain; the user reported their actual chain shows only Bid/Ask cells per strike. Rewritten
  around the universal mechanic — **clicking a strike's BID starts a sell order, the ASK starts
  a buy** — with "Buy Write" explained as a concept plus an always-available two-step fallback.
  Lesson: do not write pixel-level walkthroughs of third-party UIs that cannot be verified.
- `/option-trading-guide` gained a **bid/ask section** with a worked example on one call and
  one put, covering all four buy/sell combinations. Prompted by a real user question that had
  it backwards ("is covered call = ask and CSP = bid?"). **Both are SELLS, so both use the
  bid** — which price applies depends only on buy-vs-sell, never on call-vs-put.

---

## T399 — the backtest that actually tested it, and the weights it corrected (2026-09-17)

The engine shipped with **zero resolved outcomes**. Every claim about it — that the strategy
makes money, that `quality_score` ranks anything useful, that 40/40/20 and the 2% cushion floor
were right — was untested. Waiting for live paper trades would take months and sample exactly
one market regime.

### The harness (`backtest/options_income_backtest.py`)

Replays the engine over the chain archive: **724 trading days x 29 symbols, 2023-10-23 ->
2026-09-11**. First real run settled **417 trades with zero dropped**.

**It replays the REAL selector.** `rank_income_candidates()` gained a `point_in_time` parameter
rather than the backtest forking it — there is deliberately no "backtest version" to drift
against the live one, the same single-source-of-truth discipline the engine already uses between
its screener and its autonomous half. A backtest of a parallel implementation measures the
parallel implementation.

**Three lookahead guarantees**, because a backtest with lookahead is worse than none (this repo
has the scar tissue: `docs/incidents/backtest-wall-clock-and-lookahead-bugs.md`):
1. `_chain_as_of_on_or_before()` resolves each symbol's chain strictly `<=` the entry date.
2. Selection prices are that date's own close, never a later one.
3. The only post-entry data touched is the settlement close ON the expiry date — the outcome,
   not an input.

Trades with no close at/near expiry are **dropped and counted**, never guessed.

### What it found

| | |
|---|---|
| Trades | 417 |
| Win rate | 69.5% |
| Assignment rate | 23.7% |
| Total P&L | +$183,921 |
| Avg return on collateral | +1.371%/trade |

**A suspicion that measurement disproved.** The obvious worry was that the profit was just
"stocks went up 2024-2026" — a synthetic buy-write holds 100 shares, after all. Decomposition
says no: covered calls earned $196,093 of premium against a **-$51,467** underlying
contribution, CSPs $114,062 against **-$74,768**. The underlying was a **-$126k drag** and ALL
the profit is premium. That is what a premium-selling strategy is supposed to look like — the
caps and assignments are exactly where the upside goes.

**It does lose money.** 2024 H1: **-$14,759** (57.7% win). 2025 H1 was nearly flat (+$8,172).
Four of five half-years positive, one negative — a realistic profile, not a straight line.

**The important negative finding: `quality_score` did not work.** It failed to order win rate at
all — 85+ scored 67.9%, 70-85 scored 71.7%, 50-70 scored 66.2%. But its components each carried
real signal: cushion predicted assignment monotonically (66.7% -> 19.4%), open interest
predicted win rate (58.1% vs 70.5%), and yield predicted assignment (10.4% -> 28.7%, confirming
the adverse-selection thesis — the 15-25% yield band actually **lost $29,902**).

### Re-deriving the weights (`backtest/options_income_weights.py`)

**40/40/20 -> 25/75/0**, derived from **50,787 settled contracts** rather than chosen.

| Held-out slice | Old 40/40/20 | New 25/75/0 |
|---|---|---|
| Mean return on collateral | 2.33% | **3.16%** |
| Win rate | 65.9% | **69.0%** |
| P&L (same 126 trades) | $89,191 | **$126,585** |

**+0.83pp out of sample** against a 0.10pp required margin.

Discipline that makes this an edge rather than a fit:
- **Chronological** split (train 2024-01 -> 2025-11, test -> 2026-08). Random splits leak the
  future into training on time-series data.
- The objective is what the engine DOES: re-rank each date, take the top `max_per_date` (the
  live daily cap), measure the realised return of exactly those. Optimising correlation would
  reward ordering trades the engine never opens.
- The incumbent is evaluated on the SAME held-out slice.
- A margin is required before adopting, mirroring `_passes_promotion_margin` in gate_harness.py
  — which exists because `BUG233-BACKTESTHARNESS-COINFLIP` promoted a coin flip once.
- Saturation points (`_Q_FULL_*`) deliberately NOT searched; fitting those too is how a study
  this size starts fitting noise.
- The pool is de-biased: `rank_income_candidates(all_contracts=True)` was added specifically so
  the study re-ranks contracts the incumbent score never picked. Without it the best-per-symbol
  reduction — itself decided by `quality_score` — would pre-filter the pool being used to judge
  those very weights.

**Why cushion dominates.** Every one of the top 8 train configurations put cushion at 0.65-0.95.
The equal weighting was itself the bug: yield and cushion push assignment risk in OPPOSITE
directions, so weighting them equally made them cancel — which is precisely why the old score
could not order win rate.

**Why liquidity went to ZERO**, stated plainly so nobody "fixes" it back (and locked by a test):
open interest is **negatively correlated with cushion** in this pool — OI>=2000 averages 8.13%
cushion, OI<2000 averages 11.18% — so weighting liquidity pulls selection toward THINNER cushion
and fights the strongest signal. Adding even 0.05 cost 3.16% -> 2.40% out of sample. This does
NOT mean illiquid contracts are safe: that risk is handled by the hard
`_INCOME_MIN_OPEN_INTEREST` floor, which is a **filter, not a weight**.

**A discipline call worth keeping.** Cushion-only (0/100/0) scored slightly BETTER on the
held-out slice (3.20%). It was NOT adopted: picking the configuration that won the held-out
slice would turn that slice into a training slice, defeating the split entirely. The train
winner was kept.

### A live bug the backtest surfaced

**AMD was not in the `stocks` table at all** — the only symbol of the 13-name income universe
missing. Candidate generation worked (chains key on the symbol string, live prices come from
the Redis cache), but settlement needs `stock_id` to look up the close, so the open **$50,000**
AMD cash-secured put had `stock_id = NULL` and could never have settled. The zombie-position
guard correctly detected and logged it but could not repair it, because the Stock row genuinely
did not exist. Added AMD via `add_stock()`'s own logic (752 daily bars backfilled) and repaired
the position's `stock_id`.

### What the numbers are still NOT

No slippage, no commission, no market impact, no EARLY assignment (American options can assign
any time, especially calls before a dividend — settlement here is expiry-only, exactly like the
live engine). Fills are assumed at the quoted bid. These are the same simplifications the live
engine makes, so the backtest measures **the engine as built**, not what a real brokerage
account would have returned. The live engine still has zero closed positions.
