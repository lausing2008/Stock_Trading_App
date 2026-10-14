/**
 * Copilot screen: a request in words, translated into filters that are SHOWN before the results.
 *
 * The server keeps only filters the user actually asked for, drops the rest with a reason,
 * lists what the screen cannot express, and reports the searched universe — so "3 matches"
 * always arrives with "out of how many, and where".
 */
import React, { useState } from 'react';
import Link from 'next/link';
import { newRequestId, screenCopilot, type CopilotScreenResult } from '@/lib/copilot';

const chip = (bg: string, fg: string): React.CSSProperties => ({
  fontSize: '11px', padding: '2px 8px', borderRadius: '999px', background: bg, color: fg,
  border: `1px solid ${fg}33`, whiteSpace: 'nowrap',
});

/** The result view alone — exported for tests. */
export function ScreenResultView({ r }: { r: CopilotScreenResult }) {
  const cov = r.coverage;
  return (
    <div style={{ marginTop: '10px', fontSize: '12px', color: '#cbd5e1' }}>
      <div style={{ color: '#94a3b8', marginBottom: '6px' }} data-testid="screen-coverage">
        Searched {r.scope.description}
        {cov.universe !== null && <> · {cov.universe} with a ranking in the last {cov.window_days ?? 60} days</>}
        {cov.matched !== null && <> · {cov.matched} matched{cov.shown < (cov.matched ?? 0) ? ` (showing ${cov.shown})` : ''}</>}
      </div>
      {cov.unranked.length > 0 && (
        <div style={{ color: '#fbbf24', marginBottom: '6px' }}>
          Not screened (no recent ranking): {cov.unranked.join(', ')}
        </div>
      )}
      {r.message && <div style={{ color: '#fbbf24', marginBottom: '6px' }}>{r.message}</div>}
      {r.interpreted.length > 0 && (
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '6px' }}>
          <span style={{ color: '#64748b' }}>Applied:</span>
          {r.interpreted.map(f => <span key={f.filter} style={chip('rgba(74,222,128,0.1)', '#4ade80')}>{f.label}</span>)}
          {r.sort_by && <span style={chip('rgba(148,163,184,0.1)', '#94a3b8')}>sorted by {r.sort_by}</span>}
        </div>
      )}
      {r.dropped.length > 0 && (
        <div style={{ marginBottom: '6px' }}>
          <span style={{ color: '#64748b' }}>Not applied: </span>
          {r.dropped.map((d, i) => (
            <span key={i} style={{ marginRight: '10px', color: '#f87171' }}>{d.filter} {d.value} — {d.reason}</span>
          ))}
        </div>
      )}
      {r.unsupported.length > 0 && (
        <div style={{ marginBottom: '6px' }}>
          <span style={{ color: '#64748b' }}>The screen cannot filter on: </span>
          <span style={{ color: '#fbbf24' }}>{r.unsupported.join('; ')}</span>
        </div>
      )}
      {r.results.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
          <thead>
            <tr style={{ color: '#64748b', textAlign: 'left' }}>
              {['Symbol', 'Sector', 'K-Score', 'Momentum', 'Technical', 'RS', 'Signal', 'Strength'].map(h =>
                <th key={h} style={{ padding: '4px 6px', borderBottom: '1px solid #1e293b' }}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {r.results.map(x => (
              <tr key={x.symbol}>
                <td style={{ padding: '4px 6px' }}><Link href={`/stock/${x.symbol}`} style={{ color: '#818cf8' }}>{x.symbol}</Link></td>
                <td style={{ padding: '4px 6px', color: '#94a3b8' }}>{x.sector ?? '—'}</td>
                {[x.score, x.momentum, x.technical, x.rs_score].map((v, i) =>
                  <td key={i} style={{ padding: '4px 6px' }}>{v ?? '—'}</td>)}
                <td style={{ padding: '4px 6px' }}>{x.signal ?? '—'}</td>
                <td style={{ padding: '4px 6px' }}>{x.confidence ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {r.basis && <div style={{ marginTop: '6px', fontSize: '10px', color: '#64748b' }}>{r.basis}</div>}
    </div>
  );
}

export default function CopilotScreen() {
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [res, setRes] = useState<CopilotScreenResult | null>(null);

  async function run() {
    if (!q.trim() || loading) return;
    setLoading(true); setError('');
    try {
      setRes(await screenCopilot(q.trim(), newRequestId()));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Screen failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ background: '#080f1e', border: '1px solid #4c1d95', borderRadius: '10px', padding: '12px 16px', marginBottom: '12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '11px', color: '#c4b5fd', fontWeight: 700, whiteSpace: 'nowrap' }}>🤖 Copilot screen</span>
        <input
          value={q} maxLength={1000}
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && run()}
          placeholder='e.g. "BUY signals in technology with a K-Score above 70, top 10"'
          style={{ flex: '1 1 240px', padding: '6px 10px', borderRadius: '6px', border: '1px solid #4c1d95', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }}
        />
        <button onClick={run} disabled={loading || !q.trim()}
          style={{ padding: '6px 14px', borderRadius: '6px', border: '1px solid #7c3aed', background: 'rgba(124,58,237,0.2)', color: '#c4b5fd', fontSize: '12px', fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer', opacity: (!q.trim() || loading) ? 0.5 : 1 }}>
          {loading ? 'Screening…' : 'Screen'}
        </button>
      </div>
      <div style={{ fontSize: '10px', color: '#64748b', marginTop: '4px' }}>
        Only filters you state are applied — every threshold must be one you typed. Searches your watchlists (admins: all ranked stocks).
      </div>
      {error && <div style={{ fontSize: '11px', color: '#f87171', marginTop: '6px' }}>⚠ {error}</div>}
      {res && <ScreenResultView r={res} />}
    </div>
  );
}
