# MU and CRDO: completed assessments from issuer evidence

Two companies taken end to end, 2026-10-07. Every financial figure below is from the issuer's
own SEC filing, cited by accession number. Market capitalisations are dated.

**Revised 2026-10-07 after review.** An earlier version of this document said both companies
were "rejected on economics". That was too categorical, and the document contradicted it two
sections later. The outcomes are three different things and are now reported separately:

| Dimension | MU | CRDO |
|---|---|---|
| **Valuation** | **Fails under the assumptions selected below** — every scenario sits below the market price | **Fails under the assumptions selected below** — all three scenarios do, once discounted |
| **Durability** | **Incomplete.** The reviewed evidence does not establish a durable advantage. It does not establish the absence of one either | **Incomplete**, same sense |
| **Risk review** | **Incomplete** — 6 of 12 classes not assessed | **Incomplete** — 5 of 12 classes not assessed |

A valuation failing under assumptions *I selected* is not the same as a company being
unattractive, and neither is the same as an unfinished review. Only the first is a conclusion
about price; none is a conclusion about the business.

Standing limits: no email, no alert type, no order. This is research.

---

## 1. Issuer-verified financials

Source: SEC XBRL company facts, `us-gaap` taxonomy.

**What the taxonomy establishes, and what it does not.** `us-gaap` identifies the accounting
*concept*. On its own it does not establish that a selected fact has the right duration,
consolidation scope, units or amendment vintage — four separate checks, each now enforced or
disclosed rather than assumed:

| Check | Status |
|---|---|
| **Duration** | **Enforced, and this found a real defect.** A 10-K reports its quarters as well as its year: of MU's `NetIncomeLoss` facts carrying form `10-K`, **77 span 90 days and 3 span 97, against 45 spanning a year**. The first extractor accepted all of them. Duration facts must now span 340–400 days (tolerating 52/53-week calendars). Re-running after the fix changed **no figure in this document** — the annual facts had been selected by sort order, not by design |
| **Units** | Enforced. Each quantity accepts one unit; `USD` and `USD-per-shares` are never mixed under one concept |
| **Consolidation scope** | Disclosed, not filtered. The companyfacts endpoint returns only undimensioned facts — the observed key set is `accn/end/filed/form/fp/frame/fy/start/val`, with no segment or member axis — so these are consolidated figures. That is a property of the endpoint and would need re-checking if the API changed |
| **Amendment vintage** | Enforced. Where a period is reported more than once the latest-filed value wins, so a `10-K/A` supersedes the original, and the superseded value is retained |

| | MU | CRDO |
|---|---|---|
| Entity | Micron Technology, Inc. | Credo Technology Group Holding Ltd |
| CIK | 0000723125 | 0001807794 |
| SIC | 3674 Semiconductors | 3674 Semiconductors |
| Newest **filed** 10-K | FY2025, period end **2025-08-28**, filed 2025-10-03 (`0000723125-25-000028`) | FY2026, period end **2026-05-02**, filed 2026-06-15 (`0001628280-26-043303`) |
| Revenue | $37.378bn | $1.335bn |
| Gross profit (margin) | $14.873bn (39.8%) | $908.3m (68.0%) |
| Operating income (margin) | $9.770bn (26.1%) | $445.0m (33.3%) |
| Net income | $8.539bn | $472.3m |
| Operating cash flow | $17.525bn | $464.3m |
| Capital expenditure | $15.857bn | $57.3m |
| Free cash flow | $1.668bn | $407.0m |
| Total assets / liabilities / equity | $82.798 / $28.633 / $54.165bn | $2.296 / $0.232 / $2.064bn |
| Cash (+ ST investments) | $9.642bn | $1.165bn (+$0.278bn) |
| Long-term debt | Senior notes due 2035, 2041, 2051 | **None reported** |
| R&D | $3.798bn | $279.4m |
| **Shares — three different measures** | see below | see below |

### Share counts are three different numbers, and the earlier version conflated them

An earlier draft reported "1,122m / 1,125m" as "shares outstanding / diluted" without stating
the type or the measurement date. They are not interchangeable:

| Measure | Type | MU | as of | CRDO | as of |
|---|---|---|---|---|---|
| `us-gaap:CommonStockSharesOutstanding` | **instant**, balance-sheet date | 1,122.0m | 2025-08-28 | 185.4m | 2026-05-02 |
| `us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding` | **duration** average over the year; the EPS denominator | 1,125.0m | 2024-08-30 → 2025-08-28 | 188.2m | 2025-05-04 → 2026-05-02 |
| `dei:EntityCommonStockSharesOutstanding` | **instant**, cover page — a date *after* the fiscal year end | 1,122.5m | **2025-09-26** | 186.5m | **2026-06-08** |

The cover-page count is the most recent, and is the one closest in time to a market
capitalisation — but it still predates the 2026-10-06 capitalisation used here by over a year
for MU. **No per-share conversion is performed anywhere in this document**, and that is why.

### Filing dates cannot order events within a day

`filed` is a calendar date. The acceptance timestamp is not the same thing, and the difference
is not academic: **CRDO's FY2026 10-K is dated 2026-06-15 but was accepted at
2026-06-16T01:09:27Z** — the following day in UTC. MU's FY2025 10-K: filed 2025-10-03, accepted
2025-10-03T18:42:25Z. MU's FY2026 8-K: filed 2026-09-30, accepted 2026-09-30T20:02:22Z, i.e.
after the US close.

Every extracted fact now carries `accepted` alongside `filed`, because any cutoff finer than a
day must use the former.

### Two reconciliation findings

**The provider's period label is not the issuer's fiscal end.** The stored series labels MU's
newest annual `2025-08-31`; the issuer's 10-K reports the fiscal year ended **2025-08-28**. The
revenue value reconciles exactly ($37,378,000,000 in both), so the figures agree and the *dates*
do not. This is precisely why the business-performance section says "latest stored year" and
names the label as the provider's.

**MU's FY2026 is announced, with no FY2026 10-K located — and this corrects an earlier claim
of mine.** The
Quality & Value gate reported MU's stored annual as 401 days old and said "a later fiscal year
has almost certainly been reported and is absent". The first half is right and the second half
was imprecise: MU's most recent **10-K** is still FY2025. What exists is an **8-K dated
2026-09-30** (`0000723125-26-000018`) announcing FY2026 results. So the stored series is current to the
newest 10-K located; what it is missing is an announced, unaudited fiscal year. "No FY2026 10-K
located" is the accurate claim — a search of the issuer's recent filings found none, which is
not the same as establishing that none has been filed.

### MU FY2026 as announced (8-K, unaudited — not a 10-K)

Fiscal year **ended 2026-09-03**:

- Revenue **$133.19bn** against $37.38bn — the business is **3.6× the filed record**
- GAAP net income **$84.97bn**, $74.33 per diluted share
- Non-GAAP net income $86.76bn, $75.52 per diluted share
- Operating cash flow **$89.68bn** against $17.53bn
- Q4 alone: revenue $54.23bn, GAAP net income $37.70bn
- FQ1-27 guidance: revenue **$61.5bn ± $1.5bn**, GAAP diluted EPS **$37.84 ± $1.00**, GAAP gross margin ~85.95%

Capital expenditure for FY2026 is **not stated in the highlights**, so free cash flow cannot be
computed from this release — and for MU capex is the decisive swing (FY2025: $15.86bn against
$17.53bn of operating cash flow, leaving $1.67bn). Reported as not located, not estimated.

---

## 2. Valuation, against dated market capitalisation

Both capitalisations fetched **2026-10-06**; both closing prices are the **2026-10-06** session.
Aggregate equity value against market capitalisation — never per share, because the issuer-filed
share count is as of its fiscal year end, which is not the market cap's date.

### MU — which multiple you are paying depends on which earnings level persists

| Measure | Value |
|---|---|
| Market capitalisation (2026-10-06) | **$1,201.6bn** |
| Close (2026-10-06) | $1,045.56 |
| P/E on FY2026 announced GAAP earnings | **14.1×** |
| P/E on the **six-year mean** of net income (a reference case, not a forecast) | **70.0×** |
| Price / FY2026 operating cash flow | 13.4× |

Net income by fiscal year, from the 10-Ks and the FY2026 8-K:

| FY2021 | FY2022 | FY2023 | FY2024 | FY2025 | FY2026 |
|---|---|---|---|---|---|
| $5.86bn | $8.69bn | **−$5.83bn** | $0.78bn | $8.54bn | **$84.97bn** |

FY2026 is **the highest net income in the six years examined** — 4.95× the arithmetic mean of
$17.17bn — and the series contains a real loss three years ago.

**The mean is a reference case, not normalized earnings.** It demonstrates that this business's
earnings vary by an enormous factor; it does not establish a sustainable level, because MU's
scale and mix changed over the period (revenue went from $15.5bn to $133.2bn and the segment
structure was reorganised in FY2025). Calling FY2026 "the cycle peak" would also claim more than
the evidence supports — it is the highest figure in the examined series, and whether a higher
one follows is exactly what is unknown. 14.1× and 70.0× are both arithmetically correct and are
answers to different questions.

**Earnings levels, and what each implies you are paying.** These are not scenarios with
probabilities. They are three anchors drawn from the record, with the multiple each produces:

| Earnings anchor | Source | Amount | Multiple at $1,201.6bn |
|---|---|---|---|
| Six-year mean | FY2021–FY2026 net income, 10-Ks + the FY2026 8-K | $17.17bn | **70.0×** |
| FY2026 as announced | 8-K `0000723125-26-000018` | $84.97bn | **14.1×** |
| FQ1-27 guided quarter × 4 | Guidance EPS $37.84 × 1,143.1m implied diluted shares × 4 | $173.0bn | **6.9×** |

**The last row is a mechanical run-rate illustration, not a forecast.** It multiplies one guided
quarter by four. MU guides one quarter at a time, memory revenue is seasonal and cyclical, and
nothing about four identical quarters follows from one guided one. It appears because it is the
only forward figure the issuer itself published, and because omitting it would understate what
the market may be pricing — not because anyone should expect it.

**THIS TABLE IS VALUATION CONTEXT, NOT A VALUATION ASSESSMENT.** It reports the multiple implied
at three earnings anchors. It does not produce a defensible value estimate, and the Quality &
Value valuation gate therefore stays `not_implemented` for MU — it would be wrong for this
section to close that gate, because a range of multiples is not an answer to "what is this
worth". Closing it needs an earnings level with evidence behind it, which this review did not
assemble.

**No equity value is asserted here, and the earlier version of this table should not have
asserted one.** It carried a "base case" of $60bn sustainable earnings at a 14× multiple with no
stated rationale for either number — and the result, $840bn, then read as a fair value. Neither
input was supported: nothing in this platform establishes what MU's sustainable earnings are,
and no company-specific historical multiple series was collected to justify 14×. The table above
reports what the market price implies at each anchor, which is a measurement. A fair value would
be a claim, and the evidence for it has not been assembled.

**What is fair to conclude:** at $1,201.6bn, MU is priced at 6.9× its own guided quarter
annualised and 70.0× its six-year mean. Which of those you are actually paying depends entirely
on an earnings durability question this review did not answer.

**Sensitivity.** Taking $60bn × 14× purely as an illustrative midpoint — not as a claim — and
moving each axis ±15% and ±30% spans **$0.41tn to $1.42tn**, against a $1,201.6bn market
capitalisation. The both-fall corner is not a remote tail for a business like this: a multiple
compresses as earnings peak, so the two moves are correlated. The width of that range is the
finding — the answer is governed by assumptions, not by the arithmetic.

### CRDO — priced for continuation, with concentration against it

| Measure | Value |
|---|---|
| Market capitalisation (2026-10-06) | **$39.94bn** |
| Close (2026-10-06) | $220.83 |
| Net cash (no debt reported) | $1.443bn |
| Enterprise value | $38.49bn |
| P/E on FY2026 | **84.6×** |
| EV / revenue | **28.8×** |
| EV / free cash flow | **94.6×** |
| Revenue growth FY2025 → FY2026 | $0.440bn → $1.335bn, **+203%** |

FY2026 net income is **the highest in the five years examined**, 5.16× the arithmetic mean of $91.5m (a reference case, not a sustainable level), and three of the five years were losses (FY2022 −$22m, FY2023 −$17m, FY2024 −$28m, FY2025 +$52m).

**Each scenario now states its horizon, is discounted to today, and is adjusted for dilution.**
The earlier version compared a value reached in three years directly against today's market
capitalisation, which overstates it twice over — once for time, once for the shares issued along
the way.

Discount rate **12%** and dilution **8%/yr** are assumptions, justified below and sensitive.

| Scenario | Assumption | Horizon | Terminal value | PV at 12% | After 8%/yr dilution | vs $39.94bn |
|---|---|---|---|---|---|---|
| Bear | growth stalls, earnings revert to $150m, 25× | 1 yr | $3.75bn | $3.35bn | **$3.10bn** | **−92%** |
| Base | revenue doubles once more then +20%, $900m, 30× | 2 yr | $27.0bn | $21.52bn | **$18.45bn** | **−54%** |
| Bull | hyper-growth persists, $1.8bn earnings, 35× | 3 yr | $63.0bn | $44.84bn (**+12.3%**) | **$35.60bn** | **−11%** |

**The bull case changes sign — and then straddles zero, because the dilution treatment is
genuinely ambiguous.** Undiscounted it read +58%. Discounted at 12% it is **+12.3%**. Applying a
further 8%/yr share-count dilution on top takes it to **−10.9%**.

**Both of those may double-count, and the review flagged exactly this.** The projected earnings
are GAAP, so stock-based compensation is *already expensed* inside the $1.8bn — charging the
claim a second time for the share count arguably counts the same economic cost twice. Against
that, an expense reduces earnings while an issuance reduces the fraction a current holder owns,
and those are different mechanisms. The honest position is that this review cannot settle it, so
**both figures are reported and the bull case brackets zero at −10.9% to +12.3%.** The scenarios
below carry the dilution-adjusted number, and the undiscounted and un-adjusted figures are shown
beside it so the choice is visible rather than embedded.

The bear and base cases are below the market on either treatment, so the ambiguity changes
nothing for them.

Assumptions and their sensitivity:

| Assumption | Value | Basis | What would change it |
|---|---|---|---|
| Discount rate | 12% | **A judgement, anchored on nothing company-specific.** No cost of equity was computed for CRDO — no beta, no risk-free rate and no equity risk premium were collected. It is set in the range usually applied to a single-segment, pre-scale business, and that is the whole of its justification | Dominant. Dilution-adjusted, the bull case runs −0.6% at 8%, −5.9% at 10%, −10.9% at 12%, −17.7% at 15%. Un-adjusted: +25.2%, +18.5%, +12.3%, +3.7% |
| Dilution | 8%/yr | Observed, not assumed: 171.2m → 185.4m shares outstanding, FY2025 → FY2026 (+8.3%); the prior year was +4.2% | Buybacks, or stock compensation falling as a share of revenue at scale. **And see the double-counting question above** — whether it should be applied at all is unresolved |
| Earnings measure | GAAP | As filed. SBC is already an expense within it | If a pre-SBC or cash measure were used, the dilution adjustment would be clearly required rather than arguable |
| Exit multiples (25/30/35×) | assumption | **No company-specific historical multiple series was collected.** These are judgement | Any evidence of where comparable businesses have traded through a cycle |

\* The "vs $39.94bn" column is **upside to price** — denominator is the market capitalisation.
A draft quoted the base case as −48%, which is its discount to *value*, beside bear and bull
quoted as upside to price. Caught by recomputing; exactly the error `equity_discount()` returns
two named figures to prevent, and prose is evidently no safer from it than code was.

**Reading:** the bear and base cases are below the market under every treatment examined. The
bull case — sustained hyper-growth for three years — lands between −11% and +12% depending on a
dilution question this review could not settle, which is to say it is roughly fair value if
everything goes right. The balance sheet is genuinely
strong — no debt, $1.44bn net cash, 68.0% gross margin. The conclusion is about the price under
stated assumptions, not about the business.

---

## 3. Durability: sourced arguments, each with its counterevidence

No moat rating is produced. Each argument is a claim with a source and the evidence against it.

### MU

| Argument for durability | Source | Counterevidence |
|---|---|---|
| Four reportable business units reorganised around data-centre memory (CMBU, CDBU, MCBU + embedded), with HBM for all data-centre customers | FY2025 10-K, Business Segments | A segment reorganisation is a reporting change. It evidences where management is pointing, not that the position is defensible |
| "Strategic Customer Agreements provide added confidence in the durability of Micron's financial performance" — CEO | FY2026 8-K press release, 2026-09-30 | **This is management's characterisation, not a disclosed contract term.** Neither duration, volume nor pricing is in the release. An agreement whose terms are not disclosed cannot be assessed |
| Capital intensity is itself a barrier: $15.86bn capex on $37.38bn revenue in FY2025 | FY2025 10-K | The same intensity is the cyclical mechanism. MU's own risk factors say competitors "may increase capital expenditures resulting in future increases in worldwide supply", and that supply increases "not accompanied by commensurate increases in demand, could lead to declines in average selling prices" |
| No customer at 10%+ of revenue | FY2025 10-K — **but the sentence located states this for 2023** | The FY2025-specific disclosure was not located in the filing text. Reported as not established for FY2025, not carried forward |

MU's own 10-K supplies the strongest arguments against durability, and they are structural:
consolidation of competitors, new entrants, government assistance to competitors, the
**May 2023 Cyberspace Administration of China decision barring critical-information-
infrastructure operators from purchasing Micron products** (which the filing says had an adverse
impact), and rapid technological change shortening product life cycles.

**Verdict: the reviewed evidence does not establish a durable advantage.** It does not
establish the absence of one either, and the distinction matters: no search was made for
switching costs in MU's HBM qualification process, for customer-specific design cycles, or for
the Strategic Customer Agreement terms, because none is disclosed in the documents reviewed.
What the reviewed evidence does show is a documented price cycle and a loss three years ago,
with no located mechanism that would prevent the next one.

### CRDO

| Argument for durability | Source | Counterevidence |
|---|---|---|
| 68.0% gross margin on $1.335bn revenue — pricing well above commodity | FY2026 10-K | One year at this margin. The prior year's revenue was $440m, so the margin has not been tested at scale or through a demand pause |
| 33.3% operating margin while spending $279.4m (20.9% of revenue) on R&D | FY2026 10-K | Consistent with a product cycle being harvested as much as with a durable position |
| Debt-free with $1.44bn net cash | FY2026 10-K | Balance-sheet strength is resilience, not competitive advantage. It affects who survives a downturn, not who wins |
| Single reportable segment, focused execution | FY2026 10-K, Segment Information | The same fact is concentration: there is no second business to absorb a shock in the first |

**The decisive counterevidence is in the filing.** In fiscal 2026, sales to the top 10 customers
were **approximately 90% of total revenue**, and **two customers each exceeded 10%**. CRDO's own
risk factors add that its market "is an emerging market that will depend on the success of
generative AI technologies, and this market may not develop as we currently expect", and that it
has an accumulated deficit and "may incur additional net losses".

**Verdict: the reviewed evidence does not establish a durable advantage.**

Two claims the earlier version made that the evidence does not support, and which are withdrawn:

- **Customer concentration is a risk; it is not proof that switching costs are absent.** A
  supplier embedded in a handful of customers' designs may be very hard to replace — high
  concentration is equally consistent with deep integration and with fragility, and nothing
  reviewed distinguishes them. The right statement is that concentration raises the consequence
  of losing a relationship, not that no relationship is protected.
- **A single reportable segment is not a single revenue stream.** Segment reporting follows how
  the chief operating decision-maker allocates resources, which CRDO's own filing says
  explicitly. One reportable segment can contain several products and end markets. The earlier
  phrasing "there is no second business to absorb a shock in the first" asserted a business fact
  from an accounting disclosure.

A 68.0% gross margin sustained for one year at this scale is consistent with a strong position
and with a favourable product cycle; one year does not separate them.

---

## 4. Bounded risk review — what was checked, and what was not

Stating the boundary is the point: "no critical issue identified **within this review**" is a
different claim from "the company is safe", and only the first is supported.

| Risk class | Checked? | MU | CRDO |
|---|---|---|---|
| Leverage | Yes | Liabilities $28.6bn vs equity $54.2bn; senior notes due 2035/2041/2051 — long-dated | **No debt reported.** Net cash $1.44bn |
| Cash burn | Yes | Operating cash flow positive every year examined | Positive, FCF $407m |
| Customer concentration | Yes | **Not established for FY2025** (located disclosure covers 2023) | **Top 10 ≈ 90%; two customers ≥10%** |
| Dilution | Yes | Shares 1,098m → 1,109m → 1,122m (+1.0%, +1.2%/yr) | 164.3m → 171.2m → 185.4m (**+4.2%, +8.3%**) |
| Segment concentration | Yes | Four reportable segments | **One** |
| Accounting basis | Yes | US GAAP (us-gaap taxonomy, 10-K) | US GAAP (us-gaap taxonomy, 10-K) |
| Debt maturity schedule | **Partially** | Note titles located; the maturity *table* was not parsed | N/A — no debt |
| Restatements | **No** | Not checked | Not checked |
| Related-party transactions | **No** | Not checked | Not checked |
| Auditor opinion / going concern | **No** | Not checked | Not checked |
| Litigation | **No** | Not checked | Not checked |
| Supplier concentration | **No** | Not checked | Not checked |

**Counts, per company, over the twelve classes in the table above** (an earlier version said
"seven of thirteen", which matched neither the table nor either company):

| | Complete | Partial | Not applicable | Not assessed |
|---|---|---|---|---|
| **MU** | 5 | 1 (debt maturity: note titles located, the maturity table not parsed) | 0 | **6** (FY2025 customer concentration, restatements, related-party, auditor opinion, litigation, supplier concentration) |
| **CRDO** | 6 | 0 | 1 (debt maturity — no debt reported) | **5** (restatements, related-party, auditor opinion, litigation, supplier concentration) |

Under the Quality & Value contract an unassessed critical class leaves the value-trap gate
unable to pass — which is correct, and is a statement about the completeness of this review
rather than about either company.

---

## 5. Where this leaves each company

**Neither reaches entry review.** The valuation gate fails under the assumptions selected in
section 2; the durability and risk gates are incomplete. Those are different statements and the
summary table at the top of this document keeps them apart.

**MU** — Fundamental checks: the stored series is current to the newest 10-K located, but the business is
3.6× larger than that record and the gap is an unaudited 8-K. Durability: the reviewed evidence does not establish one;
the filing's own risk factors describe the cycle mechanism. Valuation: 6.9× the guided quarter annualised,
14.1× FY2026, 70.0× the six-year mean — which you are paying is the open question. Risk: concentration
unestablished for FY2025, seven classes unchecked.

**CRDO** — Fundamental checks: pass, FY2026 filed four months ago. Durability: the reviewed evidence does not
establish one; 90% of revenue in ten customers raises the consequence of losing a relationship. Valuation: bear and base fall below the market
under every treatment; the bull case brackets zero (−11% to +12.3%) on an unresolved
dilution question. Risk: severe
customer concentration, single segment, 8.3% annual dilution, accumulated deficit.

**What would change each verdict.** For MU: an FY2026 10-K, whenever it appears — the previous
year's 10-K was filed 2025-10-03, but one year's filing date does not establish when the next is
due, and no deadline was checked. Also: disclosed Strategic Customer Agreement terms, and a
capex figure that makes free cash flow computable. For CRDO: customer concentration falling materially, or a second
revenue stream, plus evidence the 68% gross margin survives a demand pause.

---

## 5b. Every figure above was re-verified against the source

The historical net-income series was re-fetched and checked line by line rather than carried
from a first pass. Two CRDO figures were wrong in the draft (FY2022 −$25m and FY2023 −$16m; the
filings say **−$22m** and **−$17m**) and the five-year mean moved from 5.19× to **5.16×**. MU's
six-year series was correct as drafted.

Share counts were checked the same way. MU: 1,119m (FY2021) → 1,094m (FY2022, a **reduction** —
buybacks) → 1,098m → 1,109m → 1,122m. CRDO: 144.8m → 148.7m → 164.3m → 171.2m → 185.4m, every
year an increase.

## 6. What this exercise established about the pipeline

The three gates reported as unimplemented were unimplemented for a reason that EDGAR removes:

- **Share counts exist** — 1,122m for MU, 185.4m for CRDO, dated and attached to a 10-K. The
  readiness inventory's "no usable share count anywhere" was true of the *stored provider data*,
  not of the issuer record.
- **Accounting basis exists** — the `us-gaap` taxonomy makes it a property of the source.
- **Filing dates exist** — so a point-in-time claim becomes possible.

The constraint was never that this information does not exist. It was that the platform had only
ever asked a price-data provider for it.
