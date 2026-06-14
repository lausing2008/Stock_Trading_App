import { useState, useMemo, useRef } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api, type RankingRow, type SignalSummary, type LatestPrice, type WatchlistItem } from '@/lib/api';
import WatchlistPickerButton from '@/components/WatchlistPickerButton';
import { getSession } from '@/lib/auth';
import { getSignalStyle, loadSettings } from '@/lib/settings';

// ─── Merged row type ──────────────────────────────────────────────────────────

type Row = RankingRow & {
  signal?: 'BUY' | 'SELL' | 'HOLD' | 'WAIT';
  confidence?: number;
  bullish_probability?: number;
  price?: number;
  change_pct?: number;
  inWatchlist: boolean;
};

type SortKey = 'symbol' | 'score' | 'technical' | 'momentum' | 'value' | 'growth'
             | 'bullish_probability' | 'change_pct' | 'price' | 'confidence' | 'relative_strength';

// ─── Constants ────────────────────────────────────────────────────────────────

const SIGNAL_COLOR: Record<string, string> = {
  BUY: '#22c55e', HOLD: '#facc15', WAIT: '#f97316', SELL: '#ef4444',
};
const SIGNAL_BG: Record<string, string> = {
  BUY: 'rgba(34,197,94,0.12)', HOLD: 'rgba(250,204,21,0.12)',
  WAIT: 'rgba(249,115,22,0.12)', SELL: 'rgba(239,68,68,0.12)',
};
const ALL_SIGNALS = ['BUY', 'HOLD', 'WAIT', 'SELL'] as const;

const DEFAULT_FILTERS = {
  market: 'All' as 'All' | 'US' | 'HK',
  signals: new Set<string>(),        // empty = show all
  minScore: '',
  minTechnical: '',
  minMomentum: '',
  minValue: '',
  minGrowth: '',
  minBullish: '',
  minChange: '',
  maxChange: '',
  minPrice: '',
  maxPrice: '',
  sector: '',
  minFairDiscount: '',   // min % stock trades BELOW fair value (positive = undervalued)
  watchlistOnly: true,
  search: '',
};

// ─── Sub-score bar ────────────────────────────────────────────────────────────

function ScoreBar({ value, color = '#6366f1' }: { value: number | null | undefined; color?: string }) {
  if (value == null) return <span style={{ color: '#334155', fontSize: '11px' }}>—</span>;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
      <div style={{ width: '36px', height: '4px', borderRadius: '2px', background: '#1e293b', flexShrink: 0 }}>
        <div style={{ width: `${Math.min(100, value)}%`, height: '100%', borderRadius: '2px', background: color }} />
      </div>
      <span style={{ fontSize: '11px', fontVariantNumeric: 'tabular-nums', color: '#94a3b8', minWidth: '22px' }}>
        {value.toFixed(0)}
      </span>
    </div>
  );
}

// ─── Sortable column header ───────────────────────────────────────────────────

function Th({ label, col, sort, onSort }: {
  label: string; col: SortKey;
  sort: { key: SortKey; dir: 'asc' | 'desc' };
  onSort: (k: SortKey) => void;
}) {
  const active = sort.key === col;
  return (
    <th
      onClick={() => onSort(col)}
      style={{
        padding: '8px 10px', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase',
        letterSpacing: '0.06em', color: active ? '#a78bfa' : '#475569',
        cursor: 'pointer', whiteSpace: 'nowrap', userSelect: 'none',
        borderBottom: '1px solid #1e293b', background: '#080f1e',
      }}
    >
      {label}{active ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}
    </th>
  );
}

// ─── Number filter input ──────────────────────────────────────────────────────

function NumInput({ label, value, onChange, placeholder }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
      <span style={{ fontSize: '9px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
      <input
        type="number"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder ?? '0'}
        style={{
          width: '60px', padding: '4px 6px', borderRadius: '5px',
          border: '1px solid #1e293b', background: '#0b1420', color: '#e2e8f0',
          fontSize: '12px', outline: 'none',
        }}
      />
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function Screener() {
  const router = useRouter();
  const isAdmin = getSession()?.role === 'admin';

  const { data: rankData } = useSWR('rankings-all', () => api.rankings());
  const { data: signals }  = useSWR('signals-' + getSignalStyle(),  () => api.allSignals(getSignalStyle()));
  const { data: prices }   = useSWR('latest-prices', () => api.latestPrices(), { refreshInterval: 60_000 });
  const { data: wlItems }  = useSWR('watchlist', () => api.listWatchlist());

  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'score', dir: 'desc' });

  // AI natural language screener
  const [nlQuery, setNlQuery] = useState('');
  const [nlLoading, setNlLoading] = useState(false);
  const [nlError, setNlError] = useState('');
  const [nlExplain, setNlExplain] = useState('');
  const nlInputRef = useRef<HTMLInputElement>(null);
  const settings = loadSettings();
  const aiProvider = settings?.aiProvider === 'deepseek' ? 'deepseek' : 'claude';
  const aiKey = aiProvider === 'deepseek' ? (settings?.deepseekApiKey ?? '') : (settings?.claudeApiKey ?? '');
  const aiModel = aiProvider === 'deepseek' ? (settings?.deepseekModel ?? 'deepseek-chat') : (settings?.claudeModel ?? 'claude-sonnet-4-6');

  async function runNlScreener() {
    if (!nlQuery.trim()) return;
    setNlLoading(true); setNlError(''); setNlExplain('');
    const systemPrompt = `You are a stock screener assistant. The user will describe what stocks they want in plain English.
Your job: translate it into a JSON filter object with ONLY these fields (all optional):
{
  "market": "US" | "HK" | "All",
  "signals": ["BUY"] | ["BUY","HOLD"] | [] (empty = all),
  "minScore": number (0-100, K-Score),
  "minTechnical": number (0-100),
  "minMomentum": number (0-100),
  "minValue": number (0-100),
  "minGrowth": number (0-100),
  "minBullish": number (0-100, bullish probability %),
  "minChange": number (day change % min),
  "maxChange": number (day change % max),
  "minPrice": number,
  "maxPrice": number,
  "minFairDiscount": number (% below fair value, 0-100),
  "watchlistOnly": boolean,
  "explanation": "one sentence — what you set and why"
}
Respond with ONLY valid JSON — no markdown, no extra text. Set only fields relevant to the query.`;
    try {
      const resp = await api.aiChat(
        [{ role: 'user', content: nlQuery }],
        systemPrompt, aiProvider, aiKey, aiModel,
      );
      let parsed: Record<string, unknown>;
      try { parsed = JSON.parse(resp.content.trim()); }
      catch { throw new Error('AI returned invalid JSON — try rephrasing your query'); }

      const next = { ...DEFAULT_FILTERS, signals: new Set<string>() };
      if (parsed.market) next.market = parsed.market as 'All' | 'US' | 'HK';
      if (Array.isArray(parsed.signals)) next.signals = new Set(parsed.signals as string[]);
      if (parsed.minScore != null) next.minScore = String(parsed.minScore);
      if (parsed.minTechnical != null) next.minTechnical = String(parsed.minTechnical);
      if (parsed.minMomentum != null) next.minMomentum = String(parsed.minMomentum);
      if (parsed.minValue != null) next.minValue = String(parsed.minValue);
      if (parsed.minGrowth != null) next.minGrowth = String(parsed.minGrowth);
      if (parsed.minBullish != null) next.minBullish = String(parsed.minBullish);
      if (parsed.minChange != null) next.minChange = String(parsed.minChange);
      if (parsed.maxChange != null) next.maxChange = String(parsed.maxChange);
      if (parsed.minPrice != null) next.minPrice = String(parsed.minPrice);
      if (parsed.maxPrice != null) next.maxPrice = String(parsed.maxPrice);
      if (parsed.minFairDiscount != null) next.minFairDiscount = String(parsed.minFairDiscount);
      if (parsed.watchlistOnly != null) next.watchlistOnly = Boolean(parsed.watchlistOnly);
      if (typeof parsed.explanation === 'string') setNlExplain(parsed.explanation);
      setFilters(next);
    } catch (e: unknown) {
      setNlError(e instanceof Error ? e.message : 'AI screener failed');
    } finally {
      setNlLoading(false);
    }
  }

  const wlSymbols = useMemo(
    () => new Set((wlItems ?? []).map(w => w.symbol)),
    [wlItems],
  );

  const signalMap = useMemo(() => {
    const m: Record<string, SignalSummary> = {};
    for (const s of signals ?? []) m[s.symbol] = s;
    return m;
  }, [signals]);

  const priceMap = useMemo(() => {
    const m: Record<string, LatestPrice> = {};
    for (const p of prices ?? []) m[p.symbol] = p;
    return m;
  }, [prices]);

  // Merge all data
  const rows: Row[] = useMemo(() => {
    return (rankData?.rankings ?? []).map(r => {
      const sig = signalMap[r.symbol];
      const prc = priceMap[r.symbol];
      return {
        ...r,
        signal:             sig?.signal,
        confidence:         sig?.confidence,
        bullish_probability: sig?.bullish_probability ?? undefined,
        price:              prc?.price,
        change_pct:         prc?.change_pct ?? undefined,
        inWatchlist:        wlSymbols.has(r.symbol),
      };
    });
  }, [rankData, signalMap, priceMap, wlSymbols]);

  // Derive unique sectors from rows for the sector dropdown
  const sectors = useMemo(() => {
    const s = new Set<string>();
    for (const r of rows) if (r.sector) s.add(r.sector);
    return ['', ...Array.from(s).sort()];
  }, [rows]);

  // Apply filters
  const filtered = useMemo(() => {
    const search = filters.search.toLowerCase();
    const minScore    = filters.minScore    ? +filters.minScore    : null;
    const minTech     = filters.minTechnical ? +filters.minTechnical : null;
    const minMom      = filters.minMomentum  ? +filters.minMomentum  : null;
    const minVal      = filters.minValue     ? +filters.minValue     : null;
    const minGrow     = filters.minGrowth    ? +filters.minGrowth    : null;
    const minBullish  = filters.minBullish   ? +filters.minBullish / 100 : null;
    const minChg      = filters.minChange    ? +filters.minChange    : null;
    const maxChg      = filters.maxChange    ? +filters.maxChange    : null;
    const minPrc      = filters.minPrice     ? +filters.minPrice     : null;
    const maxPrc      = filters.maxPrice     ? +filters.maxPrice     : null;
    const minDisc     = filters.minFairDiscount ? +filters.minFairDiscount / 100 : null;

    return rows.filter(r => {
      if (filters.market !== 'All' && r.market !== filters.market) return false;
      if (filters.signals.size > 0 && (!r.signal || !filters.signals.has(r.signal))) return false;
      if ((!isAdmin || filters.watchlistOnly) && !r.inWatchlist) return false;
      if (search && !r.symbol.toLowerCase().includes(search) && !r.name.toLowerCase().includes(search)) return false;
      if (filters.sector && r.sector !== filters.sector) return false;
      if (minScore   != null && (r.score   ?? 0) < minScore)  return false;
      if (minTech    != null && (r.technical ?? 0) < minTech)  return false;
      if (minMom     != null && (r.momentum  ?? 0) < minMom)   return false;
      if (minVal     != null && (r.value     ?? 0) < minVal)   return false;
      if (minGrow    != null && (r.growth    ?? 0) < minGrow)  return false;
      if (minBullish != null && (r.bullish_probability ?? 0) < minBullish) return false;
      if (minChg     != null && (r.change_pct ?? 0) < minChg) return false;
      if (maxChg     != null && (r.change_pct ?? 0) > maxChg) return false;
      if (minPrc     != null && (r.price ?? 0) < minPrc) return false;
      if (maxPrc     != null && (r.price ?? 0) > maxPrc) return false;
      if (minDisc != null) {
        if (r.fair_price == null || r.price == null) return false;
        const discount = (r.fair_price - r.price) / r.price;
        if (discount < minDisc) return false;
      }
      return true;
    });
  }, [rows, filters]);

  // Sort
  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      let av: number | string = 0, bv: number | string = 0;
      if (sort.key === 'symbol') { av = a.symbol; bv = b.symbol; }
      else if (sort.key === 'score')              { av = a.score ?? -1;               bv = b.score ?? -1; }
      else if (sort.key === 'technical')          { av = a.technical ?? -1;           bv = b.technical ?? -1; }
      else if (sort.key === 'momentum')           { av = a.momentum ?? -1;            bv = b.momentum ?? -1; }
      else if (sort.key === 'value')              { av = a.value ?? -1;               bv = b.value ?? -1; }
      else if (sort.key === 'growth')             { av = a.growth ?? -1;              bv = b.growth ?? -1; }
      else if (sort.key === 'bullish_probability'){ av = a.bullish_probability ?? -1; bv = b.bullish_probability ?? -1; }
      else if (sort.key === 'change_pct')         { av = a.change_pct ?? -999;        bv = b.change_pct ?? -999; }
      else if (sort.key === 'price')              { av = a.price ?? -1;               bv = b.price ?? -1; }
      else if (sort.key === 'confidence')         { av = a.confidence ?? -1;          bv = b.confidence ?? -1; }
      else if (sort.key === 'relative_strength')  { av = a.relative_strength ?? -1;   bv = b.relative_strength ?? -1; }

      if (typeof av === 'string') return sort.dir === 'asc' ? av.localeCompare(bv as string) : (bv as string).localeCompare(av);
      return sort.dir === 'asc' ? av - (bv as number) : (bv as number) - av;
    });
  }, [filtered, sort]);

  function toggleSort(key: SortKey) {
    setSort(s => s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' });
  }

  function toggleSignal(sig: string) {
    setFilters(f => {
      const next = new Set(f.signals);
      next.has(sig) ? next.delete(sig) : next.add(sig);
      return { ...f, signals: next };
    });
  }

  function resetFilters() {
    setFilters({ ...DEFAULT_FILTERS, signals: new Set() });
  }

  const isDefaultFilters = (
    filters.market === 'All' && filters.signals.size === 0 && !filters.minScore && !filters.minTechnical &&
    !filters.minMomentum && !filters.minValue && !filters.minGrowth && !filters.minBullish &&
    !filters.minChange && !filters.maxChange && !filters.minPrice && !filters.maxPrice &&
    !filters.sector && !filters.minFairDiscount && !filters.watchlistOnly && !filters.search
  );

  const loading = !rankData || !signals || !prices;

  return (
    <div style={{ maxWidth: '1400px', margin: '0 auto', padding: '20px 16px' }}>
      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px', flexWrap: 'wrap', gap: '8px' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 700, color: '#e2e8f0' }}>Stock Screener</h1>
          <p style={{ margin: '2px 0 0', fontSize: '12px', color: '#475569' }}>
            Filter across all {rows.length} tracked stocks · {sorted.length} match
          </p>
        </div>
        {!isDefaultFilters && (
          <button
            onClick={resetFilters}
            style={{ padding: '5px 12px', borderRadius: '6px', border: '1px solid #334155', background: 'transparent', color: '#94a3b8', fontSize: '12px', cursor: 'pointer' }}
          >
            Reset filters
          </button>
        )}
      </div>

      {/* AI Natural Language Screener */}
      <div style={{ background: '#080f1e', border: '1px solid #312e81', borderRadius: '10px', padding: '12px 16px', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: nlExplain || nlError ? '8px' : '0' }}>
          <span style={{ fontSize: '11px', color: '#818cf8', fontWeight: 700, whiteSpace: 'nowrap' }}>✦ AI Screen</span>
          <input
            ref={nlInputRef}
            value={nlQuery}
            onChange={e => setNlQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && runNlScreener()}
            placeholder='Describe what you want, e.g. "tech stocks with BUY signal and high momentum"'
            style={{ flex: 1, padding: '6px 10px', borderRadius: '6px', border: '1px solid #312e81', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }}
          />
          <button
            onClick={runNlScreener}
            disabled={nlLoading || !nlQuery.trim()}
            style={{ padding: '6px 14px', borderRadius: '6px', border: '1px solid #4f46e5', background: nlLoading ? 'transparent' : 'rgba(79,70,229,0.2)', color: '#a78bfa', fontSize: '12px', fontWeight: 600, cursor: nlLoading ? 'not-allowed' : 'pointer', whiteSpace: 'nowrap', opacity: (!nlQuery.trim() || nlLoading) ? 0.5 : 1 }}
          >
            {nlLoading ? 'Thinking…' : 'Screen'}
          </button>
        </div>
        {nlExplain && (
          <div style={{ fontSize: '11px', color: '#64748b', paddingLeft: '2px' }}>↳ {nlExplain}</div>
        )}
        {nlError && (
          <div style={{ fontSize: '11px', color: '#f87171', paddingLeft: '2px' }}>⚠ {nlError}</div>
        )}
      </div>

      {/* Filter panel */}
      <div style={{ background: '#0b1420', border: '1px solid #1e293b', borderRadius: '10px', padding: '14px 16px', marginBottom: '16px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', alignItems: 'flex-end' }}>

          {/* Search */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span style={{ fontSize: '9px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Search</span>
            <input
              type="text"
              value={filters.search}
              onChange={e => setFilters(f => ({ ...f, search: e.target.value }))}
              placeholder="Symbol or name…"
              style={{
                width: '140px', padding: '4px 8px', borderRadius: '5px',
                border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0',
                fontSize: '12px', outline: 'none',
              }}
            />
          </div>

          {/* Market */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span style={{ fontSize: '9px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Market</span>
            <div style={{ display: 'flex', gap: '4px' }}>
              {(['All', 'US', 'HK'] as const).map(m => (
                <button key={m} onClick={() => setFilters(f => ({ ...f, market: m }))}
                  style={{
                    padding: '3px 10px', borderRadius: '5px', fontSize: '11px', fontWeight: 600, cursor: 'pointer',
                    border: '1px solid',
                    borderColor: filters.market === m ? '#6366f1' : '#1e293b',
                    background: filters.market === m ? 'rgba(99,102,241,0.15)' : 'transparent',
                    color: filters.market === m ? '#a78bfa' : '#64748b',
                  }}
                >{m}</button>
              ))}
            </div>
          </div>

          {/* AI Signal */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span style={{ fontSize: '9px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em' }}>AI Signal</span>
            <div style={{ display: 'flex', gap: '4px' }}>
              {ALL_SIGNALS.map(sig => {
                const active = filters.signals.has(sig);
                return (
                  <button key={sig} onClick={() => toggleSignal(sig)}
                    style={{
                      padding: '3px 8px', borderRadius: '5px', fontSize: '11px', fontWeight: 700, cursor: 'pointer',
                      border: `1px solid ${active ? SIGNAL_COLOR[sig] : '#1e293b'}`,
                      background: active ? `${SIGNAL_COLOR[sig]}22` : 'transparent',
                      color: active ? SIGNAL_COLOR[sig] : '#475569',
                    }}
                  >{sig}</button>
                );
              })}
            </div>
          </div>

          {/* Score filters */}
          <NumInput label="Min K-Score"   value={filters.minScore}     onChange={v => setFilters(f => ({ ...f, minScore: v }))}    placeholder="e.g. 50" />
          <NumInput label="Min Technical" value={filters.minTechnical} onChange={v => setFilters(f => ({ ...f, minTechnical: v }))} placeholder="e.g. 40" />
          <NumInput label="Min Momentum"  value={filters.minMomentum}  onChange={v => setFilters(f => ({ ...f, minMomentum: v }))}  placeholder="e.g. 40" />
          <NumInput label="Min Value"     value={filters.minValue}     onChange={v => setFilters(f => ({ ...f, minValue: v }))}     placeholder="e.g. 40" />
          <NumInput label="Min Growth"    value={filters.minGrowth}    onChange={v => setFilters(f => ({ ...f, minGrowth: v }))}    placeholder="e.g. 40" />
          <NumInput label="Min Bullish %" value={filters.minBullish}   onChange={v => setFilters(f => ({ ...f, minBullish: v }))}   placeholder="e.g. 60" />

          {/* Day change range */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span style={{ fontSize: '9px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Day Chg %</span>
            <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
              <input type="number" value={filters.minChange} onChange={e => setFilters(f => ({ ...f, minChange: e.target.value }))}
                placeholder="Min" style={{ width: '52px', padding: '4px 6px', borderRadius: '5px', border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }} />
              <span style={{ color: '#334155', fontSize: '11px' }}>to</span>
              <input type="number" value={filters.maxChange} onChange={e => setFilters(f => ({ ...f, maxChange: e.target.value }))}
                placeholder="Max" style={{ width: '52px', padding: '4px 6px', borderRadius: '5px', border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }} />
            </div>
          </div>

          {/* Price range */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span style={{ fontSize: '9px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Price</span>
            <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
              <input type="number" value={filters.minPrice} onChange={e => setFilters(f => ({ ...f, minPrice: e.target.value }))}
                placeholder="Min" style={{ width: '56px', padding: '4px 6px', borderRadius: '5px', border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }} />
              <span style={{ color: '#334155', fontSize: '11px' }}>to</span>
              <input type="number" value={filters.maxPrice} onChange={e => setFilters(f => ({ ...f, maxPrice: e.target.value }))}
                placeholder="Max" style={{ width: '56px', padding: '4px 6px', borderRadius: '5px', border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }} />
            </div>
          </div>

          {/* Sector */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span style={{ fontSize: '9px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Sector</span>
            <select
              value={filters.sector}
              onChange={e => setFilters(f => ({ ...f, sector: e.target.value }))}
              style={{ padding: '4px 8px', borderRadius: '5px', border: '1px solid #1e293b', background: '#0f172a', color: filters.sector ? '#e2e8f0' : '#64748b', fontSize: '12px', outline: 'none', maxWidth: '140px' }}
            >
              {sectors.map(s => <option key={s} value={s}>{s || 'All Sectors'}</option>)}
            </select>
          </div>

          {/* Fair-value discount */}
          <NumInput label="Min Underval %" value={filters.minFairDiscount} onChange={v => setFilters(f => ({ ...f, minFairDiscount: v }))} placeholder="e.g. 10" />

          {/* Watchlist toggle — admin only */}
          {isAdmin && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Filter</span>
              <button
                onClick={() => setFilters(f => ({ ...f, watchlistOnly: !f.watchlistOnly }))}
                style={{
                  padding: '4px 10px', borderRadius: '5px', fontSize: '11px', fontWeight: 600, cursor: 'pointer',
                  border: `1px solid ${filters.watchlistOnly ? '#6366f1' : '#1e293b'}`,
                  background: filters.watchlistOnly ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: filters.watchlistOnly ? '#a78bfa' : '#64748b',
                }}
              >
                {filters.watchlistOnly ? '★ Watchlist' : '☆ All Stocks'}
              </button>
            </div>
          )}

        </div>
      </div>

      {/* Results table */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: '48px', color: '#475569' }}>Loading…</div>
      ) : sorted.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '48px', color: '#475569' }}>
          No stocks match the current filters.{' '}
          <button onClick={resetFilters} style={{ color: '#818cf8', background: 'none', border: 'none', cursor: 'pointer', fontSize: '13px' }}>Reset filters</button>
        </div>
      ) : (
        <div style={{ borderRadius: '10px', border: '1px solid #1e293b', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr>
                  <Th label="Symbol"     col="symbol"              sort={sort} onSort={toggleSort} />
                  <th style={{ padding: '8px 10px', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#475569', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Market</th>
                  <th style={{ padding: '8px 10px', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#475569', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Signal</th>
                  <Th label="K-Score"    col="score"               sort={sort} onSort={toggleSort} />
                  <Th label="Technical"  col="technical"           sort={sort} onSort={toggleSort} />
                  <Th label="Momentum"   col="momentum"            sort={sort} onSort={toggleSort} />
                  <Th label="Value"      col="value"               sort={sort} onSort={toggleSort} />
                  <Th label="Growth"     col="growth"              sort={sort} onSort={toggleSort} />
                  <Th label="RS"         col="relative_strength"   sort={sort} onSort={toggleSort} />
                  <Th label="Bullish %"  col="bullish_probability" sort={sort} onSort={toggleSort} />
                  <Th label="Confidence" col="confidence"          sort={sort} onSort={toggleSort} />
                  <Th label="Day Chg"    col="change_pct"          sort={sort} onSort={toggleSort} />
                  <Th label="Price"      col="price"               sort={sort} onSort={toggleSort} />
                  <th style={{ padding: '8px 10px', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#475569', borderBottom: '1px solid #1e293b', background: '#080f1e' }} />
                </tr>
              </thead>
              <tbody>
                {sorted.map((row, i) => {
                  const chgColor = (row.change_pct ?? 0) >= 0 ? '#22c55e' : '#ef4444';
                  return (
                    <tr
                      key={row.symbol}
                      onClick={() => router.push(`/stock/${row.symbol}`)}
                      style={{
                        cursor: 'pointer',
                        background: i % 2 === 0 ? '#080f1e' : '#09101f',
                        borderBottom: '1px solid rgba(30,41,59,0.5)',
                        transition: 'background 0.1s',
                      }}
                      onMouseEnter={e => (e.currentTarget.style.background = '#0f1e35')}
                      onMouseLeave={e => (e.currentTarget.style.background = i % 2 === 0 ? '#080f1e' : '#09101f')}
                    >
                      {/* Symbol + Name */}
                      <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>
                        <div style={{ fontWeight: 700, color: '#e2e8f0' }}>{row.symbol}</div>
                        <div style={{ fontSize: '10px', color: '#475569', maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {row.name}
                        </div>
                      </td>

                      {/* Market */}
                      <td style={{ padding: '8px 10px' }}>
                        <span style={{ fontSize: '10px', fontWeight: 600, padding: '2px 5px', borderRadius: '3px', background: '#1e293b', color: '#64748b' }}>
                          {row.market}
                        </span>
                      </td>

                      {/* Signal */}
                      <td style={{ padding: '8px 10px' }}>
                        {row.signal ? (
                          <span style={{
                            fontSize: '11px', fontWeight: 700, padding: '2px 7px', borderRadius: '4px',
                            color: SIGNAL_COLOR[row.signal], background: SIGNAL_BG[row.signal],
                            border: `1px solid ${SIGNAL_COLOR[row.signal]}33`,
                          }}>
                            {row.signal}
                          </span>
                        ) : <span style={{ color: '#334155' }}>—</span>}
                      </td>

                      {/* K-Score */}
                      <td style={{ padding: '8px 10px' }}>
                        <ScoreBar value={row.score} color={
                          (row.score ?? 0) >= 70 ? '#22c55e' : (row.score ?? 0) >= 50 ? '#6366f1' : '#f59e0b'
                        } />
                      </td>

                      {/* Technical */}
                      <td style={{ padding: '8px 10px' }}><ScoreBar value={row.technical} color="#38bdf8" /></td>

                      {/* Momentum */}
                      <td style={{ padding: '8px 10px' }}><ScoreBar value={row.momentum} color="#f59e0b" /></td>

                      {/* Value */}
                      <td style={{ padding: '8px 10px' }}><ScoreBar value={row.value} color="#a78bfa" /></td>

                      {/* Growth */}
                      <td style={{ padding: '8px 10px' }}><ScoreBar value={row.growth} color="#34d399" /></td>

                      {/* Relative Strength */}
                      <td style={{ padding: '8px 10px', fontVariantNumeric: 'tabular-nums', fontWeight: 600, fontSize: '12px', color: row.relative_strength == null ? '#334155' : row.relative_strength >= 60 ? '#4ade80' : row.relative_strength >= 45 ? '#64748b' : '#f87171' }}>
                        {row.relative_strength != null ? row.relative_strength.toFixed(0) : '—'}
                      </td>

                      {/* Bullish % */}
                      <td style={{ padding: '8px 10px', fontVariantNumeric: 'tabular-nums' }}>
                        {row.bullish_probability != null
                          ? <span style={{ color: row.bullish_probability >= 0.65 ? '#22c55e' : row.bullish_probability >= 0.50 ? '#facc15' : '#ef4444', fontWeight: 600, fontSize: '12px' }}>
                              {(row.bullish_probability * 100).toFixed(0)}%
                            </span>
                          : <span style={{ color: '#334155' }}>—</span>}
                      </td>

                      {/* Confidence */}
                      <td style={{ padding: '8px 10px', color: '#64748b', fontVariantNumeric: 'tabular-nums', fontSize: '12px' }}>
                        {row.confidence != null ? `${row.confidence.toFixed(0)}%` : '—'}
                      </td>

                      {/* Day change */}
                      <td style={{ padding: '8px 10px', fontVariantNumeric: 'tabular-nums', fontWeight: 600, color: chgColor, fontSize: '12px' }}>
                        {row.change_pct != null ? `${row.change_pct >= 0 ? '+' : ''}${row.change_pct.toFixed(2)}%` : '—'}
                      </td>

                      {/* Price */}
                      <td style={{ padding: '8px 10px', color: '#94a3b8', fontVariantNumeric: 'tabular-nums', fontSize: '12px' }}>
                        {row.price != null ? row.price.toFixed(2) : '—'}
                      </td>

                      {/* Actions */}
                      <td style={{ padding: '8px 10px' }} onClick={e => e.stopPropagation()}>
                        <WatchlistPickerButton symbol={row.symbol} size="xs" />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Footer */}
          <div style={{ padding: '8px 14px', borderTop: '1px solid #1e293b', background: '#080f1e', fontSize: '10px', color: '#334155' }}>
            {sorted.length} stock{sorted.length !== 1 ? 's' : ''} shown · click any row to open stock detail · click column header to sort
          </div>
        </div>
      )}
    </div>
  );
}
