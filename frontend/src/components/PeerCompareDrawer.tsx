import { useState } from 'react';
import type { RankingRow, LatestPrice } from '@/lib/api';

type Props = {
  rows: RankingRow[];
  prices?: Record<string, LatestPrice>;
  onClose: () => void;
};

type Metric = {
  key: keyof RankingRow | 'price' | 'upside';
  label: string;
  higherBetter: boolean;
  format: (v: number | null, row: RankingRow, prices?: Record<string, LatestPrice>) => string;
};

const TECH_METRICS: Metric[] = [
  {
    key: 'price', label: 'Price', higherBetter: false,
    format: (_, row, prices) => {
      const p = prices?.[row.symbol]?.price;
      return p != null ? `$${p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';
    },
  },
  { key: 'score', label: 'K-Score', higherBetter: true, format: (v) => v != null ? v.toFixed(1) : '—' },
  { key: 'technical', label: 'Technical', higherBetter: true, format: (v) => v != null ? v.toFixed(0) : '—' },
  { key: 'momentum', label: 'Momentum', higherBetter: true, format: (v) => v != null ? v.toFixed(0) : '—' },
  { key: 'value', label: 'Value', higherBetter: true, format: (v) => v != null ? v.toFixed(0) : '—' },
  { key: 'growth', label: 'Growth', higherBetter: true, format: (v) => v != null ? v.toFixed(0) : '—' },
  { key: 'volatility', label: 'Volatility', higherBetter: true, format: (v) => v != null ? v.toFixed(0) : '—' },
  { key: 'relative_strength', label: 'Rel. Strength', higherBetter: true, format: (v) => v != null ? v.toFixed(0) : '—' },
  {
    key: 'upside', label: 'Fair Value', higherBetter: true,
    format: (_, row, prices) => {
      if (row.fair_price == null) return '—';
      const p = prices?.[row.symbol]?.price;
      if (p == null) return `$${row.fair_price.toFixed(2)}`;
      const pct = ((row.fair_price - p) / p) * 100;
      return `$${row.fair_price.toFixed(2)} (${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%)`;
    },
  },
];

const VAL_METRICS: Metric[] = [
  {
    key: 'price', label: 'Price', higherBetter: false,
    format: (_, row, prices) => {
      const p = prices?.[row.symbol]?.price;
      return p != null ? `$${p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';
    },
  },
  {
    key: 'market_cap', label: 'Mkt Cap', higherBetter: false,
    format: (v) => {
      if (v == null) return '—';
      if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
      if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
      return `$${(v / 1e6).toFixed(0)}M`;
    },
  },
  { key: 'trailing_pe', label: 'P/E (TTM)', higherBetter: false, format: (v) => v != null ? v.toFixed(1) : '—' },
  { key: 'forward_pe', label: 'Fwd P/E', higherBetter: false, format: (v) => v != null ? v.toFixed(1) : '—' },
  { key: 'peg_ratio', label: 'PEG', higherBetter: false, format: (v) => v != null ? v.toFixed(2) : '—' },
  { key: 'price_to_book', label: 'P/B', higherBetter: false, format: (v) => v != null ? v.toFixed(2) : '—' },
  { key: 'revenue_growth', label: 'Rev Growth', higherBetter: true, format: (v) => v != null ? `${(v * 100).toFixed(1)}%` : '—' },
  { key: 'earnings_growth', label: 'EPS Growth', higherBetter: true, format: (v) => v != null ? `${(v * 100).toFixed(1)}%` : '—' },
  { key: 'debt_to_equity', label: 'D/E', higherBetter: false, format: (v) => v != null ? v.toFixed(2) : '—' },
];

function getValue(metric: Metric, row: RankingRow, prices?: Record<string, LatestPrice>): number | null {
  if (metric.key === 'price') return prices?.[row.symbol]?.price ?? null;
  if (metric.key === 'upside') {
    const p = prices?.[row.symbol]?.price;
    if (p == null || row.fair_price == null) return null;
    return ((row.fair_price - p) / p) * 100;
  }
  const v = row[metric.key as keyof RankingRow];
  return typeof v === 'number' ? v : null;
}

function cellColor(value: number | null, values: (number | null)[], higherBetter: boolean): string {
  const nums = values.filter((v): v is number => v != null);
  if (nums.length < 2 || value == null) return '#94a3b8';
  const sorted = [...nums].sort((a, b) => a - b);
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  if (max === min) return '#94a3b8';
  const rank = (value - min) / (max - min);
  const rankNorm = higherBetter ? rank : 1 - rank;
  if (rankNorm >= 0.75) return '#4ade80';
  if (rankNorm <= 0.25) return '#f87171';
  return '#94a3b8';
}

function bgColor(color: string): string {
  if (color === '#4ade80') return 'rgba(74,222,128,0.08)';
  if (color === '#f87171') return 'rgba(248,113,113,0.06)';
  return 'transparent';
}

function MetricTable({ metrics, rows, prices }: { metrics: Metric[]; rows: RankingRow[]; prices?: Record<string, LatestPrice> }) {
  return (
    <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: rows.length * 150 + 130 }}>
      <thead>
        <tr>
          <th style={{ textAlign: 'left', padding: '6px 10px', fontSize: 11, color: '#475569', borderBottom: '1px solid #1e293b', width: 120 }}>
            Metric
          </th>
          {rows.map(row => (
            <th key={row.symbol} style={{
              textAlign: 'center', padding: '6px 10px',
              fontSize: 12, fontWeight: 700, color: '#818cf8',
              borderBottom: '1px solid #1e293b', minWidth: 140,
            }}>
              <a href={`/stock/${row.symbol}`} style={{ color: '#818cf8', textDecoration: 'none' }} target="_blank" rel="noreferrer">
                {row.symbol}
              </a>
              <div style={{ fontSize: 10, fontWeight: 400, color: '#475569', marginTop: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 140 }}>
                {row.name}
              </div>
              {prices?.[row.symbol]?.change_pct != null && (() => {
                const chg = prices![row.symbol]!.change_pct!;
                return (
                  <div style={{ fontSize: 10, color: chg >= 0 ? '#4ade80' : '#f87171', marginTop: 1 }}>
                    {chg >= 0 ? '+' : ''}{chg.toFixed(2)}%
                  </div>
                );
              })()}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {metrics.map((metric, mi) => {
          const values = rows.map(row => getValue(metric, row, prices));
          return (
            <tr key={metric.key} style={{ background: mi % 2 === 0 ? 'rgba(15,23,42,0.5)' : 'transparent' }}>
              <td style={{ padding: '8px 10px', fontSize: 11, color: '#64748b', borderBottom: '1px solid #0f172a', fontWeight: 500 }}>
                {metric.label}
              </td>
              {rows.map((row, ri) => {
                const v = values[ri];
                const color = metric.key === 'price' ? '#94a3b8' : cellColor(v, values, metric.higherBetter);
                const bg = metric.key === 'price' ? 'transparent' : bgColor(color);
                return (
                  <td key={row.symbol} style={{
                    padding: '8px 10px',
                    textAlign: 'center',
                    fontSize: 13,
                    fontWeight: metric.key === 'score' ? 700 : 600,
                    color,
                    background: bg,
                    borderBottom: '1px solid #0f172a',
                    borderLeft: '1px solid #0f172a',
                  }}>
                    {metric.format(v, row, prices)}
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function PeerCompareDrawer({ rows, prices, onClose }: Props) {
  const [tab, setTab] = useState<'technical' | 'valuation'>('technical');
  const tabStyle = (active: boolean) => ({
    padding: '5px 14px', fontSize: 12, fontWeight: 600, cursor: 'pointer',
    borderRadius: 6, border: 'none',
    background: active ? '#6366f120' : 'transparent',
    color: active ? '#818cf8' : '#475569',
  });

  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(2,6,23,0.75)', display: 'flex', justifyContent: 'flex-end' }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{
        width: Math.min(rows.length * 180 + 140, window.innerWidth - 24),
        maxWidth: '95vw', height: '100vh',
        background: '#0f172a', borderLeft: '1px solid #1e293b',
        display: 'flex', flexDirection: 'column', overflowY: 'auto',
      }}>
        <div style={{
          padding: '16px 20px', borderBottom: '1px solid #1e293b',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
          background: '#0a1628', position: 'sticky', top: 0, zIndex: 2,
        }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0' }}>Peer Comparison</div>
            <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>
              {rows.map(r => r.symbol).join(' · ')}
              {rows[0]?.sector && <span style={{ marginLeft: 8, color: '#334155' }}>· {rows[0].sector}</span>}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div style={{ display: 'flex', gap: 4, background: '#0f172a', borderRadius: 8, padding: 3, border: '1px solid #1e293b' }}>
              <button style={tabStyle(tab === 'technical')} onClick={() => setTab('technical')}>Technical</button>
              <button style={tabStyle(tab === 'valuation')} onClick={() => setTab('valuation')}>Valuation</button>
            </div>
            <button
              onClick={onClose}
              style={{ padding: '4px 12px', borderRadius: 6, fontSize: 13, border: '1px solid #1e293b', background: 'transparent', color: '#64748b', cursor: 'pointer' }}
            >✕ Close</button>
          </div>
        </div>

        <div style={{ padding: '16px 20px', overflowX: 'auto' }}>
          <MetricTable metrics={tab === 'technical' ? TECH_METRICS : VAL_METRICS} rows={rows} prices={prices} />
          <div style={{ marginTop: 16, display: 'flex', gap: 16, fontSize: 10, color: '#475569' }}>
            <span style={{ color: '#4ade80' }}>■</span> Best in group &nbsp;
            <span style={{ color: '#f87171' }}>■</span> Worst in group &nbsp;
            <span style={{ color: '#64748b' }}>■</span> Middle
          </div>
        </div>
      </div>
    </div>
  );
}
