import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type CalendarEvent } from '@/lib/api';

type EventType = 'all' | 'earnings' | 'dividend' | 'macro';
type MacroSubtype = 'fomc' | 'cpi' | 'nfp' | 'pce' | 'gdp';
const MACRO_SUBTYPES = new Set<string>(['fomc', 'cpi', 'nfp', 'pce', 'gdp']);

const EVENT_META: Record<string, { label: string; color: string; bg: string; dot: string }> = {
  earnings: { label: 'Earnings',   color: '#818cf8', bg: 'rgba(129,140,248,0.12)', dot: '#818cf8' },
  dividend: { label: 'Ex-Div',     color: '#4ade80', bg: 'rgba(74,222,128,0.12)',  dot: '#4ade80' },
  fomc:     { label: 'FOMC',       color: '#f59e0b', bg: 'rgba(245,158,11,0.12)',  dot: '#f59e0b' },
  cpi:      { label: 'CPI',        color: '#fb923c', bg: 'rgba(251,146,60,0.12)',  dot: '#fb923c' },
  nfp:      { label: 'Jobs (NFP)', color: '#38bdf8', bg: 'rgba(56,189,248,0.12)',  dot: '#38bdf8' },
  pce:      { label: 'PCE',        color: '#a78bfa', bg: 'rgba(167,139,250,0.12)', dot: '#a78bfa' },
  gdp:      { label: 'GDP',        color: '#34d399', bg: 'rgba(52,211,153,0.12)',  dot: '#34d399' },
};

function urgencyColor(days: number) {
  if (days <= 3) return '#ef4444';
  if (days <= 7) return '#f97316';
  if (days <= 14) return '#facc15';
  return '#64748b';
}
function urgencyBg(days: number) {
  if (days <= 3) return 'rgba(239,68,68,0.12)';
  if (days <= 7) return 'rgba(249,115,22,0.1)';
  if (days <= 14) return 'rgba(250,204,21,0.08)';
  return 'rgba(100,116,139,0.1)';
}
function fmtCap(v: number | null | undefined) {
  if (v == null) return '—';
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString()}`;
}
function fmtPct(v: number | null | undefined) {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
}
function fmtYield(v: number | null | undefined) {
  if (v == null) return '—';
  return (v * 100).toFixed(2) + '%';
}
function daysLabel(d: number) {
  if (d === 0) return 'Today';
  if (d === 1) return 'Tomorrow';
  return `${d}d`;
}
function weekGroup(d: number): string {
  if (d === 0) return 'Today';
  if (d === 1) return 'Tomorrow';
  if (d <= 7) return 'This Week';
  if (d <= 14) return 'Next Week';
  if (d <= 21) return 'In 2–3 Weeks';
  return 'In 3+ Weeks';
}

function TypeBadge({ type }: { type: string }) {
  const m = EVENT_META[type] ?? { label: type, color: '#94a3b8', bg: 'rgba(148,163,184,0.1)', dot: '#94a3b8' };
  return (
    <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4, background: m.bg, color: m.color, letterSpacing: '0.05em', textTransform: 'uppercase' }}>
      {m.label}
    </span>
  );
}

function EventCard({ ev }: { ev: CalendarEvent }) {
  const isMacro = MACRO_SUBTYPES.has(ev.type);
  const meta = EVENT_META[ev.type] ?? { color: '#94a3b8', bg: 'rgba(148,163,184,0.1)', dot: '#94a3b8' };

  return (
    <div style={{ background: '#0f172a', border: `1px solid #1e293b`, borderLeft: `3px solid ${meta.dot}`, borderRadius: '10px', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '8px' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <TypeBadge type={ev.type} />
            {ev.symbol ? (
              <Link href={`/stock/${ev.symbol}`} style={{ color: '#818cf8', fontWeight: 800, fontSize: 15, textDecoration: 'none' }}>
                {ev.symbol}
              </Link>
            ) : (
              <span style={{ color: '#e2e8f0', fontWeight: 700, fontSize: 14 }}>{ev.title}</span>
            )}
          </div>
          {ev.symbol && <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 3 }}>{ev.name}</div>}
          {ev.sector && <div style={{ fontSize: 10, color: '#475569', marginTop: 1 }}>{ev.sector} · {ev.market}</div>}
          {isMacro && ev.description && <div style={{ fontSize: 11, color: '#64748b', marginTop: 3 }}>{ev.description}</div>}
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: urgencyColor(ev.days_to_event), background: urgencyBg(ev.days_to_event), padding: '3px 8px', borderRadius: 5 }}>
            {daysLabel(ev.days_to_event)}
          </div>
          <div style={{ fontSize: 10, color: '#475569', marginTop: 4 }}>{ev.date}</div>
        </div>
      </div>

      {/* Earnings details */}
      {ev.type === 'earnings' && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11 }}><span style={{ color: '#475569' }}>EPS est: </span><span style={{ color: '#e2e8f0', fontWeight: 700 }}>{ev.eps_estimate != null ? `$${ev.eps_estimate.toFixed(2)}` : '—'}</span></span>
          <span style={{ fontSize: 11 }}><span style={{ color: '#475569' }}>Rev growth: </span><span style={{ color: (ev.revenue_growth ?? 0) >= 0 ? '#4ade80' : '#f87171', fontWeight: 700 }}>{fmtPct(ev.revenue_growth)}</span></span>
          <span style={{ fontSize: 11 }}><span style={{ color: '#475569' }}>EPS growth: </span><span style={{ color: (ev.earnings_growth ?? 0) >= 0 ? '#4ade80' : '#f87171', fontWeight: 700 }}>{fmtPct(ev.earnings_growth)}</span></span>
          <span style={{ fontSize: 11 }}><span style={{ color: '#475569' }}>Cap: </span><span style={{ color: '#94a3b8', fontWeight: 700 }}>{fmtCap(ev.market_cap)}</span></span>
        </div>
      )}

      {/* Dividend details */}
      {ev.type === 'dividend' && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11 }}><span style={{ color: '#475569' }}>Annual rate: </span><span style={{ color: '#4ade80', fontWeight: 700 }}>{ev.dividend_rate != null ? `$${ev.dividend_rate.toFixed(2)}` : '—'}</span></span>
          <span style={{ fontSize: 11 }}><span style={{ color: '#475569' }}>Yield: </span><span style={{ color: '#4ade80', fontWeight: 700 }}>{fmtYield(ev.dividend_yield)}</span></span>
          <span style={{ fontSize: 11 }}><span style={{ color: '#475569' }}>Cap: </span><span style={{ color: '#94a3b8', fontWeight: 700 }}>{fmtCap(ev.market_cap)}</span></span>
        </div>
      )}
    </div>
  );
}

export default function EventsCalendarPage() {
  const [daysAhead, setDaysAhead] = useState(45);
  const [tab, setTab] = useState<EventType>('all');
  const [market, setMarket] = useState<'All' | 'US' | 'HK'>('All');
  const [search, setSearch] = useState('');

  const { data, error, isLoading } = useSWR<CalendarEvent[]>(
    `events-cal-${daysAhead}`,
    () => api.eventsCalendar(daysAhead),
    { revalidateOnFocus: false },
  );

  const filtered = useMemo(() => {
    let items = data ?? [];
    if (tab === 'earnings') items = items.filter(e => e.type === 'earnings');
    else if (tab === 'dividend') items = items.filter(e => e.type === 'dividend');
    else if (tab === 'macro') items = items.filter(e => MACRO_SUBTYPES.has(e.type));
    if (market !== 'All') items = items.filter(e => !e.market || e.market === market);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      items = items.filter(e =>
        (e.symbol ?? '').toLowerCase().includes(q) ||
        (e.name ?? '').toLowerCase().includes(q) ||
        (e.title ?? '').toLowerCase().includes(q)
      );
    }
    return items;
  }, [data, tab, market, search]);

  const grouped = useMemo(() => {
    const groups: { label: string; items: CalendarEvent[] }[] = [];
    for (const ev of filtered) {
      const label = weekGroup(ev.days_to_event);
      const last = groups[groups.length - 1];
      if (last && last.label === label) last.items.push(ev);
      else groups.push({ label, items: [ev] });
    }
    return groups;
  }, [filtered]);

  // Counts per tab
  const allData = data ?? [];
  const counts = {
    all:      allData.length,
    earnings: allData.filter(e => e.type === 'earnings').length,
    dividend: allData.filter(e => e.type === 'dividend').length,
    macro:    allData.filter(e => MACRO_SUBTYPES.has(e.type)).length,
  };

  const TABS: { key: EventType; label: string }[] = [
    { key: 'all',      label: 'All Events' },
    { key: 'earnings', label: 'Earnings' },
    { key: 'dividend', label: 'Ex-Dividends' },
    { key: 'macro',    label: 'Macro' },
  ];

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 20, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 800, color: '#e2e8f0', marginBottom: 4 }}>Events Calendar</h1>
          <p style={{ fontSize: 12, color: '#475569' }}>Earnings, ex-dividends, and macro events — next {daysAhead} days</p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {[14, 30, 45, 90].map(d => (
            <button key={d} onClick={() => setDaysAhead(d)}
              style={{ padding: '5px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid #1e293b', fontWeight: daysAhead === d ? 700 : 400, background: daysAhead === d ? '#4f46e5' : 'transparent', color: daysAhead === d ? '#fff' : '#64748b' }}
            >{d}d</button>
          ))}
        </div>
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
        {Object.entries(EVENT_META).map(([k, m]) => (
          <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: '#64748b' }}>
            <div style={{ width: 8, height: 8, borderRadius: '50%', background: m.dot }} />
            {m.label}
          </div>
        ))}
      </div>

      {/* Tabs + Controls */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 18, flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ display: 'flex', background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, overflow: 'hidden' }}>
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              style={{ padding: '6px 14px', fontSize: 12, cursor: 'pointer', border: 'none', fontWeight: tab === t.key ? 700 : 400, background: tab === t.key ? '#1e293b' : 'transparent', color: tab === t.key ? '#e2e8f0' : '#64748b', whiteSpace: 'nowrap' }}
            >
              {t.label}
              {counts[t.key] > 0 && <span style={{ marginLeft: 5, fontSize: 10, background: tab === t.key ? '#4f46e5' : '#334155', color: tab === t.key ? '#fff' : '#94a3b8', borderRadius: 10, padding: '1px 5px' }}>{counts[t.key]}</span>}
            </button>
          ))}
        </div>
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search…"
          style={{ flex: '1 1 140px', minWidth: 120, padding: '7px 11px', borderRadius: 6, border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: 12, outline: 'none' }}
        />
        {tab !== 'macro' && (
          <div style={{ display: 'flex', gap: 6 }}>
            {(['All', 'US', 'HK'] as const).map(m => (
              <button key={m} onClick={() => setMarket(m)}
                style={{ padding: '5px 10px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid #1e293b', background: market === m ? '#334155' : 'transparent', color: market === m ? '#e2e8f0' : '#64748b' }}
              >{m}</button>
            ))}
          </div>
        )}
      </div>

      {isLoading && <div style={{ color: '#475569', fontSize: 13, padding: '40px 0', textAlign: 'center' }}>Loading events…</div>}
      {error && <div style={{ color: '#f87171', fontSize: 13 }}>Failed to load events.</div>}
      {!isLoading && !error && filtered.length === 0 && (
        <div style={{ color: '#475569', fontSize: 13, padding: '40px 0', textAlign: 'center' }}>
          No events found in the next {daysAhead} days.
          {tab === 'dividend' && <><br /><span style={{ fontSize: 11 }}>Ex-dividend dates populate after visiting individual stock pages to refresh their fundamentals cache.</span></>}
        </div>
      )}

      {grouped.map(group => (
        <div key={group.label} style={{ marginBottom: 24 }}>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#475569', marginBottom: 10, paddingBottom: 6, borderBottom: '1px solid #1e293b' }}>
            {group.label} ({group.items.length})
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 8 }}>
            {group.items.map((ev, i) => <EventCard key={`${ev.type}-${ev.symbol ?? ev.title}-${i}`} ev={ev} />)}
          </div>
        </div>
      ))}
    </div>
  );
}
