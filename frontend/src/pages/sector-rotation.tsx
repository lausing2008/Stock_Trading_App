import { useState, useEffect } from 'react';
import Link from 'next/link';
import { api, type SectorRotationEntry, type SectorRsStock } from '@/lib/api';

// ── Helpers ────────────────────────────────────────────────────────────────────

function rsColor(rs: number | null) {
  if (rs == null) return '#475569';
  if (rs >= 65) return '#4ade80';
  if (rs >= 55) return '#86efac';
  if (rs >= 45) return '#94a3b8';
  if (rs >= 35) return '#fb923c';
  return '#f87171';
}

function rsLabel(rs: number | null) {
  if (rs == null) return '—';
  if (rs >= 65) return 'Leading';
  if (rs >= 55) return 'Above avg';
  if (rs >= 45) return 'In-line';
  if (rs >= 35) return 'Below avg';
  return 'Lagging';
}

function momentumArrow(change: number | null) {
  if (change == null) return { icon: '—', color: '#475569' };
  if (change >= 3) return { icon: '↑↑', color: '#4ade80' };
  if (change >= 1) return { icon: '↑', color: '#86efac' };
  if (change > -1) return { icon: '→', color: '#94a3b8' };
  if (change > -3) return { icon: '↓', color: '#fb923c' };
  return { icon: '↓↓', color: '#f87171' };
}

// ── Components ─────────────────────────────────────────────────────────────────

function RsBar({ score }: { score: number | null }) {
  const pct = score ?? 50;
  return (
    <div style={{ position: 'relative', height: '6px', borderRadius: '3px', background: '#1e293b', overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: '1px', background: '#334155', zIndex: 1 }} />
      {pct >= 50 ? (
        <div style={{ position: 'absolute', left: '50%', width: `${(pct - 50) * 2}%`, height: '100%', background: rsColor(pct), borderRadius: '0 3px 3px 0', transition: 'width 0.5s ease' }} />
      ) : (
        <div style={{ position: 'absolute', right: `${50}%`, width: `${(50 - pct) * 2}%`, height: '100%', background: rsColor(pct), borderRadius: '3px 0 0 3px', transition: 'width 0.5s ease' }} />
      )}
    </div>
  );
}

function StockPill({ s }: { s: SectorRsStock }) {
  return (
    <Link href={`/stock/${s.symbol}`} style={{ textDecoration: 'none' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '5px 10px', borderRadius: '6px', background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.05)', cursor: 'pointer',
        transition: 'background 0.15s',
      }}>
        <span style={{ fontSize: '12px', fontWeight: 700, color: '#e2e8f0', fontFamily: 'ui-monospace,monospace' }}>{s.symbol}</span>
        <span style={{ fontSize: '11px', color: rsColor(s.rs_score), fontWeight: 600, marginLeft: '8px' }}>
          {s.rs_score?.toFixed(0) ?? '—'}
        </span>
      </div>
    </Link>
  );
}

function SectorCard({ entry, rank }: { entry: SectorRotationEntry; rank: number }) {
  const arrow = momentumArrow(entry.rs_change);
  const [expanded, setExpanded] = useState(false);

  return (
    <div style={{
      background: '#0d1829', border: '1px solid #1e293b', borderRadius: '12px',
      padding: '16px 20px', cursor: 'pointer',
    }} onClick={() => setExpanded(e => !e)}>

      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
        <span style={{ fontSize: '11px', fontWeight: 800, color: '#334155', width: '20px' }}>#{rank}</span>

        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '14px', fontWeight: 700, color: '#e2e8f0' }}>{entry.sector}</span>
            <span style={{ fontSize: '10px', padding: '2px 6px', borderRadius: '4px', background: 'rgba(99,102,241,0.12)', color: '#818cf8', fontFamily: 'ui-monospace,monospace' }}>{entry.etf}</span>
          </div>
          <span style={{ fontSize: '11px', color: '#475569' }}>{entry.stock_count} stocks</span>
        </div>

        {/* RS score */}
        <div style={{ textAlign: 'right', minWidth: '80px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', justifyContent: 'flex-end' }}>
            <span style={{ fontSize: '20px', fontWeight: 800, color: rsColor(entry.avg_rs), fontFamily: 'ui-monospace,monospace' }}>
              {entry.avg_rs.toFixed(0)}
            </span>
            <span style={{ fontSize: '14px', color: arrow.color, fontWeight: 700 }} title={`${entry.rs_change != null ? (entry.rs_change >= 0 ? '+' : '') + entry.rs_change.toFixed(1) : 'N/A'} vs 5d ago`}>
              {arrow.icon}
            </span>
          </div>
          <div style={{ fontSize: '10px', color: rsColor(entry.avg_rs) }}>{rsLabel(entry.avg_rs)}</div>
        </div>
      </div>

      {/* RS bar */}
      <RsBar score={entry.avg_rs} />

      {/* Leading / lagging counts */}
      <div style={{ display: 'flex', gap: '12px', marginTop: '10px' }}>
        <span style={{ fontSize: '11px', color: '#4ade80' }}>↑ {entry.leading} leading</span>
        <span style={{ fontSize: '11px', color: '#94a3b8' }}>→ {entry.stock_count - entry.leading - entry.lagging} in-line</span>
        <span style={{ fontSize: '11px', color: '#f87171' }}>↓ {entry.lagging} lagging</span>
        {entry.rs_change != null && (
          <span style={{ fontSize: '11px', color: arrow.color, marginLeft: 'auto' }}>
            {entry.rs_change >= 0 ? '+' : ''}{entry.rs_change.toFixed(1)} vs 5d ago
          </span>
        )}
      </div>

      {/* Top stocks (expanded) */}
      {expanded && (
        <div style={{ marginTop: '14px', paddingTop: '14px', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ fontSize: '11px', color: '#64748b', fontWeight: 700, textTransform: 'uppercase', marginBottom: '8px' }}>
            Top by RS
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(100px, 1fr))', gap: '6px' }}>
            {entry.top_stocks.map(s => <StockPill key={s.symbol} s={s} />)}
          </div>
          {entry.bottom_stocks.length > 0 && (
            <>
              <div style={{ fontSize: '11px', color: '#64748b', fontWeight: 700, textTransform: 'uppercase', margin: '12px 0 8px' }}>
                Laggards
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(100px, 1fr))', gap: '6px' }}>
                {entry.bottom_stocks.map(s => <StockPill key={s.symbol} s={s} />)}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function SectorRotationPage() {
  const [report, setReport] = useState<import('@/lib/api').SectorRotationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [market, setMarket] = useState<'US' | 'HK'>('US');

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.sectorRotation(market)
      .then(setReport)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [market]);

  const sectors = report?.sectors ?? [];
  const top3 = sectors.slice(0, 3);
  const bottom3 = sectors.slice(-3).reverse();

  return (
    <div style={{ maxWidth: '1100px', margin: '0 auto', paddingBottom: '60px' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '24px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', margin: 0 }}>Sector Rotation</h1>
          <div style={{ fontSize: '12px', color: '#475569', marginTop: '4px' }}>
            Sectors ranked by average relative strength vs sector ETF · {report ? `as of ${report.as_of}` : ''}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '6px' }}>
          {(['US', 'HK'] as const).map(m => (
            <button key={m} onClick={() => setMarket(m)} style={{
              padding: '6px 16px', borderRadius: '6px', border: '1px solid #1e293b',
              background: market === m ? 'rgba(129,140,248,0.15)' : 'transparent',
              color: market === m ? '#818cf8' : '#475569',
              fontSize: '12px', fontWeight: market === m ? 700 : 400, cursor: 'pointer',
            }}>{m}</button>
          ))}
        </div>
      </div>

      {loading && (
        <div style={{ textAlign: 'center', padding: '60px', color: '#475569' }}>Loading sector data…</div>
      )}

      {error && (
        <div style={{ padding: '12px 16px', borderRadius: '8px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#f87171', fontSize: '13px', marginBottom: '16px' }}>
          {error}
        </div>
      )}

      {!loading && !error && sectors.length > 0 && (
        <>
          {/* Summary strip */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '24px' }}>

            <div style={{ background: '#0d1829', border: '1px solid rgba(74,222,128,0.2)', borderRadius: '12px', padding: '16px 20px' }}>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#4ade80', textTransform: 'uppercase', marginBottom: '10px' }}>Leading Sectors</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {top3.map((s, i) => {
                  const arrow = momentumArrow(s.rs_change);
                  return (
                    <div key={s.sector} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{ fontSize: '10px', color: '#334155', width: '16px' }}>#{i + 1}</span>
                      <span style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', flex: 1 }}>{s.sector}</span>
                      <span style={{ fontSize: '11px', color: arrow.color }}>{arrow.icon}</span>
                      <span style={{ fontSize: '13px', fontWeight: 800, color: rsColor(s.avg_rs), fontFamily: 'ui-monospace,monospace' }}>{s.avg_rs.toFixed(0)}</span>
                    </div>
                  );
                })}
              </div>
            </div>

            <div style={{ background: '#0d1829', border: '1px solid rgba(248,113,113,0.2)', borderRadius: '12px', padding: '16px 20px' }}>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#f87171', textTransform: 'uppercase', marginBottom: '10px' }}>Lagging Sectors</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {bottom3.map((s, i) => {
                  const arrow = momentumArrow(s.rs_change);
                  return (
                    <div key={s.sector} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{ fontSize: '10px', color: '#334155', width: '16px' }}>#{sectors.length - bottom3.length + i + 1}</span>
                      <span style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', flex: 1 }}>{s.sector}</span>
                      <span style={{ fontSize: '11px', color: arrow.color }}>{arrow.icon}</span>
                      <span style={{ fontSize: '13px', fontWeight: 800, color: rsColor(s.avg_rs), fontFamily: 'ui-monospace,monospace' }}>{s.avg_rs.toFixed(0)}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Legend */}
          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '11px', color: '#475569' }}>RS score: avg relative strength vs sector ETF (0–100) · 50 = in-line with ETF</span>
            <span style={{ fontSize: '11px', color: '#475569' }}>↑↑ ↑ → ↓ ↓↓ = momentum vs 5 days ago · click card to expand stocks</span>
          </div>

          {/* Sector cards */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {sectors.map((entry, i) => (
              <SectorCard key={entry.sector} entry={entry} rank={i + 1} />
            ))}
          </div>
        </>
      )}

      {!loading && !error && sectors.length === 0 && (
        <div style={{ textAlign: 'center', padding: '80px 0', color: '#334155' }}>
          <div style={{ fontSize: '14px' }}>No sector data available — rankings must be computed first.</div>
          <div style={{ fontSize: '12px', marginTop: '8px' }}>
            <Link href="/rankings" style={{ color: '#818cf8' }}>Go to Rankings</Link> and wait for the next scheduled refresh.
          </div>
        </div>
      )}
    </div>
  );
}
