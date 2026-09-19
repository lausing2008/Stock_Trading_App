# Implementation batch: the four 2026-09-18 audits

**Written 2026-09-19.** The user asked to review and fix/implement four documents:

1. [A01-A03 broker lifecycle scoping](2026-09-18-a01-a03-broker-lifecycle-scoping.md)
2. [Alert email accuracy and UW playbooks audit](2026-09-18-alert-email-accuracy-and-uw-playbooks-audit.md)
3. [Unusual Whales data strategy audit](2026-09-18-unusual-whales-data-strategy-audit.md)
4. [UW and broker report review](2026-09-18-uw-and-broker-report-review.md) — an independent
   review of documents 1 and 3, which corrected several of their claims and materially expanded
   the broker lifecycle's real scope (B01-B12, not the original four call sites)

Combined, these four documents contain roughly 40 distinct findings, several of which are
explicitly scoped as multi-week infrastructure work requiring real broker-paper testing before
any of it should touch live capital (the review document's own words: "Keep broker live
enablement dependent on the expanded lifecycle scope, rather than treating the existing scoping
document as a complete implementation specification"). Implementing all of it in one pass would
mean either rushing the live-money-adjacent broker work or padding out cosmetic changes to claim
completeness — both worse than being explicit about what was actually built.

**What follows is a full accounting: every finding is either fixed (with tests and a sabotage
verification), or explicitly deferred with the reason.** Nothing was silently skipped.

## Fixed this pass

### UW-01 — alert-outcome recorders/evaluators used naive UTC dates (P1)

Exactly the T409 bug class, found in six more call sites in `scheduler.py`:
`_record_options_flow_alert_outcome()`, `_record_dark_pool_alert_outcome()`,
`evaluate_squeeze_alert_outcomes()`, `evaluate_prebreakout_alert_outcomes()`,
`evaluate_options_flow_alert_outcomes()`, `evaluate_dark_pool_alert_outcomes()`. In a UTC
container, `date.today()` reads one calendar day ahead of the true US trading date for ~4-5
hours every evening — confirmed live: a `DarkPoolAlertOutcome` row already existed dated
2026-09-19 while New York was still on September 18. In the four evaluator functions, `today`
gates `if target > target_date: still pending` — a maturity censoring check identical in shape
to `evaluate_signal_outcomes()`'s own, which resolves a window ONE DAY TOO EARLY on the naive
version.

Added a canonical `_today_et()` to `scheduler.py` (mirroring the existing one in
`options_income_engine.py`) and rewired all six sites. `test_uw01_alert_date_boundary.py` (9
tests: the exact reported moment, the naive-truncation counterexample, an EDT sanity check, and
a parametrized source-scan across all 6 call sites). Fixing this broke 3 existing test files
whose own exec()'d-source harnesses didn't provide `_today_et` in their namespace — updated each
to extract the real helper from source rather than stub a fake one, matching this repo's
established discipline for shared constants. Sabotage-verified (reverted one site, confirmed the
targeted test fails). Full 4067-test suite green at this point.

### B01 — broker-entry buying-power preflight failed OPEN on a fetch error (P0, review-confirmed)

`_place_broker_entry()`'s buying-power check fell through to `broker.place_order()` on ANY
exception fetching the account (a timeout, a dead token) — reasoning that "the broker's own
margin rejection is the backstop." The review reproduced this directly: a fake broker whose
account request raised `TimeoutError` still produced a captured BUY submission. That backstop is
the broker's own risk control, not this app's; a real order was being submitted from effectively
no buying-power check whenever the fetch was flaky.

Now fails CLOSED: the exception handler returns before `place_order()`, leaving the already-open
simulated trade untouched but sending no real order this cycle. Updated 2 existing tests that
had pinned the OLD fail-open behavior as correct (their own docstrings said so explicitly —
rewrote them to assert the new fail-closed contract, matching the established practice of
updating a test when its behavior legitimately changes rather than reverting the fix). Sabotage-
verified.

### B02 — exit order ID captured, logged, then discarded (P0, review-confirmed as B02)

`_place_broker_exit()` held a broker-returned SELL order's ID in a local variable long enough to
log it twice and then discarded it — no column existed to persist it to, so
`poll_broker_order_fills()` (entry-only) had no equivalent exit poller to exist for. If the
immediate-fill check inside `_place_broker_exit()` didn't resolve the fill right away
(after-hours, partial fill, a slow response), the exit order was orphaned: real and live at the
broker, with no durable link back to the paper trade the UI had already marked closed.

Added `broker_exit_order_id` / `broker_exit_fill_confirmed` columns to `PaperTrade` (migration
015, mirroring migration 012's entry-leg columns exactly). `_place_broker_exit()` now persists
the ID **before** the immediate-fill-check runs, not after, so it survives even if that inner
block raises. Added `poll_broker_exit_fills()`, symmetric to the existing
`poll_broker_order_fills()`, wired into the same locked scheduler cycle. 10 new tests
(`test_broker_exit_poll_and_persist.py`) plus 2 sabotage cycles. One collateral fix needed: a
new test's own numeric source-text assertion tripped `test_t401_source_text_assertion_ratchet.py`
(a repo-wide guard against exactly that anti-pattern) — rewrote the assertion to check the
reconciliation reassigns the field rather than pinning the exact rounding constants.

### B05 — manual exit and portfolio liquidation never touched the broker at all (P0, "arguably worse" per the original scoping doc)

`manual_exit_trade()` and `liquidate_portfolio()` both routed through `_close_one_paper_trade()`,
which deliberately omits broker-exit routing. For a genuinely broker-backed position, clicking
"manual exit" or liquidating a portfolio marked it closed and credited simulated cash while the
real position silently stayed open at the broker — no error, no warning, no record anything was
skipped. The review confirmed this by direct reproduction (zero broker calls on a broker-backed
close).

Both endpoints now call the **same shared `_place_broker_exit()` helper** `_monitor_positions()`
and `conditional_orders.py` already use — not a new, fifth broker-routing implementation. Guarded
identically to the two existing callers (belt-and-suspenders check on both the portfolio's
broker link and the trade's own broker-entered flag) and wrapped in try/except-and-log, so a
broker-side failure surfaces rather than silently undoing the close the user just requested. 9
new tests across `test_liquidate_portfolio.py` (4 behavioral, using the file's existing real
in-memory-DB extraction harness) and `test_manual_exit_broker_gap.py` (5 source-scan, since
`manual_exit_trade()`'s own `yfinance` dependency makes a full behavioral harness a second,
largely-duplicate investment against the same guard shape already covered behaviorally on the
sibling endpoint). 2 sabotage cycles, both caught cleanly.

### E01 — dark-pool instant email showed the live quote as the execution price (P1)

`check_dark_pool_alerts()` sets `price = live_price or exec_price` for backward compatibility,
but `send_dark_pool_alert_email()` read that same ambiguous `price` for "shares @ price" and the
vs-live comparison — so whenever a live quote existed (almost always), the email showed
"X shares @ $LIVE_PRICE ... +0.00% vs live," presenting the current quote as if it were the
block's actual execution price and making the comparison against itself always read zero. The
Flow Digest's `_shares()` had the identical bug one level removed: it divided the premium by
`alert_price` (the same ambiguous field) instead of `exec_price`, producing a wrong share count
whenever the two prices differed.

Both now read `exec_price` explicitly. A legacy row from before the T377 price split has no
`exec_price` to fall back to and renders unknown ("—"), rather than silently relabeling the live
quote as an execution price — the exact mechanism that produced the bug in the first place.
Reproduced and fixed the audit's own worked example (exec $100, live $105, 10,000 shares,
$1,000,000 premium → correctly "10,000 shares @ $100.00, -4.76%" instead of "$105.00, +0.00%").
14 new tests across `test_t383_darkpool_ui.py` (updated an existing fixture factory to carry
`exec_price` separately, since the old tests conflated the two the same way the bug did) and
`test_t376_digest_size.py` (updated `_dp()` similarly). 2 sabotage cycles on the production code
paths, both caught.

### E06 — AI signal email presented a conviction score as percentage confidence (P1)

`confidence = abs(fused_score - 0.5) * 200` is distance from a neutral fused model score, not an
observed trade-win probability — the generator's own comment already warns against reading it as
accuracy, but the email subject/body still rendered it as "X% conf" / "Confidence: X%." Relabeled
to "strength X/100" throughout (subject tag, text body, HTML card); `bullish_prob` relabeled
"Fused bullish score" rather than "probability," since it also needs cohort-specific calibration
before it represents a validated probability. 5 tests, 1 sabotage cycle.

### E12 — signal explanation text had several actionable inconsistencies (P2)

Five separate defects in `send_signal_alert_email()`:

1. `(None, "BUY")` (a stock's first-ever signal) fell through to the same `("neutral",
   "unchanged")` fallback every genuinely unmapped transition hits — a brand-new BUY read as
   "unchanged." Added explicit `(None, BUY/SELL/HOLD/WAIT)` entries.
2. The regime map recognized only `bull`/`bear`; the real, valid `neutral`/`choppy`/`risk_off`
   states this codebase's own regime classifier produces rendered the SAME "Unknown" text a
   genuinely missing regime would. Added distinct entries for all three.
3. The regime note hardcoded "S&P" even for HK symbols, where the classifier actually uses HSI.
   Now market-aware via `symbol.upper().endswith(".HK")`, matching this codebase's established
   convention for the same check elsewhere.
4. `_yn()` rendered a missing (`None`) boolean measurement as "No" — a false, stronger claim than
   "this wasn't measured." Now renders "Unknown" for `None` specifically.
5. The game-plan entry table listed three independently-labelled "50%" rows (Entry 1, Entry 2,
   Breakout) with no indication they're alternative tranches against one shared position budget,
   not three additive allocations. Added an explanatory line to both HTML and text.

7 tests, 1 sabotage cycle.

### E03 — flow alert's "historical win rate" didn't disclose what it actually measured (P1)

`send_options_flow_alert_email()` labelled a calibration statistic "Measured historical win rate
(bullish): 56% (n=733)" with no indication it measures the **underlying stock's** directional
movement (entry at next-session close, +10 **calendar** days, >0.5% hurdle) rather than an
option's actual profit or loss — the exact number a reader would otherwise use to size an
options trade. Relabeled to spell out the instrument, entry convention, horizon, and hurdle
explicitly on the row itself: "Underlying directional hit rate (bullish): 56% (n=733 contracts;
+10 calendar-day target after next-session close entry; >0.5% favorable move — NOT an option
P&L)." 1 new test, 1 sabotage cycle.

## Verification summary

Every fix above has: a test written against the real source (source-extraction where the module
can't be imported directly in this test environment, direct import/behavioral where it can), a
deliberate sabotage of the fix confirming the targeted test fails, and restoration confirming it
passes again. Final market-data suite: **4068 tests passing** (up from the 4022 baseline before
this session's earlier `AUD-MINRR-STYLEBLIND` work), zero skipped-but-should-run, zero xfail.

## Deployed

- Migration 015 (`broker_exit_order_id`, `broker_exit_fill_confirmed` on `paper_trades`) —
  additive, matches migration 012's own shape.
- `shared/db/models.py` changed → all 12 backend images rebuilt (not just market-data), per this
  project's own established discipline for a shared-model change, then `check_deploy_drift.sh`
  confirmed zero drift across all 12.

## Follow-up pass (same day, continued): E02, E08, E10

The user asked to continue with "the rest" after the first pass above shipped. Picked up three
more bounded, well-specified findings — each a targeted logic/labeling fix, none touching the
broker/live-money path.

### E08 — a stale-price freshness gate collapsed "no data" and "all data is stale" (P1, partial)

The audit's E08 has two halves; only the first is fixed here. The second (the BUY-path DE gate
admits `HOLD` alongside `BUY`, and a DE timeout/non-200 response also fails open, with neither
the verdict nor the unreachable-DE status disclosed anywhere in the sent email) needs a genuine
degraded-state concept threaded into the email body, not a logic-only fix — left open below.

`check_signal_alerts()`'s DP-3 freshness gate built `fresh_symbols` from a price-bar query, then
had one fallback: `if not fresh_symbols and symbols: fresh_symbols = set(symbols)` — "assume
fresh to avoid silent blackout." That fallback is correct for a genuinely missing-data case (no
price rows at all — a cold start) but was firing identically when the query found real rows and
EVERY one of them was too old to count as fresh, silently waving a real data problem through
with a log line that read "no price bars found," which was false. Fixed by tracking whether the
query returned any rows at all (`price_rows` — `None` on a DB error, `[]` on a genuine empty
result) and only falling open in that case; a non-empty, all-stale result now logs at ERROR and
leaves `fresh_symbols` empty, which the existing per-alert `if symbol not in fresh_symbols:
continue` loop already handles safely. 4 tests, 1 sabotage cycle.

### E10 — cooldown claim raced newly-seen flow contracts by contract-string, not size (P2)

`check_options_flow_alerts()`'s per-`(symbol, direction)` cooldown key was claimed by whichever
contract came first in `sorted(current_chains - prev_seen)` — alphabetical by the option-chain
identifier string, not a ranking. Only the first contract per pair could ever claim the cooldown
key; the later "largest premium first" ranking step only ever ranked among that loop's
survivors, so a $250,000 contract whose chain string sorted first could permanently squeeze out
a $5,000,000 contract for the same symbol/direction with no way to recover it downstream. Fixed
by sorting by premium (descending) *before* the cooldown-claim loop, so each pair's cooldown key
is now claimed by its own largest-premium contract. 4 tests, 1 sabotage cycle.

### E02 — stale options game plan attached to a BUY email with no freshness or expiry check (P1)

`get_latest_options_game_plan()` has no freshness check at all — it returns whichever snapshot
row is newest, however old, and the email unconditionally called its marks "real,
currently-listed contract prices, not a prediction." Measured live at audit time: 26 of 64
latest snapshots predated the report date. Fixed in `send_signal_alert_email()`: computes the
snapshot's age against `_today_et()` (America/New_York, matching this session's other
`*-DATEBOUNDARY` fixes — a day-granularity comparison against a daily batch snapshot has the
same UTC-evening drift risk as the alert-firing bugs fixed earlier) and relabels a snapshot older
than today as "historical reference; refresh required" in **both** HTML and text bodies (the
audit's own finding was that the text-only version omitted the as-of footer entirely). Each leg
(put/call) additionally checks its own `expiry` string against today and fails closed — an
expired or unparseable expiry is not rendered at all, rather than presented as a currently-listed
contract. Also added a one-line holdings caveat ("assume you already hold the underlying shares
— conditional illustrations, not a recommendation") per the audit's note that a protective
put/covered call presupposes stock exposure this template has no way to verify. 6 new tests
(reusing the file's existing pure-composition test harness), 2 sabotage cycles.

Full market-data suite after this follow-up pass: **4082 passing**.

### E04 — flow alerts could fire outside the real US session, and claimed stale events were fresh (P1)

Two related defects in `check_options_flow_alerts()`:

1. Its market-hours gate was `if not _is_market_hours("US") and not _is_market_hours("HK"):
   return` — this alert's own candidate universe is US-only, but the gate only skipped when
   BOTH markets were closed, so it kept running through the entire US overnight session
   whenever HK happened to be open (HK's session sits almost exactly inside US's closed hours,
   so this was most weeknight hours). Measured live: 97 of 247 candidate rows dated 2026-09-05
   onward were recorded outside 09:30–16:15 New York time. Fixed to gate on US alone.
2. The email's header unconditionally said "detected right now," but `get_flow_alerts()`'s own
   48-hour lookback window keeps the same UW row eligible for up to two days, and the adapter's
   `FlowAlert.created_at` was never even copied into the candidate dict — there was no way to
   tell an old event from a fresh one. Fixed by capturing `created_at` and rendering a real
   per-row age ("detected 2m ago" / "detected 1.3d ago"); the header no longer makes a blanket
   freshness claim since different rows in the same email can have genuinely different ages.

9 new tests, 2 sabotage cycles. 2 existing tests in `test_opt6_expired_and_clustering.py`
pinned the old "both closed" gate literally and needed updating to the new US-only assertion —
matching this session's established practice of updating a test when the behavior it pins
legitimately changes, never reverting the fix to keep a stale assertion green.

Full market-data suite after E04: **4091 passing**.

## Deliberately NOT implemented this pass, and why

### Broker lifecycle: B03, B06-B12 (the review's own "durable execution core")

The review is explicit that these require a genuinely different architecture — durable order
intents/attempts/fills, account-level reservations, provider-specific duplicate-ID contract
tests, and (for B12) a whole separate options-capability milestone — not a column patch. None of
it is safely buildable as an isolated afternoon fix on a live-money-adjacent path:

- **B03 (durable client-order-IDs)** needs both broker adapters (`etrade_broker.py`,
  `alpaca_broker.py`) to accept and forward an app-generated ID, AND a real retry/reconciliation
  path that would actually use it to detect a duplicate. Neither adapter currently retries a
  failed/ambiguous submission at all — adding an ID with nothing consuming it yet closes no real
  gap today, and the review's own sequencing note says building this before the state model in
  B02 "would add a correctly-generated ID that still can't be recovered after a crash." Worth
  doing once a real retry path exists to test it against.
- **B06/B07 (scale-out/scale-in never touch the broker)** need genuine per-position quantity
  reconciliation — tracking how many shares are broker-owned vs. simulated-only across partial
  adds/trims — which doesn't exist anywhere in this codebase yet. The review's own worked
  example (100 broker-filled shares, a 33-share simulated scale-out, then a 67-share full close
  leaving 33 real shares stranded) is real and current, but fixing it requires the same durable
  intent model as B02-in-full, not a narrower patch.
- **B08 (partial/rejected/cancelled entries treated as fully-owned quantity)**, **B09 (fractional
  quantities silently truncated via `int(qty)`)**, **B10 (the submission crash window between
  broker acceptance and DB commit)**, **B11 (account-level reservation/concurrency across
  strategies)** are all real, all still open, and all need the same order-state model as their
  prerequisite.
- **B12 (options adapter capability)** — the current E*Trade submit path hardcodes
  `orderType/securityType = EQ`; there is no multi-leg/open-close intent model at all. Out of
  scope until stock-only automation is proven.

**Containment already shipped covers the two items the review itself flagged as the highest
standalone value** (B01 fail-closed, B05 broker-exit wiring) plus the schema foundation (B02)
the rest of the lifecycle will build on. B03-B12 remain exactly what the review calls them:
milestones, not a checklist to clear same-session.

### UW-02 through UW-09 (data provenance, feature definitions, storage design, calibration reporting)

All are real findings; none are single-function bugs. UW-01 (the one concrete, bounded,
timezone-class defect in this list) is fixed above. The rest are either:

- **A migration decision** (UW-02: move the Yahoo-sourced EOD options-flow snapshot to a
  UW-sourced series without silently mixing the two in existing ML feature columns — needs a
  versioned second series, not a swap), or
- **A P0-labelled architecture requirement that only bites before automated options trading
  exists** (UW-03: settled archive bids are candidate research marks, not executable fills — real
  and worth fixing before ANY broker-backed options automation, but this app's options-income
  engine is currently paper-only, so this is a prerequisite for future work, not a live-money gap
  today), or
- **Feature-definition and measurement-design work** (UW-04 through UW-09: redefining what the
  flow outcome measures, preserving the fuller UW evidence envelope, building a point-in-time
  feature mart, structured provider-status envelopes, a storage/partition design, calibration
  confidence intervals) that the audit itself frames as a phased roadmap (Phase 0 through 5), not
  a punch list.

### Alert-email findings E05, E07, E09, E11, E13, E14

E02, E04, E08 (partial), and E10 are fixed above. Reviewed in full; deliberately not built:

- **E04's remaining gap**: `_is_market_hours()` (the shared helper this fix now correctly gates
  on) hardcodes 9:30-16:00 ET every trading day — it does not special-case early-close sessions
  (the day after Thanksgiving, Christmas Eve). That's a pre-existing limitation of a helper used
  across many call sites throughout this codebase, not specific to this alert — widening it
  correctly needs its own audit of every caller's assumptions, not a change bundled into one
  email fix.
- **E05 (flow evidence envelope)**, **E09 (cooldown consumed by a failed send)**, **E11
  (recipient scope doesn't match "your watched symbols")**, **E13 (outcome records can't
  establish emitted-email accuracy)** each require a genuine data-model or delivery-pipeline
  addition (an immutable event/setup/delivery chain, a transactional outbox, per-family
  subscription preferences) — the audit's own Phase 2 grouping, distinct from Phase 1's
  message-correctness fixes shipped here.
- **E07 (90-day accuracy badge mixes horizons/directions)** and **E08's second half (the DE gate
  fail-open path doesn't disclose the DE verdict or unreachable status anywhere in the email)**
  are real measurement-integrity gaps that need a states-not-booleans redesign
  (`fresh`/`stale`/`missing`/`unavailable`) touching more call sites than the scope of this pass.
- **E14 (Options Expiry Watch mixes a cautious watch with a directional win statistic)** needs
  the same three-message-class redesign (Observation / Setup / READY) the audit proposes for the
  whole alert family in section 5.1 — a design decision, not a bug fix.

### The entire UW playbook roadmap (audit document 3, sections 5-7; audit document 2, sections 5-7)

Both audits are explicit that this is a phased research program (regime gates, a directional-flow
quality score, ETF-flow regime study, dark-pool accumulation levels, short-squeeze state,
IV/skew strategy selection, three new playbooks) with named promotion gates (30+ distinct entry
sessions, positive incremental net expectancy, two+ market regimes, 100+ closed paper trades)
that nothing in this codebase has been run against yet. Building any of it now, before Phase 0's
integrity fixes are even fully landed, would be exactly the "ship a whale score before the data
is trustworthy" mistake both audits warn against in their own "what should not be built yet"
sections.

## What to check if this looks wrong

```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT column_name FROM information_schema.columns WHERE table_name='paper_trades' AND column_name LIKE 'broker_exit%';"
# Confirm both new columns exist.

docker exec stockai-market-data-1 grep -n "trade.broker_exit_order_id = order.order_id" /app/src/services/paper_trading_engine.py
docker exec stockai-market-data-1 grep -n "_place_broker_exit(session, trade, p)" /app/src/api/paper_portfolio.py

bash scripts/check_deploy_drift.sh
```
