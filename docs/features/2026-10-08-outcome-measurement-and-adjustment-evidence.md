# Outcome measurement: sessions, adjustment evidence, the calculation contract, and the screen

**2026-10-07/08.** Tier 426. Built, tested, deployed.

This is the layer that will eventually answer whether any of the platform's readings are worth
anything. It records what was concluded about an issuer on a given day and scores it against
what the price did over the next 5, 20 and 63 **trading sessions**. Four review rounds found
that nearly every figure it produced was wrong in a different way.

## What was wrong

| # | Defect | Consequence |
|---|---|---|
| 1 | A session counted as complete once 12:00 UTC passed | 08:00 ET — before the US market opens. A window could end on a bar that was forming or absent |
| 2 | The window started on the next **calendar** day | A midnight cutoff on 2026-06-01 skipped the whole of 06-01, imposing an undeclared one-session delayed entry |
| 3 | Excess return compared **different windows** | The stock from its reference close, the benchmark from the first subsequent close, then subtracted |
| 4 | Adjustment consistency was **asserted, not checked** | `adjustment_consistent=True` citing a check over the *setup* window — which, with no `adj_close`, passes vacuously and says so |
| 5 | **The resolver fingerprint covered only part of the calculation** | See below. This is the one that mattered most |

### Why #5 was the dangerous one

Each outcome carries an identity derived from the rules that produced it, so a corrected
resolver files a new result *beside* the old rather than rewriting it. But that identity was
computed from `resolve()` and two constants — **not** from session construction, price
selection, or adjustment verification.

So fixing #1–#4 would have left the fingerprint unchanged. The existing rows would have been
treated as already final, the corrected figures would never have been written, and the deploy
would have looked completely successful over silently stale numbers. **The safeguard would have
concealed its own corrections.**

`outcome_contract()` now names every input that can move a figure, including the session-hour
definitions and the holiday calendars — adding a holiday changes **past** windows, not only
future ones. A test fails when a known dependency is missing; a second proves each key is
actually digested rather than merely carried.

## Measured consequence

MU replay, cutoff 2026-06-01, reference close $970.85 as of 2026-05-29:

| Horizon | Descriptive | Excess | Versions |
|---|---|---|---|
| 1-5d | −2.24% → **−11.02%** | +0.44% → **−8.52%** | 4 retained |
| 1-4w | +18.88% → **+17.95%** | +20.31% → **+19.74%** | 4 retained |
| 1-3m | −1.25% → **−3.91%** | −2.49% → **−5.88%** | 4 retained |

The 1-5d move is almost entirely defect #2: the window now starts on 06-01 rather than 06-02.
Every earlier version is retained, marked superseded, and excluded from every summary.

## The honest interval where there was no answer

With the adjustment check actually performed, **every** outcome became
`UNRESOLVED_ADJUSTMENT_UNVERIFIED`. Measured: of 136,942 daily bars only 2,211 (1.61%) carry an
`adj_close` at all — 721 of those hold a real factor, so the column is meaningful where present,
but MU had it on 5 of 754 bars and none in this window.

The check was not weakened to produce numbers. The results were published as unavailable with
the reason stated, and the evidence was then collected.

## Adjustment evidence

Three requirements, made structural rather than documented:

1. **A missing `adj_close` is UNVERIFIED, not unusable.** `corporate_actions` plus a separate
   `corporate_action_coverage` claim establish the basis without the provider column.
2. **Split-adjusted price and total return are different measurements**, declared separately
   (`raw_price`, `split_adjusted_price`, `total_return`). A provider's adjusted close is none of
   them: yfinance adjusts for splits *and* dividends, so its factor moves identically for both
   and cannot be decomposed afterwards. It is used for the one thing it can honestly support — a
   **flat** factor rules out any action the provider recognised — and never converted into a
   basis of our choosing.
3. **Raw prices are never overwritten.** Actions, method name, source and retrieval time are
   stored; adjustment is derived at read time, so a method change re-derives under a new name.

Absence had to be made readable: "no actions recorded" is ambiguous between "none occurred" and
"nobody looked". The coverage row is written for the span **requested**, so an empty history
covers its window as fully as a full one.

**Ordinary corporate actions resolve** rather than being permanently unresolvable: a split is
applied; a dividend is excluded-and-disclosed under a price basis and included under a total
return; a benchmark-only action is applied to the benchmark alone.

### Pilot result (MU + SPY, 2025-01-01 → 2026-12-31)

6 cash dividends for MU, 7 for SPY, no splits. All three horizons now `RESOLVED` on
`split_adjusted_price`, with the in-window distributions (MU $0.15 on 2026-07-06; SPY $1.904 on
2026-06-18) excluded and disclosed on the outcome itself.

Deliberately **not** expanded to the universe: that is a separate decision with its own request
budget.

## The screen

`/stock-intelligence` ships **without returns being required**. The evidence summary, the
thirteen buckets, the frozen reference prices and the pending horizons are the research product
and none of them depend on a resolved figure. Each outcome states exactly what is missing —
*"Corporate-action adjustment evidence unavailable"* — from the backend's own state enum, not a
map kept in the frontend. Superseded outcomes are reachable, struck through, collapsed by
default, and excluded from every count.

## Publication safeguard

`invalidated_reason` lives on the **observation**. MU's observations 1–3 sit at a Saturday cutoff
produced by the session-walk defect fixed in `aa48c5d4`; they were previously excluded only by
their outcome's old fingerprint — a property of the *scoring* — so re-resolving them would have
walked them back into publication. The defect is in the **capture**, and no re-scoring repairs it.

`publishable_outcomes()` is the only selector a published figure may come from: current
resolver, not superseded, one origin, capture not invalidated. Unresolved rows are returned for
the coverage denominator but carry NULL returns — never a zero.

## Four mistakes of my own, and what caught each

| Mistake | Caught by |
|---|---|
| A test asserting an assertion was removed matched the **comment explaining its removal** — passing on its own prose, the fifth time in this repo | The test failing for the right reason once comments were stripped |
| `ast.unparse` drops comments but **keeps docstrings**, so a docstring reword would have minted a new resolver and re-scored the corpus | A test written to check exactly that |
| The one function left untested because it touches the network (`fetch`) broke on pandas Series truthiness | The first live pilot run |
| A test file beside the page became a **Next.js route**, breaking the frontend deploy after the backend had shipped | `next build`, not the unit suite — vitest runs a test wherever it sits |

Sabotage-verified throughout: 7 on the versioning work, 4 on the session/adjustment/contract
fixes, 3 on the publication selector. `make test` 196, `make test-integration` 87,
`make test-all` 546 frontend.

## Still open

* Ingestion covers MU and SPY only.
* No signed-in browser check — minting a token to inspect a page is not a casual act.
* Prospective observations #4/#5/#6 are pending on elapsed time, which is the correct state.

---

## Follow-up (2026-10-08, second round)

Four corrections after review of the first delivery.

### 1. The panel belongs inside Quality & Value — it was shipped as a separate page

The agreed integration was the summary and outcome tracking *inside* Quality & Value, keyed to
the symbol being evaluated. A standalone `/stock-intelligence` page does not satisfy that.

`StockIntelligencePanel` is now one component rendered by Quality & Value. The standalone route
imports the same component and holds no rendering or fetching of its own (asserted by test), and
is no longer in the nav. The panel renders only when **one** company is in view — over a 200-row
universe it is not a summary of anything.

**Conclusions first**, and the order is the argument: direction and horizon → main factors →
strongest counterevidence → triggers → outcome status. Buckets, adjustment evidence and
superseded results sit below, collapsed. The triggers (`confirmation_rule` /
`invalidation_rule`) were stored on every observation and had never been served.

### 2. Instrument applicability — GLD was assessed as a company it is not

GLD was labelled "Fund (inferred)" on the same screen that reported it as missing annual
statements and lacking a durable moat, with "collect the evidence" as its next research task. A
gold trust holds no operating business: "no moat" there is not a weak finding, it is a finding
about the **wrong subject**.

`assessment_applicability()` returns three states:

| Instrument | Confidence | Status |
|---|---|---|
| operating_company | declared | `applies` |
| fund | declared | `not_applicable` — names what fund analysis would require |
| anything | inferred | `unverified` — **everything today** |

An inferred fund is `unverified`, not `not_applicable`: calling it not-applicable would assert
the very type the classifier explicitly refuses to assert.

Suppressing the company backlog requires **positive evidence the subject is wrong**. An unknown
type is not such evidence — treating it as one would repeat the "absence implies fund" error the
classifier exists to avoid, and would silently empty the backlog for every company. My first
version did exactly that; an existing test caught it. Where suppressed, the items are still
carried under a heading saying what they are conditional on.

The caveat renders beside the **collapsed** gate badges, because those badges are what a reader
sees first and what the caveat governs.

Measured in production: GLD → `fund (inferred)` → `unverified`, backlog conditional, fund
analysis named. MU → `operating_company (inferred)` → `unverified`, backlog shown.

### 3. A request through a future date evidences nothing about the future

The pilot stored a coverage span ending **2026-12-31** from a fetch made on **2026-10-08** — it
asserted knowledge of corporate actions that had not happened. `requested_*` and `evidenced_*`
are now separate columns, `evidenced_to` is capped at the retrieval date, and verification reads
the evidenced one. The migration caps existing rows rather than trusting what was asked for.

An empty response establishes **"no actions were returned"**, not "none occurred" — yfinance
publishes no completeness guarantee. The window still verifies (refusing would make every
dividend payer permanently unresolvable), but `completeness_basis` records the strength and the
weaker claim is disclosed on the figure itself. A documented completeness guarantee would
justify `source_guarantee`.

### Two more mistakes of my own

* **A shadowed parameter.** I named the completeness basis `basis`, shadowing the function's
  `basis` parameter — the *return* basis. `basis == SPLIT_ADJUSTED_PRICE` then compared two
  unrelated things and was always False, silently disabling the raw-price refusal, the dividend
  disclosure and the whole total-return branch. Nothing about shadowing raises; the dividend
  tests are the only reason it did not ship.
* **A name that was never imported.** `classify_instrument` was used in `evaluations()` without
  a module-level import, so every call raised `NameError`. My guard for "does this import exist"
  matched a **local** import inside a different function. `make test`, `make test-integration`
  and `make test-all` were all green over a route that could not run; it was found by calling it
  against production. A test now compiles the function and resolves every global it loads
  against what the module and the function actually bind, local imports included.

### Status of the figures

The return figures in this document are **reported results, not independently verified**. The
adjustment pilot covers MU and SPY only.
