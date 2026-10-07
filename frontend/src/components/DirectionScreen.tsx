import React, { useState } from 'react';
import useSWR from 'swr';
import { api } from '@/lib/api';

const control = { background: '#111827', color: '#e2e8f0', border: '1px solid #334155',
  borderRadius: 6, padding: '7px 9px' };

export default function DirectionScreen({ symbols }: { symbols: string }) {
  const [market, setMarket] = useState('ALL');
  const [direction, setDirection] = useState('all');
  const [limit, setLimit] = useState('20');
  const [sector, setSector] = useState('');
  const params = new URLSearchParams({ market, direction, limit, symbols });
  if (sector) params.set('sector', sector);
  const query = params.toString();
  const { data, error, isLoading, mutate } = useSWR(['direction-screen', query],
    () => api.directionScreen(query), { revalidateOnFocus: false });
  return <section aria-label="Direction and setups" style={{ margin: '20px 0', color: '#cbd5e1' }}>
    <h2 style={{ fontSize: 18 }}>Direction &amp; setups</h2>
    <p style={{ fontSize: 12 }}>Daily price setup for a 5–20-session research horizon. Read the direction,
      main factors and trigger first; company quality and valuation remain separate checks.</p>
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
      <label>Market <select aria-label="Market" style={control} value={market}
        onChange={e => { setMarket(e.target.value); setSector(''); }}>
        <option value="ALL">US + HK</option><option value="US">US</option><option value="HK">HK</option>
      </select></label>
      <label>Setup <select aria-label="Setup" style={control} value={direction}
        onChange={e => setDirection(e.target.value)}>
        <option value="all">All setups</option>
        <option value="breakout_watch">Breakout watch</option><option value="breakout">Range break up</option>
        <option value="breakdown_watch">Breakdown watch</option><option value="breakdown">Range break down</option>
        <option value="range">Inside range</option><option value="unknown">Unknown / data gaps</option>
      </select></label>
      <label>Per market <select aria-label="Results per market" style={control} value={limit}
        onChange={e => setLimit(e.target.value)}>
        <option value="10">Top 10</option><option value="20">Top 20</option>
        <option value="50">Top 50</option><option value="100">Top 100</option>
      </select></label>
      <label>Sector <select aria-label="Sector" style={control} value={sector}
        onChange={e => setSector(e.target.value)}>
        <option value="">All sectors</option>{data?.sectors.map(s => <option key={s}>{s}</option>)}
      </select></label>
      <button style={control} onClick={() => mutate()}>Refresh setups</button>
    </div>
    {error && <p role="alert">Could not load price setups. {String(error)}</p>}
    {isLoading && <p>Reading completed sessions…</p>}
    {data && <>
      {!data.calendar_available && <p role="alert">Calendar coverage unavailable — no setups evaluated.</p>}
      <p style={{ fontSize: 12, color: '#94a3b8' }}>{data.scanned} listings scanned · {data.matching} match ·
        {' '}{data.rows.length} shown. Sessions: {Object.entries(data.session_dates).map(([m, d]) => `${m} ${d}`).join(' · ')}.
        {' '}Rank is a research priority, not a probability.</p>
      {!data.rows.length && <p>No listings match these filters. Broaden the filters or check data coverage.</p>}
      {data.rows.map(r => <article key={`${r.market}:${r.symbol}`} style={{ border: '1px solid #253047',
        borderRadius: 8, padding: 12, marginBottom: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <strong>{r.symbol} · {r.name} <small>({r.market})</small></strong>
          <strong style={{ color: r.setup.direction.startsWith('breakout') ? '#6ee7b7'
            : r.setup.direction.startsWith('breakdown') ? '#fca5a5' : '#cbd5e1' }}>{r.setup.label}</strong>
        </div>
        <p style={{ fontSize: 13, margin: '7px 0' }}>{r.setup.factors.join(' · ')}</p>
        {r.setup.close !== undefined && <p style={{ fontSize: 12, margin: '7px 0' }}>
          Close {r.setup.close.toFixed(2)} {r.currency} · Upside trigger: close above {r.setup.resistance?.toFixed(2)} ·
          {' '}Downside trigger: close below {r.setup.support?.toFixed(2)}</p>}
        <div style={{ fontSize: 13 }}><b>Approach:</b> {r.setup.strategy}</div>
        <details style={{ marginTop: 7, fontSize: 12 }}><summary>Evidence &amp; limits</summary>
          <p>Session {r.setup.session || 'unavailable'} · policy {data.policy}.</p>
          {r.setup.limitations?.map(l => <p key={l}>{l}</p>)}
          <p>Range = previous 20 completed sessions, excluding the tested close. Watch = within 2%
            of a boundary with the 20-close average aligned. Volume is corroboration, not proof.</p>
        </details>
      </article>)}
      <details style={{ fontSize: 12 }}><summary>Ranking, coverage and strategy limits</summary>
        <p>{data.note}</p><p>Proposed workflow: shortlist → verify company, liquidity and event risk →
          wait for the stated trigger → define invalidation and maximum loss before considering an entry.
          Downward setups are risk-review prompts, not automatic short-sale recommendations.
          Thresholds need prospective validation with costs before being treated as a strategy with an edge.</p>
      </details>
    </>}
  </section>;
}
