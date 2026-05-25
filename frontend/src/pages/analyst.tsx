import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type AnalystRating } from '@/lib/api';

const ACTION_COLORS: Record<string, string> = {
  upgrade: '#4ade80',
  downgrade: '#f87171',
  init: '#818cf8',
  reiterate: '#94a3b8',
  maintain: '#64748b',
};

const ACTION_BG: Record<string, string> = {
  upgrade: 'rgba(74,222,128,0.12)',
  downgrade: 'rgba(248,113,113,0.12)',
  init: 'rgba(129,140,248,0.12)',
  reiterate: 'rgba(148,163,184,0.1)',
  maintain: 'rgba(100,116,139,0.1)',
};

// yfinance Action column uses short codes: "up", "down", "init", "reit", "main"
function actionKey(action: string): string {
  const a = action.toLowerCase().trim();
  if (a === 'up' || a.includes('upgrade')) return 'upgrade';
  if (a === 'down' || a.includes('downgrade')) return 'downgrade';
  if (a === 'init' || a.includes('init') || a.includes('coverage') || a.includes('start')) return 'init';
  if (a === 'reit' || a.includes('reiterate')) return 'reiterate';
  return 'maintain';
}

function actionLabel(action: string): string {
  const k = actionKey(action);
  if (k === 'upgrade') return '▲ Upgrade';
  if (k === 'downgrade') return '▼ Downgrade';
  if (k === 'init') return '◆ Initiated';
  if (k === 'reiterate') return '→ Reiterated';
  return '· ' + action;
}

function gradeColor(grade: string): string {
  const g = grade.toLowerCase();
  if (g.includes('buy') || g.includes('outperform') || g.includes('overweight') || g.includes('positive')) return '#4ade80';
  if (g.includes('sell') || g.includes('underperform') || g.includes('underweight') || g.includes('negative')) return '#f87171';
  if (g.includes('hold') || g.includes('neutral') || g.includes('equal')) return '#facc15';
  return '#94a3b8';
}

function relDate(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const d = Math.floor(diff / 86_400_000);
  if (d === 0) return 'Today';
  if (d === 1) return 'Yesterday';
  if (d < 7) return `${d}d ago`;
  return iso.slice(5);
}

export default function AnalystPage() {
  const [days, setDays] = useState(30);
  const [market, setMarket] = useState<'All' | 'US' | 'HK'>('All');
  const [search, setSearch] = useState('');
  const [actionFilter, setActionFilter] = useState<'all' | 'upgrade' | 'downgrade' | 'init'>('all');

  const { data, error, isLoading } = useSWR<AnalystRating[]>(
    `analyst-${days}`,
    () => api.analystRatings(days),
    { revalidateOnFocus: false },
  );

  const rows = useMemo(() => {
    let items = data ?? [];
    if (market !== 'All') items = items.filter(i => i.market === market);
    if (actionFilter !== 'all') items = items.filter(i => actionKey(i.action) === actionFilter);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      items = items.filter(i =>
        i.symbol.toLowerCase().includes(q) ||
        i.name.toLowerCase().includes(q) ||
        i.firm.toLowerCase().includes(q)
      );
    }
    return items;
  }, [data, market, actionFilter, search]);

  const counts = useMemo(() => {
    const all = data ?? [];
    return {
      upgrade: all.filter(i => actionKey(i.action) === 'upgrade').length,
      downgrade: all.filter(i => actionKey(i.action) === 'downgrade').length,
      init: all.filter(i => actionKey(i.action) === 'init').length,
    };
  }, [data]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>Analyst Ratings</h1>
          <p style={{ fontSize: '12px', color: '#475569' }}>Recent upgrades, downgrades, and initiations from Wall Street firms</p>
        </div>
        <div style={{ display: 'flex', gap: '6px' }}>
          {[7, 14, 30, 90].map(d => (
            <button key={d} onClick={() => setDays(d)}
              style={{ padding: '5px 12px', borderRadius: '6px', fontSize: '12px', cursor: 'pointer', border: '1px solid #1e293b', fontWeight: days === d ? 700 : 400, background: days === d ? '#4f46e5' : 'transparent', color: days === d ? '#fff' : '#64748b' }}
            >{d}d</button>
          ))}
        </div>
      </div>

      {/* Summary chips */}
      {data && (
        <div style={{ display: 'flex', gap: '10px', marginBottom: '16px', flexWrap: 'wrap' }}>
          <div style={{ padding: '8px 14px', borderRadius: '8px', background: 'rgba(74,222,128,0.08)', border: '1px solid rgba(74,222,128,0.15)', cursor: 'pointer', userSelect: 'none' }}
            onClick={() => setActionFilter(actionFilter === 'upgrade' ? 'all' : 'upgrade')}>
            <span style={{ fontSize: '12px', color: '#4ade80', fontWeight: 700 }}>▲ {counts.upgrade} Upgrades</span>
          </div>
          <div style={{ padding: '8px 14px', borderRadius: '8px', background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.15)', cursor: 'pointer', userSelect: 'none' }}
            onClick={() => setActionFilter(actionFilter === 'downgrade' ? 'all' : 'downgrade')}>
            <span style={{ fontSize: '12px', color: '#f87171', fontWeight: 700 }}>▼ {counts.downgrade} Downgrades</span>
          </div>
          <div style={{ padding: '8px 14px', borderRadius: '8px', background: 'rgba(129,140,248,0.08)', border: '1px solid rgba(129,140,248,0.15)', cursor: 'pointer', userSelect: 'none' }}
            onClick={() => setActionFilter(actionFilter === 'init' ? 'all' : 'init')}>
            <span style={{ fontSize: '12px', color: '#818cf8', fontWeight: 700 }}>◆ {counts.init} Initiated</span>
          </div>
        </div>
      )}

      {/* Controls */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '18px', flexWrap: 'wrap' }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search symbol, name, or firm…"
          style={{ flex: '1 1 200px', padding: '7px 11px', borderRadius: '6px', border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }}
        />
        <div style={{ display: 'flex', gap: '6px' }}>
          {(['All', 'US', 'HK'] as const).map(m => (
            <button key={m} onClick={() => setMarket(m)}
              style={{ padding: '5px 12px', borderRadius: '6px', fontSize: '12px', cursor: 'pointer', border: '1px solid #1e293b', background: market === m ? '#334155' : 'transparent', color: market === m ? '#e2e8f0' : '#64748b' }}
            >{m}</button>
          ))}
        </div>
      </div>

      {isLoading && <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>Loading analyst ratings…</div>}
      {error && <div style={{ color: '#f87171', fontSize: '13px' }}>Failed to load analyst data.</div>}
      {!isLoading && !error && rows.length === 0 && (
        <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>
          No analyst actions found in the last {days} days.<br />
          <span style={{ fontSize: '11px' }}>Data available only for stocks with cached fundamentals — visit stock pages to populate.</span>
        </div>
      )}

      {rows.length > 0 && (
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
            <thead>
              <tr style={{ background: '#080f1e' }}>
                {['Date', 'Action', 'Symbol', 'Firm', 'Grade Change', 'Consensus', 'Target'].map(h => (
                  <th key={h} style={{ padding: '9px 14px', textAlign: 'left', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const ak = actionKey(r.action);
                return (
                  <tr key={i} style={{ borderBottom: '1px solid rgba(30,41,59,0.5)', transition: 'background 0.1s' }}>
                    <td style={{ padding: '10px 14px', color: '#475569', whiteSpace: 'nowrap', fontSize: '11px' }}>
                      {relDate(r.date)}
                    </td>
                    <td style={{ padding: '10px 14px', whiteSpace: 'nowrap' }}>
                      <span style={{ fontSize: '11px', fontWeight: 700, padding: '3px 8px', borderRadius: '5px', color: ACTION_COLORS[ak], background: ACTION_BG[ak] }}>
                        {actionLabel(r.action)}
                      </span>
                    </td>
                    <td style={{ padding: '10px 14px', whiteSpace: 'nowrap' }}>
                      <Link href={`/stock/${r.symbol}`} style={{ color: '#818cf8', fontWeight: 700, textDecoration: 'none' }}>
                        {r.symbol}
                      </Link>
                      <div style={{ fontSize: '10px', color: '#475569', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</div>
                    </td>
                    <td style={{ padding: '10px 14px', color: '#94a3b8', whiteSpace: 'nowrap' }}>
                      {r.firm || '—'}
                    </td>
                    <td style={{ padding: '10px 14px', whiteSpace: 'nowrap' }}>
                      {r.from_grade && r.to_grade ? (
                        <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span style={{ fontSize: '11px', color: gradeColor(r.from_grade) }}>{r.from_grade}</span>
                          <span style={{ color: '#334155', fontSize: '10px' }}>→</span>
                          <span style={{ fontSize: '11px', fontWeight: 700, color: gradeColor(r.to_grade) }}>{r.to_grade}</span>
                        </span>
                      ) : r.to_grade ? (
                        <span style={{ fontSize: '11px', fontWeight: 700, color: gradeColor(r.to_grade) }}>{r.to_grade}</span>
                      ) : (
                        <span style={{ color: '#334155' }}>—</span>
                      )}
                    </td>
                    <td style={{ padding: '10px 14px', whiteSpace: 'nowrap' }}>
                      {r.recommendation ? (
                        <span style={{ fontSize: '11px', color: gradeColor(r.recommendation), textTransform: 'capitalize' }}>{r.recommendation}</span>
                      ) : <span style={{ color: '#334155' }}>—</span>}
                    </td>
                    <td style={{ padding: '10px 14px', whiteSpace: 'nowrap', color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
                      {r.target_price != null ? `$${r.target_price.toFixed(2)}` : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div style={{ padding: '10px 16px', fontSize: '11px', color: '#334155', borderTop: '1px solid #1e293b' }}>
            {rows.length} actions · Source: Yahoo Finance analyst data · updated when stocks are viewed
          </div>
        </div>
      )}
    </div>
  );
}
