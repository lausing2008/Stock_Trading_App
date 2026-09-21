# Scoping: A01-A03 — broker order lifecycle

**Written 2026-09-18.** Deliberately a scoping document, not an implementation, per
[2026-09-17's audit review](2026-09-17-audit-review-and-t400-fixes.md), which rated A01-A03 the
most severe open findings but correctly declined to patch them same-day: they are conditional
(broker-backed execution, with real-money impact when a live account is used) but not a bounded patch — the audit's own remedy is
explicit execution modes, a durable order state machine, and fill-driven reconciliation. This
records what re-checking against current code (not the ~2-day-old audit text) actually found,
including two things the original audit didn't cover.

**Review update, September 18:** the [independent review and expanded implementation scope](2026-09-18-uw-and-broker-report-review.md)
confirms the core defects, corrects the original call-site count and recovery/idempotency wording,
and adds scale-in/out, partial-fill, quantity, and account-reconciliation requirements. Local and
production checkout were `7fb03da` at review; the three relevant running modules matched local.
Production database evidence showed one sandbox-linked portfolio and no paper trades with
broker order IDs. This establishes a latent implementation risk, not observed live-money damage.

## The three original findings, re-verified against current code

All three core defects are still present. Buying-power preflight, token-rejection handling,
and `trade.broker_fill_confirmed` do not resolve them. Their exact introduction chronology
should be established from commits rather than inferred from current source comments.

**A01 — entry fails open toward the real broker on a buying-power-check error.**
`_place_broker_entry()` ([paper_trading_engine.py:161-243](../../services/market-data/src/services/paper_trading_engine.py#L161-L243))
fetches the real account's buying power as a pre-flight and skips the real order if the
simulated position size would exceed it — but a generic exception *fetching the account itself*
(a transient network blip, same as any real outage) falls through to placing the order anyway,
by explicit design ("the broker's own margin rejection is the backstop"). That backstop is the
broker's own risk control, not this app's — acceptable only if everyone downstream agrees a
transient error should fail toward placing a real order rather than skipping it.

**A02 — the exit order ID is captured, logged, and then discarded.**
`_place_broker_exit()` ([paper_trading_engine.py:243-308](../../services/market-data/src/services/paper_trading_engine.py#L243-L308))
holds the real order's `order.order_id` in a local variable long enough to log it twice
(`broker.exit_order_placed`, and inside the immediate-fill-check block) and never assigns it to
any field on `trade`. No `broker_exit_order_id` column exists. Two concrete consequences:

1. `poll_broker_order_fills()` ([paper_trading_engine.py:308-360+](../../services/market-data/src/services/paper_trading_engine.py#L308-L360))
   filters on `broker_order_id.isnot(None), broker_fill_confirmed.is_(False)` — this is the
   *entry*-fill poller. There is no equivalent poller for exits, because there is no column to
   poll.
2. If the immediate-fill check inside `_place_broker_exit()` doesn't resolve the fill right away
   (after-hours, partial fill, or a slow response), the exit order lacks a durable link to the
   paper trade and cannot be reconciled by the existing entry-only poller. Broker order lists,
   account history, and logs may support recovery; it is not inherently unrecoverable. The trade is marked `closed`
   in our DB using whatever price the simulated/immediate-fill path produced, with no path back
   to the order that will actually determine the real fill.

**A03 — client-order-IDs are not durable, so a retry after a crash can double-submit.**
`interface.py`'s `place_order()` has no `client_order_id` parameter at all. `etrade_broker.py`
generates `"clientOrderId": str(uuid.uuid4())[:20]` fresh on every call — a new ID every time,
which prevents using a durable app-generated identity for recovery. `alpaca_broker.py` does not
accept or persist such an identity either. Broker duplicate-ID behavior is provider-specific;
it must not be assumed to be a successful no-op. If the app crashes or persistence fails after
broker acceptance, blindly rerunning submission with a new identity can create a duplicate.
The required recovery is lookup/reconciliation of the original intent before any resubmission;
the current portfolio execution path lacks that durable linkage.

## Two findings the original A01-A03 text didn't cover

Found while re-verifying the above against current code, not part of the 2026-09-17 audit.

**A02 has a sibling: a second, hand-maintained reimplementation of the same broker-exit
sequencing risk.** `conditional_orders.py::_execute_close_position()`
([conditional_orders.py:427+](../../services/market-data/src/services/conditional_orders.py#L427))
is not a call into `_monitor_positions()`'s close flow — its own docstring calls it a "faithful,
minimal reimplementation." It duplicates simulated close bookkeeping and then calls the same
`_place_broker_exit()` helper used by the monitor. Thus order submission is already shared;
fixing ID handling inside that helper would affect both callers. The remaining duplication is
the surrounding close/cash sequencing, which marks the simulated position closed before broker
fill confirmation. That needs one coordinated, fill-driven lifecycle.

**A separate, arguably worse gap: manual exit and portfolio liquidation never touch the broker at
all.** `paper_portfolio.py`'s `manual_exit_trade()` and `liquidate_portfolio()`
([paper_portfolio.py:467-553](../../services/market-data/src/api/paper_portfolio.py#L467-L553))
both route through a third close-flow implementation, `_close_one_paper_trade()` — whose own
docstring is explicit that it deliberately omits broker-exit routing, "matching
`manual_exit_trade()`'s own pre-existing, narrower behavior exactly." Concretely: if a
broker-linked position exists (`trade.broker_order_id` is set) and a user clicks "manual exit" in
the UI, or liquidates the whole portfolio, the paper trade is marked `closed` and cash is
credited in the simulated ledger — **the real broker position is never touched.** Unlike A02
(an in-flight order that becomes unreconcilable), this is a real, live position that the UI now
reports as closed while it silently remains open at the broker, with no error, no warning, and no
record that anything was skipped. This is user-triggered rather than automatic, which if anything
makes it more likely to be acted on with confidence.

## Durable repair and immediate containment

The findings share a lack of one durable owner for broker-backed position changes. The four
full-close callers below are not the complete scope: the independent review also identifies
automatic partial exits, conditional partial sells, and scale-ins. The complete fix is larger
than a column patch, but bounded containment is still useful: fail closed on new-entry preflight
failure, make execution mode explicit, and never report broker closure from a simulated ledger
change. Existing exposure requires a verified broker exit/reconciliation path, not simply
disabled exit processing. The durable design needs:

1. **Durable order intents, attempts, and fills**, supporting multiple orders per position,
   partial fills, rejected/cancelled/expired orders, replacements, and submission-unknown states.
   A single exit-ID column can assist a transition but is not the complete model.
2. **One position-change service**, used by full and partial exits and scale-ins. Commit the
   intent and client identity before the network call; recording a returned order ID afterward
   does not close the crash window. Derive broker-backed quantity/cash from fills and keep
   `exit_pending` separate from `closed`. `_close_one_paper_trade()` is currently a state and
   ledger mutation helper, not merely a math helper.
3. **Reconciliation for every nonterminal order and account position**, independent of whether
   the old paper trade is labelled open. Recover after restart and handle external/manual orders.
4. **Broker-aware client-order identity and ambiguous-submit recovery**. Preserve an identity
   for the same intent; query/reconcile after timeout before deciding whether resubmission is safe.
   Test duplicate-ID behavior for each provider rather than assuming a successful no-op.
5. **An explicit execution-mode concept** (RESEARCH / SIMULATED / BROKER_PAPER / LIVE) on
   `PaperPortfolio`, so "does this portfolio's close path touch a real broker at all" is a
   declared property checked once, rather than something each of the four call sites currently
   answers for itself. Of the four named full-close callers, two route to `_place_broker_exit()`
   (automatic monitor and conditional full close); two omit it (manual exit and liquidation).
   Enforce mode and account/environment consistency centrally, not through optional caller choice.

The adapter contract must accept the durable client identity and expose the lookup/recovery
capabilities each broker actually supports. A new parameter alone is insufficient, but the
interface and state model can be designed together. Options legs/open-close intent and complex
orders require additional capability work; the current E*Trade submit path is explicitly equity-only.

## Estimate

An additive schema is only part of the work. Account reservation/concurrency, ambiguous network
responses, partial quantities, restarts, migration of existing linked portfolios, and broker
contract tests make the remaining effort uncertain. The original four-call-site estimate was
incomplete. Estimate and implement the milestones and fault-injection acceptance tests in the
linked review before assigning a delivery date or treating this as ready for live capital.
