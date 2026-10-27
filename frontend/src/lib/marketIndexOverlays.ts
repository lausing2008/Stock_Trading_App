/**
 * SMA/EMA overlays for the /market/[ticker] index chart.
 *
 * The endpoint returns warm-up bars before the requested range (`visible_from`), so an average
 * is computed over every bar and only the visible part is drawn — SMA 200 on a 6M daily chart
 * starts at the left edge instead of 200 bars in. Same periods and colours as PriceChart.
 */
import { computeEMA, computeSMA } from '@/lib/indicators';

export type OverlayKey = 'SMA5' | 'SMA10' | 'SMA20' | 'SMA50' | 'SMA200' | 'EMA20' | 'EMA50' | 'EMA200';

export const OVERLAYS: { key: OverlayKey; label: string; kind: 'sma' | 'ema'; period: number; color: string }[] = [
  { key: 'SMA5',   label: 'SMA 5',   kind: 'sma', period: 5,   color: '#facc15' },
  { key: 'SMA10',  label: 'SMA 10',  kind: 'sma', period: 10,  color: '#fb7185' },
  { key: 'SMA20',  label: 'SMA 20',  kind: 'sma', period: 20,  color: '#38bdf8' },
  { key: 'SMA50',  label: 'SMA 50',  kind: 'sma', period: 50,  color: '#f59e0b' },
  { key: 'SMA200', label: 'SMA 200', kind: 'sma', period: 200, color: '#a78bfa' },
  { key: 'EMA20',  label: 'EMA 20',  kind: 'ema', period: 20,  color: '#34d399' },
  { key: 'EMA50',  label: 'EMA 50',  kind: 'ema', period: 50,  color: '#f472b6' },
  { key: 'EMA200', label: 'EMA 200', kind: 'ema', period: 200, color: '#e879f9' },
];

export const DEFAULT_OVERLAYS: OverlayKey[] = ['SMA20', 'SMA50', 'SMA200'];

export type OverlayLine = {
  key: OverlayKey;
  label: string;
  color: string;
  points: { t: number; value: number }[];
  /** True when the average has no value anywhere in the visible range (too few bars). */
  insufficient: boolean;
};

export function overlayLines(
  bars: { t: number; c: number }[],
  visibleFrom: number,
  selected: OverlayKey[],
): OverlayLine[] {
  const closes = bars.map(b => b.c);
  return OVERLAYS.filter(o => selected.includes(o.key)).map(o => {
    const vals = o.kind === 'sma' ? computeSMA(closes, o.period) : computeEMA(closes, o.period);
    const points: { t: number; value: number }[] = [];
    for (let i = Math.max(0, visibleFrom); i < bars.length; i++) {
      const v = vals[i];
      if (v != null) points.push({ t: bars[i].t, value: v });
    }
    return { key: o.key, label: o.label, color: o.color, points, insufficient: points.length === 0 };
  });
}
