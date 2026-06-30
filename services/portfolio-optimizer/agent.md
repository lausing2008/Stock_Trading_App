# Portfolio Optimizer — Engineering Agent Behavior

How to behave when working on `services/portfolio-optimizer/`. Optimization outputs drive
capital allocation — numerical correctness is non-negotiable.

---

## Mindset for This Service

Portfolio optimization math is subtle. Wrong covariance estimation, incorrect weight normalization,
or a missing constraint can produce weights that look reasonable but blow up in volatile markets.

**Always verify outputs sum to 1.0** (within floating point tolerance). A weight vector that sums
to 1.02 due to a normalization bug will silently overallocate by 2%.

**Test with degenerate inputs:** What happens with 2 perfectly correlated assets? What happens
with 1 asset? What happens with a singular covariance matrix (collinear assets)?

---

## Modifying Optimization Methods

When changing `methods.py`:
1. Verify the mathematical correctness of the change (cite the source formula)
2. Test with a portfolio of 5 assets where you know the expected output approximately
3. Check weight constraints are enforced after optimization (min_weight, max_weight)
4. Verify the output is not sensitive to small input perturbations (especially mean-variance)

For HRP specifically:
- The clustering step must use single-linkage hierarchical clustering on the correlation matrix
- The quasi-diagonalization step must reorder the covariance matrix before applying risk parity
- Changes here are mathematically sensitive — read the López de Prado paper before modifying

---

## Verifying Optimization Outputs

```bash
# Test mean-variance optimization
curl -s -X POST -H "Authorization: Bearer <token>" \
  "https://lausing.com/portfolio/optimize" \
  -H "Content-Type: application/json" \
  -d '{"symbols":["AAPL","MSFT","GOOG","AMZN"],"method":"hrp","lookback_days":252}' \
  | python3 -m json.tool

# Verify weights sum to 1.0
python3 -c "weights={'AAPL':0.35,'MSFT':0.40,'GOOG':0.25}; print(sum(weights.values()))"
```

---

## Deployment

```bash
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "git -C /home/ec2-user/Stock_Trading_App pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/services/portfolio-optimizer/src/<file> \
   stockai-portfolio-optimizer-1:/app/src/<file> && \
   docker restart stockai-portfolio-optimizer-1"
```
