import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type SuppressedSignalRow } from '@/lib/api';

// ── Static config ─────────────────────────────────────────────────────────────

const STYLES = ['SHORT', 'SWING', 'LONG'] as const;
const SIGNAL_OPTS = ['ALL', 'BUY', 'HOLD', 'WAIT', 'SELL'] as const;

type CondKey = keyof SuppressedSignalRow['conditions'];

const CONDITIONS: { key: CondKey; label: string; short: string; color: string; tip: string }[] = [
  { key: 'weekly_gate',          label: 'Weekly Gate',         short: 'Gate',    color: '#ef4444', tip: 'RSI(14w) < 40 AND weekly trend down — hard 0.40× block after cap' },
  { key: 'stale_data',           label: 'Stale Data',          short: 'Stale',   color: '#ef4444', tip: 'Last price bar > 3 days old — signal unreliable (0.60×)' },
  { key: 'insufficient_history', label: 'Insufficient History',short: 'History', color: '#f87171', tip: '< 50 daily bars — indicators unreliable (0.50×)' },
  { key: 'weekly_misalignment',  label: 'Weekly Misalign',     short: 'W.Align', color: '#f97316', tip: 'Daily and weekly momentum directions conflict (0.85× SWING)' },
  { key: 'high_vol_regime',      label: 'High-Vol Regime',     short: 'Hi-Vol',  color: '#f97316', tip: 'Fear & Greed < 30 — market stress (0.85× SWING)' },
  { key: 'earnings_caution',     label: 'Earnings Risk',       short: 'Earn',    color: '#fb923c', tip: 'Earnings within 10 days — binary event risk (0.50–0.90×)' },
  { key: 'negative_news',        label: 'Negative News',       short: 'News',    color: '#fb923c', tip: 'News sentiment < 35/100 (0.75–0.85×)' },
  { key: 'adx_choppy',          label: 'ADX Choppy',          short: 'ADX',     color: '#eab308', tip: 'ADX below minimum — directionless market (0.90× SWING)' },
  { key: 'low_breadth',         label: 'Low Breadth',         short: 'Breadth', color: '#eab308', tip: '< 40% of stocks above 200-day SMA (0.90× SWING)' },
  { key: 'rs_lagging',          label: 'RS Lagging',          short: 'RS',      color: '#eab308', tip: 'Stock lagging sector ETF by > 20% on 20d basis (0.85× SWING)' },
  { key: 'bearish_options',     label: 'Bearish Options',     short: 'Options', color: '#a3a3a3', tip: 'Elevated put volume or bearish C/P ratio (0.92–0.96×)' },
  { key: 'compression_cap',     label: 'Cap Applied',         short: 'Cap',     color: '#818cf8', tip: 'Stacked filters hit the max_compress_ratio floor' },
];

type SortKey =
  | 'symbol' | 'signal' | 'bullish_probability' | 'suppression_count'
  | 'weekly_rsi' | 'rsi' | 'adx' | 'days_to_earnings' | 'news_sentiment' | 'rs_score' | 'breadth_pct';

// Tooltip text for every sortable column header
const COL_TIPS: Record<SortKey, string> = {
  symbol:             'Stock ticker symbol. Click to open the stock detail page.',
  signal:             'Current signal from the latest AI analysis: BUY / HOLD / WAIT / SELL.',
  bullish_probability:'Fused probability score (0–100%) after all filters applied. Above 50% = bullish lean. BUY threshold is 65% (SWING bull regime).',
  suppression_count:  'Number of suppression conditions currently active for this stock. Higher = signal is being held back by more filters.',
  weekly_rsi:         'RSI(14) computed on weekly bars (daily OHLCV resampled to Monday-anchored weeks). Below 40 = weekly bearish momentum. Used by the Weekly Gate.',
  rsi:                'Daily RSI(14). Below 35 = oversold (potential entry zone, green). Above 70 = overbought (yellow). Drives the TA score.',
  adx:                'Average Directional Index (14). Below 20 = choppy/directionless market — ADX filter fires and compresses signal 10% toward neutral.',
  days_to_earnings:   'Trading days until next earnings announcement. ≤2d = strong block (0.50×). ≤5d = caution (0.75×). ≤10d = watch (0.90×). SWING only.',
  news_sentiment:     'Aggregate news sentiment score 0–100 (50 = neutral). Claude Haiku when API key set, otherwise enhanced VADER. Below 25 = strong negative (0.75×). Below 35 = negative (0.85×). SWING only.',
  rs_score:           'Relative Strength score vs sector ETF (XLK, XLV, etc.) on a 20-day return basis. 50 = in-line. Below 40 = lagging (compresses 15%). Above 60 = outperforming.',
  breadth_pct:        'Percentage of all tracked US stocks currently trading above their 200-day SMA. Below 40% = broad market weakness — signal compressed 10% even in a nominally-bull SPY regime.',
};

const SORT_LABELS: Record<SortKey, string> = {
  symbol: 'Symbol', signal: 'Signal', bullish_probability: 'Bull%',
  suppression_count: 'Filters', weekly_rsi: 'W.RSI', rsi: 'RSI',
  adx: 'ADX', days_to_earnings: 'Earn.d', news_sentiment: 'News',
  rs_score: 'RS', breadth_pct: 'Breadth',
};

const SIGNAL_COLORS: Record<string, string> = {
  BUY: '#22c55e', HOLD: '#38bdf8', WAIT: '#f59e0b', SELL: '#ef4444',
};

const REGIME_COLORS: Record<string, string> = {
  bull: '#22c55e', high_vol: '#f97316', bear: '#ef4444', unknown: '#64748b',
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, digits = 1): string {
  return n == null ? '—' : n.toFixed(digits);
}

function fmtTs(ts: string | null | undefined): string {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' +
      d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}

function numVal(row: SuppressedSignalRow, key: SortKey): number {
  if (key === 'symbol') return 0;
  if (key === 'signal') return ['BUY', 'HOLD', 'WAIT', 'SELL'].indexOf(row.signal);
  if (key === 'bullish_probability') return row.bullish_probability ?? 0;
  if (key === 'suppression_count') return row.suppression_count;
  if (key === 'weekly_rsi') return row.weekly_rsi ?? 999;
  if (key === 'rsi') return row.rsi ?? 999;
  if (key === 'adx') return row.adx ?? 0;
  if (key === 'days_to_earnings') return row.days_to_earnings ?? 9999;
  if (key === 'news_sentiment') return row.news_sentiment ?? 50;
  if (key === 'rs_score') return row.rs_score ?? 50;
  if (key === 'breadth_pct') return row.breadth_pct ?? 50;
  return 0;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SortTh({
  col, label, sortKey, dir, onSort, extraStyle,
}: {
  col: SortKey; label: string; sortKey: SortKey; dir: 'asc' | 'desc'; onSort: (k: SortKey) => void; extraStyle?: React.CSSProperties;
}) {
  const active = col === sortKey;
  const tip = COL_TIPS[col] ?? '';
  return (
    <th
      onClick={() => onSort(col)}
      style={{
        padding: '8px 8px', textAlign: 'left',
        color: active ? '#818cf8' : '#64748b',
        fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap',
        textTransform: 'uppercase', letterSpacing: '0.04em',
        cursor: 'pointer', userSelect: 'none',
        borderBottom: active ? '1px solid #6366f1' : '1px solid #1e293b',
        ...extraStyle,
      }}
    >
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        {label}
        {active ? (dir === 'asc' ? ' ↑' : ' ↓') : ''}
        <span
          title={tip}
          onClick={e => e.stopPropagation()}
          style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 13, height: 13, borderRadius: '50%',
            background: '#1e293b', color: '#64748b',
            fontSize: 9, fontWeight: 700, cursor: 'help',
            border: '1px solid #334155', lineHeight: 1, flexShrink: 0,
            marginLeft: 1,
          }}
        >!</span>
      </span>
    </th>
  );
}

function CondDot({ fired, color, tip }: { fired: boolean; color: string; tip: string }) {
  return (
    <span title={tip} style={{
      display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
      background: fired ? color : '#1e293b',
      border: `1px solid ${fired ? color : '#334155'}`,
      cursor: 'help',
    }} />
  );
}

function CountBadge({ n }: { n: number }) {
  const color = n === 0 ? '#22c55e' : n <= 2 ? '#f59e0b' : n <= 4 ? '#f97316' : '#ef4444';
  return (
    <span style={{
      display: 'inline-block', minWidth: 22, textAlign: 'center',
      padding: '1px 7px', borderRadius: 10, fontSize: 12, fontWeight: 700,
      background: `${color}22`, color, border: `1px solid ${color}44`,
    }}>{n}</span>
  );
}

// Summary bar showing how many stocks have each condition firing
function SummaryBar({ rows }: { rows: SuppressedSignalRow[] }) {
  if (!rows.length) return null;
  const total = rows.length;
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: 7, marginBottom: 16,
      padding: '10px 14px', background: '#0b1420', borderRadius: 10,
      border: '1px solid #1e293b',
    }}>
      {CONDITIONS.map(({ key, short, color, tip }) => {
        const count = rows.filter(r => r.conditions[key] === true).length;
        const pct = Math.round(count / total * 100);
        return (
          <span key={key} title={tip} style={{
            display: 'flex', alignItems: 'center', gap: 5, cursor: 'help',
            padding: '3px 9px', borderRadius: 6,
            background: count > 0 ? `${color}18` : '#0f172a',
            border: `1px solid ${count > 0 ? color + '44' : '#1e293b'}`,
            fontSize: 11,
          }}>
            <span style={{ color: count > 0 ? color : '#475569', fontWeight: 700 }}>{short}</span>
            <span style={{ color: count > 0 ? '#94a3b8' : '#334155' }}>{count} ({pct}%)</span>
          </span>
        );
      })}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SignalFiltersPage() {
  const [style, setStyle] = useState<string>('SWING');
  const [sigFilter, setSigFilter] = useState<string>('ALL');
  const [condFilters, setCondFilters] = useState<Set<CondKey>>(new Set());
  const [onlySuppressed, setOnlySuppressed] = useState(false);
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('suppression_count');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const { data, isLoading, error, mutate } = useSWR(
    ['suppressed', style],
    () => api.suppressedSignals(style),
    { revalidateOnFocus: false },
  );

  function handleSort(key: SortKey) {
    if (key === sortKey) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('desc'); }
  }

  function toggleCond(key: CondKey) {
    setCondFilters(prev => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  const rows = useMemo(() => {
    let r = data ?? [];

    // Signal type filter
    if (sigFilter !== 'ALL') r = r.filter(x => x.signal === sigFilter);

    // Suppressed-only toggle
    if (onlySuppressed) r = r.filter(x => x.suppression_count > 0);

    // Condition filters — show only stocks where ALL selected conditions fire
    if (condFilters.size > 0) {
      r = r.filter(x => [...condFilters].every(k => x.conditions[k] === true));
    }

    // Text search
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      r = r.filter(x => x.symbol.toLowerCase().includes(q) || x.name.toLowerCase().includes(q));
    }

    // Sort
    r = [...r].sort((a, b) => {
      let av: number | string = sortKey === 'symbol' ? a.symbol : numVal(a, sortKey);
      let bv: number | string = sortKey === 'symbol' ? b.symbol : numVal(b, sortKey);
      if (typeof av === 'string' && typeof bv === 'string') {
        return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      return sortDir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });

    return r;
  }, [data, sigFilter, onlySuppressed, condFilters, search, sortKey, sortDir]);

  const total = data?.length ?? 0;
  const buyCount = data?.filter(r => r.signal === 'BUY').length ?? 0;
  const gateCount = data?.filter(r => r.conditions.weekly_gate).length ?? 0;
  const suppCount = data?.filter(r => r.suppression_count > 0).length ?? 0;

  return (
    <div style={{ padding: '24px 28px', maxWidth: 1700, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 18 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#e2e8f0' }}>
            Signal Filter Monitor
          </h1>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
            All active stocks with suppression conditions from the latest signal. Hover dots for descriptions. Click headers to sort.
          </p>
        </div>
        <button
          onClick={() => mutate()}
          style={{
            padding: '7px 16px', borderRadius: 8, border: '1px solid #1e293b',
            background: '#0b1420', color: '#94a3b8', cursor: 'pointer', fontSize: 12,
          }}
        >
          Refresh
        </button>
      </div>

      {/* Stat pills */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
        {[
          { label: 'Total',       value: total,     color: '#94a3b8' },
          { label: 'BUY',         value: buyCount,  color: '#22c55e' },
          { label: 'Any filter',  value: suppCount, color: '#f97316' },
          { label: 'Gate fired',  value: gateCount, color: '#ef4444' },
          { label: 'Showing',     value: rows.length, color: '#818cf8' },
        ].map(p => (
          <div key={p.label} style={{
            padding: '7px 14px', background: '#0b1420', border: '1px solid #1e293b',
            borderRadius: 8, fontSize: 13,
          }}>
            <span style={{ color: '#475569' }}>{p.label}: </span>
            <span style={{ color: p.color, fontWeight: 700 }}>{p.value}</span>
          </div>
        ))}
      </div>

      {/* ── Controls ─────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        {/* Style */}
        <div style={{ display: 'flex', gap: 2, background: '#0b1420', padding: 3, borderRadius: 8, border: '1px solid #1e293b' }}>
          {STYLES.map(s => (
            <button key={s} onClick={() => setStyle(s)} style={{
              padding: '5px 14px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600,
              background: style === s ? '#6366f1' : 'transparent',
              color: style === s ? '#fff' : '#64748b',
            }}>{s}</button>
          ))}
        </div>

        {/* Signal */}
        <div style={{ display: 'flex', gap: 2, background: '#0b1420', padding: 3, borderRadius: 8, border: '1px solid #1e293b' }}>
          {SIGNAL_OPTS.map(s => (
            <button key={s} onClick={() => setSigFilter(s)} style={{
              padding: '5px 11px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600,
              background: sigFilter === s
                ? (s === 'ALL' ? '#334155' : (SIGNAL_COLORS[s] ?? '#334155') + '33')
                : 'transparent',
              color: sigFilter === s ? (s === 'ALL' ? '#e2e8f0' : SIGNAL_COLORS[s] ?? '#e2e8f0') : '#64748b',
            }}>{s}</button>
          ))}
        </div>

        {/* Suppressed only */}
        <button onClick={() => setOnlySuppressed(v => !v)} style={{
          padding: '5px 14px', borderRadius: 8, border: `1px solid ${onlySuppressed ? '#f97316' : '#1e293b'}`,
          background: onlySuppressed ? '#f9731618' : '#0b1420',
          color: onlySuppressed ? '#f97316' : '#64748b',
          cursor: 'pointer', fontSize: 12, fontWeight: 600,
        }}>
          Suppressed only
        </button>

        {/* Search */}
        <input
          placeholder="Search symbol / name…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{
            padding: '6px 12px', borderRadius: 8, border: '1px solid #1e293b',
            background: '#0b1420', color: '#e2e8f0', fontSize: 12, outline: 'none', width: 190,
          }}
        />
      </div>

      {/* ── Condition filter chips ─────────────────────────────────────────── */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 14 }}>
        <span style={{ fontSize: 11, color: '#475569', alignSelf: 'center', marginRight: 2 }}>Filter by condition:</span>
        {CONDITIONS.map(({ key, label, color, tip }) => {
          const active = condFilters.has(key);
          const count = (data ?? []).filter(r => r.conditions[key] === true).length;
          return (
            <button
              key={key}
              title={tip}
              onClick={() => toggleCond(key)}
              style={{
                padding: '3px 10px', borderRadius: 20, border: `1px solid ${active ? color : '#1e293b'}`,
                background: active ? `${color}22` : '#0b1420',
                color: active ? color : '#475569',
                cursor: 'pointer', fontSize: 11, fontWeight: active ? 700 : 400,
                display: 'flex', alignItems: 'center', gap: 5,
              }}
            >
              <span style={{
                width: 7, height: 7, borderRadius: '50%', display: 'inline-block',
                background: color, opacity: active ? 1 : 0.3,
              }} />
              {label}
              <span style={{ color: '#64748b', fontSize: 10 }}>({count})</span>
            </button>
          );
        })}
        {condFilters.size > 0 && (
          <button
            onClick={() => setCondFilters(new Set())}
            style={{
              padding: '3px 10px', borderRadius: 20, border: '1px solid #334155',
              background: 'transparent', color: '#64748b', cursor: 'pointer', fontSize: 11,
            }}
          >
            Clear filters ×
          </button>
        )}
      </div>

      {/* Summary bar */}
      {data && <SummaryBar rows={rows} />}

      {/* ── Table ─────────────────────────────────────────────────────────── */}
      {isLoading ? (
        <div style={{ color: '#64748b', textAlign: 'center', padding: 60 }}>Loading signals…</div>
      ) : error ? (
        <div style={{ color: '#ef4444', textAlign: 'center', padding: 60 }}>Failed to load — is the signal-engine running?</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                <SortTh col="symbol"             label="Symbol"   sortKey={sortKey} dir={sortDir} onSort={handleSort} extraStyle={{ position: 'sticky', left: 0, zIndex: 2, background: '#0b1420' }} />
                <SortTh col="signal"             label="Signal"   sortKey={sortKey} dir={sortDir} onSort={handleSort} />
                <th style={TH_STATIC} title="Email alert status from the conviction gate. ✓ = email sent. ✗ = gate blocked — hover to see why. — = no alert subscription or not yet checked.">
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    Alert
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      width: 13, height: 13, borderRadius: '50%',
                      background: '#1e293b', color: '#64748b',
                      fontSize: 9, fontWeight: 700, cursor: 'help',
                      border: '1px solid #334155', lineHeight: 1,
                    }}>!</span>
                  </span>
                </th>
                <SortTh col="bullish_probability" label="Bull%"   sortKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortTh col="suppression_count"  label="Filters"  sortKey={sortKey} dir={sortDir} onSort={handleSort} />

                {/* Condition columns — coloured, not sortable, each has ! tooltip */}
                {CONDITIONS.map(c => (
                  <th key={c.key} style={{
                    padding: '8px 6px', textAlign: 'center', color: c.color,
                    fontSize: 10, fontWeight: 700, whiteSpace: 'nowrap',
                    textTransform: 'uppercase', letterSpacing: '0.04em',
                    borderBottom: '1px solid #1e293b',
                  }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                      {c.short}
                      <span
                        title={c.tip}
                        style={{
                          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                          width: 12, height: 12, borderRadius: '50%',
                          background: c.color + '22', color: c.color,
                          fontSize: 8, fontWeight: 700, cursor: 'help',
                          border: `1px solid ${c.color}44`, lineHeight: 1,
                        }}
                      >!</span>
                    </span>
                  </th>
                ))}

                <SortTh col="weekly_rsi"       label="W.RSI"    sortKey={sortKey} dir={sortDir} onSort={handleSort} />
                <th style={TH_STATIC} title="Weekly price trend vs 10-week SMA. Up ↑ = price > SMA +1%. Down ↓ = price < SMA −1%. Used with Weekly RSI by the Gate.">
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    W.Trend
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      width: 13, height: 13, borderRadius: '50%',
                      background: '#1e293b', color: '#64748b',
                      fontSize: 9, fontWeight: 700, cursor: 'help',
                      border: '1px solid #334155', lineHeight: 1,
                    }}>!</span>
                  </span>
                </th>
                <SortTh col="rsi"              label="RSI"      sortKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortTh col="adx"              label="ADX"      sortKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortTh col="breadth_pct"      label="Breadth"  sortKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortTh col="days_to_earnings" label="Earn.d"   sortKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortTh col="news_sentiment"   label="News"     sortKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortTh col="rs_score"         label="RS"       sortKey={sortKey} dir={sortDir} onSort={handleSort} />
                <th style={TH_STATIC} title="Market regime based on SPY vs 200-day SMA and Fear & Greed score. Bull = SPY above 200MA + F&G ≥ 30. High-Vol = SPY above 200MA but F&G < 30. Bear = SPY below 200MA. Each regime uses a different BUY threshold.">
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    Regime
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      width: 13, height: 13, borderRadius: '50%',
                      background: '#1e293b', color: '#64748b',
                      fontSize: 9, fontWeight: 700, cursor: 'help',
                      border: '1px solid #334155', lineHeight: 1,
                    }}>!</span>
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const sigColor = SIGNAL_COLORS[row.signal] ?? '#94a3b8';
                const hasGate = row.conditions.weekly_gate;
                return (
                  <tr key={row.symbol} style={{
                    borderBottom: '1px solid #0f172a',
                    background: hasGate ? '#ef444408' : row.suppression_count >= 3 ? '#f9731604' : 'transparent',
                    transition: 'background 0.1s',
                  }}
                    onMouseEnter={e => (e.currentTarget.style.background = '#ffffff08')}
                    onMouseLeave={e => (e.currentTarget.style.background =
                      hasGate ? '#ef444408' : row.suppression_count >= 3 ? '#f9731604' : 'transparent'
                    )}
                  >
                    {/* Symbol — sticky left */}
                    <td style={{ ...TD, position: 'sticky', left: 0, zIndex: 1, background: hasGate ? '#1a0f10' : row.suppression_count >= 3 ? '#130f0b' : '#0b1420' }}>
                      <Link href={`/stock/${row.symbol}`} style={{ color: '#818cf8', textDecoration: 'none', fontWeight: 600 }}>
                        {row.symbol}
                      </Link>
                      <span style={{ color: '#334155', marginLeft: 6, fontSize: 10 }}>
                        {row.name.length > 20 ? row.name.slice(0, 20) + '…' : row.name}
                      </span>
                    </td>

                    {/* Signal badge */}
                    <td style={TD}>
                      <span style={{
                        padding: '2px 8px', borderRadius: 5, fontSize: 11, fontWeight: 700,
                        background: sigColor + '22', color: sigColor, border: `1px solid ${sigColor}44`,
                      }}>{row.signal}</span>
                    </td>

                    {/* Alert / conviction gate */}
                    <td style={{ ...TD, minWidth: 110 }}>
                      {row.conviction == null ? (
                        <span style={{ color: '#475569', fontSize: 11 }}>—</span>
                      ) : row.conviction.sent ? (
                        <span style={{ display: 'block', lineHeight: 1.5 }}>
                          <span style={{ color: '#22c55e', fontSize: 11, fontWeight: 700 }}>✓ Sent</span>
                          <span style={{ display: 'block', color: '#475569', fontSize: 10 }}>
                            {fmtTs(row.conviction.ts)}
                          </span>
                        </span>
                      ) : (
                        <span
                          title={row.conviction.failed.join('\n')}
                          style={{ color: '#f87171', fontSize: 11, cursor: 'help', display: 'block', lineHeight: 1.5 }}
                        >
                          ✗ {row.conviction.failed[0] ?? 'Gate failed'}
                          {row.conviction.failed.length > 1 && (
                            <span style={{ color: '#64748b' }}> +{row.conviction.failed.length - 1}</span>
                          )}
                          <span style={{ display: 'block', color: '#475569', fontSize: 10 }}>
                            {fmtTs(row.conviction.ts)}
                          </span>
                        </span>
                      )}
                    </td>

                    {/* Bull% */}
                    <td style={{ ...TD, color: (row.bullish_probability ?? 0) >= 0.5 ? '#22c55e' : '#f87171', fontWeight: 600 }}>
                      {row.bullish_probability != null ? `${(row.bullish_probability * 100).toFixed(1)}%` : '—'}
                    </td>

                    {/* Filter count badge */}
                    <td style={{ ...TD, textAlign: 'center' }}>
                      <CountBadge n={row.suppression_count} />
                    </td>

                    {/* Condition dots */}
                    {CONDITIONS.map(c => (
                      <td key={c.key} style={{ ...TD, textAlign: 'center', padding: '7px 6px' }}>
                        <CondDot fired={row.conditions[c.key] === true} color={c.color} tip={c.tip} />
                      </td>
                    ))}

                    {/* Weekly RSI */}
                    <td style={{
                      ...TD,
                      color: row.weekly_rsi != null
                        ? row.weekly_rsi < 30 ? '#ef4444'
                        : row.weekly_rsi < 40 ? '#f97316'
                        : row.weekly_rsi > 70 ? '#f59e0b'
                        : '#94a3b8' : '#475569',
                      fontWeight: row.weekly_rsi != null && row.weekly_rsi < 40 ? 700 : 400,
                    }}>
                      {fmt(row.weekly_rsi)}
                    </td>

                    {/* Weekly trend */}
                    <td style={{
                      ...TD,
                      color: row.weekly_trend === 'up' ? '#22c55e'
                        : row.weekly_trend === 'down' ? '#ef4444'
                        : '#64748b',
                      fontWeight: 600,
                    }}>
                      {row.weekly_trend === 'up' ? '↑ up' : row.weekly_trend === 'down' ? '↓ down' : '→ neutral'}
                    </td>

                    {/* Daily RSI */}
                    <td style={{
                      ...TD,
                      color: row.rsi != null
                        ? row.rsi < 30 ? '#22c55e'
                        : row.rsi < 35 ? '#4ade80'
                        : row.rsi > 75 ? '#ef4444'
                        : row.rsi > 70 ? '#f59e0b'
                        : '#94a3b8' : '#475569',
                    }}>
                      {fmt(row.rsi)}
                    </td>

                    {/* ADX */}
                    <td style={{ ...TD, color: row.adx != null && row.adx < 20 ? '#eab308' : '#94a3b8' }}>
                      {fmt(row.adx)}
                    </td>

                    {/* Breadth */}
                    <td style={{ ...TD, color: row.breadth_pct != null && row.breadth_pct < 40 ? '#eab308' : '#94a3b8' }}>
                      {row.breadth_pct != null ? `${fmt(row.breadth_pct, 0)}%` : '—'}
                    </td>

                    {/* Days to earnings */}
                    <td style={{
                      ...TD,
                      color: row.days_to_earnings != null
                        ? row.days_to_earnings <= 2 ? '#ef4444'
                        : row.days_to_earnings <= 5 ? '#f97316'
                        : row.days_to_earnings <= 10 ? '#eab308'
                        : '#94a3b8' : '#475569',
                      fontWeight: row.days_to_earnings != null && row.days_to_earnings <= 5 ? 700 : 400,
                    }}>
                      {row.days_to_earnings != null ? `${row.days_to_earnings}d` : '—'}
                    </td>

                    {/* News sentiment */}
                    <td style={{
                      ...TD,
                      color: row.news_sentiment != null
                        ? row.news_sentiment < 25 ? '#ef4444'
                        : row.news_sentiment < 35 ? '#f97316'
                        : row.news_sentiment > 65 ? '#22c55e'
                        : '#94a3b8' : '#475569',
                    }}>
                      {row.news_sentiment != null ? fmt(row.news_sentiment, 0) : '—'}
                    </td>

                    {/* RS score */}
                    <td style={{
                      ...TD,
                      color: row.rs_score != null
                        ? row.rs_score >= 60 ? '#22c55e'
                        : row.rs_score < 40 ? '#ef4444'
                        : '#94a3b8' : '#475569',
                    }}>
                      {row.rs_score != null ? fmt(row.rs_score, 0) : '—'}
                    </td>

                    {/* Regime */}
                    <td style={{ ...TD, color: REGIME_COLORS[row.market_regime ?? ''] ?? '#64748b', fontWeight: 600 }}>
                      {row.market_regime ?? '—'}
                    </td>
                  </tr>
                );
              })}
              {rows.length === 0 && !isLoading && (
                <tr>
                  <td colSpan={18 + CONDITIONS.length} style={{ textAlign: 'center', padding: 48, color: '#475569' }}>
                    No stocks match the current filters.
                    {condFilters.size > 0 && (
                      <button onClick={() => setCondFilters(new Set())} style={{
                        marginLeft: 8, color: '#818cf8', background: 'none', border: 'none', cursor: 'pointer', fontSize: 12,
                      }}>Clear condition filters</button>
                    )}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Legend */}
      <div style={{ marginTop: 24, padding: '12px 16px', background: '#0b1420', borderRadius: 8, border: '1px solid #1e293b' }}>
        <p style={{ margin: '0 0 8px', fontSize: 11, fontWeight: 600, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Severity legend
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          {[
            { color: '#ef4444', label: 'Hard block — BUY effectively impossible without extreme setup (weekly gate, stale data)' },
            { color: '#f97316', label: 'Strong suppression — major compression, significantly raises the bar (high-vol, earnings, neg news)' },
            { color: '#eab308', label: 'Moderate suppression — BUY still reachable but harder (ADX choppy, low breadth, RS lagging)' },
            { color: '#818cf8', label: 'Informational — compression cap hit, not itself a block (shows stacked filters reached the floor)' },
          ].map(({ color, label }) => (
            <div key={color} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: '#94a3b8' }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, display: 'inline-block', flexShrink: 0 }} />
              {label}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const TH_STATIC: React.CSSProperties = {
  padding: '8px 10px', textAlign: 'left', color: '#64748b',
  fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap',
  textTransform: 'uppercase', letterSpacing: '0.04em',
  borderBottom: '1px solid #1e293b',
};

const TD: React.CSSProperties = {
  padding: '7px 10px', color: '#94a3b8', whiteSpace: 'nowrap', verticalAlign: 'middle',
};
