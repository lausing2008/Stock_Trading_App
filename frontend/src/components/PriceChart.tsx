/**
 * PriceChart — interactive candlestick chart powered by lightweight-charts v4.
 *
 * Props
 * ─────
 * symbol        Stock ticker — used to fetch intraday (5m) data on demand.
 * prices        OHLCV array from the market-data service (daily bars).
 * indicators    Optional TA overlay values (SMA 20/50/200, BB, RSI, MACD).
 * levels        Optional support/resistance levels from the TA service.
 * signalMarkers BUY/SELL signal history points — rendered as chart markers.
 * patterns      Current live pattern signals — shown as a tag strip above chart.
 *
 * Range selector
 * ──────────────
 * "5m" range fetches intraday 5-minute bars directly from the API and renders
 * them with time labels (HH:MM UTC).  All other ranges slice the pre-fetched
 * daily prices[].
 */
'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createChart, CandlestickData, IChartApi, LineData, Time, LineStyle, LogicalRange, UTCTimestamp } from 'lightweight-charts';
import type { Price, Overview, Levels, SignalHistoryPoint, PatternSignal } from '@/lib/api';
import { api } from '@/lib/api';

type Props = {
  symbol: string;
  prices: Price[];
  indicators?: Overview['indicators'];
  levels?: Levels;
  signalMarkers?: SignalHistoryPoint[];
  patterns?: PatternSignal[];
  gamePlanLevels?: {
    entryLow?: number | null;
    entryHigh?: number | null;
    stopLoss?: number | null;
    target1?: number | null;
    target2?: number | null;
  } | null;
};

// Daily ranges — 1D removed; use the 5m button for intraday view
const DAILY_RANGES = [
  { label: '5D',  days: 5    },
  { label: '1M',  days: 21   },
  { label: '3M',  days: 63   },
  { label: '6M',  days: 126  },
  { label: '1Y',  days: 252  },
  { label: '5Y',  days: 1260 },
  { label: 'All', days: null },
] as const;
type DailyLabel = typeof DAILY_RANGES[number]['label'];
type RangeLabel = '5m' | DailyLabel;

const CHART_THEME = {
  layout: { background: { color: '#0b1020' }, textColor: '#94a3b8' },
  grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
  rightPriceScale: { borderColor: '#1e293b' },
  timeScale: { borderColor: '#1e293b', timeVisible: true, secondsVisible: false },
};

const INTRADAY_THEME = {
  ...CHART_THEME,
  timeScale: { borderColor: '#1e293b', timeVisible: true, secondsVisible: false },
};

function toTime(ts: string): Time { return ts.slice(0, 10) as Time; }

function toIntradayTime(ts: string): UTCTimestamp {
  return Math.floor(new Date(ts + 'Z').getTime() / 1000) as UTCTimestamp;
}

function toLine(ts: string[], vals: (number | null)[]): LineData<Time>[] {
  return ts
    .map((t, i) => ({ time: toTime(t), value: vals[i] }))
    .filter((d): d is LineData<Time> => d.value != null);
}

// EMA 200 computed over the full prices array for proper warmup, then filtered to visible range
function computeEma200Map(allPrices: Price[]): Map<string, number> {
  const k = 2 / 201;
  let ema = +allPrices[0].close;
  const out = new Map<string, number>();
  for (const p of allPrices) {
    ema = +p.close * k + ema * (1 - k);
    out.set(p.ts.slice(0, 10), ema);
  }
  return out;
}

// VWAP: cumulative (vol × typical_price) / cumulative vol — resets at start of visible window
function computeVwap(priceData: Price[]): number[] {
  let cumTP = 0, cumVol = 0;
  return priceData.map(p => {
    const tp = (+p.high + +p.low + +p.close) / 3;
    cumTP  += tp * +p.volume;
    cumVol += +p.volume;
    return cumVol > 0 ? cumTP / cumVol : +p.close;
  });
}

// Rolling N-period volume average
function computeVolMA(priceData: Price[], period = 20): (number | null)[] {
  return priceData.map((_, i) => {
    if (i < period - 1) return null;
    const slice = priceData.slice(i - period + 1, i + 1);
    return slice.reduce((s, p) => s + +p.volume, 0) / period;
  });
}

type SmaVals  = { sma_20: number|null; sma_50: number|null; sma_200: number|null; ema_20: number|null; ema_50: number|null; ema_200: number|null };
type MacdVals = { macd: number|null; signal: number|null; hist: number|null };

export default function PriceChart({ symbol, prices, indicators, levels, signalMarkers, patterns, gamePlanLevels }: Props) {
  const mainRef = useRef<HTMLDivElement>(null);
  const rsiRef  = useRef<HTMLDivElement>(null);
  const macdRef = useRef<HTMLDivElement>(null);

  const [range, setRange] = useState<RangeLabel>('3M');
  const [showSMA20,   setShowSMA20]   = useState(true);
  const [showSMA50,   setShowSMA50]   = useState(true);
  const [showSMA200,  setShowSMA200]  = useState(true);
  const [showEMA20,   setShowEMA20]   = useState(false);
  const [showEMA50,   setShowEMA50]   = useState(false);
  const [showEMA200,  setShowEMA200]  = useState(false);
  const [showBB,      setShowBB]      = useState(false);
  const [showVol,     setShowVol]     = useState(true);
  const [showVWAP,    setShowVWAP]    = useState(false);
  const [showRSI,     setShowRSI]     = useState(false);
  const [showMACD,    setShowMACD]    = useState(true);
  const [showSignals, setShowSignals] = useState(true);

  // ── Intraday 5m state ─────────────────────────────────────────────────────
  const [intradayPrices, setIntradayPrices] = useState<Price[] | null>(null);
  const [intradayLoading, setIntradayLoading] = useState(false);
  const isIntraday = range === '5m';

  useEffect(() => {
    if (!isIntraday) { setIntradayPrices(null); return; }
    setIntradayLoading(true);
    // Pass today's UTC date so only the current session is returned — prevents
    // the overnight gap from appearing as a disconnection and avoids showing
    // yesterday's higher prices as a false spike in the Y-axis range.
    const todayUTC = new Date().toISOString().slice(0, 10);
    api.getPrices(symbol, '5m', 200, todayUTC)
      .then(data => {
        // Safety: if today returned very few bars (< 5, e.g. pre-market),
        // fall back to 100 bars without the date filter to show recent history.
        if (data.length < 5) {
          return api.getPrices(symbol, '5m', 100).then(d => setIntradayPrices(d));
        }
        setIntradayPrices(data);
      })
      .catch(() => setIntradayPrices([]))
      .finally(() => setIntradayLoading(false));
  }, [isIntraday, symbol]);

  // ── Daily slice (memoised to avoid chart flicker on SWR polls) ────────────
  const dailyConfig = DAILY_RANGES.find(r => r.label === range);
  const visiblePrices = useMemo(
    () => !dailyConfig ? prices : dailyConfig.days == null ? prices : prices.slice(-dailyConfig.days),
    [prices, dailyConfig],
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

  const [smaVals,   setSmaVals]   = useState<SmaVals>({ sma_20: null, sma_50: null, sma_200: null, ema_20: null, ema_50: null, ema_200: null });
  const [rsiVal,    setRsiVal]    = useState<number|null>(null);
  const [macdCross, setMacdCross] = useState<MacdVals>({ macd: null, signal: null, hist: null });

  type LabelPos = { price: number; y: number; kind: 'support' | 'resistance'; strength: number };
  const [srLabels, setSrLabels] = useState<LabelPos[]>([]);
  const chartRef = useRef<IChartApi | null>(null);

  // Active price data: intraday when 5m selected, daily otherwise
  const activePrices = isIntraday ? (intradayPrices ?? []) : visiblePrices;

  useEffect(() => {
    if (!mainRef.current || activePrices.length === 0) return;

    const theme = isIntraday ? INTRADAY_THEME : CHART_THEME;

    // ── Main chart ─────────────────────────────────────────────────────────
    const chart = createChart(mainRef.current, {
      ...theme,
      autoSize: true,
      height: 420,
    });

    const candles = chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderVisible: false, wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    });

    if (isIntraday) {
      candles.setData(activePrices.map<CandlestickData<Time>>(p => ({
        time: toIntradayTime(p.ts) as unknown as Time,
        open: +p.open, high: +p.high, low: +p.low, close: +p.close,
      })));
    } else {
      candles.setData(activePrices.map<CandlestickData<Time>>(p => ({
        time: toTime(p.ts),
        open: +p.open, high: +p.high, low: +p.low, close: +p.close,
      })));
    }

    // ── 52-week high/low reference lines (daily only) ──────────────────────
    if (!isIntraday && prices.length > 0) {
      const bars52   = prices.slice(-252);
      const high52   = Math.max(...bars52.map(p => +p.high));
      const low52    = Math.min(...bars52.map(p => +p.low));
      candles.createPriceLine({
        price: high52, color: '#facc1566', lineWidth: 1 as const,
        lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '52W H',
      });
      candles.createPriceLine({
        price: low52, color: '#fb923c66', lineWidth: 1 as const,
        lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '52W L',
      });
    }

    // ── Signal BUY/SELL markers (daily only, transition points only) ──────
    if (!isIntraday && showSignals && signalMarkers && signalMarkers.length > 0) {
      // Step 1: keep last entry per calendar date (signals fire every 5 min while stable)
      const byDate = new Map<string, SignalHistoryPoint>();
      for (const m of signalMarkers) {
        if (!m.ts || (m.signal !== 'BUY' && m.signal !== 'SELL')) continue;
        byDate.set(m.ts.slice(0, 10), m);
      }
      const sorted = Array.from(byDate.entries())
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([, m]) => m);
      // Step 2: keep only transition points (first day a new signal direction appears)
      const transitions = sorted.filter((m, i) => i === 0 || m.signal !== sorted[i - 1].signal);
      const markers = transitions.map(m => ({
        time: m.ts!.slice(0, 10) as Time,
        position: (m.signal === 'BUY' ? 'belowBar' : 'aboveBar') as 'belowBar' | 'aboveBar',
        color: m.signal === 'BUY' ? '#22c55e' : '#ef4444',
        shape: (m.signal === 'BUY' ? 'arrowUp' : 'arrowDown') as 'arrowUp' | 'arrowDown',
        text: `${Math.round(m.confidence ?? 0)}%`,
        size: 1,
      }));
      if (markers.length > 0) candles.setMarkers(markers);
    }

    // ── Volume histogram + 20-day MA line ─────────────────────────────────
    if (showVol) {
      const vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(activePrices.map(p => ({
        time: isIntraday
          ? toIntradayTime(p.ts) as unknown as Time
          : toTime(p.ts),
        value: p.volume,
        color: p.close >= p.open ? '#22c55e33' : '#ef444433',
      })));

      const volMaVals = computeVolMA(activePrices, 20);
      const volMaData = activePrices
        .map((p, i) => ({
          time: isIntraday ? toIntradayTime(p.ts) as unknown as Time : toTime(p.ts),
          value: volMaVals[i],
        }))
        .filter((d): d is { time: Time; value: number } => d.value != null);
      if (volMaData.length > 0) {
        const volMaLine = chart.addLineSeries({
          color: '#fbbf24aa',
          lineWidth: 1 as const,
          priceScaleId: 'vol',
        });
        chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
        volMaLine.setData(volMaData);
      }
    }

    // ── VWAP overlay ──────────────────────────────────────────────────────
    if (showVWAP && activePrices.length > 0) {
      const vwapVals = computeVwap(activePrices);
      const vwapData: LineData<Time>[] = activePrices.map((p, i) => ({
        time: isIntraday ? toIntradayTime(p.ts) as unknown as Time : toTime(p.ts),
        value: vwapVals[i],
      }));
      const vwapLine = chart.addLineSeries({ color: '#a78bfa', lineWidth: 1 as const, lineStyle: LineStyle.Dashed });
      vwapLine.setData(vwapData);
    }

    // ── Line overlays (SMA / EMA / BB + EMA 200) — daily only ─────────────
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const lineSeries: Record<string, any> = {};
    if (!isIntraday && visibleIndicators) {
      const lineConfig: { key: string; color: string; show: boolean }[] = [
        { key: 'sma_20',  color: '#38bdf8', show: showSMA20  },
        { key: 'sma_50',  color: '#f59e0b', show: showSMA50  },
        { key: 'sma_200', color: '#a78bfa', show: showSMA200 },
        { key: 'ema_20',  color: '#34d399', show: showEMA20  },
        { key: 'ema_50',  color: '#f472b6', show: showEMA50  },
      ];
      for (const { key, color, show } of lineConfig) {
        if (!show) continue;
        const vals = visibleIndicators.values[key];
        if (!vals) continue;
        const s = chart.addLineSeries({ color, lineWidth: 1 as const });
        s.setData(toLine(visibleIndicators.ts, vals));
        lineSeries[key] = s;
      }

      // EMA 200 — computed from full prices array for warmup accuracy
      if (showEMA200 && prices.length > 0) {
        const ema200Map = computeEma200Map(prices);
        const ema200Data: LineData<Time>[] = visiblePrices
          .map(p => ({ time: toTime(p.ts), value: ema200Map.get(p.ts.slice(0, 10)) }))
          .filter((d): d is LineData<Time> => d.value != null);
        if (ema200Data.length > 0) {
          const s = chart.addLineSeries({ color: '#e879f9', lineWidth: 1 as const, lineStyle: LineStyle.Solid });
          s.setData(ema200Data);
          lineSeries['ema_200'] = s;
        }
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
        if (!param.time) {
          setSmaVals({ sma_20: null, sma_50: null, sma_200: null, ema_20: null, ema_50: null, ema_200: null });
          return;
        }
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const v = (key: string) => lineSeries[key] ? (param.seriesData.get(lineSeries[key]) as any)?.value ?? null : null;
        setSmaVals({
          sma_20: v('sma_20'), sma_50: v('sma_50'), sma_200: v('sma_200'),
          ema_20: v('ema_20'), ema_50: v('ema_50'), ema_200: v('ema_200'),
        });
      });
    }

    // ── S/R levels (daily mode only) ───────────────────────────────────────
    const lastClose = !isIntraday ? (activePrices.at(-1)?.close ?? null) : null;
    const srLevels = !isIntraday
      ? (levels?.support_resistance?.slice(0, 8) ?? []).map(lvl => ({
          ...lvl,
          kind: lastClose != null
            ? (lvl.price > lastClose ? 'resistance' : 'support') as 'support' | 'resistance'
            : lvl.kind,
        }))
      : [];
    for (const lvl of srLevels) {
      candles.createPriceLine({
        price: lvl.price,
        color: lvl.kind === 'support' ? '#22c55e66' : '#ef444466',
        lineWidth: 1 as const, lineStyle: LineStyle.Dotted, axisLabelVisible: false,
        title: '',
      });
    }

    // ── Game Plan levels (daily only) ─────────────────────────────────────
    if (!isIntraday && gamePlanLevels) {
      const gpl = gamePlanLevels;
      if (gpl.entryLow && gpl.entryLow > 0) {
        candles.createPriceLine({ price: gpl.entryLow, color: '#4ade80', lineWidth: 2 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'Entry Low' });
      }
      if (gpl.entryHigh && gpl.entryHigh > 0) {
        candles.createPriceLine({ price: gpl.entryHigh, color: '#4ade80', lineWidth: 2 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'Entry High' });
      }
      if (gpl.stopLoss && gpl.stopLoss > 0) {
        candles.createPriceLine({ price: gpl.stopLoss, color: '#f87171', lineWidth: 2 as const, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: 'Stop' });
      }
      if (gpl.target1 && gpl.target1 > 0) {
        candles.createPriceLine({ price: gpl.target1, color: '#a78bfa', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'Target 1' });
      }
      if (gpl.target2 && gpl.target2 > 0) {
        candles.createPriceLine({ price: gpl.target2, color: '#38bdf8', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'Target 2' });
      }
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
    chart.timeScale().fitContent();

    // ── RSI chart (daily only) ─────────────────────────────────────────────
    let rsiChart: IChartApi | null = null;
    if (!isIntraday && showRSI && rsiRef.current && visibleIndicators) {
      rsiChart = createChart(rsiRef.current, {
        ...CHART_THEME, autoSize: true, height: 120,
        timeScale: { ...CHART_THEME.timeScale, visible: false },
      });
      const rsiLine = rsiChart.addLineSeries({ color: '#f59e0b', lineWidth: 1 });
      const rsiVals = visibleIndicators.values['rsi_14'];
      if (rsiVals) {
        rsiLine.setData(toLine(visibleIndicators.ts, rsiVals));
        rsiLine.createPriceLine({ price: 70, color: '#ef444466', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70' });
        rsiLine.createPriceLine({ price: 50, color: '#94a3b844', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '50' });
        rsiLine.createPriceLine({ price: 30, color: '#22c55e66', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30' });
      }
      rsiChart.subscribeCrosshairMove((param) => {
        if (!param.time) { setRsiVal(null); return; }
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        setRsiVal((param.seriesData.get(rsiLine) as any)?.value ?? null);
      });
    }

    // ── MACD chart (daily only) ────────────────────────────────────────────
    let macdChart: IChartApi | null = null;
    if (!isIntraday && showMACD && macdRef.current && visibleIndicators) {
      macdChart = createChart(macdRef.current, {
        ...CHART_THEME, autoSize: true, height: 120,
        timeScale: { ...CHART_THEME.timeScale, visible: !showRSI },
      });

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let histSeries: any = null, macdLine: any = null, signalLine: any = null;

      const histVals = visibleIndicators.values['hist'];
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
      const sigVals = visibleIndicators.values['signal'];
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
  }, [activePrices, visibleIndicators, levels, prices, signalMarkers, gamePlanLevels, showSMA20, showSMA50, showSMA200, showEMA20, showEMA50, showEMA200, showBB, showVol, showVWAP, showRSI, showMACD, showSignals, isIntraday]);

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

      {/* Range selector + toolbar row */}
      <div className="flex flex-wrap items-center gap-x-1 gap-y-1 px-3 pt-2 pb-1.5 border-b border-slate-800">
        {/* Intraday button */}
        <button
          onClick={() => setRange('5m')}
          className={`px-2.5 py-1 rounded text-xs font-semibold transition-colors ${
            range === '5m'
              ? 'bg-indigo-600 text-white'
              : 'text-slate-400 hover:text-indigo-300 hover:bg-slate-800'
          }`}
        >
          5m
        </button>
        {/* Daily range buttons */}
        {DAILY_RANGES.map(r => (
          <button
            key={r.label}
            onClick={() => setRange(r.label)}
            className={`px-2.5 py-1 rounded text-xs font-semibold transition-colors ${
              range === r.label
                ? 'bg-indigo-600 text-white'
                : 'text-slate-400 hover:text-indigo-300 hover:bg-slate-800'
            }`}
          >
            {r.label}
          </button>
        ))}
        <span className="mx-2 h-4 w-px bg-slate-700" />
        <span className="text-xs text-slate-600">
          {isIntraday
            ? (intradayLoading ? 'loading…' : `${activePrices.length} bars · UTC`)
            : `${activePrices.length} bars`}
        </span>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2 border-b border-slate-800 text-xs">
        {!isIntraday ? (
          <>
            {/* SMA group */}
            <div className="flex items-center gap-1">
              <span className="text-slate-500 mr-0.5">SMA</span>
              {btn(showSMA20,  '20',  () => setShowSMA20((v: boolean)  => !v), '#38bdf8')}
              {btn(showSMA50,  '50',  () => setShowSMA50((v: boolean)  => !v), '#f59e0b')}
              {btn(showSMA200, '200', () => setShowSMA200((v: boolean) => !v), '#a78bfa')}
            </div>
            {/* EMA group */}
            <div className="flex items-center gap-1">
              <span className="text-slate-500 mr-0.5">EMA</span>
              {btn(showEMA20,  '20',  () => setShowEMA20((v: boolean)  => !v), '#34d399')}
              {btn(showEMA50,  '50',  () => setShowEMA50((v: boolean)  => !v), '#f472b6')}
              {btn(showEMA200, '200', () => setShowEMA200((v: boolean) => !v), '#e879f9')}
            </div>
            {/* Other overlays */}
            <div className="flex items-center gap-1">
              {btn(showBB,      'BB',   () => setShowBB((v: boolean)      => !v), '#6366f1')}
              {btn(showVol,     'Vol',  () => setShowVol((v: boolean)     => !v), '#22c55e')}
              {btn(showVWAP,    'VWAP', () => setShowVWAP((v: boolean)    => !v), '#a78bfa')}
              {btn(showSignals, 'Sig',  () => setShowSignals((v: boolean) => !v), '#22c55e')}
            </div>
            {/* Panels */}
            <div className="flex items-center gap-1">
              <span className="text-slate-500 mr-0.5">Panel</span>
              {btn(showRSI,  'RSI',  () => setShowRSI((v: boolean)  => !v), '#f59e0b')}
              {btn(showMACD, 'MACD', () => setShowMACD((v: boolean) => !v), '#38bdf8')}
            </div>
          </>
        ) : (
          <>
            {btn(showVol,  'Vol',  () => setShowVol((v: boolean)  => !v), '#22c55e')}
            {btn(showVWAP, 'VWAP', () => setShowVWAP((v: boolean) => !v), '#a78bfa')}
            <span className="text-slate-600 ml-1">5-min · SMA/EMA/MACD on daily only</span>
          </>
        )}

        {/* Legend */}
        <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-400">
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-sm bg-green-500 opacity-80" />Up</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-sm bg-red-500 opacity-80" />Down</span>
          {!isIntraday && showSMA20   && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-sky-400" />SMA 20</span>}
          {!isIntraday && showSMA50   && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-amber-400" />SMA 50</span>}
          {!isIntraday && showSMA200  && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-violet-400" />SMA 200</span>}
          {!isIntraday && showEMA20   && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-emerald-400" />EMA 20</span>}
          {!isIntraday && showEMA50   && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-pink-400" />EMA 50</span>}
          {!isIntraday && showEMA200  && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-fuchsia-400" />EMA 200</span>}
          {!isIntraday && showBB      && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-indigo-400 opacity-70" />BB</span>}
          {showVWAP                   && <span className="flex items-center gap-1"><span className="inline-block w-4 border-t border-dashed border-violet-400" />VWAP</span>}
          {!isIntraday && levels?.support_resistance?.length ? <>
            <span className="flex items-center gap-1"><span className="inline-block w-4 border-t border-dashed border-green-500 opacity-70" />Support</span>
            <span className="flex items-center gap-1"><span className="inline-block w-4 border-t border-dashed border-red-500 opacity-70" />Resist.</span>
          </> : null}
        </div>
      </div>

      {/* Crosshair readout — line values at cursor (daily only) */}
      {!isIntraday && visibleIndicators && (
        <div className="flex flex-wrap items-center gap-4 px-3 py-1.5 text-xs font-mono bg-slate-900/60 border-b border-slate-800/50 min-h-[26px]">
          {smaVals.sma_20   != null && showSMA20   && <span style={{ color: '#38bdf8' }}>SMA 20 = {f2(smaVals.sma_20)}</span>}
          {smaVals.sma_50   != null && showSMA50   && <span style={{ color: '#f59e0b' }}>SMA 50 = {f2(smaVals.sma_50)}</span>}
          {smaVals.sma_200  != null && showSMA200  && <span style={{ color: '#a78bfa' }}>SMA 200 = {f2(smaVals.sma_200)}</span>}
          {smaVals.ema_20   != null && showEMA20   && <span style={{ color: '#34d399' }}>EMA 20 = {f2(smaVals.ema_20)}</span>}
          {smaVals.ema_50   != null && showEMA50   && <span style={{ color: '#f472b6' }}>EMA 50 = {f2(smaVals.ema_50)}</span>}
          {smaVals.ema_200  != null && showEMA200  && <span style={{ color: '#e879f9' }}>EMA 200 = {f2(smaVals.ema_200)}</span>}
        </div>
      )}

      {/* Detected pattern signals strip */}
      {patterns && patterns.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 px-3 py-1.5 border-b border-slate-800/50 bg-slate-900/40">
          <span className="text-xs text-slate-500 shrink-0">Patterns:</span>
          {patterns.map((p, i) => (
            <span
              key={i}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${p.bullish ? 'bg-green-900/40 text-green-400 border border-green-800/50' : 'bg-red-900/40 text-red-400 border border-red-800/50'}`}
            >
              <span>{p.bullish ? '▲' : '▼'}</span>
              {p.label}
            </span>
          ))}
        </div>
      )}

      {/* Loading overlay for intraday fetch */}
      {isIntraday && intradayLoading && (
        <div className="flex items-center justify-center h-[380px] text-sm text-slate-500">
          Loading 5m bars…
        </div>
      )}

      <div style={{ position: 'relative', display: isIntraday && intradayLoading ? 'none' : 'block' }}>
        <div ref={mainRef} className="w-full" />
        {/* S/R labels pinned to left edge (daily only) */}
        {!isIntraday && srLabels.map((l, i) => {
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

      {!isIntraday && showRSI && (
        <div className="border-t border-slate-700/60">
          <div className="flex items-center gap-5 px-3 py-1.5 bg-slate-900/40">
            <span className="text-slate-400 text-xs font-semibold tracking-wide">RSI (14)</span>
            <span style={{ color: '#f59e0b' }} className="text-xs font-mono">{rsiVal != null ? f2(rsiVal) : '—'}</span>
            <span className="text-slate-600 text-xs ml-auto">OB 70 · Mid 50 · OS 30</span>
          </div>
          <div ref={rsiRef} className="w-full" />
        </div>
      )}

      {!isIntraday && showMACD && (
        <div className="border-t border-slate-700/60">
          <div className="flex items-center gap-5 px-3 py-1.5 bg-slate-900/40">
            <span className="text-slate-400 text-xs font-semibold tracking-wide">MACD (12,26,9)</span>
            <span style={{ color: '#38bdf8' }} className="text-xs font-mono">{macdCross.macd != null ? f3(macdCross.macd) : '—'}</span>
            <span style={{ color: '#f59e0b' }} className="text-xs font-mono">Sig {macdCross.signal != null ? f3(macdCross.signal) : '—'}</span>
            <span style={{ color: macdCross.hist != null && macdCross.hist >= 0 ? '#22c55e' : '#ef4444' }} className="text-xs font-mono">
              Hist {macdCross.hist != null ? f3(macdCross.hist) : '—'}
            </span>
          </div>
          <div ref={macdRef} className="w-full" />
        </div>
      )}
    </div>
  );
}
