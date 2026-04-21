'use client';
import { useEffect, useRef, useState } from 'react';
import { createChart, CandlestickData, IChartApi, LineData, Time, LineStyle, LogicalRange } from 'lightweight-charts';
import type { Price, Overview, Levels } from '@/lib/api';

type Props = { prices: Price[]; indicators?: Overview['indicators']; levels?: Levels };

const CHART_THEME = {
  layout: { background: { color: '#0b1020' }, textColor: '#94a3b8' },
  grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
  rightPriceScale: { borderColor: '#1e293b' },
  timeScale: { borderColor: '#1e293b', timeVisible: true, secondsVisible: false },
};

function toTime(ts: string) { return ts.slice(0, 10) as Time; }
function toLine(ts: string[], vals: (number | null)[]): LineData<Time>[] {
  return ts
    .map((t, i) => ({ time: toTime(t), value: vals[i] }))
    .filter((d): d is LineData<Time> => d.value != null);
}

export default function PriceChart({ prices, indicators, levels }: Props) {
  const mainRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const macdRef = useRef<HTMLDivElement>(null);

  const [showBB, setShowBB] = useState(false);
  const [showVol, setShowVol] = useState(true);
  const [showRSI, setShowRSI] = useState(false);
  const [showMACD, setShowMACD] = useState(false);

  useEffect(() => {
    if (!mainRef.current || prices.length === 0) return;

    // ── Main chart ─────────────────────────────────────────────────────────
    const chart = createChart(mainRef.current, {
      ...CHART_THEME,
      width: mainRef.current.clientWidth,
      height: 380,
    });

    const candles = chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderVisible: false, wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    });
    candles.setData(prices.map<CandlestickData<Time>>(p => ({
      time: toTime(p.ts), open: p.open, high: p.high, low: p.low, close: p.close,
    })));

    // Volume histogram
    if (showVol) {
      const vol = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'vol',
      });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(prices.map(p => ({
        time: toTime(p.ts),
        value: p.volume,
        color: p.close >= p.open ? '#22c55e33' : '#ef444433',
      })));
    }

    // SMA lines
    if (indicators) {
      const smaStyle = {
        sma_20:  { color: '#38bdf8', lineWidth: 1 as const },
        sma_50:  { color: '#f59e0b', lineWidth: 1 as const },
        sma_200: { color: '#a78bfa', lineWidth: 1 as const },
      };
      for (const [key, style] of Object.entries(smaStyle)) {
        const vals = indicators.values[key];
        if (!vals) continue;
        chart.addLineSeries(style).setData(toLine(indicators.ts, vals));
      }

      // Bollinger Bands
      if (showBB) {
        for (const key of ['bb_upper', 'bb_lower', 'bb_mid']) {
          const vals = indicators.values[key];
          if (!vals) continue;
          chart.addLineSeries({
            color: '#6366f188',
            lineWidth: 1 as const,
            lineStyle: key === 'bb_mid' ? LineStyle.Dashed : LineStyle.Solid,
          }).setData(toLine(indicators.ts, vals));
        }
      }
    }

    // Support / Resistance levels
    if (levels?.support_resistance) {
      for (const lvl of levels.support_resistance.slice(0, 8)) {
        candles.createPriceLine({
          price: lvl.price,
          color: lvl.kind === 'support' ? '#22c55e99' : '#ef444499',
          lineWidth: 1 as const,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: `${lvl.kind === 'support' ? 'S' : 'R'}(${lvl.strength})`,
        });
      }
    }

    // ── RSI chart ──────────────────────────────────────────────────────────
    let rsiChart: IChartApi | null = null;
    if (showRSI && rsiRef.current && indicators) {
      rsiChart = createChart(rsiRef.current, {
        ...CHART_THEME,
        width: rsiRef.current.clientWidth,
        height: 110,
        timeScale: { ...CHART_THEME.timeScale, visible: false },
      });
      const rsiLine = rsiChart.addLineSeries({ color: '#f59e0b', lineWidth: 1 });
      const rsiVals = indicators.values['rsi_14'];
      if (rsiVals) {
        rsiLine.setData(toLine(indicators.ts, rsiVals));
        rsiLine.createPriceLine({ price: 70, color: '#ef444466', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70' });
        rsiLine.createPriceLine({ price: 30, color: '#22c55e66', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30' });
      }
    }

    // ── MACD chart ─────────────────────────────────────────────────────────
    let macdChart: IChartApi | null = null;
    if (showMACD && macdRef.current && indicators) {
      macdChart = createChart(macdRef.current, {
        ...CHART_THEME,
        width: macdRef.current.clientWidth,
        height: 110,
        timeScale: { ...CHART_THEME.timeScale, visible: !showRSI },
      });
      const histVals = indicators.values['macd_hist'];
      if (histVals) {
        const histSeries = macdChart.addHistogramSeries({ priceScaleId: 'right' });
        histSeries.setData(
          indicators.ts
            .map((t, i) => ({ time: toTime(t), value: histVals[i], color: (histVals[i] ?? 0) >= 0 ? '#22c55e' : '#ef4444' }))
            .filter(d => d.value != null) as { time: Time; value: number; color: string }[]
        );
      }
      const macdVals = indicators.values['macd'];
      if (macdVals) macdChart.addLineSeries({ color: '#38bdf8', lineWidth: 1 }).setData(toLine(indicators.ts, macdVals));
      const sigVals = indicators.values['macd_signal'];
      if (sigVals) macdChart.addLineSeries({ color: '#f59e0b', lineWidth: 1 }).setData(toLine(indicators.ts, sigVals));
    }

    // Sync all time scales to main chart
    chart.timeScale().subscribeVisibleLogicalRangeChange((range: LogicalRange | null) => {
      if (!range) return;
      rsiChart?.timeScale().setVisibleLogicalRange(range);
      macdChart?.timeScale().setVisibleLogicalRange(range);
    });

    const onResize = () => {
      chart.applyOptions({ width: mainRef.current!.clientWidth });
      rsiChart?.applyOptions({ width: rsiRef.current!.clientWidth });
      macdChart?.applyOptions({ width: macdRef.current!.clientWidth });
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.remove();
      rsiChart?.remove();
      macdChart?.remove();
    };
  }, [prices, indicators, levels, showBB, showVol, showRSI, showMACD]);

  const btn = (active: boolean, label: string, onClick: () => void) => (
    <button
      onClick={onClick}
      className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${active ? 'bg-indigo-600 text-white' : 'border border-slate-700 text-slate-400 hover:border-indigo-500 hover:text-indigo-300'}`}
    >
      {label}
    </button>
  );

  return (
    <div className="rounded-md border border-slate-800 bg-[#0b1020] overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-800">
        <span className="text-xs text-slate-500">Overlays:</span>
        {btn(showBB, 'BB', () => setShowBB((v: boolean) => !v))}
        {btn(showVol, 'Volume', () => setShowVol((v: boolean) => !v))}
        <span className="text-xs text-slate-500 ml-2">Panels:</span>
        {btn(showRSI, 'RSI', () => setShowRSI((v: boolean) => !v))}
        {btn(showMACD, 'MACD', () => setShowMACD((v: boolean) => !v))}
        <span className="ml-auto text-xs text-slate-600">
          <span className="inline-block w-3 h-0.5 bg-sky-400 mr-1 align-middle" />SMA20
          <span className="inline-block w-3 h-0.5 bg-amber-400 mx-1 ml-2 align-middle" />SMA50
          <span className="inline-block w-3 h-0.5 bg-violet-400 mx-1 ml-2 align-middle" />SMA200
        </span>
      </div>
      <div ref={mainRef} className="w-full" />
      {showRSI && (
        <div className="border-t border-slate-800">
          <div className="px-3 pt-1 text-xs text-slate-500">RSI (14)</div>
          <div ref={rsiRef} className="w-full" />
        </div>
      )}
      {showMACD && (
        <div className="border-t border-slate-800">
          <div className="px-3 pt-1 text-xs text-slate-500">MACD (12/26/9)</div>
          <div ref={macdRef} className="w-full" />
        </div>
      )}
    </div>
  );
}
