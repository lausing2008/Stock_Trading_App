/**
 * Client-side technical indicators — SMA/EMA/RSI/MACD/Bollinger Bands computed directly
 * from OHLCV bars, matching shared/common/indicators.py's exact formulas so results agree
 * with the backend's daily-bar indicator series.
 *
 * Built for intraday timeframes (5m/15m/1h/4h): the technical-analysis service only computes
 * indicator series for daily bars (Overview['indicators']), so intraday chart requests never
 * carry SMA/EMA/RSI/MACD/BB data at all — these overlays/panels silently disappeared for any
 * non-daily timeframe. Rather than add backend computation for every intraday timeframe (a
 * bigger change touching another service), this mirrors the same local-computation approach
 * PriceChart.tsx already uses for VWAP and EMA 200 (computeVwap/computeEma200Map).
 */

const NA = null;

/** Simple moving average — matches pandas' close.rolling(window, min_periods=window).mean(). */
export function computeSMA(closes: number[], window: number): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(NA);
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i];
    if (i >= window) sum -= closes[i - window];
    if (i >= window - 1) out[i] = sum / window;
  }
  return out;
}

/**
 * Exponential moving average — matches pandas' close.ewm(span=window, adjust=False,
 * min_periods=window).mean(). Verified directly against a real pandas run: adjust=False
 * seeds the recursion at the FIRST value unconditionally (y[0] = x[0]), NOT an SMA of the
 * first `window` values — min_periods=window only masks the output as null/NaN until that
 * many inputs have been seen, it does not change the underlying recursive seed. (An earlier
 * version of this function incorrectly seeded with an SMA — caught by cross-checking against
 * a real `pandas.Series.ewm(...)` call before trusting the hand-translated formula.)
 */
export function computeEMA(closes: number[], window: number): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(NA);
  if (closes.length === 0) return out;
  const alpha = 2 / (window + 1);
  let ema = closes[0];
  if (window - 1 === 0) out[0] = ema; // min_periods already satisfied at index 0
  for (let i = 1; i < closes.length; i++) {
    ema = closes[i] * alpha + ema * (1 - alpha);
    if (i >= window - 1) out[i] = ema;
  }
  return out;
}

/**
 * Wilder's RSI — matches indicators.py's rsi(): delta = close.diff() (index 0 is undefined,
 * matching pandas), then Wilder smoothing (ewm alpha=1/window, adjust=False, min_periods=
 * window) of gains/losses. Cross-checked directly against a real pandas run before trusting
 * the hand-translated formula (see computeEMA's docstring for why that check mattered): a
 * leading NaN/undefined delta is NOT included in the ewm seed — pandas' adjust=False
 * recursion seeds at the first REAL value (deltas[1], i.e. gains[1]/losses[1] here, since
 * gains[0]/losses[0] correspond to the undefined deltas[0]), not an SMA of a fixed window.
 * min_periods=window then masks the output null until `window` real (post-seed) values have
 * been folded in — i.e. output first becomes non-null at absolute index `window`, since the
 * seed itself (at index 1) counts as the first of those.
 */
export function computeRSI(closes: number[], window = 14): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(NA);
  if (closes.length < window + 1) return out;

  const alpha = 1 / window;
  let avgGain = Math.max(closes[1] - closes[0], 0); // seed at first real delta (index 1)
  let avgLoss = Math.max(-(closes[1] - closes[0]), 0);

  const rsiAt = (ag: number, al: number): number => {
    if (al === 0) return 100.0;
    const rs = ag / al;
    return 100 - 100 / (1 + rs);
  };
  // window real values folded in by absolute index `window` (seed at 1, then window-1 more
  // recursive steps land at index 1 + (window - 1) = window) — matches the real pandas
  // output verified directly (first non-null RSI at index `window`, e.g. index 14 for the
  // default 14-period window).
  if (window === 1) out[1] = rsiAt(avgGain, avgLoss);
  for (let i = 2; i < closes.length; i++) {
    const delta = closes[i] - closes[i - 1];
    const gain = Math.max(delta, 0);
    const loss = Math.max(-delta, 0);
    avgGain = gain * alpha + avgGain * (1 - alpha);
    avgLoss = loss * alpha + avgLoss * (1 - alpha);
    if (i >= window) out[i] = rsiAt(avgGain, avgLoss);
  }
  return out;
}

export type MACDResult = { macd: (number | null)[]; signal: (number | null)[]; hist: (number | null)[] };

/** MACD — matches indicators.py's macd(): EMA(fast) - EMA(slow), then EMA(signal) of that. */
export function computeMACD(closes: number[], fast = 12, slow = 26, signal = 9): MACDResult {
  const emaFast = computeEMA(closes, fast);
  const emaSlow = computeEMA(closes, slow);
  const macdLine: (number | null)[] = closes.map((_, i) =>
    emaFast[i] != null && emaSlow[i] != null ? (emaFast[i] as number) - (emaSlow[i] as number) : null
  );
  // computeEMA expects a dense number[] with no gaps — feed it only the non-null tail of
  // macdLine, then re-align back to the original index space for the signal/hist output.
  const firstValid = macdLine.findIndex(v => v !== null);
  if (firstValid === -1) return { macd: macdLine, signal: new Array(closes.length).fill(NA), hist: new Array(closes.length).fill(NA) };
  const macdValues = macdLine.slice(firstValid) as number[];
  const signalTail = computeEMA(macdValues, signal);
  const signalLine: (number | null)[] = new Array(closes.length).fill(NA);
  const hist: (number | null)[] = new Array(closes.length).fill(NA);
  for (let i = 0; i < signalTail.length; i++) {
    const idx = firstValid + i;
    signalLine[idx] = signalTail[i];
    if (signalTail[i] != null) hist[idx] = (macdLine[idx] as number) - (signalTail[i] as number);
  }
  return { macd: macdLine, signal: signalLine, hist };
}

export type BollingerResult = { upper: (number | null)[]; lower: (number | null)[]; mid: (number | null)[] };

/** Bollinger Bands — matches indicators.py's bollinger_bands(): SMA mid, ±nStd sample std dev. */
export function computeBollingerBands(closes: number[], window = 20, nStd = 2.0): BollingerResult {
  const mid = computeSMA(closes, window);
  const upper: (number | null)[] = new Array(closes.length).fill(NA);
  const lower: (number | null)[] = new Array(closes.length).fill(NA);
  for (let i = window - 1; i < closes.length; i++) {
    const slice = closes.slice(i - window + 1, i + 1);
    const mean = mid[i] as number;
    // Sample standard deviation (ddof=1), matching pandas' default .std().
    const variance = slice.reduce((s, v) => s + (v - mean) ** 2, 0) / (window - 1);
    const std = Math.sqrt(variance);
    upper[i] = mean + nStd * std;
    lower[i] = mean - nStd * std;
  }
  return { upper, lower, mid };
}
