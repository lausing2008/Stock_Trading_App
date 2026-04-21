import type { NewsItem } from '@/lib/api';

const SENTIMENT: Record<string, { color: string; bg: string; border: string }> = {
  bullish: { color: '#4ade80', bg: 'rgba(34,197,94,0.1)',    border: 'rgba(34,197,94,0.25)'    },
  bearish: { color: '#f87171', bg: 'rgba(239,68,68,0.1)',    border: 'rgba(239,68,68,0.25)'    },
  neutral: { color: '#94a3b8', bg: 'rgba(148,163,184,0.08)', border: 'rgba(148,163,184,0.15)'  },
};

export default function NewsCard({ item }: { item: NewsItem }) {
  const age = Math.floor((Date.now() / 1000 - item.published_at) / 3600);
  const ageLabel = age < 1 ? 'just now' : age < 24 ? `${age}h ago` : `${Math.floor(age / 24)}d ago`;
  const s = SENTIMENT[item.sentiment_label] ?? SENTIMENT.neutral;

  return (
    <a
      href={item.url}
      target="_blank"
      rel="noopener noreferrer"
      style={{
        display: 'block', borderRadius: '10px',
        border: '1px solid #1e293b', background: '#0f172a',
        padding: '12px', textDecoration: 'none', transition: 'border-color 0.15s',
      }}
      className="news-card"
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px' }}>
        {item.thumbnail && (
          <img
            src={item.thumbnail}
            alt=""
            style={{ width: '56px', height: '56px', borderRadius: '6px', objectFit: 'cover', flexShrink: 0, opacity: 0.85 }}
          />
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{
            fontSize: '13px', color: '#cbd5e1', lineHeight: 1.45, margin: '0 0 8px',
            display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
          }}>
            {item.title}
          </p>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ fontSize: '11px', color: '#475569' }}>{item.source}</span>
            <span style={{ fontSize: '11px', color: '#334155' }}>·</span>
            <span style={{ fontSize: '11px', color: '#475569' }}>{ageLabel}</span>
            <span style={{
              marginLeft: 'auto', fontSize: '10px', fontWeight: 700,
              padding: '2px 7px', borderRadius: '4px',
              color: s.color, background: s.bg, border: `1px solid ${s.border}`,
              letterSpacing: '0.03em', textTransform: 'capitalize',
            }}>
              {item.sentiment_label}
            </span>
          </div>
        </div>
      </div>
      <style>{`.news-card:hover { border-color: #334155 !important; }`}</style>
    </a>
  );
}
