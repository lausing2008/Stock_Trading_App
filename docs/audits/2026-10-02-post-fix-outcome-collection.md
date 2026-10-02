# Post-fix outcome collection: the boundary, and what it will and will not answer

Date: October 2, 2026, 14:03 UTC. Boundary commit `f9b6edb3` (SR-01…SR-08).
Read-only; this document records a line in the sand, not a result.

## Why a boundary is needed at all

Everything shipped in this cycle is evidence of **consistency** — tests prove the code behaves
as specified, probes prove the deployed code behaves as tested. None of it is evidence of
**usefulness**. Moving to that question requires comparing outcomes after the change against
outcomes before it, and that comparison is only honest if the dividing line is fixed *before*
the data accumulates. Picking the boundary afterwards is how a neutral result becomes a
favourable one.

These counts are therefore frozen here, at the deploy, with no projection attached.

## The baseline

Full detail in [the captured JSON](evidence/2026-10-02-post-fix-outcome-baseline.json);
[the probe](evidence/2026-10-02-post-fix-outcome-baseline.py) is SELECT-only.

Resolved `signal_outcomes` at the boundary:

| Direction / horizon | n | Mean return |
|---|---:|---:|
| BUY SHORT | 4,118 | −1.34% |
| BUY SWING | 3,453 | −2.57% |
| BUY LONG | 2,343 | −3.47% |
| BUY GROWTH | 3,978 | −2.68% |
| SELL SHORT | 1,420 | +0.62% |
| SELL SWING | 1,539 | +0.40% |

Options-flow alert outcomes: bearish n=1,123 (912 resolved at 10d, mean +2.39%); bullish
n=1,097 (846 resolved, mean +2.92%).

Also captured: 30 days of signal counts by market/horizon/signal, and per-portfolio paper
trade counts and mean P&L by stage.

## What a later read CAN answer

- Whether the alert families SR-03/SR-04 touched produce a different **count** of delivered
  alerts, and whether deferred/omitted work is now being retried rather than consumed. The
  new per-recipient accounting line makes that directly countable.
- Whether the HK SWING generator produces any BUY signal once the regime changes, and — from
  the newly recorded `fused_pre_compression` — whether any failure was suppression-bound
  rather than an absence of evidence.
- Whether the decision engine's conviction and freshness gates block a different population,
  via the `conviction_gate_no_information` tally now recorded in the paper scan.

## What it CANNOT answer, and should not be asked to

- **Whether the fixes made money.** Seven of the eight were correctness repairs to paths whose
  frequency is unknown; none was measured for impact, and a return difference after the
  boundary has many causes, most of them the market.
- **Whether the two policy constants were set well.** `_HOT_NEWS_DELAYED_COMPRESS` and
  `_MIN_VERTICAL_EDGE_PER_SHARE` need their own cohorts, which this baseline does not
  establish. Observing a return change after the boundary attributes nothing to either value.
- **Anything about the BUY-side inversion above.** It predates this work and is separately
  recorded; it is captured here only so a later read is not surprised by it.
- **A conclusion from a short window.** The horizons here run 5 to 28 days; a read before the
  slowest horizon has matured is measuring unresolved rows, not performance.

## The rule for reading it later

Compare the same grain: direction × horizon, resolved rows only, and the post-boundary period
on its own rather than pooled with everything before it. The session that produced this
already had to correct a pooled figure that hid a flat recent period — the same mistake is
available here and is the one to avoid.
