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
