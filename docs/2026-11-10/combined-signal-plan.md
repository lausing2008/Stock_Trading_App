# Combined signal test (E3) — plan fixed before any result (2026-11-10)

Written for whoever runs or reviews the test. It follows
`signal-research-what-finds-trading-signals.md`, section 7d, which listed four experiments.
They are steps of one test, so they are run as one:

| Step | Status when this plan was written |
|---|---|
| E1 re-check the platform's +4.19% earnings figure | Done: `combined-signal-evidence/e1-results.txt`. Results go in the results document, not here |
| E2 analyst revisions | EPS-estimate revision history is not available free (the platform stores 5 weeks; Yahoo gives only today's). Yahoo's history of rating changes and price-target changes (249,645 records, 963 symbols, 2012 on) is used instead |
| E3 combined ranking | This plan |
| E4 forward run | After E3, and only if it passes |

## The test

Definitions are in `combined-signal-evidence/combined_study.py` at this commit. In words:

- Every month-end, rank every eligible stock 0 to 1 on each input:
  - **momentum**: 12-month return excluding the last month;
  - **pullback**: the past week's losers rank highest;
  - **earnings surprise**: the latest in the past 90 days; none counts as average;
  - **analyst revisions**: net upgrades and target raises over 90 days, used in the secondary test only.
- **Score** = the average of the momentum, pullback and surprise ranks. Equal weights, nothing
  fitted.
- **Portfolio**: the top 20, equal weight, bought at the next open and held for a month.
- **Measure**: monthly return minus the equal-weight average of all eligible stocks, after
  0.05% per side on what is traded.
- **Data**: the 993-stock Yahoo daily set from 2005 (portfolios from 2006, once 12 months of
  history exist), Yahoo earnings and analyst records as fetched 2026-11-10.

## Primary test: the 3-input score, 2006-01 to 2026-10

It passes only if all three hold:

1. The 95% interval of its mean monthly excess return is above zero (the wider of an ordinary
   bootstrap of months and a 6-month block bootstrap).
2. Its mean beats the top-20 portfolio built on each input alone (momentum, pullback, surprise).
   If not, combining added nothing.
3. Its mean is above zero in each period: 2006–2015, 2016–2020, 2021–2026.

## Secondary (reported, not decisive)

- S1: the 4-input score (adding analyst revisions) minus the 3-input score, 2013 onwards.
- S2: the analyst input alone, 2013 onwards.
- S3: top 50 instead of top 20.
- S4: the score restricted to the platform's own symbols, against their own average.

## What a pass would and would not mean

- **The inputs were chosen after seeing this data**, in the single-signal study. Equal weights
  and no tuning limit the damage, but a pass here is not out-of-sample evidence.
- **The universe holds only survivors.** That flatters momentum and pullback buying, so any
  result is an upper bound. A fail is informative; a pass is not proof.
- **The only clean test is forward (E4):** run the ranking at the 2026-11-30 close, commit the
  20 names before the 2026-12-01 open, and score them against the universe at the end of
  December, then monthly. Three months before any decision.
