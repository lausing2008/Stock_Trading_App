## INCIDENT 2026-08-05: Full EC2 Reboot Reverted signal-engine to a PRE-SPLIT Image — SA-33 No Longer Live

**Status at time of writing: NOT yet remediated.** Recorded so the recovery is not lost.

**What happened**: mid-session the instance became fully unreachable (SSH refused, HTTPS 000,
100% ping loss; own connectivity verified fine via a github.com control). User restarted the
server. All 15 containers came back **healthy** — but the reboot recreated every container, which
per this file's own standing invariant reverts every `docker cp` hotfix.

**Confirmed damage (verified against the running container, not assumed):**

| | running container | EC2 git checkout |
|---|---|---|
| `signals.py` | **2,439 lines** | 2,833 lines |
| `calibration.py` | **does not exist** | 2,614 lines |
| `outcomes.py` | **does not exist** | present |
| SA-33 / `early_recovery_trend` | **0 matches** | present |
| `tune_sell_pillars` | **absent** | present |

So signal-engine is running a **pre-2026-07-22 (pre-routes-split)** image. The T233 routes split,
SA-33 entry timing, `tune_sell_pillars`, and `bearish_pillars_active` generation are all **not
live right now**.

**Not an outage** — the service booted cleanly (`Application startup complete`, no crash loop)
and endpoints respond correctly (`/signals/accuracy` 200, `/signals/tune_status` 401 = auth
required). Those routes existed in the old single-file `routes.py`, so traffic is served. What is
lost is the newer *logic*, not availability — which is exactly why this is easy to miss.

**Proof this was caused by the reboot, not a pre-existing state**: earlier the same session I
verified against production that SA-33 was live and firing — **284 of 1,100 signals in 24h**
carried `early_recovery_trend=true`, and 1,084 carried `bearish_pillars_active`. Both are now
gone from the container.

**Recovery (standard pattern, per the 2026-07-17 incident section above):** do NOT trust a
remembered subset. Run an exhaustive sweep — for every `.py` under every `services/*/src/`, diff
the container copy against the EC2 checkout — plus `shared/db/` and `shared/common/` across all
backend containers. Then `docker cp`, clear `__pycache__`, restart, and confirm a clean startup
log. signal-engine additionally needs `main.py`'s 3-router mount and the `routers=[...]` ordering
(catch-all `/{symbol}` LAST — see `BUG233-ROUTERORDER`).

**Standing exposure this re-proves**: `docker cp` is session-scoped. Every one of these files is
still owed a real image rebuild; until then any reboot silently reverts them again.

---


## INCIDENT 2026-08-05 (RESOLVED): TLS Certificate Expired — No Auto-Renewal Was Configured At All

**Root cause of the "site externally unreachable" symptom** (distinct from, and diagnosed after,
the container-reboot recovery documented above): the Let's Encrypt cert for `lausing.com` expired
at **2026-08-05 04:20:57 UTC**. Confirmed via `curl -sv https://lausing.com`: TLS handshake
completed through ServerHello/Certificate, then the client sent
`TLS alert, certificate expired (557)` — a genuine expired-cert rejection, not a network or DNS
issue. Port 443 was open at the TCP level the whole time; nginx, the frontend container, and
api-gateway were all healthy and responding on localhost — the failure was purely the TLS
handshake, which is why `docker ps`/local `curl` checks looked completely fine while the public
site was down.

**Why it expired**: there was **no renewal automation of any kind** —
`systemctl list-timers | grep certbot` returned nothing, no root crontab entry, no
`/etc/cron.d` entry. The cert was a one-time `certbot` issuance (authenticator=nginx,
installer=nginx per `/etc/letsencrypt/renewal/lausing.com.conf`) that was never wired to renew.
Let's Encrypt certs are 90-day; this was simply the first time nobody manually renewed it in time.

**Fix applied**:
1. `sudo certbot renew --force-renewal --non-interactive` — succeeded, nginx auto-reloaded by
   certbot's own nginx installer plugin. New expiry: **2026-11-03**.
2. Added a root cron entry: `17 3 * * * /usr/bin/certbot renew --quiet --deploy-hook
   "systemctl reload nginx"`. Safe to run daily — certbot's own `renew` subcommand only actually
   renews a cert within ~30 days of its expiry, so this doesn't re-issue needlessly.
3. Verified end-to-end: `sudo certbot renew --dry-run` succeeds; `https://lausing.com` and
   `https://lausing.com/earnings` both return real 200s through the public path (not just
   localhost).

**What to check if this recurs**:
```bash
sudo certbot certificates                     # check Expiry Date / VALID-vs-INVALID
sudo crontab -l | grep certbot                 # confirm the renewal cron still exists
curl -sv https://lausing.com 2>&1 | grep -i "certificate expired"   # the exact symptom signature
```

**Design invariant**: any manually-provisioned TLS cert on this infra needs an explicit renewal
cron/timer checked in at setup time — a working cert today gives zero signal that it will keep
renewing itself. This is the SSL-equivalent of the repo's own standing "a container that looks
running can still be silently broken" discipline — a green `docker ps` said nothing about the
one thing that was actually broken.

---

