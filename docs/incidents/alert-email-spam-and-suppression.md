## Recurring Issue: Signal Alert Email Spam — BUY→HOLD→BUY Oscillation

**Symptom:** User receives many signal change emails for the same stock within 1–2 hours,
cycling BUY→HOLD→BUY→HOLD repeatedly. Happens for stocks sitting right at the buy_threshold.

**Root cause (fixed 2026-06-18):** Two bugs compounded:

1. **`check_signal_alerts()` in `scheduler.py` called `GET /signals/{sym}` without `live=False`.**
   The signal endpoint defaults to `live=True` — it recomputes the signal fresh from current
   intraday prices on every call. Since the alert checker runs every minute and the signal
   endpoint recomputed live each time, a stock at the threshold boundary (e.g. 0981.HK) would
   flip BUY↔HOLD on every minute tick, firing an email on each flip.

2. **No same-direction cooldown.** Once a BUY email fired, if the signal dropped to HOLD and
   then recovered to BUY within minutes, a second BUY email fired immediately.

**Fix applied:**
1. Pass `live=False` in the signal fetch: `params={"style": style, "live": "false"}`. Alert
   checker now reads the stored DB signal — consistent with what the Signal Filter page shows.
   DB signals only change when scheduled refreshes run (5×/day), eliminating intraday oscillation.
2. Added 2-hour same-direction cooldown on `last_sent_at`. Even if DB signals oscillate between
   scheduled refreshes, no more than one email per 2 hours per symbol+horizon. Full BUY↔SELL
   reversals bypass the cooldown.

**File:** `services/market-data/src/services/scheduler.py`, function `check_signal_alerts()`

**What to check if oscillation recurs:**
```bash
# Check what signal the alert checker is actually reading
docker logs stockai-market-data-1 --since 2h | grep 'signal_alert'
# Confirm live=False is being passed (grep signal fetch in scheduler)
docker exec stockai-market-data-1 grep -n 'live.*false' /app/src/services/scheduler.py
```

**Design invariant:** `check_signal_alerts()` must always read DB signals (`live=False`), not
live-computed signals. The DB signal is the source of truth for the Signal Filter page — alerts
and the filter must agree on what the current signal is.

---


## Recurring Issue: Alert Email Suppression — market:refresh_failed Flag (BUG-8)

**Symptom:** All email alerts are silently suppressed for up to 6 hours. `check_signal_alerts()` logs
`signal_alert.suppressed_refresh_failed` on every run and returns early without checking any alerts.

**Root cause (found 2026-07-01):** `_post()` in `scheduler.py` sets the Redis key `market:refresh_failed`
whenever ANY downstream POST call fails all 3 retries. This includes the EDGAR 8-K sync endpoint
(`event-intelligence:8010/events/sync/8k`), which can legitimately time out when there's a large batch
of 8-K filings. A single EDGAR timeout suppresses ALL signal alerts for 6 hours.

The key value is the URL that failed (not a boolean). `check_signal_alerts()` checks `exists()` on the
key — if the key exists for ANY reason, all alerts are blocked.

**Fix applied (2026-07-01):** Removed the `setex` call from `_post()`. The function now logs the HTTP
failure but does NOT set the global flag. The per-symbol price freshness check inside `check_signal_alerts()`
(stale_cutoff = 4 days) is the correct safety net for stale data.

**Immediate fix if alerts are suppressed:**
```bash
docker exec stockai-redis-1 redis-cli exists market:refresh_failed   # 1 = flag is set
docker exec stockai-redis-1 redis-cli get market:refresh_failed      # shows which URL failed
docker exec stockai-redis-1 redis-cli del market:refresh_failed      # clears it
```

**What to check:**
1. `docker logs stockai-market-data-1 --since 6h | grep 'suppressed_refresh_failed'` — confirms suppression
2. `docker logs stockai-market-data-1 --since 6h | grep 'http_failed'` — shows which URL triggered it
3. If `event-intelligence:8010/events/sync/8k` keeps timing out: check event-intelligence container health
   and whether the EDGAR API is rate-limiting or timing out

**Design invariant:** The `market:refresh_failed` flag MUST NOT be set by ancillary service calls
(EDGAR 8-K, calibration, research triggers). It should only be set by code that directly indicates
price data is stale. Currently the flag is effectively deprecated — price freshness is checked per-symbol.

---


## Recurring Issue: BUG-MORNINGDIGEST-SENDLOOP — Same Unguarded Send-Loop Bug, Different Job (Fixed 2026-07-21)

**Symptom:** none reported yet — this was explicitly flagged as a known, same-class follow-up
when `send_premarket_brief()`'s identical bug was fixed (AUD256, 2026-07-20c), and fixed
proactively before it could produce a real incident.

**Root cause:** `send_morning_digest()` (`services/market-data/src/services/scheduler.py`) had
the exact same two gaps `send_premarket_brief()` already had: no dedup (a restart within this
job's own misfire-grace window could re-email every recipient a second time) and no
per-recipient error isolation (a single bad send would propagate to the outer
`except Exception`, aborting the whole batch and silently skipping every recipient still left
in the loop). `send_morning_digest()`'s audience is broader (all `User` rows with an email, not
the `PriceAlert`-subscribed audience `send_premarket_brief()` uses) — same bug class, different
recipient scope.

**Fix applied:** ported the identical fix pattern already proven for the pre-market brief:
a Redis dedup key scoped to `stockai:morning_digest:{user.id}:{market_key}:{date}` (20h TTL,
set only after a genuinely successful send), and the send call wrapped in its own
try/except that logs `morning_digest.recipient_send_error` and increments an `errors` counter
instead of re-raising. The dedup key deliberately includes `market_key` — `send_morning_digest()`
is called once per market (US and HK are separate invocations per its own docstring), so a
US-market digest and an HK-market digest on the same day must not collide and suppress each
other via a shared key.

**Tests**: `services/market-data/tests/test_morning_digest_send_loop.py` (new, 5 cases),
mirroring `test_premarket_brief.py`'s established source-text-extraction technique exactly
(`scheduler.py` can't be imported directly in this test environment) — the dedup check happens
before the send call, the dedup key is set only after a successful send, the send call has its
own try/except distinct from the outer one, the per-recipient error is logged/counted without
re-raising, and the dedup key is correctly scoped per-market.

**Adversarial verification** — 2 sabotage cycles, both caught and reverted: removing the dedup
check entirely, and removing the per-recipient try/except so a send exception would propagate
unguarded.

Full 344-test market-data suite (up from 339) and frontend typecheck green.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n 'stockai:morning_digest:' /app/src/services/scheduler.py
docker exec stockai-redis-1 redis-cli keys 'stockai:morning_digest:*'
```
If a user reports getting the morning digest twice on the same day for the same market, check
whether the job actually fired twice
(`docker logs stockai-market-data-1 --since 24h | grep morning_digest`) — the dedup key should
have prevented a second send within its 20h TTL.

---


## AUD-DARKPOOL-STALEPRINT — The Same Dark-Pool Print Re-Emailed Every 60 Minutes (Fixed 2026-09-08)

**Reported by the user from their own inbox**, not found by an audit sweep — two Dark Pool
Activity emails 61 minutes apart, byte-identical:

```
3:41pm   NET $39,137,875 · 137,664 shares @ $284.30 · venue L
         TSM $37,978,768 ·  86,512 shares @ $439.00 · venue L
4:42pm   ...the same two lines, again
```

They were not two events. The DB holds **exactly one** NET print and **one** TSM print, both
executed 20:00–20:04 UTC. The same blocks were re-selected and re-sent.

### Two defects combining

**1. No age filter.** `get_dark_pool_prints(symbol)` returns UW's **rolling window** — production
carried **4+ days** of prints per symbol (NET 242 rows back to 2026-09-04, TSM 1,117) — while
candidate selection is `max(qualifying, key=premium)` with no check on *when* the print executed.
So the single biggest block a symbol had ever printed stayed "the" candidate indefinitely.

**2. The cooldown key could not tell prints apart.** It was
`stockai:dark_pool_alert_cooldown:{uid}:{symbol}` — user and symbol only. It suppressed for 60
minutes and then let the **same block** through again.

The job runs **every 1 minute** against **32 candidate symbols**, each with its own independent
60-minute timer — which is why this presented as a steady drip rather than an occasional repeat.

**A worse case was latent:** a TSM print from 2026-09-04 at **$50.4M** is *larger* than either of
the two that spammed, so while it stayed in UW's window it would out-rank them and alert as
though new.

### The fix

A **90-minute age filter** (deliberately longer than the 60-minute cooldown, so a genuinely new
print cannot expire before it is ever sent) plus a cooldown key that includes the print's
**execution timestamp**. A new block alerts on the run that first sees it because it has its own
key; an already-sent one can never re-send whatever the cooldown does.

The age check **fails closed** on a missing or unparseable timestamp — an alert we cannot date is
one we cannot prove is new, and that is the entire point of the filter. Naive timestamps are
treated as UTC and a trailing `Z` is normalised, so the fix does not smuggle in a timezone bug of
the kind `AUD-EXIT-HKENTRYDATE` had just closed.

Persistence still runs **before** filtering, preserving `AUD-DARKPOOL-NOPERSIST`'s baseline
distribution.

### Why an audit missed it

The alert dedup/cooldown path was surveyed **CLEAN** on 2026-09-08 — and correctly so: the
cooldown works exactly as designed. The defect was that the *design* deduped on `(user, symbol)`
rather than on the print. No amount of reading the cooldown logic reveals that without asking:
**"what happens to the SAME print an hour later?"**

> **The generalisable check for any cooldown:** ask what its key can and cannot distinguish. A
> key that omits the identity of the thing being announced will re-announce it forever.

---

## AUD-CONVICTION-RSIDIV-NOWRITER — An Alert Email That Asserted a Check It Never Ran (Fixed 2026-09-08)

`rsi_divergence` has **no producer**. It was removed from `signals.py`, and **0 of 4,316** signals
in the last 7 days carry the key. Three consumers still read it:

1. `_is_conviction_buy`'s hard disqualifier — listed **first** in its own docstring, so it reads
   as live protection against a false BUY. It can never fire.
2. `analytics.py`'s gate-replica backtest — scores a disqualifier that never fires, so replay and
   live agree only by accident.
3. The alert email — rendered **"None detected"** to every user on every alert.

**The third one is the actual harm.** It is a *confidently false statement, not a null*: it told
the reader divergence had been **checked and found absent**, when nothing evaluated it at all.

Fixed by **omitting the row** when the key is missing, while a genuine `none` from a future
producer still renders. Other evaluated-but-empty fields keep their explicit `—`.

> The distinction between **"not measured"** and **"measured as nothing"** is the whole finding.
> Collapsing them is the same error class as `AUD-RANK-RSPLACEHOLDER`'s fabricated 50.0.

### Two corrections to the record — placed in the source, not just here

**The removal comment's premise is wrong.** It claims detection was *"hard-zeroed (argmax bug)"*.
Across the 4,678 historical rows still carrying the key: **4,147 `none`, 376 `bearish`, 155
`bullish`** — 11% non-none. **The detector did fire.** Whatever the bug was, it was not a hard
zero, and nobody recorded what it actually did — which is itself why restoring it is more work
than it looks. Corrected in `signals.py`, where someone deciding to restore it would read it.

**But the signal is too small to act on.** On resolved BUY outcomes:

| divergence | n | avg 10d | win |
|---|---|---|---|
| none | 1217 | −1.31% | 48.0% |
| **bearish** | **42** | −1.92% | 42.9% |
| bullish | 14 | −0.94% | 57.1% |

Directionally right, and **n=42**. A 0.6pp gap on 42 samples is noise — three findings in this
same session reversed under exactly that test.

The gate is **left in place** but marked `DORMANT` / `DO NOT COUNT THIS AS PROTECTION`, so a
restored producer re-arms it automatically while nobody counts it as live defence meanwhile.

### On restoring the detector

Worth knowing what it is *for*: RSI divergence measures **price rising while momentum fades** — a
**non-momentum** signal. The 2026-09-05 audit root-caused this platform's one genuinely weak
component (entry timing) to *every conviction pillar being a momentum measure*, so the use case
is real and aimed at a known weakness.

It is still not worth restoring on n=42, and **the cheaper path already exists**:
`check_prebreakout_alerts()` is the non-momentum pillar for exactly that weakness, already
accumulating outcomes, with a scheduled evaluation. Revisit only if that evaluation says another
non-momentum input is still needed.

---


---

## Detail relocated from CLAUDE.md's index (2026-09-10)

**T382-CLAUDEMD-REINDEX.** The lines below lived in `.claude/CLAUDE.md`'s Topic File Index,
which is read at the start of EVERY session and re-paid on every prompt-cache rebuild. They
were verified to be **new content, not duplicates** of this file — a sampled check found only
1-2 of 6 claims from each oversized index entry already present here — so they are moved rather
than deleted, and the index keeps a short pointer.

Preserved verbatim. Formatting is unchanged from the index entry, including its emphasis, so
nothing is lost to a reflow.

Signal Alert Email Spam — BUY→HOLD→BUY Oscillation; Alert Email Suppression — market:refresh_failed Flag (BUG-8); BUG-MORNINGDIGEST-SENDLOOP — Same Unguarded...; **AUD-DARKPOOL-STALEPRINT (2026-09-08)** — the same dark-pool print re-emailed every 60 min, **reported by the USER from their inbox after this exact path had been audited CLEAN**. The cooldown works as designed; the DESIGN deduped on `(user, symbol)` with nothing identifying the print, and `get_dark_pool_prints()` returns UW's **multi-day rolling window** with no age filter — so the biggest block a symbol ever printed stayed the candidate forever. Carries the generalisable check: **ask what a cooldown key can and cannot distinguish** — one that omits the identity of the thing being announced will re-announce it forever. **AUD-CONVICTION-RSIDIV-NOWRITER** — `rsi_divergence` has NO producer (0 of 4,316 signals in 7d) yet 3 consumers read it, and the alert email rendered **"None detected"** to every user: a confidently FALSE statement, not a null. Same error class as `AUD-RANK-RSPLACEHOLDER` — **"not measured" collapsed into "measured as nothing"**. Two corrections placed in the SOURCE: the removal comment's "hard-zeroed (argmax bug)" claim is **WRONG** (376 bearish / 155 bullish across 4,678 rows — it DID fire), and the signal is directionally right but **n=42**, far too small to restore on. Gate left in place, marked DORMANT.
