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

