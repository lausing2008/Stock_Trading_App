# Evidence and verification record

Review date: November 6, 2026, America/Los_Angeles.

## Scope and source identity

Repository HEAD at inspection: `49802ce516fe064e9356d90d1bd675a442097d4a`. The checkout was already dirty, including market-data routes/main/dark-pool work, valuation work, backup scripts, and untracked options-study modules. These were read where relevant and left unchanged. [source-manifest.json](source-manifest.json) records hashes for the principal inspected files and the non-audit worktree status at capture. This is a local source snapshot, not an atomic production inventory.

Review methods: targeted source reads and call-path tracing, recent audit/design reconciliation, actual-function synthetic witnesses, focused existing tests, and primary-source research. No live broker, paid market-data, production DB, Redis mutation, retraining, calibration, email or deployment action was performed. No application or test source was edited. The only created files are this documentation package and its source manifest.

The requested feature families were inspected: AI Signals, RVOL, Volume Profile/Fixed Range VP, VWAP, HMM, Little RZY, news/events, FVG, squeeze scoring and options. Cross-cutting inspection included entry authority/fallback, model evaluation, outcome/receipt attribution, data basis, metrics, risk controls, learning content, gateway and compose boundaries. This is not exhaustive line-by-line review of every service, production penetration testing, or a full statistical re-evaluation of all historical datasets.

## Fresh existing-test runs

Executed with `PYTHONDONTWRITEBYTECODE=1` and pytest cache disabled. All are local targeted checks; no production database or full integration suite was run.

| Working directory | Test selection | Result |
|---|---|---|
| `services/technical-analysis` | `tests/test_fair_value_gaps.py` | **14 passed** |
| `services/market-data` | `tests/test_short_squeeze_score.py tests/test_session_elapsed_rvol_thresholds.py tests/test_value_area_breakdown_alert.py` | **56 passed** |
| `services/signal-engine` | `tests/test_f04_ml_unavailable.py tests/test_f01_calibration_query_to_bucket.py tests/test_f02_daily_input_contract.py tests/test_overextension_guards.py tests/test_sa33_early_recovery.py` | **55 passed** |

Command shape: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider <selection>`.

**125 existing tests passed across these invocations.** Passing tests establish the tested behaviors, not predictive edge or absence of other defects. In particular, the existing FVG tests pass while the new synthetic disconnected-coverage witness exposes an untested case. Squeeze tests pin the existing formula; they do not validate the economic interpretation of that formula.

## Actual-function witnesses

The probes extracted named pure functions/classes from the local source via Python AST (FVG/HMM) or the same bounded source block used by the existing squeeze tests. They executed those actual definitions with synthetic inputs, avoiding service imports, providers and model fitting. They were run from stdin and did not add test/source files.

### W1: disconnected FVG overlap discarded

Input bars, all with internally valid high/low/close values:

| Index | High | Low | Close |
|---:|---:|---:|---:|
| 0 | 100 | 99 | 100 |
| 1 | 111 | 99 | 110 |
| 2 | 112 | 110 | 111 |
| 3 | 103 | 100 | 102 |
| 4 | 110 | 107 | 109 |
| 5 | 107 | 103 | 106 |

Called actual `detect_fair_value_gaps` and selected `idx == 1`. Gap boundaries are `[100,110]`. Later overlaps are `[100,103]`, `[107,110]`, `[103,107]`; their union is the complete interval. Expected under the documented cumulative-coverage rule: `filled=True`, `filled_idx=5`.

Actual output:

```json
{"top":110.0,"bottom":100.0,"kind":"bullish","idx":1,"filled":false,"filled_idx":null}
```

This establishes a computation defect for that input; not that any specific live symbol has exhibited it. A fix must preserve OHLC's limitation: a bar spanning a range is the algorithm's coverage assumption, not proof of trades at every interior price.

### W2: HMM VIX tie-break direction

Called actual `_label_states` with identity inverse-scaling and raw model means `[VIX, SPY_5d_return, IWM_vs_EMA200]`:

```text
state 0: [10,  .02, 0]
state 1: [30,  .02, 0]
state 2: [20,  .00, 0]
state 3: [40, -.02, 0]
```

Expected by the function's declared lower-VIX tie-break: `[0,1,2,3]`. Actual: **`[1,0,2,3]`**. The primary return sort works; the secondary VIX sign conflicts with the specification. The witness concerns an exact tie, not a measured incidence rate in production.

A separate attempt to inspect local `hmmlearn.base.ConvergenceMonitor` could not run because hmmlearn is not installed in the local Python environment. The convergence concern in A06 is supported by inspected usage and the official library documentation, not a fresh installed-library test. Requirements declare `hmmlearn>=0.3.0`; production package identity remains unverified.

### W3: squeeze DTC weighting and availability ratio

Called actual `compute_short_squeeze_score` with short float 20%, momentum score 50 and daily change 2%. Other optional values absent:

| Input | Short float points | DTC points | Momentum points | Change points |
|---|---:|---:|---:|---:|
| DTC = 1 | 24 | **30** | 10 | 2 |
| DTC = 5 | 24 | **0** | 10 | 2 |

With DTC 5, `short_shares_available=100`, `short_interest=90`, the function also outputs `uw_utilization_pct=90.0` and `uw_utilization_pts=5.0`. This pins the formula only. It does not establish that those provider fields are comparable quantities or true utilization; provider schema/universe/timestamp verification remains required.

### Minimal reproduction pattern

The FVG/HMM witnesses can be repeated without modifying the repository:

```python
import ast
from pathlib import Path

def actual_definitions(path, names, namespace):
    tree = ast.parse(Path(path).read_text())
    nodes = [n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)
    return namespace
```

For W1 pass names `FairValueGap`/`detect_fair_value_gaps`, namespace containing `pd=pandas` and `dataclass=dataclasses.dataclass`, and the table above as a DataFrame. For W2 pass `_label_states`, namespace `np=numpy`, and the four-row means with an `inverse_transform` identity method. For W3 the existing `test_short_squeeze_score.py` contains the exact source extraction helper; use the inputs above. These are synthetic unit-level witnesses, not API/integration tests.

## Current source versus historical records

| Topic | Current local observation | Historical record used carefully |
|---|---|---|
| ML unavailable | Suppression/fallback tests pass | [October 28 deployment/replay](../audits/2026-10-28-f04-ml-unavailable-replay.md) |
| Final threshold/report split | Adjacent split remains in trainer | Existing held F04 plan; artifact counts from older captures are not asserted as current |
| Daily calibration | F01 projection and F02 basis tests pass | October review's then-zero verified outcomes is historical, not today's measured count |
| Delivery attribution | Reader still uses daily Signal delivery state | F03 observation deployment does not itself establish receipt-specific outcomes |
| Intraday | Separate contracts/read APIs; production generation false | [Stage C final review](../audits/2026-10-29-intraday-stage-c-final-review.md) cleared contract-only scope |
| RZY | Deterministic rules and confirmation examined | [October 26 review](../audits/2026-10-26-little-rzy-ai-signals-full-review.md) cites October 20's 692-trade negative-net study; not rerun here |
| Options | Structure/contract/lead semantics inspected | [October 31 study](../audits/2026-10-31-options-candidate-study-results.md) reviewed, not rerun; its held-out period is now examined data |
| Decision authority | Primary DE plus local fallback | July architecture description is stale for this path |

## External references

Browsed during this review. Each source supports definitions or validation practice, not the profitability of this platform's features.

- [FINRA short-interest metadata](https://www.finra.org/sites/default/files/Equity_Short_Interest_Data_File_Download_API.pdf): days-to-cover definition.
- [FINRA: short interest versus short-sale volume](https://syndication.finra.org/content/short-interest-what-it-what-it-not): distinct populations and reporting meaning.
- [IBKR Orbisa securities lending](https://www.ibkrguides.com/clientportal/researchmenu/orbisa-securities-lending.htm): utilization denominator and lending context; not proof of the current provider's field contract.
- [hmmlearn API](https://hmmlearn.readthedocs.io/en/stable/api.html): convergence monitor semantics.
- [scikit-learn probability calibration](https://scikit-learn.org/stable/modules/calibration.html): reliability diagrams, proper scores and separation of calibration fitting from evaluation.
- [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf): strategy-selection overfitting.
- [OIC option price behavior](https://www.optionseducation.org/referencelibrary/faq/option-price-behavior): option value sensitivities and why underlying direction is insufficient.
- [FINRA Regulatory Notice 16-19](https://www.finra.org/sites/default/files/notice_other_file_ref/Regulatory-Notice-16-19.pdf): stop orders and volatile-market execution risk.

## Required follow-up evidence before implementation claims

Read-only current production inventory and source parity; provider data/entitlement/delay and borrowing-field contracts; verified eligible outcome counts; precise receipt attribution; qualified model coverage and reporting windows; current scheduler latency/coverage; option quote age/size; and portfolio-level costs/drawdown. None can be replaced by adding confidence points or by citing passing unit tests.

For the documentation package itself, verify relative Markdown links, source-manifest hashes, example arithmetic and that the final diff adds only this folder. Application builds and broad service tests are not required for prose-only changes; targeted runs above were audit evidence.

**Final documentation verification:** 39 relative Markdown links resolve; all 31 recorded source hashes still match; worked-example sizing and expectancy arithmetic checks pass; `git diff --check` reports no whitespace errors. Final worktree status retains the pre-existing changes and adds only `docs/2026-11-06-platform-audit/` for this task. These checks do not validate external websites indefinitely or establish production parity.

## Whole-platform extension

The subsequent request expanded the review to institutional-quality architecture, operations, security, portfolio/execution and usability. Added documents: [platform review](platform-review.md), [consolidation](consolidation.md), [UX specification](experience-design.md), [controls/roadmap](institutional-roadmap.md). This extension introduces ten scoped findings P01–P10; they are source-established behaviors or architectural assessments, not new production incidents.

Fresh inventory counted 76 `.tsx` page components excluding `_app`, `_document` and API handlers, 12 `services/*/src/main.py` entrypoints and 115 classes declaring `__tablename__` in `shared/db/models.py`. Separate Intraday declarations are not included in that ORM-file count. The [extension manifest](platform-source-manifest.json) records inspected source hashes and counts; it does not attest deployed schema or runtime settings.

Additional reads covered global navigation, auth/session/password change, broker credentials/submission/interfaces, conditional orders, portfolio-risk subset/weight handling, Copilot action drafts, typed narration, workspace preferences, valuation qualification, DSL evaluation, startup DDL, CI, deployment and backup tooling. The saved October [desktop workspace image](../features/assets/trading-workspace/trading-workspace-desktop.png) was visually inspected; it contains sample data and is not a fresh live UI test. No full application rendering, accessibility scan, dependency installation/scan, new application tests, live broker calls, load test, security exploit attempt or restore was performed. The earlier 125-test result is not claimed as a newly repeated run for this extension.

Primary-source references checked for the extension:

- [OWASP ASVS](https://asvs.owasp.org/): security verification framework for the proposed session/authorization controls.
- [NIST AI RMF core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/): organization of AI governance, measurement and management.
- [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/): proposed accessibility target; no current compliance assertion.
- [PostgreSQL 16 continuous archiving/PITR](https://www.postgresql.org/docs/16/continuous-archiving.html): recovery architecture beyond logical dumps.
- [Vercel's middleware-bypass postmortem](https://vercel.com/blog/postmortem-on-next-js-middleware-bypass): historical Next.js security patch context. Source package pin is 14.2.5; no middleware file was found by the inspected filename search. Exploitability and current installed dependency health were not established. The historical patched version is not presented as today's upgrade recommendation.

The documents deliberately leave production flag values, actual backup scheduling, current broker-state reconciliation, secret-storage row formats, provider entitlements and measured service objectives as verification items. Repository capability cannot establish these facts.

**Extension verification:** all 73 relative links in the expanded package resolve, all 41 extension source/artifact hashes match, and `git diff --check` passes. The four new design/review documents contain approximately 10,000 words. Existing application changes remain untouched; this task's additions/updates are confined to the audit documentation directory.

## Verification follow-up and index housekeeping

The user supplied an independent file/link/hash review reporting 73 resolving links and matching 41/41 and 31/31 manifest entries at `49802ce5`. That review did not repeat the 125 tests or independently recheck A01–A18. Preserve those limits; it is not renewed production or performance validation.

Follow-up source inspection reconciled P02 as an already-documented R07 gap raised in priority, clarified P03's credential-decryption/rotation implications, and extended P05's weight behavior to both `portfolio_risk` and `portfolio_stress_test`. It also corrected an error in the original report and supplied verification: both endpoints select HSI by **strict majority of available HK symbols**, not “any HK stock.” The latter wording appears in a stale source comment. Ties use SPY; neither selection is exposure-weighted.

Added one audit index pointer under Audits in `.claude/CLAUDE.md`, preserving its pre-existing edits. This is the only follow-up edit outside this documentation directory. No source fixes, settings changes, commits or test reruns were performed; the audit folder remains untracked until explicitly committed.

The subsequent clarification adds concrete P05 examples showing that exposure weights do not affect the benchmark vote and missing history changes its eligible population. A one-HK-symbol example describes the formula in isolation, not a supported endpoint response: both routes require at least two requested and two available symbols. The stale application-code comment is intentionally left unchanged.
