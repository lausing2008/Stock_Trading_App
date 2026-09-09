## Paper Portfolio Badges Are Two Independent Layers — Don't Expect Them to Always Match Across Portfolios

**Context (2026-07-07/08):** User asked why HK SWING Portfolio and HK GROWTH Portfolio — same
market, both HK — showed different badges on the Paper Portfolio card grid. This surfaced a real
bug (below) but also a conceptual point worth documenting so it isn't re-investigated as a bug
every time it looks like this again: **the two portfolios are only supposed to agree on layer 1,
never necessarily on layer 2.**

**Layer 1 — portfolio-level / market-level gates** (`_write_gate_block()` in
`paper_trading_engine.py`, read by `/paper-portfolio/list`'s `entry_gate_block` field). One of 11
gates: `drawdown`, `daily_loss`, `weekly_loss`, `weekly_gain_lock`, `consecutive_losses`,
`daily_entry_cap`, `regime_bear`, `regime_risk_off`, `regime_suspension`, `entry_throttle`,
`heat_brake`, `index_trend`, `market_cluster_cap`. Most of these are genuinely per-portfolio
(drawdown, consecutive losses, etc.), but the three `regime_*` gates are **market-wide** — every
portfolio in the same market (`cfg["market"]`) reads the identical cached regime dict from
`GET /stocks/regime?market=HK` (see `get_last_hk_regime()` / `get_last_regime()`, the single
canonical classifier, T232-DL-REGIME5X). **Two portfolios in the same market showing DIFFERENT
regime-gate badges at the same moment is always a bug, not expected behavior.**

**Layer 2 — per-candidate "why no entry" summary** (`_write_no_entry_summary()`, read as a
separate Redis key `paper:no_entry_summary:{portfolio_id}`, shown as e.g. "Not trading: Volume
below..."). This fires when every gate in layer 1 is clear but every individual BUY candidate that
portfolio scanned still failed its own per-symbol check (K-Score, volume_z, TA score, cooldown,
etc.). This is **inherently portfolio/style-specific** — SWING and GROWTH read from different
watchlists (`Watchlist.trading_style`), so they are frequently scanning entirely different symbols
on the same tick, each with their own thresholds. **Two portfolios in the same market showing
DIFFERENT layer-2 badges is normal and expected**, not a sign anything is broken — it means their
respective candidate lists happened to fail different (or no) per-symbol checks that cycle.

**The bug found this session (T237-GATE1, fixed 2026-07-07):** layer-1's `_write_gate_block()`
Redis key only self-expired after a 4h TTL — nothing cleared it early once a portfolio actually
passed all its gates again in a later scan. HK GROWTH kept showing a "Risk-Off Regime" badge for
~2 hours after HK's regime had already recovered to `choppy`, while HK SWING (whose key had
already expired/cleared) showed nothing — this LOOKED like the two portfolios disagreeing on
regime, but was actually just one stale Redis key. Fixed by adding an unconditional
`_clear_gate_block(portfolio.id)` call once a portfolio passes the last layer-1 gate
(`market_cluster_cap`) in `_scan_for_entries()`, so the badge clears immediately instead of
waiting out the TTL. This fix is gate-agnostic — it protects against staleness on all 11 layer-1
gates, all markets, all portfolios, not just the HK regime case that surfaced it.

**What to check if this looks wrong again:**
```bash
# Confirm both portfolios in the same market are reading the identical regime:
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/regime?market=HK'
# Check each portfolio's actual layer-1 gate Redis state directly (bypass the UI):
docker exec stockai-redis-1 redis-cli get paper:gate_block:<portfolio_id>
docker exec stockai-redis-1 redis-cli ttl paper:gate_block:<portfolio_id>
# If two same-market portfolios show DIFFERENT regime_* gate reasons — that's the bug class
# above; if they show different non-regime reasons (volume, K-score) — that's layer 2, expected.
```
---

## T372-PORTFOLIO-DIGEST-CONSOLIDATE — Ten Emails Became One Per Market (Built 2026-09-09)

**REPORTED BY THE USER FROM THEIR INBOX:** ten `[Paper Portfolio]` emails arrived at 14:00 PST,
five reading **"+0.0% Total Return / $0 Total P&L"** — the five portfolios created on 2026-09-08,
which hold no positions yet.

### The cause was structural, not a bug

The loop was `for user: for portfolio: send()`, so **email count = users × active portfolios**. It
silently **doubled from 5 to 10** the day five portfolios were added, and half the new volume
carried no information. Nothing failed; the volume just grew.

### Two changes

**1. One email per market**, every portfolio as a row, with today's closed trades and open
positions consolidated across all of them:

```
[Paper Portfolio] US — Sep 09, 2026 · 6 portfolios

  GROWTH Paper Portfolio          +0.5%        +$231  open=5   sharpe= -0.16
  ETrade Sandbox SWING            -3.0%      $-1,512  open=0   sharpe= -6.44   (no activity)
  US SWING After 09082026         +0.0%          +$0  open=0   sharpe=     —   (no activity)
  ...
CLOSED TODAY (2)
  VZ   US SWING Portfolio  +$383 (+7.7%) trailing_stop
```

**2. Per-market timing.** Each fires an hour after its **own** close — `paper_portfolio_digest_us`
at 17:00 ET, `paper_portfolio_digest_hk` at 17:00 HKT. Previously a single 17:00 ET job reported
HK portfolios ~17 hours after the HK close **and gated them on the US calendar**, so an HK digest
could be skipped on a US holiday and sent on an HK one. Same class as `AUD-PT-CROSSMARKETSWEEP`.

The market filter runs **in Python, not SQL** — `config` is a `json` column, so a `->>` filter
needs a `::jsonb` cast and a portfolio omitting the key would silently vanish from its digest.

### Empty portfolios are still shown, deliberately

A portfolio at exactly 0.0% with no trades is a **real state** — it means the entry gates admitted
nothing — and hiding it would make its absence ambiguous between "nothing happened" and "the job
died". They are tagged `(no activity)` rather than omitted. **What was wrong was giving each one
its own email, not showing them.**

### The dedup scope inverted, on purpose

The key moved from `(user, portfolio, date)` to `(user, market, date)`. Keeping the portfolio id
would let a restart re-send the **same consolidated email once for every portfolio it contains** —
the exact duplicate-send the original key existed to prevent, just inverted by the consolidation.
The pre-existing test asserting the old scope was rewritten to explain that inversion rather than
deleted.

### Three pre-existing tests changed, all for the same reason

They asserted **structure** rather than **invariant**: the exact dedup key string, and an ordering
anchor that assumed dedup came *before* metrics. Metrics now run once per portfolio *before* the
recipient loop (they never depended on which user was being emailed — the old nesting recomputed
every portfolio's risk metrics for each recipient). **AUD301's isolation invariant is preserved
and still pinned**: one portfolio's bad data (`initial_capital == 0` → `ZeroDivisionError`) cannot
abort the whole market's digest.

**Volume:** 10 emails/day → 2. 22 new tests, five sabotages caught.
