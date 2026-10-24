# News classification measurements and NU crossover

Read-only production checks on October 22, 2026. Local fixes only; nothing deployed or sent.
[Recorded measurements](evidence/2026-10-22-news-and-nu/measurements.json).

October 23 local fix: the filter-audit API now withholds its edge and returns insufficient evidence for an empty cohort, with matching UI handling and a new cache namespace. This is not deployed and does not change the historical production measurements below. See [the fix validation](2026-10-23-pending-working-tree-review.md#completion--local-fixes-not-deployed).

## Is AI news classification helpful?

We measure throughput and downstream signal cohorts, but have not established incremental trading value or independently measured label accuracy. The existing Signal Accuracy → Filter Audit page exposes news-related cohorts.

The latest 30-day SWING / 6-calendar-day audit found 462 BUY signals and 433 with return data. Five carried negative material hot-news flags: average return −14.25%, versus −6.60% for the 428 unflagged observations. Benchmark-relative averages were −13.17% versus −6.12%. This is consistent with a defensive warning identifying adverse observations, but five observations do not establish usefulness. The endpoint's “predictive” label is descriptive thresholding, not statistical validation. The separate news-sentiment flag had zero active observations. Its endpoint verdict incorrectly also says “predictive”: missing win rate is treated as zero. Do not use that verdict as evidence.

These are selected surviving BUY signals, not all classified headlines or all suppressed candidates. There is no causal on/off comparison, independent human-label test set, classifier precision/recall, or demonstrated net financial benefit. Historical September findings differ and should not be substituted for today's small sample.

A defensible evaluation needs a frozen classifier version, article-level deduplication and classification timestamps; a human-reviewed relevance/materiality/sentiment sample; and prospective shadow evaluation with and without news using identical candidates, entry times, exits and costs. Report sample size, distinct dates/symbols, benchmark-relative return and downside losses, uncertainty, latency and cost per eligible article. Preserve deferred/unavailable classification states. Do not change trading weights based on five observations.

## NU alert reproduction

The alert sent October 22 at 06:43 Pacific / 09:43 Eastern used a forming **daily** candle, whereas the screenshot shows **5-minute** candles. The scheduler's 260-bar EMA calculation reproduces the email at price 15.015:

| Basis | EMA50 | EMA200 |
|---|---:|---:|
| Previous daily candle, 260-bar calculation | 14.473731 | 14.488621 |
| Email price substituted into latest candle | 14.494957 | 14.493859 |
| Latest stored close at probe, 14.725 | 14.483585 | 14.490973 |

The tiny upward cross vanished as the candle changed. Both values rounded to 14.49, hiding its size. A downward EMA50/EMA200 cross is a death cross; a price decline alone does not define either crossover.

There is also a substantial recursive-seed error. At the same emailed price, 400 bars yield EMA200 14.356840 and all 755 available bars yield 14.368434, versus 14.493859 from 260. The short-history event is therefore unreliable even aside from candle completion. These are reconstructions using currently stored history, not immutable at-send snapshots.

## Local changes

- Golden/death-cross checks fetch up to 1,000 daily bars for recursive warmup. Other technical conditions retain their 260-bar calculation window.
- Exclude sessions until exchange close plus 15 minutes, using the existing calendar (DST, US early closes, HK). Require 201 completed observations so both crossing points have 200 bars. Include daily candle date and six decimal places; label the price as the daily candle close. Deduplicate the completed candle across notification dates.
- Fetch up to 1,000 five-minute bars, calculate SMA/EMA/Bollinger overlays on that history and slice to current-session timestamps. The existing 74-bar display can now show EMA200. If stored history is still insufficient, display the required bar count. Explain that daily crossover alerts must be compared on 1d.

Limitations: daily bars have no persisted finalization/update timestamp; the close buffer relies on the refresh pipeline rather than proving provider finalization. EMA initialization still depends on available history, and daily chart/server indicator windows can differ. No live browser visual verification or deployment occurred.

Validation: 6 daily-cross regressions, 72 existing alert/email regressions, 13 indicator/history tests and 10 chart component tests passed (101 total). Frontend TypeScript check, Python compilation and diff whitespace checks passed.
