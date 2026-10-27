import { describe, expect, it } from 'vitest';
import { overlayLines } from './marketIndexOverlays';

const bars = Array.from({ length: 300 }, (_, i) => ({ t: 1_000 + i * 60, c: i + 1 }));

describe('overlayLines', () => {
  it('uses warm-up bars so SMA 200 starts at the first visible bar', () => {
    const [sma] = overlayLines(bars, 250, ['SMA200']);
    expect(sma.points).toHaveLength(50);
    expect(sma.points[0].t).toBe(bars[250].t);
    // mean of closes 52..251
    expect(sma.points[0].value).toBeCloseTo(151.5, 6);
  });

  it('flags an average with no value inside the visible range', () => {
    const short = bars.slice(0, 120);
    const [sma20, sma200] = overlayLines(short, 0, ['SMA200', 'SMA20']); // fixed order: SMA 20 first
    expect(sma200.insufficient).toBe(true);
    expect(sma20.insufficient).toBe(false);
    expect(sma20.points[0].t).toBe(short[19].t);
  });

  it('returns only the selected overlays, in the fixed order', () => {
    expect(overlayLines(bars, 0, ['EMA50', 'SMA5']).map(l => l.key)).toEqual(['SMA5', 'EMA50']);
  });
});
