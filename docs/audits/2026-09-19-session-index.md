# Session index — 2026-09-18/19

**Purpose:** one entry point for everything this session touched, in order, with links to the
full write-up for each. Individual topic files (linked below) carry the real technical detail;
this index exists so a later reader — or a later session — doesn't have to reconstruct the
sequence from scattered commits and doc timestamps. Matches the established precedent of
`docs/2026-09-05/SESSION_INDEX_AND_NEXT_STEPS.md` for a prior multi-day session.

Commits, in order: `36c2e32` → `d9fab51` → `5a446ef` → `7fb03da` → `9bdeee8` → `ec340bc` →
`4990a89` → `81ffe8f`. All eight are on `prod`, pushed and deployed; `bash
scripts/check_deploy_drift.sh` reported 0 drift after each deploy in this sequence.

## 1. T410 — independent per-window signal outcome resolution (AUD-C02/C03)

**[docs/features/independent-horizon-resolution.md](../features/independent-horizon-resolution.md)** ·
**[docs/audits/2026-09-18-c02-c03-outcome-horizon-scoping.md](2026-09-18-c02-c03-outcome-horizon-scoping.md)**

Audited all 201 non-test references to `SignalOutcome` across 12 modules (including ML
training) before touching anything — found every one already safe under existing semantics,
which flipped the plan from "patch `SignalOutcome`" to "add a new, additive
`signal_outcome_horizons` table nothing existing reads." A LONG BUY now gets a resolved 5-day
outcome without waiting for its 28-day primary to mature. Found and fixed the T409 UTC/ET
date-boundary bug twice in the process (once in the function this build depends on, once fresh
in the new coverage endpoint it added). 22 tests, 3 sabotage directions, all caught.

## 2. A01-A03 broker order lifecycle — initial scoping

**[docs/audits/2026-09-18-a01-a03-broker-lifecycle-scoping.md](2026-09-18-a01-a03-broker-lifecycle-scoping.md)**

Re-verified all three findings from a prior day's external audit against current source (not
the ~2-day-old audit text): all three still true. Found two more in the process — a second
hand-maintained "faithful reimplementation" of the close flow in `conditional_orders.py`, and
that manual exit/liquidation never touch the broker at all. Deliberately scoped, not
implemented, that day — the fix needs a durable order-state machine, not a column patch.

## 3. "Why isn't US SWING After 09082026 trading?" → AUD-MINRR-STYLEBLIND → its own correction

This is the longest, most consequential thread of the session, and the one place this session's
own work needed correcting mid-stream. In order:

1. **Diagnosis** — confirmed the portfolio wasn't uniquely broken: every US SWING portfolio had
   zero entries since 2026-09-03/04 (16+ days) while GROWTH/LONG traded normally. Traced this to
   an auto-calibrated `min_rr_ratio` floor of 2.25 that appeared, from an ad-hoc `_decide()`
   reproduction, to sit right where SWING's own R:R structure clusters.
2. **AUD-MINRR-STYLEBLIND fix** (`d9fab51`) —
   [docs/incidents/self-tuning-job-performance-bugs.md](../incidents/self-tuning-job-performance-bugs.md#recurring-issue-aud-minrr-styleblind--same-bug-different-axis-style-instead-of-market-fixed-2026-09-19):
   found `calibrate_min_rr_ratio()` pools every closed trade across ALL trading styles with no
   split — the exact `AUD-MINRR-MARKETBLIND` bug class, one axis over. Built a style-aware
   `by_style` cap mirroring the existing `by_market` one. 13 tests, 2 sabotage cycles.
3. **AUD-MINRR-STYLEBLIND-STUCKFILE fix** (`5a446ef`, same doc) — discovered while trying to
   actually *run* the fix: the pooled calibration had converged on the value already live, so
   the "candidate must beat baseline" check was comparing a number against itself and could
   never pass — meaning `by_market`/`by_style` had silently stopped refreshing at all, 19 days
   stale, despite the job running on schedule. Fixed by decoupling "did the pooled number
   change" from "refresh the diagnostics." 5 more tests, verified live against production data
   (`7fb03da`, docs-only commit recording the real resulting `by_style` values).
4. **CORRECTION** (`docs/incidents/self-tuning-job-performance-bugs.md`, same entry;
   [docs/audits/2026-09-19-paper-trading-horizon-audit-review.md](2026-09-19-paper-trading-horizon-audit-review.md)) —
   an independent external audit's PT-H01 finding, re-derived and confirmed directly against
   running code and live data (not accepted on faith): `_DEFAULT_CONFIG["min_rr_ratio"] = 2.0`
   is a literal no style ever overrides, and all 11 live portfolios store `2.0` explicitly, so
   `resolve_entry_config()` always returns the key **present** — meaning `cfg.get(key,
   calibrated_fallback)` never reaches the fallback, for any portfolio, ever. The calibrated
   base floor (2.25 pooled, or this session's own by-style-corrected value) was **never** the
   real gate for a bull/neutral-regime scan. The fix from steps 2-3 is not reverted — it has a
   real, narrower effect on the choppy/risk_off regime-stiffened floor — but it did not, and
   could not, fix the reported symptom. Why SWING actually stopped trading on 2026-09-03/04
   remains genuinely unestablished: the Redis diagnostics that would show the real per-scan
   rejection reason carry a 4-hour TTL and had already expired.

**The lesson, stated plainly:** an ad-hoc reproduction that doesn't send the exact same
`config_overrides` the real production call site sends can exercise a completely different,
unrepresentative code path (here: decision-engine's "standalone caller, no overrides" fallback,
which *does* read calibration, versus the real trading path, which always overrides it
explicitly) — and get a plausible, reproducible, wrong answer. This wasn't caught by this
session's own review; it took an independent audit re-deriving the claim from scratch.

## 4. Four-document audit review and implementation batch

**[docs/audits/2026-09-19-four-audit-implementation-batch.md](2026-09-19-four-audit-implementation-batch.md)**

The user asked to review and fix/implement four documents (~40 findings total): the A01-A03
scoping doc (§2 above), an independent review that expanded broker scope to B01-B12 and
corrected several claims, a UW data-strategy audit (UW-01 to UW-09), and an alert-email-accuracy
audit (E01-E14). Implemented across three rounds the same day, each deployed and drift-checked
before starting the next:

**Round 1** (`9bdeee8`) — UW-01 (naive-UTC-date bug in 6 alert-outcome functions, the same T409
class found again), B01 (broker entry preflight now fails CLOSED on a fetch error instead of
submitting a real order from an unverified buying-power figure), B02 (`broker_exit_order_id`
column + migration 015 + a new `poll_broker_exit_fills()`, since the exit order ID was
previously logged twice and discarded), B05 (manual exit/liquidation now route through the same
shared `_place_broker_exit()` the other two close paths already use), E01 (dark-pool
email/digest showed the live quote as the execution price), E03 (flow email's "win rate"
relabeled to state it measures the underlying stock, not option P&L), E06 (AI email's
conviction score relabeled "strength N/100", never "%"), E12 (5 signal-explanation text fixes).

**Round 2** (`4990a89`, same day, "let's work on the rest") — E08 partial (a stale-price
freshness gate no longer waves a genuinely-stale universe through as fresh), E10 (options-flow
cooldown claim now races by premium, not by contract-string sort order), E02 (options game-plan
emails now check freshness and per-leg expiry before calling marks "currently listed").

**Round 3** (`81ffe8f`, same day, continued) — E04 (the flow alert's market-hours gate checked
"both US and HK closed" despite being a US-only alert; also added real per-row event age instead
of a blanket "detected right now" claim).

**Deliberately not built**, with reasons recorded in the batch doc: B03/B06-B12 (the review's
own "durable execution core" — needs real broker-paper testing, not a same-session patch),
UW-02 through UW-09 (a phased data/measurement roadmap, not single-function bugs), and
E05/E07/E08's DE-visibility half/E09/E11/E13/E14 (each needs a genuine
freshness/outbox/preference/ledger architecture addition).

Cumulative for this batch: ~64 new/updated tests across 15+ files, every fix sabotage-verified,
full market-data suite at **4091 passing** by the end of round 3 (up from 4022 at the start of
the day).

## 5. Fix-effectiveness page crash (live bug report, mid-session)

**Commit `ec340bc`** (frontend only, no dedicated topic-file entry — see the commit message and
`frontend/src/lib/fixEffectiveness.test.ts` for the full account).

User reported the page wasn't working. `fix_effectiveness.py`'s own `take_fix_snapshot()`
deliberately records a completely different JSON shape (`{status: "unsupported", domain,
reason, supported_domains}`, no `by_bucket` at all) for a domain with no snapshot metric
function registered — a real production row (`AUD-DECIDE1-LOWGATECONFIG`, `decision_making`)
already had exactly this shape. The frontend's `FixMetrics` type declared `by_bucket` as always
present, which was simply untrue; reading `.by_bucket[key]` on it crashed the whole page's
render. Fixed with a proper `FixMetricsMeasured | FixMetricsUnsupported` union, a type guard,
and an honest "cannot be measured yet" UI state. 3 tests reproducing the exact production row,
sabotage-verified.

## 6. Independent audit review — Paper Trading Engine horizon/threshold mechanics

**[docs/audits/2026-09-19-paper-trading-horizon-audit-review.md](2026-09-19-paper-trading-horizon-audit-review.md)** ·
subject: **[docs/audits/2026-09-19-paper-trading-horizon-threshold-audit.md](2026-09-19-paper-trading-horizon-threshold-audit.md)**

Covered in full in §3 above (PT-H01, the finding that corrects this session's own earlier work).
Also independently confirmed PT-H02 (the DE/fallback R:R divergence only matters in
choppy/risk_off regimes — SWING was in a neutral/bull regime when this session investigated it)
and PT-H03 (`_monitor_positions()` still uses the exact config-merge logic the entry side was
fixed away from on 2026-09-07, and never applies `_HK_MARKET_OVERRIDES` — a real, confirmed,
structural drift bug, deliberately not fixed in this pass because it would silently move real
stop/trailing settings under every currently open position). PT-H04 spot-checked and confirmed.
PT-H05-H09 read and found plausible but not independently re-derived — flagged as such rather
than claimed as re-confirmed.

## What's still open, across the whole session

- **PT-H01's real fix** (explicit manual/calibrated threshold-mode resolver) and **PT-H03's real
  fix** (`_monitor_positions()` reusing `resolve_entry_config()`) — both real, both need a
  deliberate decision about live-trading impact before shipping, not a same-pass bundle.
- **PT-H08's persistent gate-block records** — arguably the single highest-leverage unbuilt item
  from the whole session: it's the infrastructure that would make the *next* "why isn't this
  trading" question answerable in minutes instead of unrecoverable after a 4-hour TTL expires.
- **B03/B06-B12** (broker order lifecycle) and **UW-02 through UW-09** (data/measurement
  roadmap) — both explicitly scoped as multi-week efforts needing their own process, not
  reattempted here.
- **E05/E07/E09/E11/E13/E14** — the remaining alert-email findings, each needing a genuine
  architecture addition (outbox, subscription preferences, immutable event ledger).
- The full experiment framework the horizon-threshold audit itself proposes (frozen candidate
  streams, isolated virtual books, purged train/validation splits, promotion gates) — the
  audit's own stated prerequisite for trusting *any* future threshold change, not attempted here.
