# Little RZY evaluation — RZY_EVALUATION_V2

Snapshot exported 2026-11-08T22:25:20.500794+00:00; strategy LITTLE_RZY_INSPIRED_V1 (config 8ceb8cf780c3eb981983). Unseen period: signals after 2026-10-20T14:04:54+00:00.

## Predeclared questions

| ID | Role | Question | Trades | Result | 95% CI | Verdict |
|---|---|---|---|---|---|---|
| H3 | primary | Do intraday trades aligned with the daily trend have a larger edge than intraday trades that are not? | 212 vs 474 | +0.095 vs -0.067 (diff +0.163R) | +0.026 to +0.312 | first group better |
| H1 | secondary | Is the baseline edge above zero on all data? | 744 | edge -0.017R | -0.080 to +0.047 | no measurable edge |
| H2 | secondary | Is the baseline edge above zero on sessions no earlier evaluation saw? | 125 | edge -0.189R | -0.276 to -0.098 | edge below zero |
| H4 | secondary | Same as H3 using the next higher timeframe (5m→15m, 15m→1h, 1h→daily). | 255 vs 385 | -0.002 vs -0.001 (diff -0.001R) | -0.144 to +0.147 | no measurable difference |
| H5 | secondary | Do trades whose other three timeframes all agree beat the rest? | 24 vs 560 | +0.310 vs -0.017 (diff +0.327R) | -0.268 to +1.031 | insufficient sample |

## Baseline (live rules), all data

744 trades, win rate 22.0%, mean -0.133R, edge vs random timing -0.017R (95% CI by symbol -0.081 to +0.047): **no measurable edge vs control**.
Average best move during a trade (MFE) +0.482R; average worst move (MAE) +0.393R; 12.8% of trades reached +1R at some point; 3.0% exited at the target.

### By timeframe

| Group | Trades | Win rate | Mean R | Edge R | Edge 95% CI | Verdict |
|---|---|---|---|---|---|---|
| 15m | 189 | 24.3% | -0.071 | -0.005 | -0.169 to +0.170 | no measurable edge vs control |
| 1d | 53 | 34.0% | +0.013 | -0.030 | -0.308 to +0.328 | no measurable edge vs control |
| 1h | 29 | 24.1% | -0.106 | -0.025 | -0.284 to +0.248 | insufficient sample (<30 trades) |
| 5m | 473 | 19.7% | -0.175 | -0.019 | -0.090 to +0.051 | no measurable edge vs control |

### Seen vs unseen

| Group | Trades | Win rate | Mean R | Edge R | Edge 95% CI | Verdict |
|---|---|---|---|---|---|---|
| seen_by_v1 | 619 | 24.6% | -0.103 | +0.018 | -0.059 to +0.100 | no measurable edge vs control |
| unseen_after_2026-10-20 | 125 | 9.6% | -0.279 | -0.189 | -0.279 to -0.102 | worse than control |

### Daily-trend alignment

| Group | Trades | Win rate | Mean R | Edge R | Edge 95% CI | Verdict |
|---|---|---|---|---|---|---|
| aligned | 212 | 23.6% | -0.038 | +0.095 | -0.030 to +0.236 | no measurable edge vs control |
| neutral | 272 | 19.9% | -0.177 | -0.074 | -0.166 to +0.009 | no measurable edge vs control |
| not_applicable | 53 | 34.0% | +0.013 | -0.030 | -0.316 to +0.321 | no measurable edge vs control |
| opposed | 202 | 20.3% | -0.212 | -0.059 | -0.166 to +0.041 | no measurable edge vs control |
| unknown | 5 | 20.0% | -0.102 | +0.167 | -0.338 to +0.293 | insufficient sample (<30 trades) |

### Next-higher-timeframe alignment

| Group | Trades | Win rate | Mean R | Edge R | Edge 95% CI | Verdict |
|---|---|---|---|---|---|---|
| aligned | 255 | 20.0% | -0.118 | -0.002 | -0.113 to +0.125 | no measurable edge vs control |
| neutral | 222 | 20.7% | -0.182 | -0.051 | -0.134 to +0.040 | no measurable edge vs control |
| not_applicable | 53 | 34.0% | +0.013 | -0.030 | -0.303 to +0.349 | no measurable edge vs control |
| opposed | 163 | 23.3% | -0.095 | +0.067 | -0.067 to +0.213 | no measurable edge vs control |
| unknown | 51 | 21.6% | -0.264 | -0.197 | -0.430 to +0.068 | no measurable edge vs control |

### Agreement across timeframes

| Group | Trades | Win rate | Mean R | Edge R | Edge 95% CI | Verdict |
|---|---|---|---|---|---|---|
| all_aligned | 24 | 12.5% | +0.061 | +0.310 | -0.248 to +0.958 | insufficient sample (<30 trades) |
| incomplete | 160 | 25.0% | -0.134 | -0.064 | -0.203 to +0.099 | no measurable edge vs control |
| majority_aligned | 135 | 26.7% | +0.001 | +0.110 | -0.044 to +0.285 | no measurable edge vs control |
| minority_aligned | 425 | 20.0% | -0.186 | -0.058 | -0.131 to +0.016 | no measurable edge vs control |

### Market regime (SPY vs 50-day average)

| Group | Trades | Win rate | Mean R | Edge R | Edge 95% CI | Verdict |
|---|---|---|---|---|---|---|
| unknown | 744 | 22.0% | -0.133 | -0.017 | -0.081 to +0.049 | no measurable edge vs control |

### Time of day (New York)

| Group | Trades | Win rate | Mean R | Edge R | Edge 95% CI | Verdict |
|---|---|---|---|---|---|---|
| 09:30-10:30 | 28 | 28.6% | +0.167 | +0.257 | -0.215 to +0.798 | insufficient sample (<30 trades) |
| 10:30-12:00 | 152 | 25.7% | -0.149 | +0.023 | -0.042 to +0.100 | no measurable edge vs control |
| 12:00-14:00 | 290 | 17.6% | -0.184 | -0.068 | -0.135 to +0.009 | no measurable edge vs control |
| 14:00-15:00 | 126 | 18.3% | -0.151 | -0.015 | -0.158 to +0.166 | no measurable edge vs control |
| 15:00-16:00 | 95 | 26.3% | -0.096 | +0.001 | -0.286 to +0.318 | no measurable edge vs control |
| not_applicable | 53 | 34.0% | +0.013 | -0.030 | -0.313 to +0.328 | no measurable edge vs control |

### Volatility third

| Group | Trades | Win rate | Mean R | Edge R | Edge 95% CI | Verdict |
|---|---|---|---|---|---|---|
| high | 249 | 23.7% | -0.129 | -0.030 | -0.123 to +0.068 | no measurable edge vs control |
| low | 246 | 21.1% | -0.154 | +0.026 | -0.100 to +0.165 | no measurable edge vs control |
| mid | 249 | 21.3% | -0.115 | -0.045 | -0.134 to +0.057 | no measurable edge vs control |

### Direction

| Group | Trades | Win rate | Mean R | Edge R | Edge 95% CI | Verdict |
|---|---|---|---|---|---|---|
| long | 361 | 21.9% | -0.140 | +0.002 | -0.086 to +0.094 | no measurable edge vs control |
| short | 383 | 22.2% | -0.126 | -0.035 | -0.127 to +0.066 | no measurable edge vs control |

## Rule variants

| Variant | What changes | Trades | Mean R | Edge R | Edge 95% CI | Verdict |
|---|---|---|---|---|---|---|
| baseline | The live rules. | 744 | -0.133 | -0.017 | -0.081 to +0.047 | no measurable edge vs control |
| no_bollinger_context | Skips the Bollinger-band pullback check. | 767 | -0.134 | -0.038 | -0.104 to +0.027 | no measurable edge vs control |
| no_chase_filter_off | Allows entries that close outside the band (no-chase filter off). | 773 | -0.130 | -0.037 | -0.103 to +0.031 | no measurable edge vs control |
| unrestricted_sequence | Allows every pullback in a trend, not only the 1st and 2nd. | 793 | -0.111 | -0.025 | -0.095 to +0.044 | no measurable edge vs control |
| partial_runner | Takes half at the target and runs the rest to a 2x target. | 734 | -0.138 | -0.034 | -0.102 to +0.032 | no measurable edge vs control |
| line_stop | Puts the stop at the trendline instead of below the structure. | 990 | -0.381 | -0.128 | -0.242 to -0.011 | worse than control |
| research_stop_target_only | Exits only at the stop or target, never on a close through the line. | 709 | -0.284 | -0.178 | -0.315 to -0.037 | worse than control |

## Many-comparisons scorecard

49 slice cells with at least 30 trades were tested. 0 looked better than control and 4 worse; about 1.2 of each would be expected by chance alone.

## Assumptions

- execution_eligible: assumed True for research (stored prices carry no verified provider delay)
- shortable: assumed True for research; long-only results reported separately
- borrow_rate_annual: 0.005
- note: Assumptions make paper fills possible; they are not verified execution or borrow data.
