/**
 * PriceChart — interactive candlestick chart powered by lightweight-charts v4.
 *
 * Props
 * ─────
 * prices      OHLCV array from the market-data service (up to 1 260 bars).
 *             Bars arrive as YYYY-MM-DD strings; toTime() truncates any ISO
 *             time component so they work as Business Day keys.
 * indicators  Optional TA overlay values (SMA 20/50/200, BB, RSI, MACD) from
 *             the technical-analysis service.  Timestamps use ISO-8601 format;
 *             toLine() strips them to YYYY-MM-DD for series alignment.
 * levels      Optional support/resistance levels from the TA service.  Backend
 *             labels (kind = 'support'|'resistance') are set at detection time
 *             from pivot lows/highs and go stale as price moves; the chart
 *             re-classifies every level at render time based on the last close.
 *
 * Range selector
 * ──────────────
 * Slices prices[] to the last N bars (1D=1, 5D=5, 1M=21, 3M=63, 6M=126,
 * 1Y=252, 5Y=1 260, All=full array).  Both visiblePrices and visibleIndicators
 * are wrapped in useMemo — without it, .slice() and the inline indicator filter
 * produce new object references on every parent re-render (SWR polls every
 * 30–60 s), which caused the useEffect to destroy/recreate the chart 26+ times
 * per page load, clearing the canvas before the browser could paint.
 *
 * Right-axis price labels (colored)
 * ──────────────────────────────────
 * lightweight-charts pins a colored label to the right price axis for each
 * series at its current value:
 *   • Last close     green  (#22c55e) — matches up-candle body color
 *   • SMA 20         cyan   (#38bdf8)
 *   • SMA 50         amber  (#f59e0b)
 *   • SMA 200        violet (#a78bfa)
 *   • Support lines  green  (dotted, no axis label)
 *   • Resistance     red    (dotted, no axis label)
 *
 * S/R overlay badges
 * ──────────────────
 * Absolutely-positioned HTML <div> elements (not canvas) pinned to the left
 * edge of the chart.  Positions are recomputed via candles.priceToCoordinate()
 * on every visible range change and on container resize (ResizeObserver).
 * Format: "R(2) 74.80" where the number in parentheses is the touch count.
 *
 * Sub-panels (RSI, MACD)
 * ──────────────────────
 * Separate lightweight-charts instances mounted in rsiRef / macdRef.  Their
 * time scales are locked to the main chart via subscribeVisibleLogicalRangeChange
 * so panning/zooming stays in sync.  All three charts use autoSize:true so
 * the canvas fills the container even when the component mounts before the
 * grid layout is finalized.
 */
'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createChart, CandlestickData, IChartApi, LineData, Time, LineStyle, LogicalRange } from 'lightweight-charts';
import type { Price, Overview, Levels } from '@/lib/api';

type Props = { prices: Price[]; indicators?: Overview['indicators']; levels?: Levels };

const RANGES = [
  { label: '1D',  days: 1    },
  { label: '5D',  days: 5    },
  { label: '1M',  days: 21   },
  { label: '3M',  days: 63   },
  { label: '6M',  days: 126  },
  { label: '1Y',  days: 252  },
  { label: '5Y',  days: 1260 },
  { label: 'All', days: null },
] as const;
type RangeLabel = typeof RANGES[number]['label'];

const CHART_THEME = {
  layout: { background: { color: '#0b1020' }, textColor: '#94a3b8' },
  grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
  rightPriceScale: { borderColor: '#1e293b' },
  timeScale: { borderColor: '#1e293b', timeVisible: false, secondsVisible: false },
};

function toTime(ts: string) { return ts.slice(0, 10) as Time; }
function toLine(ts: string[], vals: (number | null)[]): LineData<Time>[] {
  return ts
    .map((t, i) => ({ time: toTime(t), value: vals[i] }))
    .filter((d): d is LineData<Time> => d.value != null);
}

type SmaVals  = { sma_20: number|null; sma_50: number|null; sma_200: number|null };
type MacdVals = { macd: number|null; signal: number|null; hist: number|null };

export default function PriceChart({ prices, indicators, levels }: Props) {
  const mainRef = useRef<HTMLDivElement>(null);
  const rsiRef  = useRef<HTMLDivElement>(null);
  const macdRef = useRef<HTMLDivElement>(null);

  const [range, setRange] = useState<RangeLabel>('3M');
  const [showBB,   setShowBB]   = useState(false);
  const [showVol,  setShowVol]  = useState(true);
  const [showRSI,  setShowRSI]  = useState(false);
  const [showMACD, setShowMACD] = useState(false);

  const rangeConfig = RANGES.find(r => r.label === range)!;

  // Memoize so the useEffect only re-runs when the actual data changes,
  // not on every parent re-render triggered by SWR polls. Without useMemo,
  // .slice() and the inline object literal produce new references every render,
  // causing the chart to be destroyed/recreated on each SWR poll (26+ times),
  // which clears the canvas before the browser can paint the candles.
  const visiblePrices = useMemo(
    () => rangeConfig.days == null ? prices : prices.slice(-rangeConfig.days),
    [prices, rangeConfig.days],
  );

  const visibleIndicators = useMemo((): typeof indicators => {
    if (!indicators) return indicators;
    const cutoffTs = visiblePrices.length > 0 ? visiblePrices[0].ts : null;
    if (!cutoffTs) return indicators;
    const startIdx = indicators.ts.findIndex(t => t >= cutoffTs);
    if (startIdx < 0) return indicators;
    return {
      ts: indicators.ts.slice(startIdx),
      values: Object.fromEntries(
        Object.entries(indicators.values).map(([k, v]) => [k, v.slice(startIdx)])
      ),
    };
  }, [indicators, visiblePrices]);

  const [smaVals,  setSmaVals]  = useState<SmaVals>({ sma_20: null, sma_50: null, sma_200: null });
  const [rsiVal,   setRsiVal]   = useState<number|null>(null);
  const [macdCross, setMacdCross] = useState<MacdVals>({ macd: null, signal: null, hist: null });

  // S/R label overlay positions
  type LabelPos = { price: number; y: number; kind: 'support' | 'resistance'; strength: number };
  const [srLabels, setSrLabels] = useState<LabelPos[]>([]);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!mainRef.current || visiblePrices.length === 0) return;

    // ── Main chart ─────────────────────────────────────────────────────────
    const chart = createChart(mainRef.current, {
      ...CHART_THEME,
      autoSize: true,
      height: 380,
    });

    const candles = chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderVisible: false, wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    });
    candles.setData(visiblePrices.map<CandlestickData<Time>>(p => ({
      time: toTime(p.ts),
      open: +p.open, high: +p.high, low: +p.low, close: +p.close,
    })));

    if (showVol) {
      const vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(visiblePrices.map(p => ({
        time: toTime(p.ts), value: p.volume,
        color: p.close >= p.open ? '#22c55e33' : '#ef444433',
      })));
    }

    // SMA lines
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const smaSeries: Record<string, any> = {};
    if (visibleIndicators) {
      const smaStyle = {
        sma_20:  { color: '#38bdf8', lineWidth: 1 as const },
        sma_50:  { color: '#f59e0b', lineWidth: 1 as const },
        sma_200: { color: '#a78bfa', lineWidth: 1 as const },
      };
      for (const [key, style] of Object.entries(smaStyle)) {
        const vals = visibleIndicators.values[key];
        if (!vals) continue;
        const s = chart.addLineSeries(style);
        s.setData(toLine(visibleIndicators.ts, vals));
        smaSeries[key] = s;
      }

      if (showBB) {
        for (const key of ['bb_upper', 'bb_lower', 'bb_mid']) {
          const vals = visibleIndicators.values[key];
          if (!vals) continue;
          chart.addLineSeries({
            color: '#6366f188', lineWidth: 1 as const,
            lineStyle: key === 'bb_mid' ? LineStyle.Dashed : LineStyle.Solid,
          }).setData(toLine(visibleIndicators.ts, vals));
        }
      }

      chart.subscribeCrosshairMove((param) => {
        if (!param.time) { setSmaVals({ sma_20: null, sma_50: null, sma_200: null }); return; }
        setSmaVals({
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          sma_20:  smaSeries['sma_20']  ? (param.seriesData.get(smaSeries['sma_20'])  as any)?.value ?? null : null,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          sma_50:  smaSeries['sma_50']  ? (param.seriesData.get(smaSeries['sma_50'])  as any)?.value ?? null : null,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          sma_200: smaSeries['sma_200'] ? (param.seriesData.get(smaSeries['sma_200']) as any)?.value ?? null : null,
        });
      });
    }

    // Re-classify S/R based on current price — backend kind is set at detection
    // time from pivot highs/lows and doesn't update when price moves later.
    const lastClose = visiblePrices.at(-1)?.close ?? null;
    const srLevels = (levels?.support_resistance?.slice(0, 8) ?? []).map(lvl => ({
      ...lvl,
      kind: lastClose != null
        ? (lvl.price > lastClose ? 'resistance' : 'support') as 'support' | 'resistance'
        : lvl.kind,
    }));
    for (const lvl of srLevels) {
      candles.createPriceLine({
        price: lvl.price,
        color: lvl.kind === 'support' ? '#22c55e66' : '#ef444466',
        lineWidth: 1 as const, lineStyle: LineStyle.Dotted, axisLabelVisible: false,
        title: '',
      });
    }
    chartRef.current = chart;

    function updateSrLabels() {
      if (!mainRef.current) return;
      const labels: LabelPos[] = [];
      for (const lvl of srLevels) {
        const y = candles.priceToCoordinate(lvl.price);
        if (y != null && y > 0 && y < mainRef.current.clientHeight) {
          labels.push({ price: lvl.price, y, kind: lvl.kind, strength: lvl.strength });
        }
      }
      setSrLabels(labels);
    }
    updateSrLabels();
    chart.timeScale().subscribeVisibleTimeRangeChange(updateSrLabels);

    // Fit all data into view. autoSize handles width; fitContent handles the
    // time range so all bars are shown when the chart first mounts.
    chart.timeScale().fitContent();

    // ── RSI chart ──────────────────────────────────────────────────────────
    let rsiChart: IChartApi | null = null;
    if (showRSI && rsiRef.current && visibleIndicators) {
      rsiChart = createChart(rsiRef.current, {
        ...CHART_THEME, autoSize: true, height: 110,
        timeScale: { ...CHART_THEME.timeScale, visible: false },
      });
      const rsiLine = rsiChart.addLineSeries({ color: '#f59e0b', lineWidth: 1 });
      const rsiVals = visibleIndicators.values['rsi_14'];
      if (rsiVals) {
        rsiLine.setData(toLine(visibleIndicators.ts, rsiVals));
        rsiLine.createPriceLine({ price: 70, color: '#ef444466', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70' });
        rsiLine.createPriceLine({ price: 30, color: '#22c55e66', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30' });
      }
      rsiChart.subscribeCrosshairMove((param) => {
        if (!param.time) { setRsiVal(null); return; }
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        setRsiVal((param.seriesData.get(rsiLine) as any)?.value ?? null);
      });
    }

    // ── MACD chart ─────────────────────────────────────────────────────────
    let macdChart: IChartApi | null = null;
    if (showMACD && macdRef.current && visibleIndicators) {
      macdChart = createChart(macdRef.current, {
        ...CHART_THEME, autoSize: true, height: 110,
        timeScale: { ...CHART_THEME.timeScale, visible: !showRSI },
      });

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let histSeries: any = null, macdLine: any = null, signalLine: any = null;

      const histVals = visibleIndicators.values['macd_hist'];
      if (histVals) {
        histSeries = macdChart.addHistogramSeries({ priceScaleId: 'right' });
        histSeries.setData(
          visibleIndicators.ts
            .map((t, i) => ({ time: toTime(t), value: histVals[i], color: (histVals[i] ?? 0) >= 0 ? '#22c55e' : '#ef4444' }))
            .filter(d => d.value != null) as { time: Time; value: number; color: string }[]
        );
      }
      const macdValsData = visibleIndicators.values['macd'];
      if (macdValsData) {
        macdLine = macdChart.addLineSeries({ color: '#38bdf8', lineWidth: 1 });
        macdLine.setData(toLine(visibleIndicators.ts, macdValsData));
      }
      const sigVals = visibleIndicators.values['macd_signal'];
      if (sigVals) {
        signalLine = macdChart.addLineSeries({ color: '#f59e0b', lineWidth: 1 });
        signalLine.setData(toLine(visibleIndicators.ts, sigVals));
      }

      macdChart.subscribeCrosshairMove((param) => {
        if (!param.time) { setMacdCross({ macd: null, signal: null, hist: null }); return; }
        setMacdCross({
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          macd:   macdLine   ? (param.seriesData.get(macdLine)   as any)?.value ?? null : null,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          signal: signalLine ? (param.seriesData.get(signalLine) as any)?.value ?? null : null,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          hist:   histSeries ? (param.seriesData.get(histSeries) as any)?.value ?? null : null,
        });
      });
    }

    chart.timeScale().subscribeVisibleLogicalRangeChange((range: LogicalRange | null) => {
      if (!range) return;
      rsiChart?.timeScale().setVisibleLogicalRange(range);
      macdChart?.timeScale().setVisibleLogicalRange(range);
    });

    // Keep S/R label positions in sync when the chart auto-resizes.
    const ro = new ResizeObserver(() => updateSrLabels());
    if (mainRef.current) ro.observe(mainRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      rsiChart?.remove();
      macdChart?.remove();
      chartRef.current = null;
      setSrLabels([]);
    };
  }, [visiblePrices, visibleIndicators, levels, showBB, showVol, showRSI, showMACD]);

  const btn = (active: boolean, label: string, onClick: () => void, color?: string) => (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium transition-colors ${active ? 'bg-indigo-600 text-white' : 'border border-slate-700 text-slate-400 hover:border-indigo-500 hover:text-indigo-300'}`}
    >
      {color && <span className="inline-block w-3 h-0.5 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />}
      {label}
    </button>
  );

  const f2 = (v: number|null) => v != null ? v.toFixed(2) : '—';
  const f3 = (v: number|null) => v != null ? v.toFixed(3) : '—';

  return (
    <div className="rounded-md border border-slate-800 bg-[#0b1020] overflow-hidden">

      {/* Range selector */}
      <div className="flex items-center gap-1 px-3 pt-2 pb-1">
        {RANGES.map(r => (
          <button
            key={r.label}
            onClick={() => setRange(r.label)}
            className={`px-2.5 py-0.5 rounded text-xs font-semibold transition-colors ${
              range === r.label
                ? 'bg-indigo-600 text-white'
                : 'text-slate-400 hover:text-indigo-300 hover:bg-slate-800'
            }`}
          >
            {r.label}
          </button>
        ))}
        <span className="ml-2 text-xs text-slate-600">
          {visiblePrices.length} bars
        </span>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 border-b border-slate-800">
        <span className="text-xs text-slate-500">Overlays:</span>
        {btn(showBB,   'BB',     () => setShowBB((v: boolean)   => !v), '#6366f1')}
        {btn(showVol,  'Volume', () => setShowVol((v: boolean)  => !v), '#22c55e')}
        <span className="text-xs text-slate-500 ml-2">Panels:</span>
        {btn(showRSI,  'RSI',   () => setShowRSI((v: boolean)  => !v), '#f59e0b')}
        {btn(showMACD, 'MACD',  () => setShowMACD((v: boolean) => !v), '#38bdf8')}

        {/* Static legend */}
        <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-sm bg-green-500 opacity-80" />Up</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-sm bg-red-500 opacity-80" />Down</span>
          {visibleIndicators && <>
            <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-sky-400" />SMA 20</span>
            <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-amber-400" />SMA 50</span>
            <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-violet-400" />SMA 200</span>
          </>}
          {showBB && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-indigo-400 opacity-70" />BB</span>}
          {levels?.support_resistance?.length ? <>
            <span className="flex items-center gap-1"><span className="inline-block w-4 border-t border-dashed border-green-500 opacity-70" />Support</span>
            <span className="flex items-center gap-1"><span className="inline-block w-4 border-t border-dashed border-red-500 opacity-70" />Resist.</span>
          </> : null}
          {showMACD && <>
            <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-sky-400" />MACD</span>
            <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-amber-400" />Signal</span>
          </>}
          {showRSI && <>
            <span className="flex items-center gap-1"><span className="inline-block w-3 border-t border-dashed border-red-400 opacity-70" />70</span>
            <span className="flex items-center gap-1"><span className="inline-block w-3 border-t border-dashed border-green-400 opacity-70" />30</span>
          </>}
        </div>
      </div>

      {/* SMA crosshair readout */}
      {visibleIndicators && (
        <div className="flex items-center gap-4 px-3 py-1 text-xs font-mono bg-[#0b1020] border-b border-slate-800/50 min-h-[22px]">
          {smaVals.sma_20  != null && <span style={{ color: '#38bdf8' }}>─ SMA 20 = {f2(smaVals.sma_20)}</span>}
          {smaVals.sma_50  != null && <span style={{ color: '#f59e0b' }}>─ SMA 50 = {f2(smaVals.sma_50)}</span>}
          {smaVals.sma_200 != null && <span style={{ color: '#a78bfa' }}>─ SMA 200 = {f2(smaVals.sma_200)}</span>}
        </div>
      )}

      <div style={{ position: 'relative' }}>
        <div ref={mainRef} className="w-full" />
        {/* S/R labels pinned to left edge */}
        {srLabels.map((l, i) => {
          const isSupport = l.kind === 'support';
          return (
            <div
              key={i}
              style={{
                position: 'absolute',
                left: 0,
                top: l.y - 9,
                display: 'flex',
                alignItems: 'center',
                gap: '3px',
                pointerEvents: 'none',
                zIndex: 10,
              }}
            >
              <div style={{
                fontSize: '10px',
                fontWeight: 700,
                fontFamily: 'monospace',
                lineHeight: 1,
                padding: '2px 5px',
                borderRadius: '3px',
                background: isSupport ? 'rgba(34,197,94,0.18)' : 'rgba(239,68,68,0.18)',
                border: `1px solid ${isSupport ? 'rgba(34,197,94,0.5)' : 'rgba(239,68,68,0.5)'}`,
                color: isSupport ? '#4ade80' : '#f87171',
                whiteSpace: 'nowrap',
              }}>
                {isSupport ? 'S' : 'R'}{l.strength > 1 ? `(${l.strength})` : ''} {l.price.toFixed(2)}
              </div>
            </div>
          );
        })}
      </div>

      {showRSI && (
        <div className="border-t border-slate-800">
          <div className="flex items-center gap-4 px-3 py-1 text-xs font-mono">
            <span style={{ color: '#f59e0b' }}>─ RSI (14){rsiVal != null ? ` = ${f2(rsiVal)}` : ''}</span>
            <span className="text-slate-600">OB: 70 · OS: 30</span>
          </div>
          <div ref={rsiRef} className="w-full" />
        </div>
      )}

      {showMACD && (
        <div className="border-t border-slate-800">
          <div className="flex items-center gap-4 px-3 py-1 text-xs font-mono">
            <span style={{ color: '#38bdf8' }}>─ MACD (12,26){macdCross.macd != null ? ` = ${f3(macdCross.macd)}` : ''}</span>
            <span style={{ color: '#f59e0b' }}>─ Signal (9){macdCross.signal != null ? ` = ${f3(macdCross.signal)}` : ''}</span>
            <span style={{ color: macdCross.hist != null && macdCross.hist >= 0 ? '#22c55e' : '#ef4444' }}>
              ─ Divergence{macdCross.hist != null ? ` = ${f3(macdCross.hist)}` : ''}
            </span>
          </div>
          <div ref={macdRef} className="w-full" />
        </div>
      )}
    </div>
  );
}
