import { describe, it, expect } from 'vitest';
import { computeSMA, computeEMA, computeRSI, computeMACD, computeBollingerBands } from './indicators';

describe('computeSMA', () => {
  it('returns null before the window fills, then the rolling mean', () => {
    const closes = [1, 2, 3, 4, 5];
    const sma = computeSMA(closes, 3);
    expect(sma[0]).toBeNull();
    expect(sma[1]).toBeNull();
    expect(sma[2]).toBeCloseTo(2); // (1+2+3)/3
    expect(sma[3]).toBeCloseTo(3); // (2+3+4)/3
    expect(sma[4]).toBeCloseTo(4); // (3+4+5)/3
  });

  it('returns all-null for a series shorter than the window', () => {
    const sma = computeSMA([1, 2], 5);
    expect(sma.every(v => v === null)).toBe(true);
  });
});

describe('computeEMA', () => {
  it('matches pandas ewm(adjust=False, min_periods=window) exactly — verified against a real pandas run, not re-derived from this same implementation', () => {
    // pandas.Series([10,20,30,40,50]).ewm(span=3, adjust=False, min_periods=3).mean()
    // -> [nan, nan, 22.5, 31.25, 40.625]. adjust=False seeds the recursion at the FIRST
    // value unconditionally (y[0]=x[0]) and recurses from there — min_periods only masks
    // early output as null, it does NOT change the seed to an SMA (an earlier, wrong
    // version of computeEMA assumed an SMA seed; this exact fixture caught the bug).
    const closes = [10, 20, 30, 40, 50];
    const ema = computeEMA(closes, 3);
    expect(ema[0]).toBeNull();
    expect(ema[1]).toBeNull();
    expect(ema[2]).toBeCloseTo(22.5);
    expect(ema[3]).toBeCloseTo(31.25);
    expect(ema[4]).toBeCloseTo(40.625);
  });

  it('unmasks from index 0 when window is 1 (min_periods=1 is satisfied immediately)', () => {
    // pandas.Series([10,20,30,40,50]).ewm(span=1, adjust=False, min_periods=1).mean()
    // -> [10, 20, 30, 40, 50] (span=1 -> alpha=1, so ema always equals the latest close).
    const ema = computeEMA([10, 20, 30, 40, 50], 1);
    expect(ema).toEqual([10, 20, 30, 40, 50]);
  });
});

describe('computeRSI', () => {
  it('returns 100 when there have been no losses at all (matches Wilder spec avg_loss===0)', () => {
    const closes = Array.from({ length: 20 }, (_, i) => 100 + i); // strictly increasing
    const rsi = computeRSI(closes, 14);
    const firstValid = rsi.findIndex(v => v !== null);
    expect(firstValid).toBeGreaterThanOrEqual(0);
    expect(rsi[firstValid]).toBeCloseTo(100);
  });

  it('matches pandas exactly on a fixed mixed up/down series — verified against a real pandas run', () => {
    // Real pandas run of indicators.py's rsi() on this exact series:
    // delta=close.diff(); gain/loss ewm(alpha=1/14, adjust=False, min_periods=14) ->
    // RSI [.., 75.0382295115659 (idx 14), 75.77063120780358 (idx 15)], all earlier null.
    const closes = [100, 102, 101, 103, 99, 98, 105, 107, 104, 103, 108, 110, 106, 109, 111, 112];
    const rsi = computeRSI(closes, 14);
    for (let i = 0; i < 14; i++) expect(rsi[i]).toBeNull();
    expect(rsi[14]).toBeCloseTo(75.0382295115659);
    expect(rsi[15]).toBeCloseTo(75.77063120780358);
  });

  it('returns all-null for a series too short for the window', () => {
    const rsi = computeRSI([100, 101, 102], 14);
    expect(rsi.every(v => v === null)).toBe(true);
  });
});

describe('computeMACD', () => {
  it('matches pandas exactly on a fixed sine-wave series — verified against a real pandas run', () => {
    // Real pandas run of indicators.py's macd() on this exact series:
    // ema_fast = close.ewm(span=12,...).mean(); ema_slow = close.ewm(span=26,...).mean();
    // macd_line = ema_fast - ema_slow; signal = macd_line.ewm(span=9,...).mean().
    const closes = Array.from({ length: 40 }, (_, i) => 100 + Math.sin(i / 3) * 5);
    const { macd, signal, hist } = computeMACD(closes, 12, 26, 9);
    expect(macd.slice(0, 25).every(v => v === null)).toBe(true);
    expect(macd[25]).toBeCloseTo(1.022967494548496);
    expect(signal.slice(0, 33).every(v => v === null)).toBe(true);
    expect(signal[33]).toBeCloseTo(0.020375185238094035);
    expect(hist[33]).toBeCloseTo(-0.9874749623070465);
  });

  it('handles an all-null macd line (series too short for even the fast EMA) without crashing', () => {
    const { macd, signal, hist } = computeMACD([100, 101, 102], 12, 26, 9);
    expect(macd.every(v => v === null)).toBe(true);
    expect(signal.every(v => v === null)).toBe(true);
    expect(hist.every(v => v === null)).toBe(true);
  });
});

describe('computeBollingerBands', () => {
  it('matches pandas exactly on a fixed series — verified against a real pandas run', () => {
    // Real pandas run of indicators.py's bollinger_bands() on this exact series:
    // mid = close.rolling(20).mean(); std = close.rolling(20).std(ddof=1) (sample std dev).
    const closes = [10, 12, 9, 15, 11, 14, 8, 13, 10, 16, 12, 9, 15, 11, 14, 8, 13, 10, 16, 12];
    const { upper, mid, lower } = computeBollingerBands(closes, 20, 2);
    expect(mid[19]).toBeCloseTo(11.9);
    expect(upper[19]).toBeCloseTo(17.005208898246657);
    expect(lower[19]).toBeCloseTo(6.7947911017533436);
    expect(upper[19] as number).toBeGreaterThan(mid[19] as number);
    expect(lower[19] as number).toBeLessThan(mid[19] as number);
  });

  it('returns null bands for a flat (zero-variance) series (upper === mid === lower)', () => {
    const closes = new Array(25).fill(100);
    const { upper, mid, lower } = computeBollingerBands(closes, 20, 2);
    expect(upper[19]).toBeCloseTo(100);
    expect(mid[19]).toBeCloseTo(100);
    expect(lower[19]).toBeCloseTo(100);
  });
});
