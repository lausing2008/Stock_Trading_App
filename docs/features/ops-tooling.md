## Feature Reference: T270-DBSYNC-PROD-TO-LOCAL-WEEKLY Layer 2 — scripts/sync_prod_to_local.sh (Built 2026-08-17)

**Closes the last open piece of this tracker item** — Layer 1 (a hard `Settings.env ==
"production"` gate in `scheduler.py`'s `start_scheduler()`, structurally preventing any
non-production environment from ever registering/running an alert-emitting job) and Layer 3
(an admin force-enable escape hatch) already shipped in an earlier session; this is Layer 2,
the actual prod→local sync tool that makes restoring a real prod dump locally an actual
routine workflow instead of a hypothetical one.

**Resolves the two open scoping questions the earlier Layer 1 update explicitly left
unanswered**:
1. **Manual trigger, not cron'd** — the design doc itself (`docs/DESIGN_SIX_ITEM_BATCH_
   2026-08-11.md`) found no strong reason to prefer one over the other; a manual command
   avoids a stale/rotated SSH key silently failing on an unattended schedule with nobody
   watching. (If a future session wants this on a schedule, wrapping the script in a cron/
   launchd entry needs no change to the script itself — nothing about it assumes manual
   invocation.)
2. **The PII scrub IS included by default** (`SKIP_PII_SCRUB=1` opts out) — per the design
   doc's own "I'd lean toward doing both since it's nearly free" recommendation. Explicitly
   defense-in-depth ON TOP OF Layer 1's hard env gate, never a replacement for it — the script's
   own top comment says so directly.

**What it does** (`scripts/sync_prod_to_local.sh`): SSH-based `pg_dump` (`docker exec` on the
real EC2 Postgres container, reusing `backup_db.sh`'s own established invocation pattern)
piped to the local machine, DROP/CREATE the local database (terminating existing connections
first via `pg_terminate_backend()` so a live local app connection can't block the DROP),
restore, then scrub:
- `users`/`price_alerts`/`signal_alerts`/`conditional_orders`/`earnings_alert_subscriptions`'
  real email columns rewritten to a deterministic, obviously-fake `<prefix><id>+devnull@
  example.invalid` pattern — deliberately NOT `NULL`, so a dev can still visually tell "this
  row had a real email once" from "this user never set one," and any code path that wrongly
  assumes a non-NULL email (rather than actually checking) fails loudly and obviously instead
  of silently.
- Beyond this item's own original email-only scope, but genuinely cheap and done in the same
  pass: `broker_connections.config` (real OAuth consumer keys/tokens as JSON — a materially
  higher-value target than an email address if a synced-down local machine is ever
  compromised) is blanked to `{}` and `is_authorized` reset.

**Safety guards**: refuses to run if it detects it's accidentally being invoked ON the EC2
host itself (checks for the production `docker-compose.yml` path, which would make "local" and
"prod" the same container — a real restore-onto-itself risk); aborts before touching the local
DB if the dump is suspiciously small (<1KB); checks the local Postgres container is actually
running first, with a clear error instead of a cryptic `docker exec` failure.

**The PII-scrub SQL syntax was verified against a real, disposable `postgres:16-alpine`
Docker container before trusting it** — not assumed correct from memory, matching this repo's
own standing "verify against real behavior, don't trust a hand-written guess" discipline.

**Local-only dev tool — no EC2/production deploy needed or performed.** Verified via direct
code read rather than a live run (an actual live run would mean really SSHing into and
dumping the real production database, a genuine — if reversible — action outside this
session's own scope of EC2 changes).

**Usage**:
```bash
bash scripts/sync_prod_to_local.sh                      # full sync + PII scrub (default)
SKIP_PII_SCRUB=1 bash scripts/sync_prod_to_local.sh      # restore only, no scrub (rare)
```

**What to check if this looks wrong**:
```bash
# Confirm the script exists and is executable:
ls -la scripts/sync_prod_to_local.sh
# Confirm Layer 1's env gate is still in place (this script's own safety net):
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.services.scheduler import _is_alerting_enabled
print(_is_alerting_enabled())"
```

---

