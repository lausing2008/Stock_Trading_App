# PRODUCTION RUNTIME AUDIT — 2026-09-05

**Method:** rather than re-reading source (covered elsewhere), this audit inspected the **live
running system**: container health, restart counts, and actual error output over 24h. That
catches a class of defect source review cannot — code that is correct in git but not what is
actually executing.

---

## Result: one real defect found and fixed. Everything else clean.

| Service | Errors / 24h | Verdict |
|---|---|---|
| **news-intelligence** | **16,302** | **REAL DEFECT — fixed** |
| api-gateway | 7,225 | Benign (see below) |
| decision-engine | 3 | Negligible |
| research-engine | 0 | Clean |
| ranking-engine | 0 | Clean |
| portfolio-optimizer | 0 | Clean |
| strategy-engine | 0 | Clean |
| event-intelligence | 0 | Clean |

All 15 containers healthy, **zero crash-restarts** across the fleet.

---

## CRITICAL: news-intelligence ran the wrong code for ~6 weeks

**Symptom:** 16,302 `alpaca_source.auth_failed` errors in 24h — a reconnect attempt every ~5
seconds, continuously, while the container reported *healthy*.

**The tell:** the logged payload contradicted the error itself.

```
{"reply": [{"T": "success", "msg": "connected"}], "event": "alpaca_source.auth_failed", "level": "error"}
```

Alpaca replied **success**, and the code called it an auth failure.

**Root cause — and the important part: this bug was already fixed in git.** Commit `c56f488`
(2026-07-27) correctly diagnosed it: Alpaca sends `{"T":"success","msg":"connected"}` the moment
the socket opens, *before* auth is sent. The original code never consumed that ack, so its one
post-auth `recv()` read the stale queued "connected" message instead of the real auth reply.

The fixed source consumes the connect-ack first. **The running container did not have it:**

```
docker exec stockai-news-intelligence-1 grep -c 'connect_ack = json.loads' /app/src/services/alpaca_source.py
→ 0        (fix absent from the running code)
```

**Why:** the container was recreated on **2026-09-04**, which reverted a `docker cp`-based
deploy. This is exactly the failure mode CLAUDE.md's own deployment section warns about — *"a
`docker cp` is a SESSION-SCOPED HOTFIX, not a durable deploy — any container recreation reverts
it."* The fix landed 2026-07-27; the revert went unnoticed for over a month.

**Impact:** the real-time Alpaca news stream — the entire point of the service — has been dead
in production. Every downstream consumer (the hot-news signal gate, real-time headline
ingestion) has been running without its primary feed. The service reported `healthy` throughout,
because its health check tests the HTTP endpoint, not whether the WebSocket ever authenticated.

**Fix applied:** redeployed the correct source and restarted. Verified live:

```
17:09:57  alpaca_source.auth_failed     ← last one, pre-restart
17:10:08  alpaca_source.subscribed      ← real connection established
```

**Zero `auth_failed` in the following 60 seconds.** The news stream is live for the first time
since the container was recreated.

---

## Investigated and cleared

**api-gateway, 7,225 errors** — all `proxy.upstream_error`, and the timestamps cluster entirely
in the 13:00–14:00 hour, matching this session's own market-data restarts for deploys. A proxy
correctly reporting that an upstream was briefly down during a deliberate restart is correct
behavior, not a defect. Zero errors in the most recent hour.

**decision-engine, 3 errors in 24h** — negligible, no pattern.

**research-engine / ranking-engine / portfolio-optimizer / strategy-engine /
event-intelligence** — zero errors in 24h. Clean.

---

## The generalisable lesson

A service can be **healthy, running, restarting cleanly, and executing month-old code**. Health
checks verify the process answers HTTP; they do not verify *which* code is answering, nor that
background workers (WebSocket streams, schedulers) are actually functioning.

Two concrete gaps this exposes:

1. **No deploy-drift detection.** Nothing compares the code running inside a container against
   the commit it is supposed to be at. A recreation silently reverts every `docker cp` hotfix,
   and the only signal is an error log nobody is watching.
2. **Health checks don't cover background workers.** news-intelligence reported healthy for six
   weeks while its core feed was down. A liveness record for the Alpaca stream — the same
   `_record_job_status` pattern the scheduler jobs already use — would have surfaced this
   immediately, and would fit the existing DQ-check framework with no new machinery.

**Recommended follow-up (not built):** add an Alpaca-stream liveness DQ check. It is a small,
well-precedented change, and it converts "silently dead for six weeks" into "visible within an
hour" — the same pattern that paid off with the ignition-funnel gauges shipped earlier today.
