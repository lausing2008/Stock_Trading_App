/**
 * Stock detail page (/stock/[symbol]) — per-symbol deep-dive with two AI features.
 *
 * AI provider: whichever is configured in Settings → AI Assistant
 *              (Claude or DeepSeek). Uses temperature=0.2 (default).
 *
 * Feature 1 — Game Plan (generateGamePlan)
 * ─────────────────────────────────────────
 * Triggered by the "Generate 10-Day Game Plan" button, shown only when the
 * AI signal is BUY or HOLD. Builds a context string containing:
 *   - Current price, intraday change %, currency
 *   - AI signal, confidence, bullish probability
 *   - K-Score breakdown (technical, momentum, value, growth)
 *   - Fair value estimate and analyst target / recommendation
 *   - Beta, sector, next earnings date
 *   - Nearest 2 support and 2 resistance levels (with strength)
 *   - Fibonacci retracement levels
 *   - Technical indicators: RSI, MACD, SMA50/200, ADX, Stoch RSI
 *   - VWAP(20d), weekly alignment, active chart patterns, earnings warning
 * System prompt: professional swing trader producing a strict JSON GamePlan
 * with 3 entry orders (two limit buys + one breakout), stop loss, take profit,
 * 3 catalysts, and a single-sentence risk statement. max_tokens=1024.
 *
 * Feature 2 — AI Chat (handleChat)
 * ──────────────────────────────────
 * Free-form Q&A panel in the sidebar. The same context string used for the
 * game plan is prepended as a system prompt on the first message, giving the
 * AI full knowledge of the stock's current state. Subsequent turns append to
 * the conversation history so the AI maintains context across the session.
 * max_tokens=2048 (default).
 */
import { useRouter } from 'next/router';
import { useState, useEffect, useRef, useMemo } from 'react';
import useSWR from 'swr';
import dynamic from 'next/dynamic';
import SignalCard from '@/components/SignalCard';
import PositionSizer from '@/components/PositionSizer';
import PeerCompareDrawer from '@/components/PeerCompareDrawer';
import NewsCard from '@/components/NewsCard';
import { api, type Overview, type Signal, type Prediction, type NewsItem, type LatestPrice, type WatchlistMeta, type PriceAlert, type FearGreed, type SignalAlertItem, type DividendData, type InstitutionalData, type RankingRow, type SignalHistoryPoint, type PatternSignal, type ResearchSummary, type FeatureImportanceResult, type OutcomesSummary, type QuarterlyRow } from '@/lib/api';
import { confluenceScoreFull, confluenceGrade } from '@/lib/confluence';
import { nearestActionableFvg, nearestPivotToFvg, classifyFvgVolumeContext } from '@/lib/fvgTradePlan';
import { detectSwingPivots } from '@/lib/swingPivots';
import { computeVolumeProfile } from '@/lib/volumeProfile';
import { mutate as globalMutate } from 'swr';
import { askAI, isAiConfigured, getAiProviderLabel, type AiMessage } from '@/lib/ai';
import { activeNewsSources, loadSettings } from '@/lib/settings';
import { getUsername } from '@/lib/auth';

function RefreshButton({ onClick, loading }: { onClick: () => void; loading: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      style={{
        display: 'flex', alignItems: 'center', gap: '6px',
        padding: '6px 13px', borderRadius: '6px',
        border: '1px solid rgba(148,163,184,0.15)',
        background: 'rgba(255,255,255,0.03)',
        color: loading ? '#818cf8' : '#64748b',
        cursor: loading ? 'not-allowed' : 'pointer',
        fontSize: '12px', transition: 'all 0.15s',
      }}
    >
      <span style={{ display: 'inline-block', fontSize: '14px', lineHeight: 1, animation: loading ? 'spin 0.8s linear infinite' : 'none' }}>↻</span>
      {loading ? 'Refreshing…' : 'Refresh'}
    </button>
  );
}

const PriceChart = dynamic(() => import('@/components/PriceChart'), { ssr: false });

const SIGNAL_DOT: Record<string, string> = { BUY: '#4ade80', HOLD: '#38bdf8', WAIT: '#fbbf24', SELL: '#f87171' };

function ConfidenceTrend({ history }: { history: SignalHistoryPoint[] }) {
  if (!history || history.length < 2) return null;
  const W = 260, H = 56, PAD = 4;
  const vals = history.map(h => h.confidence);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const pts = history.map((h, i) => {
    const x = PAD + (i / (history.length - 1)) * (W - PAD * 2);
    const y = H - PAD - ((h.confidence - min) / range) * (H - PAD * 2);
    return { x, y, signal: h.signal, conf: h.confidence };
  });
  const polyline = pts.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const last = pts[pts.length - 1];
  const prev = pts[pts.length - 2];
  const trend = last.conf - prev.conf;

  return (
    <div style={{ background: '#0a0f1e', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Confidence Trend ({history.length}d)</span>
        <span style={{ fontSize: 12, fontWeight: 700, color: trend >= 0 ? '#4ade80' : '#f87171' }}>
          {trend >= 0 ? '↑' : '↓'} {last.conf.toFixed(0)}%
        </span>
      </div>
      <svg width={W} height={H} style={{ display: 'block', overflow: 'visible' }}>
        {/* Reference lines */}
        <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="#1e293b" strokeWidth="1" />
        <line x1={PAD} y1={PAD} x2={W - PAD} y2={PAD} stroke="#1e293b" strokeWidth="1" strokeDasharray="3,3" />
        {/* Area fill */}
        <polygon
          points={`${pts[0].x},${H - PAD} ${polyline} ${pts[pts.length - 1].x},${H - PAD}`}
          fill="rgba(99,102,241,0.08)"
        />
        {/* Line */}
        <polyline points={polyline} fill="none" stroke="rgba(99,102,241,0.6)" strokeWidth="1.5" strokeLinejoin="round" />
        {/* Signal transition dots — only show on signal changes */}
        {pts.map((p, i) => {
          if (i === 0) return null;
          const prev = pts[i - 1];
          const changed = history[i].signal !== history[i - 1].signal;
          if (!changed && i !== pts.length - 1) return null;
          return (
            <circle key={i} cx={p.x} cy={p.y} r={i === pts.length - 1 ? 4 : 3}
              fill={SIGNAL_DOT[p.signal] ?? '#818cf8'}
              stroke="#0a0f1e" strokeWidth="1.5"
            />
          );
        })}
        {/* Min/max labels */}
        <text x={PAD} y={H - 1} fontSize="9" fill="#334155">{min.toFixed(0)}%</text>
        <text x={PAD} y={PAD + 8} fontSize="9" fill="#334155">{max.toFixed(0)}%</text>
      </svg>
    </div>
  );
}


export default function StockDetail() {
  const r = useRouter();
  const symbol = (r.query.symbol as string) ?? '';
  const pageStyle = ((r.query.style as string) ?? '').toUpperCase() || null;
  const u = getUsername();

  const { data, error, isLoading, mutate: mutateOverview } = useSWR<Overview>(
    symbol ? `overview-${symbol}` : null,
    () => api.overview(symbol),
  );
  const { data: allPrices } = useSWR<LatestPrice[]>(
    'latest-prices',
    () => api.latestPrices(),
    { refreshInterval: 60_000 },
  );
  const newsSources = typeof window !== 'undefined' ? activeNewsSources() : 'yfinance,google';
  const { data: news, mutate: mutateNews } = useSWR<NewsItem[]>(
    symbol ? `news-${symbol}-${newsSources}` : null,
    () => api.getNews(symbol, newsSources),
  );

  const [watched, setWatched] = useState(false);
  const [watchMenuOpen, setWatchMenuOpen] = useState(false);
  const [listStates, setListStates] = useState<Record<number, boolean> | null>(null);
  const [listPending, setListPending] = useState<number | null>(null);
  const watchMenuRef = useRef<HTMLDivElement>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [sigRefreshing, setSigRefreshing] = useState(false);
  const [fundRefreshing, setFundRefreshing] = useState(false);
  const [fullRefreshing, setFullRefreshing] = useState(false);
  const [fullRefreshMsg, setFullRefreshMsg] = useState('');
  const [mlResult, setMlResult] = useState<Prediction | null>(null);
  const [mlModel, setMlModel] = useState('xgboost');
  const [mlLoading, setMlLoading] = useState(false);
  const [mlError, setMlError] = useState('');
  const [mlTrainOpen, setMlTrainOpen] = useState(false);
  const [featureImportance, setFeatureImportance] = useState<FeatureImportanceResult | null>(null);
  const [fiLoading, setFiLoading] = useState(false);

  // Research summary (INT-1, INT-2, INT-6)
  const [researchSummary, setResearchSummary] = useState<ResearchSummary | null>(null);
  const [researchRefreshing, setResearchRefreshing] = useState(false);
  const [researchTriggerMsg, setResearchTriggerMsg] = useState<string | null>(null);

  // AI chat state
  const [aiMessages, setAiMessages] = useState<AiMessage[]>([]);
  const [aiInput, setAiInput] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState('');
  const [aiOpen, setAiOpen] = useState(false);
  const aiBottomRef = useRef<HTMLDivElement>(null);

  const { data: watchlists } = useSWR<WatchlistMeta[]>(`${u}:watchlists`, () => api.listWatchlists());
  const { data: fearGreed } = useSWR<FearGreed>('fear-greed', () => api.fearGreed(), { refreshInterval: 3_600_000 });
  const { data: signalAlerts, mutate: mutateSignalAlerts } = useSWR<SignalAlertItem[]>(
    `${u}:signal-alerts`, () => api.listSignalAlerts(),
  );
  // All-horizon signals for the consensus indicator
  // live=false → reads stored DB signal (matches signal filter); Refresh button uses live=true
  const { data: sigShort,  mutate: mutateSigShort }  = useSWR(symbol ? `sig-${symbol}-SHORT`  : null, () => api.signal(symbol, 'SHORT',  false), { revalidateOnFocus: false });
  const { data: sigSwing,  mutate: mutateSigSwing }  = useSWR(symbol ? `sig-${symbol}-SWING`  : null, () => api.signal(symbol, 'SWING',  false), { revalidateOnFocus: false });
  const { data: tuneStatus } = useSWR('tune-status', () => api.signalTuneStatus(), { refreshInterval: 5 * 60_000, revalidateOnFocus: false });
  const { data: sigLong,   mutate: mutateSigLong }   = useSWR(symbol ? `sig-${symbol}-LONG`   : null, () => api.signal(symbol, 'LONG',   false), { revalidateOnFocus: false });
  const { data: sigGrowth, mutate: mutateSigGrowth } = useSWR(symbol ? `sig-${symbol}-GROWTH` : null, () => api.signal(symbol, 'GROWTH', false), { revalidateOnFocus: false });
  const allHorizonSignals: { label: string; horizon: string; sig: typeof sigShort }[] = [
    { label: 'SHORT', horizon: 'SHORT', sig: sigShort },
    { label: 'SWING', horizon: 'SWING', sig: sigSwing },
    { label: 'LONG',  horizon: 'LONG',  sig: sigLong  },
    { label: 'GROWTH',horizon: 'GROWTH',sig: sigGrowth },
  ];
  const [signalAlertSaving, setSignalAlertSaving] = useState(false);
  const [signalAlertError, setSignalAlertError] = useState('');
  const [selectedHorizon, setSelectedHorizon] = useState<string>('SWING');
  // Sync to watchlist style when page loads (pageStyle comes from router.query, may arrive late)
  useEffect(() => { if (pageStyle) setSelectedHorizon(pageStyle); }, [pageStyle]);

  // T230-CHARTING-TIMEFRAMES: chart timeframe selector state
  const [chartTf, setChartTf] = useState<'5m' | '15m' | '1h' | '4h' | '1d'>('1d');
  const { data: tfPrices } = useSWR(
    symbol && chartTf !== '1d' && chartTf !== '5m' ? `prices-tf-${symbol}-${chartTf}` : null,
    () => api.pricesTf(symbol!, chartTf as '15m' | '1h' | '4h'),
    { revalidateOnFocus: false },
  );

  // T230-CHARTING-COMPARE-OVERLAY: comparison overlay state
  const [compareSymbol, setCompareSymbol] = useState<string | null>(null);
  const [compareInputOpen, setCompareInputOpen] = useState(false);
  const [compareCustomInput, setCompareCustomInput] = useState('');
  const { data: comparePrices } = useSWR(
    compareSymbol ? `compare-prices-${compareSymbol}` : null,
    () => api.getPrices(compareSymbol!, '1d', 400),
    { revalidateOnFocus: false },
  );

  // Game plan state
  type GamePlanEntry = { label: string; price: number; rationale: string };
  type GamePlan = {
    title: string;
    entries: GamePlanEntry[];
    stop_loss: { price: number; rationale: string };
    take_profit: { price: number; rationale: string } | null;
    catalysts: string[];
    risk: string;
  };
  const [gamePlan, setGamePlan] = useState<GamePlan | null>(null);
  const [gamePlanLoading, setGamePlanLoading] = useState(false);
  const [gamePlanError, setGamePlanError] = useState('');
  const [gamePlanOpen, setGamePlanOpen] = useState(true);
  const [savedToBoard, setSavedToBoard] = useState(false);
  const [savingToBoard, setSavingToBoard] = useState(false);
  const [saveBoardError, setSaveBoardError] = useState('');
  const [copied, setCopied] = useState(false);

  async function saveGamePlanToBoard() {
    if (!gamePlan || !symbol) return;
    setSavingToBoard(true);
    setSaveBoardError('');
    try {
      await api.createBoardPlan({
        symbol,
        stage: 'planning',
        game_plan: gamePlan as unknown as Record<string, unknown>,
        entry_price: gamePlan.entries[0]?.price ?? null,
        stop_loss: gamePlan.stop_loss?.price ?? null,
        take_profit: gamePlan.take_profit?.price ?? null,
        source: 'gameplan',
      });
      setSavedToBoard(true);
      globalMutate(`${u}:board`);
    } catch {
      // T237-FE2: this is a user-initiated action (clicking Save), not a background poll —
      // silently reverting the button to its idle state with no feedback let a failed save
      // look identical to a successful one, risking the user believing their plan was saved
      // when it wasn't (gamePlan itself resets to null on the next symbol navigation).
      setSaveBoardError('Failed to save — try again');
      setTimeout(() => setSaveBoardError(''), 4000);
    }
    setSavingToBoard(false);
  }

  function copyGamePlan() {
    if (!gamePlan || !symbol) return;
    const lines = [
      `📋 ${symbol} — ${gamePlan.title}`,
      '',
      'ENTRY STRATEGY',
      ...(gamePlan.entries ?? []).map((e: GamePlanEntry) => `  ${e.label}: $${e.price.toFixed(2)} — ${e.rationale}`),
      '',
      gamePlan.stop_loss ? `STOP LOSS: $${gamePlan.stop_loss.price.toFixed(2)} — ${gamePlan.stop_loss.rationale}` : '',
      gamePlan.take_profit ? `TAKE PROFIT: $${gamePlan.take_profit.price.toFixed(2)} — ${gamePlan.take_profit.rationale}` : '',
      '',
      'CATALYSTS',
      ...(gamePlan.catalysts ?? []).map((c: string) => `  › ${c}`),
      '',
      `KEY RISK: ${gamePlan.risk}`,
    ].filter(l => l !== undefined);
    navigator.clipboard.writeText(lines.join('\n')).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  const { data: allAlerts, mutate: mutateAlerts } = useSWR<PriceAlert[]>(
    `${u}:alerts`,
    () => api.listAlerts(),
    { refreshInterval: 30_000 },
  );
  const alerts = (allAlerts ?? []).filter(a => a.symbol === symbol);

  const { data: livePatterns } = useSWR<{ symbol: string; patterns: PatternSignal[]; as_of: string }>(
    symbol ? `patterns-${symbol}` : null,
    () => api.getPatterns(symbol as string),
    { revalidateOnFocus: false, dedupingInterval: 300_000 },
  );

  const [divOpen, setDivOpen] = useState(false);
  const [instOpen, setInstOpen] = useState(false);
  const [chainOpen, setChainOpen] = useState(false);
  const [chainExpiry, setChainExpiry] = useState<string | undefined>(undefined);
  const { data: optionsFlow } = useSWR(
    symbol ? `options-flow-${symbol}` : null,
    () => api.getOptionsFlow(symbol),
    { revalidateOnFocus: false },
  );
  // T230-DATA-OPTIONS-CHAIN: only fetched once the user opens the section — the full
  // strike matrix is a heavier fetch than options-flow's own top-3-per-side summary, and
  // most visits to a stock page don't need it.
  const { data: optionsChain } = useSWR(
    symbol && chainOpen ? `options-chain-${symbol}-${chainExpiry ?? 'default'}` : null,
    () => api.getOptionsChain(symbol, chainExpiry),
    { revalidateOnFocus: false },
  );
  const { data: dividendData } = useSWR<DividendData>(
    symbol && divOpen ? `dividends-${symbol}` : null,
    () => api.getDividends(symbol),
    { revalidateOnFocus: false },
  );
  const { data: instData } = useSWR<InstitutionalData>(
    symbol && instOpen ? `institutional-${symbol}` : null,
    () => api.getInstitutional(symbol),
    { revalidateOnFocus: false },
  );

  const { data: atrData } = useSWR(
    symbol ? `atr-${symbol}` : null,
    () => api.stockAtr(symbol),
    { revalidateOnFocus: false },
  );

  const { data: allRankings } = useSWR(
    'rankings-all',
    () => api.rankings(),
    { revalidateOnFocus: false },
  );

  // Fetch the signal matching the current page style (from watchlist ?style= param)
  const { data: shortSignal } = useSWR<Signal>(
    symbol && pageStyle === 'SHORT' ? `short-signal-${symbol}` : null,
    () => api.signal(symbol, 'SHORT'),
    { revalidateOnFocus: false },
  );
  const { data: longSignal } = useSWR<Signal>(
    symbol && pageStyle === 'LONG' ? `long-signal-${symbol}` : null,
    () => api.signal(symbol, 'LONG'),
    { revalidateOnFocus: false },
  );
  const { data: growthSignal } = useSWR<Signal>(
    symbol && pageStyle === 'GROWTH' ? `growth-signal-${symbol}` : null,
    () => api.signal(symbol, 'GROWTH'),
    { revalidateOnFocus: false },
  );

  const activeHorizon = selectedHorizon || 'SWING';

  const { data: signalHistory } = useSWR<SignalHistoryPoint[]>(
    symbol ? `signal-history-${symbol}-${activeHorizon}` : null,
    () => api.signalHistory(symbol, activeHorizon, 60),
    { revalidateOnFocus: false },
  );
  const { data: symbolOutcomes } = useSWR(
    symbol ? `symbol-outcomes-${symbol}-${activeHorizon}` : null,
    () => api.symbolOutcomes(symbol, activeHorizon),
    { revalidateOnFocus: false },
  );

  const { data: quarterly } = useSWR<QuarterlyRow[]>(
    symbol ? `quarterly-${symbol}` : null,
    () => api.quarterlyFinancials(symbol!),
    { revalidateOnFocus: false },
  );

  const [compareOpen, setCompareOpen] = useState(false);

  // Alert form state
  const [alertOpen, setAlertOpen] = useState<boolean>(false);
  const [alertCondition, setAlertCondition] = useState<string>('above');
  const [alertThreshold, setAlertThreshold] = useState<string>('');
  const [alertEmaPeriod, setAlertEmaPeriod] = useState<string>('20');
  const [alertEmail, setAlertEmail] = useState<string>('');
  const [alertNote, setAlertNote] = useState<string>('');
  const [alertRecurring, setAlertRecurring] = useState<boolean>(false);
  const [alertSaving, setAlertSaving] = useState<boolean>(false);
  const [alertMsg, setAlertMsg] = useState<string>('');

  // Pre-fill email from last used value
  useEffect(() => {
    const saved = typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') : null;
    if (saved) setAlertEmail(saved);
  }, []);

  const isEmaCondition = alertCondition === 'cross_above_ema' || alertCondition === 'cross_below_ema';
  const isNoThreshold = ['new_52wk_high', 'new_52wk_low', 'golden_cross', 'death_cross', 'macd_bullish_cross', 'rsi_oversold_bounce', 'double_bottom', 'breakout'].includes(alertCondition);

  async function createAlert() {
    if (!alertEmail) return;
    const threshold = isNoThreshold ? 0 : isEmaCondition ? parseInt(alertEmaPeriod) : parseFloat(alertThreshold);
    if (!isNoThreshold && !isEmaCondition && (!alertThreshold || isNaN(threshold))) return;
    setAlertSaving(true);
    setAlertMsg('');
    try {
      await api.createAlert({ symbol, condition: alertCondition, threshold, email: alertEmail, note: alertNote || undefined, recurring: alertRecurring });
      localStorage.setItem('stockai_alert_email', alertEmail);
      setAlertMsg('Alert set!');
      setAlertThreshold('');
      setAlertNote('');
      mutateAlerts();
      setTimeout(() => { setAlertMsg(''); setAlertOpen(false); }, 1500);
    } catch {
      setAlertMsg('Failed to save alert.');
    } finally {
      setAlertSaving(false);
    }
  }

  async function removeAlert(id: number) {
    await api.deleteAlert(id);
    mutateAlerts();
  }

  useEffect(() => {
    if (!symbol) return;
    // T237-FE1: guard against a stale response landing after the user has already navigated
    // to a different symbol (e.g. clicking a Sector Peer link) — Next.js's Pages Router reuses
    // this component instance across /stock/[symbol] navigations rather than remounting it.
    let ignore = false;
    api.isWatched(symbol).then(r => { if (!ignore) setWatched(r); }).catch(() => {});
    return () => { ignore = true; };
  }, [symbol]);

  useEffect(() => {
    setGamePlan(null);
    setGamePlanError('');
    setGamePlanOpen(true);
    setSavedToBoard(false);
    setAiMessages([]);
    setMlResult(null);
    setResearchSummary(null);
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    // T237-FE1: same stale-response guard as the isWatched effect above — a late-arriving
    // research summary for a previously-viewed symbol must not overwrite the current one.
    let ignore = false;
    api.getResearchSummary(symbol)
      .then(r => { if (!ignore) setResearchSummary(r); })
      .catch(() => { if (!ignore) setResearchSummary(null); });
    return () => { ignore = true; };
  }, [symbol]);


  useEffect(() => {
    if (!watchMenuOpen) return;
    function handler(e: MouseEvent) {
      if (watchMenuRef.current && !watchMenuRef.current.contains(e.target as Node)) {
        setWatchMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [watchMenuOpen]);

  async function openWatchMenu() {
    const opening = !watchMenuOpen;
    setWatchMenuOpen(opening);
    if (opening && watchlists?.length) {
      setListStates(null);
      const states: Record<number, boolean> = {};
      await Promise.all(watchlists.map(async (wl: WatchlistMeta) => {
        try {
          const items = await api.listWatchlist(wl.id);
          states[wl.id] = items.some(i => i.symbol === symbol);
        } catch { states[wl.id] = false; }
      }));
      setListStates(states);
      setWatched(Object.values(states).some(v => v));
    }
  }

  async function toggleListItem(listId: number) {
    if (!listStates) return;
    setListPending(listId);
    try {
      const inList = listStates[listId];
      if (inList) {
        await api.removeFromWatchlist(symbol, listId);
      } else {
        await api.addToWatchlist(symbol, listId);
      }
      const newStates = { ...listStates, [listId]: !inList };
      setListStates(newStates);
      setWatched(Object.values(newStates).some(v => v));
    } finally {
      setListPending(null);
    }
  }

  async function handleRefresh() {
    setRefreshing(true);
    await Promise.all([mutateOverview(), mutateNews()]);
    setRefreshing(false);
  }

  async function handleRefreshSignal() {
    setSigRefreshing(true);
    try {
      // Recompute live + persist to DB, then reload all stored signals
      await api.refreshSignal(symbol);
      await Promise.all([
        mutateOverview(),
        mutateSigShort(), mutateSigSwing(), mutateSigLong(), mutateSigGrowth(),
      ]);
    } catch { /* non-fatal */ }
    setSigRefreshing(false);
  }

  async function handleFullRefresh() {
    setFullRefreshing(true);
    setFullRefreshMsg('Re-fetching full price history…');
    try {
      await api.ingest([symbol], true);
      setFullRefreshMsg('Reloading chart…');
      await Promise.all([mutateOverview(), mutateNews()]);
      setFullRefreshMsg('Done');
      setTimeout(() => setFullRefreshMsg(''), 3000);
    } catch {
      setFullRefreshMsg('Ingest failed — check backend logs');
      setTimeout(() => setFullRefreshMsg(''), 4000);
    } finally {
      setFullRefreshing(false);
    }
  }

  async function generateGamePlan() {
    if (!data) return;
    setGamePlanLoading(true);
    setGamePlanError('');
    setGamePlan(null);
    setGamePlanOpen(true);

    const lp = allPrices?.find(p => p.symbol === symbol);
    const currentPrice = lp?.price ?? (data.prices?.at(-1)?.close ?? null);
    // Use the signal for the active horizon tab, not just the default SWING signal
    const sig = allHorizonSignals.find(h => h.horizon === selectedHorizon)?.sig
      ?? (selectedHorizon === 'SWING' ? data.signal : data.signal);
    const rank = data.ranking;
    const fund = data.fundamentals;
    const levels = data.levels;

    const tradeStyle = (sig?.horizon ?? 'SWING').toUpperCase() as 'SHORT' | 'SWING' | 'LONG' | 'GROWTH';
    const styleLabels: Record<string, string> = {
      SHORT: 'Short-Term (1–5 Days)',
      SWING: 'Swing (5–30 Days)',
      LONG: 'Position (1–12 Months)',
      GROWTH: 'Growth Momentum (10–20 Days)',
    };
    const styleRules: Record<string, string> = {
      SHORT: `TRADING STYLE: SHORT-TERM (1–5 days)
- Entry 1: 0.5% below current — tight entry for quick momentum trade
- Entry 2: 1.5% below current — secondary entry on minor intraday dip
- Breakout entry: 1% above current
- Stop loss: 3% below current — tight stop, this is a momentum play
- Take profit: 5% above current or nearest resistance (whichever is closer)
- Prioritise speed of execution over perfect fill; note this is a momentum trade`,
      SWING: `TRADING STYLE: SWING (5–30 days)
- Entry 1: at or just above the nearest strong support below current price (typically 1.5–2% below)
- Entry 2: at a deeper support or fibonacci level for averaging down (typically 3.5–4% below)
- Breakout entry: above the nearest resistance level — take 50% size
- Stop loss: just below the lowest entry support — a close below invalidates the setup (typically 5.5% below)
- Take profit: analyst target or +12% from current`,
      LONG: `TRADING STYLE: POSITION / LONG (1–12 months)
- Entry 1: 2% below current — build initial position, patient entry
- Entry 2: 5% below current — add on deeper pullback to accumulate over days/weeks
- Breakout entry: 3% above current — only if fundamental thesis strengthens
- Stop loss: 10% below current — wide stop allows for normal volatility; weekly close below invalidates thesis
- Take profit: analyst mean/high target or +25% from current (position trade requires large reward/risk)
- Note this is a multi-month hold; size for volatility and manage around earnings`,
      GROWTH: `TRADING STYLE: GROWTH MOMENTUM (10–20 days)
- Entry 1: at or just above nearest support below current price (typically 1.5–2.5% below)
- Entry 2: at a deeper support level for averaging down (typically 4–5% below)
- Breakout entry: above nearest resistance — take 60% size on confirmed momentum
- Stop loss: just below the lowest entry support (typically 6% below) — wider than SWING to tolerate momentum volatility
- Take profit: +15–20% from current or nearest resistance; consider partial exit at +10%
- Note this is a higher-volatility momentum style; size accordingly and trail stop after +8% gain`,
    };
    const planLabel = styleLabels[tradeStyle] ?? tradeStyle;
    const styleInstruction = styleRules[tradeStyle] ?? styleRules['SWING'];

    // Sort supports/resistances by distance from current price
    const supports = (levels?.support_resistance ?? [])
      .filter(l => currentPrice == null || l.price < currentPrice)
      .sort((a, b) => b.price - a.price)
      .slice(0, 3);
    const resistances = (levels?.support_resistance ?? [])
      .filter(l => currentPrice == null || l.price > currentPrice)
      .sort((a, b) => a.price - b.price)
      .slice(0, 2);
    const fib = levels?.fibonacci ?? {};

    const reasons = (sig as unknown as { reasons?: Record<string, unknown> })?.reasons ?? {};

    const context = `SYMBOL: ${symbol}
CURRENT PRICE: ${currentPrice != null ? currentPrice.toFixed(2) : 'N/A'} ${lp ? `(${lp.change_pct != null ? (lp.change_pct >= 0 ? '+' : '') + lp.change_pct.toFixed(2) + '%' : ''} today)` : ''}
CURRENCY: ${lp?.currency ?? 'USD'}

AI SIGNAL: ${sig?.signal ?? 'N/A'} | CONFIDENCE: ${sig?.confidence?.toFixed(0) ?? '?'}% | BULLISH PROB: ${sig?.bullish_probability != null ? (sig.bullish_probability * 100).toFixed(0) : '?'}%
K-SCORE: ${rank?.score?.toFixed(0) ?? '?'} | TECHNICAL: ${rank?.technical?.toFixed(0) ?? '?'} | MOMENTUM: ${rank?.momentum?.toFixed(0) ?? '?'} | VALUE: ${rank?.value?.toFixed(0) ?? '?'}
FAIR VALUE (K-Score intrinsic estimate): ${rank?.fair_price != null ? `$${rank.fair_price.toFixed(2)}${currentPrice != null ? ` — ${rank.fair_price > currentPrice ? `+${(((rank.fair_price - currentPrice) / currentPrice) * 100).toFixed(1)}% above current (valid take-profit candidate)` : `${(((rank.fair_price - currentPrice) / currentPrice) * 100).toFixed(1)}% below current (stock overvalued; do NOT use as take-profit)`}` : ''}` : 'N/A'}
ANALYST TARGET (mean): ${fund?.target_price != null ? `$${fund.target_price.toFixed(2)}${currentPrice != null ? (fund.target_price <= currentPrice ? ' — BELOW CURRENT PRICE, do NOT use as take-profit' : ` — +${(((fund.target_price - currentPrice) / currentPrice) * 100).toFixed(1)}% above current (valid take-profit candidate)`) : ''}` : 'N/A'}
ANALYST TARGET (high): ${fund?.target_high != null ? `$${fund.target_high.toFixed(2)}${currentPrice != null ? (fund.target_high <= currentPrice ? ' — BELOW CURRENT PRICE, do NOT use as take-profit' : ` — +${(((fund.target_high - currentPrice) / currentPrice) * 100).toFixed(1)}% above current (valid take-profit candidate)`) : ''}` : 'N/A'}
RECOMMENDATION: ${fund?.recommendation?.toUpperCase() ?? 'N/A'} | # ANALYSTS: ${fund?.number_of_analysts ?? '?'}
BETA: ${fund?.beta?.toFixed(2) ?? 'N/A'} | SECTOR: ${data.price?.sector ?? 'N/A'}
NEXT EARNINGS: ${fund?.next_earnings_date ?? 'N/A'}${fund?.days_to_earnings != null ? ` (${fund.days_to_earnings}d away)` : ''}

SUPPORT LEVELS (nearest first, below current price):
${supports.length ? supports.map(s => `  $${s.price.toFixed(2)} (strength ${s.strength.toFixed(0)})`).join('\n') : '  None identified'}

RESISTANCE LEVELS (nearest first, above current price):
${resistances.length ? resistances.map(r => `  $${r.price.toFixed(2)} (strength ${r.strength.toFixed(0)})`).join('\n') : '  None identified'}

FIBONACCI RETRACEMENTS:
${Object.entries(fib).map(([k, v]) => `  ${k}%: $${(v as number).toFixed(2)}`).join('\n') || '  Not available'}

TECHNICAL INDICATORS:
  RSI(14): ${reasons.rsi != null ? Number(reasons.rsi).toFixed(1) : '?'}
  MACD hist: ${reasons.macd_hist != null ? Number(reasons.macd_hist).toFixed(3) : '?'} (${reasons.macd_momentum_fading ? 'fading ⚠ — momentum exhausting' : reasons.macd_hist_expanding !== undefined ? (reasons.macd_hist_expanding ? 'expanding' : 'contracting') : (reasons.macd_rising ? 'rising' : 'falling')})${reasons.macd_hist_slope != null ? ` slope: ${Number(reasons.macd_hist_slope).toFixed(4)}` : ''}
  GC spread: ${reasons.gc_spread_pct != null ? `${(Number(reasons.gc_spread_pct)*100).toFixed(1)}%` : '?'} (${reasons.gc_spread_expanding === true ? 'expanding' : reasons.gc_spread_expanding === false ? 'narrowing ⚠' : 'unknown'})
  Above SMA50: ${reasons.trend_above_sma50 ? 'Yes' : 'No'} | SMA50>SMA200: ${reasons.sma50_above_sma200 ? 'Yes' : 'No'}
  ADX: ${reasons.adx != null ? Number(reasons.adx).toFixed(1) : '?'} | Stoch RSI %K: ${reasons.stoch_rsi_k != null ? (Number(reasons.stoch_rsi_k) * 100).toFixed(0) : '?'}%
  VWMA(20d): ${reasons.price_above_vwap === true ? 'Price ABOVE VWMA' : reasons.price_above_vwap === false ? 'Price BELOW VWMA' : 'N/A'}${reasons.vwma_20 != null ? ` ($${Number(reasons.vwma_20).toFixed(2)})` : ''}
  Weekly alignment: ${reasons.weekly_alignment === true ? 'CONFIRMED (daily+weekly agree)' : reasons.weekly_alignment === false ? 'CONFLICT (timeframes diverge)' : 'N/A'} | Weekly TA score: ${reasons.weekly_ta_score != null ? (Number(reasons.weekly_ta_score) * 100).toFixed(0) : '?'}
  Active chart patterns: ${(reasons.active_patterns as string[] | undefined)?.length ? (reasons.active_patterns as string[]).join(', ') : 'none'}
  Earnings warning: ${reasons.earnings_warning ?? 'none'}${reasons.days_to_earnings != null ? ` (${reasons.days_to_earnings}d to earnings)` : ''}
  News sentiment (7d): ${reasons.news_sentiment != null ? `${Number(reasons.news_sentiment).toFixed(0)}/100` : 'N/A'}${reasons.news_sentiment_flag ? ` — ${String(reasons.news_sentiment_flag).replace(/_/g, ' ')}` : ''}
  Market regime: ${reasons.market_regime ?? 'unknown'}`;

    const systemPrompt = `You are a professional trader generating a concrete trade plan for a stock that has just received a BUY AI signal.

${styleInstruction}

ADDITIONAL RULES:
- Use the exact support/resistance/fibonacci levels provided — pick the most relevant ones for entry and stop placement
- Take profit: choose the BEST of these — in order of preference: (1) analyst mean or high target if labelled "valid take-profit candidate" and meaningfully above current price (>3%); (2) K-Score fair value if labelled "valid take-profit candidate"; (3) nearest resistance above current price. NEVER use any target labelled "BELOW CURRENT PRICE"
- Catalysts: 3 bullets, each ≤12 words, specific (mention earnings date, sector, analyst coverage)
- Risk: single sentence naming the biggest concrete threat (earnings, macro, overbought, etc.)
- Use the same currency as the stock (check CURRENCY field)
- CRITICAL PRICE CONSTRAINTS: entry prices MUST be below current price; stop loss MUST be below all entry prices; take profit MUST be above current price — violating any of these makes the plan unusable

Return ONLY valid JSON — no markdown, no prose:
{
  "title": "Game Plan — ${planLabel} — SYMBOL",
  "entries": [
    { "label": "Limit buy — 50%", "price": 0.00, "rationale": "..." },
    { "label": "Limit buy — 50%", "price": 0.00, "rationale": "..." },
    { "label": "Breakout entry — 50%", "price": 0.00, "rationale": "..." }
  ],
  "stop_loss": { "price": 0.00, "rationale": "..." },
  "take_profit": { "price": 0.00, "rationale": "..." },
  "catalysts": ["...", "...", "..."],
  "risk": "..."
}`;

    try {
      const raw = await askAI([{ role: 'user', content: context }], systemPrompt, 1024);
      // Depth-counter extraction — avoids greedy regex over-extending into
      // trailing Claude commentary that contains } characters.
      const objStart = raw.indexOf('{');
      if (objStart === -1) throw new Error('AI response did not contain JSON.');
      let depth = 0, objEnd = -1;
      for (let i = objStart; i < raw.length; i++) {
        if (raw[i] === '{') depth++;
        else if (raw[i] === '}') { depth--; if (depth === 0) { objEnd = i; break; } }
      }
      if (objEnd === -1) throw new Error('AI response contained malformed JSON.');
      const parsed = JSON.parse(raw.slice(objStart, objEnd + 1)) as GamePlan;
      if (parsed.take_profit && currentPrice != null && parsed.take_profit.price <= currentPrice) {
        throw new Error(`Take profit $${parsed.take_profit.price.toFixed(2)} is at or below current price $${currentPrice.toFixed(2)} — regenerate.`);
      }
      setGamePlan(parsed);
    } catch (e: unknown) {
      setGamePlanError(e instanceof Error ? e.message : 'Failed to generate game plan.');
    } finally {
      setGamePlanLoading(false);
    }
  }

  async function handleFundRefresh() {
    setFundRefreshing(true);
    try {
      await api.refreshFundamentals(symbol);
      await mutateOverview();
    } finally {
      setFundRefreshing(false);
    }
  }

  async function handleResearchRefresh() {
    setResearchRefreshing(true);
    setResearchTriggerMsg(null);
    try {
      const res = await api.triggerResearch(symbol);
      setResearchTriggerMsg(res?.status === 'triggered' ? 'Refresh queued — report updates in ~30s' : 'Refresh queued');
    } catch {
      setResearchTriggerMsg('Trigger failed');
    }
    setResearchRefreshing(false);
  }

  async function runML() {
    setMlLoading(true);
    setMlError('');
    try {
      const result = await api.predict(symbol, mlModel);
      setMlResult(result);
    } catch {
      setMlError('Model not trained yet. Train first.');
    } finally {
      setMlLoading(false);
    }
  }

  async function trainML() {
    setMlLoading(true);
    setMlError('');
    try {
      await api.trainModel(symbol, mlModel);
      setMlError('Training started — takes ~30s, then run predict.');
    } finally {
      setMlLoading(false);
    }
  }

  async function sendAiMessage() {
    const text = aiInput.trim();
    if (!text || aiLoading) return;
    setAiError('');
    const userMsg: AiMessage = { role: 'user', content: text };
    const updated = [...aiMessages, userMsg];
    setAiMessages(updated);
    setAiInput('');
    setAiLoading(true);
    setTimeout(() => aiBottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
    try {
      const systemCtx = [
        `You are a financial analyst assistant for the stock ${symbol} (${(data as Overview & { price?: { name?: string } })?.price?.name ?? symbol}).`,
        `Current price: ${data?.price ? JSON.stringify(data.price) : 'N/A'}`,
        data?.signal ? `Signal: ${data.signal.signal} (${((data.signal.bullish_probability ?? 0) * 100).toFixed(0)}% bullish, ${(data.signal.confidence ?? 0).toFixed(0)}% confidence)` : '',
        data?.ranking ? `K-Score: ${data.ranking.score?.toFixed(0)}, Fair Value: $${data.ranking.fair_price?.toFixed(2)}` : '',
        `Recent headlines: ${(news ?? []).slice(0, 5).map(n => n.title).join(' | ')}`,
        'Be concise, data-driven, and reference the above context in your answers.',
      ].filter(Boolean).join('\n');
      const reply = await askAI(updated, systemCtx);
      setAiMessages(prev => [...prev, { role: 'assistant', content: reply }]);
    } catch (e) {
      setAiError(e instanceof Error ? e.message : 'AI request failed');
    } finally {
      setAiLoading(false);
      setTimeout(() => aiBottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 100);
    }
  }

  const priceMap = useMemo(() => {
    const m: Record<string, LatestPrice> = {};
    for (const p of allPrices ?? []) m[p.symbol] = p;
    return m;
  }, [allPrices]);

  const currentSector: string | null = data?.price?.sector ?? null;
  const currentMarket: string | null = data?.price?.market ?? null;

  const sectorPeers = useMemo((): RankingRow[] => {
    if (!allRankings || !currentSector) return [];
    return allRankings.rankings
      .filter((r: RankingRow) => r.symbol !== symbol && r.sector === currentSector && (!currentMarket || r.market === currentMarket))
      .sort((a: RankingRow, b: RankingRow) => (b.score ?? 0) - (a.score ?? 0))
      .slice(0, 3);
  }, [allRankings, currentSector, currentMarket, symbol]);

  const compareRows = useMemo((): RankingRow[] => {
    const _ranking = data?.ranking;
    if (!_ranking) return sectorPeers;
    const currentRow: RankingRow = {
      symbol: symbol as string,
      name: data?.price?.name ?? (symbol as string),
      name_zh: data?.price?.name_zh ?? null,
      market: currentMarket ?? '',
      sector: currentSector ?? null,
      score: _ranking.score,
      technical: _ranking.technical,
      momentum: _ranking.momentum,
      value: _ranking.value,
      growth: _ranking.growth,
      volatility: _ranking.volatility,
      fair_price: _ranking.fair_price ?? null,
      relative_strength: _ranking.relative_strength ?? null,
    };
    return [currentRow, ...sectorPeers];
  }, [data?.ranking, sectorPeers, symbol, data?.price, currentMarket, currentSector]);

  if (isLoading) return <div className="text-slate-400 p-4">Loading…</div>;
  if (error || !data) return <div className="text-slate-300 p-4">Error loading {symbol}.</div>;

  const liveQuote = allPrices?.find(p => p.symbol === symbol) ?? null;
  const curPrice: number | null = liveQuote?.price ?? (data.prices && data.prices.length > 0 ? data.prices[data.prices.length - 1].close : null);
  const changePct: number | null = liveQuote?.change_pct ?? null;
  const prevClose: number | null = liveQuote?.prev_close ?? null;

  const ranking = data.ranking;
  const fairUpside = (ranking?.fair_price != null && curPrice != null)
    ? ((ranking.fair_price - curPrice) / curPrice) * 100
    : null;

  // Risk metrics (annualised, 1-year rolling) — calculated from existing price data
  const riskMetrics1y: { sharpe: number; sortino: number; maxDrawdown: number } | null = (() => {
    const prices = data.prices;
    if (!prices || prices.length < 30) return null;
    const closes = prices.map((p: { close: number }) => p.close);
    const cutoff = closes.length - 252;
    const window = closes.slice(Math.max(0, cutoff));
    if (window.length < 20) return null;
    const returns: number[] = [];
    for (let i = 1; i < window.length; i++) {
      returns.push((window[i] - window[i - 1]) / window[i - 1]);
    }
    const n = returns.length;
    const mean = returns.reduce((a, b) => a + b, 0) / n;
    const std = Math.sqrt(returns.reduce((a, b) => a + (b - mean) ** 2, 0) / n);
    // Sortino: downside std (returns below 0 only)
    const downside = returns.filter(r => r < 0);
    const downsideStd = downside.length > 1
      ? Math.sqrt(downside.reduce((a, b) => a + b ** 2, 0) / n)
      : std;
    // Max drawdown over the window
    let peak = window[0], maxDD = 0;
    for (const p of window) {
      if (p > peak) peak = p;
      const dd = (peak - p) / peak;
      if (dd > maxDD) maxDD = dd;
    }
    if (std === 0) return null;
    return {
      sharpe:      parseFloat(((mean / std) * Math.sqrt(252)).toFixed(2)),
      sortino:     parseFloat(((mean / downsideStd) * Math.sqrt(252)).toFixed(2)),
      maxDrawdown: parseFloat((maxDD * 100).toFixed(1)),
    };
  })();
  const sharpeRatio1y = riskMetrics1y?.sharpe ?? null;

  const levels = data.levels;
  const srLevels = levels?.support_resistance ?? [];
  const fibLevels = levels?.fibonacci ?? {};

  return (
    <div className="space-y-4">
      {/* Back button */}
      <div>
        <button
          onClick={() => r.back()}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '6px',
            padding: '5px 12px', borderRadius: '6px', fontSize: '13px',
            border: '1px solid #1e293b', background: 'transparent',
            color: '#64748b', cursor: 'pointer', transition: 'all 0.15s',
          }}
        >
          ← Back
        </button>
      </div>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-6">
          <div>
            <h1 className="text-2xl font-bold">{symbol}</h1>
            <div className="text-sm text-slate-400">{(data.price as { name?: string })?.name}</div>
            {(data.price as { name_zh?: string | null })?.name_zh && (
              <div className="text-xs text-slate-500" style={{ marginTop: '1px' }}>
                {(data.price as { name_zh?: string | null })?.name_zh}
              </div>
            )}
            <div className="flex gap-3 mt-1 text-xs text-slate-500">
              {data.price && <span>{(data.price as { market?: string })?.market} · {(data.price as { exchange?: string })?.exchange}</span>}
              {data.price && <span>{(data.price as { sector?: string })?.sector}</span>}
            </div>
          </div>
          {/* Live price card */}
          <div style={{ textAlign: 'center', padding: '10px 20px', borderRadius: '8px', border: '1px solid #1e293b', background: 'rgba(255,255,255,0.02)', minWidth: '110px' }}>
            <div style={{ fontSize: '10px', color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '2px' }}>
              {liveQuote ? 'Live Price' : 'Last Close'}
            </div>
            <div style={{ fontSize: '24px', fontWeight: 800, color: '#f1f5f9', lineHeight: 1.1 }}>
              {curPrice != null ? `$${curPrice.toFixed(2)}` : '—'}
            </div>
            {changePct != null && (
              <div style={{ fontSize: '13px', fontWeight: 700, marginTop: '2px', color: changePct >= 0 ? '#4ade80' : '#f87171' }}>
                {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%
              </div>
            )}
            {prevClose != null && (
              <div style={{ fontSize: '10px', color: '#475569', marginTop: '1px' }}>
                Prev ${prevClose.toFixed(2)}
              </div>
            )}
          </div>

          {/* RVOL chip (T220-I) — derived from liveQuote volume vs avg_volume */}
          {liveQuote?.volume != null && liveQuote?.avg_volume != null && liveQuote.avg_volume > 0 && (() => {
            const rvol = liveQuote.volume! / liveQuote.avg_volume!;
            if (rvol < 1.5) return null;
            const isHigh = rvol >= 2.0;
            return (
              <div
                title={`Relative volume ${rvol.toFixed(2)}× 20-day avg — ${isHigh ? 'unusual institutional activity detected' : 'above-average volume'}`}
                style={{
                  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                  padding: '6px 12px', borderRadius: 8,
                  border: `1px solid ${isHigh ? 'rgba(239,68,68,0.4)' : 'rgba(251,191,36,0.35)'}`,
                  background: isHigh ? 'rgba(239,68,68,0.08)' : 'rgba(251,191,36,0.08)',
                  minWidth: 72,
                }}
              >
                <span style={{ fontSize: 10, fontWeight: 600, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 2 }}>RVOL</span>
                <span style={{ fontSize: 18, fontWeight: 800, color: isHigh ? '#f87171' : '#fbbf24', lineHeight: 1.1 }}>{rvol.toFixed(1)}×</span>
                <span style={{ fontSize: 9, color: isHigh ? '#f87171' : '#fbbf24', marginTop: 1 }}>{isHigh ? 'Unusual vol' : 'Elevated'}</span>
              </div>
            );
          })()}

          {ranking?.fair_price != null && (
            <div className="rounded-md border border-indigo-800 bg-indigo-950/40 px-4 py-2 text-center" style={{ minWidth: 120 }}>
              <div className="text-xs text-indigo-400 font-medium mb-0.5">Fair Value</div>
              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'center', gap: 6 }}>
                <span className="text-xl font-bold text-indigo-300">${ranking.fair_price.toFixed(2)}</span>
                {fairUpside != null && (
                  <span style={{ fontSize: 13, fontWeight: 800, color: fairUpside > 0 ? '#4ade80' : '#f87171' }}>
                    {fairUpside >= 0 ? '+' : ''}{fairUpside.toFixed(1)}%
                  </span>
                )}
              </div>
              {fairUpside != null && (
                <div style={{ fontSize: 11, fontWeight: 600, color: fairUpside > 0 ? '#4ade80' : '#f87171', marginTop: 1 }}>
                  {fairUpside > 0 ? 'upside' : 'overvalued'}
                </div>
              )}
              {ranking?.score != null && (
                <div className="text-xs text-slate-500 mt-0.5">K-Score {ranking.score.toFixed(0)}</div>
              )}
            </div>
          )}
          {(() => {
            // FE-A1: was hardcoded to sigSwing regardless of the active horizon tab — switching
            // to LONG/SHORT/GROWTH left the header badge still showing the SWING signal, visibly
            // contradicting the tab content below it. Same lookup pattern already used by the
            // sidebar's tabbed horizon switcher (see allHorizonSignals.find below, ~line 1904).
            const badgeSig = allHorizonSignals.find(h => h.horizon === selectedHorizon)?.sig
              ?? (selectedHorizon === 'SWING' ? data.signal : null);
            if (!badgeSig) return null;
            const s = badgeSig.signal;
            const borderCls = s === 'BUY' ? 'border-green-800 bg-green-950/40' : s === 'SELL' ? 'border-red-800 bg-red-950/40' : s === 'WAIT' ? 'border-orange-800 bg-orange-950/40' : 'border-yellow-800 bg-yellow-950/40';
            const labelCls  = s === 'BUY' ? 'text-green-400'  : s === 'SELL' ? 'text-red-400'  : s === 'WAIT' ? 'text-orange-400'  : 'text-yellow-400';
            const valueCls  = s === 'BUY' ? 'text-green-300'  : s === 'SELL' ? 'text-red-300'  : s === 'WAIT' ? 'text-orange-300'  : 'text-yellow-300';
            return (
              <div className={`rounded-md border px-4 py-2 text-center ${borderCls}`}>
                <div className={`text-xs font-medium mb-0.5 ${labelCls}`}>AI Signal</div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                  <div className={`text-xl font-bold ${valueCls}`}>{s}</div>
                  {(s === 'HOLD' || s === 'WAIT') && badgeSig.bullish_probability != null && (() => {
                    const bp = badgeSig.bullish_probability;
                    if (bp >= 0.55 && bp < 0.65) return (
                      <span style={{ fontSize: 10, fontWeight: 700, color: '#fbbf24', background: 'rgba(251,191,36,0.12)', border: '1px solid rgba(251,191,36,0.35)', padding: '2px 6px', borderRadius: 4 }}
                            title={`Near BUY — ${(bp * 100).toFixed(1)}% bullish probability`}>~BUY</span>
                    );
                    if (bp > 0.35 && bp <= 0.45) return (
                      <span style={{ fontSize: 10, fontWeight: 700, color: '#f87171', background: 'rgba(248,113,113,0.12)', border: '1px solid rgba(248,113,113,0.35)', padding: '2px 6px', borderRadius: 4 }}
                            title={`Near SELL — ${(bp * 100).toFixed(1)}% bullish probability`}>~SELL</span>
                    );
                    return null;
                  })()}
                </div>
                <div className="text-xs text-slate-500 mt-0.5">{((badgeSig.bullish_probability ?? 0) * 100).toFixed(0)}% bullish · stored {selectedHorizon}</div>
              </div>
            );
          })()}
          {/* Risk Metrics — Sharpe / Sortino / Max Drawdown */}
          {riskMetrics1y !== null && (
            <div style={{ display: 'flex', gap: 6 }}>
              {(() => {
                const sr = riskMetrics1y.sharpe;
                const color = sr >= 1.5 ? '#4ade80' : sr >= 1.0 ? '#86efac' : sr >= 0.5 ? '#fbbf24' : '#f87171';
                return (
                  <div style={{ padding: '8px 12px', borderRadius: '8px', border: `1px solid ${color}33`, background: `${color}08`, textAlign: 'center', minWidth: '72px' }}>
                    <div style={{ fontSize: '9px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Sharpe 1Y</div>
                    <div style={{ fontSize: '16px', fontWeight: 800, color, marginTop: '2px' }}>{sr.toFixed(2)}</div>
                  </div>
                );
              })()}
              {(() => {
                const so = riskMetrics1y.sortino;
                const color = so >= 2.0 ? '#4ade80' : so >= 1.2 ? '#86efac' : so >= 0.6 ? '#fbbf24' : '#f87171';
                return (
                  <div style={{ padding: '8px 12px', borderRadius: '8px', border: `1px solid ${color}33`, background: `${color}08`, textAlign: 'center', minWidth: '72px' }}>
                    <div style={{ fontSize: '9px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Sortino 1Y</div>
                    <div style={{ fontSize: '16px', fontWeight: 800, color, marginTop: '2px' }}>{so.toFixed(2)}</div>
                  </div>
                );
              })()}
              {(() => {
                const dd = riskMetrics1y.maxDrawdown;
                const color = dd <= 10 ? '#4ade80' : dd <= 20 ? '#fbbf24' : dd <= 35 ? '#fb923c' : '#f87171';
                return (
                  <div style={{ padding: '8px 12px', borderRadius: '8px', border: `1px solid ${color}33`, background: `${color}08`, textAlign: 'center', minWidth: '72px' }}>
                    <div style={{ fontSize: '9px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Max DD 1Y</div>
                    <div style={{ fontSize: '16px', fontWeight: 800, color, marginTop: '2px' }}>-{dd.toFixed(1)}%</div>
                  </div>
                );
              })()}
            </div>
          )}
          {/* Earnings warning badge */}
          {data.fundamentals?.next_earnings_date && (() => {
            const d = data.fundamentals!.days_to_earnings;
            const isImminent = d != null && d <= 7;
            const isSoon = d != null && d <= 21;
            const bg = isImminent ? 'rgba(239,68,68,0.1)' : isSoon ? 'rgba(251,191,36,0.08)' : 'rgba(99,102,241,0.06)';
            const border = isImminent ? 'rgba(239,68,68,0.4)' : isSoon ? 'rgba(251,191,36,0.3)' : 'rgba(99,102,241,0.2)';
            const color = isImminent ? '#f87171' : isSoon ? '#fbbf24' : '#818cf8';
            return (
              <div style={{ padding: '8px 14px', borderRadius: '8px', border: `1px solid ${border}`, background: bg, textAlign: 'center', minWidth: '90px' }}>
                <div style={{ fontSize: '9px', fontWeight: 700, color, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  {isImminent ? '⚠ Earnings Soon' : '📅 Earnings'}
                </div>
                <div style={{ fontSize: '13px', fontWeight: 800, color, marginTop: '2px' }}>
                  {d != null ? `${d}d` : data.fundamentals!.next_earnings_date}
                </div>
                <div style={{ fontSize: '9px', color: '#475569', marginTop: '1px' }}>{data.fundamentals!.next_earnings_date}</div>
              </div>
            );
          })()}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '6px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <RefreshButton onClick={handleRefresh} loading={refreshing} />
          <button
            onClick={handleRefreshSignal}
            disabled={sigRefreshing}
            title="Recompute AI signal now for this stock"
            style={{
              display: 'flex', alignItems: 'center', gap: '6px',
              padding: '6px 13px', borderRadius: '6px',
              border: `1px solid ${sigRefreshing ? 'rgba(129,140,248,0.4)' : 'rgba(129,140,248,0.2)'}`,
              background: sigRefreshing ? 'rgba(99,102,241,0.1)' : 'rgba(99,102,241,0.05)',
              color: sigRefreshing ? '#818cf8' : '#6366f1',
              cursor: sigRefreshing ? 'not-allowed' : 'pointer',
              fontSize: '12px', transition: 'all 0.15s',
            }}
          >
            <span style={{ display: 'inline-block', fontSize: '13px', lineHeight: 1, animation: sigRefreshing ? 'spin 0.8s linear infinite' : 'none' }}>⚡</span>
            {sigRefreshing ? 'Computing…' : 'Refresh Signal'}
          </button>
          <button
            onClick={handleFullRefresh}
            disabled={fullRefreshing}
            title="Re-fetch price data from source, then reload"
            style={{
              display: 'flex', alignItems: 'center', gap: '6px',
              padding: '6px 13px', borderRadius: '6px',
              border: '1px solid rgba(99,102,241,0.35)',
              background: fullRefreshing ? 'rgba(99,102,241,0.15)' : 'rgba(99,102,241,0.08)',
              color: fullRefreshing ? '#818cf8' : '#6366f1',
              cursor: fullRefreshing ? 'not-allowed' : 'pointer',
              fontSize: '12px', fontWeight: 600, transition: 'all 0.15s',
            }}
          >
            <span style={{ display: 'inline-block', fontSize: '14px', lineHeight: 1, animation: fullRefreshing ? 'spin 0.8s linear infinite' : 'none' }}>⟳</span>
            {fullRefreshing ? 'Fetching…' : 'Full Refresh'}
          </button>
          </div>
          {fullRefreshMsg && (
            <div style={{ fontSize: '11px', color: fullRefreshMsg === 'Done' ? '#4ade80' : fullRefreshMsg.includes('failed') ? '#f87171' : '#818cf8' }}>
              {fullRefreshMsg}
            </div>
          )}
          <div ref={watchMenuRef} style={{ position: 'relative' }}>
            <button
              onClick={openWatchMenu}
              style={{
                display: 'flex', alignItems: 'center', gap: '6px',
                padding: '6px 14px', borderRadius: '6px', fontSize: '13px', fontWeight: 600, cursor: 'pointer',
                border: watched ? 'none' : '1px solid #475569',
                background: watched ? '#4f46e5' : 'transparent',
                color: watched ? '#ffffff' : '#cbd5e1', transition: 'all 0.15s',
              }}
            >
              {watched ? '★ Watching' : '☆ Watch'}
              <span style={{ fontSize: '10px', opacity: 0.6 }}>▾</span>
            </button>
            {watchMenuOpen && (
              <div style={{
                position: 'absolute', right: 0, top: 'calc(100% + 4px)', zIndex: 100,
                background: '#0d1424', border: '1px solid rgba(99,102,241,0.3)', borderRadius: '10px',
                boxShadow: '0 16px 32px rgba(0,0,0,0.5)', padding: '6px', minWidth: '180px',
              }}>
                <div style={{ fontSize: '10px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', padding: '4px 8px 6px' }}>
                  Add to watchlist
                </div>
                {listStates === null && (
                  <div style={{ padding: '8px 10px', fontSize: '12px', color: '#475569' }}>Loading…</div>
                )}
                {listStates !== null && (watchlists ?? []).map((wl: WatchlistMeta) => {
                  const inList = listStates[wl.id];
                  const pending = listPending === wl.id;
                  return (
                    <button
                      key={wl.id}
                      onClick={() => toggleListItem(wl.id)}
                      disabled={pending}
                      style={{
                        display: 'flex', alignItems: 'center', gap: '8px', width: '100%',
                        padding: '8px 10px', borderRadius: '6px', border: 'none', cursor: 'pointer',
                        background: inList ? 'rgba(99,102,241,0.12)' : 'transparent',
                        color: inList ? '#818cf8' : '#94a3b8', fontSize: '13px', textAlign: 'left',
                        transition: 'all 0.1s',
                      }}
                    >
                      <span>{pending ? '…' : inList ? '★' : '☆'}</span>
                      <span style={{ flex: 1 }}>{wl.name}</span>
                      <span style={{ fontSize: '11px', color: '#334155' }}>{wl.item_count}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Key metrics strip */}
      {data.fundamentals && (() => {
        const f = data.fundamentals!;
        const evSales = f.ev_to_revenue;
        const items: [string, string][] = [
          ['P/E (TTM)', f.trailing_pe != null ? `${f.trailing_pe.toFixed(1)}x` : '—'],
          ['Fwd P/E', f.forward_pe != null ? `${f.forward_pe.toFixed(1)}x` : '—'],
          ['EV / Sales', evSales != null ? `${evSales.toFixed(1)}x` : '—'],
          ['EV / EBITDA', f.ev_to_ebitda != null ? `${f.ev_to_ebitda.toFixed(1)}x` : '—'],
          ['P/B', f.price_to_book != null ? `${f.price_to_book.toFixed(1)}x` : '—'],
          ['Beta', f.beta != null ? f.beta.toFixed(2) : '—'],
        ];
        return (
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {items.map(([label, val]) => (
              <div key={label} style={{ padding: '7px 14px', borderRadius: '8px', border: '1px solid #1e293b', background: 'rgba(255,255,255,0.02)', textAlign: 'center', minWidth: '80px' }}>
                <div style={{ fontSize: '9px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
                <div style={{ fontSize: '14px', fontWeight: 700, color: '#e2e8f0', marginTop: '2px' }}>{val}</div>
              </div>
            ))}
          </div>
        );
      })()}

      {/* Main layout: chart left, sidebar right */}
      <div className="grid gap-4 stock-detail-main-grid">
        {/* Left column: chart + analysis panels */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

          {/* T230-CHARTING-TIMEFRAMES: Timeframe selector + Compare overlay controls */}
          {data.prices && data.prices.length > 0 && (() => {
            // Compute compare stat for the badge (% change over the shared visible period)
            let compareStat: string | null = null;
            if (compareSymbol && comparePrices && comparePrices.length > 1 && data.prices && data.prices.length > 0) {
              const mainStart = data.prices[0].ts.slice(0, 10);
              const mainEnd = data.prices[data.prices.length - 1].ts.slice(0, 10);
              const cAligned = comparePrices.filter(p => {
                const d = p.ts.slice(0, 10);
                return d >= mainStart && d <= mainEnd;
              });
              if (cAligned.length > 1) {
                const cRet = ((+cAligned[cAligned.length - 1].close / +cAligned[0].close) - 1) * 100;
                const sRet = ((+data.prices[data.prices.length - 1].close / +data.prices[0].close) - 1) * 100;
                compareStat = `${symbol} ${sRet >= 0 ? '+' : ''}${sRet.toFixed(1)}%  vs  ${compareSymbol} ${cRet >= 0 ? '+' : ''}${cRet.toFixed(1)}%`;
              }
            }

            return (
              <div>
                {/* Timeframe + Compare toolbar */}
                <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px', marginBottom: '6px' }}>
                  <span style={{ fontSize: '11px', color: '#475569', fontWeight: 600, marginRight: '2px' }}>TF</span>
                  {(['5m', '15m', '1h', '4h', '1d'] as const).map(tf => (
                    <button
                      key={tf}
                      onClick={() => setChartTf(tf)}
                      style={{
                        padding: '3px 10px',
                        borderRadius: '12px',
                        fontSize: '11px',
                        fontWeight: 600,
                        border: chartTf === tf ? 'none' : '1px solid #1e293b',
                        background: chartTf === tf ? '#4f46e5' : 'rgba(255,255,255,0.03)',
                        color: chartTf === tf ? '#fff' : '#64748b',
                        cursor: 'pointer',
                        transition: 'all 0.12s',
                      }}
                    >
                      {tf}
                    </button>
                  ))}
                  <span style={{ margin: '0 4px', width: '1px', height: '14px', background: '#1e293b', display: 'inline-block' }} />
                  {/* Compare button */}
                  <div style={{ position: 'relative' }}>
                    <button
                      onClick={() => {
                        if (compareSymbol) { setCompareSymbol(null); setCompareCustomInput(''); setCompareInputOpen(false); }
                        else setCompareInputOpen(v => !v);
                      }}
                      style={{
                        padding: '3px 10px',
                        borderRadius: '12px',
                        fontSize: '11px',
                        fontWeight: 600,
                        border: compareSymbol ? 'none' : '1px solid #1e293b',
                        background: compareSymbol ? '#78350f' : 'rgba(255,255,255,0.03)',
                        color: compareSymbol ? '#fbbf24' : '#64748b',
                        cursor: 'pointer',
                      }}
                    >
                      {compareSymbol ? `vs ${compareSymbol} x` : 'Compare'}
                    </button>
                    {compareInputOpen && !compareSymbol && (
                      <div style={{ position: 'absolute', top: '100%', left: 0, zIndex: 50, marginTop: '4px', background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '10px', minWidth: '200px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                          {['SPY', 'QQQ', '^HSI'].map(preset => (
                            <button
                              key={preset}
                              onClick={() => { setCompareSymbol(preset); setCompareInputOpen(false); }}
                              style={{ padding: '3px 10px', borderRadius: '8px', fontSize: '11px', fontWeight: 600, border: '1px solid #334155', background: 'transparent', color: '#94a3b8', cursor: 'pointer' }}
                            >
                              {preset === '^HSI' ? 'HSI' : preset}
                            </button>
                          ))}
                        </div>
                        <div style={{ display: 'flex', gap: '4px' }}>
                          <input
                            value={compareCustomInput}
                            onChange={e => setCompareCustomInput(e.target.value.toUpperCase())}
                            onKeyDown={e => { if (e.key === 'Enter' && compareCustomInput.trim()) { setCompareSymbol(compareCustomInput.trim()); setCompareInputOpen(false); } }}
                            placeholder="e.g. NVDA"
                            style={{ flex: 1, padding: '4px 8px', borderRadius: '6px', border: '1px solid #334155', background: '#1e293b', color: '#e2e8f0', fontSize: '11px', outline: 'none' }}
                          />
                          <button
                            onClick={() => { if (compareCustomInput.trim()) { setCompareSymbol(compareCustomInput.trim()); setCompareInputOpen(false); } }}
                            style={{ padding: '4px 10px', borderRadius: '6px', fontSize: '11px', background: '#4f46e5', color: '#fff', border: 'none', cursor: 'pointer', fontWeight: 600 }}
                          >
                            Go
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                  {/* Compare stat badge */}
                  {compareStat && (
                    <span style={{ fontSize: '11px', color: '#94a3b8', marginLeft: '4px', fontFamily: 'monospace' }}>
                      {compareStat}
                    </span>
                  )}
                  {/* Loading indicator for tf prices */}
                  {chartTf !== '1d' && chartTf !== '5m' && !tfPrices && (
                    <span style={{ fontSize: '11px', color: '#475569' }}>loading…</span>
                  )}
                </div>

                {/* Chart */}
                <PriceChart
                  symbol={symbol as string}
                  prices={data.prices}
                  indicators={chartTf === '1d' ? data.indicators : undefined}
                  levels={chartTf === '1d' ? data.levels : undefined}
                  signalMarkers={chartTf === '1d' ? signalHistory : undefined}
                  patterns={livePatterns?.patterns}
                  gamePlanLevels={gamePlan && chartTf === '1d' ? {
                    entryLow: gamePlan.entries[0]?.price ?? null,
                    entryHigh: gamePlan.entries[1]?.price ?? gamePlan.entries[0]?.price ?? null,
                    stopLoss: gamePlan.stop_loss?.price ?? null,
                    target1: gamePlan.take_profit?.price ?? null,
                  } : null}
                  riskRewardLevels={chartTf === '1d' ? (() => {
                    const lpChart = allPrices?.find(p => p.symbol === symbol);
                    const chartCurPx = lpChart?.price ?? data.prices?.at(-1)?.close ?? null;
                    const chartNearestSupport = data.levels?.support_resistance
                      ?.filter(l => chartCurPx == null || l.price < chartCurPx)
                      .sort((a, b) => b.price - a.price)[0]?.price ?? null;
                    return {
                      entry: chartCurPx,
                      stop: atrData?.stop_loss_2atr ?? chartNearestSupport ?? null,
                      target: data.fundamentals?.target_price ?? null,
                    };
                  })() : null}
                  intradayOverride={
                    chartTf !== '1d' && chartTf !== '5m'
                      ? (tfPrices ?? null)
                      : null
                  }
                  compareData={
                    compareSymbol && comparePrices && comparePrices.length > 1
                      ? { symbol: compareSymbol, prices: comparePrices }
                      : null
                  }
                />
              </div>
            );
          })()}
          {data.prices && data.prices.length === 0 && (
            <div className="rounded-md border border-slate-800 bg-slate-900 p-4 text-slate-400">
              No price data available for {symbol}. Try clicking Full Refresh above to ingest history.
            </div>
          )}

          {/* Volume Bar Chart */}
          {data.prices && data.prices.length >= 5 && (() => {
            const dailyBars = [...data.prices!]
              .filter(p => p.volume > 0)
              .sort((a, b) => a.ts.localeCompare(b.ts))
              .slice(-60);
            if (dailyBars.length < 5) return null;
            // 20-day moving average volume per bar
            const avgVols = dailyBars.map((_, i) => {
              const window = dailyBars.slice(Math.max(0, i - 19), i + 1);
              return window.reduce((s, p) => s + p.volume, 0) / window.length;
            });
            const maxVol = Math.max(...dailyBars.map(p => p.volume));
            const latestVol = dailyBars[dailyBars.length - 1].volume;
            const latestAvg = avgVols[avgVols.length - 1];
            const latestRatio = latestAvg > 0 ? latestVol / latestAvg : null;
            const isSpike = latestRatio != null && latestRatio >= 2;
            const padL = 44, padR = 8, padT = 12, padB = 24;
            const W = 600, H = 100;
            const chartW = W - padL - padR;
            const chartH = H - padT - padB;
            const barW = Math.max(1, chartW / dailyBars.length - 1);
            function xPos(i: number) { return padL + (i / (dailyBars.length - 1)) * chartW; }
            function yPos(v: number) { return padT + chartH - (v / maxVol) * chartH; }
            function barColor(vol: number, avg: number) {
              if (avg <= 0) return '#334155';
              const r = vol / avg;
              if (r >= 2) return '#f59e0b';
              if (r >= 1.5) return '#4ade80';
              if (r >= 1) return '#6366f1';
              return '#334155';
            }
            const tickVols = [0, maxVol * 0.5, maxVol];
            function fmtVolShort(v: number) {
              if (v >= 1e9) return `${(v/1e9).toFixed(1)}B`;
              if (v >= 1e6) return `${(v/1e6).toFixed(0)}M`;
              if (v >= 1e3) return `${(v/1e3).toFixed(0)}K`;
              return String(Math.round(v));
            }
            const xLabels: { i: number; label: string }[] = [];
            const step = Math.max(1, Math.floor(dailyBars.length / 5));
            dailyBars.forEach((p, i) => {
              if (i % step === 0 || i === dailyBars.length - 1) {
                xLabels.push({ i, label: p.ts.slice(5, 10) });
              }
            });
            return (
              <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', padding: '14px 16px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
                  <span style={{ fontSize: '12px', fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Volume</span>
                  {isSpike && (
                    <span style={{ fontSize: '10px', fontWeight: 800, background: 'rgba(245,158,11,0.15)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.4)', borderRadius: '4px', padding: '1px 6px', letterSpacing: '0.06em' }}>
                      ⚡ VOLUME SPIKE {latestRatio!.toFixed(1)}× AVG
                    </span>
                  )}
                  {latestRatio != null && !isSpike && (
                    <span style={{ fontSize: '11px', color: latestRatio >= 1 ? '#4ade80' : '#64748b' }}>
                      {latestRatio.toFixed(2)}× avg today
                    </span>
                  )}
                  <span style={{ marginLeft: 'auto', fontSize: '11px', color: '#475569' }}>20d avg — dashed</span>
                </div>
                <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
                  {/* Y-axis gridlines + labels */}
                  {tickVols.map((tv, ti) => (
                    <g key={ti}>
                      <line x1={padL} y1={yPos(tv)} x2={W - padR} y2={yPos(tv)} stroke="#1e293b" strokeWidth={1} />
                      <text x={padL - 4} y={yPos(tv) + 4} fill="#475569" fontSize={9} textAnchor="end">{fmtVolShort(tv)}</text>
                    </g>
                  ))}
                  {/* Volume bars */}
                  {dailyBars.map((p, i) => {
                    const bx = xPos(i);
                    return (
                      <rect key={i}
                        x={bx - barW / 2} y={padT + chartH - (p.volume / maxVol) * chartH}
                        width={barW} height={(p.volume / maxVol) * chartH}
                        fill={barColor(p.volume, avgVols[i])}
                        opacity={0.85}
                      />
                    );
                  })}
                  {/* 20d avg line */}
                  <polyline
                    points={avgVols.map((av, i) => `${xPos(i).toFixed(1)},${yPos(av).toFixed(1)}`).join(' ')}
                    fill="none" stroke="#60a5fa" strokeWidth={1.5} strokeDasharray="4,3" opacity={0.7}
                  />
                  {/* X-axis labels */}
                  {xLabels.map(({ i, label }) => (
                    <text key={i} x={xPos(i)} y={H - 4} fill="#475569" fontSize={9} textAnchor="middle">{label}</text>
                  ))}
                </svg>
                <div style={{ display: 'flex', gap: '16px', marginTop: '6px', fontSize: '10px', color: '#475569' }}>
                  <span><span style={{ color: '#f59e0b' }}>■</span> Spike (≥2× avg)</span>
                  <span><span style={{ color: '#4ade80' }}>■</span> High (≥1.5×)</span>
                  <span><span style={{ color: '#6366f1' }}>■</span> Normal (≥1×)</span>
                  <span><span style={{ color: '#60a5fa' }}>— —</span> 20d Avg</span>
                </div>
              </div>
            );
          })()}

          {/* K-Score + Fear & Greed side by side */}
          {(ranking || fearGreed) && (
            <div className="stock-detail-kscore-feargreed-grid" style={{ display: 'grid', gridTemplateColumns: ranking && fearGreed ? '1fr 1fr' : '1fr', gap: '16px', alignItems: 'stretch' }}>

              {/* K-Score with progress bars */}
              {ranking && (() => {
                const s = ranking.score ?? 0;
                const scoreColor = s >= 70 ? '#4ade80' : s >= 50 ? '#facc15' : '#f87171';
                const bars: [string, number | null | undefined, string][] = [
                  ['Technical', ranking.technical, '35%'],
                  ['Momentum',  ranking.momentum,  '25%'],
                  ['Value',     ranking.value,      '20%'],
                  ['Growth',    ranking.growth,     '10%'],
                  ['Volatility',ranking.volatility, '10%'],
                ];
                return (
                  <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
                      <h3 className="text-sm font-semibold text-slate-300">K-Score Breakdown</h3>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        {(() => {
                          const og = s >= 90 ? 'A+' : s >= 80 ? 'A' : s >= 65 ? 'B' : s >= 50 ? 'C' : s >= 35 ? 'D' : 'F';
                          const ogc = s >= 80 ? '#4ade80' : s >= 65 ? '#86efac' : s >= 50 ? '#facc15' : s >= 35 ? '#fb923c' : '#f87171';
                          return <span style={{ fontSize: 15, fontWeight: 800, color: ogc, background: `${ogc}18`, border: `1px solid ${ogc}50`, borderRadius: 4, padding: '2px 7px', lineHeight: '20px' }}>{og}</span>;
                        })()}
                        <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
                          <span style={{ fontSize: 26, fontWeight: 800, color: scoreColor, lineHeight: 1 }}>{s.toFixed(0)}</span>
                          <span style={{ fontSize: 11, color: '#475569' }}>/100</span>
                        </div>
                      </div>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
                      {bars.map(([label, value, weight]) => {
                        const pct = typeof value === 'number' ? value : 0;
                        const bc = pct >= 70 ? '#4ade80' : pct >= 50 ? '#facc15' : '#f87171';
                        const grade = typeof value !== 'number' ? null : pct >= 90 ? 'A+' : pct >= 80 ? 'A' : pct >= 65 ? 'B' : pct >= 50 ? 'C' : pct >= 35 ? 'D' : 'F';
                        const gradeColor = !grade ? '#475569' : pct >= 80 ? '#4ade80' : pct >= 65 ? '#86efac' : pct >= 50 ? '#facc15' : pct >= 35 ? '#fb923c' : '#f87171';
                        return (
                          <div key={label}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                              <span style={{ fontSize: 11, color: '#94a3b8' }}>
                                {label} <span style={{ color: '#334155', fontSize: 10 }}>{weight}</span>
                              </span>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                {grade && (
                                  <span style={{ fontSize: 10, fontWeight: 800, color: gradeColor, background: `${gradeColor}18`, border: `1px solid ${gradeColor}40`, borderRadius: 3, padding: '0px 5px', lineHeight: '16px' }}>{grade}</span>
                                )}
                                <span style={{ fontSize: 11, fontWeight: 700, color: bc }}>{typeof value === 'number' ? value.toFixed(0) : '—'}</span>
                              </div>
                            </div>
                            <div style={{ height: 4, borderRadius: 2, background: '#1e293b' }}>
                              <div style={{ height: '100%', width: `${pct}%`, background: bc, borderRadius: 2, transition: 'width 0.5s' }} />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    {(ranking.fair_price != null || ranking.relative_strength != null) && (
                      <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid #1e293b', display: 'flex', flexDirection: 'column', gap: 4 }}>
                        {ranking.fair_price != null && (
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                            <span style={{ color: '#64748b' }}>Fair Price</span>
                            <span style={{ color: '#a5b4fc', fontWeight: 600 }}>${ranking.fair_price.toFixed(2)}</span>
                          </div>
                        )}
                        {ranking.relative_strength != null && (() => {
                          const rs = ranking.relative_strength as number;
                          const rsColor = rs >= 60 ? '#4ade80' : rs >= 45 ? '#94a3b8' : '#f87171';
                          return (
                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                              <span style={{ color: '#64748b' }}>Rel. Strength vs sector</span>
                              <span style={{ color: rsColor, fontWeight: 600 }}>{rs.toFixed(0)} — {rs >= 60 ? 'leading' : rs >= 45 ? 'in-line' : 'lagging'}</span>
                            </div>
                          );
                        })()}
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* Snowflake Radar */}
              {ranking && (() => {
                const axes = [
                  { label: 'Technical', value: ranking.technical ?? 0, color: '#38bdf8' },
                  { label: 'Momentum',  value: ranking.momentum  ?? 0, color: '#fb923c' },
                  { label: 'Value',     value: ranking.value     ?? 0, color: '#a78bfa' },
                  { label: 'Growth',    value: ranking.growth    ?? 0, color: '#34d399' },
                  { label: 'Strength',  value: Math.min(100, ranking.relative_strength ?? 50), color: '#facc15' },
                ];
                const n = axes.length;
                const cx = 80; const cy = 80; const R = 60;
                const step = (2 * Math.PI) / n;
                const angle = (i: number) => -Math.PI / 2 + i * step;
                const pt = (i: number, r: number) => ({
                  x: cx + r * Math.cos(angle(i)),
                  y: cy + r * Math.sin(angle(i)),
                });
                // Polygon points for filled area
                const polyPts = axes.map((a, i) => {
                  const { x, y } = pt(i, (a.value / 100) * R);
                  return `${x.toFixed(1)},${y.toFixed(1)}`;
                }).join(' ');
                // Grid rings at 25, 50, 75, 100
                const rings = [0.25, 0.5, 0.75, 1.0];
                return (
                  <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
                    <h3 className="text-sm font-semibold text-slate-300 mb-3">Score Profile</h3>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
                      <svg viewBox="0 0 160 160" style={{ width: 160, height: 160, flexShrink: 0 }}>
                        {/* Grid rings */}
                        {rings.map(r => (
                          <polygon key={r}
                            points={axes.map((_, i) => { const p = pt(i, r * R); return `${p.x.toFixed(1)},${p.y.toFixed(1)}`; }).join(' ')}
                            fill="none" stroke="#1e293b" strokeWidth={1} />
                        ))}
                        {/* Axis lines */}
                        {axes.map((_, i) => {
                          const p = pt(i, R);
                          return <line key={i} x1={cx} y1={cy} x2={p.x} y2={p.y} stroke="#1e293b" strokeWidth={1} />;
                        })}
                        {/* Filled polygon */}
                        <polygon points={polyPts} fill="#4f46e540" stroke="#6366f1" strokeWidth={1.5} strokeLinejoin="round" />
                        {/* Dots */}
                        {axes.map((a, i) => {
                          const { x, y } = pt(i, (a.value / 100) * R);
                          return <circle key={i} cx={x} cy={y} r={3} fill={a.color} />;
                        })}
                        {/* Labels */}
                        {axes.map((a, i) => {
                          const { x, y } = pt(i, R + 14);
                          return (
                            <text key={i} x={x} y={y} textAnchor="middle" dominantBaseline="middle"
                              fontSize={8} fill="#64748b" fontWeight={600}>
                              {a.label}
                            </text>
                          );
                        })}
                      </svg>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', flex: 1 }}>
                        {axes.map(a => (
                          <div key={a.label} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <div style={{ width: '8px', height: '8px', borderRadius: '2px', background: a.color, flexShrink: 0 }} />
                            <span style={{ fontSize: '11px', color: '#94a3b8', width: '68px' }}>{a.label}</span>
                            <div style={{ flex: 1, height: '3px', background: '#1e293b', borderRadius: '2px' }}>
                              <div style={{ width: `${a.value}%`, height: '100%', background: a.color, borderRadius: '2px', transition: 'width 0.4s' }} />
                            </div>
                            <span style={{ fontSize: '11px', fontWeight: 700, color: a.color, width: '28px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                              {a.value.toFixed(0)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                );
              })()}

              {/* Fear & Greed */}
              {fearGreed && (() => {
                const score = fearGreed.score;
                const ratingColor: Record<string, string> = {
                  'Extreme Fear': '#ef4444', 'Fear': '#f97316',
                  'Neutral': '#facc15', 'Greed': '#86efac', 'Extreme Greed': '#22c55e',
                };
                const color = ratingColor[fearGreed.rating] ?? '#94a3b8';
                const angle = (score / 100) * 180 - 90;
                return (
                  <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                      <h3 className="text-sm font-semibold text-slate-300">Fear &amp; Greed Index</h3>
                      <span style={{ fontSize: '10px', color: '#475569' }}>CNN · 1 h cache</span>
                    </div>
                    <div style={{ display: 'flex', gap: '20px', alignItems: 'center' }}>
                      {/* Gauge */}
                      <div style={{ flexShrink: 0 }}>
                        <div style={{ position: 'relative', width: '130px', height: '68px', overflow: 'hidden' }}>
                          <svg width="130" height="68" viewBox="0 0 140 72">
                            <path d="M 10 70 A 60 60 0 0 1 130 70" fill="none" stroke="#1e293b" strokeWidth="12" strokeLinecap="round"/>
                            {[
                              { start: 0,   end: 36,  color: '#ef4444' },
                              { start: 36,  end: 72,  color: '#f97316' },
                              { start: 72,  end: 108, color: '#facc15' },
                              { start: 108, end: 144, color: '#86efac' },
                              { start: 144, end: 180, color: '#22c55e' },
                            ].map(seg => {
                              const r = 60, cx = 70, cy = 70;
                              const toRad = (d: number) => (d - 180) * Math.PI / 180;
                              const x1 = cx + r * Math.cos(toRad(seg.start));
                              const y1 = cy + r * Math.sin(toRad(seg.start));
                              const x2 = cx + r * Math.cos(toRad(seg.end));
                              const y2 = cy + r * Math.sin(toRad(seg.end));
                              return <path key={seg.start} d={`M ${x1} ${y1} A ${r} ${r} 0 0 1 ${x2} ${y2}`} fill="none" stroke={seg.color} strokeWidth="10" strokeLinecap="butt" opacity="0.85" />;
                            })}
                            <line x1="70" y1="70" x2={70 + 52 * Math.cos((angle - 90) * Math.PI / 180)} y2={70 + 52 * Math.sin((angle - 90) * Math.PI / 180)} stroke={color} strokeWidth="2.5" strokeLinecap="round" />
                            <circle cx="70" cy="70" r="5" fill={color} />
                          </svg>
                        </div>
                        <div style={{ textAlign: 'center', marginTop: 2 }}>
                          <div style={{ fontSize: '22px', fontWeight: 800, color, lineHeight: 1 }}>{score}</div>
                          <div style={{ fontSize: '11px', fontWeight: 700, color, marginTop: '2px' }}>{fearGreed.rating}</div>
                        </div>
                      </div>
                      {/* Right side: history + regime */}
                      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 10 }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                          {[['Prev close', fearGreed.previous_close], ['1 Week ago', fearGreed.previous_1_week], ['1 Month ago', fearGreed.previous_1_month], ['1 Year ago', fearGreed.previous_1_year]].map(([lbl, val]) => (
                            <div key={lbl as string} style={{ background: '#0f172a', borderRadius: 5, padding: '5px 8px' }}>
                              <div style={{ fontSize: '9px', color: '#475569', fontWeight: 700, textTransform: 'uppercase' }}>{lbl}</div>
                              <div style={{ fontSize: '13px', fontWeight: 700, color: '#94a3b8' }}>{val != null ? (val as number).toFixed(0) : '—'}</div>
                            </div>
                          ))}
                        </div>
                        {fearGreed.sp500_regime && (
                          <div style={{ paddingTop: 8, borderTop: '1px solid #1e293b' }}>
                            <div style={{ fontSize: '9px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>S&amp;P 500 Regime</div>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                <span style={{ width: 8, height: 8, borderRadius: '50%', background: fearGreed.sp500_regime === 'bull' ? '#22c55e' : '#ef4444', display: 'inline-block', boxShadow: `0 0 6px ${fearGreed.sp500_regime === 'bull' ? '#22c55e' : '#ef4444'}` }} />
                                <span style={{ fontSize: 13, fontWeight: 800, color: fearGreed.sp500_regime === 'bull' ? '#4ade80' : '#f87171' }}>
                                  {fearGreed.sp500_regime === 'bull' ? 'Bull Market' : 'Bear Market'}
                                </span>
                              </div>
                              {fearGreed.sp500_vs_ma200_pct != null && (
                                <span style={{ fontSize: 11, fontWeight: 700, color: fearGreed.sp500_vs_ma200_pct >= 0 ? '#4ade80' : '#f87171' }}>
                                  {fearGreed.sp500_vs_ma200_pct >= 0 ? '+' : ''}{fearGreed.sp500_vs_ma200_pct.toFixed(1)}% vs 200MA
                                </span>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })()}
            </div>
          )}

          {/* Sector Peers comparison */}
          {sectorPeers.length > 0 && (
            <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <div>
                  <h3 className="text-sm font-semibold text-slate-300">Sector Peers</h3>
                  {currentSector && <div style={{ fontSize: 10, color: '#475569', marginTop: 1 }}>{currentSector}</div>}
                </div>
                {ranking && (
                  <button
                    onClick={() => setCompareOpen(true)}
                    style={{
                      padding: '4px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600,
                      border: '1px solid #6366f1', background: 'rgba(99,102,241,0.12)',
                      color: '#818cf8', cursor: 'pointer',
                    }}
                  >
                    Compare
                  </button>
                )}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {sectorPeers.map(peer => {
                  const pp = priceMap[peer.symbol];
                  const chg = pp?.change_pct;
                  return (
                    <a
                      key={peer.symbol}
                      href={`/stock/${peer.symbol}`}
                      style={{
                        display: 'flex', flexDirection: 'column', alignItems: 'center',
                        padding: '8px 14px', borderRadius: 8,
                        border: '1px solid #1e293b', background: 'rgba(255,255,255,0.02)',
                        textDecoration: 'none', minWidth: 90,
                      }}
                    >
                      <span style={{ fontSize: 13, fontWeight: 700, color: '#818cf8' }}>{peer.symbol}</span>
                      {peer.score != null && (
                        <span style={{ fontSize: 10, color: peer.score >= 70 ? '#4ade80' : peer.score >= 50 ? '#facc15' : '#f87171', marginTop: 2 }}>
                          K {peer.score.toFixed(0)}
                        </span>
                      )}
                      {pp?.price != null && (
                        <span style={{ fontSize: 11, color: '#94a3b8', marginTop: 1 }}>${pp.price.toFixed(2)}</span>
                      )}
                      {chg != null && (
                        <span style={{ fontSize: 10, color: chg >= 0 ? '#4ade80' : '#f87171' }}>
                          {chg >= 0 ? '+' : ''}{chg.toFixed(2)}%
                        </span>
                      )}
                    </a>
                  );
                })}
              </div>
            </div>
          )}

          {/* ML Prediction — full width of left column */}
          <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <h3 className="text-sm font-semibold text-slate-300">ML Prediction</h3>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <select
                  value={mlModel}
                  onChange={e => { setMlModel(e.target.value); setMlResult(null); }}
                  className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-300"
                >
                  {['xgboost', 'random_forest', 'gradient_boosting', 'lstm'].map(m => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <button onClick={runML} disabled={mlLoading} style={{ borderRadius: 6, background: '#4f46e5', border: 'none', padding: '5px 12px', fontSize: 12, color: '#fff', cursor: mlLoading ? 'not-allowed' : 'pointer', opacity: mlLoading ? 0.5 : 1, fontWeight: 600 }}>
                  {mlLoading ? '…' : '↻ Predict'}
                </button>
              </div>
            </div>
            {/* Default: show ML prob already baked into the AI Signal (always consistent with dashboard) */}
            {(() => {
              const displayResult = mlResult ?? (data.signal?.bullish_probability != null ? {
                direction: data.signal.bullish_probability >= 0.5 ? 'UP' : 'DOWN',
                bullish_probability: data.signal.bullish_probability,
                confidence: Math.abs(data.signal.bullish_probability - 0.5) * 200,
              } : null);
              const isFresh = !!mlResult;
              if (mlLoading) return <div style={{ fontSize: 12, color: '#475569' }}>Running model…</div>;
              if (mlError && !displayResult) return <div style={{ fontSize: 11, color: '#fbbf24' }}>{mlError}</div>;
              if (!displayResult) return <div style={{ fontSize: 12, color: '#334155' }}>No signal data yet.</div>;
              const bullishPct = Math.round(displayResult.bullish_probability * 100);
              const isUp = displayResult.direction === 'UP';
              return (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 20, marginBottom: 10 }}>
                    <div>
                      <div style={{ fontSize: 28, fontWeight: 800, color: isUp ? '#4ade80' : '#f87171', lineHeight: 1 }}>
                        {isUp ? '↑' : '↓'} {displayResult.direction}
                      </div>
                      <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 4 }}>
                        {bullishPct}% bullish · {displayResult.confidence?.toFixed(1)}% confidence
                      </div>
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ height: 8, borderRadius: 4, background: '#1e293b', overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${bullishPct}%`, background: isUp ? '#4ade80' : '#f87171', borderRadius: 4, transition: 'width 0.4s' }} />
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4, fontSize: 10, color: '#334155' }}>
                        <span>Bearish 0%</span><span>Bullish 100%</span>
                      </div>
                    </div>
                  </div>
                  <div style={{ fontSize: 10, color: '#334155' }}>
                    {isFresh ? '↻ Fresh model run · may differ from AI Signal' : 'From AI Signal — same value used by the dashboard'}
                  </div>
                  {mlError && <div style={{ fontSize: 11, color: '#fbbf24', marginTop: 4 }}>{mlError}</div>}
                  {/* Kelly Criterion position sizing hint */}
                  {(() => {
                    const p = displayResult.bullish_probability;
                    const b = 2.5; // standard SWING R:R
                    const f = (p * b - (1 - p)) / b;
                    if (f <= 0) return (
                      <div style={{ marginTop: 8, fontSize: 10, color: '#475569', borderTop: '1px solid #1e293b', paddingTop: 6 }}>
                        Kelly sizing: negative edge at 2.5:1 R:R — below breakeven probability ({((1/(1+b))*100).toFixed(0)}%)
                      </div>
                    );
                    const fullK = Math.min(f * 100, 25);
                    const halfK = fullK / 2;
                    const kColor = fullK > 15 ? '#4ade80' : fullK > 7 ? '#fbbf24' : '#94a3b8';
                    return (
                      <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 10, borderTop: '1px solid #1e293b', paddingTop: 6, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 10, color: '#475569' }}>Kelly (2.5:1 R:R):</span>
                        <span style={{ fontSize: 11, fontWeight: 700, color: kColor }}>{fullK.toFixed(1)}% full</span>
                        <span style={{ fontSize: 10, color: '#334155' }}>·</span>
                        <span style={{ fontSize: 11, fontWeight: 700, color: kColor }}>{halfK.toFixed(1)}% half-Kelly</span>
                        <span style={{ fontSize: 9, color: '#334155' }}>(half-Kelly recommended)</span>
                      </div>
                    );
                  })()}
                </div>
              );
            })()}
            {/* Training controls — collapsed by default */}
            <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid #1e293b' }}>
              <button
                onClick={() => setMlTrainOpen(o => !o)}
                style={{ fontSize: 11, color: '#334155', background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: 0 }}
              >
                <span style={{ fontSize: 9 }}>{mlTrainOpen ? '▾' : '▸'}</span> Model training
              </button>
              {mlTrainOpen && (
                <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <button onClick={trainML} disabled={mlLoading} style={{ borderRadius: 6, background: 'transparent', border: '1px solid #334155', padding: '6px', fontSize: 12, color: '#94a3b8', cursor: mlLoading ? 'not-allowed' : 'pointer', opacity: mlLoading ? 0.5 : 1 }}>
                    {mlLoading ? 'Training…' : 'Train model for this stock'}
                  </button>
                </div>
              )}
            </div>
            {/* ML-FUND-3: Feature importance — collapsed, loaded on demand */}
            <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid #1e293b' }}>
              <button
                onClick={async () => {
                  if (featureImportance) { setFeatureImportance(null); return; }
                  setFiLoading(true);
                  try {
                    const fi = await api.mlFeatureImportance(symbol, mlModel);
                    setFeatureImportance(fi);
                  } catch { /* no model yet */ }
                  setFiLoading(false);
                }}
                style={{ fontSize: 11, color: '#334155', background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: 0 }}
              >
                <span style={{ fontSize: 9 }}>{featureImportance ? '▾' : '▸'}</span> {fiLoading ? 'Loading…' : 'Top model drivers'}
              </button>
              {featureImportance && (
                <div style={{ marginTop: 8 }}>
                  {featureImportance.features.slice(0, 8).map((f, i) => {
                    const catColor = f.category === 'fundamental' ? '#a78bfa' : f.category === 'macro' ? '#60a5fa' : '#4ade80';
                    const barPct = Math.round((f.importance / (featureImportance.features[0]?.importance || 1)) * 100);
                    return (
                      <div key={f.name} style={{ marginBottom: 5 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                            <span style={{ fontSize: 9, fontWeight: 700, color: catColor, background: `${catColor}22`, borderRadius: 3, padding: '1px 4px', textTransform: 'uppercase' }}>{f.category.slice(0, 4)}</span>
                            <span style={{ fontSize: 11, color: '#cbd5e1' }}>{f.name}</span>
                          </div>
                          <span style={{ fontSize: 10, color: '#475569' }}>{(f.importance * 100).toFixed(1)}%</span>
                        </div>
                        <div style={{ height: 4, borderRadius: 2, background: '#1e293b', overflow: 'hidden' }}>
                          <div style={{ height: '100%', width: `${barPct}%`, background: catColor, borderRadius: 2 }} />
                        </div>
                      </div>
                    );
                  })}
                  <div style={{ fontSize: 9, color: '#334155', marginTop: 6, display: 'flex', gap: 10 }}>
                    <span style={{ color: '#4ade80' }}>■ technical</span>
                    <span style={{ color: '#60a5fa' }}>■ macro</span>
                    <span style={{ color: '#a78bfa' }}>■ fundamental</span>
                  </div>
                  {featureImportance.trained_at && (
                    <div style={{ fontSize: 9, color: '#334155', marginTop: 4 }}>
                      Trained {new Date(featureImportance.trained_at).toLocaleDateString()}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* S/R + Fibonacci + Patterns */}
          {(srLevels.length > 0 || Object.keys(fibLevels).length > 0 || (data.patterns?.patterns && data.patterns.patterns.length > 0) || (livePatterns && livePatterns.patterns.length > 0)) && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '16px', alignItems: 'start' }}>

              {srLevels.length > 0 && (
                <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
                  <h3 className="text-sm font-semibold text-slate-300 mb-2">Support &amp; Resistance</h3>
                  <div className="space-y-1">
                    {srLevels.slice().map(lvl => ({ ...lvl, kind: curPrice != null ? (lvl.price > curPrice ? 'resistance' : 'support') : lvl.kind })).sort((a, b) => b.price - a.price).slice(0, 8).map((lvl, i) => (
                      <div key={i} className="flex items-center justify-between text-xs">
                        <span className={lvl.kind === 'support' ? 'text-green-400' : 'text-red-400'}>
                          {lvl.kind === 'support' ? 'S' : 'R'} ${lvl.price.toFixed(2)}
                        </span>
                        <span className="text-slate-500">{lvl.strength} {lvl.strength === 1 ? 'touch' : 'touches'}</span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-2 pt-2 border-t border-slate-800 text-xs text-slate-600">Touches = bounces off this level</div>
                </div>
              )}

              {Object.keys(fibLevels).length > 0 && (
                <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
                  <h3 className="text-sm font-semibold text-slate-300 mb-2">Fibonacci Levels</h3>
                  <div className="space-y-1">
                    {Object.entries(fibLevels).map(([k, v]) => (
                      <div key={k} className="flex justify-between text-xs">
                        <span className="text-slate-500">{k}</span>
                        <span className="text-slate-300">${(v as number).toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {data.patterns?.patterns && data.patterns.patterns.length > 0 && (
                <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
                  <h3 className="text-sm font-semibold text-slate-300 mb-2">Chart Patterns</h3>
                  <div className="space-y-1">
                    {data.patterns.patterns.map((p, i) => (
                      <div key={i} className="flex items-center justify-between text-xs">
                        <span className="text-slate-300">{p.name}</span>
                        <span className="text-slate-500">{((p.confidence ?? 0) * 100).toFixed(0)}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {livePatterns && livePatterns.patterns.length > 0 && (
                <div className="rounded-md p-4" style={{ border: '1px solid rgba(99,102,241,0.35)', background: 'rgba(99,102,241,0.06)' }}>
                  <h3 className="text-sm font-semibold mb-3" style={{ color: '#a5b4fc' }}>Live Pattern Signals</h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {livePatterns.patterns.map((p, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', padding: '8px 10px', borderRadius: '7px', background: p.bullish ? 'rgba(74,222,128,0.07)' : 'rgba(239,68,68,0.07)', border: `1px solid ${p.bullish ? 'rgba(74,222,128,0.2)' : 'rgba(239,68,68,0.2)'}` }}>
                        <span style={{ fontSize: '16px', lineHeight: 1, marginTop: '1px' }}>{p.bullish ? '↑' : '↓'}</span>
                        <div style={{ flex: 1 }}>
                          <div style={{ fontSize: '12px', fontWeight: 700, color: p.bullish ? '#4ade80' : '#f87171', marginBottom: '2px' }}>{p.label}</div>
                          <div style={{ fontSize: '11px', color: '#64748b' }}>{p.description}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                  <div style={{ fontSize: '10px', color: '#334155', marginTop: '8px' }}>Detected in last 3–5 sessions</div>
                </div>
              )}

            </div>
          )}

        </div>

        {/* Sidebar */}
        <div className="space-y-3">
          {/* AI Signal — tabbed horizon switcher */}
          {(() => {
            const SIG_C: Record<string, string> = { BUY: '#4ade80', SELL: '#f87171', WAIT: '#fbbf24', HOLD: '#94a3b8' };
            const HORIZON_COLOR: Record<string, string> = { SHORT: '#38bdf8', SWING: '#818cf8', LONG: '#4ade80', GROWTH: '#a78bfa' };
            const activeSig = allHorizonSignals.find(h => h.horizon === selectedHorizon)?.sig
              ?? (selectedHorizon === 'SWING' ? data.signal : null);
            const sigTs = activeSig && 'ts' in activeSig ? (activeSig as Signal & { ts?: string }).ts : null;
            const storedAge = sigTs ? (() => {
              const mins = Math.round((Date.now() - new Date(sigTs).getTime()) / 60000);
              if (mins < 90) return `${mins}m ago`;
              const hrs = Math.round(mins / 60);
              if (hrs < 48) return `${hrs}h ago`;
              return `${Math.round(hrs / 24)}d ago`;
            })() : null;
            return (
              <div>
                {/* Stored-signal badge */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={{ fontSize: 9, color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>AI Signals</span>
                  {storedAge && <span style={{ fontSize: 9, color: '#334155' }}>stored · {storedAge} · <span style={{ color: '#475569' }}>Refresh to update</span></span>}
                </div>
                {/* Horizon tabs */}
                <div style={{ display: 'flex', gap: 3, marginBottom: 8 }}>
                  {allHorizonSignals.map(({ label, horizon, sig }) => {
                    const isActive = selectedHorizon === horizon;
                    const hColor = HORIZON_COLOR[horizon] ?? '#818cf8';
                    return (
                      <button
                        key={horizon}
                        onClick={() => setSelectedHorizon(horizon)}
                        style={{
                          flex: 1, padding: '5px 4px', borderRadius: 6, border: `1px solid ${isActive ? hColor : '#1e293b'}`,
                          background: isActive ? `${hColor}18` : 'rgba(255,255,255,0.02)',
                          cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
                        }}
                      >
                        <span style={{ fontSize: 9, fontWeight: 700, color: isActive ? hColor : '#475569', letterSpacing: '0.04em' }}>{label}</span>
                        {sig ? (
                          <>
                            <span style={{ fontSize: 10, fontWeight: 800, color: SIG_C[sig.signal] ?? '#475569' }}>{sig.signal}</span>
                            <span style={{ fontSize: 9, color: isActive ? hColor : '#334155', opacity: 0.9 }}>
                              {sig.bullish_probability != null ? `${(sig.bullish_probability * 100).toFixed(0)}%` : ''}
                            </span>
                          </>
                        ) : (
                          <span style={{ fontSize: 10, color: '#334155' }}>—</span>
                        )}
                        {horizon === pageStyle && (
                          <span style={{ fontSize: 8, color: hColor, opacity: 0.7 }}>watchlist</span>
                        )}
                      </button>
                    );
                  })}
                </div>
                {/* Signal card for selected horizon */}
                {activeSig && (
                  <div>
                    {selectedHorizon === 'GROWTH' && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                        <span style={{ fontSize: 10, fontWeight: 700, color: '#a78bfa', background: 'rgba(139,92,246,0.12)', border: '1px solid rgba(139,92,246,0.3)', padding: '2px 8px', borderRadius: 4, letterSpacing: '0.05em' }}>GROWTH / MOMENTUM</span>
                        <span style={{ fontSize: 10, color: '#64748b' }}>Relaxed thresholds for high-volatility stocks</span>
                      </div>
                    )}
                    <SignalCard signal={activeSig} />
                    {/* Conviction Gate — per-layer pass/fail using signal.reasons */}
                    {(() => {
                      const r = activeSig.reasons as Record<string, unknown>;
                      const kscore = data?.ranking?.score;
                      const regime = (r.market_regime as string) || 'unknown';
                      // H2: use effective thresholds from tune_status watchdog/calibration (5-min cache)
                      const tuneStyleData = tuneStatus?.styles?.[selectedHorizon];
                      const baseMlThresh = tuneStyleData?.effective?.buy_threshold_bull ?? 0.558;
                      const mlThreshMap: Record<string, number> = {
                        bull: baseMlThresh,
                        neutral: Math.min(baseMlThresh + 0.05, 0.82),
                        high_vol: Math.min(baseMlThresh + 0.12, 0.85),
                        bear: Math.min(baseMlThresh + 0.12, 0.85),
                      };
                      const mlThresh = mlThreshMap[regime] ?? Math.min(baseMlThresh + 0.05, 0.82);
                      const mlProb = r.ml_probability != null ? Number(r.ml_probability) : null;
                      // ml_weight=0 means model AUC<0.50 — signal-engine gave it zero weight,
                      // so the gate mirrors that: soft-pass ML (same logic as Python gate)
                      const mlWeight = r.ml_weight != null ? Number(r.ml_weight) : null;
                      const rsi = r.rsi != null ? Number(r.rsi) : null;
                      const rsiLo = selectedHorizon === 'GROWTH' ? 50 : 45;
                      const rsiHi = selectedHorizon === 'GROWTH' ? 85 : 72;
                      const macdHist = Number(r.macd_hist || 0);
                      const macdExpanding = r.macd_hist_expanding !== undefined ? Boolean(r.macd_hist_expanding) : Boolean(r.macd_rising);
                      const macdFading = Boolean(r.macd_momentum_fading);
                      const macdCross = Boolean(r.macd_zero_cross_up);
                      const layers = [
                        { key: 'ks',  label: 'K-Score',  ok: kscore != null && kscore >= 55,  detail: kscore != null ? `${kscore.toFixed(0)} (min 55)` : 'unavailable', soft: false },
                        { key: 'up',  label: 'Uptrend',  ok: selectedHorizon === 'GROWTH' ? Boolean(r.trend_above_sma50) : Boolean(r.sma50_above_sma200) && Boolean(r.trend_above_sma50), detail: selectedHorizon === 'GROWTH' ? 'price > SMA50' : 'SMA50>200 & price>SMA50', soft: false },
                        { key: 'rsi', label: 'RSI',      ok: rsi != null && rsi >= rsiLo && rsi <= rsiHi, detail: rsi != null ? `${rsi.toFixed(0)} (${rsiLo}–${rsiHi})` : 'n/a', soft: false },
                        { key: 'mac', label: 'MACD',     ok: (macdHist > 0 || macdCross) && !macdFading, detail: `hist ${macdHist.toFixed(3)} ${macdExpanding ? '↑ expanding' : macdFading ? '⚠ fading' : '↓'}`, soft: true },
                        { key: 'obv', label: 'OBV',      ok: Boolean(r.obv_trend_bullish), detail: Boolean(r.obv_trend_bullish) ? 'confirming' : 'not confirming', soft: true },
                        { key: 'adx', label: 'ADX',      ok: Boolean(r.adx_trending), detail: r.adx != null ? `ADX ${Number(r.adx).toFixed(0)} (min 25)` : 'n/a', soft: true },
                        { key: 'ml',  label: 'ML Model', ok: mlProb == null || mlWeight === 0 || mlProb > mlThresh, detail: mlProb != null ? (mlWeight === 0 ? `AUC<0.50 — zero weight, gate skipped` : `${(mlProb*100).toFixed(0)}% vs ${(mlThresh*100).toFixed(0)}% (${regime})`) : 'no model', soft: true },
                      ];
                      const failed = layers.filter(l => !l.ok);
                      const softFailed = failed.filter(l => l.soft);
                      const hardFailed = failed.filter(l => !l.soft);
                      const hasRsiDiv = r.rsi_divergence === 'bearish';
                      const hasStochOB = Boolean(r.stoch_rsi_overbought);
                      let tier: 'FULL' | 'NEAR' | 'FAILED' = 'FAILED';
                      if (!hasRsiDiv && !hasStochOB) {
                        if (failed.length === 0) tier = 'FULL';
                        else if (hardFailed.length === 0 && softFailed.length === 1) tier = 'NEAR';
                      }
                      const tierColor = tier === 'FULL' ? '#4ade80' : tier === 'NEAR' ? '#fbbf24' : '#ef4444';
                      const tierLabel = tier === 'FULL' ? '✓ Full conviction' : tier === 'NEAR' ? '~ Near conviction (1 soft miss)' : '✗ Gate not met';
                      return (
                        <div style={{ marginTop: 8, padding: '8px 10px', background: 'rgba(15,23,42,0.8)', border: `1px solid ${tierColor}33`, borderRadius: 6 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                            <span style={{ fontSize: 10, fontWeight: 700, color: '#334155', letterSpacing: '0.05em' }}>CONVICTION GATE</span>
                            <span style={{ fontSize: 10, fontWeight: 700, color: tierColor }}>{tierLabel}</span>
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                            {layers.map(l => (
                              <div key={l.key} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                                <span style={{ fontSize: 10, color: l.ok ? '#4ade80' : l.soft ? '#fbbf24' : '#f87171', width: 10, flexShrink: 0 }}>{l.ok ? '✓' : '✗'}</span>
                                <span style={{ fontSize: 10, color: '#64748b', width: 62, flexShrink: 0 }}>{l.label}</span>
                                <span style={{ fontSize: 10, color: l.ok ? '#475569' : l.soft ? '#92400e' : '#7f1d1d' }}>{l.detail}</span>
                                {!l.ok && l.soft && <span style={{ fontSize: 9, color: '#334155', marginLeft: 2 }}>(soft)</span>}
                              </div>
                            ))}
                            {hasRsiDiv && <div style={{ fontSize: 10, color: '#f87171', marginTop: 2 }}>✗ Bearish RSI divergence — disqualifier</div>}
                            {hasStochOB && <div style={{ fontSize: 10, color: '#f87171', marginTop: 2 }}>✗ Stoch RSI overbought — disqualifier</div>}
                          </div>
                        </div>
                      );
                    })()}
                  </div>
                )}
                {!activeSig && (
                  <div style={{ padding: '20px', textAlign: 'center', color: '#334155', fontSize: 12, border: '1px solid #1e293b', borderRadius: 8 }}>
                    Loading {selectedHorizon} signal…
                  </div>
                )}
              </div>
            );
          })()}
          {/* Research Intelligence — INT-1, INT-2, INT-6 */}
          {(() => {
            const REC_COLOR: Record<string, string> = { 'STRONG BUY': '#4ade80', BUY: '#86efac', WATCH: '#facc15', AVOID: '#fb923c', SELL: '#f87171' };
            const REC_BG: Record<string, string> = { 'STRONG BUY': 'rgba(74,222,128,0.12)', BUY: 'rgba(74,222,128,0.08)', WATCH: 'rgba(250,204,21,0.12)', AVOID: 'rgba(251,146,60,0.12)', SELL: 'rgba(248,113,113,0.12)' };
            const activeSig = data?.signal;
            const sigConf = activeSig?.confidence ?? null;
            const resCore = researchSummary?.recommendation;
            const resScore = researchSummary?.overall_score ?? null;
            const resConf = researchSummary?.confidence ?? null;
            const resGenAt = researchSummary?.generated_at ? new Date(researchSummary.generated_at) : null;
            const resAge = resGenAt && !isNaN(resGenAt.getTime()) ? Math.floor((Date.now() - resGenAt.getTime()) / 3600000) : null;

            // Alignment logic (INT-2)
            const sigIsBuy = activeSig?.signal === 'BUY' || activeSig?.signal === 'HOLD';
            const resIsBuy = resCore === 'STRONG BUY' || resCore === 'BUY';
            const resIsNeg = resCore === 'WATCH' || resCore === 'AVOID' || resCore === 'SELL';
            let alignLabel = '', alignColor = '', alignBg = '', alignBorder = '';
            if (resCore) {
              if (sigIsBuy && resIsNeg) { alignLabel = 'DIVERGENT'; alignColor = '#f59e0b'; alignBg = 'rgba(245,158,11,0.12)'; alignBorder = 'rgba(245,158,11,0.3)'; }
              else if (sigIsBuy && resIsBuy && (resConf ?? 0) >= 65) { alignLabel = 'STRONGLY ALIGNED'; alignColor = '#4ade80'; alignBg = 'rgba(74,222,128,0.12)'; alignBorder = 'rgba(74,222,128,0.3)'; }
              else if (sigIsBuy && resIsBuy) { alignLabel = 'ALIGNED'; alignColor = '#86efac'; alignBg = 'rgba(74,222,128,0.08)'; alignBorder = 'rgba(74,222,128,0.25)'; }
              else if (sigIsBuy && resCore === 'WATCH') { alignLabel = 'PARTIALLY ALIGNED'; alignColor = '#facc15'; alignBg = 'rgba(250,204,21,0.1)'; alignBorder = 'rgba(250,204,21,0.25)'; }
              else { alignLabel = 'NEUTRAL'; alignColor = '#64748b'; alignBg = 'rgba(255,255,255,0.04)'; alignBorder = '#1e293b'; }
            }

            // Composite conviction (INT-6)
            const staleResearch = resAge !== null && resAge > 14 * 24;
            const convScore = sigConf !== null && resScore !== null && !staleResearch
              ? Math.round(sigConf * 0.5 + resScore * 0.5)
              : sigConf !== null ? Math.round(sigConf) : null;
            const convColor = convScore !== null ? (convScore >= 75 ? '#4ade80' : convScore >= 60 ? '#facc15' : '#f87171') : '#475569';
            const convLabel = sigConf !== null && resScore !== null && !staleResearch ? 'Conviction' : 'Signal conf.';

            if (!resCore && !researchSummary) {
              return (
                <div style={{ fontSize: 10, color: '#334155', padding: '8px 12px', borderRadius: 8, border: '1px dashed #1e293b', textAlign: 'center' }}>
                  No research report — <a href={`/research/${symbol}`} style={{ color: '#818cf8', textDecoration: 'none' }}>Generate</a> to see alignment
                </div>
              );
            }

            return (
              <div style={{ background: '#0a0f1e', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                {/* Research badge (INT-1) */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Research</span>
                    {resCore && (
                      <a href={`/research/${symbol}`} style={{ textDecoration: 'none' }}>
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4, color: REC_COLOR[resCore] ?? '#94a3b8', background: REC_BG[resCore] ?? 'rgba(255,255,255,0.05)', border: `1px solid ${(REC_COLOR[resCore] ?? '#475569')}44`, cursor: 'pointer' }}>
                          {resCore}
                        </span>
                      </a>
                    )}
                    {resScore !== null && <span style={{ fontSize: 10, color: '#64748b' }}>{resScore} pts</span>}
                    {resAge !== null && <span style={{ fontSize: 9, color: staleResearch ? '#f87171' : '#334155' }}>{resAge < 24 ? `${resAge}h ago` : `${Math.floor(resAge / 24)}d ago`}{staleResearch ? ' · STALE' : ''}</span>}
                    <button
                      onClick={handleResearchRefresh}
                      disabled={researchRefreshing}
                      title="Trigger a fresh research report"
                      style={{ marginLeft: 4, padding: '2px 7px', borderRadius: 5, border: '1px solid rgba(129,140,248,0.25)', background: 'rgba(129,140,248,0.08)', color: researchRefreshing ? '#f59e0b' : '#818cf8', cursor: researchRefreshing ? 'default' : 'pointer', fontSize: 10, fontWeight: 700, lineHeight: 1 }}
                    >
                      {researchRefreshing ? '…' : '↻'}
                    </button>
                    {researchTriggerMsg && <span style={{ fontSize: 9, color: '#4ade80', marginLeft: 4 }}>{researchTriggerMsg}</span>}
                    {!resCore && <span style={{ fontSize: 10, color: '#334155' }}>—</span>}
                  </div>
                  {/* Conviction gauge (INT-6) */}
                  {convScore !== null && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ fontSize: 9, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{convLabel}</span>
                      <span style={{ fontSize: 13, fontWeight: 800, color: convColor }}>{convScore}</span>
                      {sigConf !== null && resScore !== null && !staleResearch && (
                        <span style={{ fontSize: 9, color: '#334155' }} title={`Signal ${Math.round(sigConf)} × 50% + Research ${resScore} × 50%`}>ℹ</span>
                      )}
                    </div>
                  )}
                  {(() => {
                    const symBySym = symbolOutcomes?.by_symbol;
                    if (!symBySym?.length) return null;
                    const row = symBySym.find(r => r.symbol === symbol) ?? symBySym[0];
                    if (row.count < 3) return null;
                    const wr = Math.round(row.win_rate * 100);
                    const wrColor = wr >= 55 ? '#4ade80' : wr >= 45 ? '#facc15' : '#f87171';
                    return (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }} title={`${row.count} closed signals in last 90d`}>
                        <span style={{ fontSize: 9, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{activeHorizon} 90d</span>
                        <span style={{ fontSize: 12, fontWeight: 700, color: wrColor }}>{wr}%WR</span>
                        <span style={{ fontSize: 9, color: '#334155' }}>({row.count})</span>
                      </div>
                    );
                  })()}
                </div>
                {/* Alignment indicator (INT-2) */}
                {alignLabel && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4, color: alignColor, background: alignBg, border: `1px solid ${alignBorder}`, letterSpacing: '0.04em' }}>
                      {alignLabel}
                    </span>
                    {alignLabel === 'DIVERGENT' && (
                      <span style={{ fontSize: 10, color: '#64748b' }}>
                        Signal: {activeSig?.signal} (conf {Math.round(sigConf ?? 0)}) vs Research: {resCore} (conf {resConf}) — review before entering
                      </span>
                    )}
                    {alignLabel.includes('ALIGNED') && (
                      <span style={{ fontSize: 10, color: '#475569' }}>Signal and research agree — higher conviction setup</span>
                    )}
                  </div>
                )}
              </div>
            );
          })()}

          {/* Event Intelligence panel — catalyst/insider/congress scores from signal reasons */}
          {(() => {
            const r = data?.signal?.reasons as Record<string, unknown> | null | undefined;
            if (!r) return null;
            const catalystScore  = r?.catalyst_score  != null ? Number(r.catalyst_score)  : null;
            const insiderScore   = r?.insider_score   != null ? Number(r.insider_score)   : null;
            const congressScore  = r?.congress_score  != null ? Number(r.congress_score)  : null;
            const compositeScore = r?.composite_score != null ? Number(r.composite_score) : null;
            if (catalystScore == null && insiderScore == null && congressScore == null) return null;
            const sc = (n: number | null) => n == null ? '#6b7280' : n >= 60 ? '#22c55e' : n >= 30 ? '#f59e0b' : n < 0 ? '#ef4444' : '#6b7280';
            const fmt = (n: number | null) => n == null ? '—' : n.toFixed(0);
            const bar = (n: number | null, maxAbs = 100) => {
              if (n == null) return null;
              const pct = Math.max(0, Math.min(100, ((n + maxAbs) / (2 * maxAbs)) * 100));
              return <div style={{ flex: 1, height: 4, background: '#1f2937', borderRadius: 2 }}><div style={{ width: `${pct}%`, height: '100%', borderRadius: 2, background: sc(n) }} /></div>;
            };
            return (
              <div style={{ background: '#0a0f1e', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 14px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Event Intelligence</span>
                  <a href="/intelligence" style={{ fontSize: 9, color: '#f59e0b', textDecoration: 'none', opacity: 0.7 }}>Full dashboard →</a>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                  {[
                    { label: 'Catalyst', val: catalystScore, tip: 'Composite: earnings × insider × congress × institutional' },
                    { label: 'Insider',  val: insiderScore,  tip: 'Net insider buying pressure (−100 to +100, role-weighted)' },
                    { label: 'Congress', val: congressScore, tip: 'Congressional buying interest (0–100, net purchase bias)' },
                    { label: 'AI Composite', val: compositeScore, tip: '0.5×catalyst + 0.3×(100−risk) + 0.2×earnings' },
                  ].map(({ label, val, tip }) => (
                    <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 6 }} title={tip}>
                      <span style={{ fontSize: 10, color: '#64748b', width: 70, flexShrink: 0 }}>{label}</span>
                      {bar(val)}
                      <span style={{ fontSize: 10, fontWeight: 700, color: sc(val), width: 28, textAlign: 'right', flexShrink: 0 }}>{fmt(val)}</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}

          {signalHistory && signalHistory.length >= 2 && (
            <ConfidenceTrend history={signalHistory} />
          )}

          {/* Signal History — recent transitions */}
          {signalHistory && signalHistory.length >= 2 && (() => {
            const SIG_C: Record<string, string> = { BUY: '#4ade80', SELL: '#f87171', HOLD: '#94a3b8', WAIT: '#fbbf24' };
            // Dedupe to transitions only (reversed = newest first)
            const transitions: SignalHistoryPoint[] = [];
            for (const h of [...signalHistory].reverse()) {
              if (transitions.length === 0 || h.signal !== transitions[transitions.length - 1].signal) {
                transitions.push(h);
                if (transitions.length >= 5) break;
              }
            }
            if (transitions.length < 2) return null;
            return (
              <div style={{ background: '#0a0f1e', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 14px' }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>
                  Signal History
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                  {transitions.map((h, i) => {
                    const color = SIG_C[h.signal] ?? '#94a3b8';
                    const date = h.ts ? new Date(h.ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '—';
                    const bullPct = h.bullish_probability != null ? Math.round(h.bullish_probability * 100) : null;
                    return (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 6px', borderRadius: 5, background: i === 0 ? `${color}0f` : 'transparent' }}>
                        <span style={{ fontSize: 9, fontWeight: 700, color: i === 0 ? color : '#334155', width: 26, flexShrink: 0 }}>
                          {i === 0 ? 'NOW' : `−${i}`}
                        </span>
                        <span style={{ fontSize: 10, color: '#475569', fontFamily: 'monospace', width: 48, flexShrink: 0 }}>{date}</span>
                        <span style={{ fontSize: 11, fontWeight: 700, color, width: 34, flexShrink: 0 }}>{h.signal}</span>
                        <span style={{ fontSize: 10, color: '#64748b' }}>{Math.round(h.confidence ?? 0)}%</span>
                        {bullPct != null && <span style={{ fontSize: 10, color: '#475569', marginLeft: 'auto' }}>{bullPct}% bull</span>}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })()}

          {/* Fair Value */}
          {ranking?.fair_price != null && (() => {
            const lp2 = allPrices?.find(p => p.symbol === symbol);
            const curPx = lp2?.price ?? data.prices?.at(-1)?.close ?? null;
            const fv = ranking.fair_price;
            const pct = curPx != null ? ((fv - curPx) / curPx) * 100 : null;
            const isUpside = pct != null && pct > 0;
            return (
              <div style={{ background: '#0f172a', border: '1px solid #312e81', borderRadius: 8, padding: '14px 16px', textAlign: 'center' }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#818cf8', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>Fair Value (K-Score)</div>
                <div style={{ fontSize: 28, fontWeight: 800, color: '#a5b4fc', lineHeight: 1 }}>${fv.toFixed(2)}</div>
                {pct != null && (
                  <div style={{ fontSize: 14, fontWeight: 700, marginTop: 5, color: isUpside ? '#4ade80' : '#f87171' }}>
                    {pct >= 0 ? '+' : ''}{pct.toFixed(1)}% {isUpside ? 'upside' : 'overvalued'}
                  </div>
                )}
                {curPx != null && (
                  <div style={{ fontSize: 11, color: '#475569', marginTop: 3 }}>vs current ${curPx.toFixed(2)}</div>
                )}
                {ranking.score != null && (
                  <div style={{ fontSize: 11, color: '#4f46e5', marginTop: 4, fontWeight: 600 }}>K-Score {ranking.score.toFixed(0)}/100</div>
                )}
              </div>
            );
          })()}

          {/* Confluence Score + Trade Setup */}
          {(() => {
            const ranking = data.ranking;
            if (!ranking) return null;
            const sig = data.signal
              ? { signal: data.signal.signal, confidence: data.signal.confidence }
              : undefined;
            const recMean = data.fundamentals?.recommendation_mean ?? null;
            const cs = confluenceScoreFull(ranking, sig, recMean);
            const grade = confluenceGrade(cs);
            const lp2 = allPrices?.find(p => p.symbol === symbol);
            const curPx = lp2?.price ?? data.prices?.at(-1)?.close;
            const supports = (data.levels?.support_resistance ?? [])
              .filter(l => l.kind === 'support' && (curPx == null || l.price < curPx))
              .sort((a, b) => b.price - a.price);
            const resistances = (data.levels?.support_resistance ?? [])
              .filter(l => l.kind === 'resistance' && (curPx == null || l.price > curPx))
              .sort((a, b) => a.price - b.price);
            const entryZone = supports[0]?.price;
            const exitMean = data.fundamentals?.target_price;
            const exitHigh = data.fundamentals?.target_high;
            const exitFair = ranking.fair_price;
            return (
              <div style={{ background: '#0f172a', border: `1px solid ${grade.color}30`, borderRadius: 8, padding: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8' }}>Confluence Score</span>
                  <span style={{ fontSize: 10, color: '#475569' }}>AI · K-Score · Analyst · TA · Mom</span>
                </div>
                {/* Score bar */}
                <div style={{ marginBottom: 10 }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
                    <span style={{ fontSize: 32, fontWeight: 800, color: grade.color, lineHeight: 1 }}>{cs}</span>
                    <span style={{ fontSize: 13, fontWeight: 700, color: grade.color }}>{grade.label}</span>
                    <span style={{ fontSize: 11, color: '#475569', marginLeft: 'auto' }}>/100</span>
                  </div>
                  <div style={{ height: 6, borderRadius: 3, background: '#1e293b', overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${cs}%`, background: grade.color, borderRadius: 3, transition: 'width 0.4s' }} />
                  </div>
                  <div style={{ fontSize: 11, color: '#64748b', marginTop: 5 }}>{grade.description}</div>
                </div>
                {/* Position size recommendation */}
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 10px', borderRadius: 6, background: 'rgba(255,255,255,0.03)', border: '1px solid #1e293b', marginBottom: 10 }}>
                  <span style={{ fontSize: 11, color: '#64748b' }}>Max position size</span>
                  <span style={{ fontSize: 12, fontWeight: 700, color: grade.color }}>{grade.maxPositionPct}</span>
                </div>
                {/* Entry / Exit targets */}
                <div style={{ fontSize: 11, color: '#64748b', marginBottom: 5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Trade setup</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {entryZone && (
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: 11, color: '#64748b' }}>Entry zone (nearest support)</span>
                      <span style={{ fontSize: 12, fontWeight: 600, color: '#86efac' }}>${entryZone.toFixed(2)}</span>
                    </div>
                  )}
                  {resistances[0] && (
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: 11, color: '#64748b' }}>Nearest resistance</span>
                      <span style={{ fontSize: 12, fontWeight: 600, color: '#f87171' }}>${resistances[0].price.toFixed(2)}</span>
                    </div>
                  )}
                  {exitMean && (
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: 11, color: '#64748b' }}>Target 1 (analyst mean)</span>
                      <span style={{ fontSize: 12, fontWeight: 600, color: '#facc15' }}>${exitMean.toFixed(2)}</span>
                    </div>
                  )}
                  {exitHigh && (
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: 11, color: '#64748b' }}>Target 2 (analyst high)</span>
                      <span style={{ fontSize: 12, fontWeight: 600, color: '#818cf8' }}>${exitHigh.toFixed(2)}</span>
                    </div>
                  )}
                  {exitFair && (
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: 11, color: '#64748b' }}>K-Score fair value</span>
                      <span style={{ fontSize: 12, fontWeight: 600, color: '#38bdf8' }}>
                        ${exitFair.toFixed(2)}
                        {curPrice != null && (
                          <span style={{ marginLeft: 6, fontSize: 11, color: exitFair > curPrice ? '#4ade80' : '#f87171' }}>
                            {exitFair > curPrice ? '+' : ''}{(((exitFair - curPrice) / curPrice) * 100).toFixed(1)}%
                          </span>
                        )}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            );
          })()}

          {/* Position Sizer */}
          {(() => {
            const lp2 = allPrices?.find(p => p.symbol === symbol);
            const curPx = lp2?.price ?? data.prices?.at(-1)?.close ?? undefined;
            const nearestSupport = data.levels?.support_resistance
              ?.filter(l => curPx == null || l.price < curPx)
              .sort((a, b) => b.price - a.price)[0]?.price ?? undefined;
            return (
              <PositionSizer
                symbol={symbol as string}
                entryPrice={curPx}
                atrStop={atrData?.stop_loss_2atr ?? null}
                atr={atrData?.atr ?? null}
                stopLoss={nearestSupport}
                takeProfit={data.fundamentals?.target_price ?? undefined}
                currency={lp2?.currency}
              />
            );
          })()}

          {/* Fair Value Gap trade plan — a distinct, structural-level-based alternative to the
              ATR/support-based Position Sizer above. Shown as its own reference, not silently
              merged into PositionSizer's numbers, so a user can compare the two approaches
              rather than have one invisibly override the other. */}
          {(() => {
            const lp3 = allPrices?.find(p => p.symbol === symbol);
            const curPx = lp3?.price ?? data.prices?.at(-1)?.close ?? undefined;
            const plan = nearestActionableFvg(data.levels?.fair_value_gaps, curPx ?? null);
            if (!plan) return null;
            const isLong = plan.gap.kind === 'bullish';

            // Combination 1: is this gap's far edge also a real swing pivot? (corroborating
            // structure, not just "the nearest untraded pocket"). Combination 2: does this gap
            // overlap real volume-profile conviction (POC/HVN), or sit in a thin/untraded zone?
            // Both computed here (not inside PriceChart.tsx) since this card is the trade-plan
            // decision surface — see the "Feature Combinations" reference for the full rationale.
            const bars = data.prices ?? [];
            const pivots = bars.length > 0 ? detectSwingPivots(bars, 5) : [];
            const pivotAnchor = nearestPivotToFvg(plan.gap, pivots);
            const profile = bars.length > 0 ? computeVolumeProfile(bars, 24) : null;
            const volumeContext = classifyFvgVolumeContext(plan.gap, profile);

            return (
              <div style={{ background: '#1e293b', borderRadius: 10, padding: '14px 18px', border: '1px solid #334155', marginTop: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 600, color: '#f1f5f9', fontSize: 13 }}>Fair Value Gap Trade Plan</span>
                  <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4,
                    background: isLong ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
                    color: isLong ? '#4ade80' : '#f87171' }}>
                    {isLong ? 'LONG (bullish gap)' : 'SHORT (bearish gap)'}
                  </span>
                  {pivotAnchor && (
                    <span
                      title={`This gap's far edge ($${(isLong ? plan.gap.bottom : plan.gap.top).toFixed(2)}) sits within ${(pivotAnchor.distancePct * 100).toFixed(1)}% of a real swing ${pivotAnchor.pivot.kind} at $${pivotAnchor.pivot.price.toFixed(2)} — corroborating structure, not just the nearest untraded pocket.`}
                      style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: 'rgba(96,165,250,0.15)', color: '#60a5fa', cursor: 'help' }}
                    >
                      ⚓ Pivot-anchored
                    </span>
                  )}
                  {(volumeContext === 'poc' || volumeContext === 'hvn') && (
                    <span
                      title={volumeContext === 'poc'
                        ? 'This gap zone contains the Point of Control — the single price level with the most historical volume. Real conviction behind this level, a stronger candidate to hold on retest.'
                        : 'This gap zone contains a High Volume Node — a local volume peak. Real historical conviction behind this level, a stronger candidate to hold on retest.'}
                      style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: 'rgba(250,204,21,0.15)', color: '#facc15', cursor: 'help' }}
                    >
                      {volumeContext === 'poc' ? '📊 At POC' : '📊 At HVN'}
                    </span>
                  )}
                  {volumeContext === 'thin' && (
                    <span
                      title="This gap zone overlaps the volume profile, but at a comparatively low-volume price level — thinner historical conviction, more likely to be sliced through than held."
                      style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: 'rgba(148,163,184,0.15)', color: '#94a3b8', cursor: 'help' }}
                    >
                      📊 Thin zone
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 11, color: '#64748b', marginBottom: 10 }}>
                  Based on the nearest unfilled {isLong ? 'bullish' : 'bearish'} gap {isLong ? 'below' : 'above'} the
                  current price (${curPx?.toFixed(2)}) — a 3-candle imbalance price often retraces into before
                  continuing. Entry = gap midpoint; stop = just past the gap's far edge (a full fill invalidates
                  the setup); target = {plan.rr.toFixed(1)}:1 reward:risk from the gap's own size.
                </div>
                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 12 }}>
                  <div><span style={{ color: '#64748b' }}>Gap zone:</span> <span style={{ color: '#f1f5f9', fontWeight: 600 }}>${plan.gap.bottom.toFixed(2)} – ${plan.gap.top.toFixed(2)}</span></div>
                  <div><span style={{ color: '#64748b' }}>Entry:</span> <span style={{ color: '#4ade80', fontWeight: 600 }}>${plan.entry.toFixed(2)}</span></div>
                  <div><span style={{ color: '#64748b' }}>Stop:</span> <span style={{ color: '#f87171', fontWeight: 600 }}>${plan.stop.toFixed(2)}</span></div>
                  <div><span style={{ color: '#64748b' }}>Target:</span> <span style={{ color: '#a78bfa', fontWeight: 600 }}>${plan.target.toFixed(2)}</span></div>
                  <div><span style={{ color: '#64748b' }}>R:R:</span> <span style={{ color: '#f1f5f9', fontWeight: 600 }}>{plan.rr.toFixed(1)}:1</span></div>
                </div>
              </div>
            );
          })()}

          {/* T258-ACCUM-DIST-BREAKOUT-QUALITY: a volume-PATTERN-based accumulation/distribution
              read + a breakout-quality assessment (real/failed/unconfirmed), replacing what the
              Volume Profile docs previously taught as a manual chart-reading exercise
              ("poke-and-reject = false breakout"). No block-trade/dark-pool data source exists
              anywhere in this app — both reads are framed honestly as pattern-derived, not true
              institutional-flow detection. Shown only when there's something to say (a state
              other than the two-signals-disagree default, or an actual breakout in play). */}
          {(() => {
            const ad = data.levels?.accumulation_distribution;
            const bq = data.levels?.breakout_quality;
            if (!ad && !bq) return null;
            const AD_COLOR: Record<string, string> = { accumulation: '#4ade80', distribution: '#f87171', neutral: '#94a3b8' };
            const BQ_COLOR: Record<string, string> = { real: '#4ade80', failed: '#f87171', unconfirmed: '#fbbf24' };
            return (
              <div style={{ background: '#1e293b', borderRadius: 10, padding: '14px 18px', border: '1px solid #334155', marginTop: 12 }}>
                <div style={{ fontWeight: 600, color: '#f1f5f9', fontSize: 13, marginBottom: 8 }}>Volume Pattern Read</div>
                <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', fontSize: 12 }}>
                  {ad && (
                    <div>
                      <span style={{ color: '#64748b' }}>Accumulation/Distribution:</span>{' '}
                      <span style={{ color: AD_COLOR[ad.state], fontWeight: 600, textTransform: 'capitalize' }}>{ad.state}</span>
                      {ad.updown_vol_ratio != null && (
                        <span style={{ color: '#64748b' }}> (up/down vol {ad.updown_vol_ratio.toFixed(2)}x)</span>
                      )}
                    </div>
                  )}
                  {bq && (
                    <div>
                      <span style={{ color: '#64748b' }}>Breakout Quality:</span>{' '}
                      <span style={{ color: BQ_COLOR[bq.quality], fontWeight: 600, textTransform: 'capitalize' }}>{bq.quality}</span>
                      <span style={{ color: '#64748b' }}>
                        {' '}({bq.direction === 'up' ? 'above' : 'below'} ${bq.level.toFixed(2)}, {bq.breakout_rvol != null ? `${bq.breakout_rvol.toFixed(1)}x vol` : 'no vol data'})
                      </span>
                    </div>
                  )}
                </div>
                <div style={{ fontSize: 11, color: '#64748b', marginTop: 8 }}>
                  Volume-pattern-based reads (OBV trend + up/down-day volume ratio; next-bar hold
                  + relative volume on the breakout bar) — no institutional/block-trade data
                  source exists, so these describe price/volume PATTERNS, not confirmed
                  institutional flow.
                </div>
              </div>
            );
          })()}

          {/* Signal Consensus + per-horizon alert subscriptions */}
          {(() => {
            const SIG_C: Record<string, string> = { BUY: '#4ade80', SELL: '#f87171', WAIT: '#fbbf24', HOLD: '#94a3b8' };
            const SIG_BG: Record<string, string> = { BUY: 'rgba(74,222,128,0.1)', SELL: 'rgba(239,68,68,0.1)', WAIT: 'rgba(251,191,36,0.08)', HOLD: 'rgba(148,163,184,0.06)' };
            const email = (typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') : null) ?? '';

            const directions = allHorizonSignals.map(h => h.sig?.signal).filter(Boolean);
            const buyCount  = directions.filter(d => d === 'BUY').length;
            const sellCount = directions.filter(d => d === 'SELL').length;
            const consensusLabel = directions.length === 0 ? null
              : buyCount >= 3 ? 'Strong bullish'
              : buyCount === 2 ? 'Moderately bullish'
              : sellCount >= 3 ? 'Strong bearish'
              : sellCount === 2 ? 'Moderately bearish'
              : 'Mixed — check entry timing';
            const consensusColor = buyCount >= 3 ? '#4ade80' : buyCount === 2 ? '#a3e635' : sellCount >= 3 ? '#f87171' : sellCount === 2 ? '#fb923c' : '#fbbf24';

            async function toggleHorizon(horizon: string) {
              setSignalAlertError('');
              setSignalAlertSaving(true);
              const existing = signalAlerts?.find(a => a.symbol === symbol && a.horizon === horizon);
              try {
                if (existing) {
                  await api.deleteSignalAlert(existing.id);
                } else {
                  if (!email) { setSignalAlertError('Set an email in Settings → Profile first'); return; }
                  await api.createSignalAlert(symbol as string, email, 'all', horizon);
                }
                await mutateSignalAlerts();
              } catch (err: unknown) {
                const msg = err instanceof Error ? err.message : String(err);
                setSignalAlertError(msg.includes('400') ? 'Set an email in Settings → Profile first' : 'Failed to save alert');
              } finally { setSignalAlertSaving(false); }
            }

            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {/* Consensus banner */}
                <div style={{ padding: '8px 12px', borderRadius: '8px', background: 'rgba(15,23,42,0.8)', border: '1px solid #1e293b' }}>
                  <div style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em', marginBottom: '6px' }}>SIGNAL CONSENSUS</div>
                  <div style={{ display: 'flex', gap: '4px', marginBottom: '6px' }}>
                    {allHorizonSignals.map(({ label, sig }) => (
                      <div key={label} style={{ flex: 1, textAlign: 'center', padding: '4px 2px', borderRadius: '6px', background: SIG_BG[sig?.signal ?? ''] ?? 'rgba(255,255,255,0.02)', border: `1px solid ${SIG_C[sig?.signal ?? ''] ?? '#1e293b'}33` }}>
                        <div style={{ fontSize: '9px', color: '#475569', marginBottom: '2px' }}>{label}</div>
                        <div style={{ fontSize: '11px', fontWeight: 800, color: SIG_C[sig?.signal ?? ''] ?? '#334155' }}>
                          {sig?.signal ?? '—'}
                        </div>
                        {sig?.confidence != null && (
                          <div style={{ fontSize: '9px', color: '#334155', marginTop: '1px' }}>{sig.confidence.toFixed(0)}%</div>
                        )}
                      </div>
                    ))}
                  </div>
                  {consensusLabel && (
                    <div style={{ fontSize: '11px', fontWeight: 600, color: consensusColor }}>
                      {consensusLabel}
                    </div>
                  )}
                </div>

                {/* Per-horizon alert rows */}
                <div style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em', marginTop: '2px', marginBottom: '2px' }}>SIGNAL ALERTS</div>
                {allHorizonSignals.map(({ label, horizon, sig }) => {
                  const sub = signalAlerts?.find(a => a.symbol === symbol && a.horizon === horizon);
                  const active = !!sub;
                  return (
                    <div key={horizon} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '7px 10px', borderRadius: '7px', border: active ? '1px solid rgba(99,102,241,0.4)' : '1px solid #1e293b', background: active ? 'rgba(99,102,241,0.07)' : 'rgba(255,255,255,0.01)' }}>
                      <span style={{ fontSize: '10px', fontWeight: 700, color: '#475569', width: '42px', flexShrink: 0 }}>{label}</span>
                      <span style={{ fontSize: '11px', fontWeight: 700, color: SIG_C[sig?.signal ?? ''] ?? '#334155', width: '36px', flexShrink: 0 }}>{sig?.signal ?? '—'}</span>
                      <span style={{ flex: 1, fontSize: '10px', color: '#334155' }}>
                        {active && sub.last_signal ? `Last sent: ${sub.last_signal}` : active ? 'Watching' : ''}
                      </span>
                      <button
                        onClick={() => toggleHorizon(horizon)}
                        disabled={signalAlertSaving}
                        title={active ? `Stop ${label} signal alerts` : `Get emailed when ${label} signal improves`}
                        style={{ fontSize: '13px', background: 'none', border: 'none', cursor: 'pointer', padding: '2px', color: active ? '#818cf8' : '#334155', transition: 'color 0.15s' }}
                      >
                        {active ? '🔔' : '🔕'}
                      </button>
                    </div>
                  );
                })}
                {signalAlertError && (
                  <p style={{ margin: '2px 0 0', fontSize: '11px', color: '#f87171' }}>{signalAlertError}</p>
                )}
              </div>
            );
          })()}

          {/* Game Plan — only for BUY/HOLD signals; hidden for WAIT/SELL */}
          {isAiConfigured() && (() => {
            const gpSig = allHorizonSignals.find(h => h.horizon === selectedHorizon)?.sig
              ?? (selectedHorizon === 'SWING' ? data.signal : data.signal);
            const gpDirection = gpSig?.signal;
            if (gpDirection === 'WAIT' || gpDirection === 'SELL') return null;
            const gpButtonLabel = 'Generate 10-Day Game Plan';
            return (
            <div>
              {/* Generate button */}
              {!gamePlan && !gamePlanLoading && (
                <button
                  onClick={generateGamePlan}
                  style={{
                    width: '100%', display: 'flex', alignItems: 'center', gap: '8px',
                    padding: '9px 14px', borderRadius: '8px', cursor: 'pointer',
                    border: '1px solid rgba(34,197,94,0.3)',
                    background: 'rgba(34,197,94,0.06)',
                    color: '#4ade80', fontSize: '12px', fontWeight: 600,
                    transition: 'all 0.15s', textAlign: 'left',
                  }}
                >
                  <span style={{ fontSize: '14px' }}>📋</span>
                  <span style={{ flex: 1 }}>{gpButtonLabel}</span>
                  <span style={{ fontSize: '10px', color: '#22c55e', opacity: 0.7 }}>AI</span>
                </button>
              )}

              {/* Loading */}
              {gamePlanLoading && (
                <div style={{ padding: '14px 16px', borderRadius: '8px', border: '1px solid rgba(34,197,94,0.2)', background: 'rgba(15,23,42,0.6)', display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{ fontSize: '14px', display: 'inline-block', animation: 'spin 0.8s linear infinite' }}>↻</span>
                  <span style={{ fontSize: '12px', color: '#64748b' }}>Generating game plan…</span>
                </div>
              )}

              {/* Error */}
              {gamePlanError && (
                <div style={{ padding: '10px 14px', borderRadius: '8px', border: '1px solid rgba(248,113,113,0.3)', background: 'rgba(248,113,113,0.06)', fontSize: '11px', color: '#f87171' }}>
                  {gamePlanError}
                </div>
              )}

              {/* Game Plan Card */}
              {gamePlan && (
                <div style={{ borderRadius: '10px', border: '1px solid rgba(34,197,94,0.25)', background: 'rgba(15,23,42,0.85)', overflow: 'hidden' }}>
                  {/* Header */}
                  <div style={{ padding: '11px 14px', background: 'rgba(34,197,94,0.08)', borderBottom: '1px solid rgba(34,197,94,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
                      <span style={{ fontSize: '13px' }}>📋</span>
                      <span style={{ fontSize: '12px', fontWeight: 700, color: '#4ade80' }}>{gamePlan.title}</span>
                    </div>
                    <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
                      <button
                        onClick={copyGamePlan}
                        title="Copy as text"
                        style={{ background: copied ? 'rgba(34,197,94,0.1)' : 'transparent', border: `1px solid ${copied ? 'rgba(34,197,94,0.3)' : 'rgba(255,255,255,0.06)'}`, borderRadius: '5px', padding: '2px 7px', color: copied ? '#4ade80' : '#475569', cursor: 'pointer', fontSize: '11px' }}
                      >
                        {copied ? '✓' : '⎘'}
                      </button>
                      <button
                        onClick={saveGamePlanToBoard}
                        disabled={savingToBoard || savedToBoard}
                        title={saveBoardError || (savedToBoard ? 'Saved to Trade Board' : 'Save to Trade Board')}
                        style={{ background: savedToBoard ? 'rgba(129,140,248,0.15)' : saveBoardError ? 'rgba(248,113,113,0.1)' : 'transparent', border: `1px solid ${savedToBoard ? 'rgba(129,140,248,0.4)' : saveBoardError ? 'rgba(248,113,113,0.4)' : 'rgba(255,255,255,0.06)'}`, borderRadius: '5px', padding: '2px 7px', color: savedToBoard ? '#818cf8' : saveBoardError ? '#f87171' : '#475569', cursor: savedToBoard ? 'default' : 'pointer', fontSize: '11px', opacity: savingToBoard ? 0.5 : 1 }}
                      >
                        {savingToBoard ? '…' : savedToBoard ? '📌' : saveBoardError ? '⚠ Failed' : '📌 Save'}
                      </button>
                      <button onClick={() => setGamePlanOpen(o => !o)} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '12px', padding: '2px 4px' }}>
                        {gamePlanOpen ? '▲' : '▼'}
                      </button>
                      <button onClick={() => { setGamePlan(null); setGamePlanError(''); setSavedToBoard(false); }} style={{ background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '14px', padding: '2px 4px' }} title="Clear">✕</button>
                    </div>
                  </div>

                  {gamePlanOpen && (
                    <div style={{ padding: '14px', display: 'flex', flexDirection: 'column', gap: '12px' }}>

                      {/* Entries */}
                      <div>
                        <div style={{ fontSize: '10px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '7px' }}>Entry Strategy</div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                          {(gamePlan.entries ?? []).map((e, i) => {
                            const isBreakout = e.label.toLowerCase().includes('breakout');
                            return (
                              <div key={i} style={{ padding: '8px 10px', borderRadius: '6px', border: `1px solid ${isBreakout ? 'rgba(251,191,36,0.25)' : 'rgba(34,197,94,0.2)'}`, background: isBreakout ? 'rgba(251,191,36,0.05)' : 'rgba(34,197,94,0.05)' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2px' }}>
                                  <span style={{ fontSize: '11px', fontWeight: 700, color: isBreakout ? '#fbbf24' : '#4ade80' }}>{e.label}</span>
                                  <span style={{ fontSize: '13px', fontWeight: 800, color: isBreakout ? '#fbbf24' : '#4ade80', fontFamily: 'monospace' }}>${e.price.toFixed(2)}</span>
                                </div>
                                <div style={{ fontSize: '10px', color: '#64748b', lineHeight: 1.4 }}>{e.rationale}</div>
                              </div>
                            );
                          })}
                        </div>
                      </div>

                      {/* Stop & Target */}
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                        <div style={{ padding: '8px 10px', borderRadius: '6px', border: '1px solid rgba(248,113,113,0.25)', background: 'rgba(248,113,113,0.05)' }}>
                          <div style={{ fontSize: '10px', fontWeight: 700, color: '#f87171', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '3px' }}>Stop Loss</div>
                          <div style={{ fontSize: '14px', fontWeight: 800, color: '#f87171', fontFamily: 'monospace' }}>${(gamePlan.stop_loss?.price ?? 0).toFixed(2)}</div>
                          <div style={{ fontSize: '10px', color: '#64748b', marginTop: '2px', lineHeight: 1.3 }}>{gamePlan.stop_loss?.rationale ?? ''}</div>
                        </div>
                        {gamePlan.take_profit && (
                          <div style={{ padding: '8px 10px', borderRadius: '6px', border: '1px solid rgba(99,102,241,0.25)', background: 'rgba(99,102,241,0.05)' }}>
                            <div style={{ fontSize: '10px', fontWeight: 700, color: '#818cf8', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '3px' }}>Take Profit</div>
                            <div style={{ fontSize: '14px', fontWeight: 800, color: '#818cf8', fontFamily: 'monospace' }}>${gamePlan.take_profit.price.toFixed(2)}</div>
                            <div style={{ fontSize: '10px', color: '#64748b', marginTop: '2px', lineHeight: 1.3 }}>{gamePlan.take_profit.rationale}</div>
                          </div>
                        )}
                      </div>

                      {/* Catalysts */}
                      <div>
                        <div style={{ fontSize: '10px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '7px' }}>Catalysts in the Window</div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                          {(gamePlan.catalysts ?? []).map((c, i) => (
                            <div key={i} style={{ display: 'flex', gap: '7px', fontSize: '11px', color: '#94a3b8', lineHeight: 1.4 }}>
                              <span style={{ color: '#4ade80', flexShrink: 0 }}>›</span>
                              <span>{c}</span>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Risk */}
                      <div style={{ padding: '9px 12px', borderRadius: '6px', border: '1px solid rgba(251,191,36,0.2)', background: 'rgba(251,191,36,0.05)', display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
                        <span style={{ fontSize: '12px', flexShrink: 0, marginTop: '1px' }}>⚠</span>
                        <div>
                          <div style={{ fontSize: '10px', fontWeight: 700, color: '#fbbf24', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '2px' }}>Key Risk</div>
                          <div style={{ fontSize: '11px', color: '#94a3b8', lineHeight: 1.4 }}>{gamePlan.risk}</div>
                        </div>
                      </div>

                      {/* Regenerate */}
                      <button
                        onClick={generateGamePlan}
                        disabled={gamePlanLoading}
                        style={{ fontSize: '11px', color: '#475569', background: 'none', border: '1px solid #1e293b', borderRadius: '6px', padding: '5px 10px', cursor: 'pointer', width: '100%' }}
                      >
                        ↻ Regenerate
                      </button>

                    </div>
                  )}
                </div>
              )}
            </div>
          );
          })()}

        </div>
      </div>


      {/* Company Financials — full width */}
      {data.fundamentals && (() => {
        const f = data.fundamentals!;

        function fmtBig(n: number | null | undefined): string {
          if (n == null) return '—';
          const abs = Math.abs(n);
          if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
          if (abs >= 1e9)  return `${(n / 1e9).toFixed(2)}B`;
          if (abs >= 1e6)  return `${(n / 1e6).toFixed(2)}M`;
          if (abs >= 1e3)  return `${(n / 1e3).toFixed(1)}K`;
          return n.toFixed(2);
        }
        function fmtPct(n: number | null | undefined): string {
          if (n == null) return '—';
          return `${(n * 100).toFixed(1)}%`;
        }
        function fmtX(n: number | null | undefined): string {
          if (n == null) return '—';
          return `${n.toFixed(1)}x`;
        }
        function fmtNum(n: number | null | undefined, d = 2): string {
          if (n == null) return '—';
          return n.toFixed(d);
        }
        function growthColor(n: number | null | undefined): string {
          if (n == null) return '#94a3b8';
          return n >= 0 ? '#4ade80' : '#f87171';
        }

        const recColors: Record<string, string> = {
          buy: '#4ade80', 'strong_buy': '#22c55e',
          hold: '#facc15', neutral: '#facc15',
          sell: '#f87171', 'strong_sell': '#ef4444',
          underperform: '#fb923c', outperform: '#86efac',
        };
        const recLabel: Record<string, string> = {
          buy: 'BUY', strong_buy: 'STRONG BUY',
          hold: 'HOLD', neutral: 'NEUTRAL',
          sell: 'SELL', strong_sell: 'STRONG SELL',
          underperform: 'UNDERPERFORM', outperform: 'OUTPERFORM',
        };

        const card = (label: string, value: string, sub?: string, valueColor?: string) => (
          <div key={label} style={{ background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid #1e293b', padding: '10px 13px' }}>
            <div style={{ fontSize: '10px', color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '4px' }}>{label}</div>
            <div style={{ fontSize: '15px', fontWeight: 700, color: valueColor ?? '#e2e8f0' }}>{value}</div>
            {sub && <div style={{ fontSize: '10px', color: '#475569', marginTop: '2px' }}>{sub}</div>}
          </div>
        );

        const hi = f.week_52_high, lo = f.week_52_low;
        const rangePct = (hi && lo && hi > lo) ? ((((curPrice ?? lo) - lo) / (hi - lo)) * 100) : null;

        return (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: '12px' }}>
              <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#cbd5e1', margin: 0 }}>Company Financials</h2>
              {f.fetched_at && (() => {
                const daysOld = Math.floor((Date.now() - new Date(f.fetched_at!).getTime()) / 86400000);
                const stale = daysOld > 90;
                return (
                  <span style={{ fontSize: 10, color: stale ? '#fbbf24' : '#475569', background: stale ? 'rgba(251,191,36,0.1)' : 'transparent', border: stale ? '1px solid rgba(251,191,36,0.3)' : 'none', borderRadius: 4, padding: stale ? '1px 6px' : 0 }}>
                    {stale ? `⚠ ${daysOld}d old` : `as of ${new Date(f.fetched_at!).toLocaleDateString()}`}
                  </span>
                );
              })()}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>

              {/* Row 1 — Valuation */}
              <div>
                <div style={{ fontSize: '10px', fontWeight: 700, color: '#4f46e5', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Valuation</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: '8px' }}>
                  {card('Market Cap', fmtBig(f.market_cap))}
                  {card('Enterprise Value', fmtBig(f.enterprise_value))}
                  {card('P/E (TTM)', fmtX(f.trailing_pe))}
                  {card('Forward P/E', fmtX(f.forward_pe))}
                  {card('P/B Ratio', fmtX(f.price_to_book))}
                  {card('EV / Sales', fmtX(f.ev_to_revenue))}
                  {card('EV / EBITDA', fmtX(f.ev_to_ebitda))}
                </div>
              </div>

              {/* Row 2 — Income + Cash */}
              <div>
                <div style={{ fontSize: '10px', fontWeight: 700, color: '#0891b2', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Financials (TTM)</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: '8px' }}>
                  {card('Revenue', fmtBig(f.total_revenue), f.revenue_growth != null ? `${f.revenue_growth >= 0 ? '+' : ''}${fmtPct(f.revenue_growth)} YoY` : undefined, '#e2e8f0')}
                  {card('Gross Profit', fmtBig(f.gross_profit))}
                  {card('Net Income', fmtBig(f.net_income), undefined, f.net_income != null ? (f.net_income >= 0 ? '#4ade80' : '#f87171') : undefined)}
                  {card('EBITDA', fmtBig(f.ebitda))}
                  {card('Free Cash Flow', fmtBig(f.free_cashflow), undefined, f.free_cashflow != null ? (f.free_cashflow >= 0 ? '#4ade80' : '#f87171') : undefined)}
                  {card('Operating CF', fmtBig(f.operating_cashflow))}
                </div>
              </div>

              {/* Row 3 — Balance sheet + margins + per share */}
              <div className="stock-detail-fundamentals-row3-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px' }}>
                {/* Balance sheet */}
                <div>
                  <div style={{ fontSize: '10px', fontWeight: 700, color: '#7c3aed', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Balance Sheet</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {card('Total Cash', fmtBig(f.total_cash))}
                    {card('Total Debt', fmtBig(f.total_debt), undefined, f.total_debt != null && f.total_cash != null ? (f.total_cash > f.total_debt ? '#4ade80' : '#f87171') : undefined)}
                  </div>
                </div>
                {/* Margins */}
                <div>
                  <div style={{ fontSize: '10px', fontWeight: 700, color: '#059669', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Margins</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {card('Gross Margin', fmtPct(f.gross_margin))}
                    {card('Operating Margin', fmtPct(f.operating_margin))}
                    {card('Profit Margin', fmtPct(f.profit_margin))}
                  </div>
                </div>
                {/* Returns + growth */}
                <div>
                  <div style={{ fontSize: '10px', fontWeight: 700, color: '#b45309', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Returns &amp; Growth</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {card('ROE', fmtPct(f.return_on_equity))}
                    {card('ROA', fmtPct(f.return_on_assets))}
                    {card('Earnings Growth', fmtPct(f.earnings_growth), 'YoY', growthColor(f.earnings_growth))}
                  </div>
                </div>
              </div>

              {/* Row 4 — Per share & risk + 52-week range */}
              <div className="stock-detail-fundamentals-row4-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div>
                  <div style={{ fontSize: '10px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Per Share &amp; Risk</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                    {card('EPS (TTM)', fmtNum(f.trailing_eps))}
                    {card('Fwd EPS', fmtNum(f.forward_eps))}
                    {card('Book Value', fmtNum(f.book_value))}
                    {card('Dividend Yield', f.dividend_yield != null ? fmtPct(f.dividend_yield) : '—', f.dividend_rate != null ? `$${f.dividend_rate.toFixed(2)}/yr` : undefined)}
                    {card('Beta', fmtNum(f.beta), 'vs market')}
                    {card('Shares Out', fmtBig(f.shares_outstanding))}
                    {card('Volume (Today)', fmtBig(liveQuote?.volume), undefined, '#94a3b8')}
                    {card('Avg Vol (3M)', fmtBig(f.average_volume), liveQuote?.volume != null && f.average_volume != null && f.average_volume > 0 ? `${(liveQuote.volume / f.average_volume).toFixed(2)}× avg` : undefined, liveQuote?.volume != null && f.average_volume != null && f.average_volume > 0 ? (liveQuote.volume > f.average_volume * 1.5 ? '#4ade80' : liveQuote.volume < f.average_volume * 0.5 ? '#f87171' : '#94a3b8') : '#94a3b8')}
                  </div>
                </div>
                {hi != null && lo != null && (
                  <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                    <div style={{ fontSize: '10px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>52-Week Range</div>
                    <div style={{ background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid #1e293b', padding: '14px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '8px' }}>
                        <span style={{ color: '#f87171' }}>${lo.toFixed(2)}</span>
                        <span style={{ color: '#64748b', fontSize: '11px' }}>52-Week Low → High</span>
                        <span style={{ color: '#4ade80' }}>${hi.toFixed(2)}</span>
                      </div>
                      <div style={{ height: '6px', background: '#1e293b', borderRadius: '3px', overflow: 'hidden', position: 'relative' }}>
                        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${rangePct ?? 50}%`, background: 'linear-gradient(90deg,#f87171,#facc15,#4ade80)', borderRadius: '3px' }} />
                      </div>
                      {curPrice && <div style={{ fontSize: '11px', color: '#64748b', marginTop: '6px', textAlign: 'center' }}>Current ${curPrice.toFixed(2)} · {rangePct != null ? `${rangePct.toFixed(0)}% of range` : ''}</div>}
                    </div>
                  </div>
                )}
              </div>

              {/* Row 5 — Short Interest + Ownership + Squeeze Score */}
              {(f.short_percent_of_float != null || f.held_percent_institutions != null) && (() => {
                // Squeeze score: 0–100 from short float %, days-to-cover, and options flow
                const floatPct = (f.short_percent_of_float ?? 0) * 100;
                const ratio    = f.short_ratio ?? 0;
                const floatPts = floatPct >= 20 ? 40 : floatPct >= 15 ? 25 : floatPct >= 10 ? 15 : 0;
                const ratioPts = ratio >= 8 ? 30 : ratio >= 5 ? 20 : ratio >= 3 ? 10 : 0;
                const optPts   = optionsFlow?.available
                  ? ((optionsFlow.whale_count ?? 0) > 0 && (optionsFlow.cp_ratio ?? 0) >= 1.5 ? 30
                    : (optionsFlow.cp_ratio ?? 0) >= 1.5 ? 20
                    : (optionsFlow.cp_ratio ?? 0) >= 1.2 ? 10 : 0)
                  : 0;
                const squeezeScore = floatPts + ratioPts + optPts;
                const hasShortData = floatPct > 0 || ratio > 0;
                return (
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: '6px' }}>
                      <div style={{ fontSize: '10px', fontWeight: 700, color: '#ef4444', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Short Interest &amp; Ownership</div>
                      {hasShortData && squeezeScore > 0 && (() => {
                        const sc = squeezeScore;
                        const [bg, border, text, label] = sc >= 70
                          ? ['rgba(251,146,60,0.12)', 'rgba(251,146,60,0.4)', '#fb923c', '🔥 HIGH SQUEEZE']
                          : sc >= 40
                          ? ['rgba(251,191,36,0.1)', 'rgba(251,191,36,0.35)', '#fbbf24', '⚡ MODERATE']
                          : ['rgba(148,163,184,0.08)', 'rgba(148,163,184,0.2)', '#94a3b8', 'LOW'];
                        return (
                          <span style={{ fontSize: 11, fontWeight: 700, color: text, background: bg, border: `1px solid ${border}`, borderRadius: 5, padding: '2px 8px' }}>
                            {label} · {sc}/100
                          </span>
                        );
                      })()}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: '8px' }}>
                      {f.short_percent_of_float != null && (() => {
                        const pct = f.short_percent_of_float! * 100;
                        const color = pct >= 20 ? '#f87171' : pct >= 10 ? '#fbbf24' : '#4ade80';
                        const label = pct >= 20 ? 'High — squeeze risk' : pct >= 10 ? 'Elevated' : 'Low';
                        return card('Short % of Float', `${pct.toFixed(1)}%`, label, color);
                      })()}
                      {f.shares_short != null && f.shares_short_prior_month != null && (() => {
                        const rising = f.shares_short > f.shares_short_prior_month!;
                        const pctChg = ((f.shares_short - f.shares_short_prior_month!) / f.shares_short_prior_month!) * 100;
                        const color = rising ? '#f87171' : '#4ade80';
                        return card('Short Trend', rising ? `↑ ${pctChg.toFixed(0)}% MoM` : `↓ ${Math.abs(pctChg).toFixed(0)}% MoM`, rising ? 'Shorts rising (bearish)' : 'Shorts falling (bullish)', color);
                      })()}
                      {f.short_ratio != null && (() => {
                        const color = f.short_ratio >= 5 ? '#f87171' : f.short_ratio >= 3 ? '#fbbf24' : '#94a3b8';
                        return card('Days to Cover', `${f.short_ratio.toFixed(1)}d`, 'short ratio', color);
                      })()}
                      {f.held_percent_institutions != null && card('Institutional', `${(f.held_percent_institutions * 100).toFixed(1)}%`, 'of float held')}
                      {f.held_percent_insiders != null && card('Insider Hold', `${(f.held_percent_insiders * 100).toFixed(1)}%`, 'of float held')}
                    </div>
                  </div>
                );
              })()}

              {/* Row 6 — Quarterly Revenue & Earnings Trend (T230) */}
              {quarterly && quarterly.length > 0 && (() => {
                function fmtQ(n: number | null): string {
                  if (n == null) return '—';
                  const abs = Math.abs(n);
                  const sign = n < 0 ? '-' : '';
                  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(1)}T`;
                  if (abs >= 1e9)  return `${sign}$${(abs / 1e9).toFixed(1)}B`;
                  if (abs >= 1e6)  return `${sign}$${(abs / 1e6).toFixed(0)}M`;
                  if (abs >= 1e3)  return `${sign}$${(abs / 1e3).toFixed(0)}K`;
                  return `${sign}$${abs.toFixed(0)}`;
                }
                const qRows: { label: string; key: keyof QuarterlyRow; color?: (v: number | null) => string }[] = [
                  { label: 'Revenue',      key: 'revenue' },
                  { label: 'Gross Profit', key: 'gross_profit' },
                  { label: 'Net Income',   key: 'net_income',   color: (v) => v == null ? '#94a3b8' : v >= 0 ? '#4ade80' : '#f87171' },
                  { label: 'EBITDA',       key: 'ebitda' },
                ];
                return (
                  <div>
                    <div style={{ fontSize: '10px', fontWeight: 700, color: '#0891b2', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
                      Quarterly Trend (last {quarterly.length}Q)
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11px' }}>
                        <thead>
                          <tr>
                            <th style={{ textAlign: 'left', color: '#475569', fontWeight: 600, padding: '4px 8px 4px 0', whiteSpace: 'nowrap', width: '90px' }}></th>
                            {quarterly.map(q => (
                              <th key={q.date} style={{ textAlign: 'right', color: '#475569', fontWeight: 600, padding: '4px 6px', whiteSpace: 'nowrap', fontSize: '10px' }}>
                                {q.date.slice(0, 7)}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {qRows.map(row => (
                            <tr key={row.label} style={{ borderTop: '1px solid #1e293b' }}>
                              <td style={{ color: '#94a3b8', padding: '5px 8px 5px 0', whiteSpace: 'nowrap', fontWeight: 600 }}>{row.label}</td>
                              {quarterly.map(q => {
                                const v = q[row.key] as number | null;
                                const cellColor = row.color ? row.color(v) : '#e2e8f0';
                                return (
                                  <td key={q.date} style={{ textAlign: 'right', padding: '5px 6px', fontWeight: 600, color: cellColor, whiteSpace: 'nowrap' }}>
                                    {fmtQ(v)}
                                  </td>
                                );
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                );
              })()}

              {/* Row 7 — EPS Surprise History */}
              {f.eps_history && f.eps_history.length > 0 && (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
                    <div style={{ fontSize: '10px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em' }}>EPS Surprise History</div>
                    {f.eps_beat_rate != null && (
                      <span style={{ fontSize: '11px', fontWeight: 700, color: f.eps_beat_rate >= 0.75 ? '#4ade80' : f.eps_beat_rate >= 0.5 ? '#fbbf24' : '#f87171', background: 'rgba(255,255,255,0.04)', border: `1px solid ${f.eps_beat_rate >= 0.75 ? '#4ade8044' : f.eps_beat_rate >= 0.5 ? '#fbbf2444' : '#f8717144'}`, borderRadius: '6px', padding: '2px 8px' }}>
                        {Math.round(f.eps_beat_rate * 100)}% beat rate
                      </span>
                    )}
                    {f.eps_avg_surprise_pct != null && (
                      <span style={{ fontSize: '11px', color: f.eps_avg_surprise_pct >= 0 ? '#4ade80' : '#f87171' }}>avg {f.eps_avg_surprise_pct >= 0 ? '+' : ''}{f.eps_avg_surprise_pct.toFixed(1)}% surprise</span>
                    )}
                    {f.eps_surprise_trend && (
                      <span style={{ fontSize: '11px', color: f.eps_surprise_trend === 'improving' ? '#4ade80' : f.eps_surprise_trend === 'declining' ? '#f87171' : '#94a3b8', marginLeft: 'auto' }}>
                        {f.eps_surprise_trend === 'improving' ? '↑ Improving' : f.eps_surprise_trend === 'declining' ? '↓ Declining' : '→ Stable'}
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: `repeat(${f.eps_history.length}, 1fr)`, gap: '4px' }}>
                    {f.eps_history.map((q: { quarter: string; actual: number | null; estimate: number | null; surprise_pct: number | null }) => {
                      const beat = q.actual != null && q.estimate != null && q.actual > q.estimate;
                      const miss = q.actual != null && q.estimate != null && q.actual < q.estimate;
                      const qColor = beat ? '#4ade80' : miss ? '#f87171' : '#94a3b8';
                      return (
                        <div key={q.quarter} style={{ background: 'rgba(255,255,255,0.02)', border: `1px solid ${qColor}33`, borderRadius: '6px', padding: '6px 4px', textAlign: 'center' as const }}>
                          <div style={{ fontSize: '9px', color: '#475569', marginBottom: '2px' }}>{q.quarter.slice(0, 7)}</div>
                          <div style={{ fontSize: '11px', fontWeight: 700, color: qColor }}>{q.actual != null ? `$${q.actual.toFixed(2)}` : '—'}</div>
                          <div style={{ fontSize: '9px', color: '#475569' }}>est ${q.estimate != null ? q.estimate.toFixed(2) : '—'}</div>
                          {q.surprise_pct != null && (
                            <div style={{ fontSize: '9px', color: qColor, marginTop: '2px' }}>{q.surprise_pct >= 0 ? '+' : ''}{q.surprise_pct.toFixed(1)}%</div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Row 6 — Analyst Ratings & Price Targets */}
              {(() => {
                const hasRatings = f.recommendation != null || f.target_price != null;
                const hasCounts = (f.analyst_strong_buy ?? 0) + (f.analyst_buy ?? 0) + (f.analyst_hold ?? 0) + (f.analyst_underperform ?? 0) + (f.analyst_sell ?? 0) > 0;
                const totalAnalysts = hasCounts
                  ? (f.analyst_strong_buy ?? 0) + (f.analyst_buy ?? 0) + (f.analyst_hold ?? 0) + (f.analyst_underperform ?? 0) + (f.analyst_sell ?? 0)
                  : (f.number_of_analysts ?? 0);
                if (!hasRatings) return null;

                // Price target range
                const tLow  = f.target_low;
                const tMed  = f.target_median;
                const tMean = f.target_price;
                const tHigh = f.target_high;
                const hasTargets = tLow != null && tHigh != null && tHigh > tLow;
                const rangeMin = hasTargets ? tLow! * 0.98 : null;
                const rangeMax = hasTargets ? tHigh! * 1.02 : null;
                const toBarPct = (p: number) =>
                  rangeMin != null && rangeMax != null
                    ? Math.max(0, Math.min(100, ((p - rangeMin) / (rangeMax - rangeMin)) * 100))
                    : null;

                // Upside from current price to mean target
                const upside = tMean != null && curPrice != null ? ((tMean - curPrice) / curPrice) * 100 : null;

                // Nearest support/resistance from srLevels
                const supports = srLevels.filter(l => l.kind === 'support' && curPrice != null && l.price < curPrice).sort((a, b) => b.price - a.price);
                const resistances = srLevels.filter(l => l.kind === 'resistance' && curPrice != null && l.price > curPrice).sort((a, b) => a.price - b.price);
                const nearestSupport = supports[0]?.price ?? null;
                const nearestResistance = resistances[0]?.price ?? null;

                // Rating bar segments
                const ratingSegs = [
                  { key: 'Strong Buy',  count: f.analyst_strong_buy  ?? 0, color: '#22c55e' },
                  { key: 'Buy',         count: f.analyst_buy         ?? 0, color: '#4ade80' },
                  { key: 'Hold',        count: f.analyst_hold        ?? 0, color: '#facc15' },
                  { key: 'Underperform',count: f.analyst_underperform ?? 0, color: '#fb923c' },
                  { key: 'Sell',        count: f.analyst_sell        ?? 0, color: '#ef4444' },
                ];

                // Recommendation mean → label + star score
                const recMean = f.recommendation_mean;
                const starScore = recMean != null ? Math.max(0, Math.min(5, 5 - recMean + 1)) : null;

                // Buy zone: from analyst low (or support) up to current price
                const buyLower = tLow ?? nearestSupport;
                const buyUpper = curPrice;

                // Sell zone: from mean target to high target (+ fair value if available)
                const sellLower = tMean;
                const sellUpper = tHigh;
                const fairPrice = ranking?.fair_price ?? null;

                return (
                  <div style={{ borderRadius: '12px', border: '1px solid rgba(99,102,241,0.2)', background: 'rgba(15,23,42,0.9)', overflow: 'hidden' }}>
                    {/* Section header */}
                    <div style={{ padding: '12px 16px', borderBottom: '1px solid #1e293b', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '6px' }}>
                      <div>
                        <div style={{ fontSize: '12px', fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                          Analyst Ratings &amp; Price Targets
                        </div>
                        <div style={{ fontSize: '10px', color: '#334155', marginTop: '2px' }}>
                          Via Yahoo Finance · consensus of Wall Street analysts · updated daily · not a personalised recommendation
                        </div>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {totalAnalysts > 0 && (
                          <span style={{ fontSize: '11px', color: '#475569' }}>{totalAnalysts} analysts</span>
                        )}
                        <button
                          onClick={handleFundRefresh}
                          disabled={fundRefreshing}
                          title="Force-refresh analyst data (bypasses 24h cache)"
                          style={{
                            display: 'flex', alignItems: 'center', gap: '4px',
                            padding: '3px 8px', borderRadius: '5px', fontSize: '11px',
                            border: '1px solid rgba(148,163,184,0.15)',
                            background: 'rgba(255,255,255,0.03)',
                            color: fundRefreshing ? '#818cf8' : '#475569',
                            cursor: fundRefreshing ? 'not-allowed' : 'pointer',
                          }}
                        >
                          <span style={{ display: 'inline-block', animation: fundRefreshing ? 'spin 0.8s linear infinite' : 'none' }}>↻</span>
                          {fundRefreshing ? 'Refreshing…' : 'Refresh'}
                        </button>
                      </div>
                    </div>

                    <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '14px' }}>

                      {/* Top row: rating distribution + consensus */}
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '16px', alignItems: 'start' }}>

                        {/* Rating bar + breakdown */}
                        <div>
                          {hasCounts && (
                            <>
                              {/* Stacked bar */}
                              <div style={{ display: 'flex', height: '10px', borderRadius: '5px', overflow: 'hidden', gap: '1px', marginBottom: '8px' }}>
                                {ratingSegs.map(seg => seg.count > 0 && (
                                  <div key={seg.key} title={`${seg.key}: ${seg.count}`}
                                    style={{ flex: seg.count, background: seg.color, minWidth: '4px' }} />
                                ))}
                              </div>
                              {/* Count labels */}
                              <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                                {ratingSegs.map(seg => (
                                  <div key={seg.key} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                    <span style={{ width: '8px', height: '8px', borderRadius: '2px', background: seg.color, display: 'inline-block', flexShrink: 0 }} />
                                    <span style={{ fontSize: '11px', color: '#64748b' }}>{seg.key}</span>
                                    <span style={{ fontSize: '12px', fontWeight: 700, color: seg.count > 0 ? seg.color : '#1e293b' }}>{seg.count}</span>
                                  </div>
                                ))}
                              </div>
                            </>
                          )}
                        </div>

                        {/* Consensus badge */}
                        {f.recommendation && (
                          <div style={{ textAlign: 'center', padding: '10px 18px', borderRadius: '10px', background: `${recColors[f.recommendation] ?? '#64748b'}12`, border: `1px solid ${recColors[f.recommendation] ?? '#64748b'}35` }}>
                            <div style={{ fontSize: '18px', fontWeight: 800, color: recColors[f.recommendation] ?? '#94a3b8', whiteSpace: 'nowrap' }}>
                              {recLabel[f.recommendation] ?? f.recommendation.toUpperCase()}
                            </div>
                            {starScore != null && (
                              <div style={{ fontSize: '12px', marginTop: '4px' }}>
                                {[1,2,3,4,5].map(i => (
                                  <span key={i} style={{ color: i <= Math.round(starScore) ? '#facc15' : '#1e293b', fontSize: '14px' }}>★</span>
                                ))}
                                <div style={{ fontSize: '10px', color: '#475569', marginTop: '2px' }}>
                                  Mean score: {recMean?.toFixed(2)}
                                </div>
                                <div style={{ fontSize: '9px', color: '#334155', marginTop: '1px' }}>
                                  1.0 = Strong Buy · 5.0 = Sell
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </div>

                      {/* Price target range visualization */}
                      {hasTargets && (
                        <div>
                          <div style={{ fontSize: '10px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '10px' }}>
                            Price Target Range
                            {upside != null && (
                              <span style={{ marginLeft: '10px', color: upside >= 0 ? '#4ade80' : '#f87171', fontWeight: 700, textTransform: 'none', letterSpacing: 0 }}>
                                {upside >= 0 ? '+' : ''}{upside.toFixed(1)}% to mean target
                              </span>
                            )}
                          </div>

                          {/* Range bar with price markers */}
                          <div style={{ position: 'relative', height: '40px', marginBottom: '4px' }}>
                            {/* Background bar */}
                            <div style={{ position: 'absolute', top: '18px', left: 0, right: 0, height: '4px', background: '#1e293b', borderRadius: '2px' }} />
                            {/* Filled bar: low → high */}
                            {toBarPct(tLow!) != null && toBarPct(tHigh!) != null && (
                              <div style={{
                                position: 'absolute', top: '18px', height: '4px', borderRadius: '2px',
                                left: `${toBarPct(tLow!)}%`,
                                width: `${toBarPct(tHigh!)! - toBarPct(tLow!)!}%`,
                                background: 'linear-gradient(90deg,#ef4444,#facc15,#22c55e)',
                              }} />
                            )}
                            {/* Markers */}
                            {[
                              { price: tLow,  label: `Low\n$${tLow!.toFixed(2)}`,   color: '#ef4444', size: 8 },
                              { price: tMed,  label: `Med\n$${tMed?.toFixed(2)}`,   color: '#facc15', size: 8 },
                              { price: tMean, label: `Mean\n$${tMean?.toFixed(2)}`, color: '#818cf8', size: 10 },
                              { price: tHigh, label: `High\n$${tHigh!.toFixed(2)}`, color: '#22c55e', size: 8 },
                              { price: curPrice, label: `Now\n$${curPrice?.toFixed(2)}`, color: '#f1f5f9', size: 12 },
                            ].filter(m => m.price != null).map(m => {
                              const pct = toBarPct(m.price!);
                              if (pct == null) return null;
                              const lines = m.label.split('\n');
                              return (
                                <div key={m.label} style={{ position: 'absolute', left: `${pct}%`, top: 0, transform: 'translateX(-50%)', textAlign: 'center', width: '48px', marginLeft: '-24px' }}>
                                  <div style={{ fontSize: '9px', color: m.color, fontWeight: 700, lineHeight: 1.2, whiteSpace: 'nowrap', marginBottom: '2px' }}>
                                    {lines[0]}
                                  </div>
                                  <div style={{
                                    width: `${m.size}px`, height: `${m.size}px`,
                                    borderRadius: '50%', background: m.color,
                                    margin: '0 auto',
                                    border: m.price === curPrice ? '2px solid #fff' : 'none',
                                    boxShadow: m.price === curPrice ? `0 0 6px ${m.color}` : 'none',
                                  }} />
                                  <div style={{ fontSize: '9px', color: m.color, fontWeight: 600, marginTop: '2px', whiteSpace: 'nowrap' }}>
                                    {lines[1]}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Buy zone + Sell / Target zone */}
                      <div className="stock-detail-buyzone-sellzone-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                        {/* BUY ZONE */}
                        <div style={{ borderRadius: '10px', padding: '12px 14px', background: 'rgba(34,197,94,0.06)', border: '1px solid rgba(34,197,94,0.2)' }}>
                          <div style={{ fontSize: '10px', fontWeight: 800, color: '#22c55e', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
                            ↓ Buy Zone
                          </div>
                          {buyLower != null && buyUpper != null ? (
                            <div style={{ fontSize: '20px', fontWeight: 800, color: '#4ade80', marginBottom: '6px' }}>
                              ${buyLower.toFixed(2)} – ${buyUpper.toFixed(2)}
                            </div>
                          ) : (
                            <div style={{ fontSize: '13px', color: '#475569', marginBottom: '6px' }}>See support levels</div>
                          )}
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                            {tLow != null && <div style={{ fontSize: '11px', color: '#64748b' }}>Analyst low target: <span style={{ color: '#4ade80' }}>${tLow.toFixed(2)}</span></div>}
                            {nearestSupport != null && <div style={{ fontSize: '11px', color: '#64748b' }}>Nearest support: <span style={{ color: '#4ade80' }}>${nearestSupport.toFixed(2)}</span></div>}
                            {curPrice != null && tMean != null && curPrice > tMean && (
                              <div style={{ fontSize: '11px', color: '#fb923c', marginTop: '4px' }}>⚠ Above analyst consensus — consider waiting for pullback</div>
                            )}
                            {curPrice != null && tMean != null && curPrice <= tMean && upside != null && (
                              <div style={{ fontSize: '11px', color: '#4ade80', marginTop: '4px' }}>+{upside.toFixed(1)}% upside to mean target</div>
                            )}
                          </div>
                        </div>

                        {/* SELL / TARGET ZONE */}
                        <div style={{ borderRadius: '10px', padding: '12px 14px', background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)' }}>
                          <div style={{ fontSize: '10px', fontWeight: 800, color: '#ef4444', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
                            ↑ Sell / Target Zone
                          </div>
                          {sellLower != null ? (
                            <div style={{ fontSize: '20px', fontWeight: 800, color: '#f87171', marginBottom: '6px' }}>
                              ${sellLower.toFixed(2)}{sellUpper != null ? ` – $${sellUpper.toFixed(2)}` : ''}
                            </div>
                          ) : (
                            <div style={{ fontSize: '13px', color: '#475569', marginBottom: '6px' }}>See resistance levels</div>
                          )}
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                            {tMean != null && <div style={{ fontSize: '11px', color: '#64748b' }}>Analyst mean target: <span style={{ color: '#f87171' }}>${tMean.toFixed(2)}</span></div>}
                            {tHigh != null && <div style={{ fontSize: '11px', color: '#64748b' }}>Bull case (high): <span style={{ color: '#f87171' }}>${tHigh.toFixed(2)}</span></div>}
                            {fairPrice != null && <div style={{ fontSize: '11px', color: '#64748b' }}>K-Score fair value: <span style={{ color: '#818cf8' }}>${fairPrice.toFixed(2)}</span></div>}
                            {nearestResistance != null && <div style={{ fontSize: '11px', color: '#64748b' }}>Nearest resistance: <span style={{ color: '#fb923c' }}>${nearestResistance.toFixed(2)}</span></div>}
                          </div>
                        </div>
                      </div>

                      {/* Recent analyst actions from individual firms */}
                      {f.analyst_actions && f.analyst_actions.length > 0 && (() => {
                        const ACTION_COLOR: Record<string, string> = {
                          Upgraded:   '#22c55e',
                          Downgraded: '#ef4444',
                          'Initiated Coverage On': '#818cf8',
                          Initiated:  '#818cf8',
                          Maintained: '#94a3b8',
                          Reiterated: '#94a3b8',
                          'Lowered Target': '#fb923c',
                          'Raised Target':  '#4ade80',
                        };
                        const UP_ACTIONS = ['up', 'upgrade', 'init', 'initiated'];
                        const DOWN_ACTIONS = ['down', 'downgrade'];
                        const sevenDaysAgo = new Date(Date.now() - 7 * 86400_000).toISOString().slice(0, 10);
                        const recent7d = f.analyst_actions.filter(a => a.date >= sevenDaysAgo);
                        const ups7d = recent7d.filter(a => UP_ACTIONS.includes(a.action.toLowerCase())).length;
                        const downs7d = recent7d.filter(a => DOWN_ACTIONS.includes(a.action.toLowerCase())).length;
                        const actionColor = (a: string) => {
                          for (const [k, v] of Object.entries(ACTION_COLOR)) {
                            if (a.toLowerCase().includes(k.toLowerCase())) return v;
                          }
                          return '#64748b';
                        };
                        const gradeColor = (g: string) => {
                          const l = g.toLowerCase();
                          if (l.includes('strong buy') || l.includes('overweight') || l.includes('outperform') || l.includes('buy')) return '#22c55e';
                          if (l.includes('sell') || l.includes('underweight') || l.includes('underperform')) return '#ef4444';
                          if (l.includes('hold') || l.includes('neutral') || l.includes('equal')) return '#facc15';
                          return '#94a3b8';
                        };
                        return (
                          <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: '8px' }}>
                              <div style={{ fontSize: '10px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                                Recent Analyst Actions <span style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0, color: '#334155' }}>· last 90 days</span>
                              </div>
                              {(ups7d > 0 || downs7d > 0) && (
                                <div style={{ display: 'flex', gap: 4, marginLeft: 'auto' }}>
                                  {ups7d > 0 && <span style={{ fontSize: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', borderRadius: 4, padding: '1px 6px', fontWeight: 700 }}>+{ups7d} 7d</span>}
                                  {downs7d > 0 && <span style={{ fontSize: 10, background: 'rgba(239,68,68,0.12)', color: '#ef4444', borderRadius: 4, padding: '1px 6px', fontWeight: 700 }}>−{downs7d} 7d</span>}
                                </div>
                              )}
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                              {f.analyst_actions.map((a, i) => (
                                <div key={i} style={{ display: 'grid', gridTemplateColumns: '70px 1fr auto', gap: '8px', alignItems: 'center', padding: '5px 8px', borderRadius: '6px', background: 'rgba(255,255,255,0.02)', borderBottom: i < f.analyst_actions.length - 1 ? '1px solid rgba(30,41,59,0.5)' : 'none' }}>
                                  <span style={{ fontSize: '10px', color: '#475569', fontVariantNumeric: 'tabular-nums' }}>{a.date.slice(5)}</span>
                                  <div style={{ minWidth: 0 }}>
                                    <span style={{ fontSize: '12px', fontWeight: 600, color: '#cbd5e1', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.firm || '—'}</span>
                                    <span style={{ fontSize: '11px', color: actionColor(a.action) }}>{a.action}</span>
                                  </div>
                                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                                    {a.from_grade && a.to_grade && a.from_grade !== a.to_grade ? (
                                      <span style={{ fontSize: '11px' }}>
                                        <span style={{ color: gradeColor(a.from_grade) }}>{a.from_grade}</span>
                                        <span style={{ color: '#475569', margin: '0 4px' }}>→</span>
                                        <span style={{ color: gradeColor(a.to_grade), fontWeight: 700 }}>{a.to_grade}</span>
                                      </span>
                                    ) : a.to_grade ? (
                                      <span style={{ fontSize: '11px', fontWeight: 700, color: gradeColor(a.to_grade) }}>{a.to_grade}</span>
                                    ) : null}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        );
                      })()}

                    </div>
                  </div>
                );
              })()}

              {/* Row 6 — Insider Activity */}
              {(() => {
                const f = data.fundamentals!;
                const hasBuys = f.insider_buy_shares_6m != null || f.insider_sell_shares_6m != null;
                if (!hasBuys) return null;
                const buys = f.insider_buy_shares_6m ?? 0;
                const sells = f.insider_sell_shares_6m ?? 0;
                const net = buys - sells;
                const total = buys + sells;
                const buyPct = total > 0 ? (buys / total) * 100 : 0;
                const netColor = net >= 0 ? '#22c55e' : '#ef4444';
                const netLabel = net >= 0 ? 'Net Buyers' : 'Net Sellers';
                return (
                  <div style={{ borderRadius: '10px', border: '1px solid rgba(148,163,184,0.12)', background: 'rgba(255,255,255,0.02)', padding: '14px 16px' }}>
                    <div style={{ fontSize: '10px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '10px' }}>
                      Insider Activity (Last 6 Months)
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
                      <div style={{ flex: 1, minWidth: '160px' }}>
                        {total > 0 && (
                          <div style={{ display: 'flex', height: '8px', borderRadius: '4px', overflow: 'hidden', marginBottom: '8px' }}>
                            <div style={{ flex: buys, background: '#22c55e', minWidth: buys > 0 ? '4px' : 0 }} title={`Buys: ${buys.toLocaleString()} shares`} />
                            <div style={{ flex: sells, background: '#ef4444', minWidth: sells > 0 ? '4px' : 0 }} title={`Sales: ${sells.toLocaleString()} shares`} />
                          </div>
                        )}
                        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <span style={{ width: '8px', height: '8px', borderRadius: '2px', background: '#22c55e', display: 'inline-block' }} />
                            <span style={{ fontSize: '11px', color: '#64748b' }}>Buys</span>
                            <span style={{ fontSize: '12px', fontWeight: 700, color: '#4ade80' }}>{buys.toLocaleString()}</span>
                            {f.insider_buy_transactions_6m != null && (
                              <span style={{ fontSize: '10px', color: '#334155' }}>({f.insider_buy_transactions_6m} txn)</span>
                            )}
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <span style={{ width: '8px', height: '8px', borderRadius: '2px', background: '#ef4444', display: 'inline-block' }} />
                            <span style={{ fontSize: '11px', color: '#64748b' }}>Sales</span>
                            <span style={{ fontSize: '12px', fontWeight: 700, color: '#f87171' }}>{sells.toLocaleString()}</span>
                          </div>
                        </div>
                      </div>
                      <div style={{ textAlign: 'center', padding: '8px 14px', borderRadius: '8px', background: `${netColor}12`, border: `1px solid ${netColor}30` }}>
                        <div style={{ fontSize: '11px', color: '#475569', marginBottom: '2px' }}>{netLabel}</div>
                        <div style={{ fontSize: '18px', fontWeight: 800, color: netColor }}>
                          {net >= 0 ? '+' : ''}{net.toLocaleString()}
                        </div>
                        {f.insider_net_pct != null && (
                          <div style={{ fontSize: '10px', color: '#475569', marginTop: '2px' }}>
                            {f.insider_net_pct >= 0 ? '+' : ''}{(f.insider_net_pct * 100).toFixed(2)}% of float
                          </div>
                        )}
                        {total > 0 && (
                          <div style={{ fontSize: '10px', color: '#334155', marginTop: '2px' }}>
                            {buyPct.toFixed(0)}% buy ratio
                          </div>
                        )}
                      </div>
                    </div>
                    <div style={{ fontSize: '10px', color: '#334155', marginTop: '8px' }}>
                      Source: SEC filings via Yahoo Finance · open-market transactions only
                    </div>
                  </div>
                );
              })()}

            </div>
          </div>
        );
      })()}

      {/* AI Chat Panel */}
      <div style={{ borderRadius: '12px', border: '1px solid rgba(167,139,250,0.25)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#a78bfa,#c4b5fd,#a78bfa)' }} />
        <button
          onClick={() => setAiOpen(o => !o)}
          style={{
            width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '14px 20px', background: 'transparent', border: 'none', cursor: 'pointer',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '15px' }}>🤖</span>
            <span style={{ fontSize: '14px', fontWeight: 700, color: '#c4b5fd' }}>
              Ask AI about {symbol}
            </span>
            {isAiConfigured() && (
              <span style={{
                fontSize: '10px', padding: '1px 7px', borderRadius: '999px',
                background: 'rgba(167,139,250,0.15)', color: '#a78bfa', fontWeight: 700,
              }}>
                {getAiProviderLabel()}
              </span>
            )}
            {!isAiConfigured() && (
              <span style={{ fontSize: '11px', color: '#475569' }}>— configure in Settings</span>
            )}
          </div>
          <span style={{ color: '#475569', fontSize: '12px', transform: aiOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>▼</span>
        </button>

        {aiOpen && (
          <div style={{ borderTop: '1px solid #1e293b' }}>
            {!isAiConfigured() ? (
              <div style={{ padding: '20px', textAlign: 'center', fontSize: '13px', color: '#475569' }}>
                No AI provider configured.{' '}
                <a href="/settings" style={{ color: '#a78bfa', textDecoration: 'none' }}>Go to Settings → AI Assistant</a>
                {' '}to set up Claude or DeepSeek.
              </div>
            ) : (
              <>
                {/* Suggested questions */}
                {aiMessages.length === 0 && (
                  <div style={{ padding: '12px 16px', display: 'flex', gap: '6px', flexWrap: 'wrap', borderBottom: '1px solid #1e293b' }}>
                    {[
                      `Should I buy ${symbol} now?`,
                      `What are the key risks?`,
                      `Summarise the latest news`,
                      `What does the K-Score mean?`,
                    ].map(q => (
                      <button
                        key={q}
                        onClick={() => { setAiInput(q); }}
                        style={{
                          fontSize: '11px', padding: '4px 10px', borderRadius: '6px',
                          background: 'rgba(167,139,250,0.1)', border: '1px solid rgba(167,139,250,0.2)',
                          color: '#a78bfa', cursor: 'pointer',
                        }}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                )}

                {/* Message history */}
                {aiMessages.length > 0 && (
                  <div style={{ maxHeight: '360px', overflowY: 'auto', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    {aiMessages.map((m, i) => (
                      <div key={i} style={{
                        display: 'flex', flexDirection: m.role === 'user' ? 'row-reverse' : 'row', gap: '8px', alignItems: 'flex-start',
                      }}>
                        <div style={{
                          maxWidth: '80%', padding: '10px 14px', borderRadius: '10px', fontSize: '13px', lineHeight: 1.6,
                          background: m.role === 'user' ? 'rgba(167,139,250,0.15)' : 'rgba(255,255,255,0.04)',
                          border: m.role === 'user' ? '1px solid rgba(167,139,250,0.3)' : '1px solid #1e293b',
                          color: m.role === 'user' ? '#c4b5fd' : '#cbd5e1',
                          whiteSpace: 'pre-wrap',
                        }}>
                          {m.content}
                        </div>
                      </div>
                    ))}
                    {aiLoading && (
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
                        <div style={{ padding: '10px 14px', borderRadius: '10px', background: 'rgba(255,255,255,0.04)', border: '1px solid #1e293b', color: '#475569', fontSize: '13px' }}>
                          ⟳ Thinking…
                        </div>
                      </div>
                    )}
                    {aiError && (
                      <div style={{ padding: '8px 12px', borderRadius: '8px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)', color: '#f87171', fontSize: '12px' }}>
                        {aiError}
                      </div>
                    )}
                    <div ref={aiBottomRef} />
                  </div>
                )}

                {/* Input */}
                <div style={{ padding: '12px 16px', borderTop: '1px solid #1e293b', display: 'flex', gap: '8px' }}>
                  <input
                    value={aiInput}
                    onChange={e => setAiInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAiMessage(); } }}
                    placeholder={`Ask anything about ${symbol}…`}
                    style={{
                      flex: 1, background: '#0f172a', border: '1px solid #1e293b',
                      borderRadius: '8px', padding: '9px 12px', fontSize: '13px',
                      color: '#e2e8f0', outline: 'none',
                    }}
                  />
                  <button
                    onClick={sendAiMessage}
                    disabled={aiLoading || !aiInput.trim()}
                    style={{
                      padding: '9px 18px', borderRadius: '8px', fontSize: '13px', fontWeight: 700,
                      cursor: aiLoading || !aiInput.trim() ? 'not-allowed' : 'pointer',
                      background: aiLoading || !aiInput.trim() ? '#1e293b' : 'linear-gradient(135deg,#7c3aed,#a78bfa)',
                      border: 'none', color: aiLoading || !aiInput.trim() ? '#475569' : '#fff',
                      transition: 'all 0.15s',
                    }}
                  >
                    Send
                  </button>
                  {aiMessages.length > 0 && (
                    <button
                      onClick={() => { setAiMessages([]); setAiError(''); }}
                      style={{ padding: '9px 12px', borderRadius: '8px', background: 'transparent', border: '1px solid #1e293b', color: '#475569', cursor: 'pointer', fontSize: '12px' }}
                    >
                      Clear
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* Price Alerts */}
      <div style={{ marginBottom: '32px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
          <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#cbd5e1', margin: 0 }}>Price Alerts</h2>
          <button
            onClick={() => {
              if (!alertOpen && curPrice != null && !alertThreshold) {
                setAlertThreshold(curPrice.toFixed(2));
              }
              setAlertOpen((prev: boolean) => !prev);
            }}
            style={{ fontSize: '12px', padding: '5px 12px', borderRadius: '6px', border: '1px solid rgba(99,102,241,0.4)', background: 'rgba(99,102,241,0.08)', color: '#818cf8', cursor: 'pointer' }}
          >
            + New Alert
          </button>
        </div>

        {alertOpen && (
          <div style={{ background: 'rgba(30,41,59,0.8)', border: '1px solid rgba(99,102,241,0.2)', borderRadius: '10px', padding: '16px', marginBottom: '12px', display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'flex-end' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <label style={{ fontSize: '11px', color: '#64748b' }}>Condition</label>
              <select
                value={alertCondition}
                onChange={e => setAlertCondition(e.target.value)}
                style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '6px', padding: '6px 10px', fontSize: '13px' }}
              >
                <optgroup label="Price">
                  <option value="above">Price rises above</option>
                  <option value="below">Price falls below</option>
                </optgroup>
                <optgroup label="Price vs EMA">
                  <option value="cross_above_ema">Crosses above EMA</option>
                  <option value="cross_below_ema">Crosses below EMA</option>
                </optgroup>
                <optgroup label="EMA50 vs EMA200">
                  <option value="golden_cross">Golden Cross (EMA50 ↑ EMA200)</option>
                  <option value="death_cross">Death Cross (EMA50 ↓ EMA200)</option>
                </optgroup>
                <optgroup label="Milestone">
                  <option value="new_52wk_high">New 52-week high</option>
                  <option value="new_52wk_low">New 52-week low</option>
                </optgroup>
                <optgroup label="Pattern Signals">
                  <option value="macd_bullish_cross">MACD Bullish Crossover</option>
                  <option value="rsi_oversold_bounce">RSI Oversold Bounce (crosses 30)</option>
                  <option value="double_bottom">Double Bottom (W-pattern)</option>
                  <option value="breakout">Volume Breakout (20-day high + surge)</option>
                </optgroup>
              </select>
            </div>
            {/* Price threshold — only for above/below */}
            {!isEmaCondition && !isNoThreshold && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <label style={{ fontSize: '11px', color: '#64748b' }}>Target price</label>
                <input
                  type="number"
                  placeholder={curPrice ? curPrice.toFixed(2) : '0.00'}
                  value={alertThreshold}
                  onChange={e => setAlertThreshold(e.target.value)}
                  style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '6px', padding: '6px 10px', fontSize: '13px', width: '110px' }}
                />
              </div>
            )}
            {/* EMA period selector */}
            {isEmaCondition && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <label style={{ fontSize: '11px', color: '#64748b' }}>EMA period</label>
                <select
                  value={alertEmaPeriod}
                  onChange={e => setAlertEmaPeriod(e.target.value)}
                  style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '6px', padding: '6px 10px', fontSize: '13px' }}
                >
                  <option value="20">20-day</option>
                  <option value="50">50-day</option>
                  <option value="200">200-day</option>
                </select>
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', flex: 1, minWidth: '160px' }}>
              <label style={{ fontSize: '11px', color: '#64748b' }}>Note (optional)</label>
              <input
                type="text"
                placeholder="e.g. Buy signal"
                value={alertNote}
                onChange={e => setAlertNote(e.target.value)}
                style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid rgba(148,163,184,0.15)', borderRadius: '6px', padding: '6px 10px', fontSize: '13px', width: '100%' }}
              />
            </div>
            {/* Recurring toggle — only for pattern/technical conditions */}
            {isNoThreshold && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <label style={{ fontSize: '11px', color: '#64748b' }}>Alert mode</label>
                <div style={{ display: 'flex', gap: '6px' }}>
                  <button
                    onClick={() => setAlertRecurring(false)}
                    style={{ padding: '5px 10px', borderRadius: '5px', fontSize: '11px', border: `1px solid ${!alertRecurring ? 'rgba(99,102,241,0.5)' : '#1e293b'}`, background: !alertRecurring ? 'rgba(99,102,241,0.15)' : 'transparent', color: !alertRecurring ? '#a5b4fc' : '#475569', cursor: 'pointer' }}
                  >Once</button>
                  <button
                    onClick={() => setAlertRecurring(true)}
                    style={{ padding: '5px 10px', borderRadius: '5px', fontSize: '11px', border: `1px solid ${alertRecurring ? 'rgba(251,191,36,0.5)' : '#1e293b'}`, background: alertRecurring ? 'rgba(251,191,36,0.08)' : 'transparent', color: alertRecurring ? '#fbbf24' : '#475569', cursor: 'pointer' }}
                  >↻ Recurring</button>
                </div>
              </div>
            )}
            {/* Show email field only if no account email is saved */}
            {!alertEmail ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', flex: 1, minWidth: '180px' }}>
                <label style={{ fontSize: '11px', color: '#f87171' }}>Email required — set in Settings or enter below</label>
                <input
                  type="email"
                  placeholder="you@example.com"
                  value={alertEmail}
                  onChange={e => setAlertEmail(e.target.value)}
                  style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid rgba(239,68,68,0.4)', borderRadius: '6px', padding: '6px 10px', fontSize: '13px', width: '100%' }}
                />
              </div>
            ) : (
              <div style={{ fontSize: '11px', color: '#475569', alignSelf: 'center' }}>
                → {alertEmail}
              </div>
            )}
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <button
                onClick={createAlert}
                disabled={alertSaving || (!isNoThreshold && !isEmaCondition && !alertThreshold) || !alertEmail}
                style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: alertSaving || (!isNoThreshold && !isEmaCondition && !alertThreshold) || !alertEmail ? '#334155' : '#6366f1', color: '#fff', fontSize: '13px', cursor: alertSaving || (!isNoThreshold && !isEmaCondition && !alertThreshold) || !alertEmail ? 'not-allowed' : 'pointer' }}
              >
                {alertSaving ? 'Saving…' : 'Set Alert'}
              </button>
              {alertMsg && <span style={{ fontSize: '12px', color: alertMsg === 'Alert set!' ? '#4ade80' : '#f87171' }}>{alertMsg}</span>}
            </div>
          </div>
        )}

        {alerts && alerts.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {alerts.map(a => {
              const isUp = ['above', 'cross_above_ema', 'new_52wk_high', 'golden_cross', 'macd_bullish_cross', 'rsi_oversold_bounce', 'double_bottom', 'breakout'].includes(a.condition);
              const icon = isUp ? '▲' : '▼';
              let label = '';
              if (a.condition === 'above') label = `Rises above ${a.threshold}`;
              else if (a.condition === 'below') label = `Falls below ${a.threshold}`;
              else if (a.condition === 'cross_above_ema') label = `Crosses above EMA${a.threshold}`;
              else if (a.condition === 'cross_below_ema') label = `Crosses below EMA${a.threshold}`;
              else if (a.condition === 'new_52wk_high') label = 'New 52-week high';
              else if (a.condition === 'new_52wk_low') label = 'New 52-week low';
              else if (a.condition === 'golden_cross') label = 'Golden Cross (EMA50 ↑ EMA200)';
              else if (a.condition === 'death_cross') label = 'Death Cross (EMA50 ↓ EMA200)';
              else if (a.condition === 'macd_bullish_cross') label = 'MACD Bullish Crossover';
              else if (a.condition === 'rsi_oversold_bounce') label = 'RSI Oversold Bounce (crosses 30)';
              else if (a.condition === 'double_bottom') label = 'Double Bottom (W-pattern)';
              else if (a.condition === 'breakout') label = 'Volume Breakout (20-day high + surge)';
              else label = a.condition;
              const isDone = a.triggered && !a.recurring;
              const lastFired = a.recurring && a.last_sent_at
                ? new Date(a.last_sent_at).toLocaleDateString()
                : a.triggered_at ? new Date(a.triggered_at).toLocaleDateString() : null;
              return (
                <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: '12px', background: isDone ? 'rgba(30,41,59,0.4)' : 'rgba(30,41,59,0.7)', border: `1px solid ${isDone ? 'rgba(148,163,184,0.1)' : a.recurring ? 'rgba(251,191,36,0.25)' : 'rgba(99,102,241,0.2)'}`, borderRadius: '8px', padding: '10px 14px' }}>
                  <span style={{ fontSize: '18px' }}>{icon}</span>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: '13px', color: isDone ? '#64748b' : '#e2e8f0' }}>
                      {label}
                      {a.note && <span style={{ color: '#64748b', marginLeft: '8px' }}>— {a.note}</span>}
                    </div>
                    <div style={{ fontSize: '11px', color: '#475569', marginTop: '2px' }}>
                      → {a.email}
                      {lastFired && <span style={{ marginLeft: '8px' }}>· last fired {lastFired}</span>}
                    </div>
                  </div>
                  {a.recurring && (
                    <span style={{ fontSize: '10px', background: 'rgba(251,191,36,0.08)', color: '#fbbf24', padding: '2px 7px', borderRadius: '4px', border: '1px solid rgba(251,191,36,0.2)', whiteSpace: 'nowrap' }}>↻ recurring</span>
                  )}
                  {isDone && (
                    <span style={{ fontSize: '11px', background: 'rgba(74,222,128,0.1)', color: '#4ade80', padding: '2px 8px', borderRadius: '4px' }}>Triggered</span>
                  )}
                  <button
                    onClick={() => removeAlert(a.id)}
                    style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '16px', lineHeight: 1, padding: '2px 4px' }}
                    title="Delete alert"
                  >×</button>
                </div>
              );
            })}
          </div>
        ) : (
          <div style={{ fontSize: '12px', color: '#475569' }}>No alerts set for {symbol}. Click "+ New Alert" to get notified by email when the price hits your target.</div>
        )}
      </div>

      {/* Dividends */}
      <div style={{ borderRadius: '12px', border: '1px solid #1e293b', overflow: 'hidden' }}>
        <button
          onClick={() => setDivOpen((o: boolean) => !o)}
          style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', background: 'rgba(255,255,255,0.02)', border: 'none', cursor: 'pointer' }}
        >
          <span style={{ fontSize: '14px', fontWeight: 700, color: '#cbd5e1' }}>Dividends</span>
          <span style={{ color: '#475569', fontSize: '12px', transform: divOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>▼</span>
        </button>
        {divOpen && (
          <div style={{ padding: '16px 18px', borderTop: '1px solid #1e293b' }}>
            {!dividendData && <div style={{ fontSize: '12px', color: '#475569' }}>Loading dividend data…</div>}
            {dividendData?.error && <div style={{ fontSize: '12px', color: '#f87171' }}>Error: {dividendData.error}</div>}
            {dividendData && !dividendData.error && (
              <>
                <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap', marginBottom: '16px' }}>
                  {[
                    { label: 'Annual Rate', value: dividendData.annual_div_rate != null ? `$${dividendData.annual_div_rate.toFixed(4)}` : '—' },
                    { label: 'Div Yield', value: dividendData.dividend_yield != null ? `${(dividendData.dividend_yield * 100).toFixed(2)}%` : '—' },
                    { label: 'Payout Ratio', value: dividendData.payout_ratio != null ? `${(dividendData.payout_ratio * 100).toFixed(1)}%` : '—' },
                    { label: 'Ex-Div Date', value: dividendData.ex_dividend_date != null ? new Date(dividendData.ex_dividend_date * 1000).toLocaleDateString() : '—' },
                  ].map(item => (
                    <div key={item.label}>
                      <div style={{ fontSize: '10px', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '3px' }}>{item.label}</div>
                      <div style={{ fontSize: '15px', fontWeight: 700, color: '#e2e8f0' }}>{item.value}</div>
                    </div>
                  ))}
                  {/* Dividend sustainability grade */}
                  {dividendData.payout_ratio != null && (() => {
                    const pr = dividendData.payout_ratio;
                    const eg = data.fundamentals?.earnings_growth ?? null;
                    const dy = dividendData.dividend_yield ?? 0;
                    let score = 100;
                    if (pr > 0.9) score -= 50;
                    else if (pr > 0.75) score -= 30;
                    else if (pr > 0.6) score -= 15;
                    else if (pr > 0.4) score -= 5;
                    if (eg != null) {
                      if (eg < -0.1) score -= 20;
                      else if (eg < 0) score -= 10;
                      else if (eg > 0.1) score += 10;
                    }
                    if (dy > 0.08) score -= 10;
                    const [g, c, d] = score >= 90 ? ['A', '#4ade80', 'Very safe'] :
                                      score >= 75 ? ['B', '#86efac', 'Sustainable'] :
                                      score >= 55 ? ['C', '#fbbf24', 'Adequate'] :
                                      score >= 35 ? ['D', '#fb923c', 'Stretched'] :
                                                    ['F', '#f87171', 'At risk'];
                    return (
                      <div>
                        <div style={{ fontSize: '10px', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '3px' }}>Div Safety</div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span style={{ fontSize: '18px', fontWeight: 800, color: c }}>{g}</span>
                          <span style={{ fontSize: '11px', color: '#64748b' }}>{d}</span>
                        </div>
                      </div>
                    );
                  })()}
                </div>
                {dividendData.dividends.length === 0 ? (
                  <div style={{ fontSize: '12px', color: '#475569' }}>No dividend history found.</div>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                      <thead>
                        <tr style={{ background: '#080f1e' }}>
                          {['Date', 'Amount'].map(h => (
                            <th key={h} style={{ padding: '7px 12px', textAlign: h === 'Amount' ? 'right' : 'left', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {dividendData.dividends.map((d, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid rgba(30,41,59,0.4)' }}>
                            <td style={{ padding: '7px 12px', color: '#64748b' }}>{d.date}</td>
                            <td style={{ padding: '7px 12px', textAlign: 'right', color: '#e2e8f0', fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>${d.amount.toFixed(4)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <div style={{ fontSize: '10px', color: '#334155', marginTop: '10px' }}>Source: Yahoo Finance · last 40 payments shown</div>
              </>
            )}
          </div>
        )}
      </div>

      {/* Institutional Holdings */}
      <div style={{ borderRadius: '12px', border: '1px solid #1e293b', overflow: 'hidden' }}>
        <button
          onClick={() => setInstOpen(o => !o)}
          style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', background: 'rgba(255,255,255,0.02)', border: 'none', cursor: 'pointer' }}
        >
          <span style={{ fontSize: '14px', fontWeight: 700, color: '#cbd5e1' }}>Institutional Holdings</span>
          <span style={{ color: '#475569', fontSize: '12px', transform: instOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>▼</span>
        </button>
        {instOpen && (
          <div style={{ padding: '16px 18px', borderTop: '1px solid #1e293b' }}>
            {!instData && <div style={{ fontSize: '12px', color: '#475569' }}>Loading institutional data…</div>}
            {instData?.error && <div style={{ fontSize: '12px', color: '#f87171' }}>Error: {instData.error}</div>}
            {instData && !instData.error && (
              <>
                <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap', marginBottom: '16px' }}>
                  {[
                    { label: 'Institutions Hold', value: instData.held_pct_institutions != null ? `${(instData.held_pct_institutions * 100).toFixed(1)}%` : '—' },
                    { label: 'Insiders Hold', value: instData.held_pct_insiders != null ? `${(instData.held_pct_insiders * 100).toFixed(1)}%` : '—' },
                    { label: 'Float Shares', value: instData.float_shares != null ? (instData.float_shares >= 1e9 ? `${(instData.float_shares / 1e9).toFixed(2)}B` : `${(instData.float_shares / 1e6).toFixed(1)}M`) : '—' },
                    { label: 'Shares Outstanding', value: instData.shares_outstanding != null ? (instData.shares_outstanding >= 1e9 ? `${(instData.shares_outstanding / 1e9).toFixed(2)}B` : `${(instData.shares_outstanding / 1e6).toFixed(1)}M`) : '—' },
                  ].map(item => (
                    <div key={item.label}>
                      <div style={{ fontSize: '10px', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '3px' }}>{item.label}</div>
                      <div style={{ fontSize: '15px', fontWeight: 700, color: '#e2e8f0' }}>{item.value}</div>
                    </div>
                  ))}
                </div>
                {instData.institutional_holders.length === 0 ? (
                  <div style={{ fontSize: '12px', color: '#475569' }}>No institutional holder data found.</div>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                      <thead>
                        <tr style={{ background: '#080f1e' }}>
                          {['Holder', 'Shares', '% Out', 'Value', 'Reported'].map(h => (
                            <th key={h} style={{ padding: '7px 12px', textAlign: h === 'Holder' ? 'left' : 'right', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', whiteSpace: 'nowrap' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {instData.institutional_holders.map((h, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid rgba(30,41,59,0.4)' }}>
                            <td style={{ padding: '7px 12px', color: '#94a3b8' }}>{h.holder}</td>
                            <td style={{ padding: '7px 12px', textAlign: 'right', color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
                              {h.shares != null ? (h.shares >= 1e6 ? `${(h.shares / 1e6).toFixed(2)}M` : h.shares.toLocaleString()) : '—'}
                            </td>
                            <td style={{ padding: '7px 12px', textAlign: 'right', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                              {h.pct_out != null ? `${(h.pct_out * 100).toFixed(2)}%` : '—'}
                            </td>
                            <td style={{ padding: '7px 12px', textAlign: 'right', color: '#64748b', fontVariantNumeric: 'tabular-nums' }}>
                              {h.value != null ? (h.value >= 1e9 ? `$${(h.value / 1e9).toFixed(2)}B` : `$${(h.value / 1e6).toFixed(1)}M`) : '—'}
                            </td>
                            <td style={{ padding: '7px 12px', textAlign: 'right', color: '#475569' }}>{h.date_reported ?? '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <div style={{ fontSize: '10px', color: '#334155', marginTop: '10px' }}>Source: Yahoo Finance / SEC 13F filings · top 20 holders · 3-day cache</div>
              </>
            )}
          </div>
        )}
      </div>

      {/* Options Flow */}
      {optionsFlow && optionsFlow.available && (
        <div style={{ marginBottom: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#cbd5e1', margin: 0 }}>Options Flow</h2>
            {(optionsFlow.whale_count ?? 0) > 0 && (
              <span style={{ fontSize: 11, fontWeight: 700, color: '#f59e0b', background: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.3)', borderRadius: 5, padding: '2px 8px' }}>
                🐋 {optionsFlow.whale_count} whale {(optionsFlow.whale_count ?? 0) === 1 ? 'trade' : 'trades'}
              </span>
            )}
          </div>
          <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
            {/* C/P ratio bar */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#64748b', marginBottom: 3 }}>
                  <span>Calls {optionsFlow.call_volume?.toLocaleString()}</span>
                  <span>C/P {optionsFlow.cp_ratio?.toFixed(2)}</span>
                  <span>Puts {optionsFlow.put_volume?.toLocaleString()}</span>
                </div>
                <div style={{ height: 8, borderRadius: 4, background: '#1e293b', overflow: 'hidden', display: 'flex' }}>
                  {(() => {
                    const total = (optionsFlow.call_volume ?? 0) + (optionsFlow.put_volume ?? 0);
                    const callPct = total > 0 ? (optionsFlow.call_volume ?? 0) / total * 100 : 50;
                    return <>
                      <div style={{ width: `${callPct}%`, background: '#22c55e', borderRadius: '4px 0 0 4px' }} />
                      <div style={{ flex: 1, background: '#ef4444', borderRadius: '0 4px 4px 0' }} />
                    </>;
                  })()}
                </div>
              </div>
              <div style={{
                padding: '3px 10px', borderRadius: 5, fontSize: 11, fontWeight: 700, flexShrink: 0,
                background: optionsFlow.sentiment?.includes('bullish') ? 'rgba(34,197,94,0.15)' : optionsFlow.sentiment?.includes('bearish') ? 'rgba(239,68,68,0.15)' : 'rgba(100,116,139,0.15)',
                color: optionsFlow.sentiment?.includes('bullish') ? '#4ade80' : optionsFlow.sentiment?.includes('bearish') ? '#f87171' : '#94a3b8',
              }}>
                {(optionsFlow.sentiment ?? 'neutral').replace(/_/g, ' ')}
              </div>
            </div>

            {/* Unusual contracts table */}
            {optionsFlow.unusual && optionsFlow.unusual.length > 0 && (
              <>
                <div style={{ fontSize: 10, color: '#475569', fontWeight: 700, letterSpacing: '0.06em', marginBottom: 6 }}>
                  UNUSUAL ACTIVITY — {optionsFlow.expiries_used?.join(', ')}
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid #1e293b' }}>
                        {['Side', 'Strike', 'Expiry', 'Volume', 'OI', 'Vol/OI', 'IV', 'ITM', 'Premium'].map(h => (
                          <th key={h} style={{ padding: '4px 8px', textAlign: 'left', color: '#475569', fontWeight: 500 }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {optionsFlow.unusual.map((c, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid #0f172a', background: c.is_whale ? 'rgba(245,158,11,0.05)' : 'transparent', outline: c.is_whale ? '1px solid rgba(245,158,11,0.2)' : 'none' }}>
                          <td style={{ padding: '5px 8px' }}>
                            <span style={{ fontWeight: 700, color: c.side === 'call' ? '#4ade80' : '#f87171' }}>
                              {c.side.toUpperCase()}
                            </span>
                          </td>
                          <td style={{ padding: '5px 8px', color: '#e2e8f0' }}>${c.strike}</td>
                          <td style={{ padding: '5px 8px', color: '#64748b' }}>{c.expiry}</td>
                          <td style={{ padding: '5px 8px', color: '#e2e8f0', fontWeight: 600 }}>{c.volume.toLocaleString()}</td>
                          <td style={{ padding: '5px 8px', color: '#64748b' }}>{c.oi.toLocaleString()}</td>
                          <td style={{ padding: '5px 8px', color: c.vol_oi > 1 ? '#f59e0b' : '#94a3b8', fontWeight: c.vol_oi > 1 ? 700 : 400 }}>{c.vol_oi.toFixed(2)}×</td>
                          <td style={{ padding: '5px 8px', color: '#94a3b8' }}>{c.iv.toFixed(0)}%</td>
                          <td style={{ padding: '5px 8px', color: c.itm ? '#4ade80' : '#475569' }}>{c.itm ? 'ITM' : 'OTM'}</td>
                          <td style={{ padding: '5px 8px', color: c.is_whale ? '#f59e0b' : '#475569', fontWeight: c.is_whale ? 700 : 400 }}>
                            {c.premium >= 1_000_000 ? `$${(c.premium / 1_000_000).toFixed(1)}M` : c.premium >= 1_000 ? `$${Math.round(c.premium / 1_000)}K` : c.premium > 0 ? `$${c.premium}` : '—'}
                            {c.is_whale && ' 🐋'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* T230-DATA-OPTIONS-CHAIN: full strike/expiry matrix, opt-in expand (heavier fetch
          than the Options Flow summary above) */}
      {optionsFlow && optionsFlow.available && (
        <div style={{ marginBottom: 24 }}>
          <div
            onClick={() => setChainOpen(o => !o)}
            style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: chainOpen ? 12 : 0, cursor: 'pointer' }}
          >
            <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#cbd5e1', margin: 0 }}>Options Chain</h2>
            <span style={{ fontSize: 11, color: '#475569' }}>{chainOpen ? '▲ Hide' : '▼ Show full strike matrix'}</span>
          </div>
          {chainOpen && (
            <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
              {!optionsChain && <div style={{ fontSize: 12, color: '#475569' }}>Loading options chain…</div>}
              {optionsChain && !optionsChain.available && (
                <div style={{ fontSize: 12, color: '#475569' }}>No options chain available for this symbol.</div>
              )}
              {optionsChain && optionsChain.available && (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 11, color: '#64748b' }}>Expiry:</span>
                    {(optionsChain.expiries ?? []).map(exp => (
                      <button
                        key={exp}
                        onClick={() => setChainExpiry(exp)}
                        style={{
                          fontSize: 11, fontWeight: (optionsChain.expiry === exp) ? 700 : 400,
                          padding: '3px 10px', borderRadius: 5, cursor: 'pointer',
                          background: optionsChain.expiry === exp ? 'rgba(56,189,248,0.15)' : 'transparent',
                          border: `1px solid ${optionsChain.expiry === exp ? 'rgba(56,189,248,0.4)' : '#1e293b'}`,
                          color: optionsChain.expiry === exp ? '#38bdf8' : '#94a3b8',
                        }}
                      >
                        {exp}
                      </button>
                    ))}
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid #1e293b' }}>
                          {['Bid', 'Ask', 'Last', 'Vol', 'OI', 'IV'].map(h => (
                            <th key={`c-${h}`} style={{ padding: '4px 8px', textAlign: 'right', color: '#4ade80', fontWeight: 500 }}>{h}</th>
                          ))}
                          <th style={{ padding: '4px 12px', textAlign: 'center', color: '#475569', fontWeight: 700 }}>Strike</th>
                          {['Bid', 'Ask', 'Last', 'Vol', 'OI', 'IV'].map(h => (
                            <th key={`p-${h}`} style={{ padding: '4px 8px', textAlign: 'left', color: '#f87171', fontWeight: 500 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {(() => {
                          const calls = optionsChain.calls ?? [];
                          const puts = optionsChain.puts ?? [];
                          const strikes = Array.from(new Set([...calls.map(c => c.strike), ...puts.map(p => p.strike)])).sort((a, b) => a - b);
                          return strikes.map(strike => {
                            const c = calls.find(x => x.strike === strike);
                            const p = puts.find(x => x.strike === strike);
                            return (
                              <tr key={strike} style={{ borderBottom: '1px solid #0f172a' }}>
                                <td style={{ padding: '4px 8px', textAlign: 'right', color: c?.itm ? '#4ade80' : '#94a3b8' }}>{c ? c.bid.toFixed(2) : '—'}</td>
                                <td style={{ padding: '4px 8px', textAlign: 'right', color: c?.itm ? '#4ade80' : '#94a3b8' }}>{c ? c.ask.toFixed(2) : '—'}</td>
                                <td style={{ padding: '4px 8px', textAlign: 'right', color: '#94a3b8' }}>{c ? c.last_price.toFixed(2) : '—'}</td>
                                <td style={{ padding: '4px 8px', textAlign: 'right', color: '#94a3b8' }}>{c ? c.volume.toLocaleString() : '—'}</td>
                                <td style={{ padding: '4px 8px', textAlign: 'right', color: '#64748b' }}>{c ? c.oi.toLocaleString() : '—'}</td>
                                <td style={{ padding: '4px 8px', textAlign: 'right', color: '#64748b' }}>{c ? `${c.iv.toFixed(0)}%` : '—'}</td>
                                <td style={{ padding: '4px 12px', textAlign: 'center', color: '#e2e8f0', fontWeight: 700 }}>${strike}</td>
                                <td style={{ padding: '4px 8px', textAlign: 'left', color: p?.itm ? '#f87171' : '#94a3b8' }}>{p ? p.bid.toFixed(2) : '—'}</td>
                                <td style={{ padding: '4px 8px', textAlign: 'left', color: p?.itm ? '#f87171' : '#94a3b8' }}>{p ? p.ask.toFixed(2) : '—'}</td>
                                <td style={{ padding: '4px 8px', textAlign: 'left', color: '#94a3b8' }}>{p ? p.last_price.toFixed(2) : '—'}</td>
                                <td style={{ padding: '4px 8px', textAlign: 'left', color: '#94a3b8' }}>{p ? p.volume.toLocaleString() : '—'}</td>
                                <td style={{ padding: '4px 8px', textAlign: 'left', color: '#64748b' }}>{p ? p.oi.toLocaleString() : '—'}</td>
                                <td style={{ padding: '4px 8px', textAlign: 'left', color: '#64748b' }}>{p ? `${p.iv.toFixed(0)}%` : '—'}</td>
                              </tr>
                            );
                          });
                        })()}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* News feed — full width below chart */}
      <div>
        <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#cbd5e1', marginBottom: '12px' }}>
          News &amp; Sentiment
        </h2>
        {!news && <div style={{ fontSize: '12px', color: '#475569' }}>Loading news…</div>}
        {news && news.length === 0 && <div style={{ fontSize: '12px', color: '#475569' }}>No recent news found.</div>}
        {news && news.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '10px' }}>
            {news.map((item, i) => <NewsCard key={i} item={item} />)}
          </div>
        )}
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>

      {compareOpen && compareRows.length >= 2 && (
        <PeerCompareDrawer
          rows={compareRows}
          prices={priceMap}
          onClose={() => setCompareOpen(false)}
        />
      )}
    </div>
  );
}
