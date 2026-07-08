import { useState, useEffect } from 'react';
import Head from 'next/head';
import { api, RegimeStatus } from '../lib/api';

const REGIME_COLOR: Record<string, string> = {
  bull:     '#22c55e',
  neutral:  '#94a3b8',
  choppy:   '#f59e0b',
  risk_off: '#f97316',
  bear:     '#ef4444',
};

const REGIME_LABEL: Record<string, string> = {
  bull:     'Bull',
  neutral:  'Neutral',
  choppy:   'Choppy',
  risk_off: 'Risk-Off',
  bear:     'Bear',
};

const REGIME_DESC: Record<string, string> = {
  bull:     'SPY above 50EMA + 200EMA, VIX < 25. Full position sizing allowed.',
  neutral:  'Mixed signals — no clear trend. Standard sizing.',
  choppy:   'SPY below 50EMA or hugging EMA20. Min score raised to 4. Avoid new entries.',
  risk_off: 'VIX ≥ 25 or SPY below EMA50. Min score raised to 5. Tight sizing.',
  bear:     'SPY below 200EMA or VIX ≥ 30 + SPY below EMA50. All new entries BLOCKED.',
};

const MIN_SCORE: Record<string, number> = {
  bull: 3, neutral: 3, choppy: 4, risk_off: 5, bear: 999,
};

function fmt(n: number | null | undefined, decimals = 2, prefix = '') {
  if (n == null) return '—';
  return `${prefix}${n.toFixed(decimals)}`;
}

function pct(n: number | null | undefined) {
  if (n == null) return '—';
  const s = n > 0 ? '+' : '';
  return `${s}${n.toFixed(2)}%`;
}

function StatPill({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ padding: '8px 14px', background: '#0f172a', borderRadius: 8, display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: 11, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</span>
      <span style={{ fontSize: 16, fontWeight: 700, color: color ?? '#e2e8f0' }}>{value}</span>
    </div>
  );
}

function RegimeCard({ market, data, loading }: { market: string; data: RegimeStatus | null; loading: boolean }) {
  const state = data?.state ?? 'neutral';
  const color = REGIME_COLOR[state] ?? '#94a3b8';

  return (
    <div style={{ flex: 1, padding: 20, background: '#0f172a', borderRadius: 12, border: `2px solid ${loading ? '#1e293b' : color}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: '#94a3b8' }}>{market} Market</div>
        {loading ? (
          <div style={{ padding: '4px 14px', background: '#1e293b', borderRadius: 16, color: '#475569', fontSize: 13 }}>Loading…</div>
        ) : (
          <div style={{
            padding: '4px 16px', borderRadius: 16, fontWeight: 800, fontSize: 16,
            background: `${color}18`, color, border: `1px solid ${color}`,
          }}>
            {REGIME_LABEL[state] ?? state}
          </div>
        )}
        {data && (
          <div style={{ marginLeft: 'auto', padding: '3px 10px', borderRadius: 8, fontSize: 11, fontWeight: 600,
            background: MIN_SCORE[state] >= 999 ? '#450a0a' : '#0f172a',
            color: MIN_SCORE[state] >= 999 ? '#ef4444' : '#64748b',
            border: `1px solid ${MIN_SCORE[state] >= 999 ? '#ef4444' : '#1e293b'}`,
          }}>
            {MIN_SCORE[state] >= 999 ? 'BLOCKED' : `Min Score: ${MIN_SCORE[state]}`}
          </div>
        )}
      </div>

      {data && (
        <p style={{ fontSize: 12, color: '#64748b', marginBottom: 14, lineHeight: 1.5 }}>
          {REGIME_DESC[state]}
        </p>
      )}

      {market === 'US' && data && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, marginBottom: 12 }}>
            <StatPill label="VIX" value={fmt(data.vix, 2)} color={
              data.vix && data.vix >= 30 ? '#ef4444' : data.vix && data.vix >= 25 ? '#f97316' : '#22c55e'
            } />
            <StatPill label="VIX9D" value={fmt(data.vix9d, 2)} />
            <StatPill label="VIX Trend (5d)" value={data.vix_5d_trend ?? '—'} color={
              data.vix_5d_trend === 'rising' ? '#ef4444' : data.vix_5d_trend === 'falling' ? '#22c55e' : '#94a3b8'
            } />
            <StatPill label="SPY" value={fmt(data.spy_price, 2, '$')} />
            <StatPill label="SPY 20d Return" value={pct(data.spy_20d_ret)} color={
              data.spy_20d_ret && data.spy_20d_ret > 0 ? '#22c55e' : '#ef4444'
            } />
            <StatPill label="QQQ" value={fmt(data.qqq_price, 2, '$')} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, marginBottom: 12 }}>
            <StatPill label="SPY EMA20" value={fmt(data.spy_ema20, 2, '$')} color={
              data.spy_price && data.spy_ema20 ? (data.spy_price > data.spy_ema20 ? '#22c55e' : '#ef4444') : '#94a3b8'
            } />
            <StatPill label="SPY EMA50" value={fmt(data.spy_ema50, 2, '$')} color={
              data.spy_price && data.spy_ema50 ? (data.spy_price > data.spy_ema50 ? '#22c55e' : '#ef4444') : '#94a3b8'
            } />
            <StatPill label="SPY EMA200" value={fmt(data.spy_ema200, 2, '$')} color={
              data.spy_price && data.spy_ema200 ? (data.spy_price > data.spy_ema200 ? '#22c55e' : '#ef4444') : '#94a3b8'
            } />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 12 }}>
            <StatPill label="VIX Term Inverted" value={data.vix_term_inverted ? 'YES (stress signal)' : 'No'} color={data.vix_term_inverted ? '#ef4444' : '#22c55e'} />
            <StatPill label="Breadth (IWM+MDY)" value={data.breadth_weak ? 'Weak — both below 200EMA' : `Size mult ${data.breadth_size_mult.toFixed(2)}×`} color={data.breadth_weak ? '#ef4444' : '#22c55e'} />
          </div>
        </>
      )}

      {market === 'HK' && data && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, marginBottom: 12 }}>
          <StatPill label="HSI" value={fmt(data.hsi_price, 0)} />
          <StatPill label="HSI SMA50" value={fmt(data.hsi_ema50, 0)} color={
            data.hsi_price && data.hsi_ema50 ? (data.hsi_price > data.hsi_ema50 ? '#22c55e' : '#ef4444') : '#94a3b8'
          } />
          <StatPill label="HSI SMA200" value={fmt(data.hsi_ema200, 0)} color={
            data.hsi_price && data.hsi_ema200 ? (data.hsi_price > data.hsi_ema200 ? '#22c55e' : '#ef4444') : '#94a3b8'
          } />
        </div>
      )}

      {data?.notes && data.notes.length > 0 && (
        <div style={{ marginTop: 8 }}>
          {data.notes.map((note, i) => (
            <div key={i} style={{ fontSize: 12, color: '#64748b', padding: '4px 0', borderTop: i > 0 ? '1px solid #1e293b' : 'none' }}>
              {note}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function RegimePage() {
  const [us, setUs] = useState<RegimeStatus | null>(null);
  const [hk, setHk] = useState<RegimeStatus | null>(null);
  const [loadingUs, setLoadingUs] = useState(true);
  const [loadingHk, setLoadingHk] = useState(true);
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);

  const load = () => {
    setLoadingUs(true);
    setLoadingHk(true);
    api.regime('US').then(d => { setUs(d); setLoadingUs(false); }).catch(() => setLoadingUs(false));
    api.regime('HK').then(d => { setHk(d); setLoadingHk(false); }).catch(() => setLoadingHk(false));
    setRefreshedAt(new Date());
  };

  useEffect(() => { load(); }, []);

  return (
    <>
      <Head><title>Market Regime — StockAI</title></Head>
      <div style={{ minHeight: '100vh', background: '#020617', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 900, margin: '0 auto' }}>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <h1 style={{ fontSize: 22, fontWeight: 700, color: '#f1f5f9', margin: 0 }}>Market Regime</h1>
            <button
              onClick={load}
              style={{ padding: '7px 16px', background: '#1e293b', border: '1px solid #334155', borderRadius: 7, color: '#94a3b8', fontSize: 13, cursor: 'pointer' }}
            >
              Refresh
            </button>
          </div>
          <p style={{ fontSize: 13, color: '#475569', marginBottom: 24 }}>
            Live regime from Decision Engine (4-hour cache) — drives min_score thresholds and Kelly multipliers.
            {refreshedAt && <span> · Fetched {refreshedAt.toLocaleTimeString()}</span>}
          </p>

          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <RegimeCard market="US" data={us} loading={loadingUs} />
            <RegimeCard market="HK" data={hk} loading={loadingHk} />
          </div>

          {/* Regime reference table */}
          <div style={{ marginTop: 24, padding: 20, background: '#0f172a', borderRadius: 12, border: '1px solid #1e293b' }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 12 }}>Regime Reference</div>
            <div style={{ display: 'grid', gridTemplateColumns: '100px 80px 120px 1fr', gap: '8px 16px', fontSize: 12 }}>
              <span style={{ color: '#475569', fontWeight: 600 }}>Regime</span>
              <span style={{ color: '#475569', fontWeight: 600 }}>Min Score</span>
              <span style={{ color: '#475569', fontWeight: 600 }}>Kelly Mult</span>
              <span style={{ color: '#475569', fontWeight: 600 }}>Decision</span>
              {[
                ['bull',     '3',   '1.00×', 'Full entries allowed'],
                ['neutral',  '3',   '1.00×', 'Standard entries'],
                ['choppy',   '4',   '0.75×', 'Raised threshold — only high-conviction entries'],
                ['risk_off', '5',   '0.50×', 'Very high bar — most candidates skipped'],
                ['bear',     '∞',   '0.00×', 'All new entries blocked'],
              ].map(([regime, ms, km, desc]) => (
                <>
                  <span key={`r${regime}`} style={{ color: REGIME_COLOR[regime], fontWeight: 700 }}>{REGIME_LABEL[regime]}</span>
                  <span key={`ms${regime}`} style={{ color: '#e2e8f0' }}>{ms}</span>
                  <span key={`km${regime}`} style={{ color: regime === 'bear' ? '#ef4444' : '#94a3b8' }}>{km}</span>
                  <span key={`d${regime}`} style={{ color: '#64748b' }}>{desc}</span>
                </>
              ))}
            </div>
          </div>

        </div>
      </div>
    </>
  );
}
