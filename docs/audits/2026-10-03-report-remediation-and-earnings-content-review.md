# Report remediation, screenshots and earnings-content review

Date: October 3, 2026. Local baseline: `448dd2d0`. Inputs: Claude's remediation account, four user-supplied screenshots, current source, and Micron's official earnings release/SEC exhibit.

**Recommendation:** keep the stronger reporting contracts and build the richer earnings analysis the user requested. The current page is primarily a rendering of structured fields. A useful earnings report needs sourced financial comparisons, cash-flow analysis, guidance, management commentary and clearly separated price reaction. Fix the remaining correctness and rendering issues before adding persuasive prose.

## What the remediation establishes

Source inspection confirms the release-boundary call-site correction, evidence recording/validation, removal of cloned horizon forecasts, use of frozen expectations, and database-backed version conflict handling. Those are substantive improvements. The screenshots independently show partial states, separate breadth denominators, policy/version labels and unavailable horizons.

This pass does not independently verify deployment drift, run PostgreSQL contention tests, or operate the live browser. A screenshot confirms a rendered state, not every interaction. The local targeted suite result is recorded below. Do not infer full point-in-time correctness merely from the presence of evidence records or a passing contention test.

## Remaining issues and product gaps

### 1. Earnings-return units are wrong — confirmed by execution

`services/event-intelligence/src/services/earnings.py::_compute_post_earnings_returns` returns fractional returns (`price / baseline - 1`). `services/research-engine/src/intel_reports/adapters.py::post_event_reaction` emits those same values with `units='pct'`. `_historical_reactions` in the report generator similarly summarizes fractions under percentage-named fields.

Executed the actual extracted adapter with the screenshot's values:

```text
stored 0.1538  → rendered value 0.1538, units pct
stored -0.0185 → rendered value -0.0185, units pct
```

Those fractional values correspond to **+15.38% and −1.85%**, not +0.1538% and −0.0185%. This validates a unit-conversion defect, not the accuracy or event alignment of the original stored returns.

**Fix:** retain one explicit raw return unit and convert once at presentation. Apply the same contract to historical median/min/max, post-event returns, exports and evidence. Test producer→adapter→rendering together. Also audit the return baseline: the existing calculator uses the close before report date and later bars; this is not necessarily an immediately pre-release price, especially for after-hours earnings. Label its exact window rather than claiming a normalized release reaction.

### 2. Wrong event for the user's intended post-earnings report — visible, cause unverified

The screenshot generated on October 3 identifies `earnings:MU:2026-06-24`. It does not analyze the September release. The current generator selects the latest qualifying **stored** event; this review has not queried production to determine why the September event is absent from that result. Do not diagnose ingestion, mapping, stale reuse or an explicit event selection from the screenshot alone.

**Fix:** expose an event selector and prominent fiscal period/release date, default to the latest verified released event, and show a coverage warning when the known latest release cannot be assembled. Do not silently substitute an older quarter. Trace issuer→source release→event row→report selection for MU. The December pre-report may legitimately describe the next event; it must not be presented as the missing frozen baseline for September.

### 3. Rendering and history defects — screenshot plus source

- Sector rankings display `[object Object]`. `Value` in `frontend/src/pages/intelligence-reports.tsx` stringifies nested values. Render sector/name/return/count as a typed table; nested arrays need structured rendering in both UI and export.
- The market card says v2 supersedes #1, but its comparison says first report. In the generate API, reused current reports cause `diff(None, report)` because `previous.id == report.id`. Compare with `supersedes_id`, or display “unchanged since this version”; reserve “first report” for actual absence of history.
- Alphabetical field order produces return windows 1, 20, 5, 63 and mixes identity, conclusions and raw inputs. Use report-specific section order, with numeric horizon ordering and human-readable units.
- The supplied images show navigation obscuring mid-page content. Confirm in an actual browser whether this is sticky-layout overlap or screenshot-stitching behavior before changing layout.

### 4. Observation time is still being labeled availability time — source-confirmed residual

`adapters.record_bar()` assigns both `published_at` and `first_available_at` from `bar.ts`. `daily_bars()` filters `Price.ts <= cutoff`. A daily bar dated at midnight does not establish that its closing price was available at midnight. Revised or backfilled data also cannot acquire historical availability merely from its observation date.

**Fix:** use actual ingestion/publication/revision lineage and completed-session rules. Where first availability is not stored, mark it unknown and exclude those inputs from claims requiring historical availability. Current research can show dated data with that limitation; backtests/pre-event accountability cannot pretend the missing timestamp was measured. Add a same-session pre-close fixture and a late-backfill fixture.

### 5. Date-only release boundary needs market timezone — source-confirmed residual

`release_boundary` uses naive midnight of `report_date`. That is not universally conservative when interpreted as UTC: a prior-UTC-day report can already fall on the HK release day. The date-only policy should use the issuer's relevant exchange-local start of day, converted to canonical UTC, or withhold baseline certification where timezone/date semantics are uncertain. Test both US and HK boundaries and authoritative early-release evidence.

## Should the report use the user's four-phase earnings template?

**Yes, as the analytical structure, with stronger source and inference rules.** Rename “financial statement audit” to “financial statement analysis”: this product is not providing an accounting audit opinion.

### Official Micron verification, limited to the claims checked

The September 30 release identifies fiscal Q4/year-end September 3, 2026. It reports revenue $54.23B, adjusted EPS $33.42, operating cash flow $43.97B, and adjusted free cash flow $33.20B. Gross margin is **86.8% GAAP / 87.0% non-GAAP**. Q1 guidance is revenue **$61.5B ± $1.5B** and adjusted EPS **$38.15 ± $1.00**. The $73.48B balance includes marketable investments and restricted cash; it is not all unrestricted cash. [Official release](https://investors.micron.com/news/press-release/2026/Micron-Technology-Inc--Reports-Record-Fiscal-Fourth-Quarter-and-Full-Year-2026-Results/default.aspx), [SEC exhibit](https://www.sec.gov/Archives/edgar/data/723125/000072312526000018/a2026q4ex991-pressrelease.htm).

The precise consensus figures in the user's example remain unverified here: the supplied CNBC page could not be retrieved. Store provider, snapshot time, contributor coverage, period and GAAP/adjusted basis before labeling a surprise. Do not choose whichever published consensus creates the largest beat.

### Correct these interpretations before generating prose

| Example statement | Safer analytical treatment |
|---|---|
| A double beat proves organic growth, rather than cost-cutting, drove profits | A double beat establishes comparisons to particular estimates. Attribute profit drivers only after analyzing revenue mix, pricing/volume, costs, tax, share count, acquisitions and adjustments. |
| OCF above non-GAAP net income confirms customers are paying and receivables are not inflated | Compare OCF with GAAP income first, reconcile adjustments, and inspect depreciation, stock compensation, receivables/DSO, inventory, payables, deferred revenue/customer advances and tax timing. One ratio cannot prove collection quality. |
| Strong next-quarter guidance is “beat and raise” | Compare guidance separately with prior company guidance for the **same period** and pre-release consensus. First-issued guidance is not automatically a raise. |
| All data-center revenue rose eleven-fold | Name the exact business segment and denominator. Do not extend one segment's growth to all related segments. |
| The stock fell because results were priced in | Report the move with timestamp/window. “Priced in” is a hypothesis or attributed analyst interpretation, not a measured causal fact. Consider guidance/margins, positioning, broader market and prior run-up. |
| Strong reported demand proves the cycle will persist | Attribute management's outlook and add contrary evidence/cycle risks. Guidance remains a forward-looking statement. |

## Preferred earnings report layout

### Executive summary

Start with event/fiscal identity, data as-of and completeness. Then three short verdicts: **reported performance versus comparable expectations**, **forward guidance**, and **observed market reaction**. Follow with the strongest positive, strongest concern and unresolved question. No single “matrix verdict” should merge these into a BUY recommendation.

### 1. Headline metrics versus frozen expectations

Table columns: metric; fiscal period; accounting basis; actual; frozen consensus/source/time; prior company guidance; absolute/percentage surprise where meaningful; YoY/QoQ; evidence link. Include revenue and EPS, plus sector-relevant operating KPIs. Distinguish unavailable expectations from a genuine zero estimate.

### 2. Financial quality and sustainability

Show margins and their drivers, GAAP↔adjusted bridge, cash-flow bridge, capex definition, reported versus standardized FCF, working-capital movements, debt/cash/restricted cash, dilution and investment commitments. Use quarterly and annual columns deliberately; do not mix year-to-date cash flow with quarterly income. State the measurement limits of each interpretation.

### 3. Guidance and changes in expectations

Show new low/mid/high ranges versus comparable prior guidance and separately versus frozen consensus. Identify period, basis and source. Distinguish raised, maintained, lowered, withdrawn and first-issued. Explain what assumptions would have to hold and what changed; track subsequent analyst revisions as a later, separately dated section.

### 4. Management commentary and industry read-through

Evidence-linked themes: demand, price/volume/mix, capacity, customers, competition, margins, investment and risks. For memory, include sourced DRAM/NAND/HBM context; do not invent pricing or supply-chain estimates. Separate management statements, external observations and the system's interpretation. Use short attributed quotations only where necessary.

### 5. Market reaction and conditional outlook

Show pre-release reference, after-hours/premarket reaction, first regular-session reaction, benchmark-relative move, volume and quote quality. Define every window. Keep option reaction unavailable until valid option quotes exist; favorable stock direction is not option profitability. Give conditional bull/base/bear paths with confirmation and invalidation, plus what changes versus the frozen pre-report. Distinguish current trend from evaluated future prediction.

### 6. Accountability and sources

Link frozen pre-report, revisions and outcomes. Label historical reconstruction separately. Expandable evidence shows immutable values, source, units, accounting basis and availability limits. Missing critical inputs belong in a compact “what we cannot conclude” section rather than drowning the reader in implementation messages.

## Implementation order for Claude

1. Correct percent scaling, reused-report diff and nested rendering; add browser acceptance for all four views.
2. Resolve MU's latest-event selection using read-only production/source reconciliation. Do not write a replacement event or overwrite historical snapshots on inference alone.
3. Repair availability/session/timezone semantics before certifying historical or pre-release use.
4. Reuse existing issuer-news/filing ingestion to attach the official release, financial tables, source-confirmed fiscal identity and publication time to the canonical earnings event. Preserve conflicting provider values for review.
5. Join comparable consensus snapshots and guidance. Build deterministic comparison/quality tables before narration.
6. Add evidence-constrained narrative and the six-section layout. Narration may omit unsupported conclusions and must not change numeric facts, units or gate decisions. Preserve deterministic reports if the LLM fails.

Deliver the next slice as one complete, source-linked earnings report with UI/browser verification, not another blanket “all fields integrated” claim. A report with many named unavailable fields is honest but does not yet satisfy the user's desired financial analysis.

## Verification limits

This pass inspected source and user screenshots, checked official public sources, and executed the fractional-return witness locally. No application code, deployment, flags, trades, emails or `CLAUDE.md` changed. PostgreSQL races, the latest-event production query and authenticated browser interaction were not rerun here.

Targeted local verification: `python -m pytest -q --noconftest services/research-engine/tests/test_intelligence_reports.py` — **20 passed**, using the SQLite fallback. That passing suite does not cover the newly identified return-unit/display issues and is not a fresh PostgreSQL or full-suite claim.
