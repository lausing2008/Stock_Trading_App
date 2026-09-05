# CAN THE SQUEEZE IGNITION TIER BE IMPROVED?

**Date:** 2026-09-05
**Follows:** `SHORT_SQUEEZE_ALERT_TUNING_REVIEW.md`, which found the classic short-squeeze alert
fires too late and should not be loosened. The ignition tier is designed to fire *earlier*, so
it is the natural place to look next.

---

## Headline finding: it has NEVER fired. Not once.

`squeeze_alert_outcomes` by alert type:

| Alert type | Rows | First | Last |
|---|---|---|---|
| `gamma_unwind_puts` | 211 | 2026-08-15 | 2026-09-05 |
| `gamma_unwind_calls` | 98 | 2026-08-16 | 2026-09-05 |
| `short_squeeze` | 11 | 2026-08-17 | 2026-08-24 |
| **`squeeze_ignition`** | **0** | — | — |

The job is correctly registered (`squeeze_ignition_alert_check`, every 60s) and **is running** —
its liveness record shows `status: ok, last_run: 2026-09-05T16:21:50, duration_s: 0.0`. It
executes and finds nothing, every minute, forever.

## Why it never fires

Not the reasons one would first guess — both were checked and cleared:

- **Not the universe.** 20 of 96 symbols clear the 15% short-float bar (SOUN 39.9%, TMDX 35.5%,
  AI 32.6%, QUBT 32.0%, UPST 29.1%, …).
- **Not short-interest staleness.** The 30-day cutoff is 2026-08-06 and the cached data is
  dated 2026-08-14 — **all 20 high-short symbols pass**.
- **Not subscription coverage.** Untriggered `price_alerts` include SOUN, AI, UPST, QBTS.

**It is the conjunction.** A candidate must satisfy, *on the same one-minute tick*:

| Condition | Value |
|---|---|
| Short % of float | ≥ 15% |
| Intraday move | **≥1.0% AND < 3.0%** (a narrow band) |
| RVOL | ≥ 1.3–1.8 (session-scaled) |
| Short-interest data | < 30 days old |

The move band is bounded on *both* sides by design — above 3% the classic alert owns the
candidate. So the window is a narrow move band, intersected with elevated volume, intersected
with high short float, evaluated on a 1-minute snapshot. On the measured data that intersection
is close to empty in practice.

## Is the earlier entry actually better? — the test that mattered

The reason to care: if the ignition band genuinely outperforms, it justifies loosening it. This
was tested on daily bars over high-short-float names.

**First look (3 months, 16 symbols) — encouraging:**

| Bucket | n | Avg fwd 5d | % up |
|---|---|---|---|
| IGNITION (1–3%, RVOL≥1.3) | 10 | **−0.09%** | **50.0%** |
| CLASSIC (3%+, RVOL≥1.5) | 24 | −3.18% | 37.5% |
| No signal | 710 | −0.84% | 42.3% |

That looked like real confirmation of the late-entry thesis — earlier entry beating later entry
and beating doing nothing.

**Then the sample was widened (full year, 26 symbols) — and it REVERSED:**

| Bucket | n | Avg fwd 5d | % up |
|---|---|---|---|
| CLASSIC | 97 | **+3.76%** | 48.5% |
| IGNITION | 28 | **−3.05%** | 39.3% |
| No signal | 2007 | +0.87% | 43.9% |

The two buckets swapped places entirely. Same test, larger sample, opposite conclusion.

**The standard deviation explains it.** On the year-long run over the original 16 symbols:

| Bucket | n | Avg fwd 5d | **SD** | % up |
|---|---|---|---|---|
| CLASSIC | 91 | +4.03% | **27.7** | 49.5% |
| IGNITION | 26 | −2.15% | 9.3 | 42.3% |
| No signal | 1851 | +0.98% | 20.0 | 44.1% |

A +4.03% mean with SD 27.7 at n=91 has a standard error of roughly **2.9%** — the result is
statistically indistinguishable from zero, and it flipped sign between two overlapping samples.

**Conclusion: both results are noise.** Neither the ignition band nor the classic band has
demonstrated an edge. The apparent 3-month "ignition wins" finding did not survive contact with
more data, and reporting it as a win would have been wrong.

## So can it be improved?

**Yes — but "improved" here means "made to fire at all", not "made profitable", and those are
different goals.**

The conditions could be loosened until it fires (widen the move band, drop RVOL to ~1.1, relax
short float to 10%). That would produce alerts. Nothing in the measurement says those alerts
would make money — and the sibling analysis found the *classic* alert's looser variants were
measurably worse.

### What is genuinely worth doing

1. **Decide whether this alert should exist at all.** It has run every 60 seconds since T260,
   consuming a scheduler slot and a Redis lock, and has produced zero output. Either loosen it
   deliberately and measure, or retire it. Silently running forever with no output is the worst
   of the three.
2. **If kept, instrument WHY it finds nothing.** Add a rolling counter per rejection reason
   (move-band miss, RVOL miss, short-float miss, staleness miss) — the same `_incr_rolling_counter`
   pattern the fundamentals-cache-miss gauge already uses. Right now "it fired 0 times" and "it
   is broken" are indistinguishable from outside, which is exactly the observability gap this
   repo's own DQ-check framework exists to close.
3. **Do NOT loosen blind.** Every looser variant tested in the sibling review performed worse.
   Any loosening should ship behind the counter instrumentation above so its effect is
   measurable.
4. **The structural fix remains the same as everywhere else in this system:** these alerts all
   trigger on a move that has already happened. Real improvement needs a non-momentum
   precondition (days-to-cover, borrow-rate spike, unusual call buying *before* the move), not
   a different threshold on the same momentum measure.

## Caveats

- Daily-bar reconstruction; the live alert evaluates 1-minute snapshots, so its real trigger
  frequency and entry price differ. The direction of the conclusion (narrow conjunction ⇒
  near-empty) is robust; exact counts are approximate.
- High-short-float names are extremely volatile (SD 20–28% on 5-day returns). Any conclusion
  from samples of this size should be treated as provisional — which is precisely the lesson of
  the reversal above.
