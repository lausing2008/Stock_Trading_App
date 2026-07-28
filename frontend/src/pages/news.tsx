import { useState } from 'react';
import useSWR from 'swr';
import { api, type RealtimeNewsItem } from '@/lib/api';

const SOURCE_LABEL: Record<RealtimeNewsItem['source'], string> = {
  pr_newswire: 'PR Newswire',
  businesswire: 'Business Wire',
  sec_edgar: 'SEC EDGAR',
  alpaca: 'Alpaca',
};

const SOURCE_COLOR: Record<RealtimeNewsItem['source'], string> = {
  pr_newswire: '#38bdf8',
  businesswire: '#a78bfa',
  sec_edgar: '#f59e0b',
  alpaca: '#4ade80',
};

const CATEGORY_LABEL: Record<string, string> = {
  earnings: 'Earnings', fda: 'FDA', ma: 'M&A', analyst: 'Analyst', macro: 'Macro', other: 'Other',
};

function sentimentColor(label: string | null): string {
  if (label === 'positive') return '#4ade80';
  if (label === 'negative') return '#f87171';
  return '#64748b';
}

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export default function NewsPage() {
  const [symbolFilter, setSymbolFilter] = useState('');
  const [materialOnly, setMaterialOnly] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<'all' | RealtimeNewsItem['source']>('all');

  const activeSymbol = symbolFilter.trim().toUpperCase() || undefined;
  const { data, isLoading, error } = useSWR(
    ['news', activeSymbol],
    () => api.news({ symbol: activeSymbol, limit: 100, sinceHours: 48 }),
    { refreshInterval: 60_000 }
  );

  const items = (data ?? []).filter(it => {
    if (materialOnly && !it.is_material) return false;
    if (sourceFilter !== 'all' && it.source !== sourceFilter) return false;
    return true;
  });

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', paddingTop: '8px' }}>
      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 800, color: '#f1f5f9' }}>
          Real-Time News
        </h1>
        <div style={{ fontSize: '12px', color: '#475569', marginTop: '3px' }}>
          Live company headlines from PR Newswire, Business Wire, SEC EDGAR filings, and Alpaca
          (if configured) — classified by sentiment and materiality. Material headlines also feed
          a short-lived hot-news gate on the AI Signal engine (see Settings for Alpaca setup).
        </div>
      </div>

      <div style={{
        display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center',
        marginBottom: '16px', padding: '12px 14px', borderRadius: '10px',
        border: '1px solid #1e293b', background: 'rgba(15,23,42,0.6)',
      }}>
        <input
          value={symbolFilter}
          onChange={e => setSymbolFilter(e.target.value)}
          placeholder="Filter by symbol (e.g. AAPL)"
          style={{
            background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px',
            padding: '7px 10px', fontSize: '13px', color: '#e2e8f0', outline: 'none', width: '200px',
          }}
        />
        <button
          onClick={() => setMaterialOnly(v => !v)}
          style={{
            padding: '7px 14px', borderRadius: '8px', fontSize: '12px', fontWeight: 600,
            cursor: 'pointer', border: materialOnly ? '1px solid rgba(56,189,248,0.5)' : '1px solid #1e293b',
            background: materialOnly ? 'rgba(56,189,248,0.15)' : 'transparent',
            color: materialOnly ? '#7dd3fc' : '#64748b',
          }}
        >
          Material only
        </button>
        <select
          value={sourceFilter}
          onChange={e => setSourceFilter(e.target.value as typeof sourceFilter)}
          style={{
            background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px',
            padding: '7px 10px', fontSize: '12px', color: '#e2e8f0', outline: 'none',
          }}
        >
          <option value="all">All sources</option>
          {(Object.keys(SOURCE_LABEL) as RealtimeNewsItem['source'][]).map(s => (
            <option key={s} value={s}>{SOURCE_LABEL[s]}</option>
          ))}
        </select>
        <div style={{ fontSize: '11px', color: '#334155', marginLeft: 'auto' }}>
          Refreshes every 60s
        </div>
      </div>

      {isLoading && (
        <div style={{ padding: '30px', textAlign: 'center', color: '#475569', fontSize: '13px' }}>
          Loading news…
        </div>
      )}
      {error && (
        <div style={{ padding: '30px', textAlign: 'center', color: '#f87171', fontSize: '13px' }}>
          Failed to load news.
        </div>
      )}
      {!isLoading && !error && items.length === 0 && (
        <div style={{ padding: '30px', textAlign: 'center', color: '#475569', fontSize: '13px' }}>
          No headlines match the current filters in the last 48 hours.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {items.map(it => (
          <a
            key={it.id}
            href={it.url ?? undefined}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: 'block', padding: '12px 14px', borderRadius: '10px',
              border: it.is_material ? '1px solid rgba(56,189,248,0.3)' : '1px solid #1e293b',
              background: 'rgba(15,23,42,0.7)', textDecoration: 'none', cursor: it.url ? 'pointer' : 'default',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px', flexWrap: 'wrap' }}>
              {it.symbol && (
                <span style={{
                  fontSize: '11px', fontWeight: 700, color: '#e2e8f0',
                  padding: '2px 8px', borderRadius: '5px', background: 'rgba(99,102,241,0.15)',
                }}>
                  {it.symbol}
                </span>
              )}
              <span style={{
                fontSize: '10px', fontWeight: 600, color: SOURCE_COLOR[it.source],
                padding: '1px 7px', borderRadius: '4px', background: `${SOURCE_COLOR[it.source]}18`,
              }}>
                {SOURCE_LABEL[it.source]}
              </span>
              {it.is_material && (
                <span style={{
                  fontSize: '10px', fontWeight: 700, color: '#7dd3fc',
                  padding: '1px 7px', borderRadius: '4px', background: 'rgba(56,189,248,0.15)',
                }}>
                  MATERIAL
                </span>
              )}
              {it.category && it.category !== 'other' && (
                <span style={{ fontSize: '10px', color: '#94a3b8' }}>{CATEGORY_LABEL[it.category] ?? it.category}</span>
              )}
              {it.sentiment_label && (
                <span style={{ fontSize: '10px', color: sentimentColor(it.sentiment_label) }}>
                  ● {it.sentiment_label}
                </span>
              )}
              <span style={{ fontSize: '10px', color: '#334155', marginLeft: 'auto' }}>{timeAgo(it.published_at)}</span>
            </div>
            <div style={{ fontSize: '13px', color: '#e2e8f0', lineHeight: 1.4 }}>{it.headline}</div>
          </a>
        ))}
      </div>
    </div>
  );
}
