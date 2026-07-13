# Watchlist Auto-Rotation

**Status:** Implemented and deployed 2026-07-13. First scheduled run: Sunday 17:00 ET.

Tracker item: `WATCHLIST-AUTO-ROTATION` (Tier 243) in `frontend/src/pages/improvements.tsx`.

---

## 1. What this is

A weekly scheduled job (`_run_watchlist_auto_rotation()` in
`services/market-data/src/services/scheduler.py`) that automatically removes consistently
underperforming stocks from a watchlist and adds newly-strong candidates — replacing what was
previously 100% manual curation.

**Context that shaped the design:** the user asked (2026-07-11) whether to replace the 4
style-specific watchlists (SHORT/SWING/LONG/GROWTH) with one generic cross-market list rotated
weekly, or keep the 4 separate lists. The recommendation given — and the one this feature
implements — was to **keep the 4 separate lists**, because each style has its own
independently-trained ML model per symbol and its own threshold profile (LONG in particular
applies a fundamentals/K-Score boost the other styles don't). "Good for GROWTH" and "good for
LONG" are genuinely different judgments about the same stock, not the same ranking viewed two
ways — merging them into one list would throw that distinction away.

## 2. Why rotation is scoped per *watchlist*, not per *style*

The obvious design would run rotation once per style (SHORT/SWING/LONG/GROWTH) against one merged
candidate pool. This is wrong for this app specifically: **checked against real production data,
several style watchlists genuinely mix US and HK stocks under the same style tag** —

| Watchlist | Style | Markets |
|---|---|---|
| Growth / Momentum | GROWTH | US + HK |
| Swing Trade | SWING | US + HK |
| 10 Days Swing Trading | SWING | US + HK |
| Long Term | LONG | US only |
| Short Term | SHORT | US only |

A per-style rotation with one merged candidate pool would risk suggesting HK candidates to a
US-heavy GROWTH watchlist, or vice versa. The job instead runs independently **per
`watchlist_id`**: each watchlist's candidate pool is scoped to that watchlist's own *dominant*
market (whichever market has more existing members; ties break toward US). A watchlist that is
purely one market only ever gets candidates from that market.

## 3. The algorithm

For each watchlist with a non-null `trading_style`:

### Drop
A stock is removed if **both**:
- It has **≥ 15 resolved outcomes** (`SignalOutcome.is_correct IS NOT NULL`) in the trailing 90
  days for that style, **and**
- Its win rate over those outcomes is **< 40%**.

### Add
Up to **3 candidates per watchlist per week**, chosen as: the highest `Ranking.score` (K-Score)
stocks, on the watchlist's dominant market, that are `Stock.active` and not already a member.

### Audit trail
Every add and every drop writes exactly one row to `TuneHistory`
(`parameter_class="watchlist_rotation"`, `parameter_name="add"` or `"drop"`) — the same
audit-trail table and discipline every other self-tuning mechanism in this codebase uses
(`signal_watchdog`, `tune_style_profiles`, `promotion_gate`, etc.). A drop's `old_value` records
`{watchlist_id, watchlist_name, stock_id, symbol}`; an add's `new_value` records the same plus
`kscore`. `validation_ev_pct`/`baseline_validation_ev_pct`/`validation_n` carry the win rate, the
40% floor, and the sample count for drops, so a rejected/accepted decision is fully reconstructable
later without touching container logs.

### Schedule
Sunday 17:00 ET — deliberately placed *after* `fundamentals_snapshot_weekly` (16:30 ET) and
`sector_rotation_weekly` (16:00 ET), so the K-Score rankings used for candidate selection are as
fresh as that week's data gets before rotation runs.

## 4. Why these specific numbers (whipsaw guard)

The tracker item's own original impact note flagged the real risk here: **whipsawing** — dropping
a stock right before it recovers, if the lookback window or minimum-sample gate isn't chosen
carefully. A 90-day sample often thin (n=4-10 was common in an earlier review of this exact data)
means a monthly rotation reacting to noise could drop stocks on statistically meaningless swings.

The **15-outcome floor is not arbitrary** — it directly mirrors `signal_watchdog`'s own floor
(raised for the identical reason, documented in `signal-engine/src/api/routes.py`'s AUD232-018
comment: *every other threshold-mutation path in this file requires a real sample floor before
acting; the watchdog previously acted on as few as 5 samples and was tightened specifically to
reduce acting on pure noise*). Using the same floor as an already-battle-tested mechanism in this
codebase, rather than inventing a new number, is the deliberate choice.

## 5. What was intentionally NOT built

**No new `WatchlistItem` provenance column** (e.g. `source: "manual" | "auto"`). Neither
`Watchlist` nor `WatchlistItem` has any field distinguishing a manually-added item from a
system-added one today, and adding one would mean altering an existing, populated production
table — this repo has no working Alembic migration path for that (see CLAUDE.md's documented
`create_all()` gap: it only creates brand-new tables, never `ALTER TABLE`s an existing one). Rather
than doing a manual production schema change for this feature, `TuneHistory` serves as the sole
audit record of what the job did. This means: a stock currently on a watchlist doesn't carry a
visible "added by rotation" badge on the watchlist itself — you have to check the Rotation History
page to see whether a given member arrived manually or via the job.

**No automatic drift/regression detection on the rotation job itself** (unlike
`position_scaling_gate`'s drift-check job) — this is a new mechanism; consider adding one after a
few months of real run history exist to look at.

## 6. Safety net: history + revert

Per explicit user request, before the rotation job's code was even compiled, a full history and
one-click revert capability was built:

- **`GET /admin/watchlist-rotation-history`** — every add/drop action, newest first, filterable
  by `watchlist_id` or `style`. Each row shows the action, symbol, watchlist, win rate (drops) or
  K-Score (adds), sample count, and whether it's already been reverted.
- **`POST /admin/watchlist-rotation-history/{id}/revert`** — undoes one specific action: reverting
  a "drop" re-adds that stock to the same watchlist it came from; reverting an "add" removes it
  again. The `TuneHistory` row itself is never deleted — it's marked reverted (via its
  `gate_failures` field, the only free-text-ish column already on that model) so the history page
  keeps an honest record of what happened and that it was later undone, rather than erasing the
  trail.
- **Frontend:** a new "Rotation History" section on the Watchlist Performance page
  (`/watchlist-performance`) — a timestamped log with a Revert button per row and a confirm
  dialog, reverted rows shown grayed out.

This means a bad week of drops/adds is always fully undoable, one action at a time, without
needing direct database access.

## 7. What to check if this needs debugging

```bash
# Confirm the job is registered and its last run status:
docker exec stockai-redis-1 redis-cli get scheduler:job:watchlist_auto_rotation

# Browse everything the job has ever done for a style:
# GET /admin/watchlist-rotation-history?style=GROWTH  (admin JWT required)

# Direct DB check — every rotation action ever recorded:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app')
from db import SessionLocal, TuneHistory
from sqlalchemy import select
with SessionLocal() as s:
    rows = s.execute(select(TuneHistory).where(TuneHistory.parameter_class=='watchlist_rotation').order_by(TuneHistory.ts.desc()).limit(20)).scalars().all()
    for r in rows: print(r.ts, r.parameter_name, r.style, r.old_value, r.new_value)
"
```

**Design invariant:** the job must never write a `WatchlistItem` without also writing the
corresponding `TuneHistory` row in the same commit — the history page is the only place a user can
see *why* a stock disappeared or appeared, so an add/drop with no matching audit row would be
exactly the kind of silent, unexplained change this feature was built to prevent.
