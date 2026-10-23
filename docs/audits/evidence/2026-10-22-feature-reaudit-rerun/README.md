# Re-audit witnesses, rerun after the R01–R07 fixes

Report: [Options, off-exchange activity and AI Signals](../../2026-10-22-options-dark-pool-ai-signals-reaudit.md), section 9.

This directory is separate on purpose: the original witnesses and their results in [../2026-10-22-feature-reaudit/](../2026-10-22-feature-reaudit/README.md) are unchanged and remain the record of what the audit observed at `994e967b`.

Scope: HEAD `b997cadb` plus the uncommitted local fixes. No production request, database query or deployment.

## Why a separate script

The original `probe.py` **asserts** each defect, so after a fix it stops at the first assertion instead of recording what it now sees. Its R07 witness also re-implements the old `sort_values(...).head(3)` itself rather than calling the route, so it reproduces the defect whatever the route does. `rerun.py` records each witness's observation without asserting, and runs R07 through the real `get_options_flow` body using the maintained regression harness.

```bash
cd services/market-data
python3 ../../docs/audits/evidence/2026-10-22-feature-reaudit-rerun/rerun.py
```

It writes only `rerun-results.json` in this directory.

## Results

Latest run: after R01–R07 were fixed locally (`rerun-results.json`, `ui-witness-rerun.txt`). `reproduced: true` means the defect is still observed.

| Witness | Reproduced | Note |
|---|---|---|
| R01 provider failure (real provider → route) | no | A synthetic upstream exception now reaches the route as `status: upstream_error`, not `no_data`. The scanning wrapper still returns `[]` by design |
| R03/R04 missing inputs (adapter) | no | Moneyness without a reference is `None`; missing volume/OI/IV are `None` |
| R05 identity (real chain rows → game plan) | no | `option_symbol` and `contract_terms` kept; the adjusted contract is refused (`covered_call: null`, refusal counted) |
| R07 tied selection (real route) | no | Forward and reversed give the same set; 1 distinct selection over 20 shuffles |
| R08 pressure reciprocals | yes | Open, not in this pass |

UI witnesses (`ui-witness-rerun.txt`): the saved `ui-witness.test.tsx` was copied temporarily into `frontend/src/__tests__/`, run, and removed. A witness passes while its defect is present.

| Witness | Result | Meaning |
|---|---|---|
| Cached flow hides a failed refresh (R02) | **fails** | Defect no longer observed |
| Print age is a frozen response value (R02) | **fails** | Defect no longer observed |
| Held fill disappears without a quote (R06) | passes, **for a different reason** | Its fixture row has no contract terms, so R05 now refuses it as unidentified before R06's logic is reached. `ui-witness-r06-with-terms.test.tsx` gives the same row standard terms and observes the fix: the row is kept, priced from the fill, with no current mark |

An earlier run in this directory, after R03/R04/R06/R07 only, recorded R01, R05 and both R02 witnesses as still reproduced; that intermediate output was overwritten by the latest run.

The ML calibration witness (R11/R12) is not rerun here: R11–R14 are pending review and their code is unchanged.

## R05 exclusion measurement

`r05-exclusions-latest-session.sql` and `.out.txt`: a read-only production query on each symbol's latest archived session. 285,643 contracts across 150 symbols, all standard; none adjusted, missing or unreadable. The archive-wide breakdown and open options-income positions were not measured.

