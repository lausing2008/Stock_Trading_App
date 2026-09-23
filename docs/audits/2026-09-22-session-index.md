# Session Index — 2026-09-21/22: "Are our predictions any good?"

**START HERE** for this session. 30 commits, 41 files, ~5,160 insertions. Everything is
deployed with 0 drift; all four backend suites green (market-data 4,205 · signal-engine 549 ·
event-intelligence 489 · news-intelligence 89).

The session began as "next improvements", caused a production incident, and turned into a
measurement audit that answered a question the platform had never actually asked itself.

**Primary record:** `docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md` (~1,140 lines) —
read that for evidence and methodology. This file is the map, plus the thing audits usually omit:
**what was deliberately not built, and why.**

---

## The finding that reframed everything

Not that the signals are weak — that they are **inverted**.

| | |
|---|---|
| Date-matched alpha vs SPY | **−1.076 pp** per 6-day trade, **t = −9.24** (n=4,134) |
| SELL vs BUY | SELL **+1.19%** (n=4,838) vs BUY **−2.48%** (n=13,238) — a **3.67 pp** gap the wrong way |
| Selection-adjusted ordering | `SELL +1.89% > WAIT +1.20% > HOLD −0.76% > BUY −2.04%` — perfectly monotone, wrong direction |
| Confidence | Degrades return monotonically across 7 buckets (≤40 −1.67% → >100 **−11.43%**) while hit rate stays flat ~38–43% |
| Exhaustive search | ~1,020 cells; positive-cell count **below chance expectation** |
| Live paper trading | −$8,222 over 123 closed trades, −$66.85/trade |

**The user's correction, which materially changed the conclusion.** Asked to "check only this
month" — and was right. Benchmark-matched by month: June −6.53% → July −2.02% → Aug −1.81% →
**Sept −0.32% at t = −0.99, not distinguishable from zero.** The weekly view showed it is not a
trend at all but **two discrete bad episodes** (late June −8.53%/wk, mid-Aug −2.98%/wk) against a
flat baseline, with losses concentrated in `bull` regime (n=12,570, t=−30.3) while `choppy` is
flat. "Rebuild the ranking" was downgraded to "find what changed and protect it".

---

## BUILT

### The incident that opened the session

| | commit |
|---|---|
| `AUD-CONNPOOL-NESTEDSESSION` — PT-H08's scan-log write opened a 2nd DB connection per "no entry" cycle, exhausting a 15-connection pool and cascading platform-wide. Root-caused live via `pg_stat_activity` → `pg_blocking_pids()` → a `py-spy` dump in the running container. | `a9e6bbc` |
| Defensive sweep found the same anti-pattern twice more — `_should_enter()` and `_compute_hk_breadth()` | `fb5bd07` |

### Phase 0 — measurement (complete)

| | commit |
|---|---|
| `hot_news_flag` scored in `filter_audit` for the first time — **immediately read "harmful"** | `0d2ecc3` |
| `AUD-ALPHAEVAL` — benchmark-relative alpha with **per-market** benchmarks (SPY / 2800.HK). Cross-validated at −1.13% against an independently-written −1.076% | `e55f216` |
| Phase 0d — day-clustered alpha in fix-effectiveness snapshots, built on the **existing** `FixRecord` mechanism rather than a new table | `8a72385` |

### Phase 1 — behaviour changes (all pre-registered, all measurable)

| | commit |
|---|---|
| `AUD-REGIME-MARKETBLIND` — `_fetch_market_regime()` hardcoded `market="US"`; **94.5% of HK signals were tagged `bull`** while HK was `choppy` | `af74d29` |
| `AUD-CONFSIZE-INVERTED` — confidence-weighted sizing gated off. **89 of 123 trades (72%) had been sized UP 1.25× and carried $7,042 of the loss.** Counterfactual **+$1,425** | `327eb47` |
| `AUD-EARNCOMPRESS-PROXIMITY` — pre-earnings compression suspended as an **A/B, not a fix** (see "not built" for why) | `fad1ba7` |

### The earnings thread — the one real edge found

| | commit |
|---|---|
| `AUD-PEARN-COVERAGE` — backfill window parameterised; one run filled **555 rows**, coverage **9.1% → 77.8%** | `24d269d` |
| `AUD-EARNSURPRISE-SECTOR` — impact-by-sector endpoint + Earnings Impact tab | `7f3a5ad`, `1a7be3d` |
| `AUD-EARNSURPRISE-ALERT` — fresh surprises with sector base rate + window decay, and the Live Surprises UI | `ca5d4e6`, `79063b2` |
| `AUD-EARNSURPRISE-STOCK` — per-stock history as **evidence, not a rate**, on the stock detail page | `233ea47`, `56fb5ea` |

**The edge:** a >10% EPS beat → **+4.19% over 5 days measured from the first close after the
report** (n=250) vs −0.05% in-line. Industrials +5.55% (n=89, 50 beats); Technology +4.42%
(n=293) but only a 2.50pp spread since tech drifts up on non-beats too.

### Cost and hygiene

| | commit |
|---|---|
| `AUD-NEWSCLASSIFY-ORDERING` — resolve the symbol **before** the Claude call. 70.8% of 263,457 classifications had `symbol IS NULL`; EDGAR 97.0% unusable. `news_classify` is 87% of platform Claude spend | `f1b94c4` |
| `AUD-REPORTSTAB-DEDUP` — removed duplicate News/CAPE tabs, deep-linked to Event Intelligence | `12dc9f2` |
| `AUD-PURGEDWF` — purged + embargoed walk-forward splits (see below) | `40e398c` |
| Suite greened (4 red tests) + a second ratchet fix | `6e8bc1c`, `2a63630` |

---

## NOT BUILT — and why

This is the section that decays fastest and matters most. Each of these was a deliberate call
with evidence, not an oversight.

### Rejected on the evidence

**The HMM bear gate was NOT flipped.** Refit on 20 years (bear sample 11 → **588 days**, spanning
2008/2011/2015/2018/2020/2022), the states *do* separate forward returns — but bear carries
**2.5× the volatility**, so its Sharpe (0.215) is indistinguishable from the market's (0.208) and
worse than `neutral`'s (0.265). Sizing down there is defensible **volatility targeting**; the
code's "catches early-phase downturns" comment is contradicted by its own fitted model (bear =
VIX 36.74, a coincident panic). *Do not re-lition this without new evidence.*

**SELL was NOT flipped into a long.** As an absolute strategy it is only **+0.37% alpha at
t = +0.96** — not significant. The information is in the **ranking**, not in a ready-made inverse.

**A per-stock earnings drift RATE was NOT built.** Across 130 symbols the **median is 5 earnings
events** (mean 4.8, max 9); only 6 have ≥8. NVDA is the live proof: it has beaten every quarter
on record (consistency 1.0) yet its last four 5-day moves were −2.88%, −5.09%, −0.61%, −5.56%. An
averaged per-stock figure would read ≈ −3.5% on n=4 and make a perfect-record beater look like a
short. Raw events are shown instead, with the **sector** base rate as the supported number.

**An index was NOT added** for the timed-out cohort queries. A trivial `pg_stat_activity` query
also timed out, which no index explains. The cause was **I/O starvation from a concurrent
frontend build** (`ebs-io-credit-exhaustion.md`); on a quiet instance the same query ran in
seconds. **Never run heavy queries during a frontend docker build on this instance.**

**A news outcome table was NOT added.** Effectiveness is already measurable from
`earnings_events` / `filter_audit`, because the criteria are a pure function of stored columns.
A table would have cost a `shared/db/models.py` change and an all-12-backend rebuild for no
measurement gain.

### Reviewed and declined — two external specs

**"AI Semiconductor Market Information Agent"** — declined. ~80% already existed: the options
flow filter (`_OPTIONS_FLOW_ALERT_MIN_PREMIUM = 250_000`, *looser* than the spec's $1M), earnings
consensus columns, a 4-source news pipeline, release-day-armed FRED/FOMC polling, market-implied
Fed odds. Its `/api/alerts` endpoint does not exist (it is `/api/option-trades/flow-alerts`), its
in-memory dedup is the exact `BUG-NEWSCLASSIFY-REPEATCOST` failure mode, and it reintroduces
yfinance for earnings — the dependency T404 migrated away from after a silent 3-day outage.

**"Quantitative Portfolio Manager" ML spec** — partially adopted. Its methodology is sound and it
correctly identified that the HMM is never validated walk-forward. Two of its four deliverables
already existed (`triple_barrier_labeling.py`, `walk_forward_train()`); the genuine gap was
purge/embargo, now built as `AUD-PURGEDWF`. **Rejected from it:** the 90-day GROWTH model
(1,251 days of history ≈ **10 independent windows**; purging leaves nothing), the `-1` short class
(`CLAUDE.md:161` — cash-only, no margin), and its State-1→0% rule (contradicted by the 20-year
HMM measurement above). It also calls Z-scoring against an asset's own 20-day window
"cross-sectional"; that is **time-series** normalisation, and the distinction is load-bearing for
a pooled all-ticker model.

### Deferred with evidence — the live backlog

| item | evidence | why deferred |
|---|---|---|
| **GROWTH exit config** (target ~1.5 ATR, hold 60→10) | **+1.3–1.5 pp/trade, t≈12**, worst cell of a 240-config sweep | Best-evidenced item still unbuilt. Held to avoid stacking a 4th behaviour change before measurement |
| `/api/screener/analysts` wiring | 100% ticker-tagged, structured, **zero LLM cost**, ~50 calls/day | Not started |
| EDGAR filing-type pruning | 29,303 424B2 supplements → **1** material flag; ~7,400 fund filings → zero | Superseded in part by `AUD-NEWSCLASSIFY-ORDERING` |
| Earnings alert **delivery** | detection exists; no email path | market-data half of the detect/deliver split |
| HK watchlist prune | 42 "HK" names are ~33 Tech, China semis that fell 15–25% | **User's decision** — it is their universe |
| Ranking rebuild | the inversion | Blocked on the 2026-12-04 read |

---

## Corrections made against myself

Recorded because the reasoning matters more than a clean record.

1. **Pooled framing was misleading** — the user caught it; September is statistically flat.
2. **`post_earnings_return` is a FRACTION, not a percent** (0.3384 = +33.8%). I called the column
   broken when it was not. The same trap later bit my own test fixtures.
3. **The AUD-T401 ratchet caught my own tests twice** — source-text assertions pinning numbers,
   which survive a change to `0.5 - 99999`. Replaced with value tests, then verified by sabotage.
4. **A sabotage silently failed to apply** (a comment wrapped differently), so a test "passed"
   against unmodified code. Re-run with an assertion; it does catch the regression.
5. **A benchmark-map test shadowed the source**, so pointing HK at SPY passed all 12 tests.
6. **I introduced a fail-closed bug** in `AUD-NEWSCLASSIFY-ORDERING` — an empty ticker universe
   would have silently stopped every hot-news flag. The suite caught it; now fails open with a
   log line.
7. **I got the embargo band wrong** in `AUD-PURGEDWF` — placed after the previous validation
   window, which with contiguous tiling is the current one, so it removed nothing while appearing
   to work.

---

## What is NOT yet verified

**No UI from this session has been viewed in a browser.** Bundle contents, HTTP 200 and API
payloads are all verified; visual rendering is not. `/stock/NVDA` and `/intelligence?tab=surprise`
are the two to look at.

---

## Next

Ordered by (evidence × cheapness), and **none of it should pre-empt the 2026-12-04 read**.

1. **Look at the two UI pages** — closes the only verification gap.
2. **HMM-per-fold evaluation** using `AUD-PURGEDWF`. Pure offline research, no live behaviour
   change, no attribution cost — and it answers the bear-state question empirically instead of by
   assumption. The natural next build.
3. **GROWTH exit config** — best-evidenced live change remaining, but it *is* a 4th behaviour
   change; ideally after the 2026-10-06 read.
4. `/api/screener/analysts` — additive, free, no conflict.
5. **Everything about the ranking rebuild waits for 2026-12-04.**

See `…-prediction-audit.md` §7.4 for the two dated checkpoints and the rule that matters most:
**`|t_day| < 2` means NOT YET MEASURABLE, never failure.**
