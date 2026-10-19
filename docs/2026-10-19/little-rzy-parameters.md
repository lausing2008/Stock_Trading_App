# Little RZY parameters — 2026-10-19

Defaults are engineering assumptions, not calibrated estimates. Periods are bars of the selected timeframe. Price values use the instrument quote currency. Change via validated `LITTLE_RZY_CONFIG` JSON for live research, or `--config` JSON for offline comparisons. A configuration change creates a distinct hashed stream.

| Parameter | Default | Meaning |
|---|---|---|
| `bb_length` | `20` | Completed closes in the Bollinger window |
| `bb_std` | `2.0` | Population standard-deviation multiplier |
| `atr_length` | `14` | Wilder true-range smoothing period |
| `pivot_left` | `7` | Bars preceding a pivot candidate |
| `pivot_right` | `7` | Completed bars required after a candidate before confirmation |
| `near_band_atr` | `0.25` | ATR allowance at the middle band for pullback context at C |
| `touch_atr` | `0.1` | ATR tolerance for a candle touching the frozen structure line |
| `stop_buffer_atr` | `0.25` | ATR added beyond structural or line stop |
| `min_impulse_atr` | `1.5` | Minimum A-to-B impulse divided by ATR at B |
| `retracement_min` | `0.2` | Minimum B-to-C retracement as fraction of A-to-B |
| `retracement_max` | `0.8` | Maximum B-to-C retracement as fraction of A-to-B |
| `min_rr` | `1.5` | Minimum planned and next-open reward/risk |
| `max_armed_bars` | `20` | Completed bars allowed after arming |
| `preferred_sequences` | `(1, 2)` | Allowed known sequence numbers when filtering |
| `exhaustion_min_sequence` | `3` | First observed sequence eligible for exhaustion research warning |
| `exhaustion_lookback` | `5` | Prior bars searched for an outer-band close |
| `partial_fraction` | `0.5` | Fraction exited at primary target in experimental partial variant |
| `runner_multiple` | `2.0` | D multiplier from measured extreme for runner target |
| `sequence_filter` | `True` | Restrict known sequences; never invent initial sequence confidence |
| `bollinger_context` | `True` | Require middle-band pullback context at C |
| `no_chase_filter` | `True` | Reject continuation closes beyond the directional outer band |
| `partial_runner` | `False` | Experimental partial/runner exit instead of baseline full primary exit |
| `structural_stop` | `True` | Use protective C/trigger/line extreme rather than line-only stop |
| `commission_per_share` | `0.005` | Commission per share per execution, in quote-currency units |
| `spread_bps` | `2.0` | Full spread; half charged per execution |
| `slippage_bps` | `2.0` | Adverse slippage per execution |
| `min_bar_volume` | `1.0` | Minimum volume of the already completed trigger candle; the entry candle’s final volume is not used |
| `reversal_window` | `20` | Maximum bars from completion/exhaustion context to reversal watch |

Example reproduction (from repository root):

```sh
python scripts/backtest_little_rzy.py docs/2026-10-19/little-rzy-synthetic.csv --symbol SYNTHETIC --timeframe 5m --output /tmp/little-rzy-report.json
```

The CSV is explicitly synthetic regular-session data with invented execution/borrow evidence. The checked-in report is a plumbing fixture, not a market-performance result.
