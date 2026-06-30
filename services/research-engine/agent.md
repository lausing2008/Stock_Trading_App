# Research Engine — Engineering Agent Behavior

How to behave when working on `services/research-engine/`. This is the AI integration layer —
it calls Claude to generate qualitative research that complements the quantitative signals.

---

## Mindset for This Service

The research engine is the most expensive service to run (Claude API calls at ~$0.01–$0.05 each).
Cache aggressively. Every code path that calls the Claude API should check the Redis cache first.

The research report does NOT drive trading decisions directly — it is advisory. The paper trading
engine reads `GET /research/{symbol}/summary` for divergence detection only, not for ENTER/SKIP.
Do not add research verdict as a hard gate in the DE without deliberate design.

---

## Working on Report Generation

**The prompt is the product.** The quality of the research report is entirely determined by the
prompt structure in `routes.py`. When improving reports:
1. Read the current prompt template carefully — it aggregates 7 data sources
2. Test with 3–4 diverse symbols before deploying (growth stock, value stock, HK stock, distressed)
3. Check that the response parser handles edge cases (missing fields, low-confidence outputs)
4. Consider cache invalidation — does the change warrant clearing existing cached reports?

**Do not change the response schema** (verdict values: STRONG_BUY/BUY/NEUTRAL/AVOID/SELL)
without updating every consumer (`/research/{symbol}/summary` callers, the frontend research page,
and the signal-engine divergence check).

---

## Auth Invariant for `/research/{symbol}/summary`

Signal-engine calls this endpoint from `_bulk_persist()`. It MUST have a JWT in the auth header.
If you see research divergence never appearing in logs:
1. Check signal-engine's `_bulk_persist()` for the auth header
2. Check research-engine logs for 401s: `docker logs stockai-research-engine-1 | grep 401`

**Never remove auth from `/research/{symbol}/summary`** — it contains aggregated data that
should only be accessible to authenticated services.

**Never add auth to `/research/{symbol}/trigger`** — it is intentionally open. See CLAUDE.md.

---

## Cache Management

```bash
# View cached report keys in Redis
docker exec stockai-redis-1 redis-cli keys "research:*" | head -20

# Clear cache for a specific symbol (forces fresh Claude call)
docker exec stockai-redis-1 redis-cli del "research:AAPL:SWING:2026-06-29"

# Clear all research cache (use sparingly — costs API money to rebuild)
docker exec stockai-redis-1 redis-cli keys "research:*" | xargs docker exec stockai-redis-1 redis-cli del
```

---

## Verifying Report Quality

```bash
# Fetch a full report
curl -s -H "Authorization: Bearer <token>" \
  "https://lausing.com/research/AAPL" | python3 -m json.tool | head -50

# Check verdict distribution across recent reports
docker logs stockai-research-engine-1 --since 24h | grep 'verdict' | sort | uniq -c
```

---

## Deployment

```bash
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "git -C /home/ec2-user/Stock_Trading_App pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/services/research-engine/src/api/routes.py \
   stockai-research-engine-1:/app/src/api/routes.py && \
   docker restart stockai-research-engine-1"
```
