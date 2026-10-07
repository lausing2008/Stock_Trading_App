import React, { useEffect, useState } from 'react';
import useSWR from 'swr';
import { api, type DirectionScreen as DirectionScreenData } from '@/lib/api';
type DirectionScreenRow = DirectionScreenData['rows'][number];

const control = { background: '#111827', color: '#e2e8f0', border: '1px solid #334155',
  borderRadius: 6, padding: '7px 9px' };

const GUIDE_KEY = 'qv.directionScreen.guideOpen';

/** Open the first time, then remember what the reader chose.
 *
 *  localStorage can be absent or throw — a private window, blocked site data — so every read
 *  and write is guarded and the fallback is OPEN. Defaulting closed on a storage error would
 *  hide the guide from exactly the readers whose browser told us least about them.
 */
function readGuideOpen(): boolean {
  try {
    const v = window.localStorage.getItem(GUIDE_KEY);
    return v === null ? true : v === '1';
  } catch { return true; }
}

function SetupGuide() {
  const [open, setOpen] = useState(true);
  // Read after mount: the server render has no localStorage, and reading during render would
  // produce markup that disagrees with the client.
  useEffect(() => { setOpen(readGuideOpen()); }, []);
  const remember = (next: boolean) => {
    setOpen(next);
    try { window.localStorage.setItem(GUIDE_KEY, next ? '1' : '0'); } catch { /* not fatal */ }
  };
  const rows = [
    ['Breakout watch', 'Upward setup — awaiting break',
      'The close is still inside the range, within 2% of resistance and above the prior 20-close average.',
      'An upward break has not happened yet. Watch for a completed close above resistance.'],
    ['Range break up', 'Up — range break observed',
      'The tested daily close is above the highest high of the preceding 20 sessions.',
      'The break has happened. Watch whether price holds above the level; a close back below invalidates this break.'],
    ['Breakdown watch', 'Downward setup — awaiting break',
      'The close is still inside the range, within 2% of support and below the prior 20-close average.',
      'A downward break has not happened yet. Watch for a completed close below support.'],
    ['Range break down', 'Down — range break observed',
      'The tested daily close is below the lowest low of the preceding 20 sessions.',
      'The break has happened. Watch whether price reclaims support; review downside exposure.'],
    ['Inside range', 'Inside range — no directional setup',
      'The close is inside the range and meets neither watch rule.',
      'No setup under these rules. This does not predict that the price will stay the same.'],
    ['Unknown / data gaps', 'Unknown',
      'The required price or volume history is missing, stale, invalid, or needs corporate-action reconciliation.',
      'Wait for usable data. Unknown is not a neutral or sideways signal.'],
  ];
  const cell = { padding: '9px 10px', textAlign: 'left' as const,
    verticalAlign: 'top', borderBottom: '1px solid #253047' };
  return <details open={open} onToggle={e => remember((e.currentTarget as HTMLDetailsElement).open)}
    style={{ border: '1px solid #334155', borderRadius: 8,
    padding: 12, marginBottom: 16, fontSize: 12, lineHeight: 1.55 }}>
    <summary style={{ cursor: 'pointer', fontWeight: 700, fontSize: 14 }}>
      What is a setup? How to read these labels
    </summary>
    <p>A setup is a price pattern worth watching, based on completed daily sessions. It describes
      where the close sits relative to recent price levels; it is not a buy/sell instruction or
      a probability. <strong>Watch = a possible break ahead. Observed = a break already happened.</strong></p>
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 580 }}>
        <caption style={{ textAlign: 'left', color: '#94a3b8', marginBottom: 6 }}>
          Setup filter names and the matching labels on each stock card
        </caption>
        <thead><tr>{['Setup / card label', 'What it means', 'What to watch next'].map(t =>
          <th key={t} scope="col" style={cell}>{t}</th>)}</tr></thead>
        <tbody>{rows.map(([name, label, meaning, next]) => <tr key={name}>
          <th scope="row" style={{ ...cell, minWidth: 155 }}>
            {name}<div style={{ color: '#94a3b8', fontWeight: 400 }}>{label}</div>
          </th><td style={cell}>{meaning}</td><td style={cell}>{next}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <p><strong>Support / downside trigger:</strong> the lowest low of the prior 20 sessions.
      {' '}<strong>Resistance / upside trigger:</strong> the highest high over those sessions.
      The tested session is excluded from both levels. A trigger is a level to monitor, not an order;
      on an observed-break card, one trigger has already been crossed.</p>
    <p><strong>Volume 2×:</strong> twice the prior 20-session average daily volume.
      {' '}<strong>Retest:</strong> price returns to the broken level to see whether it holds.
      {' '}<strong>Invalidation:</strong> the condition that makes the setup no longer hold.</p>
    <p style={{ color: '#94a3b8', marginBottom: 0 }}>All setups shows every category. Top 20 means up to
      20 ranked listings per selected market, not the 20 safest stocks. Read the session date:
      today’s local session is excluded. The 5–20-session horizon is a research window, not a
      deadline for a move. Company quality, valuation and event risk still need separate review.</p>
  </details>;
}

/* INSTRUMENT TYPE BESIDE THE SYMBOL. The shortlist is not all operating companies — QQQM and
   QLD appear in it — and a fund's range break is a statement about its basket, not a business.
   A leveraged fund's is a statement about a daily-reset multiple of one, which §30 of the
   intelligence spec says must not be read like an ordinary 1x ETF. The basis travels in the
   tooltip because the type is inferred, not looked up. */
const TAG: Record<string, { text: string; fg: string; bg: string }> = {
  operating_company: { text: 'Company', fg: '#94a3b8', bg: 'rgba(148,163,184,0.12)' },
  fund:              { text: 'Fund',    fg: '#7dd3fc', bg: 'rgba(56,189,248,0.14)' },
  unverified:        { text: 'Type unverified', fg: '#fdba74', bg: 'rgba(251,146,60,0.14)' },
};

function InstrumentTag({ i }: { i: NonNullable<DirectionScreenRow['instrument']> }) {
  const t = TAG[i.type] ?? TAG.unverified;
  const pill = { marginLeft: 6, padding: '1px 6px', borderRadius: 4, fontSize: 10,
    fontWeight: 700, letterSpacing: '0.04em', whiteSpace: 'nowrap' as const };
  return <>
    <span title={i.basis} style={{ ...pill, color: t.fg, background: t.bg }}>{t.text}</span>
    {i.leveraged && <span title="Daily-reset leveraged or inverse fund — its return is a
 multiple of a daily move, not of the period." style={{ ...pill, color: '#fca5a5',
      background: 'rgba(248,113,113,0.16)' }}>Leveraged</span>}
  </>;
}

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
    <SetupGuide />
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
          {r.instrument && <InstrumentTag i={r.instrument} />}
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
