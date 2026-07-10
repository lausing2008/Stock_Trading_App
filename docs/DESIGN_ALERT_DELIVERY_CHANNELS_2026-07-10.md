# Alert Delivery Channels: Email, Discord/Slack Webhook, Web Push

**Status:** All three channels are live in production as of 2026-07-10. Email is the
guaranteed-delivery primary; webhook and push are best-effort supplements — a failure in
either never blocks or fails the underlying alert.

This app fires two kinds of alerts, both defined in `services/market-data/src/services/scheduler.py`:

- **Signal alerts** — a BUY/SELL/HOLD/WAIT transition on a stock a user is subscribed to
  (`SignalAlert` rows, checked by `check_signal_alerts()`).
- **Price alerts** — a stock crossing a user-set price threshold (`PriceAlert` rows, checked
  by the price-alert scan loop).

Every alert that fires attempts delivery on **all three channels the user has configured**, not
just one. None of the channels are exclusive — a user can have email + a Discord webhook + push
enabled all at once, and every alert goes to all three.

---

## 1. Email

The only channel with retry logic and a guaranteed-delivery expectation (`email_service.py`,
SMTP/SES via `common.config`'s `email_provider` setting). Every signal/price alert email
includes the current price, subject-line signal transition, and (for signal alerts) a full
game plan / conviction breakdown. See `CLAUDE.md`'s several "Recurring Issue" sections on
email quota handling, the login-redirect-loop history, etc. — email has the most operational
history of the three channels and the most edge cases already found and fixed.

Configured via: `PUT /auth/me` with `{"email": "..."}`, or the "Alert Email" field in
Settings → Account.

## 2. Discord / Slack Webhook

**What it's for:** a *shared-channel* delivery target, distinct from personal email or a
single browser's push subscription. A webhook URL posts to a Discord/Slack channel that
anyone with access to that channel can see — useful for a trading group, a shared "signals"
channel, or just having alerts show up somewhere you're already looking (Discord/Slack open)
without needing to check an inbox.

- Signal alerts POST a Discord embed (`send_webhook_notification()` in `email_service.py`) —
  title = symbol + signal transition, color-coded green (BUY-direction) / red (SELL-direction).
- Price alerts already had their own, separately-implemented webhook delivery via
  `PriceAlert.webhook_url` (a **per-alert** field, set at alert-creation time — different from
  the user-level webhook below, which applies to every signal alert a user has).
- SSRF-guarded: the webhook URL must be `https://` and cannot target a private/internal IP
  range (`validate_webhook_url()` in `auth.py`, shared by both the per-user and per-alert paths).

Configured via: `PUT /auth/me` with `{"notification_webhook": "https://discord.com/api/webhooks/..."}`,
or the "Discord / Slack Webhook" field in Settings → Account (right below Alert Email).

**Incident, 2026-07-10 — this channel was silently dead for 9 days:** `User.notification_webhook`
was referenced in `scheduler.py` via `getattr(alert.user, "notification_webhook", None)` since
2026-07-01, and the tracker (`frontend/src/pages/improvements.tsx`, `T230-ALERTING-SLACK-DISCORD`)
was marked "done" — but the field never actually existed on the `User` model. The `getattr`
fallback meant this always silently evaluated to `None` and the webhook branch never fired, for
the entire time it was believed to be working. There was also no frontend UI anywhere that would
have let a user set the value even if the field had existed. Both gaps were only discovered while
wiring Web Push into the same call site (below) and noticing the `getattr(..., None)` pattern was
suspicious. Fixed by adding the real column and the missing settings UI — see §4 for the deploy
incident this fix itself caused.

## 3. Web Push (browser/mobile)

**What it's for:** near-instant (seconds, not email's 5-15 minute latency), personal,
per-device delivery — the browser shows a native OS notification even if the tab isn't open,
as long as the browser process is running (or, on some platforms, even if it isn't).

- Uses the standard Web Push protocol (VAPID auth) via `pywebpush`
  (`services/market-data/src/services/push_service.py`).
- A user can have multiple subscriptions (one per browser/device where they clicked "Enable
  push notifications"). All of them receive every alert.
- Dead subscriptions (revoked permission, uninstalled PWA) are detected via 404/410 responses
  from the push service and pruned automatically — no manual cleanup needed.
- Fails open: if VAPID keys aren't configured server-side, or a user has no subscriptions,
  this is a silent no-op, never an error.

Configured via: the "Push Notifications" toggle in Settings → Notifications (client-side —
registers `public/sw.js`, subscribes via the browser's Push API, then calls
`POST /push/subscribe` to store the subscription server-side).

Full technical reference: `frontend/src/lib/push.ts` (browser-side subscribe/unsubscribe),
`services/market-data/src/api/push.py` (backend endpoints), `push_service.py` (send logic).

---

## 4. Incident: adding `notification_webhook` broke production login for a few minutes

**What happened:** fixing the dead-webhook bug above meant adding a new column
(`notification_webhook`) to the `User` model. The `users` table already existed in production
with real rows. This repo's only schema-application mechanism is SQLAlchemy's
`Base.metadata.create_all()` (run on every service startup, in `shared/db/session.py`) — and
`create_all()` **only creates tables that don't exist yet; it never adds columns to existing
tables.** The new `PushSubscription` table (also added this session) worked fine via this same
mechanism, because it was a brand-new table — that's what made the gap easy to miss.

The moment the rebuilt `market-data` container restarted, every query touching `User` (which is
almost everything — login, `/auth/me`, every alert check) started raising
`psycopg2.errors.UndefinedColumn: column users.notification_webhook does not exist`.

**Caught immediately** via the container's own startup/request logs showing the live error.
**Fixed** with one manual, explicit DDL statement run directly against production Postgres:

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS notification_webhook VARCHAR(2048);
```

**Verified recovered** via a real end-to-end `GET /auth/me` call returning 200 with the new
field present, and confirming no further `UndefinedColumn` errors appeared in the logs after
the fix.

**Standing gap, not a one-off:** this repo has `alembic.ini` present but zero real Alembic
migration files — there is no automated mechanism that applies a schema change to an existing
table. See the new "Recurring Issue: Adding a Column to an EXISTING Table Doesn't Auto-Apply"
section in `.claude/CLAUDE.md` for the full checklist to run through before adding any field to
an *existing* (not brand-new) model going forward.
