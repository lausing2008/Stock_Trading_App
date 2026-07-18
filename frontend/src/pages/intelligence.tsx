import { useState } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import {
  api,
  type EconomicEvent,
  type EarningsEvent,
  type InsiderLeaderItem,
  type CongressLeaderItem,
  type OverviewInsiderTopBuy,
  type OverviewCongressTopBuy,
  type CongressTrade,
  type CatalystLeaderItem,
  type CatalystScore,
  type PoliticalEvent,
  type EventIntelOverview,
  type CapeReading,
  type MarketPulse,
} from '@/lib/api';
import { getSession } from '@/lib/auth';

type Tab = 'overview' | 'economic' | 'earnings' | 'insider' | 'congress' | 'catalyst' | 'risk' | 'political' | 'valuation';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview',  label: 'Overview' },
  { key: 'economic',  label: 'Economic Calendar' },
  { key: 'earnings',  label: 'Earnings Calendar' },
  { key: 'insider',   label: 'Insider Activity' },
  { key: 'congress',  label: 'Congress Trades' },
  { key: 'catalyst',  label: 'Catalyst Leaders' },
  { key: 'risk',      label: 'Risk Leaders' },
  { key: 'political', label: 'Political Contracts' },
  { key: 'valuation', label: 'Bubble Warning' },
];

function fmt(n: number | null | undefined, digits = 0): string {
  if (n == null) return '—';
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtUsd(n: number | null | undefined): string {
  if (n == null) return '—';
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function scoreColor(score: number | null | undefined): string {
  if (score == null) return '#9ca3af';
  if (score >= 70) return '#22c55e';
  if (score >= 40) return '#f59e0b';
  if (score >= 0)  return '#9ca3af';
  return '#ef4444';
}

function ScoreBar({ score, max = 100 }: { score: number | null; max?: number }) {
  if (score == null) return <span style={{ color: '#6b7280' }}>—</span>;
  const pct = Math.max(0, Math.min(100, ((score + max) / (2 * max)) * 100));
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ flex: 1, height: 6, background: '#1f2937', borderRadius: 3 }}>
        <div style={{ width: `${pct}%`, height: '100%', borderRadius: 3, background: scoreColor(score) }} />
      </div>
      <span style={{ color: scoreColor(score), fontWeight: 700, minWidth: 36, textAlign: 'right' }}>
        {fmt(score)}
      </span>
    </div>
  );
}

function pulseColor(label: string): string {
  if (label === 'positive') return '#4ade80';
  if (label === 'negative') return '#f87171';
  return '#9ca3af';
}

function MarketPulseCard() {
  // 30-min cadence, same TTL as the backend's Redis cache — a passive dashboard card, not a
  // real-time alert feed (see T249-MARKETMOVER-P4's tracker note for why real-time breaking
  // news is an explicit non-goal for the free-tier data sources this reads from).
  const { data, isLoading } = useSWR('marketPulse', () => api.marketPulse(), { refreshInterval: 300_000 });

  if (isLoading) return null;
  if (!data) return null;

  const pulse = data as MarketPulse;
  const color = pulseColor(pulse.label);

  return (
    <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 8, padding: '16px 20px', marginBottom: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <h3 style={{ color: '#d1d5db', fontSize: 13, fontWeight: 600, margin: 0 }}>
          📰 MARKET PULSE
        </h3>
        <span style={{ color: '#6b7280', fontSize: 11 }}>
          as of {new Date(pulse.generated_at * 1000).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })} · refreshes every 30 min
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: pulse.themes.length ? 10 : 0 }}>
        <span style={{ color, fontWeight: 700, fontSize: 15, textTransform: 'capitalize' }}>{pulse.label}</span>
        <span style={{ color: '#6b7280', fontSize: 12 }}>({fmt(pulse.score)}/100 · {pulse.source})</span>
      </div>
      {pulse.themes.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {pulse.themes.map(t => (
            <span key={t} style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 10, color: '#d1d5db', background: 'rgba(148,163,184,0.12)', border: '1px solid rgba(148,163,184,0.3)' }}>
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function OverviewTab() {
  const { data, isLoading } = useSWR('eventsOverview', () => api.eventsOverview(), { refreshInterval: 300_000 });

  if (isLoading) return <p style={{ color: '#9ca3af', padding: '32px 0' }}>Loading overview…</p>;
  if (!data) return <p style={{ color: '#ef4444', padding: '32px 0' }}>Failed to load overview</p>;

  const ov = data as EventIntelOverview;

  return (
    <div>
      {/* Summary cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12, marginBottom: 32 }}>
        {[
          { label: 'Upcoming Economic Events', value: ov.economic.upcoming_count },
          { label: 'FOMC Days Away', value: ov.economic.fomc_days_away ?? 'Unknown' },
          { label: 'Upcoming Earnings', value: ov.earnings.upcoming_count },
          { label: 'Catalyst Leaders', value: ov.catalyst_leaders.length },
        ].map(c => (
          <div key={c.label} style={{ background: '#111827', borderRadius: 8, padding: '16px', border: '1px solid #1f2937' }}>
            <div style={{ color: '#6b7280', fontSize: 11, marginBottom: 4 }}>{c.label}</div>
            <div style={{ color: '#f9fafb', fontSize: 24, fontWeight: 700 }}>{c.value}</div>
          </div>
        ))}
      </div>

      {/* T249-MARKETMOVER-P4: market-level news pulse card */}
      <MarketPulseCard />

      {/* T249-MARKETMOVER-P2: latest macro fast-reaction */}
      {ov.latest_macro_reaction && (
        <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 8, padding: '16px 20px', marginBottom: 24 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
            <h3 style={{ color: '#d1d5db', fontSize: 13, fontWeight: 600, margin: 0 }}>
              📈 LATEST MACRO REACTION — {ov.latest_macro_reaction.title}
            </h3>
            {ov.latest_macro_reaction.generated_at && (
              <span style={{ color: '#6b7280', fontSize: 11 }}>
                as of {new Date(ov.latest_macro_reaction.generated_at).toLocaleString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', month: 'short', day: 'numeric' })} ET
              </span>
            )}
          </div>
          <div style={{ color: '#9ca3af', fontSize: 12, marginBottom: 8 }}>
            Actual: <strong style={{ color: '#f9fafb' }}>{ov.latest_macro_reaction.actual_value}</strong>
            {ov.latest_macro_reaction.previous_value != null && (
              <> · Previous: {ov.latest_macro_reaction.previous_value}</>
            )}
          </div>
          {(ov.latest_macro_reaction.sectors_helped.length > 0 || ov.latest_macro_reaction.sectors_hurt.length > 0) && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
              {ov.latest_macro_reaction.sectors_helped.map(s => (
                <span key={`helped-${s}`} style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 10, color: '#4ade80', background: 'rgba(74,222,128,0.12)', border: '1px solid rgba(74,222,128,0.3)' }}>
                  ▲ {s}
                </span>
              ))}
              {ov.latest_macro_reaction.sectors_hurt.map(s => (
                <span key={`hurt-${s}`} style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 10, color: '#f87171', background: 'rgba(248,113,113,0.12)', border: '1px solid rgba(248,113,113,0.3)' }}>
                  ▼ {s}
                </span>
              ))}
            </div>
          )}
          <p style={{ color: '#e5e7eb', fontSize: 13, lineHeight: 1.5, margin: 0 }}>
            {ov.latest_macro_reaction.reaction_text}
          </p>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 24 }}>
        {/* Top insider buys */}
        <div>
          <h3 style={{ color: '#d1d5db', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>TOP INSIDER BUYS</h3>
          <div style={{ background: '#111827', borderRadius: 8, border: '1px solid #1f2937', overflow: 'hidden' }}>
            {(ov.insider?.top_buys ?? []).slice(0, 8).map((item: OverviewInsiderTopBuy) => (
              <div key={item.symbol} style={{ padding: '8px 12px', borderBottom: '1px solid #1f2937', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ color: '#60a5fa', fontWeight: 600 }}>{item.symbol}</span>
                <span style={{ color: '#22c55e', fontSize: 13 }}>{item.net_value != null ? fmtUsd(item.net_value) : `${item.purchases} buys`}</span>
              </div>
            ))}
            {(ov.insider?.top_buys ?? []).length === 0 && (
              <div style={{ padding: '16px', color: '#6b7280', fontSize: 13 }}>No data yet — sync in progress</div>
            )}
          </div>
        </div>

        {/* Top congress buys */}
        <div>
          <h3 style={{ color: '#d1d5db', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>TOP CONGRESS BUYS</h3>
          <div style={{ background: '#111827', borderRadius: 8, border: '1px solid #1f2937', overflow: 'hidden' }}>
            {(ov.congress?.top_buys ?? []).slice(0, 8).map((item: OverviewCongressTopBuy) => (
              <div key={item.symbol} style={{ padding: '8px 12px', borderBottom: '1px solid #1f2937', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ color: '#60a5fa', fontWeight: 600 }}>{item.symbol}</span>
                <span style={{ color: '#22c55e', fontSize: 13 }}>${Math.round(item.net_amount).toLocaleString()}</span>
              </div>
            ))}
            {(ov.congress?.top_buys ?? []).length === 0 && (
              <div style={{ padding: '16px', color: '#6b7280', fontSize: 13 }}>No data yet — sync in progress</div>
            )}
          </div>
        </div>

        {/* Composite leaders */}
        <div>
          <h3 style={{ color: '#d1d5db', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>TOP COMPOSITE SCORES</h3>
          <div style={{ background: '#111827', borderRadius: 8, border: '1px solid #1f2937', overflow: 'hidden' }}>
            {(ov.composite_leaders ?? []).slice(0, 8).map((item: CatalystLeaderItem) => (
              <div key={item.symbol} style={{ padding: '8px 12px', borderBottom: '1px solid #1f2937', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ color: '#60a5fa', fontWeight: 600 }}>{item.symbol}</span>
                <span style={{ color: scoreColor(item.score), fontSize: 13, fontWeight: 600 }}>{fmt(item.score)}</span>
              </div>
            ))}
            {(ov.composite_leaders ?? []).length === 0 && (
              <div style={{ padding: '16px', color: '#6b7280', fontSize: 13 }}>No data yet — sync in progress</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function EconomicTab() {
  const { data, isLoading } = useSWR('eventsEconomic', () => api.eventsEconomic(30, 'US'), { refreshInterval: 300_000 });

  if (isLoading) return <p style={{ color: '#9ca3af', padding: '32px 0' }}>Loading economic events…</p>;

  const events: EconomicEvent[] = data?.events ?? [];
  const fomcDays = data?.fomc_days_away;

  return (
    <div>
      {fomcDays != null && (
        <div style={{ background: '#1c1917', border: '1px solid #44403c', borderRadius: 8, padding: '12px 16px', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 20 }}>🏦</span>
          <span style={{ color: '#f5d0a9', fontWeight: 600 }}>
            Next FOMC meeting: <strong>{fomcDays} days away</strong>
          </span>
        </div>
      )}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #1f2937' }}>
            {['Date', 'Event', 'Type', 'Market', 'Impact', 'Previous', 'Forecast', 'Actual'].map(h => (
              <th key={h} style={{ textAlign: 'left', padding: '8px 10px', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {events.map(ev => {
            const hasActual = ev.actual_value != null;
            const beat = hasActual && ev.forecast_value != null && ev.actual_value! > ev.forecast_value;
            return (
              <tr key={ev.id} style={{ borderBottom: '1px solid #111827' }}>
                <td style={{ padding: '8px 10px', color: '#d1d5db' }}>{ev.event_date}</td>
                <td style={{ padding: '8px 10px', color: '#f9fafb', fontWeight: 500 }}>{ev.event_name}</td>
                <td style={{ padding: '8px 10px', color: '#9ca3af' }}>{ev.event_type}</td>
                <td style={{ padding: '8px 10px', color: '#9ca3af' }}>{ev.market}</td>
                <td style={{ padding: '8px 10px' }}>
                  {ev.impact_level === 'high' && <span style={{ color: '#ef4444', fontWeight: 700 }}>HIGH</span>}
                  {ev.impact_level === 'medium' && <span style={{ color: '#f59e0b', fontWeight: 600 }}>MED</span>}
                  {ev.impact_level === 'low' && <span style={{ color: '#6b7280' }}>low</span>}
                  {!ev.impact_level && <span style={{ color: '#374151' }}>—</span>}
                </td>
                <td style={{ padding: '8px 10px', color: '#9ca3af' }}>{ev.previous_value != null ? ev.previous_value : '—'}</td>
                <td style={{ padding: '8px 10px', color: '#9ca3af' }}>{ev.forecast_value != null ? ev.forecast_value : '—'}</td>
                <td style={{ padding: '8px 10px', color: beat ? '#22c55e' : hasActual ? '#f87171' : '#6b7280', fontWeight: hasActual ? 700 : 400 }}>
                  {hasActual ? ev.actual_value : '—'}
                </td>
              </tr>
            );
          })}
          {events.length === 0 && (
            <tr><td colSpan={8} style={{ padding: '32px', textAlign: 'center', color: '#6b7280' }}>No economic events — sync may still be running</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function EarningsTab() {
  const { data, isLoading } = useSWR('eventsEarnings', () => api.eventsEarningsCalendar(21), { refreshInterval: 300_000 });

  if (isLoading) return <p style={{ color: '#9ca3af', padding: '32px 0' }}>Loading earnings calendar…</p>;

  const events: EarningsEvent[] = Array.isArray(data) ? data : [];

  return (
    <div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #1f2937' }}>
            {['Symbol', 'Date', 'Est EPS', 'Actual EPS', 'Surprise %', 'Beat Rate', 'Status'].map(h => (
              <th key={h} style={{ textAlign: 'left', padding: '8px 10px', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {events.map(ev => {
            const surprise = ev.surprise_pct;
            return (
              <tr key={ev.id} style={{ borderBottom: '1px solid #111827' }}>
                <td style={{ padding: '8px 10px', color: '#60a5fa', fontWeight: 600 }}>{ev.symbol}</td>
                <td style={{ padding: '8px 10px', color: '#d1d5db' }}>{ev.earnings_date}</td>
                <td style={{ padding: '8px 10px', color: '#9ca3af' }}>{ev.estimated_eps != null ? `$${ev.estimated_eps.toFixed(2)}` : '—'}</td>
                <td style={{ padding: '8px 10px', color: ev.actual_eps != null ? '#f9fafb' : '#6b7280', fontWeight: ev.actual_eps != null ? 600 : 400 }}>
                  {ev.actual_eps != null ? `$${ev.actual_eps.toFixed(2)}` : '—'}
                </td>
                <td style={{ padding: '8px 10px', color: surprise != null ? (surprise >= 0 ? '#22c55e' : '#ef4444') : '#6b7280', fontWeight: surprise != null ? 700 : 400 }}>
                  {surprise != null ? `${surprise >= 0 ? '+' : ''}${surprise.toFixed(1)}%` : '—'}
                </td>
                <td style={{ padding: '8px 10px', color: ev.beat_rate != null ? (ev.beat_rate >= 0.6 ? '#22c55e' : '#9ca3af') : '#6b7280' }}>
                  {ev.beat_rate != null ? `${(ev.beat_rate * 100).toFixed(0)}%` : '—'}
                </td>
                <td style={{ padding: '8px 10px' }}>
                  {ev.is_upcoming
                    ? <span style={{ background: '#1e3a5f', color: '#60a5fa', borderRadius: 4, padding: '2px 8px', fontSize: 11, fontWeight: 600 }}>UPCOMING</span>
                    : <span style={{ color: '#6b7280', fontSize: 11 }}>reported</span>
                  }
                </td>
              </tr>
            );
          })}
          {events.length === 0 && (
            <tr><td colSpan={7} style={{ padding: '32px', textAlign: 'center', color: '#6b7280' }}>No earnings data — sync may still be running</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function InsiderTab() {
  const { data, isLoading } = useSWR('eventsInsiderLeader', () => api.eventsInsiderLeaderboard(30), { refreshInterval: 300_000 });

  if (isLoading) return <p style={{ color: '#9ca3af', padding: '32px 0' }}>Loading insider activity…</p>;

  const items: InsiderLeaderItem[] = Array.isArray(data) ? data : [];

  return (
    <div>
      <p style={{ color: '#6b7280', fontSize: 13, marginBottom: 20 }}>
        Insider score: +100 = strong net buying by executives/directors; −100 = heavy selling. Weighted by role (CEO/CFO = 1.5×, Director = 0.8×).
      </p>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #1f2937' }}>
            {['Symbol', 'Score', 'Buy Transactions', 'Sell Transactions', 'Net Value'].map(h => (
              <th key={h} style={{ textAlign: 'left', padding: '8px 10px', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map(item => (
            <tr key={item.symbol} style={{ borderBottom: '1px solid #111827' }}>
              <td style={{ padding: '8px 10px', color: '#60a5fa', fontWeight: 600 }}>{item.symbol}</td>
              <td style={{ padding: '8px 10px', minWidth: 160 }}><ScoreBar score={item.score} max={100} /></td>
              <td style={{ padding: '8px 10px', color: '#22c55e' }}>{item.buy_count}</td>
              <td style={{ padding: '8px 10px', color: '#ef4444' }}>{item.sell_count}</td>
              <td style={{ padding: '8px 10px', color: item.net_value != null && item.net_value >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                {fmtUsd(item.net_value)}
              </td>
            </tr>
          ))}
          {items.length === 0 && (
            <tr><td colSpan={5} style={{ padding: '32px', textAlign: 'center', color: '#6b7280' }}>No insider data — sync may still be running</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function CongressTab() {
  const [view, setView] = useState<'leaderboard' | 'recent'>('leaderboard');
  const { data: leaders, isLoading: l1 } = useSWR('congressLeader', () => api.eventsCongressLeaderboard(90), { refreshInterval: 300_000 });
  const { data: recent, isLoading: l2 } = useSWR('congressRecent', () => api.eventsCongressRecent(30), { refreshInterval: 300_000 });

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        {['leaderboard', 'recent'].map(v => (
          <button
            key={v}
            onClick={() => setView(v as 'leaderboard' | 'recent')}
            style={{ padding: '6px 14px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 600,
              background: view === v ? '#2563eb' : '#1f2937', color: view === v ? '#fff' : '#9ca3af' }}
          >
            {v === 'leaderboard' ? 'Stock Leaderboard' : 'Recent Trades'}
          </button>
        ))}
      </div>

      {view === 'leaderboard' && (
        <div>
          {l1 && <p style={{ color: '#9ca3af' }}>Loading…</p>}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '2px solid #1f2937' }}>
                {['Symbol', 'Net Amount', 'Purchases', 'Sales', 'Politicians'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 10px', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(leaders ?? []).map((item: CongressLeaderItem) => (
                <tr key={item.symbol} style={{ borderBottom: '1px solid #111827' }}>
                  <td style={{ padding: '8px 10px', color: '#60a5fa', fontWeight: 600 }}>{item.symbol}</td>
                  <td style={{ padding: '8px 10px', color: item.net_amount >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                    {item.net_amount >= 0 ? '+' : ''}${Math.round(item.net_amount).toLocaleString()}
                  </td>
                  <td style={{ padding: '8px 10px', color: '#22c55e' }}>{item.purchases}</td>
                  <td style={{ padding: '8px 10px', color: '#ef4444' }}>{item.sales}</td>
                  <td style={{ padding: '8px 10px', color: '#9ca3af' }}>{item.unique_politicians}</td>
                </tr>
              ))}
              {(leaders ?? []).length === 0 && !l1 && (
                <tr><td colSpan={5} style={{ padding: '32px', textAlign: 'center', color: '#6b7280' }}>No congress trade data — sync may still be running</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {view === 'recent' && (
        <div>
          {l2 && <p style={{ color: '#9ca3af' }}>Loading…</p>}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '2px solid #1f2937' }}>
                {['Symbol', 'Politician', 'Chamber', 'Party', 'Type', 'Amount', 'Date'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 10px', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(recent ?? []).map((t: CongressTrade) => (
                <tr key={t.id} style={{ borderBottom: '1px solid #111827' }}>
                  <td style={{ padding: '8px 10px', color: '#60a5fa', fontWeight: 600 }}>{t.ticker}</td>
                  <td style={{ padding: '8px 10px', color: '#d1d5db' }}>{t.politician_name}</td>
                  <td style={{ padding: '8px 10px', color: '#9ca3af' }}>{t.chamber ?? '—'}</td>
                  <td style={{ padding: '8px 10px' }}>
                    {t.party === 'R' && <span style={{ color: '#f87171' }}>R</span>}
                    {t.party === 'D' && <span style={{ color: '#60a5fa' }}>D</span>}
                    {!t.party && '—'}
                  </td>
                  <td style={{ padding: '8px 10px', color: t.transaction_type === 'purchase' ? '#22c55e' : t.transaction_type === 'sale' ? '#ef4444' : '#9ca3af', fontWeight: 600 }}>
                    {t.transaction_type}
                  </td>
                  <td style={{ padding: '8px 10px', color: '#9ca3af' }}>{t.amount_range ?? '—'}</td>
                  <td style={{ padding: '8px 10px', color: '#d1d5db' }}>{t.trade_date ?? '—'}</td>
                </tr>
              ))}
              {(recent ?? []).length === 0 && !l2 && (
                <tr><td colSpan={7} style={{ padding: '32px', textAlign: 'center', color: '#6b7280' }}>No recent trades — sync may still be running</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function LeaderboardTab({ fetcher, title, scoreLabel }: { fetcher: () => Promise<CatalystLeaderItem[]>; title: string; scoreLabel: string }) {
  const { data, isLoading, mutate } = useSWR(title, fetcher, { refreshInterval: 300_000 });

  const items: CatalystLeaderItem[] = Array.isArray(data) ? data : [];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <p style={{ color: '#6b7280', fontSize: 13 }}>{title} — updated 4× daily (00:00, 06:00, 12:00, 18:00 UTC)</p>
        <button onClick={() => mutate()} style={{ padding: '6px 14px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 12, background: '#1f2937', color: '#9ca3af' }}>
          Refresh
        </button>
      </div>
      {isLoading && <p style={{ color: '#9ca3af' }}>Loading…</p>}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #1f2937' }}>
            <th style={{ textAlign: 'left', padding: '8px 10px', color: '#6b7280', fontWeight: 600, fontSize: 11 }}>#</th>
            <th style={{ textAlign: 'left', padding: '8px 10px', color: '#6b7280', fontWeight: 600, fontSize: 11 }}>SYMBOL</th>
            <th style={{ textAlign: 'left', padding: '8px 10px', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>{scoreLabel}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => (
            <tr key={item.symbol} style={{ borderBottom: '1px solid #111827' }}>
              <td style={{ padding: '8px 10px', color: '#4b5563', width: 40 }}>{i + 1}</td>
              <td style={{ padding: '8px 10px', color: '#60a5fa', fontWeight: 600 }}>{item.symbol}</td>
              <td style={{ padding: '8px 10px', minWidth: 200 }}><ScoreBar score={item.score} /></td>
            </tr>
          ))}
          {items.length === 0 && !isLoading && (
            <tr><td colSpan={3} style={{ padding: '32px', textAlign: 'center', color: '#6b7280' }}>No data yet — scores are computed after initial sync</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function PoliticalTab() {
  const { data, isLoading } = useSWR('eventsPolitical', () => api.eventsPolitical(30), { refreshInterval: 300_000 });

  if (isLoading) return <p style={{ color: '#9ca3af', padding: '32px 0' }}>Loading political contracts…</p>;

  const events: PoliticalEvent[] = Array.isArray(data) ? data : [];

  return (
    <div>
      <p style={{ color: '#6b7280', fontSize: 13, marginBottom: 20 }}>
        Government contract awards &gt;$1M from USASpending.gov — defense, tech, health sectors.
      </p>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #1f2937' }}>
            {['Symbol', 'Company', 'Agency', 'Amount', 'Sector', 'Date'].map(h => (
              <th key={h} style={{ textAlign: 'left', padding: '8px 10px', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {events.map(ev => (
            <tr key={ev.id} style={{ borderBottom: '1px solid #111827' }}>
              <td style={{ padding: '8px 10px', color: ev.symbol ? '#60a5fa' : '#6b7280', fontWeight: ev.symbol ? 600 : 400 }}>{ev.symbol ?? '—'}</td>
              <td style={{ padding: '8px 10px', color: '#d1d5db' }}>{ev.company_name}</td>
              <td style={{ padding: '8px 10px', color: '#9ca3af', fontSize: 12 }}>{ev.agency ?? '—'}</td>
              <td style={{ padding: '8px 10px', color: '#22c55e', fontWeight: 600 }}>{fmtUsd(ev.contract_amount)}</td>
              <td style={{ padding: '8px 10px', color: '#9ca3af' }}>{ev.sector ?? '—'}</td>
              <td style={{ padding: '8px 10px', color: '#d1d5db' }}>{ev.award_date}</td>
            </tr>
          ))}
          {events.length === 0 && (
            <tr><td colSpan={6} style={{ padding: '32px', textAlign: 'center', color: '#6b7280' }}>No contract data — sync may still be running</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function capeBandColor(band: string): string {
  if (band === 'normal') return '#22c55e';
  if (band === 'elevated') return '#f59e0b';
  if (band === 'high') return '#f87171';
  return '#ef4444'; // extreme
}

function ValuationTab() {
  const { data, isLoading } = useSWR('eventsCape', () => api.eventsCape(36), { refreshInterval: 300_000 });

  if (isLoading) return <p style={{ color: '#9ca3af', padding: '32px 0' }}>Loading CAPE data…</p>;

  if (!data) {
    return <p style={{ color: '#6b7280', padding: '32px 0', textAlign: 'center' }}>No CAPE data synced yet</p>;
  }

  const { latest, history } = data;
  const bandColor = capeBandColor(latest.band);

  return (
    <div>
      <p style={{ color: '#6b7280', fontSize: 13, marginBottom: 20 }}>
        CAPE (Shiller cyclically-adjusted P/E) — a macro valuation indicator for the S&amp;P 500. Historically
        elevated readings have preceded major corrections, but CAPE is a slow-moving signal that can stay
        elevated for years — treat this as macro context, not a trade trigger. Sourced from multpl.com (an
        unofficial third-party feed), not a live/official data provider.
      </p>

      <div style={{ background: '#1c1917', border: `1px solid ${bandColor}`, borderRadius: 8, padding: '20px 24px', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 24 }}>
        <div>
          <div style={{ color: '#9ca3af', fontSize: 11, fontWeight: 600, textTransform: 'uppercase', marginBottom: 4 }}>Current CAPE</div>
          <div style={{ color: '#f9fafb', fontSize: 36, fontWeight: 800 }}>{latest.cape_value.toFixed(2)}</div>
        </div>
        <div>
          <span style={{ background: bandColor, color: '#0b0f19', borderRadius: 6, padding: '4px 14px', fontSize: 13, fontWeight: 700, textTransform: 'uppercase' }}>
            {latest.band}
          </span>
        </div>
        <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <div style={{ color: '#6b7280', fontSize: 12 }}>As of {latest.reading_date}</div>
          {latest.stale && (
            <div style={{ color: '#f87171', fontSize: 12, fontWeight: 600, marginTop: 2 }}>⚠ Data may be stale ({latest.age_days}d old)</div>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16, marginBottom: 20, fontSize: 12, color: '#9ca3af' }}>
        <span><strong style={{ color: '#22c55e' }}>Normal</strong> &lt;30</span>
        <span><strong style={{ color: '#f59e0b' }}>Elevated</strong> 30–35</span>
        <span><strong style={{ color: '#f87171' }}>High</strong> 35–40 (1929 peak: ~32–33)</span>
        <span><strong style={{ color: '#ef4444' }}>Extreme</strong> ≥40 (2021 peak: ~38.6; dot-com peak: 44.19)</span>
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #1f2937' }}>
            {['Date', 'CAPE', 'Band'].map(h => (
              <th key={h} style={{ textAlign: 'left', padding: '8px 10px', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {history.map((r: CapeReading) => (
            <tr key={r.reading_date} style={{ borderBottom: '1px solid #111827' }}>
              <td style={{ padding: '8px 10px', color: '#d1d5db' }}>{r.reading_date}</td>
              <td style={{ padding: '8px 10px', color: '#f9fafb', fontWeight: 600 }}>{r.cape_value.toFixed(2)}</td>
              <td style={{ padding: '8px 10px', color: capeBandColor(r.band ?? 'normal'), fontWeight: 600, textTransform: 'uppercase', fontSize: 11 }}>{r.band}</td>
            </tr>
          ))}
          {history.length === 0 && (
            <tr><td colSpan={3} style={{ padding: '32px', textAlign: 'center', color: '#6b7280' }}>No history yet</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export default function IntelligencePage() {
  const router = useRouter();
  const session = getSession();

  if (!session) {
    if (typeof window !== 'undefined') router.replace('/login');
    return null;
  }

  const [tab, setTab] = useState<Tab>('overview');

  return (
    <div style={{ minHeight: '100vh', background: '#0a0a0a', color: '#f9fafb', fontFamily: 'system-ui, sans-serif' }}>
      {/* Header */}
      <div style={{ background: '#111827', borderBottom: '1px solid #1f2937', padding: '0 24px' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto', display: 'flex', alignItems: 'center', gap: 24, height: 56 }}>
          <button onClick={() => router.push('/')} style={{ background: 'none', border: 'none', color: '#6b7280', cursor: 'pointer', fontSize: 13 }}>
            ← Back
          </button>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: '#f9fafb' }}>
            Event Intelligence
          </h1>
          <span style={{ color: '#6b7280', fontSize: 13 }}>Economic · Earnings · Insider · Congress · Catalyst</span>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ background: '#111827', borderBottom: '1px solid #1f2937', padding: '0 24px' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto', display: 'flex', gap: 0 }}>
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                background: 'none', border: 'none', cursor: 'pointer', padding: '12px 16px', fontSize: 13, fontWeight: 500,
                color: tab === t.key ? '#f9fafb' : '#6b7280',
                borderBottom: tab === t.key ? '2px solid #2563eb' : '2px solid transparent',
                transition: 'color 0.15s',
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div style={{ maxWidth: 1400, margin: '0 auto', padding: '28px 24px' }}>
        {tab === 'overview'  && <OverviewTab />}
        {tab === 'economic'  && <EconomicTab />}
        {tab === 'earnings'  && <EarningsTab />}
        {tab === 'insider'   && <InsiderTab />}
        {tab === 'congress'  && <CongressTab />}
        {tab === 'catalyst'  && <LeaderboardTab fetcher={() => api.catalystLeaderboard(50)} title="Catalyst Leaderboard" scoreLabel="Catalyst Score (0–100)" />}
        {tab === 'risk'      && <LeaderboardTab fetcher={() => api.riskLeaderboard(50)} title="Risk Leaderboard" scoreLabel="Risk Score (0–100)" />}
        {tab === 'political' && <PoliticalTab />}
        {tab === 'valuation' && <ValuationTab />}
      </div>
    </div>
  );
}
