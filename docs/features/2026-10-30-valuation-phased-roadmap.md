# Valuation report phased implementation

The user approved both the report roadmap and the remaining issuer checks, delivered in phases. Preserve free Yahoo/SEC sources and existing UW access; no Morningstar subscription is required.

| Phase | Deliverable | Acceptance |
|---|---|---|
| 1 | SEC filing inventory, annual financial reconciliation and a revised Adobe assessment | Dated issuer identity and source checks; newer filings reopen content review; provider mismatches remain visible; no automatic fundamental gate passes |
| 2 | Broader eligible US listing catalog and reliable report refresh | Discovery independent of tracked names, bounded provider load, explicit catalog/sample coverage; queued jobs survive restarts, retry safely and refresh after completed sessions |
| 3 | Sector-specific valuation and point-in-time outcome measurement | Separate bank/REIT/unprofitable-business input contracts, honest unavailable states, frozen cohort membership, benchmark-relative completed-session returns and coverage accounting |

All three software phases are implemented; production release verification is pending. Substantive issuer judgments can remain insufficient after research, and performance horizons need time to mature. An empty qualified list is a valid conclusion.

## Phase 1 design

Fetch SEC submissions and companyfacts only for issuers with a reviewed CIK. Reuse the existing declaring User-Agent. Serialize requests, cache submissions for one hour and annual facts for 24 hours, preserve original observation timestamps and bound each enrichment pass to 45 seconds with five-second requests. A provider outage must preserve a usable report with explicit missing verification.

The submissions parser checks issuer CIK/ticker, aligned columns, filing identities, dates, document paths and full recent-history coverage of the source window. It excludes Forms 3/4/5/144 and their amendments; all other submissions in the window require a dated disposition before qualification. This is an inventory, not a materiality or content opinion. Unfetched historical pages cannot silently establish coverage. Same-day records are retained; the date-based inventory is not an intraday point-in-time source.

Companyfacts extraction retains US-GAAP concepts, units, annual durations, filing dates and accessions. Future-filed observations are excluded. Stock compensation is a separate quantity, and diluted annual averages remain distinct from current outstanding shares. Different provider/SEC fiscal periods are flagged rather than relabeled. Numeric comparisons require the same period and allow 0.5% rounding tolerance; capex sign conventions are explicit. Source values do not silently replace the provider valuation model.

Issuer verification and reconciliation are frozen into the report, visible in company detail and included in CSV. Qualification requires a current issuer-matched inventory, current annual-fact verification, dispositions for every included filing and all existing sourced review gates. Adobe's updated review records supported historical cash-generation findings while durability, calibrated valuation, the full risk checklist and complete filing-content review remain insufficient.

Primary API contract: [SEC EDGAR API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces). The live probe on October 30, 2026 found 22 non-ownership Adobe submissions since January 15 and confirmed FY2025 operating cash flow, capital spending, stock compensation and diluted shares. Both SEC endpoints were also reachable from production before release.

Approved registry reviews can supply a missing competitive, valuation or value-trap assessment to the existing gate composer only after the full source/freshness/input contract passes. A contrary stored assessment remains blocking. Integration tests exercise this actual route path, not only an injected all-pass dictionary. Source accessions and publication dates must match the live filing inventory. Valid reconciled model inputs can resolve a provider discrepancy without hiding the original discrepancy.

## Phase 2 design and operation

The [Nasdaq directory definitions](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs) describe the free Nasdaq and other-exchange files. A dated catalog filters test issues, ETFs and identifiable preferred shares, debt, warrants and units; common/ordinary/capital-share and ADR names remain discovery candidates. This is not a permanent-identifier security master or verified fundamentals coverage. A live pre-release probe identified 3,373 Nasdaq candidates and 2,264 other-exchange candidates before cross-file deduplication.

Each refresh retains up to 12 explicitly reviewed symbols, up to 20 screen results and a rotating directory allocation within the existing 60-name maximum. It fills unused capacity from covered US names. No Stock or watchlist membership is written. A Redis cursor preserves rotation position; unavailable cursor persistence is disclosed. The catalog caches for a day, retains a visibly stale fallback for up to seven days and otherwise falls back to Yahoo/UW discovery. Directory dates, counts, selection metadata and the catalog fingerprint are frozen into each report. Source membership changes can move rotation positions; catalog counts are not counts of current ratings.

Input cache contract v2 carries common-equity/common-income fields for bank models. Old v1 data is retained only as dated failure fallback, not treated as a fresh v2 contract. Existing adjusted histories are reused for outcome prices to avoid duplicate Yahoo requests.

Research-engine owns four additive tables: **valuation_report_jobs**, **valuation_report_job_control**, **valuation_report_cohorts**, and **valuation_report_outcomes**. Startup creates missing tables under a PostgreSQL advisory lock and bounded DDL timeouts; shared model enums and existing report tables are unchanged.

Manual generation commits a job before returning 202. It persists no user JWT. A background worker mints an internal token for each attempt, claims one active job under database locks, renews a two-minute lease every 30 seconds and rejects expired or superseded worker tokens. A pre-save owner check fences report publication. Three attempts and a two-hour job age limit bound retries; input-refresh contention waits without consuming the attempt budget. Terminal failures preserve the last successful report. A five-minute enqueue cooldown survives restarts. Previously issued Redis job IDs remain readable until their existing expiry.

The worker checks the official US calendar and queues a refresh after the completed-session close plus 30 minutes when that session has no saved report. Session markers prevent duplicate scheduled jobs after restarts. All-stale input sets cannot supersede the last current report. **VALUATION_WORKER_ENABLED=false** disables the worker for controlled maintenance; it should remain enabled for normal generation.

An isolated production-PostgreSQL probe verified four concurrent enqueue requests produce one job, four claimers produce one active worker, a crashed lease is reclaimed and the superseded worker cannot finish. The probe schema was removed afterward.

## Phase 3 models

- **Banks:** common equity and attributable common income over consecutive annual periods feed a steady-state justified P/B sensitivity. This uses equity discount rates and does not subtract deposit debt from common equity a second time. Generic ROE/growth/rate scenarios remain uncalibrated and require issuer credit, capital and funding review. Missing common metrics are not substituted with total equity or total earnings. Basis: [financial-service value drivers](https://pages.stern.nyu.edu/adamodar/New_Home_Page/littlebook/bankvaluedriver.htm).
- **REITs:** a reviewed issuer FFO-per-diluted-common-share input, its fiscal period and sources feed explicit 12x/15x/18x sensitivities. Missing FFO stays unavailable; total depreciation does not stand in for property depreciation. FFO multiples are not a calibrated NAV or owner-cash valuation. Basis: [Nareit FFO](https://www.reit.com/glossary/funds-operation-ffo).
- **Loss-making nonfinancial businesses:** explicit five-year FCFF scenarios may contain early losses, but require a positive terminal flow, bounded discount/terminal assumptions, projected diluted shares and sufficient existing funds. The funding test reserves reported debt and senior claims upfront; companies needing new financing require a fuller financing/ownership model. Without sourced forecasts, the report shows a historical cash-runway diagnostic and unavailable value.

All paths retain source, liquidity, freshness and qualification requirements. A new method is marked as a methodology change rather than presented as a newly changed current rating. Other unsupported financial/real-estate business types remain explicitly unsupported.

## Phase 3 outcome measurement

Current and previous reports encountered during generation register frozen cohort definitions. Separate append-only observations measure qualified opportunities and below-model-value research against SPY at 5/20/60 completed sessions. The source report ID, version, policy and both publication/cutoff timestamps are retained. Entry is the first close strictly after the later timestamp, preventing reuse of the earlier screen close as an attainable entry.

The idle worker reserves due cohorts with a fenced lease, fetches only required adjusted price histories and stores new observations without changing prior outcomes or membership. Price collection caps at 61 symbols and two concurrent requests, reuses existing fresh histories, clips forming sessions and never substitutes unadjusted closes. The provider adjustment basis and original retrieval times are retained. Historical adjustment revisions remain a measurement limitation.

Any missing stock, delisted name or missing benchmark prevents an aggregate cohort return; available individual observations remain visible in the evidence and expected-member counts stay fixed. Future horizons are pending and empty cohorts are distinct from zero-return portfolios. Excess is arithmetic percentage-point return minus SPY, before trading costs or slippage. This is prospective descriptive measurement, not evidence that the model predicts returns.

GET **/intel/valuation/performance/{report_id}** reads the latest stored observation with the same public-report checks as report reads. It initiates no provider calls. The frontend displays horizon status, observation coverage, benchmark/excess returns and a separate outcome CSV. Existing snapshots not yet registered show that status explicitly. Measurement errors retain prior observations and schedule another attempt.

## Release and rollback

Rebuild market-data, research-engine and frontend using the pinned rollout's **--data-enrichment** mode. Startup initializes only the new research-owned tables; verify the worker and all four tables before generating a snapshot. Rollback images remain tagged. Additive job/cohort/outcome tables can remain after an image rollback; avoid dropping queued work or historical observations. Full provider captures and browser screenshots are kept locally and excluded from git.

The rollout accepts an optional third argument for a distinct host evidence directory. This release uses **/home/ec2-user/deploy-evidence/2026-10-30-valuation-phases**. Local validation passed 1,003 frontend tests, 227 selected research tests, 22 market-data tests, 14 gateway coverage tests and TypeScript checking. The separate host async gateway harness hung and was stopped; focused route coverage passed, and production HTTPS acceptance covers the release's actual authenticated routes. Two missing tier-476 label/color entries in the existing improvements tracker were repaired after the full frontend suite exposed that rendering omission.

Remaining evidence limits are company-specific calibration, filing-content review, fully diluted ownership and any missing sector inputs. Discovery does not provide same-day ratings for every catalog entry. Realized outcomes cannot be claimed before their sessions have elapsed.
