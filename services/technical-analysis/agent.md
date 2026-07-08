# Technical Analysis Service — Engineering Agent Behavior

How to behave when working on `services/technical-analysis/`. TA underpins signals, rankings,
and ML features — correctness is more important than speed.

---

## Mindset for This Service

TA computations feed into ML training, signal scoring, and research reports. An off-by-one in
a rolling window, a wrong period parameter, or incorrect normalization creates a subtle but
systematic error that propagates through the entire stack. Verify computations against reference
values before deploying changes.

**Vectorized only.** Never add row-by-row iteration over price bars. Use `pandas_ta`, numpy
vectorized operations, or rolling window functions. Performance matters — the service computes
indicators for hundreds of stocks per refresh cycle.

---

## Modifying or Adding an Indicator

1. Verify the formula against the canonical definition (TA-Lib documentation is the reference)
2. Test with a known dataset where the expected values are independently verified
3. Check the output normalization — `bb_pct` should be 0–1, not 0–100
4. Add the indicator to the endpoint response schema and update `skill.md`
5. Check whether the ML feature builder (`features/builder.py` in ml-prediction) needs updating

### Common correctness bugs
- **RSI**: verify it handles the initial period (first 14 bars) correctly — no NaN propagation
- **MACD**: check signal line uses EMA of MACD, not SMA
- **BB**: confirm `bb_pct = (price - lower) / (upper - lower)`, not `(price - SMA) / (upper - SMA)`
- **ATR**: verify it uses true range (max of high-low, |high-prev_close|, |low-prev_close|)

---

## Pattern Recognition Changes

Pattern detection is heuristic — there are no ground-truth labels. When changing detection logic:
1. Test with 10+ real examples of the pattern (visual inspection of charts)
2. Test with 10+ non-examples to check false positive rate
3. Adjust `confidence` threshold so the detection is neither too aggressive nor too selective
4. Log the pattern detection count per run — a sudden spike or drop indicates a bug

---

## Verifying Computations

```bash
# Fetch full TA for a symbol
curl -s -H "Authorization: Bearer <token>" \
  "https://lausing.com/ta/AAPL" | python3 -m json.tool

# Compare RSI to reference (Yahoo Finance or TradingView for AAPL should show ~same value)
# If off by more than 1–2 points, check period alignment and initial conditions
```

---

## Deployment

```bash
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "git -C /home/ec2-user/Stock_Trading_App pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/services/technical-analysis/src/<path>/<file> \
   stockai-technical-analysis-1:/app/src/<path>/<file> && \
   docker restart stockai-technical-analysis-1"
```
