# Check-back calendar — remaining work and data-gated items

**Drafted 2026-09-30.** Requested as "create a calendar to check back for me … for the work we
left and also those need data to verify."

> **STATUS: CREATED 2026-10-01 02:54 UTC.** All 8 events exist in `stockai2028@gmail.com`
> (calendar `primary`). **Do not replay the JSON beside this file — it would duplicate them.**
>
> The first attempt failed with `invalid_grant`: the stored Google OAuth grant was dead even
> though the MCP server itself was connected and healthy. Re-auth needed a specific sequence,
> because the server refuses to re-add over an existing nickname *and* refuses to remove the
> last account — add a NEW nickname to force a fresh consent flow.

Dates are America/Los_Angeles. Every item traces to the M01–M25 register in
`docs/features/2026-09-30-measurement-framework-and-improvement-backlog.md` and the status map in
`docs/audits/2026-10-01-session-status-completed-and-remaining.md`.

## Why these dates and not others

The schedule is built on **what each item is actually waiting for**, which is not the same thing
for any two of them:

| Gate type | Items | What sets the date |
|---|---|---|
| A decision only you can make | M02, M21 | M02 **expires ~Oct 6** — after that the decision is moot |
| One specific scheduled run | M19 (MU-01) | First instrumented sync, morning of Oct 1 |
| An event that has to happen | M16, M19 (MU-02 live) | Earnings season; cannot be forced |
| Accumulated outcomes | M06–M08, M10, M11, M15 | Monthly re-count; defer again if the count hasn't moved |
| Someone doing the work | M13, M14, M20, M24, M25 | Not data-gated at all |
| The market doing something | M12 | One bear sample. Quarterly, and may stay unanswerable |

The monthly and quarterly checks are deliberately **re-count-then-defer**, not deadlines. That is
the discipline the `project_pending_data_verification` memory exists to enforce: if the count has
not moved, the correct output is another deferral, not a conclusion drawn from thin data.

## The events

| When | Item | Gate |
|---|---|---|
| Oct 1, 08:00 | MU-01 — read the first instrumented earnings sync | Scheduled run |
| Oct 3, 09:00 | **DECISION**: recovery markers, portfolios 2 and 5 | Expires ~Oct 6 |
| Oct 3, 09:30 | **DECISION**: signal-outage recovery digest | Your approval |
| Oct 8, 09:00 | Engineering backlog — M13, M14, M20, M24, M25 | None |
| Oct 15, 09:00 | MU-02 live verification + broker fill re-poll | Occurrence |
| Nov 2, 09:00 | Monthly data check — the six collecting items | Outcome count |
| Dec 1, 09:00 | GEX control arm — review trigger | ~30 control events |
| Dec 31, 09:00 | Regime robustness + dark-pool baseline replay | Market / work |

Full briefing text for each lives in the JSON's `description` fields, so the detail travels into
the calendar entry itself rather than requiring this file to be open.

## Three cautions carried into the event text deliberately

These are in the descriptions because they are the specific ways each check could be read wrong:

1. **MU-01** — `eps_actual=None` in a `history_map` line is *not* proof of a mapping defect. If
   the provider returned no actual, that line is correct output. The decisive evidence is the
   four-step chain, and if the logs cannot connect it, the honest result is an **evidence gap**,
   not an inferred cause.
2. **M09 (GEX)** — ~30 control events is a **review trigger, not a promotion threshold**. Even
   worse outcomes among corroborated alerts would not establish that *using* the gate hurts
   portfolio returns; that needs a baseline-vs-gated comparison including avoided trades.
3. **M05 (dark pool)** — the relative bar rejects 15 of 9,707 prints because it sits below the
   absolute floor for 55 of 57 symbols. A bar that never binds has not been shown not to work.
   It was never given a value at which it could act.

## Already on the calendar — not duplicated here

Per `project_deferred_checks_calendar`, two checkpoints already exist: **2026-10-06** (did the
fixes take effect) and **2026-12-04** (did they work). Those carry the `|t_day| < 2` rule —
inside two days of a change, a result is **NOT YET MEASURABLE**, which is not the same as failure.
The events above are additions, not replacements.

## Status of the passed items, checked 2026-10-13

Read-only. Weekdays for the remaining dates (computed, not recalled): Thu Oct 15, Wed Oct 21
(price-risk review, added 2026-10-13), Mon Nov 2, Tue Dec 1, Thu Dec 31.

| Date | Item | Status |
| --- | --- | --- |
| Oct 1 | MU-01 | Not re-checked here |
| Sat Oct 3 | **M02** recovery markers, portfolios 2 and 5 | **No decision was recorded, and none was taken** — the last note (2026-10-02) says deletion "remains unauthorised". **Moot by expiry, as the brief predicted:** neither original marker exists. The markers present today are NEW, written by the fixed code when a trade actually opened: portfolio 5 `consec_loss_recovery = 10` (AMD entered 2026-10-13 19:36 UTC), portfolio 7 `= 4` (SPCX, 14:10 UTC). Portfolio 2 has none. Note: the register advised holding portfolio 5 pending a recovery-strategy review; it has now re-entered through the recovery branch, legitimately, under the fixed logic. |
| Sat Oct 3 | **M21** signal-outage recovery digest | **No approval or send recorded.** Register still "approval pending". The 18 consumed-and-current SELLs date from 09-25/09-28 — now over two weeks old, so the digest's value has decayed; re-run the manifest's refresh before any send, or close it. |
| Thu Oct 8 | Engineering backlog M13, M14, M20, M24, M25 | **The review itself is not recorded.** Work on every item happened 2026-10-01/02 (base-training ledger, production model inventory, outbox — partial, capability readiness, broker lifecycle verification), but `shared/metrics/register.py` has not changed since and still lists them as open. Register status is stale, not verified. |

## Oct 8 engineering review — reconciled 2026-10-13

Each register entry (`shared/metrics/register.py`, last changed 2026-10-02) checked against its
own trigger and against production, read-only. **The register text was not edited:** it lives
in `shared/`, so changing it drifts all 12 backend images until the next full rebuild
(AUD-REGISTER-POSTBUILD-DRIFT). The corrections below should land in the register with the next
`shared/` rebuild, together with commit `6d1e5d2a` (HK 2027 calendar).

| Item | Register says (as of 10-02) | Evidence 2026-10-13 | Reconciled status |
| --- | --- | --- | --- |
| **M13** ML attrition | Base ledger built 10-02; "NEITHER has yet run inside a real fit" | The ledger is wired into the trainer (`_base_ledger_boundary`, emits `train.base_ledger` per fit). Retrain jobs ran since (e.g. `position_scaling_gate_retrain` 10-11), but **the ml-prediction logs were reset by tonight's container recreation** (started 23:58 UTC), so no ledger line can be read today. | **Unverified — evidence gap, not a failure.** Read the next fit's `train.base_ledger` line. Purge/calibration/promotion ledgers are still unbuilt. |
| **M14** OOS-suppression | Inventory run in prod 10-01/10-02; decision pending on absent `cv_auc_mean` | Nothing since. | **Unchanged — awaiting a decision**, not engineering. |
| **M20** Outbox | "No job calls them and the flag is off" | **Out of date on wiring:** jobs now call the outbox for product impact (10-12), option opportunities and price risk (10-13); `earnings_outbox_drain` is registered. **But the production outbox has never held a row** (0 rows, every type), and `earnings_outbox_rollout` is still absent (off). | **Trigger NOT met** ("one alert family cut over with the flag moved off `off`"). Wiring is partially done; cutover, other families and provider callbacks remain. |
| **M24** Capability readiness | "exposed on /health; NOT yet run in prod" | **Out of date:** market-data `/health` returns the matrix in production now — `all_ready: false`, one degraded capability: `option_prospective_capture` (no live sized quote, no account permission/buying-power facts, no job calls the gate). Entry-blocked: that one; exit/reconciliation-blocked: none. | **Trigger MET** (run against production). Remaining: read the full matrix, not just the degraded list. |
| **M25** Broker lifecycle | Behind a disabled flag; A01-A03 + options marks open | `broker_submit_after_commit` flag absent (off). The 10-02 review's two control holes (NaN price, future-dated intent) were fixed the same day (`cd967916`). **`submit_pending` / `reconcile_submission` / `needs_reconciliation` still have no production caller.** | **Unchanged — the 10-02 blocker stands:** enabling the flag would stop broker entries. Keep disabled. |

So of the five, **one trigger is met (M24), one is blocked on evidence that was erased tonight
(M13), and three are unchanged** (M14 awaits a decision, M20 awaits a cutover, M25 awaits a
dispatcher).

## Decisions and corrections, 2026-10-13 (operator)

- **M21 recovery digest — CLOSED WITHOUT SENDING.** The 09-30 manifest is stale (signals from
  09-25/09-28) and is not approved for any send. A fresh assessment, if wanted, starts from
  current signals, not this manifest.
- **M02 old recovery markers — closed by expiry.** **Portfolio 5's recovery-strategy review
  remains OPEN.** Its 2026-10-13 re-entry (AMD) only shows the current rules permit it; it does
  not answer whether the recovery branch should (its seven earlier recovery trades all lost,
  −$452.92).
- **M24 — trigger met ≠ passed.** The matrix runs in production; `option_prospective_capture` is
  **degraded and needs investigation**. Starting point: the AQ11 analysis in
  [2026-10-12-calibration-basis-and-capture-readiness.md](2026-10-12-calibration-basis-and-capture-readiness.md)
  (quote size, account permission and buying-power facts have no source, and no job calls the gate).
- **Oct 8 engineering review — COMPLETED**, with the unresolved outcomes recorded in the table above.
- **HK 2027 — corrected against the government list (`6d1e5d2a`).** Exchange-specific
  verification (HKEX half-day sessions, any market-only difference) **remains open** until HKEX
  issues its 2027 circular.
- **Deployment of `6d1e5d2a`: next coordinated deployment, complete before 2027-01-01.** Not an
  immediate rebuild. **Scope follows the consumers, not the directory:** `common.market_calendar`
  is imported by **market-data, decision-engine and research-engine** only; nothing imports
  `metrics.register` at runtime, so register-only edits need no image rebuild. Caveat for whoever
  deploys: `check_deploy_drift.sh` compares `/app/shared` as one unit, so it will report the
  other nine services as drifted on `shared/` even though none of them runs the changed code.
  That reading is expected until a full rebuild, and is not by itself a reason to rebuild nine
  images. *(Correcting my own earlier statement that the change "needs all 12".)*
- **Pushing ≠ deploying** in this repository: nothing deploys on push. Deploys are run by
  `scripts/deploy.sh`, which pulls `prod` on EC2 — so a pushed `shared/` change ships with the
  next deployment of any of the three consuming services.
