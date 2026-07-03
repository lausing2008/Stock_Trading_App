# Known Limitations — Partial / Imperfect Fixes

Fixes that shipped and solved the immediate bug, but knowingly took a narrower or
less-complete approach than the full fix would require. Each entry says what was
deliberately left undone and why, so these don't get lost among the hundreds of
fully-resolved tracker items in `improvements.tsx`.

**When to add here:** a fix ships, is verified correct for the case it targets, but
you consciously scoped out part of the original ask (usually because the full version
needed a design decision, external dependency, or risk the immediate bug didn't justify).

**When to remove an entry:** once the deferred part is actually implemented — move the
detail into the corresponding `improvements.tsx` entry's `fix` field and delete the
row here.

---

## T232-PT6 — Scale-out P&L: two open trades not backfilled

**Tracker:** `T232-PT6-SCALEOUT-PNL-EXCLUDED` (done, 2026-07-03)

**What shipped:** `PaperTrade.realized_pnl` + `entry_shares` columns; every trade
opened from now on correctly accumulates partial-exit P&L into the final `pnl`/
`pct_return` at close.

**What was left out:** two production trades that already had scale-outs applied
*before* the migration ran (UPST id=23, IMVT id=7) got the generic migration
fallback (`entry_shares = shares`, `realized_pnl = 0`) instead of their true
historical values. Their true original share counts and realized P&L (~$204.58 for
UPST, ~$365.46 for IMVT) are reconstructable from `entry_decision_notes` free text,
but writing hand-parsed numbers into production financial rows was flagged as a
user-decision call rather than something to do unilaterally — the user chose the
safer no-manual-edit option.

**Effect of leaving it:** when these two specific trades eventually close, their
`pct_return` will be understated (computed against a smaller cost basis than the
real one) and their contribution to `_recent_win_rate`/`_consec_loss_streak` will
be less positive than it should be. Bounded to exactly these 2 trades — everything
else is fully correct.

**Also deferred from the original tracker ask:** "treat breakeven_stop exits
(`|pct_return| < 0.3%`) as streak-neutral" (PT-10) — a distinct behavioral change
(redefining what counts toward a loss streak), not bundled into this fix.

**Revisit:** once UPST/IMVT close naturally, no action needed (the imprecision is
self-limiting). If a similar migration ever needs to backfill mid-flight positions
again, consider parsing `entry_decision_notes` programmatically instead of leaving
the generic fallback — the text format is consistent enough (`"Scale-out-N: sold
{shares}sh @ ${price}"`) to regex-parse rather than requiring a hand reconstruction.

---

## T232-OC6 — Survivorship bias: censoring instead of scoring delistings as losses

**Tracker:** `T232-OC6-SURVIVORSHIP-IN-OUTCOMES` (done, 2026-07-03)

**What shipped:** signals whose hold window closed with no exit price found (past a
10-day ingestion-lag grace period) now write a censored `SignalOutcome` row
(`skip_reason='no_exit_price'`, `is_correct=NULL`) instead of vanishing with no row
at all. Every win-rate query already filters `is_correct IS NOT NULL`, so censored
rows are automatically excluded from win-rate math while still being visible via a
new `censored` count on `/signals/outcomes/summary` and `/evaluate`.

**What was left out:** the original tracker fix text asked to "score confirmed
delistings as full losses" — i.e., actively count them as `is_correct=False`
rather than excluding them. Not implemented because there is no reliable signal in
this system to distinguish "confirmed delisting" from "benign, longer-than-10-day
ingestion gap" — miscoding a temporary data hole as a permanent loss would trade
one bias for a different, harder-to-detect one.

**Effect of leaving it:** win rates are unbiased-by-omission (censored trades don't
inflate the denominator with survivors-only data) but are still not penalized for
the worst-case outcome (an actual delisting after a BUY signal doesn't hurt the
win rate at all, it's just excluded). The tracker's original concern — "calibration
biased optimistic exactly in the tail that matters" — is partially, not fully,
addressed.

**Revisit:** once there's a reliable way to confirm an actual delisting (e.g., a
stock status field from a data provider, or a fixed rule like ">90 days with zero
price bars and not just a market holiday gap"), switch censored rows for confirmed
delistings to `is_correct=False` instead of `NULL`.

---

## T233-ARCH-CONGRESS-DEDUP — Investigated, not fixed; re-scoped

**Tracker:** `T233-ARCH-CONGRESS-DEDUP` (still `todo`, re-scoped 2026-07-03)

**What happened:** this was originally sized as a simple "delete the duplicate,
repoint the frontend" (effort S). Investigation before starting found the two
implementations return incompatible JSON shapes (not a drop-in swap) and that
event-intelligence's own congress-data sync is *currently broken in production*
(S3 source URLs return HTTP 301) — repointing today would replace live data with
an empty table. Re-scoped to effort M with a corrected fix order (fix the sync
source first → verify real data flows → add a frontend adapter → then delete the
market-data duplicate).

**Revisit:** see the full corrected `fix` field on the tracker entry for the
4-step plan. Do not attempt the "just repoint the frontend" shortcut again without
first confirming `sync_congress_trades()` is producing `rows_upserted > 0`.

---

## T232-DL-OBSERVABILITY — Only 2 of ~60 swallowed exceptions got logging

**Tracker:** `T232-DL-OBSERVABILITY` (done, 2026-07-04)

**What shipped:** the two Redis lock-acquire call sites where failing open
re-enables a real financial race (`_run_paper_trading_step`, `check_signal_alerts`)
now log instead of silently swallowing, and the paper-trading one fails closed.

**What was left out:** the same audit found 60+ other swallowed `except: pass`
blocks across `paper_trading_engine.py`/`scheduler.py`/signal-engine. Most are
legitimately fine to fail open (skipping an optional enhancement like a macro
gate), but nearly all of them log nothing — so a Redis or upstream-service
degradation serious enough to trip many of these simultaneously would leave no
log trail anywhere except the two sites fixed here.

**Revisit:** if a future incident is hard to diagnose because of a silent
swallowed exception, this is the first place to look — the audit already has the
list of ~60 sites (Tier 232 deep logic review, `docs/AUDIT_REPORT_TIER232_2026-07-02.md`
Part 7) cataloged but not individually triaged for logging.

---

## T232-ML1-PIT-EPOCH-DATE-BUG — Fixed; retraining still required

**Tracker:** `T232-ML1-PIT-EPOCH-DATE-BUG` (done, 2026-07-02)

**What shipped:** the point-in-time fundamentals join now uses the correct date
Series instead of a bad `RangeIndex`-as-epoch bug that had silently NaN'd 4
training features for an unknown period of time.

**What was left out:** the fix makes the features live again, but every model
trained *before* the fix was trained without them. A retrain/tune_all batch is
required to actually benefit from the fix — the code fix alone doesn't improve
any currently-deployed model.

**Revisit:** confirm the next scheduled `tune_all` batch (or trigger one manually)
picked up the fix — check that `revenue_growth`/`earnings_growth`/
`return_on_equity`/`recommendation_mean` have non-null importance in the resulting
model, not just that training didn't error.
