# MU and CRDO: completed assessments from issuer evidence

Two companies taken end to end, 2026-10-07. Every financial figure below is from the issuer's
own SEC filing, cited by accession number. Market capitalisations are dated. **Neither company
reaches "entry review ready", and the reasons are economic, not missing implementation.**

Standing limits: no email, no alert type, no order. This is research.

---

## 1. Issuer-verified financials

Source: SEC XBRL company facts (`us-gaap` taxonomy, so the basis is US GAAP **by construction**
rather than by assumption — which no provider row in this platform can say about itself).

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
| Shares outstanding / diluted | 1,122m / 1,125m | 185.4m / 188.2m |

### Two reconciliation findings

**The provider's period label is not the issuer's fiscal end.** The stored series labels MU's
newest annual `2025-08-31`; the issuer's 10-K reports the fiscal year ended **2025-08-28**. The
revenue value reconciles exactly ($37,378,000,000 in both), so the figures agree and the *dates*
do not. This is precisely why the business-performance section says "latest stored year" and
names the label as the provider's.

**MU's FY2026 is announced but NOT filed, and this corrects an earlier claim of mine.** The
Quality & Value gate reported MU's stored annual as 401 days old and said "a later fiscal year
has almost certainly been reported and is absent". The first half is right and the second half
was imprecise: MU's most recent **10-K** is still FY2025. What exists is an **8-K dated
2026-09-30** (`0000723125-26-000018`) announcing FY2026 results. So the stored series is current
to the newest *filed* annual report; what it is missing is an announced, unaudited fiscal year.

### MU FY2026 as announced (8-K, unaudited — not a 10-K)

Fiscal year **ended 2026-09-03**:

- Revenue **$133.19bn** against $37.38bn — the business is **3.6× the filed record**
- GAAP net income **$84.97bn**, $74.33 per diluted share
- Non-GAAP net income $86.76bn, $75.52 per diluted share
- Operating cash flow **$89.68bn** against $17.53bn
- Q4 alone: revenue $54.23bn, GAAP net income $37.70bn
- Q1 FY2027 guidance: revenue **$61.5bn ± $1.x bn**

Capital expenditure for FY2026 is **not stated in the highlights**, so free cash flow cannot be
computed from this release — and for MU capex is the decisive swing (FY2025: $15.86bn against
$17.53bn of operating cash flow, leaving $1.67bn). Reported as not located, not estimated.

---

## 2. Valuation, against dated market capitalisation

Both capitalisations fetched **2026-10-06**; both closing prices are the **2026-10-06** session.
Aggregate equity value against market capitalisation — never per share, because the issuer-filed
share count is as of its fiscal year end, which is not the market cap's date.

### MU — the cheap-looking multiple is the warning

| Measure | Value |
|---|---|
| Market capitalisation (2026-10-06) | **$1,201.6bn** |
| Close (2026-10-06) | $1,045.56 |
| P/E on FY2026 announced GAAP earnings | **14.1×** |
| P/E on the **six-year mean** of net income | **70.0×** |
| Price / FY2026 operating cash flow | 13.4× |

Net income by fiscal year, from the 10-Ks and the FY2026 8-K:

| FY2021 | FY2022 | FY2023 | FY2024 | FY2025 | FY2026 |
|---|---|---|---|---|---|
| $5.86bn | $8.69bn | **−$5.83bn** | $0.78bn | $8.54bn | **$84.97bn** |

FY2026 earnings are **4.95× the six-year mean of $17.17bn**, and the cycle contains a real loss
year three years ago. This is the structural trap a low P/E hides on a cyclical: the multiple is
at its lowest exactly when the denominator is at its most extreme. 14.1× and 70.0× are both
arithmetically correct and they are answers to different questions.

**Scenarios** (assumptions recorded; these are arithmetic consequences, not probabilities):

| Scenario | Sustainable earnings | Multiple | Equity value | Upside to price* |
|---|---|---|---|---|
| Bear | $17.2bn (six-year mean) | 12× | $206bn | −83% |
| Base | $60bn (≈70% of FY2026 retained) | 14× | $840bn | **−30%** |
| Bull | $100bn (FY2027 guidance run-rate) | 15× | $1,500bn | +25% |

\* **Upside to price** — denominator is the $1,201.6bn market capitalisation, so it reads as the
return from here. It is NOT the discount to value, whose denominator is the scenario's own
equity value and which is a larger number for every case below the price. The base case is −30%
of the price and −43% of the value; quoting one as the other overstates by that gap.

Sensitivity around the base, ±15% and ±30% on each axis, gives **$0.41tn to $1.42tn**. The
both-fall corner is not a remote tail for a cyclical — the multiple compresses as the earnings
peak, so those two moves are correlated.

**Reading:** the base case sits roughly 30% below the market. MU is not obviously cheap; it is
priced for most of a record year persisting. That is a defensible view, but it is a *forecast*,
and nothing in this platform evidences it.

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

FY2026 net income is **5.16× the five-year mean of $91.5m**, and three of the five years were losses (FY2022 −$22m, FY2023 −$17m, FY2024 −$28m, FY2025 +$52m).

| Scenario | Assumption | Equity value | Upside to price* |
|---|---|---|---|
| Bear | growth stalls; earnings revert to $150m, 25× | $3.75bn | −91% |
| Base | revenue doubles once more then grows 20%; $900m earnings, 30× | $27.0bn | **−32%** |
| Bull | hyper-growth persists three years; $1.8bn earnings, 35× | $63.0bn | +58% |

\* Same denominator as MU's table: the $39.94bn market capitalisation. **A draft of this table
quoted the base case as −48%, which is its discount to value — the other denominator.** Caught
by recomputing both; it is exactly the error `equity_discount()` returns two named figures to
prevent, and prose is evidently no safer from it than code was.

**Reading:** CRDO requires sustained hyper-growth to justify the price. The balance sheet is
genuinely strong — no debt, $1.44bn net cash, 68% gross margin — and the valuation leaves no
room for the concentration risk below to materialise.

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

**Verdict: not established.** Memory is a commodity with a documented price cycle and a loss
three years ago. Nothing located shows a mechanism that would prevent the next one.

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

**Verdict: not established.** High margins with 90% of revenue in ten customers describe a
supplier whose position depends on a small number of relationships, not one protected from them.

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

**Seven of thirteen classes were not checked.** Under the Quality & Value contract an unchecked
critical class leaves the value-trap gate unable to pass — which is correct, and is a statement
about the completeness of this review rather than about either company.

---

## 5. Where this leaves each company

**Neither reaches entry review, and now for economic reasons.**

**MU** — Fundamental checks: the filed series is current to the newest 10-K, but the business is
3.6× larger than that record and the gap is an unaudited 8-K. Durability: not established;
the filing's own risk factors describe the cycle mechanism. Valuation: 14.1× peak earnings is
70.0× the six-year mean, and the base scenario sits ~30% below the market. Risk: concentration
unestablished for FY2025, seven classes unchecked.

**CRDO** — Fundamental checks: pass, FY2026 filed four months ago. Durability: not established;
90% of revenue in ten customers is the counterevidence. Valuation: 28.8× EV/revenue and 94.6×
EV/FCF require hyper-growth to persist; the base scenario is ~32% below the market. Risk: severe
customer concentration, single segment, 8.3% annual dilution, accumulated deficit.

**What would change each verdict.** For MU: the FY2026 10-K (due within days, given FY2025's was
filed 2025-10-03), disclosed Strategic Customer Agreement terms, and a capex figure that makes
free cash flow computable. For CRDO: customer concentration falling materially, or a second
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
