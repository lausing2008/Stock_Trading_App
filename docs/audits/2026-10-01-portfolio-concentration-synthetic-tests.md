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
| **Risk-reducing exits are not gated by entry caps** | The cap names in `_monitor_positions` are a **warning log**; zero `return`/`continue` is guarded by one |

The exit result is the one that most needed checking, and it is correct: an account over its
concentration limit can still reduce risk.

## Findings — five gaps in what the caps can see

**1. Concurrent entries within one scan can jointly breach a sector cap.** Both candidates are
sized against the *same* pre-fetched snapshot, so neither sees the other. Measured: two entries
opened **20.02% of equity** in one sector against a **15%** cap — each individually legal,
jointly over. `prefetched_open` is captured once before the candidate loop (AUD19-PERF2, to
avoid a query per candidate); that is a real performance fix, and the cost is that within one
cycle the caps run against a stale view.

**2. Pending and unfilled orders are invisible.** The snapshot query selects
`PaperTrade.stage == "open"` and nothing else, so a working conditional order or an accepted
broker order not yet filled contributes **zero** to every concentration check.

**3. A missing mark values a position at its entry price.** `_best_price` falls back to
`entry_price`. Measured: entry 100, live 200, value used when the mark is missing **100** — a
**50% understatement**, arriving exactly when a winner has grown into the risk the cap exists to
limit.

**4. Concentration sums local currency without conversion.** A 300,000 HKD position is summed
raw against USD equity — ~3x equity when its true weight is ~38%. **Latent, not live:** a
portfolio carries a single `cfg["market"]`, so US and HK holdings do not currently share a book.
The arithmetic is nonetheless currency-naive and would be wrong the moment one did.

**5. No ordered-versus-filled distinction, and no option/assignment representation.**
`PaperTrade.shares` is the only quantity field, so the model cannot express "ordered 100, filled
30" and cannot reserve the unfilled remainder. `PaperTrade` carries no option or assignment
columns, so shares delivered by assignment reach the caps only if something writes an ordinary
open trade for them.

## Deliberately not fixed here

Each finding is a design decision with trade-offs — re-querying per candidate undoes a
deliberate performance fix; reserving pending exposure needs an order model that does not exist;
FX conversion needs a timestamped rate source and a stated base currency. Shipping any of those
inside a measurement task would change live entry behaviour under cover of "adding tests". The
tests pin **current** behaviour, so a later fix has a failing test to flip.

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
