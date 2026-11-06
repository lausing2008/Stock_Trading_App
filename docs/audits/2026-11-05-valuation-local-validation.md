# Valuation report local validation — November 5, 2026

Scope: continue the three-phase implementation locally. The user explicitly prohibited production deployment. This continuation made no production requests, deployments, pushes or database changes.

## Historical-snapshot acceptance correction

The report store already marks a previous report `superseded` when inserting a newer version. Its valuation payload remains frozen. The live acceptance helper incorrectly compared the entire old response, including lifecycle status, with its pre-generation response.

The corrected assertion requires `superseded` when a different latest report exists, preserves status when generation reuses the same report, and compares every other serialized field exactly. The local SQLite workflow now verifies the entire historical response against the original response with only status changed. Offline assertion tests reject modified valuation data, coverage, version and invalid status changes. This diagnoses the earlier acceptance failure from the code contract; it does not establish the current production state.

The workflow's qualification fixture also mixed fixed October evidence with the execution date. It now uses the evidence fixture's clock for that section, preventing test expiration without relaxing application freshness requirements.

## Validation

From `services/research-engine`:

```bash
python -m pytest -q tests/test_valuation_acceptance.py tests/test_valuation_report.py tests/test_valuation_jobs.py tests/test_valuation_worker.py tests/test_candidate_qualification.py tests/test_filing_verification.py tests/test_financial_verification.py tests/test_sector_valuation.py tests/test_valuation_performance.py tests/test_issuer_facts_and_valuation.py
```

Result: **116 passed**. Providers and service boundaries are mocked in the local workflow; the database is temporary SQLite. No frontend or production application code changed in this continuation. Earlier validation totals in the implementation plan are historical, not rerun results.

## Remaining work

- Live API/browser acceptance remains pending. No live acceptance or deployment was attempted during this continuation.
- Adobe remains research-only until filing-content dispositions, durability, calibrated valuation, dilution and other required risk checks have adequate sourced support. The software does not approve those judgments automatically.
- Cohort outcomes require completed market sessions and full price coverage. This local validation establishes neither realized returns nor investment performance.

Implementation plan: [three-phase roadmap](../features/2026-10-30-valuation-phased-roadmap.md).
