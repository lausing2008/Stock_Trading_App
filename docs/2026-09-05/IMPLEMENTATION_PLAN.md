# IMPLEMENTATION PLAN — P0/P1/P2/P3 ROADMAP

**Date:** 2026-09-05 · **Companion to:** `TRADING_SYSTEM_AUDIT.md`, `RECOMMENDATIONS.md`

**Governing principle:** the binding constraint is **not** a missing feature. It is that three
of the most promising findings this session *reversed* when the sample was widened. So the plan
front-loads **measurement over building** — several P1 items are deliberately "wait and check,"
not "implement."

---

## P0 — DONE TODAY (16 fixes, all deployed and verified)

| Fix | Impact |
|---|---|
| `AUD-GLOBALSYMCAP-STALE` | 64% of net loss |
| news-intelligence 6-week-old code | Primary feed restored |
| decision-engine `shared/` drift | Latent crash-loop |
| `AUD-RESEARCH-INFLIGHT-LEAK` | Per-symbol permanent outage |
| `AUD-HOTNEWS-TS-STRIPPED` | Age decay never worked |
| `AUD-SCALEIN-BYPASSES-POSCAP` | 13% position vs 10% cap |
| `AUD-CHASE-ROC10` | +0.17 pts OOS |
| `AUD-LIVEBAR` (ML + T196) | Train/inference symmetry |
| `AUD-UWRATELIMIT-FLOWALERTS` | 22,031 → cached |
| `AUD-DARKPOOL` (threshold + persist) | 85% → targeted fire rate |
| `AUD-HKWEEKEND` | Weekend cycles on a closed market |
| `AUD-CONVRATIO-WEEKEND` | Guaranteed weekly false alarm |
| `AUD-PROVIDERKEY-INMEMORY` | Keys survive restarts |
| `AUD-GEXCORROBORATE-UNMEASURED` | Made testable |
| `AUD-IGNITION-NEVERFIRES` | Made visible |
| DFNS 125× split + 2 broken test files | Data + suite green |

**Verification standard applied throughout:** adversarial sabotage of each fix, confirmation
that exactly the targeted tests fail, byte-identical restore, full suite green
(market-data 2,831 · signal-engine 448 · ml-prediction 164).

---

## P1 — NEXT 3–4 WEEKS (mostly measurement, little building)

### P1.1 Evaluate the prebreakout/compression pillar ← **highest value**
- **Action:** wait for n ≥ 50 resolved outcomes, then compare forward returns vs the classic
  squeeze alert **and** vs no-signal on the same names.
- **Decision rule:** if compression genuinely leads → promote into signal generation as the
  non-momentum pillar. If not → the last untested structural idea is closed, and the honest
  conclusion is "research/risk tool, not an entry engine."
- **Effort:** analysis only. **Blocking:** time.

### P1.2 Verify the anti-chasing filter live
- **Action:** confirm the +0.17 pts OOS gain holds; confirm it blocks ~17% of BUYs as modelled.
- **Decision rule:** if live differs materially from OOS, re-derive before tuning further.

### P1.3 Verify GEX corroboration and dark-pool baselines
- **GEX:** does corroboration separate winners? If no → **do not build the `gamma_flip` gate.**
- **Dark pool:** confirm `dark_pool_prints` populates Monday and fire rate falls 85% → 5–10%.

### P1.4 Deploy-drift detection ← **only real build in P1**
- **Action:** a DQ check comparing in-container source checksums against the deployed commit.
- **Why now:** two incidents today; one hid for six weeks. **Effort:** low.

### P1.5 Alpaca-stream liveness check
- **Action:** `_record_job_status`-style liveness for the WebSocket, wired into `_DQ_CHECKS`.
- **Effort:** low. Uses machinery that already exists.

### P1.6 Config changes (trivial)
- Tighten `max_portfolio_drawdown_pct` 20% → 10–12%.
- Disable HK SWING pending review.

---

## P2 — 1–3 MONTHS (build, once P1 measurements are in)

### P2.1 C.0a execution instrumentation
Capture bid/ask/spread at decision time, plus MFE and MAE per trade. Unblocks every
execution-realism question, all currently **UNMEASURABLE**. **Effort:** medium-high.

### P2.2 Walk-forward validation (§F.3)
A **required gate** for live automation that does not exist. **Effort:** high (the prompt itself
estimates 2+ weeks). Until it exists, live autonomy is blocked regardless of expectancy.

### P2.3 Widen insider/institutional coverage
6 of 193 symbols today. Converts the strongest observed correlate (+0.164, currently 6 stocks)
into a testable hypothesis. **Effort:** medium.

### P2.4 Resolve the `squeeze_ignition` decision
Read the rejection-funnel gauges shipped today. Either loosen the binding constraint
deliberately and measure, or retire the alert. **Do not leave it running silently.**

---

## P3 — DEFERRED / BLOCKED

| Item | Blocked on |
|---|---|
| Regime robustness | **Time.** 1 bear observation. No build fixes this. |
| Signal-engine live-bar refactor | High regression risk vs `AUD232` for marginal gain. **Do not attempt.** |
| Kelly sizing (§F.4) | Requires stable positive expectancy. Kelly on a negative edge sizes *up* into losses. |
| Unified signal-quality score (E.3) | **Explicitly rejected** — re-weighting momentum inputs cannot create a non-momentum signal. |
| Multi-leg options (§F.6) | Base system unprofitable; adds complexity, not edge. |
| Portfolio concentration analysis | n=8 open positions. |

---

## THE GATE TO LIVE AUTOMATION

Do **not** deploy live capital until **all** hold:

1. Expectancy > 0 (currently **−0.387%**)
2. Profit factor > 1.0 (currently **0.653**)
3. Walk-forward validation exists and passes (currently **not implemented**)
4. Meaningful non-bull-regime sample (currently **1 bear observation**)
5. Realistic slippage measured, not assumed (currently **flat 10bps**)

**Condition 4 cannot be satisfied by building anything.** It requires the market to do something
it has not done during this system's lifetime. That is the honest timeline constraint, and no
amount of engineering shortens it.

---

## WHAT SUCCESS LOOKS LIKE IN 4 WEEKS

- Prebreakout pillar **evaluated** — promoted or closed out, either is a real result.
- Anti-chasing filter **confirmed** live.
- GEX / dark-pool **answered** — build or don't build, on evidence.
- Deploy drift and stream liveness **detected automatically**.
- HK SWING **disabled**, drawdown limit **tightened**.

None of that requires the system to become profitable. It requires it to become **measurable** —
which is the prerequisite, and which this session has moved substantially closer.
