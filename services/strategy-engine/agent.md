# Strategy Engine — Engineering Agent Behavior

How to behave when working on `services/strategy-engine/`. Strategy backtesting is research
tooling — accuracy of results matters more than UI polish.

---

## Mindset for This Service

A backtest is only as good as its assumptions. When working on the engine:
- Make all assumptions explicit in the response (slippage = 0, commission = 0, etc.)
- Never silently drop trades that don't have complete data — count them and report
- The absence of walk-forward validation means all backtest results have look-ahead risk;
  this is a known limitation, not a bug to patch locally

**Never add schema migrations in this service.** Migrations run from market-data only.
The startup SELECT 1 is the only DB operation allowed at startup.

---

## Working on the DSL Evaluator

The DSL is the user-facing API — changes to what operators or indicators are supported
are API-breaking changes for existing strategies. Be conservative:
- Add new operators/indicators freely
- Never remove or rename existing ones without a migration path

When adding a new indicator to the DSL:
1. Verify the indicator is available in the TA service (or computable from price data locally)
2. Add it to `evaluator.py`'s indicator resolver
3. Add a test case with a known signal date and verify the condition fires correctly

---

## Working on the Backtest Engine

Backtest changes are easy to get wrong in subtle ways:
- **Look-ahead bias**: the decision at time T must use only data available at T, not T+1
- **Fill price**: use next-bar open (or close if simplifying), not the signal bar's close
- **Boundary conditions**: what happens when a stock is halted, has a data gap, or is delisted?

When in doubt, add an explicit assumption comment and document in the API response:
```python
# Fill at next-bar close (simplified — no slippage model)
entry_price = next_bar["close"]
```

---

## Deployment

```bash
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "git -C /home/ec2-user/Stock_Trading_App pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/services/strategy-engine/src/<path>/<file> \
   stockai-strategy-engine-1:/app/src/<path>/<file> && \
   docker restart stockai-strategy-engine-1"
```
