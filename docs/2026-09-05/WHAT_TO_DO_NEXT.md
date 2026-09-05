# WHAT TO DO NEXT — A DELIBERATELY SHORT LIST

**Date:** 2026-09-05

## The recommendation: mostly stop building for ~3 weeks

Every open question about whether this system has edge is now **data-blocked, not
build-blocked**. Measured today:

| Question | Resolved outcomes | Needed | Status |
|---|---|---|---|
| Does the prebreakout pillar lead? | **10** | ~50 | shipped today |
| Does GEX corroboration separate winners? | **0** | ~60 | shipped today |
| Does the dark-pool relative threshold work? | **0** prints | ~20/symbol | shipped today |
| Do options-flow alerts predict at 5d? | **0** | ~200 | needs time |

All four instruments were built today and have essentially no data yet. **Building more
features now would add surface area to a system whose core question is still unanswered** — and
this session showed exactly how that goes wrong: three separate findings reversed when the
sample widened.

**Timeline is concrete, not indefinite.** Prebreakout fires ~2.0/day, so ~50 resolved outcomes
lands in **roughly 3 weeks**.

---

## The one thing worth building now

### Deploy-drift detection

**Why it jumps the queue:** it caused **two** production incidents today, and it is the only
finding that silently corrupts *every other measurement*.

- `news-intelligence` ran **six-week-old code** — its real-time feed was dead, while the
  container reported healthy the entire time.
- `decision-engine` had a stale `shared/db/models.py` and would have crash-looped on next
  restart.

Confirmed: **no such check exists** (`grep` for `deploy_drift|git_sha|deployed_commit` → nothing),
despite 58 DQ checks covering everything else.

**The specific risk to the 3-week plan:** if a container silently reverts mid-window, the
measurements above become garbage and nobody finds out until the analysis produces nonsense.
This protects the wait.

**Effort:** low. Compare in-container source checksums against the deployed commit, surface as a
DQ check. The framework already exists.

---

## Do these only if the itch to build is irresistible

Ranked by value-per-risk. All are small, none touches signal logic.

1. **Alpaca-stream liveness check** — the WebSocket was dead six weeks undetected. Same
   `_record_job_status` pattern already used by 17 jobs.
2. **Tighten `max_portfolio_drawdown_pct` 20% → 10–12%** — config only. 20% permits a lot of
   destruction while expectancy is negative.
3. **Disable HK SWING** — −5.66%/trade, holds 3 of the 5 worst dollar losses. Config only.
4. **Extend `by_era` to remaining dashboards** — 62% of outcomes predate the inversion fix, so
   pooled stats still blend two different systems.

---

## Explicitly do NOT do these

Each was measured this session, not assumed:

- **Don't tune any alert threshold.** Every looser variant tested performed *worse*.
- **Don't build a unified signal-quality score.** Re-weighting correlated momentum inputs cannot
  produce a non-momentum signal.
- **Don't gate on `insider_score`** despite its +0.164 correlation — it was 6 stocks splitting
  3 up / 3 down.
- **Don't run the signal-engine live-bar refactor.** High regression risk against `AUD232` (the
  fix that resolved the confidence inversion) for marginal gain.
- **Don't add multi-leg options or the AI-CIO agent fleet.** Both layer sophistication on an
  unvalidated base.
- **Don't deploy live capital.** Expectancy −0.387%, profit factor 0.653, and 97.3% single-regime
  with one bear observation.

---

## The larger build, once the 3 weeks are up

**Walk-forward validation.** Highest-value unbuilt item, on three independent grounds: §F.3's own
reasoning, D.4's live-automation gate, and this session's three measured reversals. ~2 weeks.

Do it *after* the measurement window, because its first job should be validating whatever the
prebreakout data shows.

---

## In one sentence

**Ship deploy-drift detection this week, make the four config-level changes if you want
momentum, then wait ~3 weeks and let the instruments answer the question — the build that
matters most (walk-forward) is worth more once there is something to validate.**
