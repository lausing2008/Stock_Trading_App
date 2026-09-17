# Review of the 2026-09-16 / 2026-09-17 audit documents, and the fixes taken from them

**Reviewed:** 2026-09-17.
**Subjects:** [codebase and feature review](2026-09-16-codebase-and-feature-review.md) and
[system audit and trading roadmap](2026-09-17-system-audit-and-trading-roadmap.md).
**Method:** spot-check independently verifiable claims against the source and a live run,
rather than reading for plausibility. Follows the same discipline as
[2026-08-20](2026-08-20-external-audit-doc-review.md) and
[2026-08-22](2026-08-22-external-doc-reviews-and-actions.md), where raw data held up but
analysis conclusions had gone stale.

## Verdict: both hold up. The 09-17 audit is unusually rigorous.

Six independently checkable claims were verified. **All six were correct.**

| Claim | Severity | Verification |
|---|---|---|
| A01 — buying-power preflight fails open; `place_order` still runs | P0 | `except` logs a warning and falls through to submit |
| A02 — exit order ID never persisted; polling reconciles only entries | P0 | ID only reaches `log.info`; `poll_broker_order_fills` filters `stage == "open"` |
| A04 — options equity omits the short-option liability | P1 | `equity = cash + collateral`, no liability term anywhere |
| A05 — settlement can substitute an earlier close | P1 | 7-day backward window, no holiday-vs-missing distinction |
| A13 — CI hides an earlier backend failure | P1 | reproduced the shell semantics standalone: loop exits **0** |
| research-engine has 3 failing tests | — | exactly 3 failed, 76 passed |

The 09-17 document's own methodology is sound and self-aware: it ran each service suite
separately **specifically to avoid** the Makefile bug it had identified, disclosed the
research-engine failures instead of glossing them, and labelled three timeouts as "incomplete,
not proof of a service outage" rather than overclaiming. Its probe table matches what was
independently reproduced here, item for item.

## Corrections and additions

**The three "timeouts" are not real.** `api-gateway`, `decision-engine` and
`event-intelligence` were reported as timing out at 240s. Through the fixed `make test` all
three complete in **2-4 seconds** (54, 387, 451 tests). That was an artefact of the audit's own
three-at-a-time concurrent harness — which it correctly declined to diagnose rather than guess.

**A04 does not invalidate the options backtest.** The audit does not claim it does, but the two
are easy to conflate. Settlement P&L comes from `settle_position_economics()`, where the
obligation has resolved to zero; the defect was in the mark-to-market *curve*. The 417-trade
result stands; equity/drawdown readings from the old curve were overstated.

**The 09-16 document is stale in exactly one place, honestly.** It describes the options-income
engine as having no resolved track record — superseded by the T399 backtest. It scopes itself
to baseline `26a8159`, so this is correct-as-of-baseline rather than an error. The 09-17
document already picks up the change.

## Two fair criticisms of work done 2026-09-16/17

Both land, and are recorded rather than defended:

- **A08** — the weight study does not reproduce live capital and selection constraints. It ran
  with `max_per_date=10_000` and `all_contracts=True`, deliberately, to de-bias the candidate
  pool — but that also means the weights were fitted over trades the portfolio could never have
  afforded simultaneously.
- **A09** — the chronological split does not **purge** overlapping outcomes. A trade entered two
  days before the split with a 30-day expiry settles deep inside the test window, so train and
  test outcomes overlap in time. This is the standard purge/embargo requirement and it was missed.

Neither reverses the direction of the finding (cushion >> yield is large and consistent across
the top 8 configurations), but together they mean **+0.83pp out-of-sample is softer evidence
than it was first presented as**, and not yet promotion-grade.

## Fixed in this pass (T400)

Chosen as the bounded, self-contained subset — two were defects introduced on 2026-09-16/17.

| Finding | Where it now lives |
|---|---|
| A13 — CI failure masking, plus the 3 stale tests it hid | [ci-failure-masking.md](../incidents/ci-failure-masking.md) |
| A04 — short-option liability | [options-income-engine.md](../features/options-income-engine.md) |
| A05 — settlement session substitution | [options-income-engine.md](../features/options-income-engine.md) |

## Deliberately NOT fixed, and why

**A01-A03 (P0, broker order lifecycle).** Correctly rated the most severe findings in the
document, and correctly *conditional*: they bite only when a live broker is linked. They are
also not a bounded patch — the audit's own remedy is explicit execution modes, a durable order
state machine, and fill-driven reconciliation. That is a design change requiring its own review,
not something to slip in alongside accounting fixes.

**A06 (archived bids treated as current fills).** Real, already documented as the staleness
limitation, and fixing it properly means separating research candidates from executable orders —
again a design change, not a patch.

**A08/A09 (weight-study methodology).** Re-deriving the weights with purged splits and live
capital constraints is worth doing, but it changes live trading behaviour and should be an
explicit decision with fresh evidence, not a quiet correction.

## The transferable lesson

The highest-leverage finding in a 559-line audit was a **one-line shell bug**. It cost nothing
to fix and had silently invalidated every test result the project relied on for three months.
Before trusting a measurement, check that the thing reporting it is capable of reporting
failure — and prefer verifying an audit's claims over accepting them, in both directions: five
of six findings were confirmed, and one reported symptom (the timeouts) turned out to be an
artefact of how the audit measured.
