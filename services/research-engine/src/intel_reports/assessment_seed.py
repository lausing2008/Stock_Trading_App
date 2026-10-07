"""The MU and CRDO assessments, as data the evaluator reads.

WHY AS CODE. These are the findings from
docs/audits/2026-10-07-mu-crdo-completed-assessment.md, every figure taken from the issuer's own
SEC filing. Keeping them in a module rather than a migration means they are reviewable in a diff
and reproducible into any environment; `seed()` writes them as versioned IssuerAssessment rows
and never updates one in place.

NONE OF THESE PASSES ITS GATE. That is the point: a completed assessment whose answer is
"insufficient" is finished work AND a reason to stay ineligible. MU's valuation is
`context_only` — multiples at three earnings anchors are not a value estimate. CRDO's is
`insufficient` — scenarios exist, with horizons and sensitivities, and they rest on a discount
rate anchored on nothing company-specific.
"""
from __future__ import annotations

from datetime import datetime

CUTOFF = datetime(2026, 10, 7, 0, 0, 0)
AUTHOR = "issuer-filing review 2026-10-07"

MU_10K = "MU FY2025 10-K, accession 0000723125-25-000028, filed 2025-10-03"
MU_8K = "MU FY2026 results 8-K, accession 0000723125-26-000018, filed 2026-09-30 (unaudited)"
CRDO_10K = "CRDO FY2026 10-K, accession 0001628280-26-043303, filed 2026-06-15"

#: Classes a bounded risk review must state it did or did not look at.
RISK_CLASSES = ("leverage", "cash burn", "customer concentration", "dilution",
                "segment concentration", "accounting basis", "debt maturity schedule",
                "restatements", "related-party transactions",
                "auditor opinion / going concern", "litigation", "supplier concentration")

ASSESSMENTS = [
    # ---------------------------------------------------------------- MU durability
    {
        "symbol": "MU", "dimension": "competitive_durability", "version": 1,
        "verdict": "insufficient",
        "unresolved": "Do the Strategic Customer Agreements contain terms — duration, volume, pricing — that would make MU's position defensible through a downcycle? The agreements are referenced by management but not disclosed.",
        "summary": "The reviewed evidence does not establish a durable advantage. It does not "
                   "establish the absence of one either — no search was made for switching "
                   "costs in HBM qualification, customer design cycles, or the Strategic "
                   "Customer Agreement terms, none of which is disclosed in the documents read.",
        "findings": [
            {"claim": "Four reportable business units reorganised around data-centre memory, "
                      "with HBM for all data-centre customers",
             "source": MU_10K, "source_ref": "Business Segments",
             "counterevidence": "a segment reorganisation is a reporting change; it shows where "
                                "management is pointing, not that the position is defensible"},
            {"claim": "Management cites Strategic Customer Agreements as giving 'added "
                      "confidence in the durability of Micron's financial performance'",
             "source": MU_8K, "source_ref": "CEO statement",
             "counterevidence": "a characterisation, not a disclosed contract term — neither "
                                "duration, volume nor pricing is in the release, so it cannot "
                                "be assessed"},
            {"claim": "Capital intensity is a barrier: $15.86bn capex on $37.38bn revenue",
             "source": MU_10K, "source_ref": "Cash flow statement",
             "counterevidence": "the same intensity is the cyclical mechanism. MU's own risk "
                                "factors say competitors 'may increase capital expenditures "
                                "resulting in future increases in worldwide supply', and that "
                                "supply increases without demand 'could lead to declines in "
                                "average selling prices'"},
            {"claim": "Structural threats are disclosed by the issuer itself: competitor "
                      "consolidation, new entrants, government assistance to competitors, and "
                      "the May 2023 Cyberspace Administration of China decision barring Micron "
                      "products from critical-information-infrastructure operators",
             "source": MU_10K, "source_ref": "Item 1A Risk Factors",
             "counterevidence": "these argue against durability; they are listed because the "
                                "filing supplies the strongest case against its own moat"},
        ],
        "not_assessed": [
            {"item": "HBM qualification switching costs", "status": "not_examined"},
            {"item": "customer design-cycle lock-in", "status": "not_examined"},
            {"item": "Strategic Customer Agreement terms",
             "status": "not_publicly_disclosed",
             "note": "management references them in the 8-K; no duration, volume or pricing "
                     "is filed anywhere. This may never become knowable"},
            {"item": "patent portfolio strength", "status": "not_examined"},
        ],
        "evidence": {"segments": 4, "capex_usd": 15_857_000_000, "revenue_usd": 37_378_000_000},
    },
    # ---------------------------------------------------------------- MU valuation
    {
        "symbol": "MU", "dimension": "valuation", "version": 1,
        "verdict": "context_only",
        "unresolved": 'Which earnings level is sustainable? At $1,201.6bn the stock is 70.0x the six-year mean and 6.9x the guided quarter annualised; nothing collected distinguishes them.',
        "summary": "Valuation CONTEXT only, not a value estimate. At a $1,201.6bn market "
                   "capitalisation dated 2026-10-06, MU trades at 70.0x its six-year mean "
                   "earnings, 14.1x announced FY2026, and 6.9x its guided quarter annualised. "
                   "Which of those you are paying depends on an earnings-durability question "
                   "this review did not answer, so no defensible value estimate exists.",
        "findings": [
            {"claim": "70.0x the six-year mean net income of $17.17bn (FY2021-FY2026)",
             "source": f"{MU_10K}; {MU_8K}",
             "counterevidence": "the mean is a REFERENCE CASE showing earnings variability, not "
                                "a sustainable level — revenue went $15.5bn to $133.2bn over "
                                "the period and the segments were reorganised"},
            {"claim": "14.1x FY2026 GAAP net income of $84.97bn, the highest in the six years "
                      "examined",
             "source": MU_8K,
             "counterevidence": "announced and unaudited; no FY2026 10-K located. Calling it a "
                                "cycle peak would claim more than the series supports"},
            {"claim": "6.9x the FQ1-27 guided quarter annualised ($37.84 diluted EPS x 1,143.1m "
                      "implied diluted shares x 4 = $173.0bn)",
             "source": MU_8K, "source_ref": "Business Outlook",
             "counterevidence": "a MECHANICAL RUN-RATE ILLUSTRATION, not a forecast. MU guides "
                                "one quarter at a time and memory revenue is seasonal and "
                                "cyclical; nothing about four identical quarters follows"},
        ],
        "assumptions": [
            {"name": "market capitalisation", "value": 1_201_629_036_544, "units": "USD",
             "kind": "observed",
             "basis": "provider fundamentals, fetched 2026-10-06",
             "sensitivity": "dated; the issuer share counts are as of fiscal year end, a "
                            "different date, so no per-share conversion is performed"},
        ],
        "not_assessed": [
            {"item": "a defensible sustainable-earnings estimate", "status": "not_examined",
             "note": "irreducible in part — a cyclical's mid-cycle earnings are a judgement, "
                     "not a disclosure. More evidence narrows it; nothing settles it"},
            {"item": "any company-specific historical multiple series", "status": "not_examined"},
            {"item": "FY2026 capital expenditure", "status": "not_examined",
             "note": "absent from the 8-K highlights, so free cash flow is not computable — "
                     "the FY2026 10-K would close it"},
        ],
        "evidence": {"market_cap_usd": 1_201_629_036_544, "market_cap_as_of": "2026-10-06",
                     "close_usd": 1045.56, "close_as_of": "2026-10-06",
                     "six_year_mean_net_income_usd": 17_170_000_000,
                     "fy2026_net_income_usd": 84_970_000_000},
    },
    # ---------------------------------------------------------------- MU risk
    {
        "symbol": "MU", "dimension": "value_trap_risk", "version": 1,
        "verdict": "insufficient",
        "unresolved": "What is MU's FY2025 customer concentration? The only located disclosure covers 2023, and six further risk classes were not examined.",
        "summary": "Bounded risk review: 5 of 12 classes complete, 1 partial, 6 not assessed. "
                   "No critical issue identified WITHIN THIS REVIEW — which is a different "
                   "claim from the company being safe, and only the first is supported.",
        "findings": [
            {"claim": "Leverage: liabilities $28.6bn against equity $54.2bn; senior notes due "
                      "2035, 2041 and 2051 — long-dated", "source": MU_10K,
             "counterevidence": "no threshold on this ratio has been validated, and the "
                                "maturity table itself was not parsed"},
            {"claim": "Cash burn: operating cash flow positive in every year examined",
             "source": MU_10K,
             "counterevidence": "FY2025 free cash flow was $1.67bn on $17.53bn of operating "
                                "cash flow — capital intensity leaves little margin"},
            {"claim": "Dilution: shares outstanding 1,098m to 1,109m to 1,122m (+1.0%, +1.2%)",
             "source": MU_10K,
             "counterevidence": "low, but FY2022 showed a reduction, so the direction is not "
                                "a stable trend"},
        ],
        "not_assessed": ["FY2025 customer concentration (the located disclosure covers 2023)",
                         "restatements", "related-party transactions",
                         "auditor opinion / going concern", "litigation",
                         "supplier concentration",
                         "debt maturity schedule (note titles located, table not parsed)"],
        "evidence": {"classes_total": 12, "complete": 5, "partial": 1, "not_assessed": 6},
    },
    # ---------------------------------------------------------------- CRDO durability
    {
        "symbol": "CRDO", "dimension": "competitive_durability", "version": 1,
        "verdict": "insufficient",
        "unresolved": 'Are the two >10% customers locked in by design-cycle switching costs, or merely large? Concentration alone does not distinguish deep integration from fragility.',
        "summary": "The reviewed evidence does not establish a durable advantage, and does not "
                   "establish its absence. A 68.0% gross margin sustained for one year at this "
                   "scale is consistent with a strong position and with a favourable product "
                   "cycle; one year does not separate them.",
        "findings": [
            {"claim": "68.0% gross margin on $1.335bn revenue — pricing well above commodity",
             "source": CRDO_10K,
             "counterevidence": "one year at this margin; the prior year's revenue was $440m, "
                                "so it has not been tested at scale or through a demand pause"},
            {"claim": "33.3% operating margin while spending $279.4m (20.9% of revenue) on R&D",
             "source": CRDO_10K,
             "counterevidence": "consistent with a product cycle being harvested as much as "
                                "with a durable position"},
            {"claim": "Top 10 customers were approximately 90% of fiscal 2026 revenue, with two "
                      "customers each above 10%",
             "source": CRDO_10K, "source_ref": "Item 1A Risk Factors",
             "counterevidence": "concentration raises the CONSEQUENCE of losing a relationship. "
                                "It does NOT prove switching costs are absent — a supplier "
                                "embedded in a few customers' designs may be hard to replace, "
                                "and nothing reviewed distinguishes integration from fragility"},
            {"claim": "One reportable segment",
             "source": CRDO_10K, "source_ref": "Segment Information",
             "counterevidence": "segment reporting follows how the chief operating decision-"
                                "maker allocates resources. One reportable segment is NOT a "
                                "single revenue stream and must not be read as one"},
        ],
        "not_assessed": ["switching costs in customer designs", "patent portfolio strength",
                         "competitive win/loss record", "end-market share"],
        "evidence": {"gross_margin_pct": 68.0, "top10_customer_share_pct": 90,
                     "customers_over_10pct": 2, "reportable_segments": 1},
    },
    # ---------------------------------------------------------------- CRDO valuation
    {
        "symbol": "CRDO", "dimension": "valuation", "version": 1,
        "verdict": "insufficient",
        "unresolved": 'Does applying 8%/yr dilution on top of GAAP earnings double-count stock compensation? The answer moves the bull case from -10.9% to +12.3%, and no cost of equity was computed either.',
        "summary": "Three scenarios with stated horizons, discounted and reported under two "
                   "dilution treatments. Bear and base sit below the market under every "
                   "treatment; the bull case lands between -10.9% and +12.3% depending on an "
                   "unresolved question about whether dilution double-counts GAAP stock "
                   "compensation. The discount rate is a judgement anchored on nothing "
                   "company-specific, so no defensible value estimate is claimed.",
        "findings": [
            {"claim": "Bear: earnings revert to $150m at 25x over 1 year — $3.75bn terminal, "
                      "$3.35bn at 12%, $3.10bn after dilution: -92% against the market",
             "source": CRDO_10K, "counterevidence": "below the market on every treatment"},
            {"claim": "Base: $900m earnings at 30x over 2 years — $27.0bn terminal, $21.52bn at "
                      "12%, $18.45bn after dilution: -54%",
             "source": CRDO_10K, "counterevidence": "below the market on every treatment"},
            {"claim": "Bull: $1.8bn earnings at 35x over 3 years — $63.0bn terminal, $44.84bn "
                      "at 12% (+12.3%), $35.60bn after 8%/yr dilution (-10.9%)",
             "source": CRDO_10K,
             "counterevidence": "THESE TWO ENDPOINTS ARE SEPARATE MODELLING TREATMENTS, NOT A "
                                "CONFIDENCE INTERVAL. Whether dilution double-counts depends on "
                                "how earnings, issuance and financing are modelled together, "
                                "and neither endpoint resolves that"},
        ],
        "assumptions": [
            {"name": "discount rate", "value": 12.0, "units": "percent", "kind": "modelled",
             "basis": "A JUDGEMENT ANCHORED ON NOTHING COMPANY-SPECIFIC. No beta, risk-free "
                      "rate or equity risk premium was collected for CRDO",
             "sensitivity": "dominant. Dilution-adjusted the bull case runs -0.6% at 8%, -5.9% "
                            "at 10%, -10.9% at 12%, -17.7% at 15%; un-adjusted +25.2%, +18.5%, "
                            "+12.3%, +3.7%"},
            {"name": "dilution, historical", "value": 8.3, "units": "percent",
             "kind": "observed",
             "basis": "171.2m to 185.4m shares outstanding, FY2025 to FY2026, from the 10-K. "
                      "The prior year was +4.2%",
             "sensitivity": "none — this is what happened"},
            {"name": "dilution, projected", "value": 8.0, "units": "percent per year",
             "kind": "modelled",
             "basis": "EXTENDING the observed FY2026 rate forward. The past rate is observed; "
                      "that it CONTINUES is an assumption, and the earlier version labelled "
                      "the whole thing 'observed, not assumed', which conflated them",
             "sensitivity": "whether it should be applied AT ALL is unresolved — GAAP earnings "
                            "already expense stock compensation"},
            {"name": "exit multiples", "value": "25x / 30x / 35x", "units": "earnings",
             "kind": "modelled",
             "basis": "judgement; no company-specific historical multiple series was collected",
             "sensitivity": "any evidence of where comparable businesses traded through a cycle"},
            {"name": "market capitalisation", "value": 39_936_024_576, "units": "USD",
             "kind": "observed",
             "basis": "provider fundamentals, fetched 2026-10-06",
             "sensitivity": "dated; no per-share conversion is performed"},
        ],
        "not_assessed": [
            {"item": "a company-specific cost of equity", "status": "not_examined",
             "note": "beta, risk-free rate and equity risk premium are all collectable"},
            {"item": "comparable-company multiples", "status": "not_examined"},
            {"item": "whether the dilution adjustment double-counts stock compensation",
             "status": "not_examined",
             "note": "a MODELLING question, not a disclosure gap — it is settled by choosing a "
                     "consistent treatment of earnings, issuance and financing, not by finding "
                     "another document"},
        ],
        "evidence": {"market_cap_usd": 39_936_024_576, "market_cap_as_of": "2026-10-06",
                     "close_usd": 220.83, "close_as_of": "2026-10-06",
                     "ev_usd": 38_493_000_000, "ev_revenue_x": 28.8, "ev_fcf_x": 94.6,
                     "net_cash_usd": 1_443_286_000},
    },
    # ---------------------------------------------------------------- CRDO risk
    {
        "symbol": "CRDO", "dimension": "value_trap_risk", "version": 1,
        "verdict": "insufficient",
        "unresolved": 'Do the five unexamined classes — restatements, related-party, auditor opinion, litigation, supplier concentration — contain anything disqualifying?',
        "summary": "Bounded risk review: 6 of 12 classes complete, 1 not applicable, 5 not "
                   "assessed. Severe customer concentration is identified and is a finding "
                   "about the company; the five unassessed classes are a statement about this "
                   "review's completeness.",
        "findings": [
            {"claim": "No debt reported; net cash $1.443bn ($1.165bn cash + $0.278bn short-term "
                      "investments)", "source": CRDO_10K,
             "counterevidence": "balance-sheet strength is resilience, not advantage — it "
                                "affects who survives a downturn, not who wins"},
            {"claim": "Customer concentration: top 10 approximately 90% of revenue, two "
                      "customers each above 10%", "source": CRDO_10K,
             "counterevidence": "a real and material finding; it does not by itself establish "
                                "that the relationships are weak"},
            {"claim": "Dilution: 164.3m to 171.2m to 185.4m shares (+4.2%, +8.3%)",
             "source": CRDO_10K,
             "counterevidence": "material, and it interacts with the valuation's unresolved "
                                "double-counting question"},
            {"claim": "Accumulated deficit, and the filing states the company 'may incur "
                      "additional net losses'", "source": CRDO_10K, "source_ref": "Item 1A",
             "counterevidence": "FY2026 was strongly profitable; the deficit is history"},
        ],
        "not_assessed": ["restatements", "related-party transactions",
                         "auditor opinion / going concern", "litigation",
                         "supplier concentration"],
        "evidence": {"classes_total": 12, "complete": 6, "not_applicable": 1,
                     "not_assessed": 5, "net_cash_usd": 1_443_286_000},
    },
]
