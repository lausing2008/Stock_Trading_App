# Event Intelligence — Engineering Agent Behavior

How to behave when working on `services/event-intelligence/`. Event data enriches signals with
real-world catalysts — but the data is inherently noisy and lagged.

---

## Mindset for This Service

Event data is directional context, not a precise trading signal. Insider buying suggests
conviction; it doesn't guarantee price appreciation. Congressional trading has a 15–45 day
disclosure lag — it tells you what happened, not what will happen. Frame all event data as
qualitative evidence weighting, not binary gates.

**Data sources are external and unreliable.** FRED updates on a schedule; Quiver Quant has API
rate limits; SEC EDGAR has occasional outages. Every external call must have:
- A timeout (don't let a slow FRED call block page loads)
- A fallback (return cached data or empty response, not 500)
- A logged error (so you know when a source is down)

---

## Adding a New Data Source

1. Create a new `services/{source}.py` file following the pattern of existing services
2. Handle rate limiting explicitly — use exponential backoff or respect Retry-After headers
3. Cache results in Redis with an appropriate TTL (earnings: 24h, insider: 6h, FRED: 48h)
4. Add a new endpoint in `api/routes.py` under the `/events/` prefix
5. Add the endpoint to the table in `skill.md`
6. Register the scheduler job in `scheduler.py` if it needs periodic refresh

---

## Working on Catalyst Scoring (`catalyst.py`)

The `catalyst_score` aggregates multiple event types. When adding a new event type to the score:
1. Normalize the new signal to 0–1 scale before combining
2. Document the weight assigned and the rationale
3. Test: a stock with no events should score 0; a stock with 3 insider buys + upcoming earnings should score high
4. Don't make the score a trading gate — it's advisory for the DE and research engine

---

## Handling Data Lag

Congressional trading data has 15–45 day disclosure lag. When surfacing this data:
- Always show the trade_date alongside the disclosure_date — they differ by the lag
- Don't present a congressional purchase from 30 days ago as "recent"
- Flag to users: "Disclosed on [date] for trade made on [trade_date]"

---

## Verifying Data Quality

```bash
# Check recent insider trades for AAPL
curl -s -H "Authorization: Bearer <token>" \
  "https://lausing.com/events/insider?symbol=AAPL" | python3 -m json.tool | head -30

# Check catalyst score
curl -s -H "Authorization: Bearer <token>" \
  "https://lausing.com/events/catalyst/AAPL" | python3 -m json.tool

# Check economic data freshness
curl -s -H "Authorization: Bearer <token>" \
  "https://lausing.com/events/economic" | python3 -m json.tool | grep -i 'date\|timestamp'

# Check event-intelligence scheduler logs
docker logs stockai-event-intelligence-1 --since 24h | grep -i 'sync\|error\|failed'
```

---

## Deployment

```bash
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "git -C /home/ec2-user/Stock_Trading_App pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/services/event-intelligence/src/<path>/<file> \
   stockai-event-intelligence-1:/app/src/<path>/<file> && \
   docker restart stockai-event-intelligence-1"
```
