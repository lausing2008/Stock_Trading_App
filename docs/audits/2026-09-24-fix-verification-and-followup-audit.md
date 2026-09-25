# Fix verification and follow-up audit — September 24, 2026

**Reviewed revision:** local `ac16d0e`; production checkout `ac16d0e7`.  
**Scope:** independently review DA-01–DA-12 remediation and adjacent training, valuation, news, authorization, and execution paths.  
**Previous report:** [September 23 deep audit](2026-09-23-system-deep-audit.md).  
**Evidence:** [offline behavioral probes](evidence/2026-09-24-followup-reproductions.py), [sanitized results and deployment checks](evidence/2026-09-24-followup-evidence.json).

**Later same-day addendum:** section 5 reviews Claude's remediation record at `ff55512`, including browser checks and three supplementary UI/workflow findings. The ten R-series findings below refer to the original follow-up pass.

## Assessment

**The release makes real improvements, but “all twelve closed” is too broad.** Several original reproductions are fixed, DA-02 is appropriately mitigated by disabling the blend, and DA-09 is explicitly still open. Other changes address one failure condition without restoring the larger invariant the original report required.

The most important additional finding is that final-model training appends resolved signal outcomes without a training-cutoff check. That can introduce later-period information even when the ordinary price-history split has a full embargo. The reduced-embargo change also interacts with existing calibration and threshold-selection fallbacks in ways its new tests do not check.

This report records **ten actionable follow-up findings**: six P1 and four P2. Most are remaining portions of earlier findings or adjacent defects, not ten regressions introduced by this release. Each section states the distinction. P1 is high priority for validity, protected state, or affected automation; P2 is a bounded reporting/research issue. The account remains sandbox; no real-money loss or production exploit was demonstrated.

No application code, configuration, Redis flags, orders, training artifacts, or deployments were changed. Earlier audit documents and `CLAUDE.md` remain unchanged.

## 1. What was independently verified

Production was read at **2026-09-25 00:39 UTC**, which is **September 24, 17:39 PDT / 20:39 EDT**.

| Check | Result | Limit |
|---|---|---|
| Running source | 13 selected hashes match local across seven service containers, including ML's shared auth helper | Supports deployment of these changes; not a fleet-wide or immutable-image “zero drift” certification |
| Meta blend | Redis flag absent; current code defaults to disabled | Confirms the blend is off at this snapshot; does not validate the dormant model contract |
| Equity paper trades | GROWTH 65 closed; SWING 62 closed / 1 open; LONG 2 open | 127 closed / 3 open; no mature LONG cohort |
| Paper broker order IDs | Zero entry and exit IDs | Broker integration defects remain latent in these stored trades; actual broker account was not queried |
| Options income | Nine open cash-secured puts; no closed positions | First expiry is **September 25**, not September 24; still no realized options-income win rate |
| Meta outcome population | 18,687 resolved labels in the query's capped population | Signal outcomes are not equivalent to executed paper trades |

The production source hashes, aggregate queries, and feature-flag read are retained in the evidence JSON. No model artifact was deserialized and no training was run. I did not independently repeat the reported 180-symbol feature-history measurement; its stated horizon conflicts with the current registry, as explained below.

### Original finding disposition

“Core fixed” means the specific original defect has a source remedy and relevant passing tests. It is not certification of the complete subsystem.

| Original ID | Independent disposition | Remaining scope |
|---|---|---|
| DA-01 | **Partial:** global date sorting and train-only nonconstant-column selection are present | Same-session split, label availability/purging, early-stop reuse, and historical-AUC comparison remain; R03 |
| DA-02 | **Mitigated:** disabled blend verified in production | Training/inference contract remains incompatible; returned model identity still claims meta participation; R09 |
| DA-03 | **Partial:** gaps and shortfalls are recorded | Partial gaps still leak; no eligibility consequence; smaller slices activate in-sample threshold fallback; R02 |
| DA-04 | **Core fixed:** fill-bar tracking books terminal exit and same-bar round-trip costs | Strategy suite, including terminal-cost regressions, passes; no new historical performance certification |
| DA-05 | **Partial:** intraday settlement is blocked until 16:15 ET | Clock passage does not prove the stored bar was refreshed/finalized; R04 |
| DA-06 | **Partial:** five-day query bound and intrinsic floor are present | Within-window stale time value and persistent mark provenance remain unresolved; R08 |
| DA-07 | **Core fixed:** unique ownership, compare-delete release, fail-closed acquire | Expired workers can still overlap a successor; no renewal/fencing/database serialization; R06 |
| DA-08 | **Core fixed:** ordinary user tokens denied model mutation; explicit service claim added | Previously issued admin tokens survive account disable/delete until otherwise invalidated; R07 |
| DA-09 | **Open, narrowed:** negative/older non-material stories blocked | Unrelated positive stories clear; material stories bypass the recency guard entirely; R05 |
| DA-10 | **Core fixed:** both pollers commit remaining confirmations unconditionally | Targeted mixed-batch tests pass; durable broker lifecycle still separate work |
| DA-11 | **Partial:** return, volatility, and Sharpe now reflect cash; sleeve fields identified | Historical drawdown is still copied from the fully invested sleeve; R10 |
| DA-12 | **Accepted compatibility fix:** new collateral-yield field and explicit UI labels | Ranking intentionally retains premium-on-spot; changing it requires a new study, not a silent definition change |

DA-02's disablement and DA-12's separate field are sensible choices. Neither should be undone merely to make every row say “implemented.” DA-03's metadata is useful diagnostic work, but does not turn compromised evaluation into clean evidence.

## 2. Findings, solutions, and acceptance checks

### R01 — Final training can include outcomes from after its evaluation boundary

**P1; newly identified path, pre-existing code.** [trainer.py](../../services/ml-prediction/src/training/trainer.py), `_load_outcome_features` at 414, outcome preparation at 610–672, final augmentation at 845–859.

`_load_outcome_features()` loads resolved BUY outcomes across the lookback window. It returns feature rows indexed by signal date, but not the time each outcome became knowable. `train_model()` removes overlapping dates from the main dataset where viable, defines the chronological splits, and then appends **all** retained outcome rows to `_fit_X` at double sample weight. There is no filter requiring those rows or their labels to precede the training cutoff.

Deduplicating dates does not solve this. Even if a recent outcome date is removed from the synthetic holdout, training on that later event still violates a historical prediction evaluated on earlier holdout dates. The fallback that drops overlaps from `X_out` likewise does not establish a cutoff for surviving rows.

**Probe:** executing the actual augmentation block with base training through session 139 and 20 later outcome rows appends sessions 180–199 at weight 2.0, despite an illustrative evaluation boundary at 170. This is a controlled demonstration of the missing filter, not a measurement of contamination in every deployed artifact. No model was trained.

**Solution:** preserve `feature_available_at`, `label_end_at`, and `label_available_at` through deduplication. Split first, then admit augmentation rows only if their information was available before that fold's cutoff. Evaluate a frozen candidate without later outcomes; a separate production refit may use newer data but must not inherit the candidate's held-out metrics as if they described that refit exactly. Also reconcile outcome `is_correct` with the base forward-return target and use the same feature inputs: this loader currently rebuilds with `macro_df=None` and without the richer inputs used by the main training path.

**Acceptance:** construct outcomes before, inside, and after each holdout, including early-resolved and delayed-resolved cases. Capture every row passed to `model.fit`; no post-cutoff feature or label may appear. Check feature/target definitions as well as dates. This test must exercise the augmentation path, not only `TimeSeriesSplit`.

### R02 — Partial embargo remains unsafe, and smaller slices disable calibration / honest threshold reporting

**P1; remaining DA-03 problem plus an interaction exposed by the new rule.** [trainer.py](../../services/ml-prediction/src/training/trainer.py), horizon registry at 34–39, split logic 769–814, calibration 883–894, threshold split/fallback 912–933, suppression and artifact write 984–1047.

Three separate issues matter:

1. `_afford(span)` preserves ten rows per slice, even when the resulting gap is shorter than the label horizon. The shortfall is stored in metrics, but is not consulted by suppression, artifact replacement, or ensemble eligibility. Logging leakage is not a restriction on using it.
2. Preserving **ten** rows conflicts with later stages: probability calibration requires at least **twenty**, and independent threshold reporting requires at least **ten remaining after halving**. A ten-row test set falls back to fitting the threshold and reporting results on those same ten rows. The fallback is logged, but its evaluation status is not recorded alongside the new embargo status.
3. The fix rationale and tests cite **GROWTH/28**. The actual shared `_HORIZON_BY_STYLE`, imported by the API, defines **GROWTH=15**. The claim that enforcing a full gap would disable all 180 symbols is therefore not established for the current default by the measurement as documented. This is not a claim that all symbols have enough data; it is a requirement to rerun the measurement using the actual path and post-filter row counts.

**Actual-source split probe, 200 dense clean observations:**

| Style / current horizon | Gap at calibration→test | Calibration rows | Test rows | Last calibration label ends at | Test starts at |
|---|---:|---:|---:|---:|---:|
| SHORT / 5 | 5 | 15 | 15 | 184 | 185 |
| SWING / 10 | 10 | 10 | 10 | 189 | 190 |
| GROWTH / 15 | 10 | 10 | 10 | 194 | 190 |
| LONG / 20 | 10 | 10 | 10 | 199 | 190 |

The dense-row example makes the overlap explicit; irregular/dead-zone-filtered rows require actual timestamps, not assuming every row equals one trading session. For all four examples the calibration slice is below twenty rows. The ten-row threshold probe confirms the same observations are selected on and reported on. With sufficient classes, the previous twenty-row case could have retained a ten-row reporting holdout; the new gap can remove that separation indirectly.

**Solution:** design split sizes jointly with downstream requirements. Use label availability to purge, then select a viable evaluation protocol: fewer/wider walk-forward windows, a prior frozen threshold/calibrator, or a separately validated pooled model. Keep research-only candidates if useful, but persist `evaluation_valid`, `calibration_status`, `threshold_evaluation_mode`, date ranges, class counts, and effective sample counts. Do not overwrite/promote or assign confidence-weighted trading influence based on knowingly invalid metrics. When no valid model is available, make the missing ML contribution explicit instead of inventing evidence.

Training availability and evaluation validity are separate decisions. It is possible to keep a research training job running without presenting its fitted metrics as validated trading performance. Scikit-learn's temporal splitter documents ordered splits and an explicit exclusion gap; an adaptive gap still needs to satisfy the label contract. [TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).

**Acceptance:** run the full split→fit→calibration→threshold→report chain at minimum and typical row counts for the actual four horizons. Test that a full-gap result can still fail eligibility for insufficient calibration/reporting data. Invalid candidates must not silently replace an eligible incumbent. Never assert merely that the gap is positive.

### R03 — Sorted meta records still do not provide clean promotion evidence

**P2 while blending is disabled; prerequisite to re-enabling. Remaining DA-01.** [meta_trainer.py](../../services/ml-prediction/src/training/meta_trainer.py), lines 299–432.

Global sorting fixes the original symbol-block error, and train-only feature selection is correct. But the 80/20 **row** split can divide one session across train/validation. Five symbols over four dates yield September 4 in both slices; the new guard accepts equality. More fundamentally, records carry signal date without label availability/end dates, so even strictly earlier training signals can have outcomes resolved inside the validation period.

The same validation set still selects early stopping and supplies the promotion AUC. Promotion still compares against the prior artifact's historical AUC, not the incumbent on the same current untouched observations. These remaining issues were already in the original recommendation; sorting alone does not close them.

**Solution:** group by decision session, purge using label availability, and separate early stopping from final comparison. Score incumbent and challenger on the same future holdout; retain observation counts, date ranges, and clustered uncertainty. Keep the blend disabled until R01–R03 and the semantic contract are addressed.

**Acceptance:** interleaved symbols with tied dates and varying resolution lags; assert the last training label was available before the validation decision boundary. Verify promotion neither reuses the early-stop window nor compares incomparable historical cohorts.

### R04 — Settlement checks the clock, not whether ingestion completed

**P1; remaining DA-05 failure under delayed/missing refresh.** [options_income_engine.py](../../services/market-data/src/services/options_income_engine.py), `settlement_session_is_final` at 510 and `_settlement_close` at 527; [Price schema](../../shared/db/models.py), line 146.

The 16:15 ET guard correctly blocks the original intraday manual call. However, after that time—or on any later day—the lookup accepts any D1 row with the expected date. `Price` has no finality or last-refresh field checked here. A row last refreshed at 15:00 remains eligible if the final ingestion failed. A fixed buffer is not proof that ingestion succeeded.

**Probe:** the actual clock guard and lookup at 17:00 ET accept a controlled same-date $101 row representing the unfinished price. If the actual final close was $90, the same original put-settlement misclassification remains possible. This audit did not observe a production mis-settlement; nine positions remain open.

**Solution:** settle only from a recorded final-session close, carrying source/session/fetched-at/finality. A settlement-specific final-bar ingestion record is a smaller first step than rebuilding every price consumer. On outage, leave the position pending and retry. Use an exchange calendar for the expected session; a conservative time gate can remain as an additional condition.

**Acceptance:** load an intraday bar, advance the clock beyond the buffer without refreshing it, and verify no settlement. Then publish a finalized close and verify exactly one settlement with persisted evidence. Include provider failure and next-day recovery. This is the highest-priority options check before September 25 expiry.

### R05 — News clearing remains unrelated to event resolution; material news bypasses the new guard

**P1; acknowledged DA-09 gap plus another bypass.** [news storage](../../services/news-intelligence/src/services/storage.py), `_mark_hot` at 36, `_may_clear_negative_flag` at 93, and material/clear branches 289–313.

The new helper blocks negative non-material stories and some out-of-order clears. Two important paths remain:

- A newer unrelated positive/neutral non-material story still clears the negative event. This is explicitly acknowledged in the fix and reproduced here.
- Any material non-macro story takes `_mark_hot()` directly, **without invoking the recency guard**. An older material-positive article ingested late overwrites an unresolved negative flag and removes its negative interpretation. The reproduction confirms the positive overwrite call and zero guard calls.

Additionally, the flag's `ts` is ingestion time (`datetime.now`), while the new helper compares the follow-up's publication time against it. These are different clocks. A valid correction published after the adverse article but before its delayed ingestion can be rejected. This last point is source-traced, not a measured production frequency.

**Solution:** retain multiple active material events, both publication and availability times, and explicit supersession/retraction links. Derive the symbol's negative state from unresolved events. A useful interim measure is preventing unrelated positive/neutral stories from removing an active negative flag, including via overwrite, while preserving expiry and a reviewed linked-correction path. Make updates atomic to avoid a read/check/delete race against a newer event.

**Acceptance:** unrelated newer positive, older material positive, delayed real correction, two simultaneous adverse events, and concurrent flag replacement. Only resolution of the relevant event or expiry should remove its effect.

### R06 — Correct lease release does not prevent concurrent work after expiry

**P1 for serialized financial mutations; latent overrun scenario. Remaining DA-07 architecture.** [options_income_engine.py](../../services/market-data/src/services/options_income_engine.py), lock wrapper 878–950 and worker 956 onward.

The release/acquisition fixes are correct. But there is still a fixed 1,800-second TTL, no renewal or fencing, and no portfolio row lock/idempotent intent protection in this path. If A remains active after expiry, B can acquire the lease and run while A continues updating its earlier portfolio snapshot. Compare-and-delete prevents A deleting B's lock; it does not stop A's work.

**Probe:** with the actual wrapper and a fake expiring lease, B enters its work callback while A is active; A then continues. No database duplicate was created. This proves overlap is allowed, not that a production run has exceeded 30 minutes.

**Solution:** add owned renewal and stop/abort on loss, plus database serialization or fencing around cash/position mutations and deterministic intent keys. The database must enforce the economic invariant even if a worker pauses or dies. Redis's lock guidance makes validity duration and ownership part of the safety model. [Redis distributed locks](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/).

**Acceptance:** two workers, an expired lease, and a stale worker attempting to commit. The second request must neither double-reserve cash nor duplicate the same intent. Test database results, not just whether another worker's Redis key survives.

### R07 — A disabled administrator's existing token still authorizes model changes

**P1; remaining account-state portion of DA-08.** [shared JWT auth](../../shared/common/jwt_auth.py), lines 80–110; [account toggle](../../services/market-data/src/api/auth.py), lines 528–543; delete route at 461.

Ordinary signed users are now denied, which closes the main privilege gap. However, `require_model_admin` accepts the token's role/service claim after signature/blacklist checks without reading current account state. Disabling a user changes only `is_active`; deleting a user also does not invalidate existing tokens here. The gateway does not add a live account-state check for `/ml`.

**Probe:** using an offline synthetic signing key, the actual toggle function disables a fake administrator; the actual model-auth dependency still accepts that administrator's previously issued token. No production credentials or endpoint were used. This is continued access by an existing privileged token, not a new ordinary-user escalation.

**Solution:** enforce current enabled role/account version for privileged actions, or revoke/version all tokens on disable/delete/security changes. Keep service principals separate and restrict capabilities to the actual job rather than granting every service claim every model mutation. OWASP recommends validating authorization at each request boundary. [OWASP authorization guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html).

**Acceptance:** signed ordinary, active admin, disabled admin, deleted admin, revoked token, and narrowly scoped service credentials through the real dependency chain. Disablement must take effect before JWT expiry.

### R08 — Five-day quote bounds still produce marks without durable freshness evidence

**P2 for current research accounting; remaining DA-06.** [options_income_engine.py](../../services/market-data/src/services/options_income_engine.py), lines 737–867.

The new floor correctly prevents a stale $2 ask from valuing a deeply ITM $20-intrinsic put at $2. The query also stops accepting arbitrarily old/future-dated rows. But it accepts up to five calendar days of age, returns only the ask despite selecting `as_of`, and records source counts only in logs. The equity history still lacks per-position quote timestamps/quality. Intrinsic provides no useful protection against stale **time value** for an OTM option.

**Probe:** a four-day-old $0.01 ask for an OTM put is inside the query window and becomes a $1 liability labeled `quote_ask`; its date is discarded. The current fair/executable ask is unknown, so this does not quantify an actual misvaluation. A missing underlying quote also falls back to entry price, meaning even the intrinsic computation may use a stale underlying.

**Solution:** define a documented mark-quality policy distinct from executable quote eligibility. Persist quote and underlying times, source, stale/fallback status, and uncertainty with each valuation. Display approximate/stale valuations as such; do not use a five-day archive allowance as a live execution freshness standard. Historical valuation also needs an underlying price selected as of that historical date, not the current `_fetch_live_prices` result.

**Acceptance:** stale but above-intrinsic ask, OTM option, stale/missing underlying, and historical date. Readers must reconstruct why an equity value was used without consulting ephemeral logs.

### R09 — Disabled meta still runs and is reported as an ensemble member

**P2; introduced inconsistency in DA-02 mitigation.** [trainer.py](../../services/ml-prediction/src/training/trainer.py), meta call 1450–1486, blend gate 1488, returned model metadata 1536–1544.

The flag gates only the numeric blend. The meta prediction still runs, and a non-null result adds `meta` to `model_probabilities` and `_meta` to the model name even when disabled. This misstates provenance and retains avoidable model/feature work.

**Probe:** with the flag false, base probabilities 0.7/0.6/0.5 and meta 0.9, the returned probability correctly excludes meta (0.59975 after the existing disagreement adjustment). Yet the result identifies `ensemble_xgb_lgb_rf_meta` and reports the meta probability. This is a metadata/latency defect, not a claim that the disabled blend is numerically active in production.

**Solution:** resolve the flag once before the call. Skip meta computation by default, or put intentional shadow inference in separately named diagnostic fields with an explicit zero applied weight. Report actual contributors and applied weights. A raw Redis flag alone is not an audit trail; a re-enable operation should persist actor, reason, model/schema versions, and validation evidence.

**Acceptance:** off/missing/unavailable flag produces no contributing meta member; optional shadow mode is explicitly labeled. On mode requires a validated feature/target contract and accurately reports applied weights.

### R10 — Optimizer drawdown still belongs to the fully invested sleeve

**P2; incomplete DA-11 metric correction.** [optimizer methods](../../services/portfolio-optimizer/src/optimizers/methods.py), `_metrics` at 89 and `ai_allocation` lines 410–426.

Expected return/volatility/Sharpe are now cash-aware. But `m = dict(sleeve)` also copies `max_drawdown`, which is never recomputed on returned weights. The generic response field still describes the fully invested path. Diversification is likewise retained from the sleeve and needs an explicit basis, although this finding's demonstrated numerical error is drawdown.

**Probe:** a one-asset path `[+10%, −20%, +5%]` with default 5% cash returns 19% expected volatility correctly, but reports −20% maximum drawdown. Recomputing with the returned 95% exposure and the declared zero-return cash yields −19%.

**Solution:** compute all total-portfolio metrics from the same returned allocation/return path, keeping sleeve metrics in explicitly named fields. State the cash return and rebalancing assumptions. Avoid patching each metric independently from a copied sleeve dictionary.

**Acceptance:** independent return-path reconstruction for zero/default/high cash, including drawdown, Sharpe, and weight totals. The new return/volatility tests pass but do not cover this retained field.

## 3. Suggested implementation sequence

| Order | Work | Concrete completion gate |
|---|---|---|
| 1 | Final settlement data evidence, R04 | Intraday row cannot settle after clock advance unless a final close actually arrives; pending/retry works before September 25 expiries |
| 2 | Privileged account-state checks and news-state preservation, R07/R05 | Disabled admin denied; unrelated/delayed articles cannot erase unresolved negative events |
| 3 | One timestamp-aware ML dataset/split contract, R01/R02/R03 | Every fitted label predates evaluation; actual horizon registry used; no in-sample metric marketed as OOS; valid incumbent preserved |
| 4 | Durable options serialization and valuation provenance, R06/R08 | Stale worker cannot double-commit; marks explain their timestamps and uncertainty |
| 5 | Model contributor metadata and portfolio path metrics, R09/R10 | Returned contributors match applied weights; every metric reconstructs from returned allocation |

Keep DA-02 disabled until its semantic contract and evaluation are repaired. Keep DA-12's named metrics; do not invalidate historical weights by silently changing their input. Neither new thresholds nor a higher nominal win rate should take priority over reliable validation/accounting.

After the first options positions resolve, reconcile entry cash, collateral, premium, mark history, expiry decision, settlement, and final equity per position. That is stronger evidence than checking a win-rate percentage. The current paper/sandbox stage is appropriate for these experiments; this review does not establish readiness for real automatic stock or option execution.

## 4. Verification and limits

### Existing tests independently rerun

Commands ran from each service directory with `PYTHONPATH=.:../../shared` and `python -m pytest -q -p no:cacheprovider`, bounded by a timeout.

| Scope | Result |
|---|---|
| Market-data full suite | 4,313 passed; 1 skipped |
| ML-prediction full suite | 229 passed |
| News-intelligence full suite | 107 passed |
| Strategy-engine full suite | 62 passed |
| Portfolio-optimizer full suite | 69 passed |
| Signal-engine full suite | 549 passed; 1 skipped |
| Gateway full suite | Timed out after 180 seconds; no final pass result |
| Gateway synchronous proxy/route subset | 33 passed; 3 async tests deselected |

**Total verified passing tests: 5,362.** This is six full service suites plus one gateway subset, not all repository suites. The gateway invocation again stopped progressing around its async tests in this environment. No production hang or failing assertion was established. A completed run in the claimed deployment/CI environment is needed to independently substantiate “every suite green.”

### Offline probes

Run:

```bash
python docs/audits/evidence/2026-09-24-followup-reproductions.py
```

Ten probe functions execute selected actual source statements/functions with controlled inputs and mocked infrastructure. The output separates counterexamples from confirmed deployed incidents. No network/database calls, trading, training, or file writes occur in the probe itself. It needs numpy, pandas, FastAPI, and python-jose. Its assertions intentionally record current behavior; replace/invert them into desired-behavior regression tests when repairing the paths.

Some new repository tests verify source strings or reproduce a formula independently. These can establish that a guard is present while missing whether the complete workflow respects its intended invariant. Add boundary tests at `model.fit`, persisted equity/settlement, account disablement, and concurrent database commit—not merely another assertion that an explanatory comment or condition exists.

Coverage here concentrates on the changed implementations and their adjacent consumers. It is not a fresh line-by-line audit of all services, a dependency vulnerability scan, a backtest optimization study, or a broker execution certification. Production verification was read-only; SQL used read-only transactions and eight-second statement timeouts. The recorded evidence contains aggregate counts and hashes, not credentials or individual account/trade records.

## 5. Addendum — review of Claude's remediation record and card rendering

**Reviewed after the follow-up pass:** [deep-audit remediation record](2026-09-24-deep-audit-remediation.md), at local revision `ff55512`. Since `ac16d0e`, the changes are that record, its `CLAUDE.md` index entry, and the improvements tracker. The backend and two card implementations reviewed above have not changed. No additional deployment or backend-suite rerun was needed to compare those sources. I did not edit Claude's record, index, application code, or improvements tracker.

### Claims that stand, and claims to correct

- **Record integrity confirmed:** all nine distinct cited commit IDs resolve locally, all twelve DA IDs appear, and both referenced audit/runner files exist. These checks establish traceability, not semantic closure.
- **DA-02 / DA-09:** the record correctly acknowledges disablement and incomplete news resolution. Keep those explicit dispositions. Their presence is inconsistent with interpreting the headline as twelve fully resolved subsystems.
- **DA-03:** the table still says GROWTH/28 while the API's imported registry says GROWTH/15. R02 remains applicable. Remeasure the actual post-filter/post-dedup dataset and split requirements before using “0 of 180” as a reason to accept leakage. Even a correct scarcity measurement would explain the operational constraint, not validate contaminated evaluation.
- **DA-12:** this is substantially **following**, not rejecting, the original recommendation. DA-12's original solution explicitly says to preserve the original historical feature, add a named collateral-yield field, and re-evaluate weights before changing their inputs. The compatibility decision is sound; the claimed disagreement with the audit is unnecessary. The current extra field is gross premium yield, so net costs/total strategy return remain separate work.
- **Test mutation work:** testing changed behavior rather than a dependency name, SQL parameter presence, or copied arithmetic is the right direction. The recorded sabotages do not establish coverage of every invariant: R02's downstream fallback, R04's failed-ingestion case, and R10's drawdown remain examples that require broader behavioral assertions. Prefer small integration tests against real SQL/Redis/auth dependencies where feasible, alongside fast isolated tests.
- **Suite counts:** six reported service counts match my independent completed runs. Event-intelligence was not rerun in this pass, and the reported 54 gateway tests were not independently reproduced because my full run timed out. This is a verification limit, not evidence that Claude's completed run never happened.

### U01 — Option expiry is displayed one day early in US timezones

**P2, newly browser-confirmed, pre-existing formatter.** [options-income.tsx](../../frontend/src/pages/options-income.tsx), `fmtDate` at line 27 and expiry display in `TopPicks` near 514; the same helper is used elsewhere on that page.

The actual component displays an input expiry of **`2026-09-25` as `Sep 24, 26`** in `America/Los_Angeles`. Both desktop and narrow Chrome captures reproduce it. `new Date(d)` interprets a date-only ISO string at midnight UTC; `toLocaleDateString()` converts that instant into the user's zone. Expiry is a calendar date, so this conversion changes its meaning. MDN documents the UTC interpretation of date-only input. [Date parsing behavior](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/parse).

**Impact:** the UI can tell a US user that an option expires a day before its stored contract expiry. This is a presentation error; the demonstration does not show a change to the backend's expiry or settlement date. It should be fixed promptly given the first September 25 positions.

**Solution:** use a shared date-only formatter that preserves the supplied year/month/day. Parse components into a calendar date, or format an explicitly UTC date with `timeZone: 'UTC'`; do not apply a user's zone to a session/expiry date. Keep timestamp formatting separate. Validate malformed input explicitly because an invalid `Date` usually does not throw at construction.

**Acceptance:** the same expiry renders September 25 in Los Angeles, New York, UTC, and Hong Kong, including DST boundaries. Test position rows and candidate cards, not only a helper.

### U02 — The new cash-basis hints are hard to read

**P2, visual accessibility concern.** [portfolio.tsx](../../frontend/src/pages/portfolio.tsx), metric labels and hint span near lines 350–351.

The small metric labels use `#475569`, and their important “Annualized · incl. cash” hints use even darker `#1e293b` on a dark card. In the captured fixture, their approximate contrast ratios are **2.38:1** and **1.23:1**, respectively, after compositing the card's background. The screenshots make the cash-basis explanation nearly disappear even though the numerical values are prominent. These ratios are fixture measurements, not a full-site accessibility certification.

**Solution:** choose readable text colors for metric names and basis hints, retaining clear hierarchy through spacing and weight rather than very low contrast. Test the actual composed background. WCAG's ordinary-text contrast target is at least 4.5:1. [W3C contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

**Acceptance:** automated contrast checks and visual review at desktop/narrow widths; the user can read which figures include cash. R10's drawdown mismatch still needs a backend correction—a clearer label alone does not fix that number.

### W01 — The suite runner reports success when no suites were requested

**P2, workflow guard edge case.** [run_suites.sh](../../scripts/run_suites.sh), argument loop and final success message.

I copied the unchanged runner into an isolated temporary directory and invoked it with **zero arguments**. It exited **0** and printed **`ALL SUITES GREEN`**, without running pytest. No repository caches were removed by this probe. With an explicit nonempty service list, the script correctly accumulates subprocess failures; this finding does not dispute that improvement.

**Solution:** reject zero arguments or select an explicit documented default suite list, validate service names, and report requested/completed suite counts. The claim that a shell runner “cannot be chained past” is too strong: callers can ignore any exit status. Require the relevant CI status checks in the deployment/merge policy and verify that enforcement separately.

The checked-in GitHub workflow currently invokes **`make test`**, whose own loop accumulates failures across a fixed service list. It does not invoke this helper. Therefore the empty-argument defect is **not evidence that the current CI workflow ran no tests**. Branch-protection/deployment enforcement was not inspected in this addendum.

**Acceptance:** zero-argument invocation either fails clearly or runs the documented complete default; a first-suite failure followed by a passing suite still returns nonzero; invalid names fail; CI/deployment cannot proceed when a required suite job fails.

### Browser evidence and its limits

I rendered the **actual extracted JSX and helpers** in local Chrome with synthetic input, using [this fixture builder](evidence/2026-09-24-ui-fixture.cjs). Browser timezone was explicitly `America/Los_Angeles`; Chrome DevTools emulation verified widths of 1,280 and 390 CSS pixels. The fixture calls no application API and contains no credentials or real positions.

- [Desktop screenshot](evidence/2026-09-24-cards-desktop.png)
- [Narrow screenshot](evidence/2026-09-24-cards-mobile.png)
- [Browser and record-check results](evidence/2026-09-24-remediation-comment-review-evidence.json)

The put card correctly shows **12.2% annualized on spot** and **13.5% on collateral** for the synthetic 30-DTE example. Equal-yield covered-call and legacy-response cases render without errors. The metrics show the supplied **7.6% return / 19.0% volatility** and the explicit cash hints. Both layouts had no horizontal overflow at the captured check. The fixture deliberately carries the unchanged −20% drawdown response from R10; displaying it correctly is not validating its calculation.

This closes the **isolated card rendering** check, not the authenticated production-page check. Full-page navigation, live API responses, global styles, interaction, deployment assets, and real broker/account behavior remain outside this browser fixture. The existing ten follow-up findings remain open until repaired and reverified; these comments alone do not change their status.
