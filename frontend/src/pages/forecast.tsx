/**
 * Forecast page — AI-powered swing trade screener.
 *
 * AI provider: whichever is configured in Settings → AI Assistant
 *              (Claude or DeepSeek). Uses temperature=0 for deterministic JSON.
 *
 * Two screening modes × two markets
 * ──────────────────────────────────
 * 1. My Stocks (my_stocks)
 *    Single AI call. Passes all tracked stocks in the selected market that have
 *    a BUY/HOLD signal, along with their K-Score, RSI, MACD, SMA position, and
 *    latest price. AI returns the top 10 swing picks ranked by predicted gain.
 *
 * 2. Broad Screen (broad_screen) — two-pass flow
 *    Pass 1 — SYSTEM_TICKERS prompt:
 *      Asks AI to suggest 65 tickers (US) or 50 HKEX codes (HK) in a given
 *      price range. AI returns a raw JSON string[] array.
 *    Pass 2 — api.quickScan():
 *      Fetches real OHLCV data for those tickers via yfinance
 *      (POST /stocks/quick_scan). Computes RSI-14, SMA20/50, vol_ratio, 5d
 *      change, and 20d range position server-side.
 *    Pass 3 — SYSTEM_PICKS prompt:
 *      Passes the real scan results back to AI to rank and generate game plans.
 */
import { useState, useMemo } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import { api, type RankingRow, type LatestPrice, type SignalSummary, type QuickScanResult } from '@/lib/api';
import { mutate as globalMutate } from 'swr';
import { askAI, isAiConfigured, getAiProviderLabel } from '@/lib/ai';
import WatchlistPickerButton from '@/components/WatchlistPickerButton';
import { getSignalStyle } from '@/lib/settings';

// ── Types ────────────────────────────────────────────────────────────────────

type ForecastPick = {
  symbol: string;
  rank: number;
  predicted_gain_pct: number;
  confidence: 'high' | 'medium' | 'low';
  entry_low: number;
  entry_high: number;
  stop_loss: number;
  take_profit: number;
  horizon_days: number;
  setup: string;
  catalyst: string;
  risk: string;
  rationale: string;
};

type Universe = 'my_stocks' | 'broad_screen';
type Market   = 'US' | 'HK';

type PriceRange = { label: string; min: number; max: number };

const US_PRICE_RANGES: PriceRange[] = [
  { label: '$2 – $15',   min: 2,   max: 15  },
  { label: '$15 – $50',  min: 15,  max: 50  },
  { label: '$50 – $150', min: 50,  max: 150 },
  { label: 'Any price',  min: 0,   max: 9999 },
];

const HK_PRICE_RANGES: PriceRange[] = [
  { label: 'HK$5 – $50',    min: 5,   max: 50   },
  { label: 'HK$50 – $200',  min: 50,  max: 200  },
  { label: 'HK$200 – $600', min: 200, max: 600  },
  { label: 'Any price',     min: 0,   max: 9999 },
];

// ── Helpers ──────────────────────────────────────────────────────────────────

const CONF_STYLE = {
  high:   { color: '#4ade80', bg: 'rgba(34,197,94,0.12)',   border: 'rgba(34,197,94,0.35)'  },
  medium: { color: '#facc15', bg: 'rgba(250,204,21,0.12)',  border: 'rgba(250,204,21,0.35)' },
  low:    { color: '#94a3b8', bg: 'rgba(148,163,184,0.08)', border: 'rgba(148,163,184,0.2)' },
};

// ── AI prompts ───────────────────────────────────────────────────────────────

const SYSTEM_PICKS = `You are a professional swing trader. Analyze the stock data provided and pick the top 10 swing trade candidates for the next 10 business days.

Return ONLY a raw JSON array — no markdown, no text, start immediately with [

Each element exactly:
{"symbol":"SOFI","rank":1,"predicted_gain_pct":9.5,"confidence":"high","entry_low":7.20,"entry_high":7.55,"stop_loss":6.85,"take_profit":8.20,"horizon_days":10,"setup":"Breakout above 20d SMA on rising volume","catalyst":"RSI reset from oversold, increasing volume, above SMA20","risk":"Weak broader market could cap gains","rationale":"Strong technical setup: RSI 55, vol_ratio 1.4, above SMA20, 5d momentum positive."}

confidence: "high" (predicted_gain ≥7%), "medium" (3-7%), "low" (<3%)
predicted_gain_pct: % gain from current price to take_profit
entry_low/high: tight buy zone around current price or nearest support
stop_loss: 3-6% below entry
take_profit: first major resistance or 8-15% above entry for small caps`;

function buildPicksPrompt(
  stocks: QuickScanResult[] | ReturnType<typeof buildMyStocksData>,
  market: Market = 'US',
): string {
  const currency = market === 'HK' ? 'HKD' : 'USD';
  const isQuickScan = stocks.length > 0 && 'rsi' in stocks[0];

  if (isQuickScan) {
    const rows = (stocks as QuickScanResult[])
      .map(s => {
        const sma = s.above_sma20 == null ? '?' : s.above_sma20 ? 'YES' : 'NO';
        return `${s.symbol} | ${market === 'HK' ? 'HK$' : '$'}${s.price.toFixed(2)} ${s.change_pct != null ? `(${s.change_pct >= 0 ? '+' : ''}${s.change_pct.toFixed(1)}%)` : ''} | RSI:${s.rsi ?? '?'} | SMA20:${sma} | Vol:${s.vol_ratio != null ? s.vol_ratio.toFixed(1) + 'x' : '?'} | 5d:${s.change_5d != null ? (s.change_5d >= 0 ? '+' : '') + s.change_5d.toFixed(1) + '%' : '?'}`;
      })
      .join('\n');
    return `Pick top 10 swing trades (10-day horizon) from these ${market} stocks. Prices are in ${currency}. Prefer: RSI 42-65, above SMA20, vol_ratio >1.0, positive 5d momentum. SYMBOL | PRICE | RSI | ABOVE_SMA20 | VOL_RATIO | 5D_CHG\n${rows}\n\nReturn JSON array now. Use ${currency} prices in entry_low, entry_high, stop_loss, take_profit fields.`;
  }

  const rows = (stocks as { symbol: string; price: string; signal: string; conf: string; bull: string; kscore: string; tech: string; mom: string; sector: string }[])
    .map(s => `${s.symbol} | ${s.price} | ${s.signal} | conf:${s.conf} | bull:${s.bull} | K:${s.kscore} | tech:${s.tech} | mom:${s.mom} | ${s.sector}`)
    .join('\n');
  return `Pick top 10 swing trades from these tracked ${market} stocks (10-day horizon). Prices in ${currency}. SYMBOL | PRICE | SIGNAL | CONF | BULL_PROB | K-SCORE | TECH | MOM | SECTOR\n${rows}\n\nReturn JSON array now. Use ${currency} prices in entry_low, entry_high, stop_loss, take_profit fields.`;
}

function buildMyStocksData(
  rankings: RankingRow[],
  sigMap: Record<string, SignalSummary>,
  priceMap: Record<string, LatestPrice>,
  market: Market,
) {
  const currencyPrefix = market === 'HK' ? 'HK$' : '$';
  return rankings
    .filter(r => r.market === market)
    .map(r => ({
      symbol: r.symbol,
      price: priceMap[r.symbol] ? `${currencyPrefix}${priceMap[r.symbol].price.toFixed(2)} (${(priceMap[r.symbol].change_pct ?? 0) >= 0 ? '+' : ''}${(priceMap[r.symbol].change_pct ?? 0).toFixed(1)}%)` : 'N/A',
      signal: sigMap[r.symbol]?.signal ?? 'N/A',
      conf: sigMap[r.symbol]?.confidence != null ? `${sigMap[r.symbol].confidence.toFixed(0)}%` : '?',
      bull: sigMap[r.symbol]?.bullish_probability != null ? `${(sigMap[r.symbol].bullish_probability! * 100).toFixed(0)}%` : '?',
      kscore: r.score != null ? r.score.toFixed(0) : '?',
      tech: r.technical != null ? r.technical.toFixed(0) : '?',
      mom: r.momentum != null ? r.momentum.toFixed(0) : '?',
      sector: r.sector ?? 'N/A',
    }))
    .filter(r => r.signal === 'BUY' || r.signal === 'HOLD')
    .sort((a, b) => Number(b.kscore) - Number(a.kscore))
    .slice(0, 35);
}

// Prompt AI to suggest tickers for the broad screen
const SYSTEM_TICKERS = `You output ONLY raw JSON arrays. No markdown, no explanation. Start with [ immediately.`;

function buildTickerPrompt(priceMin: number, priceMax: number, market: Market): string {
  if (market === 'HK') {
    const priceLabel = priceMax >= 9999 ? 'any price' : `HK$${priceMin}–$${priceMax}`;
    return `List 50 Hong Kong (HKEX) stock ticker codes that are good swing trading candidates right now.

Criteria:
- Use exact HKEX format with .HK suffix: "0700.HK", "9988.HK", "3690.HK"
- Share price roughly ${priceLabel} (in HKD)
- Include sectors: Technology, Consumer, Finance, Healthcare, EV/Auto, Semiconductor, Property, Retail, AI/Cloud
- Companies with recent catalysts: earnings, product launches, policy tailwinds, analyst upgrades
- Liquid stocks with active trading — avoid thinly traded names
- Mix of large-caps (Tencent, Alibaba, Meituan), mid-caps, and emerging leaders in their niche

Return ONLY a JSON array of HKEX ticker strings: ["0700.HK","9988.HK","3690.HK","9999.HK","1810.HK","2318.HK"]`;
  }

  return `List 100 US stock tickers that are good swing trading candidates right now.

Criteria:
- Share price MUST be strictly between $${priceMin} and $${priceMax} — do NOT include any ticker priced below $${priceMin} or above $${priceMax}
- Small to mid-cap companies ($200M – $15B market cap)
- Sectors with momentum: Technology, Healthcare, Biotech, Energy, Fintech, Consumer, Industrials, Defense, AI/ML
- Companies with recent catalysts: earnings beats, product launches, sector rotation, analyst upgrades
- Active traders, liquid (not thinly traded)
- NOT mega-caps (no AAPL, MSFT, GOOGL, AMZN, NVDA, TSLA, META)${priceMin >= 2 ? '' : '\n- NOT penny stocks under $1'}

Include a mix of: small caps with momentum, sector leaders in their niche, recent breakout candidates.
Remember: ALL tickers must have a current stock price between $${priceMin} and $${priceMax}.

Return ONLY a JSON array of ticker strings: ["SOFI","HOOD","DKNG","PLTR","AI","RXRX","DOCS","OPEN","IONQ","BBAI"]`;
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function ForecastPage() {
  const aiReady = isAiConfigured();

  const { data: signals }  = useSWR<SignalSummary[]>('signals-' + getSignalStyle(),     () => api.allSignals(getSignalStyle()),   { revalidateOnFocus: false });
  const { data: rankings } = useSWR<{ rankings: RankingRow[] }>('rankings-all', () => api.rankings(), { revalidateOnFocus: false });
  const { data: prices }   = useSWR<LatestPrice[]>('latest-prices',     () => api.latestPrices(), { revalidateOnFocus: false });

  const [universe, setUniverse]     = useState<Universe>('broad_screen');
  const [market, setMarket]         = useState<Market>('US');
  const [priceRange, setPriceRange] = useState<PriceRange>(US_PRICE_RANGES[0]);

  const [picks, setPicks]             = useState<ForecastPick[] | null>(null);
  const [loading, setLoading]         = useState(false);
  const [steps, setSteps]             = useState<{ text: string; done: boolean }[]>([]);
  const [error, setError]             = useState('');
  const [generatedAt, setGeneratedAt] = useState<Date | null>(null);
  const [scanCount, setScanCount]     = useState(0);

  const [sortBy, setSortBy]           = useState<'rank' | 'gain' | 'confidence'>('rank');
  const [savedToBoard, setSavedToBoard] = useState<Set<string>>(new Set());
  const [savingToBoard, setSavingToBoard] = useState<string | null>(null);

  const priceRanges = market === 'HK' ? HK_PRICE_RANGES : US_PRICE_RANGES;

  function handleMarketChange(m: Market) {
    setMarket(m);
    setPriceRange(m === 'HK' ? HK_PRICE_RANGES[0] : US_PRICE_RANGES[0]);
    setPicks(null);
    setSteps([]);
    setError('');
  }

  async function savePickToBoard(pick: ForecastPick) {
    setSavingToBoard(pick.symbol);
    try {
      await api.createBoardPlan({
        symbol: pick.symbol,
        stage: 'watch',
        entry_price: pick.entry_low,
        stop_loss: pick.stop_loss,
        take_profit: pick.take_profit,
        notes: `${pick.setup}. Catalyst: ${pick.catalyst}. Risk: ${pick.risk}`,
        source: 'forecast',
      });
      setSavedToBoard(s => new Set(s).add(pick.symbol));
      globalMutate('board');
    } catch { /* silently ignore */ }
    setSavingToBoard(null);
  }
  const [filterConf, setFilterConf]   = useState<'all' | 'high' | 'medium'>('all');

  const sigMap   = useMemo(() => { const m: Record<string, SignalSummary> = {}; (signals ?? []).forEach(s => { m[s.symbol] = s; }); return m; }, [signals]);
  const priceMap = useMemo(() => { const m: Record<string, LatestPrice>  = {}; (prices  ?? []).forEach(p => { m[p.symbol] = p; }); return m; }, [prices]);

  function addStep(text: string, done = false) {
    setSteps(prev => [...prev, { text, done }]);
  }
  function completeLastStep() {
    setSteps(prev => prev.map((s, i) => i === prev.length - 1 ? { ...s, done: true } : s));
  }

  function extractJsonArray(raw: string): string | null {
    const stripped = raw.replace(/```(?:json)?\s*/gi, '').replace(/```/g, '');
    const start = stripped.indexOf('[');
    if (start === -1) return null;
    // Walk bracket depth to find the matching close — avoids greedy regex
    // swallowing trailing text that contains ] characters (e.g. Claude footnotes).
    let depth = 0;
    for (let i = start; i < stripped.length; i++) {
      if (stripped[i] === '[') depth++;
      else if (stripped[i] === ']') { depth--; if (depth === 0) return stripped.slice(start, i + 1); }
    }
    return null;
  }

  async function parseJsonArray(raw: string): Promise<ForecastPick[]> {
    const extracted = extractJsonArray(raw);
    if (!extracted) throw new Error('AI did not return a JSON array. Try again.');
    const parsed = JSON.parse(extracted);
    if (!Array.isArray(parsed) || parsed.length === 0) throw new Error('AI returned empty results. Try again.');
    return parsed as ForecastPick[];
  }

  async function runBroadScreen() {
    const rangeLabel = market === 'HK'
      ? (priceRange.max >= 9999 ? 'any price' : `HK$${priceRange.min}–$${priceRange.max}`)
      : `$${priceRange.min}–$${priceRange.max}`;

    // Step 1: ask AI for tickers
    addStep(`Asking ${getAiProviderLabel()} to suggest ${market} tickers in ${rangeLabel} range…`);
    const tickerRaw = await askAI(
      [{ role: 'user', content: buildTickerPrompt(priceRange.min, priceRange.max, market) }],
      SYSTEM_TICKERS, 800, 0,
    );
    const tickerExtracted = extractJsonArray(tickerRaw);
    if (!tickerExtracted) throw new Error('AI did not return a ticker list.');
    const tickers: string[] = JSON.parse(tickerExtracted);
    if (!Array.isArray(tickers) || tickers.length === 0) throw new Error('AI returned no tickers.');
    completeLastStep();

    // Step 2: fetch real data from yfinance
    addStep(`Fetching live data for ${tickers.length} tickers via yfinance…`);
    const scanned = await api.quickScan(tickers, priceRange.min || undefined, priceRange.max >= 9999 ? undefined : priceRange.max);
    if (scanned.length === 0) throw new Error(`No stocks passed the $${priceRange.min}–$${priceRange.max} price filter. The AI suggested ${tickers.length} tickers but none currently trade in this range. Try a different range or run again.`);
    setScanCount(scanned.length);
    completeLastStep();

    // Step 3: pass real data to AI for picks
    addStep(`Analysing ${scanned.length} stocks for top 10 swing setups…`);
    const picksRaw = await askAI(
      [{ role: 'user', content: buildPicksPrompt(scanned, market) }],
      SYSTEM_PICKS, 4096, 0,
    );
    const result = await parseJsonArray(picksRaw);
    completeLastStep();
    return result;
  }

  async function runMyStocksScreen() {
    addStep(`Gathering BUY signals from your tracked ${market} stocks…`);
    const myData = buildMyStocksData(rankings?.rankings ?? [], sigMap, priceMap, market);
    if (myData.length === 0) throw new Error(`No BUY/HOLD signals found in your tracked ${market} stocks.`);
    completeLastStep();

    addStep(`Sending ${myData.length} candidates to ${getAiProviderLabel()}…`);
    const picksRaw = await askAI(
      [{ role: 'user', content: buildPicksPrompt(myData, market) }],
      SYSTEM_PICKS, 4096, 0,
    );
    const result = await parseJsonArray(picksRaw);
    completeLastStep();
    return result;
  }

  async function generateForecast() {
    if (!aiReady) return;
    setLoading(true);
    setError('');
    setPicks(null);
    setSteps([]);
    setScanCount(0);

    try {
      const result = universe === 'broad_screen' ? await runBroadScreen() : await runMyStocksScreen();
      setPicks(result);
      setGeneratedAt(new Date());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Forecast failed.');
    } finally {
      setLoading(false);
    }
  }

  const displayPicks = useMemo(() => {
    if (!picks) return [];
    let out = [...picks];
    if (filterConf !== 'all') out = out.filter(p => p.confidence === filterConf);
    if (sortBy === 'gain')        out.sort((a, b) => b.predicted_gain_pct - a.predicted_gain_pct);
    if (sortBy === 'confidence')  out.sort((a, b) => ({ high: 3, medium: 2, low: 1 }[b.confidence] ?? 0) - ({ high: 3, medium: 2, low: 1 }[a.confidence] ?? 0));
    return out;
  }, [picks, sortBy, filterConf]);

  const dataReady = !!(signals && rankings && prices);
  const currencyPrefix = market === 'HK' ? 'HK$' : '$';

  return (
    <div className="space-y-4">

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 800, color: '#f1f5f9', margin: 0 }}>
            AI Swing Forecast — 10 Days
          </h1>
          <div style={{ fontSize: '12px', color: '#475569', marginTop: '3px' }}>
            Screen US or HK stocks with real technical data · AI picks top swing setups with entry, stop and target
          </div>
        </div>
        {generatedAt && (
          <div style={{ fontSize: '11px', color: '#334155', textAlign: 'right' }}>
            {generatedAt.toLocaleTimeString()}{scanCount > 0 && ` · ${scanCount} stocks scanned`}
          </div>
        )}
      </div>

      {/* No AI configured */}
      {!aiReady && (
        <div style={{ padding: '32px', borderRadius: '12px', border: '1px solid rgba(167,139,250,0.3)', background: 'rgba(167,139,250,0.05)', textAlign: 'center' }}>
          <div style={{ fontSize: '28px', marginBottom: '10px' }}>🤖</div>
          <div style={{ fontSize: '15px', fontWeight: 700, color: '#c4b5fd', marginBottom: '8px' }}>AI Provider Not Configured</div>
          <div style={{ fontSize: '13px', color: '#94a3b8', marginBottom: '16px' }}>
            Configure Claude or DeepSeek in Settings → AI Assistant.
          </div>
          <Link href="/settings" style={{ display: 'inline-block', padding: '9px 22px', borderRadius: '8px', background: 'rgba(167,139,250,0.2)', border: '1px solid rgba(167,139,250,0.4)', color: '#a78bfa', fontWeight: 700, fontSize: '13px', textDecoration: 'none' }}>
            ⚙ Open Settings
          </Link>
        </div>
      )}

      {/* Controls */}
      {aiReady && (
        <div style={{ borderRadius: '12px', padding: '20px', border: '1px solid rgba(99,102,241,0.25)', background: 'rgba(15,23,42,0.6)' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'flex-start' }}>

            {/* Market picker */}
            <div>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>Market</div>
              <div style={{ display: 'flex', gap: '6px' }}>
                {([
                  { v: 'US' as Market, label: '🇺🇸 US',        desc: 'NYSE / NASDAQ' },
                  { v: 'HK' as Market, label: '🇭🇰 Hong Kong', desc: 'HKEX · HKD' },
                ]).map(({ v, label, desc }) => (
                  <button
                    key={v}
                    onClick={() => handleMarketChange(v)}
                    style={{
                      padding: '8px 14px', borderRadius: '8px', cursor: 'pointer', textAlign: 'left',
                      background: market === v ? 'rgba(99,102,241,0.2)' : 'rgba(15,23,42,0.6)',
                      border: `1px solid ${market === v ? 'rgba(99,102,241,0.5)' : '#1e293b'}`,
                      transition: 'all 0.15s',
                    }}
                  >
                    <div style={{ fontSize: '13px', fontWeight: 700, color: market === v ? '#818cf8' : '#94a3b8' }}>{label}</div>
                    <div style={{ fontSize: '10px', color: '#475569', marginTop: '1px' }}>{desc}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Universe picker */}
            <div>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>Screen Universe</div>
              <div style={{ display: 'flex', gap: '8px' }}>
                {([
                  { v: 'broad_screen' as Universe, label: '🌐 Broad Screen', desc: 'AI suggests tickers → real scan' },
                  { v: 'my_stocks'    as Universe, label: '📂 My Tracked Stocks', desc: 'From your BUY signals' },
                ] as const).map(({ v, label, desc }) => (
                  <button
                    key={v}
                    onClick={() => setUniverse(v)}
                    style={{
                      padding: '10px 16px', borderRadius: '8px', cursor: 'pointer', textAlign: 'left',
                      background: universe === v ? 'rgba(99,102,241,0.2)' : 'rgba(15,23,42,0.6)',
                      border: `1px solid ${universe === v ? 'rgba(99,102,241,0.5)' : '#1e293b'}`,
                      transition: 'all 0.15s',
                    }}
                  >
                    <div style={{ fontSize: '13px', fontWeight: 700, color: universe === v ? '#818cf8' : '#94a3b8' }}>{label}</div>
                    <div style={{ fontSize: '10px', color: '#475569', marginTop: '2px' }}>{desc}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Price range — only for broad screen */}
            {universe === 'broad_screen' && (
              <div>
                <div style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>Price Range</div>
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {priceRanges.map(r => (
                    <button
                      key={r.label}
                      onClick={() => setPriceRange(r)}
                      style={{
                        padding: '7px 14px', borderRadius: '7px', fontSize: '12px', fontWeight: 600, cursor: 'pointer',
                        background: priceRange.label === r.label ? 'rgba(251,146,60,0.2)' : 'transparent',
                        border: `1px solid ${priceRange.label === r.label ? 'rgba(251,146,60,0.5)' : '#1e293b'}`,
                        color: priceRange.label === r.label ? '#fb923c' : '#64748b',
                      }}
                    >{r.label}</button>
                  ))}
                </div>
                <div style={{ fontSize: '10px', color: '#334155', marginTop: '6px' }}>
                  AI will suggest tickers in this price range then fetch live technical data
                </div>
              </div>
            )}

            {/* Generate button */}
            <div style={{ marginLeft: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '8px' }}>
              <button
                onClick={generateForecast}
                disabled={loading || (universe === 'my_stocks' && !dataReady)}
                style={{
                  padding: '13px 32px', borderRadius: '10px', fontSize: '14px', fontWeight: 800, cursor: loading ? 'not-allowed' : 'pointer',
                  background: loading ? '#1e293b' : 'linear-gradient(135deg,#4f46e5,#818cf8)',
                  border: 'none', color: loading ? '#475569' : '#fff', transition: 'all 0.2s',
                  whiteSpace: 'nowrap',
                }}
              >
                {loading ? '⟳ Running…' : picks ? '↻ Regenerate' : '⚡ Generate Forecast'}
              </button>
              <div style={{ fontSize: '10px', color: '#334155' }}>
                {universe === 'broad_screen' ? 'Takes ~30s · uses 2 AI calls' : 'Takes ~15s · uses 1 AI call'}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Progress steps */}
      {(loading || steps.length > 0) && steps.length > 0 && (
        <div style={{ borderRadius: '10px', padding: '16px 20px', border: '1px solid rgba(99,102,241,0.2)', background: 'rgba(99,102,241,0.04)' }}>
          {steps.map((s, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '5px 0', opacity: s.done ? 0.5 : 1 }}>
              <span style={{ fontSize: '14px' }}>
                {s.done ? '✓' : i === steps.length - 1 && loading ? '⟳' : '○'}
              </span>
              <span style={{ fontSize: '13px', color: s.done ? '#475569' : '#818cf8' }}>{s.text}</span>
            </div>
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{ padding: '14px 18px', borderRadius: '10px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)', color: '#f87171', fontSize: '13px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          {error}
          <button onClick={generateForecast} style={{ background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.3)', color: '#f87171', borderRadius: '6px', padding: '4px 12px', fontSize: '12px', cursor: 'pointer' }}>
            Retry
          </button>
        </div>
      )}

      {/* Results */}
      {displayPicks.length > 0 && (
        <>
          {/* Filter / sort bar */}
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '11px', color: '#475569' }}>Confidence:</span>
            {(['all', 'high', 'medium'] as const).map(v => (
              <button key={v} onClick={() => setFilterConf(v)} style={{
                padding: '5px 12px', borderRadius: '6px', fontSize: '11px', fontWeight: 600, cursor: 'pointer',
                background: filterConf === v ? (v === 'high' ? 'rgba(34,197,94,0.15)' : v === 'medium' ? 'rgba(250,204,21,0.15)' : 'rgba(99,102,241,0.15)') : 'transparent',
                border: `1px solid ${filterConf === v ? (v === 'high' ? 'rgba(34,197,94,0.4)' : v === 'medium' ? 'rgba(250,204,21,0.4)' : 'rgba(99,102,241,0.4)') : '#1e293b'}`,
                color: filterConf === v ? (v === 'high' ? '#4ade80' : v === 'medium' ? '#facc15' : '#818cf8') : '#64748b',
              }}>{v === 'all' ? 'All' : v.charAt(0).toUpperCase() + v.slice(1)}</button>
            ))}
            <div style={{ marginLeft: 'auto', display: 'flex', gap: '6px', alignItems: 'center' }}>
              <span style={{ fontSize: '11px', color: '#475569' }}>Sort:</span>
              {([['rank', '#'], ['gain', '% Gain'], ['confidence', 'Confidence']] as const).map(([v, label]) => (
                <button key={v} onClick={() => setSortBy(v)} style={{
                  padding: '5px 10px', borderRadius: '6px', fontSize: '11px', cursor: 'pointer',
                  background: sortBy === v ? 'rgba(99,102,241,0.2)' : 'transparent',
                  border: `1px solid ${sortBy === v ? 'rgba(99,102,241,0.4)' : '#1e293b'}`,
                  color: sortBy === v ? '#818cf8' : '#475569',
                }}>{label}</button>
              ))}
            </div>
          </div>

          {/* Cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(460px,1fr))', gap: '14px' }}>
            {displayPicks.map((pick, idx) => {
              const conf = CONF_STYLE[pick.confidence] ?? CONF_STYLE.low;
              const lp   = priceMap[pick.symbol];
              const rank = pick.rank ?? idx + 1;
              const gainColor = pick.predicted_gain_pct >= 8 ? '#4ade80' : pick.predicted_gain_pct >= 4 ? '#facc15' : '#fb923c';
              const rr = pick.take_profit > pick.entry_low && pick.entry_low > pick.stop_loss
                ? ((pick.take_profit - pick.entry_high) / (pick.entry_low - pick.stop_loss)).toFixed(1)
                : null;
              return (
                <div key={pick.symbol} style={{ borderRadius: '12px', border: `1px solid ${conf.border}`, background: 'rgba(10,15,30,0.8)', overflow: 'hidden' }}>
                  {/* Accent bar */}
                  <div style={{ height: '3px', background: conf.color }} />

                  {/* Header row */}
                  <div style={{ padding: '14px 16px 10px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <div style={{ fontSize: '15px', fontWeight: 800, color: '#334155', minWidth: '24px' }}>#{rank}</div>
                      <div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <Link href={`/stock/${pick.symbol}`} style={{ fontSize: '19px', fontWeight: 900, color: '#818cf8', textDecoration: 'none' }}>
                            {pick.symbol}
                          </Link>
                          <span style={{ fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '5px', background: conf.bg, border: `1px solid ${conf.border}`, color: conf.color }}>
                            {pick.confidence.toUpperCase()}
                          </span>
                          {rr && (
                            <span style={{ fontSize: '10px', color: '#64748b' }}>R:R {rr}:1</span>
                          )}
                        </div>
                        {lp ? (
                          <div style={{ fontSize: '12px', color: '#64748b', marginTop: '2px' }}>
                            {lp.currency} {lp.price.toFixed(2)}
                            {lp.change_pct != null && (
                              <span style={{ marginLeft: '6px', color: lp.change_pct >= 0 ? '#4ade80' : '#f87171' }}>
                                {lp.change_pct >= 0 ? '+' : ''}{lp.change_pct.toFixed(2)}% today
                              </span>
                            )}
                          </div>
                        ) : (
                          <div style={{ fontSize: '12px', color: '#334155', marginTop: '2px' }}>
                            {currencyPrefix}{pick.entry_low.toFixed(2)} – {currencyPrefix}{pick.entry_high.toFixed(2)} entry zone
                          </div>
                        )}
                      </div>
                    </div>
                    <div style={{ textAlign: 'right', flexShrink: 0 }}>
                      <div style={{ fontSize: '24px', fontWeight: 900, color: gainColor, fontFamily: 'monospace', lineHeight: 1 }}>
                        +{pick.predicted_gain_pct.toFixed(1)}%
                      </div>
                      <div style={{ fontSize: '10px', color: '#475569', textTransform: 'uppercase', marginTop: '2px' }}>
                        {pick.horizon_days}d forecast
                      </div>
                    </div>
                  </div>

                  {/* Setup badge */}
                  <div style={{ margin: '0 16px 10px', padding: '5px 10px', borderRadius: '6px', background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.2)', fontSize: '11px', color: '#818cf8', fontStyle: 'italic' }}>
                    📐 {pick.setup}
                  </div>

                  {/* 3-column price levels */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '1px', margin: '0 16px 12px', borderRadius: '8px', overflow: 'hidden', border: '1px solid #1e293b' }}>
                    {[
                      { label: 'Entry Zone', value: `${currencyPrefix}${pick.entry_low.toFixed(2)}–${pick.entry_high.toFixed(2)}`, color: '#818cf8', bg: 'rgba(99,102,241,0.08)' },
                      { label: 'Stop Loss',  value: `${currencyPrefix}${pick.stop_loss.toFixed(2)}`,   color: '#f87171', bg: 'rgba(239,68,68,0.07)'   },
                      { label: 'Target',     value: `${currencyPrefix}${pick.take_profit.toFixed(2)}`, color: '#4ade80', bg: 'rgba(34,197,94,0.07)'   },
                    ].map(cell => (
                      <div key={cell.label} style={{ padding: '8px 10px', background: cell.bg, textAlign: 'center' }}>
                        <div style={{ fontSize: '10px', color: '#475569', textTransform: 'uppercase', marginBottom: '3px' }}>{cell.label}</div>
                        <div style={{ fontSize: '13px', fontWeight: 800, color: cell.color, fontFamily: 'monospace' }}>{cell.value}</div>
                      </div>
                    ))}
                  </div>

                  {/* Catalyst / Risk / Rationale */}
                  <div style={{ padding: '0 16px 14px', display: 'flex', flexDirection: 'column', gap: '5px' }}>
                    <div style={{ display: 'flex', gap: '6px', alignItems: 'flex-start' }}>
                      <span style={{ fontSize: '11px', color: '#4ade80', fontWeight: 700, flexShrink: 0 }}>▲</span>
                      <span style={{ fontSize: '11px', color: '#94a3b8', lineHeight: 1.4 }}>{pick.catalyst}</span>
                    </div>
                    <div style={{ display: 'flex', gap: '6px', alignItems: 'flex-start' }}>
                      <span style={{ fontSize: '11px', color: '#f87171', fontWeight: 700, flexShrink: 0 }}>⚠</span>
                      <span style={{ fontSize: '11px', color: '#64748b', lineHeight: 1.4 }}>{pick.risk}</span>
                    </div>
                    <div style={{ fontSize: '11px', color: '#475569', lineHeight: 1.5, marginTop: '4px', paddingTop: '8px', borderTop: '1px solid #1e293b' }}>
                      {pick.rationale}
                    </div>
                    <div style={{ marginTop: '6px', display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
                      <Link href={`/stock/${pick.symbol}`} style={{ fontSize: '11px', color: '#6366f1', textDecoration: 'none', background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.2)', padding: '4px 10px', borderRadius: '5px' }}>
                        View chart & AI chat →
                      </Link>
                      <button
                        onClick={() => savePickToBoard(pick)}
                        disabled={savingToBoard === pick.symbol || savedToBoard.has(pick.symbol)}
                        style={{ fontSize: '11px', cursor: savedToBoard.has(pick.symbol) ? 'default' : 'pointer', background: savedToBoard.has(pick.symbol) ? 'rgba(34,197,94,0.1)' : 'rgba(129,140,248,0.1)', border: `1px solid ${savedToBoard.has(pick.symbol) ? 'rgba(34,197,94,0.3)' : 'rgba(129,140,248,0.25)'}`, color: savedToBoard.has(pick.symbol) ? '#4ade80' : '#818cf8', padding: '4px 10px', borderRadius: '5px', opacity: savingToBoard === pick.symbol ? 0.5 : 1 }}
                      >
                        {savedToBoard.has(pick.symbol) ? '✓ Saved' : savingToBoard === pick.symbol ? '…' : '📌 Save to Board'}
                      </button>
                      <WatchlistPickerButton symbol={pick.symbol} size="sm" />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div style={{ padding: '10px 16px', borderRadius: '8px', background: 'rgba(30,41,59,0.4)', border: '1px solid #1e293b', fontSize: '11px', color: '#334155' }}>
            AI-generated analysis for informational purposes only. Not financial advice. Always do your own due diligence before trading.
          </div>
        </>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
