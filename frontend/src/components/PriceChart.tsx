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
import { computeVolumeProfile, sessionBars } from '@/lib/volumeProfile';
import { detectSwingPivots, nearestPivot } from '@/lib/swingPivots';
import { VolumeProfilePrimitive } from './VolumeProfilePrimitive';
import { ToolbarDropdown } from './ToolbarDropdown';
import { computeSMA, computeEMA, computeRSI, computeMACD, computeBollingerBands } from '@/lib/indicators';
import { loadDrawings, addDrawing, removeDrawing, clearDrawings, nextDrawingId, type ChartDrawing } from '@/lib/chartDrawings';

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
  /** T252: ATR/Position-Sizer-derived entry/stop/target — always available (no LLM call
   * required, unlike gamePlanLevels), drawn distinctly so it doesn't visually collide with
   * an active game plan overlay. */
  riskRewardLevels?: {
    entry?: number | null;
    stop?: number | null;
    target?: number | null;
  } | null;
  /** T230: External intraday bars (15m/1h/4h) — when provided, forces intraday rendering mode */
  intradayOverride?: Price[] | null;
  /** T230: Comparison overlay data (daily bars for a second symbol, e.g. SPY) */
  compareData?: { symbol: string; prices: Price[] } | null;
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

export default function PriceChart({ symbol, prices, indicators, levels, signalMarkers, patterns, gamePlanLevels, riskRewardLevels, intradayOverride, compareData }: Props) {
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
  const [showFVG,      setShowFVG]     = useState(false);
  // T252-DECLUTTER: S/R levels + 52W High/Low used to always render — combined with FVG,
  // Entry/Stop/Target, and indicator curves, this stacked up to 15+ overlapping lines with
  // no way to turn any group off (a user reported the chart as too cluttered to read). 52W
  // High/Low off by default (least commonly needed); S/R back on by default per explicit
  // user request — it's the most broadly useful of the three, FVG stays off by default since
  // it was the actual source of the reported clutter (up to 20 gaps rendered as 40 lines).
  const [showSR,       setShowSR]      = useState(true);
  const [show52W,      setShow52W]     = useState(false);
  const [showSwingPivots, setShowSwingPivots] = useState(false);
  // Volume profile: 'off' | 'session' (current trading session only) | 'range' (whole visible
  // window) | 'fixed' (user click-selected start/end range — the real Fixed Range VP tool).
  const [volumeProfileMode, setVolumeProfileMode] = useState<'off' | 'session' | 'range' | 'fixed'>('off');
  // Fixed Range VP selection state: 'idle' (not selecting) -> 'picking-start' (armed, waiting
  // for first click) -> 'picking-end' (first click done, waiting for second click) -> back to
  // 'idle' once both points are picked and fixedRangeSelection is set. The first click's bar
  // index is held in a ref (not state) so it doesn't trigger a chart rebuild on its own —
  // only fixedRangeSelection (set once, on the second click) should do that.
  const [fixedRangePickState, setFixedRangePickState] = useState<'idle' | 'picking-start' | 'picking-end'>('idle');
  const [fixedRangeSelection, setFixedRangeSelection] = useState<{ startIdx: number; endIdx: number } | null>(null);
  const fixedRangeStartIdxRef = useRef<number | null>(null);
  // Bumped every time the main chart-rebuild effect creates a new chart instance, so the
  // separate click-subscription effect below always resubscribes to the CURRENT instance —
  // guards against the case where the user starts picking a Fixed Range, then also toggles
  // an unrelated overlay (e.g. SMA) before finishing the 2 clicks, which would otherwise
  // rebuild the chart out from under an already-subscribed, now-stale click handler.
  const [chartInstanceVersion, setChartInstanceVersion] = useState(0);

  // ── T230-CHARTING-DRAWING-TOOLS: horizontal lines + trendlines, persisted per symbol ──
  // Same picking-state architecture as Fixed Range VP above (idle -> picking -> idle), reusing
  // the same chart.subscribeClick()-in-a-separate-effect pattern rather than inventing a new
  // mechanism. 'horizontal' needs only 1 click (price only, x position is irrelevant to a
  // horizontal ray); 'trendline' needs 2 (start point + end point, both bar-index AND price).
  const [drawTool, setDrawTool] = useState<'off' | 'horizontal' | 'trendline'>('off');
  const [drawPickState, setDrawPickState] = useState<'idle' | 'picking-start' | 'picking-end'>('idle');
  const [drawings, setDrawings] = useState<ChartDrawing[]>([]);
  const drawStartRef = useRef<{ idx: number; price: number } | null>(null);

  // Load this symbol's saved drawings on mount / symbol change.
  useEffect(() => {
    setDrawings(loadDrawings(symbol));
  }, [symbol]);

  // ── Intraday 5m state ─────────────────────────────────────────────────────
  const [intradayPrices, setIntradayPrices] = useState<Price[] | null>(null);
  const [intradayLoading, setIntradayLoading] = useState(false);
  // intradayOverride: externally provided intraday bars (15m/1h/4h) force intraday mode
  const isIntraday = range === '5m' || (intradayOverride != null && intradayOverride.length > 0);

  useEffect(() => {
    // When intradayOverride is active, skip the internal 5m fetch
    if (intradayOverride != null) { setIntradayPrices(null); return; }
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
  }, [isIntraday, symbol, intradayOverride]);

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
  const candlesRef = useRef<ReturnType<IChartApi['addCandlestickSeries']> | null>(null);

  // Active price data: intradayOverride > internal 5m > daily
  const activePrices = intradayOverride != null && intradayOverride.length > 0
    ? intradayOverride
    : isIntraday ? (intradayPrices ?? []) : visiblePrices;

  // T230-CHARTING-PREMARKET: only show the "Extended Hours" legend swatch when the current
  // intraday data actually contains a dimmed pre/post-market bar — no point explaining a
  // visual that isn't present (e.g. HK symbols, or a US symbol with no extended-hours trades).
  const hasExtendedHoursBars = isIntraday && activePrices.some(p => p.session === 'PRE' || p.session === 'POST');

  const volumeProfile = useMemo(() => {
    if (volumeProfileMode === 'off' || activePrices.length === 0) return null;
    if (volumeProfileMode === 'fixed') {
      if (!fixedRangeSelection) return null;
      const { startIdx, endIdx } = fixedRangeSelection;
      const lo = Math.min(startIdx, endIdx), hi = Math.max(startIdx, endIdx);
      const bars = activePrices.slice(lo, hi + 1);
      return computeVolumeProfile(bars, 24);
    }
    const bars = volumeProfileMode === 'session' ? sessionBars(activePrices) : activePrices;
    return computeVolumeProfile(bars, 24);
  }, [volumeProfileMode, activePrices, fixedRangeSelection]);

  // T252-AUTO-SWING-PIVOTS: computed whenever pivots might be needed for rendering OR for
  // snapping a Fixed Range VP click, not gated behind showSwingPivots alone — the click-snap
  // benefit should apply even if the user never turns the marker overlay on.
  const swingPivots = useMemo(() => {
    if (isIntraday || activePrices.length === 0) return [];
    return detectSwingPivots(activePrices, 5);
  }, [activePrices, isIntraday]);

  useEffect(() => {
    if (!mainRef.current || activePrices.length === 0) return;

    const theme = isIntraday ? INTRADAY_THEME : CHART_THEME;

    // ── Main chart ─────────────────────────────────────────────────────────
    const chart = createChart(mainRef.current, {
      ...theme,
      autoSize: true,
      height: 600,
    });

    const candles = chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderVisible: false, wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    });
    candlesRef.current = candles;

    if (isIntraday) {
      // T230-CHARTING-PREMARKET: dim pre/post-market bars so extended-hours activity is
      // visible (earnings gaps, overnight moves) without being confused for regular-session
      // volume — same up/down hue, lower opacity, distinguishing them at a glance.
      candles.setData(activePrices.map<CandlestickData<Time>>(p => {
        const isExtended = p.session === 'PRE' || p.session === 'POST';
        return {
          time: toIntradayTime(p.ts) as unknown as Time,
          open: +p.open, high: +p.high, low: +p.low, close: +p.close,
          ...(isExtended ? {
            color: +p.close >= +p.open ? '#22c55e66' : '#ef444466',
            borderColor: +p.close >= +p.open ? '#22c55e66' : '#ef444466',
            wickColor: +p.close >= +p.open ? '#22c55e66' : '#ef444466',
          } : {}),
        };
      }));
    } else {
      candles.setData(activePrices.map<CandlestickData<Time>>(p => ({
        time: toTime(p.ts),
        open: +p.open, high: +p.high, low: +p.low, close: +p.close,
      })));
    }

    // ── 52-week high/low reference lines (daily only) ──────────────────────
    if (!isIntraday && show52W && prices.length > 0) {
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

    // ── Volume profile: POC/VAH/VAL histogram over the session or the visible range ──
    // No bid/ask or tick data is available (see the T249-era footprint-chart investigation
    // — every data source here is bars-only), so this uses the standard retail-tool
    // approximation: each bar's volume is spread across its high-low range, bucketed by
    // price. See src/lib/volumeProfile.ts. Reuses the `volumeProfile` memo computed in
    // render scope (also used by the legend readout below) rather than recomputing here.
    let vpPrimitive: VolumeProfilePrimitive | null = null;
    if (volumeProfile && activePrices.length > 0) {
      const profileBars = volumeProfileMode === 'session' ? sessionBars(activePrices)
        : volumeProfileMode === 'fixed' && fixedRangeSelection
          ? activePrices.slice(
              Math.min(fixedRangeSelection.startIdx, fixedRangeSelection.endIdx),
              Math.max(fixedRangeSelection.startIdx, fixedRangeSelection.endIdx) + 1,
            )
          : activePrices;
      if (profileBars.length > 0) {
        const anchorTime = isIntraday
          ? toIntradayTime(profileBars[0].ts) as unknown as Time
          : toTime(profileBars[0].ts);
        vpPrimitive = new VolumeProfilePrimitive(chart, candles);
        vpPrimitive.setData({ time: anchorTime, profile: volumeProfile, width: profileBars.length });
        candles.attachPrimitive(vpPrimitive);
      }
    }

    // ── Chart markers: signal BUY/SELL transitions + swing pivots ─────────
    // setMarkers() replaces the whole marker set on each call — both marker sources are
    // accumulated here and set together in ONE call rather than two, which would silently
    // clobber whichever ran first.
    {
      const allMarkers: { time: Time; position: 'belowBar' | 'aboveBar'; color: string; shape: 'arrowUp' | 'arrowDown' | 'circle'; text?: string; size?: number }[] = [];

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
        for (const m of transitions) {
          allMarkers.push({
            time: m.ts!.slice(0, 10) as Time,
            position: m.signal === 'BUY' ? 'belowBar' : 'aboveBar',
            color: m.signal === 'BUY' ? '#22c55e' : '#ef4444',
            shape: m.signal === 'BUY' ? 'arrowUp' : 'arrowDown',
            text: `${Math.round(m.confidence ?? 0)}%`,
            size: 1,
          });
        }
      }

      // T252-AUTO-SWING-PIVOTS: small dot markers on real local swing highs/lows, so Fixed
      // Range VP's two clicks can be aimed at an actual extremum instead of eyeballed. Off by
      // default (matching every other opt-in overlay's decluttering convention).
      if (!isIntraday && showSwingPivots && swingPivots.length > 0) {
        for (const p of swingPivots) {
          allMarkers.push({
            time: (isIntraday ? toIntradayTime(p.ts) : toTime(p.ts)) as unknown as Time,
            position: p.kind === 'high' ? 'aboveBar' : 'belowBar',
            color: '#94a3b8',
            shape: 'circle',
            size: 1,
          });
        }
      }

      if (allMarkers.length > 0) candles.setMarkers(allMarkers);
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

    // ── Line overlays (SMA / EMA / BB + EMA 200) ──────────────────────────
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const lineSeries: Record<string, any> = {};
    if (isIntraday && activePrices.length > 0) {
      // Intraday bars carry no server-computed indicators (technical-analysis only computes
      // SMA/EMA/RSI/MACD/BB for daily bars) — compute locally from the fetched bars, same
      // approach already used here for VWAP/EMA200 on daily data. A typical single trading
      // day of 5m bars (~78 bars) won't reach SMA/EMA 200's warmup — those lines simply don't
      // render until enough bars exist, same graceful-null behavior as every other overlay.
      const closes = activePrices.map(p => +p.close);
      const intraLineConfig: { key: string; color: string; show: boolean; vals: (number | null)[] }[] = [
        { key: 'sma_20',  color: '#38bdf8', show: showSMA20,  vals: computeSMA(closes, 20) },
        { key: 'sma_50',  color: '#f59e0b', show: showSMA50,  vals: computeSMA(closes, 50) },
        { key: 'sma_200', color: '#a78bfa', show: showSMA200, vals: computeSMA(closes, 200) },
        { key: 'ema_20',  color: '#34d399', show: showEMA20,  vals: computeEMA(closes, 20) },
        { key: 'ema_50',  color: '#f472b6', show: showEMA50,  vals: computeEMA(closes, 50) },
        { key: 'ema_200', color: '#e879f9', show: showEMA200, vals: computeEMA(closes, 200) },
      ];
      for (const { key, color, show, vals } of intraLineConfig) {
        if (!show) continue;
        const data: LineData<Time>[] = activePrices
          .map((p, i) => ({ time: toIntradayTime(p.ts) as unknown as Time, value: vals[i] }))
          .filter((d): d is LineData<Time> => d.value != null);
        if (data.length === 0) continue;
        const s = chart.addLineSeries({ color, lineWidth: 1 as const });
        s.setData(data);
        lineSeries[key] = s;
      }

      if (showBB) {
        const bb = computeBollingerBands(closes, 20, 2);
        for (const [key, vals] of [['bb_upper', bb.upper], ['bb_lower', bb.lower], ['bb_mid', bb.mid]] as const) {
          const data: LineData<Time>[] = activePrices
            .map((p, i) => ({ time: toIntradayTime(p.ts) as unknown as Time, value: vals[i] }))
            .filter((d): d is LineData<Time> => d.value != null);
          if (data.length === 0) continue;
          chart.addLineSeries({
            color: '#6366f188', lineWidth: 1 as const,
            lineStyle: key === 'bb_mid' ? LineStyle.Dashed : LineStyle.Solid,
          }).setData(data);
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
    const srLevels = !isIntraday && showSR
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

    // ── Fair Value Gaps (daily mode only) — 3-candle imbalance zones price often
    // retraces into before continuing. Only unfilled gaps are actionable as entry zones,
    // but filled ones are still shown dimmed so a user can see which levels already "did
    // their job" as support/resistance on revisit. Two price lines per gap (top/bottom
    // edge), matching the same createPriceLine-per-level pattern as S/R and gamePlanLevels
    // above rather than introducing a new rendering mechanism for one more level type.
    // T252-DECLUTTER: the backend can return up to 20 gaps (detect_fair_value_gaps' own
    // max_gaps) — rendering all of them as 40 price lines was the actual cause of a chart
    // a user reported as unreadable. Cap to the 6 most relevant: unfilled gaps first (the
    // only ones that matter for a NEW entry), then the most recent filled ones to fill out
    // the cap if there aren't 6 unfilled.
    const _FVG_MAX_RENDERED = 6;
    const allFvgs = !isIntraday && showFVG ? (levels?.fair_value_gaps ?? []) : [];
    const unfilledFvgs = allFvgs.filter(g => !g.filled).slice(-_FVG_MAX_RENDERED);
    const filledFvgs = unfilledFvgs.length < _FVG_MAX_RENDERED
      ? allFvgs.filter(g => g.filled).slice(-(_FVG_MAX_RENDERED - unfilledFvgs.length))
      : [];
    const fvgs = [...unfilledFvgs, ...filledFvgs];
    for (const g of fvgs) {
      const bullColor = g.filled ? '#22c55e33' : '#22c55eaa';
      const bearColor = g.filled ? '#ef444433' : '#ef4444aa';
      const color = g.kind === 'bullish' ? bullColor : bearColor;
      const style = g.filled ? LineStyle.Dotted : LineStyle.Dashed;
      candles.createPriceLine({
        price: g.top, color, lineWidth: 1 as const, lineStyle: style,
        axisLabelVisible: false, title: g.filled ? '' : `FVG ${g.kind === 'bullish' ? '▲' : '▼'}`,
      });
      candles.createPriceLine({
        price: g.bottom, color, lineWidth: 1 as const, lineStyle: style,
        axisLabelVisible: false, title: '',
      });
    }

    // ── T230-CHARTING-DRAWING-TOOLS: user-drawn horizontal lines + trendlines ──────
    // Horizontal lines reuse createPriceLine (same mechanism as every other flat level on
    // this chart). Trendlines need a genuine 2-point line, which createPriceLine can't do
    // (it only draws flat horizontal lines) — a dedicated 2-point LineSeries per trendline
    // is the standard lightweight-charts approach for this, same technique used for the
    // Normalized Comparison overlay below.
    for (const d of drawings) {
      if (d.type === 'horizontal') {
        candles.createPriceLine({
          price: d.price, color: '#facc15', lineWidth: 2 as const, lineStyle: LineStyle.Solid,
          axisLabelVisible: true, title: '',
        });
      } else {
        const startTime = isIntraday
          ? toIntradayTime(activePrices[d.startIdx]?.ts ?? activePrices[0]?.ts ?? '') as unknown as Time
          : toTime(activePrices[d.startIdx]?.ts ?? activePrices[0]?.ts ?? '');
        const endTime = isIntraday
          ? toIntradayTime(activePrices[d.endIdx]?.ts ?? activePrices.at(-1)?.ts ?? '') as unknown as Time
          : toTime(activePrices[d.endIdx]?.ts ?? activePrices.at(-1)?.ts ?? '');
        const trendSeries = chart.addLineSeries({
          color: '#facc15', lineWidth: 2 as const, lastValueVisible: false, priceLineVisible: false,
        });
        trendSeries.setData([
          { time: startTime, value: d.startPrice },
          { time: endTime, value: d.endPrice },
        ]);
      }
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

    // ── T252: Risk/Reward levels — ATR stop / nearest support / analyst target, the same
    // values already shown as numbers in the Position Sizer card, drawn on the chart itself.
    // Only shown when there's no active LLM game plan overlay (gamePlanLevels) — the two
    // are alternative "where's my entry/stop/target" views and would visually collide if
    // both rendered at once; gamePlanLevels (user-triggered, LLM-backed) takes priority when
    // it exists since it's a more deliberate, specific plan than the always-on ATR default.
    if (!isIntraday && !gamePlanLevels && riskRewardLevels) {
      const rrl = riskRewardLevels;
      if (rrl.entry && rrl.entry > 0) {
        candles.createPriceLine({ price: rrl.entry, color: '#facc15', lineWidth: 2 as const, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: 'Entry' });
      }
      if (rrl.stop && rrl.stop > 0) {
        candles.createPriceLine({ price: rrl.stop, color: '#f87171', lineWidth: 2 as const, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: 'Stop' });
      }
      if (rrl.target && rrl.target > 0) {
        // AUD-CHART-INVERTEDRR: Math.abs on both legs used to label a positive-looking R:R
        // even when target sat on the wrong side of entry relative to stop (e.g. an analyst
        // target below current price drawn as if it were a bullish take-profit). Direction is
        // inferred from stop vs entry — only show the ratio when target is on the correct
        // side for that direction; otherwise the line still draws (it's real data) but with
        // no R:R claim attached.
        const isLong = rrl.stop != null && rrl.entry ? rrl.stop < rrl.entry : true;
        const targetValidSide = rrl.entry ? (isLong ? rrl.target > rrl.entry : rrl.target < rrl.entry) : true;
        const rrLabel = (rrl.entry && rrl.stop && rrl.entry > 0 && Math.abs(rrl.entry - rrl.stop) > 0 && targetValidSide)
          ? ` (${(Math.abs(rrl.target - rrl.entry) / Math.abs(rrl.entry - rrl.stop)).toFixed(1)}:1)`
          : '';
        candles.createPriceLine({ price: rrl.target, color: '#38bdf8', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: `Target${rrLabel}` });
      }
    }

    // ── T230: Normalized comparison overlay (daily mode only) ─────────────────
    // Renders a dashed line showing the compare symbol's % return from period start,
    // on a separate price scale so it doesn't interfere with the main price axis.
    if (!isIntraday && compareData && compareData.prices.length > 1) {
      // Align compare prices to the visible window using date keys
      const visStart = activePrices.length > 0 ? activePrices[0].ts.slice(0, 10) : null;
      const visEnd   = activePrices.length > 0 ? activePrices[activePrices.length - 1].ts.slice(0, 10) : null;
      const aligned = visStart && visEnd
        ? compareData.prices.filter(p => {
            const d = p.ts.slice(0, 10);
            return d >= visStart && d <= visEnd;
          })
        : compareData.prices;
      if (aligned.length > 1) {
        const firstClose = +aligned[0].close;
        const normData: LineData<Time>[] = aligned.map(p => ({
          time: toTime(p.ts),
          value: ((+p.close / firstClose) - 1) * 100,
        }));
        const compareLine = chart.addLineSeries({
          color: '#f59e0b',
          lineWidth: 1 as const,
          lineStyle: LineStyle.Dashed,
          priceScaleId: 'compare',
          title: compareData.symbol,
        });
        chart.priceScale('compare').applyOptions({
          scaleMargins: { top: 0.1, bottom: 0.1 },
          visible: false,  // hide axis label; use legend instead
        });
        compareLine.setData(normData);
      }
    }

    chartRef.current = chart;
    setChartInstanceVersion(v => v + 1);

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

    // ── RSI chart — daily reads server-computed values, intraday computes locally ──
    let rsiChart: IChartApi | null = null;
    if (showRSI && rsiRef.current && (isIntraday ? activePrices.length > 0 : !!visibleIndicators)) {
      rsiChart = createChart(rsiRef.current, {
        ...CHART_THEME, autoSize: true, height: 120,
        timeScale: { ...CHART_THEME.timeScale, visible: false },
      });
      const rsiLine = rsiChart.addLineSeries({ color: '#f59e0b', lineWidth: 1 });
      if (isIntraday) {
        const rsiVals = computeRSI(activePrices.map(p => +p.close), 14);
        const data: LineData<Time>[] = activePrices
          .map((p, i) => ({ time: toIntradayTime(p.ts) as unknown as Time, value: rsiVals[i] }))
          .filter((d): d is LineData<Time> => d.value != null);
        if (data.length > 0) rsiLine.setData(data);
      } else {
        const rsiVals = visibleIndicators!.values['rsi_14'];
        if (rsiVals) rsiLine.setData(toLine(visibleIndicators!.ts, rsiVals));
      }
      rsiLine.createPriceLine({ price: 70, color: '#ef444466', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70' });
      rsiLine.createPriceLine({ price: 50, color: '#94a3b844', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '50' });
      rsiLine.createPriceLine({ price: 30, color: '#22c55e66', lineWidth: 1 as const, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30' });
      rsiChart.subscribeCrosshairMove((param) => {
        if (!param.time) { setRsiVal(null); return; }
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        setRsiVal((param.seriesData.get(rsiLine) as any)?.value ?? null);
      });
    }

    // ── MACD chart — daily reads server-computed values, intraday computes locally ──
    let macdChart: IChartApi | null = null;
    if (showMACD && macdRef.current && (isIntraday ? activePrices.length > 0 : !!visibleIndicators)) {
      macdChart = createChart(macdRef.current, {
        ...CHART_THEME, autoSize: true, height: 120,
        timeScale: { ...CHART_THEME.timeScale, visible: !showRSI },
      });

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let histSeries: any = null, macdLine: any = null, signalLine: any = null;

      if (isIntraday) {
        const { macd: macdVals, signal: sigVals, hist: histVals } = computeMACD(activePrices.map(p => +p.close), 12, 26, 9);
        const histData = activePrices
          .map((p, i) => ({ time: toIntradayTime(p.ts) as unknown as Time, value: histVals[i], color: (histVals[i] ?? 0) >= 0 ? '#22c55e' : '#ef4444' }))
          .filter(d => d.value != null) as { time: Time; value: number; color: string }[];
        if (histData.length > 0) {
          histSeries = macdChart.addHistogramSeries({ priceScaleId: 'right' });
          histSeries.setData(histData);
        }
        const macdData = activePrices
          .map((p, i) => ({ time: toIntradayTime(p.ts) as unknown as Time, value: macdVals[i] }))
          .filter((d): d is LineData<Time> => d.value != null);
        if (macdData.length > 0) {
          macdLine = macdChart.addLineSeries({ color: '#38bdf8', lineWidth: 1 });
          macdLine.setData(macdData);
        }
        const sigData = activePrices
          .map((p, i) => ({ time: toIntradayTime(p.ts) as unknown as Time, value: sigVals[i] }))
          .filter((d): d is LineData<Time> => d.value != null);
        if (sigData.length > 0) {
          signalLine = macdChart.addLineSeries({ color: '#f59e0b', lineWidth: 1 });
          signalLine.setData(sigData);
        }
      } else {
        const histVals = visibleIndicators!.values['hist'];
        if (histVals) {
          histSeries = macdChart.addHistogramSeries({ priceScaleId: 'right' });
          histSeries.setData(
            visibleIndicators!.ts
              .map((t, i) => ({ time: toTime(t), value: histVals[i], color: (histVals[i] ?? 0) >= 0 ? '#22c55e' : '#ef4444' }))
              .filter(d => d.value != null) as { time: Time; value: number; color: string }[]
          );
        }
        const macdValsData = visibleIndicators!.values['macd'];
        if (macdValsData) {
          macdLine = macdChart.addLineSeries({ color: '#38bdf8', lineWidth: 1 });
          macdLine.setData(toLine(visibleIndicators!.ts, macdValsData));
        }
        const sigVals = visibleIndicators!.values['signal'];
        if (sigVals) {
          signalLine = macdChart.addLineSeries({ color: '#f59e0b', lineWidth: 1 });
          signalLine.setData(toLine(visibleIndicators!.ts, sigVals));
        }
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
  }, [activePrices, visibleIndicators, levels, prices, signalMarkers, gamePlanLevels, riskRewardLevels, showSMA20, showSMA50, showSMA200, showEMA20, showEMA50, showEMA200, showBB, showVol, showVWAP, showRSI, showMACD, showSignals, showFVG, showSR, show52W, showSwingPivots, swingPivots, drawings, isIntraday, intradayOverride, compareData, volumeProfileMode, volumeProfile, fixedRangeSelection]);

  // ── Fixed Range VP: click-to-pick start/end selection ───────────────────
  // Deliberately a SEPARATE, lightweight effect from the main chart-rebuild effect above —
  // subscribing/unsubscribing a click handler on the existing chart instance (via chartRef)
  // instead of recreating the whole chart on every click. No native drag-select gesture
  // exists in lightweight-charts (only click/dblclick/crosshair-move) — the standard pattern
  // (matching TradingView's own drawing tools and most community plugins) is two sequential
  // clicks: first click records the start bar (held in a ref, no rebuild), second click
  // finalizes fixedRangeSelection (state — this DOES trigger the main effect once, to
  // actually draw the new profile), using `param.logical` (a bar index) rather than pixel
  // coordinates so the selection is always bar-aligned.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || volumeProfileMode !== 'fixed' || fixedRangePickState === 'idle') return;

    const clickHandler = (param: { logical?: number }) => {
      if (param.logical == null) return;
      const rawIdx = Math.max(0, Math.min(activePrices.length - 1, Math.round(param.logical)));
      // T252-AUTO-SWING-PIVOTS: snap the raw click to the nearest real swing high/low within
      // a small tolerance, so a click that's a few bars off the actual extremum still lands
      // precisely on it — pixel-perfect manual clicking is no longer required.
      const snapped = nearestPivot(swingPivots, rawIdx, 3);
      const idx = snapped ? snapped.idx : rawIdx;
      if (fixedRangePickState === 'picking-start') {
        fixedRangeStartIdxRef.current = idx;
        setFixedRangePickState('picking-end');
      } else if (fixedRangePickState === 'picking-end') {
        const startIdx = fixedRangeStartIdxRef.current ?? idx;
        setFixedRangeSelection({ startIdx, endIdx: idx });
        setFixedRangePickState('idle');
      }
    };
    chart.subscribeClick(clickHandler);
    return () => chart.unsubscribeClick(clickHandler);
  }, [volumeProfileMode, fixedRangePickState, activePrices, swingPivots, chartInstanceVersion]);

  // ── T230-CHARTING-DRAWING-TOOLS: click-to-place horizontal lines / trendlines ──────
  // Same picking-state + separate-effect pattern as Fixed Range VP above. Needs the actual
  // PRICE at the click point (not just the bar index), which createPriceLine-style features
  // don't need — reads it via the candlestick series' own coordinateToPrice(), the standard
  // lightweight-charts v4 way to convert a click's pixel Y coordinate into a price value.
  useEffect(() => {
    const chart = chartRef.current;
    const candles = candlesRef.current;
    if (!chart || !candles || drawTool === 'off' || drawPickState === 'idle') return;

    const clickHandler = (param: { logical?: number; point?: { x: number; y: number } }) => {
      if (param.logical == null || param.point == null) return;
      const idx = Math.max(0, Math.min(activePrices.length - 1, Math.round(param.logical)));
      const price = candles.coordinateToPrice(param.point.y);
      if (price == null) return;

      if (drawTool === 'horizontal') {
        // Horizontal lines only need 1 click — no picking-end phase.
        const next = addDrawing(symbol, { id: nextDrawingId(), type: 'horizontal', price });
        setDrawings(next);
        setDrawPickState('idle');
        setDrawTool('off');
        return;
      }

      // Trendline — 2 clicks.
      if (drawPickState === 'picking-start') {
        drawStartRef.current = { idx, price };
        setDrawPickState('picking-end');
      } else if (drawPickState === 'picking-end') {
        const start = drawStartRef.current ?? { idx, price };
        const next = addDrawing(symbol, {
          id: nextDrawingId(), type: 'trendline',
          startIdx: start.idx, startPrice: start.price,
          endIdx: idx, endPrice: price,
        });
        setDrawings(next);
        setDrawPickState('idle');
        setDrawTool('off');
      }
    };
    chart.subscribeClick(clickHandler);
    return () => chart.unsubscribeClick(clickHandler);
  }, [drawTool, drawPickState, activePrices, symbol, chartInstanceVersion]);

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

      {/* Toolbar — grouped into dropdowns (was ~15 flat buttons, illegible once Volume
          Profile was added on top). Vol/VWAP stay as quick single-click toggles since
          they're the most frequently used; everything else groups into a dropdown. */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 px-3 py-2 border-b border-slate-800 text-xs">
        {btn(showVol,  'Vol',  () => setShowVol((v: boolean)  => !v), '#22c55e')}
        {btn(showVWAP, 'VWAP', () => setShowVWAP((v: boolean) => !v), '#a78bfa')}

        <ToolbarDropdown
          label="Indicators"
          options={[
            { key: 'sma20',  label: 'SMA 20',  checked: showSMA20,  onToggle: () => setShowSMA20(v => !v),  color: '#38bdf8' },
            { key: 'sma50',  label: 'SMA 50',  checked: showSMA50,  onToggle: () => setShowSMA50(v => !v),  color: '#f59e0b' },
            { key: 'sma200', label: 'SMA 200', checked: showSMA200, onToggle: () => setShowSMA200(v => !v), color: '#a78bfa' },
            { key: 'ema20',  label: 'EMA 20',  checked: showEMA20,  onToggle: () => setShowEMA20(v => !v),  color: '#34d399' },
            { key: 'ema50',  label: 'EMA 50',  checked: showEMA50,  onToggle: () => setShowEMA50(v => !v),  color: '#f472b6' },
            { key: 'ema200', label: 'EMA 200', checked: showEMA200, onToggle: () => setShowEMA200(v => !v), color: '#e879f9' },
            { key: 'bb',     label: 'Bollinger Bands', checked: showBB, onToggle: () => setShowBB(v => !v), color: '#6366f1' },
            { key: 'sig',    label: 'Signal markers',  checked: showSignals, onToggle: () => setShowSignals(v => !v), color: '#22c55e' },
            { key: 'fvg',    label: 'Fair Value Gaps', checked: showFVG, onToggle: () => setShowFVG(v => !v), color: '#22c55e',
              title: '3-candle price imbalance zones — price often retraces into these before continuing in the original direction. Solid = still open (unfilled); dotted = already filled by a later bar.' },
            { key: 'sr',     label: 'Support/Resistance', checked: showSR, onToggle: () => setShowSR(v => !v), color: '#22c55e',
              title: 'Pivot-clustered support (green) and resistance (red) price levels, off by default to keep the chart readable — turn on for extra context.' },
            { key: '52w',    label: '52W High/Low', checked: show52W, onToggle: () => setShow52W(v => !v), color: '#facc15',
              title: 'Trailing 52-week high and low reference lines, off by default to keep the chart readable — turn on for extra context.' },
            { key: 'pivots', label: 'Swing Pivots', checked: showSwingPivots, onToggle: () => setShowSwingPivots(v => !v), color: '#94a3b8',
              title: 'Small dot markers on real local swing highs/lows (daily chart only). Fixed Range VP\'s two clicks always snap to the nearest pivot within a few bars, whether or not this overlay is shown — turn it on to see exactly where those snap points are.' },
          ]}
        />

        <ToolbarDropdown
          label="Panels"
          options={[
            { key: 'rsi',  label: 'RSI (14)',       checked: showRSI,  onToggle: () => setShowRSI(v => !v),  color: '#f59e0b' },
            { key: 'macd', label: 'MACD (12,26,9)', checked: showMACD, onToggle: () => setShowMACD(v => !v), color: '#38bdf8' },
          ]}
        />

        <ToolbarDropdown
          label="Volume Profile"
          options={[
            { key: 'vp_session', label: 'Session VP', checked: volumeProfileMode === 'session', onToggle: () => setVolumeProfileMode(m => m === 'session' ? 'off' : 'session'), color: '#60a5fa', title: 'Profiles only today\'s bars — shows where volume concentrated during the current trading session.' },
            { key: 'vp_range',   label: 'Range VP',    checked: volumeProfileMode === 'range',   onToggle: () => setVolumeProfileMode(m => m === 'range' ? 'off' : 'range'),     color: '#fbbf24', title: 'Profiles the whole currently-visible chart window — shows where volume concentrated across the entire range you\'re looking at.' },
            { key: 'vp_fixed',   label: 'Fixed Range VP', checked: volumeProfileMode === 'fixed', onToggle: () => {
                if (volumeProfileMode === 'fixed') {
                  setVolumeProfileMode('off'); setFixedRangePickState('idle'); setFixedRangeSelection(null);
                } else {
                  setVolumeProfileMode('fixed'); setFixedRangePickState('picking-start'); setFixedRangeSelection(null);
                }
              }, color: '#a78bfa', title: 'Click a start point and an end point on the chart (e.g. a swing low and swing high) to profile only that exact range — matches TradingView\'s Fixed Range Volume Profile tool.' },
          ]}
        />

        {volumeProfileMode === 'fixed' && fixedRangePickState !== 'idle' && (
          <span className="px-2 py-1 rounded bg-violet-900/40 border border-violet-500/50 text-violet-300 text-xs">
            {fixedRangePickState === 'picking-start' ? 'Click a start point on the chart…' : 'Now click an end point…'}
          </span>
        )}
        {volumeProfileMode === 'fixed' && fixedRangePickState === 'idle' && fixedRangeSelection && (
          <button
            onClick={() => setFixedRangePickState('picking-start')}
            className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:border-violet-500 hover:text-violet-300 text-xs"
          >
            Re-pick range
          </button>
        )}

        {/* T230-CHARTING-DRAWING-TOOLS: horizontal line (1 click) + trendline (2 clicks),
            persisted per symbol via localStorage (see @/lib/chartDrawings.ts). */}
        <ToolbarDropdown
          label="Draw"
          options={[
            { key: 'draw_h', label: 'Horizontal Line', checked: drawTool === 'horizontal', onToggle: () => {
                if (drawTool === 'horizontal') { setDrawTool('off'); setDrawPickState('idle'); }
                else { setDrawTool('horizontal'); setDrawPickState('picking-start'); }
              }, color: '#facc15', title: 'Click once on the chart to drop a horizontal price line at that level.' },
            { key: 'draw_t', label: 'Trendline', checked: drawTool === 'trendline', onToggle: () => {
                if (drawTool === 'trendline') { setDrawTool('off'); setDrawPickState('idle'); }
                else { setDrawTool('trendline'); setDrawPickState('picking-start'); drawStartRef.current = null; }
              }, color: '#facc15', title: 'Click a start point, then an end point, to draw a line between two points on the chart.' },
          ]}
        />
        {drawTool !== 'off' && (
          <span className="px-2 py-1 rounded bg-amber-900/40 border border-amber-500/50 text-amber-300 text-xs">
            {drawTool === 'horizontal'
              ? 'Click the chart to place the line…'
              : (drawPickState === 'picking-start' ? 'Click a start point…' : 'Now click an end point…')}
          </span>
        )}
        {drawings.length > 0 && (
          <div className="flex items-center gap-1 flex-wrap">
            {drawings.map((d, i) => (
              <span
                key={d.id}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-900/20 border border-amber-700/40 text-amber-300 text-[11px]"
              >
                {d.type === 'horizontal' ? `Line @ $${d.price.toFixed(2)}` : `Trend #${i + 1}`}
                <button
                  onClick={() => setDrawings(removeDrawing(symbol, d.id))}
                  className="text-amber-400 hover:text-red-400 font-bold leading-none"
                  title="Delete this drawing"
                >
                  ×
                </button>
              </span>
            ))}
            <button
              onClick={() => { clearDrawings(symbol); setDrawings([]); }}
              className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:border-red-500 hover:text-red-300 text-xs"
              title="Remove all drawings on this chart"
            >
              Clear all
            </button>
          </div>
        )}

        {isIntraday && <span className="text-slate-600 ml-1">5-min</span>}

        {/* Legend */}
        <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-400">
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-sm bg-green-500 opacity-80" />Up</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-sm bg-red-500 opacity-80" />Down</span>
          {hasExtendedHoursBars && (
            <span
              className="flex items-center gap-1"
              title="Dimmed bars are pre-market (4:00-9:30am ET) or after-hours (4:00-8:00pm ET) trades — lower liquidity than the regular session, shown to surface earnings gaps and overnight moves."
            >
              <span className="inline-block w-2.5 h-2.5 rounded-sm bg-green-500 opacity-40" />Extended Hours
            </span>
          )}
          {showSMA20   && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-sky-400" />SMA 20</span>}
          {showSMA50   && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-amber-400" />SMA 50</span>}
          {showSMA200  && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-violet-400" />SMA 200</span>}
          {showEMA20   && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-emerald-400" />EMA 20</span>}
          {showEMA50   && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-pink-400" />EMA 50</span>}
          {showEMA200  && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-fuchsia-400" />EMA 200</span>}
          {showBB      && <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-indigo-400 opacity-70" />BB</span>}
          {showVWAP                   && <span className="flex items-center gap-1"><span className="inline-block w-4 border-t border-dashed border-violet-400" />VWAP</span>}
          {!isIntraday && levels?.support_resistance?.length ? <>
            <span className="flex items-center gap-1"><span className="inline-block w-4 border-t border-dashed border-green-500 opacity-70" />Support</span>
            <span className="flex items-center gap-1"><span className="inline-block w-4 border-t border-dashed border-red-500 opacity-70" />Resist.</span>
          </> : null}
        </div>
      </div>

      {/* Volume profile readout — POC / VAH / VAL. title= tooltips give a plain-English
          explanation on hover since there's no other in-app documentation for this feature. */}
      {volumeProfile && (
        <div
          className="flex flex-wrap items-center gap-4 px-3 py-1.5 text-xs font-mono bg-slate-900/60 border-b border-slate-800/50"
          title="Volume Profile: the blue horizontal bars on the chart show how much volume traded at each price level. Longer bar = more agreement on that price."
        >
          <span className="text-slate-500 not-italic font-sans">
            {volumeProfileMode === 'session' ? 'Session VP' : volumeProfileMode === 'fixed' ? 'Fixed Range VP' : 'Range VP'}
          </span>
          <span style={{ color: '#fbbf24' }} title="Point of Control — the single price level with the most volume traded. Often acts as a magnet/support-resistance level.">
            POC {f2(volumeProfile.poc)}
          </span>
          <span style={{ color: '#60a5fa' }} title="Value Area High/Low — together bracket the price range containing 70% of all traded volume. Price outside this band is comparatively under-traded.">
            VAH {f2(volumeProfile.vah)}
          </span>
          <span style={{ color: '#60a5fa' }} title="Value Area High/Low — together bracket the price range containing 70% of all traded volume. Price outside this band is comparatively under-traded.">
            VAL {f2(volumeProfile.val)}
          </span>
          {volumeProfile.hvn.length > 0 && (
            <span className="text-slate-500" title="High Volume Nodes — price levels with locally peaking volume. Tend to act as support/resistance since the market has 'agreed' on these prices before.">
              HVN {volumeProfile.hvn.slice(0, 3).map(v => v.toFixed(2)).join(', ')}
            </span>
          )}
        </div>
      )}

      {/* Crosshair readout — line values at cursor */}
      {(isIntraday ? activePrices.length > 0 : !!visibleIndicators) && (
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

      {showRSI && (
        <div className="border-t border-slate-700/60">
          <div className="flex items-center gap-5 px-3 py-1.5 bg-slate-900/40">
            <span className="text-slate-400 text-xs font-semibold tracking-wide">RSI (14)</span>
            <span style={{ color: '#f59e0b' }} className="text-xs font-mono">{rsiVal != null ? f2(rsiVal) : '—'}</span>
            <span className="text-slate-600 text-xs ml-auto">OB 70 · Mid 50 · OS 30</span>
          </div>
          <div ref={rsiRef} className="w-full" />
        </div>
      )}

      {showMACD && (
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
