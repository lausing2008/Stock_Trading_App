# Filling the delisted-company gap with Unusual Whales — plan (2026-11-10)

Written for whoever runs or reviews the test. Follows `combined-signal-results.md`, which found
that every signal's apparent edge disappeared once the stock list stopped using hindsight, but
could not say what the missing failed companies would do.

## Data

- **The gap:** former S&P 500 members with no Yahoo prices. 274 of them were members at some
  point from 2012 on (`uw-delisted-evidence/pull-list.json`, with each one's date window).
- **Prices:** Unusual Whales daily bars, one request per year of history, pulled 2026-11-10 from
  07:22 UTC (`uw_pull.py`: one request at a time, stops by itself at 12:30 UTC).
- **Earnings:** Unusual Whales earnings history (estimate, actual, before-open or after-close)
  for all 1,267 symbols.

## Validity condition (decided before the data is read)

The test counts only if the point-in-time S&P 500 is covered: **mean monthly coverage of at
least 95% and no month below 90%**, from 2012-01. Otherwise the result is reported as incomplete.

## Questions and pass marks

All use the rules of `combined-signal-plan.md` (top 20, monthly, next-open entry, 0.05% per side,
excess over the equal-weight average of eligible members), on the S&P 500 as it stood each
month, 2012-01 to 2026-10, with Unusual Whales earnings as the surprise input.

| ID | Question | Passes when |
|---|---|---|
| **U1 (primary)** | Does the combined 3-input score work once failed companies are included? | Interval above zero, beats each input alone, and positive in 2012–2016, 2017–2021 and 2022–2026 |
| U2 | Does momentum alone work? | Interval above zero |
| U3 | After a beat above 10%, does the stock beat the market over 5 days from the first open after the announcement (correct times)? | Interval above zero |
| U4 | How much do the filled companies change each result? | Reported: the same run with Yahoo-only prices |

## Known weaknesses, stated in advance

- Unusual Whales prices are not dividend-adjusted, and splits are detected from overnight jumps
  (rules in `uw_study.py`); a missed split would appear as a crash or a spike.
- A ticker reused by a later company returns nothing (seen for the old Dell), so some former
  members may stay missing. Coverage is measured and reported.
- Earnings timestamps are an assumed 08:00 ET (before the open) or 16:30 ET (after the close).
