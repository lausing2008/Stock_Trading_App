# Scoping: A01-A03 — broker order lifecycle

**Written 2026-09-18.** Deliberately a scoping document, not an implementation, per
[2026-09-17's audit review](2026-09-17-audit-review-and-t400-fixes.md), which rated A01-A03 the
most severe open findings but correctly declined to patch them same-day: they are conditional
(only bite when a live broker is linked) but not a bounded patch — the audit's own remedy is
explicit execution modes, a durable order state machine, and fill-driven reconciliation. This
records what re-checking against current code (not the ~2-day-old audit text) actually found,
including two things the original audit didn't cover.

## The three original findings, re-verified against current code

All three are still true. The code has evolved since the audit (buying-power preflight,
`_handle_broker_error_if_token_rejected`, `trade.broker_fill_confirmed` all postdate it) but none
of that touches the actual defects.

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
   (after-hours, partial fill, a slow sandbox), the exit order is now **orphaned**: real, live at
   the broker, and permanently unreconcilable from this app's side. The trade is marked `closed`
   in our DB using whatever price the simulated/immediate-fill path produced, with no path back
   to the order that will actually determine the real fill.

**A03 — client-order-IDs are not durable, so a retry after a crash can double-submit.**
`interface.py`'s `place_order()` has no `client_order_id` parameter at all. `etrade_broker.py`
generates `"clientOrderId": str(uuid.uuid4())[:20]` fresh on every call — a new ID every time,
which defeats the entire purpose of a client-order-ID (idempotent retry: same ID in ⇒ broker
recognizes a duplicate and no-ops). `alpaca_broker.py` has no client-order-id handling
whatsoever. Concretely: if this app crashes or the DB write fails *after* the broker accepts an
order but *before* `trade.broker_order_id` is persisted, the only recovery path (re-run the
entry) generates a fresh UUID and places a second, real, duplicate order — nothing on either
side can recognize the first one already went through.

## Two findings the original A01-A03 text didn't cover

Found while re-verifying the above against current code, not part of the 2026-09-17 audit.

**A02 has a sibling: a second, hand-maintained reimplementation of the same broker-exit
sequencing risk.** `conditional_orders.py::_execute_close_position()`
([conditional_orders.py:427+](../../services/market-data/src/services/conditional_orders.py#L427))
is not a call into `_monitor_positions()`'s close flow — its own docstring calls it a "faithful,
minimal reimplementation." It duplicates the stop-slippage/commission/cash-credit math *and* the
broker-exit routing, which means A02's exact defect (order ID logged, never persisted) has to be
independently not-fixed in two places, and any future fix to one has no structural way to force
the other to be updated. This is the same "parallel implementation drift" anti-pattern flagged
elsewhere in this project's audits, now confirmed inside the live broker-order path.

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

## Why none of this is a same-day patch

All three original findings, plus the two new ones, share one root cause: there is no single
place that owns "what does it mean to close a broker-backed position," so every caller that needs
to close one (`_monitor_positions()`, `conditional_orders.py`, `manual_exit_trade`,
`liquidate_portfolio`) either reimplements the sequencing itself or explicitly opts out of the
broker leg. A real fix needs, in order:

1. **A durable order-state model** — at minimum a `broker_exit_order_id` column and a `status`
   field (`pending`/`filled`/`failed`/`orphaned`) for both entry and exit legs, so an in-flight
   order can be found and reconciled after a restart, not just while its own function call is
   still on the stack.
2. **One close-flow function that owns the broker leg**, called by all four sites above — not a
   shared *math* helper (which is what `_close_one_paper_trade()` already is) but a shared
   *sequencing* helper that places the broker order, persists its ID before returning, and lets
   every caller (automatic monitor, conditional order, manual exit, liquidation) opt into the
   same reconciliation path instead of each deciding independently whether to include one.
3. **An exit-fill poller**, symmetric to `poll_broker_order_fills()`, once there's a column for it
   to poll.
4. **Idempotent client-order-IDs** — generated once per logical order attempt and persisted
   *before* the broker call, so a crash-and-retry reuses the same ID instead of minting a new one.
5. **An explicit execution-mode concept** (RESEARCH / SIMULATED / BROKER_PAPER / LIVE) on
   `PaperPortfolio`, so "does this portfolio's close path touch a real broker at all" is a
   declared property checked once, rather than something each of the four call sites currently
   answers for itself, inconsistently (three of four skip it by omission, one places a real
   order and half-persists the result).

Also unresolved by this scope, deliberately: A03's fix (real client-order-IDs) requires each
broker adapter to accept and forward one, which touches `interface.py` and both concrete adapters
— not risky individually, but only worth doing once the state model in (1) exists to make the ID
durable end to end; doing it first would add a correctly-generated ID that still isn't recoverable
after a crash.

## Estimate

Item 1 (schema) is a small, additive migration — similar shape to T410's `SignalOutcomeHorizon`
table. Item 2 (the shared sequencing helper plus rewiring all four call sites) is the actual work
and the place a mistake would be expensive, since it sits on the live-money path when a broker is
linked. Items 3-5 are each small once 1-2 exist. Not a patch; also not as large as C02/C03's
201-reference audit — the call-site count here is four, all already read in full.
