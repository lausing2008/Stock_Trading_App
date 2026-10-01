# M15 — portfolio concentration limits under synthetic positions

**2026-10-01.** Measurement, not remediation. Nothing in production changed; no threshold was
altered. Tests: `services/market-data/tests/test_portfolio_concentration.py`.

## Why synthetic, and why now

The register's own note is *"test limits now with synthetic positions; do not wait for trades to
test a hard limit."* M15 had been sitting in a "collecting" posture, which was wrong — waiting
for live exposure to accumulate means the first time a hard limit binds is in production, on
real positions. **A limit that has never bound is a limit nobody has evidence works.**

These call the **real** `_open_paper_trade` — the same function organic entries and conditional
orders both route through — against a real database with hand-built open positions. The cap
arithmetic is not reimplemented; reimplementing the logic under test is how a test ends up
asserting its own copy of a bug.

## What works

| Behaviour | Evidence |
|---|---|
| Existing exposure binds the sector cap | 3 open positions at the cap → 4th refused with `sector_cap` |
| Risk-reducing exits are not gated by entry caps **on the tested path** | In `_monitor_positions` the cap names are a **warning log**; zero `return`/`continue` is guarded by one |

**Scope of the exit result, stated precisely.** It is verified for `_monitor_positions`, the
scheduled exit path. It is *not* a claim about every exit route: source inspection of one
function cannot establish that manual exits, liquidation, conditional-order exits or broker-side
closes are equally unconstrained. Those remain unverified.

## Findings — five gaps in what the caps can see

**1. Concurrent entries within one scan can jointly breach a sector cap. — FIXED, tracked as
[M15-DEFECT-CONCURRENT-CAP](../incidents/concentration-cap-stale-snapshot.md).** Both candidates
were sized against the *same* pre-fetched snapshot, so neither saw the other: two entries opened
**20.02% of equity** in one sector against a **15%** cap. Affected organic entries *and*
conditional orders (two independent writers); the backtest has its own declared subset and is
unaffected. Fixed by atomic exposure reservation under a portfolio row lock, with the defect
witness preserved. Re-querying per candidate would have narrowed the window without closing it.

**2. Pending and unfilled orders are invisible.** The snapshot query selects
`PaperTrade.stage == "open"` and nothing else, so a working conditional order or an accepted
broker order not yet filled contributes **zero** to every concentration check.

**3. A missing mark values a position at its entry price.** `_best_price` falls back to
`entry_price`. In the fixture — entry 100, live 200 — the value used when the mark is missing is
**100**.

**The 50% figure is a property of that fixture, not an estimate of production exposure.** The
understatement equals the position's unrealised gain, so it is zero for a flat position and
unbounded for a large winner; nothing here measures the real distribution of stale marks or
unrealised gains in production. The finding is the *mechanism* — concentration is understated
exactly when a winner has grown into the risk the cap exists to limit — not a magnitude.

**4. Concentration sums local currency without conversion.** A 300,000 HKD position is summed
raw against USD equity — ~3x equity when its true weight is ~38%. **Latent, not live:** a
portfolio carries a single `cfg["market"]`, so US and HK holdings do not currently share a book.
The arithmetic is nonetheless currency-naive and would be wrong the moment one did.

**5. No ordered-versus-filled distinction, and no option/assignment representation.**
`PaperTrade.shares` is the only quantity field, so the model cannot express "ordered 100, filled
30" and cannot reserve the unfilled remainder. `PaperTrade` carries no option or assignment
columns, so shares delivered by assignment reach the caps only if something writes an ordinary
open trade for them.

## Treatment of the remaining four

Finding 1 was a defect and is fixed. The other four are not defects of the same kind — each
needs the eventual execution model rather than a competing one invented now:

| Finding | Treatment |
|---|---|
| Pending orders invisible | Include outstanding entry commitments. Partial fills transfer exposure from reserved to held without double counting; cancellations release only confirmed unfilled quantity. Shares the order model, not a parallel one. |
| Missing marks fall back to entry price | Distinguish **unvaluable** (no number; fails closed — and unreachable from the live path today) from **fallback** (a number from a substitute source, which is not current exposure and is currently permitted). **Mechanism built** (`require_fresh_marks` → `exposure_stale_mark`), **off by default**, and now measured in shadow via `paper.exposure_stale_mark_shadow` so enabling it rests on a measured block rate. Protective exits stay available either way. |
| No FX conversion | The single-currency assumption is now enforced in one place rather than assumed everywhere. Mixed-currency portfolios require timestamped FX conversion first. |
| No order/assignment representation | A prerequisite for broader broker/options automation. Assignment's stock, cash and collateral consequences must be modelled explicitly. |

The tests pin **current** behaviour for all four, so a later fix has a failing test to flip.

## Two errors in this probe, both caught before they became findings

- **A function-body slice ran 1,300 lines past its target.** Taking the next `\ndef ` after
  `_monitor_positions` swallowed `_scan_for_entries` whole, so the probe reported the entry
  scan's caps as though they sat in the exit path — it would have claimed exits were gated by
  entry caps, which is both false and alarming. Fixed to slice on real top-level boundaries.
- **The first pending-order fixture was inconclusive**: both arms returned `sector_cap`, because
  risk-based sizing meant the positions were not the sizes assumed. Replaced with the decisive
  fact — the provenance of the snapshot query — rather than a fixture that happened to agree.

## Incidental

`PaperTradeDecisionLog.id` gained a `BigInteger().with_variant(Integer, "sqlite")` so the real
entry path can run against the SQLite used by real-database tests. Production stays BIGSERIAL.
