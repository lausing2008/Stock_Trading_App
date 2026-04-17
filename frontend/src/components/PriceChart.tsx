'use client';
import { useEffect, useRef } from 'react';
import { createChart, CandlestickData, IChartApi, LineData } from 'lightweight-charts';
import type { Price, Overview } from '@/lib/api';

type Props = { prices: Price[]; indicators?: Overview['indicators'] };

export default function PriceChart({ prices, indicators }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current || prices.length === 0) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 480,
      layout: { background: { color: '#0b1020' }, textColor: '#e6edf3' },
      grid: { vertLines: { color: '#1a2440' }, horzLines: { color: '#1a2440' } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#1a2440' },
      rightPriceScale: { borderColor: '#1a2440' },
    });
    chartRef.current = chart;

    const candles = chart.addCandlestickSeries({ upColor: '#22c55e', downColor: '#ef4444', borderVisible: false, wickUpColor: '#22c55e', wickDownColor: '#ef4444' });
    const data: CandlestickData[] = prices.map((p) => ({
      time: p.ts.slice(0, 10) as never,
      open: p.open,
      high: p.high,
      low: p.low,
      close: p.close,
    }));
    candles.setData(data);

    if (indicators) {
      const palette: Record<string, string> = { sma_20: '#38bdf8', sma_50: '#f59e0b', sma_200: '#a78bfa' };
      for (const key of ['sma_20', 'sma_50', 'sma_200']) {
        const vals = indicators.values[key];
        if (!vals) continue;
        const series = chart.addLineSeries({ color: palette[key], lineWidth: 2 });
        const ld: LineData[] = indicators.ts
          .map((t, i) => ({ time: t.slice(0, 10) as never, value: vals[i] }))
          .filter((d): d is LineData => d.value != null) as LineData[];
        series.setData(ld);
      }
    }

    const onResize = () => chart.applyOptions({ width: containerRef.current!.clientWidth });
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.remove();
    };
  }, [prices, indicators]);

  return <div ref={containerRef} className="w-full rounded-md border border-slate-800 bg-[#0b1020]" />;
}
