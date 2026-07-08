# Decision Engine — Engineering Agent Behavior

How to behave when working on `services/decision-engine/`. This service determines whether capital
is deployed — accuracy and reliability matter more than throughput.

---

## Mindset for This Service

Every wrong ENTER decision deploys real paper capital. Every wrong SKIP misses a valid trade.
The DE is the highest-leverage point in the pipeline for improving paper trading P&L.

**Hard rejects are the first line of defense** — they are cheap, deterministic, and should be
correct by construction. Scoring is the second layer — it is probabilistic and tunable.

Keep hard rejects and scoring concerns separate. A gate that SHOULD be deterministic (e.g.,
signal is 5 days old) belongs in `hard_rejects.py`. A factor that SHOULD be probabilistic
(e.g., volume is slightly below average) belongs in `scorer.py` as a penalty, not a gate.

---

## Adding a Hard Reject Gate

1. Read `hard_rejects.py` to understand the existing gate interface
2. Add the gate in priority order (cheaper / more-common catches first)
3. Return `{"blocked": True, "reason": "descriptive_reason"}` on rejection
4. The calling paper trading engine logs `paper.skip_<reason>` — make the reason string match
5. Add to the gate list in `skill.md`
6. Add the corresponding config key to the portfolio config table in the market-data `skill.md`

### Gate design rules
- **Deterministic**: given the same inputs, always returns the same result
- **Fail-open**: if required data is None or missing, return `{"blocked": False}` — don't block on uncertainty
- **No scoring logic**: a hard reject is binary; no partial credit

---

## Adding or Tuning a Scoring Dimension

1. Read `scorer.py` to understand how existing dimensions are computed and weighted
2. Keep total weight sum at 12 (the denominator shown in the UI)
3. Log the per-dimension breakdown in the response `reasons` dict — the decide.tsx page shows it
4. Test with a range of inputs: what does a perfect score look like? A zero score?

---

## Verifying Changes

The `decide.tsx` page (`/decide` in the UI) shows the full DE decision breakdown including:
- Which hard reject fired (if any)
- Score per dimension
- Final position size

Use it to manually verify gate and scoring changes before deploying.

```bash
# Manually call the decide endpoint
curl -s -X POST http://localhost:8006/decide \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AAPL","style":"SWING","signal":"BUY","confidence":72,...}'
```

---

## Regime Changes

Regime affects both hard rejects and position sizing. When changing regime logic:
1. Check the Redis key is being written correctly
2. Verify that market-data's scheduler reads the same Redis key for regime-aware paper trading
3. Test with VIX spiked vs normal to confirm regime transitions work

---

## Deployment

```bash
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "git -C /home/ec2-user/Stock_Trading_App pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/services/decision-engine/src/api/core/<file> \
   stockai-decision-engine-1:/app/src/api/core/<file> && \
   docker restart stockai-decision-engine-1"
```
