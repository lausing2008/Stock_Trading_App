import { useState } from 'react';
import useSWR from 'swr';
import { api, type PortfolioWeights, type Stock } from '@/lib/api';

type Method = 'mean_variance' | 'risk_parity' | 'hierarchical_risk_parity' | 'ai_allocation';

const METHODS: { value: Method; label: string; description: string; badge: string; badgeColor: string }[] = [
  {
    value: 'mean_variance',
    label: 'Max Sharpe (MVO)',
    description: 'Finds weights that maximize the Sharpe ratio. Uses Ledoit-Wolf covariance shrinkage and James-Stein return shrinkage to reduce estimation noise.',
    badge: 'Recommended',
    badgeColor: '#4ade80',
  },
  {
    value: 'risk_parity',
    label: 'Risk Parity',
    description: 'Each asset contributes equally to total portfolio risk. Tends to be more diversified and less sensitive to return estimates.',
    badge: 'Stable',
    badgeColor: '#60a5fa',
  },
  {
    value: 'hierarchical_risk_parity',
    label: 'Hierarchical Risk Parity',
    description: 'Clusters assets by correlation, then allocates risk within and across clusters. Most robust to estimation error — no matrix inversion required.',
    badge: 'Robust',
    badgeColor: '#a78bfa',
  },
  {
    value: 'ai_allocation',
    label: 'AI Allocation',
    description: 'Filters by K-Score (removes weak stocks), blends K-Score views with historical returns, then maximizes Sharpe. Keeps a defensive cash buffer.',
    badge: 'AI-Powered',
    badgeColor: '#f472b6',
  },
];

const LOOKBACK_OPTIONS = [
  { value: 180, label: '6 months' },
  { value: 365, label: '1 year' },
  { value: 730, label: '2 years' },
  { value: 1095, label: '3 years' },
];

const inp: React.CSSProperties = {
  background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px',
  padding: '9px 12px', fontSize: '13px', color: '#e2e8f0', outline: 'none',
  width: '100%', boxSizing: 'border-box',
};

const lbl: React.CSSProperties = {
  fontSize: '11px', color: '#64748b', fontWeight: 600,
  textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '5px',
};

const metricCard = (color: string): React.CSSProperties => ({
  padding: '12px 16px', borderRadius: '10px', flex: 1,
  background: 'rgba(15,23,42,0.8)', border: `1px solid ${color}30`,
});

function fmt(v: number | null | undefined, pct = false, decimals = 1): string {
  if (v == null) return '—';
  const val = pct ? v * 100 : v;
  return (val >= 0 ? '+' : '') + val.toFixed(decimals) + (pct ? '%' : '');
}

const METHOD_COLORS: Record<string, string> = {
  mean_variance: '#818cf8',
  risk_parity: '#60a5fa',
  hierarchical_risk_parity: '#a78bfa',
  ai_allocation: '#f472b6',
};

const STOCK_COLORS = [
  '#818cf8', '#60a5fa', '#34d399', '#fbbf24', '#f472b6',
  '#a78bfa', '#4ade80', '#38bdf8', '#fb923c', '#e879f9',
];

export default function PortfolioPage() {
  const { data: stocks } = useSWR<Stock[]>('stocks', () => api.listStocks());

  const [symbolInput, setSymbolInput] = useState('AAPL,MSFT,NVDA,GOOGL,AMZN');
  const [method, setMethod] = useState<Method>('mean_variance');
  const [lookback, setLookback] = useState(365);
  const [minScore, setMinScore] = useState(60);
  const [result, setResult] = useState<PortfolioWeights | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedMeta = METHODS.find(m => m.value === method)!;
  const accentColor = METHOD_COLORS[method] ?? '#818cf8';

  async function run() {
    const syms = symbolInput.split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
    if (syms.length < 2) { setError('Enter at least 2 symbols.'); return; }
    setLoading(true);
    setError(null);
    try {
      const r = await api.optimizePortfolio({
        symbols: syms,
        method,
        lookback_days: lookback,
        min_score: minScore,
      });
      setResult(r);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Optimization failed.';
      setError(msg.includes('400') ? 'Insufficient price history for one or more symbols. Try a shorter lookback or different stocks.' : msg);
    } finally {
      setLoading(false);
    }
  }

  const allEntries = result
    ? [...Object.entries(result.weights).sort((a, b) => b[1] - a[1]), ...(result.cash > 0.001 ? [['Cash', result.cash] as [string, number]] : [])]
    : [];
  const maxWeight = allEntries.reduce((m, [, w]) => Math.max(m, w), 0);

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', paddingTop: '8px' }}>

      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 800, color: '#f1f5f9' }}>Portfolio Optimizer</h1>
        <div style={{ fontSize: '12px', color: '#475569', marginTop: '3px' }}>
          Build an optimally-allocated portfolio using advanced quantitative methods
        </div>
      </div>

      {/* Config panel */}
      <div style={{ borderRadius: '12px', border: '1px solid rgba(99,102,241,0.25)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden', marginBottom: '24px' }}>
        <div style={{ height: '3px', background: `linear-gradient(90deg,${accentColor},${accentColor}88,${accentColor})` }} />
        <div style={{ padding: '20px 24px' }}>

          {/* Method selector */}
          <div style={{ marginBottom: '18px' }}>
            <label style={lbl}>Optimization Method</label>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '8px' }}>
              {METHODS.map(m => (
                <button
                  key={m.value}
                  onClick={() => setMethod(m.value)}
                  style={{
                    padding: '10px 14px', borderRadius: '10px', cursor: 'pointer',
                    textAlign: 'left', transition: 'all 0.15s',
                    background: method === m.value ? `${METHOD_COLORS[m.value]}15` : 'rgba(15,23,42,0.6)',
                    border: method === m.value ? `1px solid ${METHOD_COLORS[m.value]}50` : '1px solid #1e293b',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                    <span style={{ fontSize: '13px', fontWeight: 700, color: method === m.value ? METHOD_COLORS[m.value] : '#94a3b8' }}>
                      {m.label}
                    </span>
                    <span style={{
                      fontSize: '9px', fontWeight: 700, padding: '1px 6px', borderRadius: '999px',
                      background: `${m.badgeColor}20`, color: m.badgeColor, letterSpacing: '0.05em',
                    }}>
                      {m.badge}
                    </span>
                  </div>
                  <div style={{ fontSize: '11px', color: '#475569', lineHeight: 1.4 }}>{m.description}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Symbols + options row */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '12px', alignItems: 'end', marginBottom: '12px' }}>
            <div>
              <label style={lbl}>Symbols (comma-separated)</label>
              <input
                value={symbolInput}
                onChange={e => setSymbolInput(e.target.value)}
                placeholder="AAPL, MSFT, NVDA, 0700.HK …"
                style={inp}
              />
            </div>
            <div>
              <label style={lbl}>Lookback</label>
              <select value={lookback} onChange={e => setLookback(Number(e.target.value))} style={{ ...inp, width: 'auto' }}>
                {LOOKBACK_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            {method === 'ai_allocation' && (
              <div>
                <label style={lbl}>Min K-Score</label>
                <select value={minScore} onChange={e => setMinScore(Number(e.target.value))} style={{ ...inp, width: 'auto' }}>
                  <option value={50}>50</option>
                  <option value={60}>60</option>
                  <option value={70}>70</option>
                  <option value={80}>80</option>
                </select>
              </div>
            )}
          </div>

          {/* Stock quick-add from watchlist */}
          {stocks && stocks.length > 0 && (
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '14px' }}>
              <span style={{ fontSize: '11px', color: '#334155', alignSelf: 'center' }}>Quick add:</span>
              {stocks.slice(0, 12).map(s => (
                <button
                  key={s.symbol}
                  onClick={() => {
                    const current = symbolInput.split(',').map(x => x.trim().toUpperCase()).filter(Boolean);
                    if (!current.includes(s.symbol)) setSymbolInput([...current, s.symbol].join(','));
                  }}
                  style={{
                    fontSize: '11px', padding: '2px 8px', borderRadius: '4px',
                    background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.2)',
                    color: '#818cf8', cursor: 'pointer',
                  }}
                >
                  {s.symbol}
                </button>
              ))}
            </div>
          )}

          {error && (
            <div style={{ marginBottom: '12px', padding: '10px 14px', borderRadius: '8px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)', color: '#f87171', fontSize: '13px' }}>
              {error}
            </div>
          )}

          <button
            onClick={run}
            disabled={loading}
            style={{
              background: loading ? '#1e293b' : `linear-gradient(135deg,${accentColor},${accentColor}bb)`,
              border: 'none', color: loading ? '#475569' : '#fff',
              padding: '10px 28px', borderRadius: '8px', fontSize: '13px',
              fontWeight: 700, cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'all 0.2s',
            }}
          >
            {loading ? '⟳ Optimizing…' : 'Optimize Portfolio'}
          </button>
        </div>
      </div>

      {/* Results */}
      {result && (
        <div>
          {/* Metrics row */}
          <div style={{ display: 'flex', gap: '10px', marginBottom: '16px', flexWrap: 'wrap' }}>
            {[
              { label: 'Expected Return', value: fmt(result.expected_return, true), color: '#4ade80', hint: 'Annualized' },
              { label: 'Expected Volatility', value: fmt(result.expected_vol, true), color: '#f87171', hint: 'Annualized' },
              { label: 'Sharpe Ratio', value: result.sharpe_ratio != null ? result.sharpe_ratio.toFixed(2) : '—', color: accentColor, hint: 'Rf = 4%' },
              { label: 'Max Drawdown', value: fmt(result.max_drawdown, true), color: '#fbbf24', hint: 'Historical' },
              { label: 'Diversification', value: result.diversification != null ? (result.diversification * 100).toFixed(0) + '%' : '—', color: '#60a5fa', hint: '1 − HHI' },
            ].map(m => (
              <div key={m.label} style={metricCard(m.color)}>
                <div style={{ fontSize: '10px', color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '4px' }}>
                  {m.label} <span style={{ color: '#1e293b' }}>· {m.hint}</span>
                </div>
                <div style={{ fontSize: '20px', fontWeight: 800, color: m.color }}>{m.value}</div>
              </div>
            ))}
          </div>

          {/* Allocation bars */}
          <div style={{ borderRadius: '12px', border: '1px solid #1e293b', background: 'rgba(15,23,42,0.95)', overflow: 'hidden' }}>
            <div style={{ height: '3px', background: `linear-gradient(90deg,${accentColor},${accentColor}88,${accentColor})` }} />
            <div style={{ padding: '16px 20px', borderBottom: '1px solid #1e293b', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <span style={{ fontSize: '14px', fontWeight: 700, color: '#e2e8f0' }}>Optimal Allocation</span>
                <span style={{ fontSize: '12px', color: '#475569', marginLeft: '8px' }}>
                  {Object.keys(result.weights).length} positions · {selectedMeta.label}
                </span>
              </div>
            </div>

            <div style={{ padding: '8px 0' }}>
              {allEntries.map(([sym, w], i) => {
                const isCash = sym === 'Cash';
                const color = isCash ? '#475569' : STOCK_COLORS[i % STOCK_COLORS.length];
                const barWidth = maxWeight > 0 ? (w / maxWeight) * 100 : 0;
                return (
                  <div key={sym} style={{ padding: '8px 20px', display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <span style={{
                      minWidth: '80px', fontSize: '13px', fontWeight: 800,
                      color: isCash ? '#475569' : color, fontFamily: 'monospace',
                    }}>
                      {sym}
                    </span>
                    <div style={{ flex: 1, height: '8px', borderRadius: '4px', background: '#1e293b', overflow: 'hidden' }}>
                      <div style={{
                        height: '100%', borderRadius: '4px', transition: 'width 0.4s ease',
                        width: `${barWidth}%`,
                        background: isCash ? '#1e293b' : `linear-gradient(90deg,${color},${color}99)`,
                        border: isCash ? '1px solid #334155' : 'none',
                      }} />
                    </div>
                    <span style={{ minWidth: '52px', textAlign: 'right', fontSize: '14px', fontWeight: 700, color: isCash ? '#475569' : '#f1f5f9', fontFamily: 'monospace' }}>
                      {(w * 100).toFixed(1)}%
                    </span>
                  </div>
                );
              })}
            </div>

            {/* Equal-weight comparison */}
            <div style={{ padding: '12px 20px', borderTop: '1px solid #1e293b', background: 'rgba(0,0,0,0.2)' }}>
              <div style={{ fontSize: '11px', color: '#334155', marginBottom: '6px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Equal-weight baseline ({(100 / (Object.keys(result.weights).length || 1)).toFixed(1)}% each)
              </div>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                {Object.keys(result.weights).map((s, i) => (
                  <div key={s} style={{
                    fontSize: '11px', padding: '2px 8px', borderRadius: '4px',
                    background: `${STOCK_COLORS[i % STOCK_COLORS.length]}10`,
                    border: `1px solid ${STOCK_COLORS[i % STOCK_COLORS.length]}25`,
                    color: '#475569',
                  }}>
                    {s}
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Interpretation */}
          <div style={{ marginTop: '16px', padding: '14px 18px', borderRadius: '10px', background: 'rgba(15,23,42,0.6)', border: '1px solid #1e293b', fontSize: '12px', color: '#64748b', lineHeight: 1.6 }}>
            <strong style={{ color: '#94a3b8' }}>How to read this:</strong>{' '}
            Allocate your investment capital according to the percentages above.
            The Sharpe ratio measures return per unit of risk (higher = better).
            Max drawdown is the worst peak-to-trough loss over the lookback period.
            Diversification score (1 − HHI) approaches 1.0 for perfectly spread portfolios.
            {method === 'ai_allocation' && (
              <> The cash buffer provides downside protection for stocks that didn&apos;t meet the K-Score threshold.</>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
