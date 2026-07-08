# Ranking Engine — Engineering Agent Behavior

How to behave when working on `services/ranking-engine/`. Rankings surface the best setups —
accuracy here directly affects which stocks users see first.

---

## Mindset for This Service

Rankings are a discovery surface, not a trading signal. Users look at top-ranked stocks to find
candidates; the signal and DE then decide whether to trade. Don't conflate K-score with signal
direction — they measure different things (relative rank vs absolute conviction).

**Changes to K-score weights affect every stock in every market simultaneously.** Test with a
broad sample (20+ stocks across sectors and markets) before deploying weight changes.

---

## Modifying K-Score (`kscore.py`)

When adjusting component weights:
1. Ensure weights sum to 100 (or normalize — pick one convention and stick to it)
2. Test with a distribution of stocks: what does the top 10 look like before vs after?
3. Check that HK stocks and US stocks rank sensibly relative to each other (or are ranked separately)
4. Verify a stock with strong technical setup ranks higher than one with weak setup

When adding a new component:
- It must be computable from data already available (prices + TA + signals)
- Do not add API calls inside the K-score computation — it runs for every stock in a refresh cycle
- If the component requires an external call, pre-fetch it before the scoring loop

---

## Verifying Rankings

```bash
# Check top 20 US rankings
curl -s -H "Authorization: Bearer <token>" \
  "https://lausing.com/rankings/top?market=US&n=20" | python3 -m json.tool | head -40

# Check a specific symbol's rank
curl -s -H "Authorization: Bearer <token>" "https://lausing.com/rankings/AAPL"

# Force a refresh
curl -s -X POST -H "Authorization: Bearer <svc-token>" \
  "http://localhost:8007/rankings/refresh?market=US"
```

---

## Deployment

```bash
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "git -C /home/ec2-user/Stock_Trading_App pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/services/ranking-engine/src/<file> \
   stockai-ranking-engine-1:/app/src/<file> && \
   docker restart stockai-ranking-engine-1"
```
