import { describe, expect, it } from 'vitest';
import { intradayWindow } from './intradayHistory';
import { computeEMA } from './indicators';

describe('intraday indicator warmup', () => {
  it('retains prior candles so EMA200 exists throughout a 74-bar session', () => {
    const prior = Array.from({ length: 300 }, (_, i) => ({ ts: `2026-10-21T${i}`, close: 10 }));
    const today = Array.from({ length: 74 }, (_, i) => ({ ts: `2026-10-22T${i}`, close: 20 }));
    const window = intradayWindow([...prior, ...today], '2026-10-22');
    expect(window.visible).toEqual(today);
    const ema = computeEMA(window.history.map(p => p.close), 200);
    expect(ema[300]).toBeCloseTo(10 + 20 / 201, 10);
    expect(ema.slice(300).every(v => v !== null)).toBe(true);
  });
  it('shows recent history before five current-session bars are available', () => {
    const history = Array.from({ length: 250 }, (_, i) => ({ ts: `2026-10-21T${i}` }));
    const window = intradayWindow(history, '2026-10-22');
    expect(window.history).toHaveLength(250);
    expect(window.visible).toEqual(history.slice(-100));
  });
});
