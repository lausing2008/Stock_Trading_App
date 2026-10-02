# SR-01…SR-08 remediation, and the paper-inactivity follow-up actions

Date: October 2, 2026. Implements every finding of
[the deep review](2026-10-01-signal-options-news-and-decision-deep-review.md) and the
recommended actions of [the inactivity follow-up](2026-09-30-paper-inactivity-followup.md),
in the review's own stated order. Each fix is sabotage-verified: the code was broken, the
targeted test confirmed failing, then restored.

**No flag was enabled, no threshold tuned, no entry taken, no recovery marker touched.** One
recommended action is deliberately NOT implemented — see the end.

## The eight defects

### SR-01 — one conviction contract, read identically by both consumers

`paper_trading_engine` had been fixed (AUD-CONVGATE-IDENTITY) to ignore a conviction record
predating the signal; the **authoritative** decision engine it calls moments later still read
`signal == "BUY" and sent is False` with no identity check at all. The local fix bought
nothing end to end — a September 30 failed record still vetoed an October 1 signal through
the authoritative path, reporting "old signal failed".

The interpretation now lives in `shared/common/conviction_gate.py` and both callers ask it.
Three verdicts, and `no_information` is neither of the other two: no record, an unreadable
record, a record about a different signal, or one predating the signal all fall through to
the remaining gates.

**Timestamps alone were never sufficient**, as the review said. The producer recorded *when
it evaluated*, not *what it evaluated*; a later evaluation of an older signal still passes a
staleness test. `_store_conviction` now stamps the judged signal's own instant (`signal_ts`)
plus a `contract_version`, and the contract prefers identity over the timestamp floor
wherever both sides carry it. Every one of the four producer call sites passes it, asserted
by an AST test rather than by reading.

### SR-02 — a freshness gate that enforced itself only for some spellings

`check_hard_rejects` normalised naive `datetime` objects to UTC but not naive ISO **strings**.
`fromisoformat("2026-09-20T15:00:00")` returns naive; subtracting it from an aware `now`
raises; the blanket `except Exception: pass` skipped the gate. The same instant with `+00:00`
was rejected as 264h old. `row.ts.isoformat()` over this platform's naive UTC columns produces
exactly the bypassing form, so the gate was off for the common case — while the route parsed
the same value *correctly for display*. The age shown was right; the age enforced was never
computed.

`shared/common/signal_time.py` parses once into four distinct states, and the route passes the
parsed instant to the gate rather than the wire form. All five spellings of the witness now
agree at 264.0h.

**A deliberate behaviour change:** unreadable or future freshness evidence now **blocks**
instead of approving. An *absent* `sig_ts` still fails open — the parameter is optional and
older callers omit it — but "supplied and unreadable" is missing evidence wearing the costume
of present evidence. Entry is risk-increasing, and the house rule for those is that unknown
blocks. One existing test asserted the old fail-open and was updated with this reasoning.

The gate's clock is now passed explicitly: its tests freeze time by patching that module's
`datetime`, and a time source reached through an unpatched import is one they cannot control.

### SR-03 — delivery identity now advances only for what was delivered

`send_ok` began `True` and the resync wrote `current_chains` whenever it was still true.
Two paths reached that line with no email: every candidate on cooldown (no sender call, yet
all became "seen"), and the email cap (omitted contracts became "seen"). Cooldowns were also
claimed *before* the cap, so an omitted contract's whole (symbol, direction) pair was
suppressed by an email that never carried it.

Five states are now distinct — observed → deferred → queued → accepted | omitted — and only
`accepted` joins the seen set. The cooldown filter is a **read**; the claim follows a
successful send and covers only the delivered payload. The pair-dedup the `nx=True` claim
used to perform as a side effect is now done explicitly in memory, so removing the early
claim did not silently remove it. Every state is counted on a per-recipient accounting line.

`AUD-E09-COOLDOWNRELEASE`'s claim-then-release became unnecessary *for this job*: a cooldown
never claimed on failure needs no release. The property it guards now holds by construction
rather than by compensation. The dark-pool job is untouched and keeps the original form.

### SR-04 — direction no longer depends on provider row order

`candidates[row.option_chain] = {...}` let the last row win, so the same two events produced
"bearish" in one order and "bullish" reversed. Newest valid event now wins, with a tie broken
on measured premium then imbalance, and `<=` keeping the incumbent on a total tie so the
result is stable under any permutation. A row whose `created_at` cannot be read is
**quarantined**, not used — the feed's window is a 48-hour discovery lookback, and without a
time there is no way to tell a fresh print from a two-day-old one. Quarantined and superseded
counts are reported on the job line, so a provider that stops sending `created_at` surfaces as
a visible zero rather than as silently reordered directions.

### SR-05 — the HK holiday

The market-closed guard checked NYSE holidays for non-HK markets and only weekday/session
times for HK, so an otherwise eligible HK entry passed at 11:00 HKT on 2026-10-01, an HKEX
securities holiday. It now dispatches on the actual venue through the shared `is_trading_day`.
The consolidation this file's own earlier fix achieved (one table, not four) was necessary and
insufficient; the consumer simply never consulted it for HK.

### SR-06 — a spread whose best case is a loss is not a plan

Construction checked only `debit > 0`. A debit vertical's maximum payoff is width less debit,
so a debit at or above the width has a losing best case: the witness (strikes 100/105, mids
12 and 2 → debit 10 against width 5) computed max profit **-$500** and was still selected as
the primary recommendation. Verticals now require a positive debit, strictly below the width,
with enough margin left to survive costs, and matched leg expiries. `_mid` refuses crossed
books and non-finite prices; `_leg` refuses a leg with no readable strike or expiry and
records whether its price came from a live two-sided quote or a stale print. `_recommend`
carries a backstop that refuses any structure with a non-positive maximum payoff and reports
what it rejected — "no valid plan" is a valid answer.

### SR-07 — a staggered collar has no single-expiry payoff

The put and call selection windows (25–60 and 14–45 DTE) routinely disagree, and the matrix
reported one fixed max profit, max loss and breakeven regardless. Witness: put 95 expiring
Nov 20, call 110 expiring Oct 16, reported max profit $900 — but a short call expiring
worthless at 100 leaves the position to earn $1,900 if the stock reaches 120 by the put's
expiry, with no call capping it. Matched expiry is now a precondition. An unmatched pair is
**not silently dropped**: it is returned under `unavailable` with the reason and rendered on
the page as "Not shown, and why", because a reader who expects a collar and sees nothing
concludes the chain had no contracts — a different and wrong diagnosis.

### SR-08 — delayed arrival no longer resets perceived economic age

The writer stamped `ts = now` and the decay read `ts`, so a story published September 1 and
first ingested October 1 drew the full first-hour compression — the platform reacting to a
month-old fact as though it were breaking. The writer now classifies the gap
(`publication_age_hours_at_ingest`, `delayed_disclosure`), and the consumer applies a reduced,
separately-labelled compression.

**The fix is deliberately not "ignore it".** Newly-disclosed risk is still risk; discarding it
is the opposite error. It is treated as delayed *evidence* rather than a fresh *catalyst*,
because the sharp move a fresh headline implies has by construction already had its chance.
An unknown publication time stays `None`, never `0` — "published exactly now" is a
measurement nobody made.

The new flag value is scored by `filter_audit`: matching only the original value would have
made every delayed-disclosure compression invisible, the exact blind spot
`AUD-NEWSGATE-UNMEASURED` exists to prevent. The reduced strength is a **stated policy, not a
calibrated parameter** — no delayed-disclosure cohort has been measured — and says so where it
is defined.

## The inactivity follow-up's recommended actions

### 4 — per-portfolio scan activity, durably, with the three states kept apart

The existing badges read Redis keys with a 4-hour TTL, so "why is this portfolio not trading"
became unanswerable hours later. The portfolio list now also reports, from
`paper_entry_scan_logs`' 90-day history: last scan, last entry, candidates seen, binding
reason, and the full skip tally.

**A portfolio-level block reports `candidates_seen: null`, never `0`** — it returned before
the candidate loop ran, so the universe was never evaluated. Rendering it as zero would claim
it was searched and found empty, which is a different problem with a different fix. The UI
renders three distinct states and carries the follow-up's own caveat that these counts repeat
the same opportunity across scans and are not distinct lost trades.

### 2 — HK candidate supply, traced upstream

Read-only, against production. **HK SWING produced zero BUY signals in three days** — 126
signals in that horizon (26 SELL, 20 HOLD, 80 WAIT) and not one BUY. Portfolio 9 is HK SWING,
and its 383 zero-candidate scans are therefore explained upstream of every portfolio gate:
there were no candidates to see. The scheduler is running and the universe is evaluated.

| Market / horizon | BUY | Other | Newest BUY |
|---|---:|---:|---|
| HK SHORT | 13 | 113 | 2026-10-02 |
| HK SWING | **0** | 126 | — |
| HK LONG | 14 | 112 | 2026-10-02 |
| HK GROWTH | 4 | 122 | 2026-09-30 |
| US SWING | 9 | 409 | 2026-10-01 |

US SWING is the same shape less severely: 9 BUYs against 84 for US LONG and 67 for US SHORT.
SWING is the least BUY-productive horizon in both markets. That is a property of signal
generation, not of any portfolio's gates, and **loosening a portfolio threshold cannot fix
it.** What it does not establish: whether zero HK SWING BUYs is correct. That needs the
horizon's own thresholds measured against outcomes, which is separate work.

HK GROWTH (portfolios 4 and 10) is consistent with the follow-up's reading: 4 BUYs, the most
recent two days stale, 351 zero-candidate scans each.

### 3 — one current rejected US candidate, end to end

Portfolios 3, 6 and 891 (all US SWING) show identical scan counts and identical tallies — the
same candidates evaluated by three portfolios with the same configuration. The binding reason
on the latest scan is `not_on_watchlist` (2 of 4 candidates), then `conviction_gate` (1).
Portfolio 7's is `not_on_watchlist` (4 of 11) then `already_open_scale_in_only` (3).

So the first gate to bind on current US candidates is **watchlist membership**, not a scoring
threshold. As the follow-up warned, this is not evidence that the gate is defective — a
candidate absent from the watchlist is being refused by design. It does locate the question:
whether the watchlist should contain these symbols, not whether the entry score is too strict.

### 5 — the UI claim, checked against the database

The database records **9 entries since September 8**, two more than the follow-up's September
30 snapshot (portfolio 1 on October 1, portfolio 8 on October 1). Portfolios 9, 10 and 891
have never recorded a trade at all; portfolio 2's last entry was June 25 and portfolio 5's
September 4. Any UI showing no entries after September 8 is therefore disagreeing with stored
data, and the `last_entry_at` field added for action 4 makes that comparison directly visible
per portfolio.

### 1 — NOT implemented, and why

Portfolio 2's suspected phantom recovery marker is left untouched. The follow-up itself
records it as awaiting a separately pending approval, and marker deletion has not been
authorised.

**481 of portfolio 2's 482 scans in the last 7 days were blocked by `consecutive_losses`**,
and 635 of 636 for portfolio 5.

CORRECTED 2026-10-02, and the correction matters. I first described this as the marker's
"cost", now "measured rather than estimated". That is wrong. **These counts measure blocked
SCANS — operational persistence — not lost profitable trades.** A blocked scan does not
establish that a candidate existed, nor that one would have survived the conviction gate, the
decision engine, watchlist membership, price-drift and sizing checks, nor that any surviving
entry would have made money. The same opportunity also repeats across scans, so the figure is
not even a count of distinct opportunities. What 481/482 establishes is that the brake has
been continuously engaged; it establishes nothing about what it cost.

The recommended sequence is therefore unchanged and is NOT "reset because the number is
large": verify portfolio 2's historical grant and order evidence first, and only then consider
a targeted reset. Portfolio 5's brake is retained pending its own recovery-strategy review.

## What this does not establish

- No claim that any of these defects caused a specific lost or bad trade. The review was
  explicit that severity is prioritisation, not evidence of realised loss, and nothing here
  measures impact.
- The options findings concern **generated advice**. This work did not establish whether
  automated options-income execution consumes that strategy matrix.
- **Two POLICY CHANGES are bundled in this correctness work and must be tracked separately
  from the bug fixes.** `_HOT_NEWS_DELAYED_COMPRESS = 0.92` (SR-08) and
  `_MIN_VERTICAL_EDGE_PER_SHARE = 0.05` (SR-06) are chosen values, not corrections of wrong
  arithmetic. Their tests establish that the code behaves as specified — they do not establish
  that the specified values improve outcomes, and no cohort has been measured for either. A
  passing suite is evidence of consistency, not of usefulness. See the policy register entry
  below; neither value may be cited as calibrated.
- The HK SWING zero-BUY finding explains the inactivity; it does not establish that zero is
  the right answer.
- Items 2–8 of the review's own implementation order (M20 first-family wiring, Milestone A/B
  lineage, the paired prospective entry-timing experiment, one-at-a-time interventions) remain
  open. This covers items 1–4 of that table.


## Policy changes introduced here, tracked separately from the corrections

Seven of the eight fixes correct behaviour that was demonstrably wrong: a veto from the wrong
signal, a gate that did not run, delivery recorded for an email never sent, a direction set by
row order, a holiday not observed, a payoff summary that did not describe the position. Those
are corrections, and a passing test is the right evidence for them.

Two values are not corrections. They are choices, and they need outcome evidence that does not
exist yet:

| Constant | Value | What is established | What is NOT |
|---|---|---|---|
| `_HOT_NEWS_DELAYED_COMPRESS` | 0.92 | A delayed adverse story compresses less than a fresh one and more than not at all; the value sits between the fresh strengths and 1.0; the state is labelled and scored by `filter_audit` | That 0.92 is better than 0.85, 0.95 or no compression. No delayed-disclosure cohort has been measured. |
| `_MIN_VERTICAL_EDGE_PER_SHARE` | 0.05 | A vertical whose maximum payoff cannot clear 5c/share is refused; the refusal is deterministic and tested | That 5c is the right floor, or that refusing those structures improves results. Fees are not modelled per contract anywhere in this module. |

Both are documented at their definition as uncalibrated, and a test asserts the SR-08 one says
so. Neither should be quoted as a tuned parameter, and neither should be adjusted without a
measured cohort — adjusting an uncalibrated constant on intuition produces a second
uncalibrated constant, not an improvement.
