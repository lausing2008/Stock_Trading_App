# Deep Audit Series (2026-09-07): Paper Trading — 5 of 6

**Domain:** position lifecycle after entry — exits, stops, trailing/breakeven, sizing, cash
accounting, broker sync.

**Result: 3 confirmed findings, one CRITICAL.** Notably, the two things that *looked* most
broken from the data (stop execution, and a portfolio holding more cash than its initial
capital) both audited **clean** — see the false-alarm section.

---

## Finding 1 — CRITICAL — Positions force-closed within minutes on a message that is factually false

**File:** `paper_trading_engine.py:3060-3078`

```python
last_non_wait_ts = session.execute(
    select(func.max(Signal.ts)) ...
    .where(..., Signal.signal != "WAIT",
           Signal.ts >= trade.entry_time)      # ← excludes the entry-triggering signal
).scalar()

still_waiting = (
    last_non_wait_ts is None or                # ← fail-OPEN on missing data
    last_non_wait_ts < now.replace(tzinfo=None) - timedelta(days=wait_days)
)
```

Two defects compound. Signals are written by the evening batch job (~20:30 UTC) and entries
execute on a later scan, so the BUY that *caused* the entry has `ts < entry_time` and is
excluded by the filter. When the query then returns `None` — which it does for every position
from entry until the next batch run produces a non-WAIT reading — `still_waiting` is `True` via
the first disjunct, and the position is closed as `momentum_exit`.

**No decay has occurred. There is simply no data yet.** This is the codebase's documented
"missing-data fail-open where fail-closed was intended" class, and the comment at `:3059` states
the opposite intent: *"true consecutive decay."* There is no minimum-hold guard.

### Verified by me against production — all 4 `momentum_exit` trades ever recorded

| symbol | held | return | message |
|---|---|---|---|
| BRK-A | 9,138 min (5d) | −1.199% | "No non-WAIT signal in 3 days" |
| NATL | **5.5 min** | −0.200% | "No non-WAIT signal in 3 days" |
| BRK-A | **5.1 min** | −0.248% | "No non-WAIT signal in 3 days" |
| BRK-A | **25.3 min** | −0.257% | "No non-WAIT signal in 3 days" |

Three positions were liquidated **within half an hour of opening**, each booking a guaranteed
small loss (entry + exit slippage) with essentially no market move. The message claiming "no
signal in 3 days" is false in every case.

Confirmed the subquery returns NULL at exit time for exactly those three. Traced the 08-31
BRK-A case end-to-end: entry `14:36:45`, entry-triggering BUY `2026-08-30 21:03:51 SWING
conf 44.63` — excluded because `21:03 (Aug 30) < 14:36 (Aug 31)`. The next non-WAIT SWING signal
was `2026-09-06`, a week later.

**Not a data-retention issue:** `signals` holds 45,282 rows back to 2026-05-25 and BRK-A's SWING
history is intact. The `>= trade.entry_time` predicate alone causes it.

**28 of 115 signal-linked trades have `entry_time > entry_signal.ts`** and are exposed to this path.

**CORRECTION to the subagent's evidence:** it presented a table implying the entry signal is
excluded in all four cases, but a naive `JOIN signals ON s.id = trade.signal_id` shows
`visible_to_query = true` for all of them — because `signal_id` is updated to a *later* signal by
dedup-on-change. The conclusion is right; that particular table does not demonstrate it. The
correct demonstration is the NULL subquery result at exit time, which I ran separately.

**This also answers Q5:** `momentum_exit`'s 2.0-day average hold and 0% win rate is not early
firing or a stop-loss substitute — it is firing on absent data.

---

## Finding 2 — MEDIUM — `earnings_size_mult` applied twice

**File:** `:4495` and `:4553`

```python
4495:  risk_dollar = _risk_base * earnings_size_mult * regime_size_mult * ...
4553:  max_pos     = equity * cfg["max_position_pct"] * earnings_size_mult
```

The multiplier (0.50 within 10 days of earnings, 0.75 within 11–20) is folded into `risk_dollar`
— which sizes `shares` at `:4514` — and then applied a *second* time to the independent position
ceiling. The other five multipliers appear exactly once; only this one is duplicated.

So when the cap binds, the effective earnings de-risking is **0.25× / 0.5625×**, not the
documented 0.50× / 0.75×. Direction is conservative (never over-sizes), which is why it survived
unreviewed — but it is a real divergence between two supposedly independent risk controls, and
the emitted note reports the cap as if it were the configured one.

---

## Finding 3 — MEDIUM — Stop/target anchored to pre-slippage price while cost basis is the slipped fill

**File:** `:4404`, `:4514`, `:4620`, `:4631`, `:4637-4639`

The trade persists two price anchors: cost basis is `slipped_entry`, but `stop_loss` /
`take_profit` are computed from the unslipped `live_price` and never re-derived. Consequences:

- True risk per share at the actual fill is `slipped_entry - stop`, exceeding the
  `stop_distance` used for sizing — so `max_loss_per_trade_pct` is enforced against an
  understated figure and real dollar risk overshoots the cap.
- `rr_ratio_at_entry`, stored and read downstream by the tuner and reporting, is systematically
  optimistic.

**Measured across every portfolio — overstated in all five:**

| portfolio | n | stored R:R | true R:R at fill |
|---|---|---|---|
| GROWTH | 42 | 3.49 | 3.33 |
| HK GROWTH | 15 | 3.48 | 3.21 |
| ETrade Sandbox | 15 | 2.71 | 2.54 |
| US SWING | 40 | 2.37 | 2.28 |
| HK SWING | 4 | 2.18 | 2.13 |

A one-directional bias of 0.05–0.27 R. The entry log at `:4664` reports `price=live_price`, not
`slipped_entry`, so the discrepancy is invisible in logs too.

---

## Two things that looked broken from the data and are NOT

Recorded because both are exactly the kind of false positive a future audit would re-derive.

**Stops execute correctly.** Raw data shows stops placed 8.28% away but realized stop losses
averaging −2.85% — a 3× gap that reads like stops not firing where placed. They do: exits
average **−0.81% vs the CURRENT stop**, and trailing/breakeven had correctly moved it on **29 of
59** stop_hit trades. The naive comparison is against the *original* `stop_loss`.

**`breakeven_stop`'s −0.30% average is structural, not a defect.** `current_stop` equals
`entry_price` to the last decimal on all 33 trades (verified: MU 976.5356/976.5356, NU
15.3104/15.3104, TMDX 92.1823/92.1823). The branch fires on `live_price <= stop` where
`stop == entry`, so the fill is at-or-below entry by construction, then exit slippage subtracts
~0.1%. Both outliers (MU −5.18%, FCEL −2.36%) predate the `AUD262-BREAKEVEN-COOLDOWN` fix;
post-fix nothing exceeds −1.27%. A "breakeven" stop can never realize exactly 0%.

**HK GROWTH's 302,315 > 300,000 initial with 0 open positions is correct** — `sum(pnl)` over its
closed trades is **+2,314.68**, and `300,000 + 2,314.68 = 302,314.68` vs actual `302,314.61`
(7-cent rounding across 15 trades). It is simply a profitable portfolio.

---

## Answers to the domain's questions

**Q2 — trailing_stop does real work.** `:3325-3331` only ratchets (`if floored_trail >
current_stop`, floored at the original hard stop), so it can neither loosen nor ratchet down
into a larger loss. Its 100% win rate is definitional: `AUD262-EXITREASON-CONFLATION-ROOT`
requires both `stop > entry` AND `live_price >= entry`, so a losing exit is relabelled
`stop_hit`. Genuinely converts open gains to realized (+4.63% avg, 14.2d).

**Q3 — the R:R gap is target-unreachability plus scale-out truncation, not stop behaviour.**
GROWTH targets sit **+34.4% away** against an 11.4% stop; **0 of 42** GROWTH trades ever reached
target. SWING targets sit +11.8% away and are reached (7 of 40, avg +12.22%, matching
`default_tp_pct = 1.12`). Separately, the two-level scale-out at `:3243`/`:3272` sells 33% at +7%
and 50%-of-remaining at +12%, so **~67% of a position is liquidated before the SWING target and
long before the GROWTH one** — upside capped by design while the full position carries downside
to the stop. Finding 3 adds a further ~0.1–0.3 R of overstatement.

**Q4 — cash accounting is correct.** Ledgers reconstructed from first principles reconcile
exactly for HK SWING and ETrade Sandbox (0.00 delta) and to 5 cents for HK GROWTH. The two
larger residuals (GROWTH +1,786, US SWING +1,573) are artifacts of the reconstruction: the
scale-IN path at `:5536-5580` blends `entry_price` as a share-weighted average, so current
`entry_price × entry_shares` no longer equals the historical debits. Entry (`:4622`) and exit
(`:3153`) each mutate cash exactly once.

**Q6 — broker sync is clean.** `poll_broker_order_fills` filters on
`broker_fill_confirmed.is_(False)` and sets the flag on **both** the delta and no-delta branches,
so the prior re-polling bug is closed in both directions. One acknowledged gap already documented
in-code at `:368-372`: a terminal `cancelled`/`rejected` status never sets the flag, so such a
trade is re-polled indefinitely — no double-fill or cash corruption, but an unbounded poll loop.

---

## CHECKED AND FOUND CLEAN

Exit cash credit (`:3153`); P&L blending across scale-outs (`:3131-3137`); `signal_outcomes`
writeback correctly storing a FRACTION while `paper_trades.pct_return` is ×100 (the documented
two-scale convention, not a unit bug); stale-price gating (`:2850-2934`); all six trailing-stop
tighteners (all floored at `stop_loss`, monotonic-raise-only); earnings-proximity freeze;
`_compute_equity`/`_best_price`; scale-out mechanics with `PARTIAL1/2_TAKEN` idempotency;
scale-in cost-basis blending (T234-PT fix verified); `hold_days` busday counting; HK board-lot
rounding in the cap branch; aggregate open-risk and sector caps (`AUD-SECTORPCTMIRROR` present).

---

## The through-line

Domains 1-4 were gates that never fired. **Domain 5 is the inverse: an exit that fires when it
must not.** Same root shape though — a missing value treated as a meaningful reading. Finding 1
reads "no data yet" as "momentum lost"; Domain 3's watchdog read a calibrated value as a
baseline. Both produce confident, wrong action rather than an error.

**Not fixed — reported only, per the agreed protocol.**
