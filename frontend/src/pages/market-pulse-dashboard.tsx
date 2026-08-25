import { useState } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import {
  api,
  type RegimeStatus,
  type FearGreed,
  type CalendarEvent,
  type SectorGroup,
  type SectorStock,
  type SqueezeAlertOutcomeRow,
  type MarketPulse,
  type NewsItem,
} from '@/lib/api';
import { getSession } from '@/lib/auth';
import NewsCard from '@/components/NewsCard';

// REALTIME-NEWS-EVENTS-INTELLIGENCE §4.3 "Market Pulse Dashboard" — a single "what's
// happening right now" view. Distinct from intelligence.tsx's own MarketPulseCard (headlines +
// sentiment score only) and from index.tsx's Dashboard (a watchlist-management console, not a
// market-wide overview) — verified before building that neither already covers this.
//
// Every section below composes ALREADY-EXISTING endpoints — zero new backend work. The design
// doc's own "squeeze_score * 0.30" FOMO-formula proposal (its §4.1) was investigated and
// dropped from this build: there is no live, per-symbol 0-100 squeeze score callable on demand
// (services/market-data/src/services/scheduler.py's squeeze scoring only exists as a
// calibration-bucket lookup keyed to already-fired alerts, not a general reusable score) — the
// doc's "all inputs exist" claim didn't hold up under direct verification for that one input.

type Regime = RegimeStatus['state'];

const REGIME_COLOR: Record<Regime, string> = {
  bull: '#4ade80', neutral: '#9ca3af', choppy: '#f59e0b', risk_off: '#fb923c', bear: '#f87171',
};
const REGIME_LABEL: Record<Regime, string> = {
  bull: 'Bull', neutral: 'Neutral', choppy: 'Choppy', risk_off: 'Risk-Off', bear: 'Bear',
};

function fmtPct(n: number | null | undefined, digits = 1): string {
  if (n == null) return '—';
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function changeColor(n: number | null | undefined): string {
  if (n == null) return '#9ca3af';
  return n > 0 ? '#4ade80' : n < 0 ? '#f87171' : '#9ca3af';
}

function impactColor(impact: string | null | undefined): string {
  if (impact === 'high') return '#f87171';
  if (impact === 'medium') return '#f59e0b';
  return '#9ca3af';
}

function card(extra: React.CSSProperties = {}): React.CSSProperties {
  return {
    background: '#111827', border: '1px solid #1f2937', borderRadius: 8,
    padding: '16px 20px', ...extra,
  };
}

function sectionTitle(text: string, right?: React.ReactNode) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
      <h3 style={{ color: '#d1d5db', fontSize: 13, fontWeight: 600, margin: 0 }}>{text}</h3>
      {right}
    </div>
  );
}

// ── Regime + Fear/Greed banner ────────────────────────────────────────────────

function RegimeBanner({ market }: { market: 'US' | 'HK' }) {
  const { data: regime, isLoading: regimeLoading } = useSWR(
    ['pulse-regime', market], () => api.regime(market), { refreshInterval: 300_000 }
  );
  const { data: fg, isLoading: fgLoading } = useSWR(
    market === 'US' ? 'pulse-fear-greed' : null, () => api.fearGreed(), { refreshInterval: 300_000 }
  );

  if (regimeLoading) return <div style={card()}>Loading regime…</div>;
  if (!regime) return null;

  const color = REGIME_COLOR[regime.state];
  return (
    <div style={card()}>
      {sectionTitle('📊 MARKET REGIME')}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <span style={{
            fontSize: 18, fontWeight: 800, color, textTransform: 'uppercase',
            padding: '4px 12px', borderRadius: 8, background: `${color}18`, border: `1px solid ${color}40`,
          }}>
            {REGIME_LABEL[regime.state]}
          </span>
        </div>
        {regime.vix != null && (
          <div>
            <div style={{ color: '#6b7280', fontSize: 11 }}>VIX</div>
            <div style={{ color: '#e5e7eb', fontSize: 14, fontWeight: 600 }}>
              {regime.vix.toFixed(1)}
              {regime.vix_5d_trend && (
                <span style={{ color: '#6b7280', fontSize: 11, marginLeft: 4 }}>
                  ({regime.vix_5d_trend})
                </span>
              )}
            </div>
          </div>
        )}
        {regime.spy_20d_ret != null && (
          <div>
            <div style={{ color: '#6b7280', fontSize: 11 }}>SPY 20d</div>
            <div style={{ color: changeColor(regime.spy_20d_ret), fontSize: 14, fontWeight: 600 }}>
              {fmtPct(regime.spy_20d_ret)}
            </div>
          </div>
        )}
        {!fgLoading && fg && (
          <div>
            <div style={{ color: '#6b7280', fontSize: 11 }}>Fear &amp; Greed</div>
            <div style={{ color: '#e5e7eb', fontSize: 14, fontWeight: 600 }}>
              {fg.score} <span style={{ color: '#9ca3af', fontWeight: 500 }}>({fg.rating})</span>
            </div>
          </div>
        )}
      </div>
      {regime.notes.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 12, color: '#6b7280' }}>
          {regime.notes.join(' · ')}
        </div>
      )}
    </div>
  );
}

// ── Macro events today ────────────────────────────────────────────────────────

function MacroEventsCard() {
  const { data: events, isLoading } = useSWR('pulse-macro-events', () => api.eventsCalendar(1), { refreshInterval: 900_000 });
  if (isLoading) return <div style={card()}>Loading macro events…</div>;

  const macroTypes = new Set(['fomc', 'cpi', 'nfp', 'pce', 'gdp', 'ppi', 'retail_sales', 'consumer_conf', 'housing_starts', 'jobless_claims', 'fed_funds']);
  const todays = (events ?? []).filter((e: CalendarEvent) => macroTypes.has(e.type) && e.days_to_event <= 0);

  return (
    <div style={card()}>
      {sectionTitle('📅 MACRO EVENTS TODAY', <span style={{ fontSize: 11, color: '#6b7280' }}>scheduled release date — see Event Intelligence for the actual result once published</span>)}
      {todays.length === 0 && <div style={{ color: '#4b5563', fontSize: 12 }}>No macro releases scheduled today.</div>}
      {todays.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {todays.map((e, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
              <span style={{
                fontSize: 10, fontWeight: 700, color: impactColor(e.impact), textTransform: 'uppercase',
                padding: '1px 7px', borderRadius: 4, background: `${impactColor(e.impact)}20`,
              }}>
                {e.impact ?? 'low'}
              </span>
              <span style={{ color: '#e5e7eb', fontWeight: 600 }}>{e.title}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Top movers + sector heat map (both derived from one sectorPerformance() call) ────────────

function MoversAndSectors({ market }: { market: 'US' | 'HK' }) {
  const { data: sectors, isLoading } = useSWR('pulse-sector-performance', () => api.sectorPerformance(), { refreshInterval: 300_000 });
  if (isLoading) return <div style={card()}>Loading market movers…</div>;
  if (!sectors) return null;

  // sector_performance() mixes US and HK stocks together in every sector with no market
  // filter of its own (each sector's own avg_change_pct/stock_count is computed across BOTH
  // markets) — this page shows this section only under the US toggle, so both the mover list
  // AND the per-sector aggregates must be recomputed from a market-filtered stock list, not
  // the backend's own mixed-market numbers.
  const allStocks: (SectorStock & { sector: string })[] = sectors.flatMap((s: SectorGroup) =>
    s.stocks.map(st => ({ ...st, sector: s.sector }))
  ).filter(s => s.market === market);

  // A single sort-by-|change_pct| list can go entirely one-sided on a broadly up or down day
  // (e.g. a day with several large losers and only modest gainers would silently show 10
  // losers and zero gainers) — split into two explicit, independently-sorted lists instead so
  // today's actual top gainer is never crowded out by today's actual top loser.
  const withChange = allStocks.filter(s => s.change_pct != null);
  const topGainers = [...withChange].filter(s => s.change_pct! > 0).sort((a, b) => b.change_pct! - a.change_pct!).slice(0, 6);
  const topLosers = [...withChange].filter(s => s.change_pct! < 0).sort((a, b) => a.change_pct! - b.change_pct!).slice(0, 6);

  const bySector = new Map<string, { symbol: string; change_pct: number | null }[]>();
  for (const s of allStocks) {
    if (!bySector.has(s.sector)) bySector.set(s.sector, []);
    bySector.get(s.sector)!.push(s);
  }
  const marketSectors = [...bySector.entries()].map(([sector, stocks]) => {
    const changes = stocks.map(s => s.change_pct).filter((c): c is number => c != null);
    const avg_change_pct = changes.length ? changes.reduce((a, b) => a + b, 0) / changes.length : null;
    return { sector, avg_change_pct, stock_count: stocks.length };
  });
  const sortedSectors = marketSectors.sort((a, b) => (b.avg_change_pct ?? 0) - (a.avg_change_pct ?? 0));

  function moverGrid(list: (SectorStock & { sector: string })[]) {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8 }}>
        {list.map(s => (
          <div key={s.symbol} style={{ padding: '8px 10px', borderRadius: 6, background: '#0f172a', border: '1px solid #1f2937' }}>
            <div style={{ color: '#e5e7eb', fontWeight: 700, fontSize: 13 }}>{s.symbol}</div>
            <div style={{ color: changeColor(s.change_pct), fontWeight: 600, fontSize: 13 }}>{fmtPct(s.change_pct)}</div>
            <div style={{ color: '#6b7280', fontSize: 10 }}>{s.sector}</div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <>
      <div style={card()}>
        {sectionTitle('🔥 TOP MOVERS', <span style={{ fontSize: 11, color: '#6b7280' }}>gainers &amp; losers across all tracked {market} sectors</span>)}
        {topGainers.length === 0 && topLosers.length === 0 && <div style={{ color: '#4b5563', fontSize: 12 }}>No data yet.</div>}
        {topGainers.length > 0 && (
          <div style={{ marginBottom: topLosers.length > 0 ? 14 : 0 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: '#4ade80', marginBottom: 6 }}>▲ TOP GAINERS</div>
            {moverGrid(topGainers)}
          </div>
        )}
        {topLosers.length > 0 && (
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: '#f87171', marginBottom: 6 }}>▼ TOP LOSERS</div>
            {moverGrid(topLosers)}
          </div>
        )}
      </div>

      <div style={card()}>
        {sectionTitle('🗺️ SECTOR HEAT MAP')}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8 }}>
          {sortedSectors.map(s => (
            <div
              key={s.sector}
              style={{
                padding: '10px 12px', borderRadius: 6,
                background: `${changeColor(s.avg_change_pct)}14`,
                border: `1px solid ${changeColor(s.avg_change_pct)}30`,
              }}
            >
              <div style={{ color: '#e5e7eb', fontWeight: 600, fontSize: 12 }}>{s.sector}</div>
              <div style={{ color: changeColor(s.avg_change_pct), fontWeight: 700, fontSize: 15 }}>
                {fmtPct(s.avg_change_pct)}
              </div>
              <div style={{ color: '#6b7280', fontSize: 10 }}>{s.stock_count} stocks</div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

// ── News pulse (reuses the existing MarketPulse endpoint/NewsCard) ───────────────────────────

function NewsPulseCard() {
  const { data: pulse, isLoading } = useSWR<MarketPulse>('pulse-news', () => api.marketPulse(), { refreshInterval: 300_000 });
  if (isLoading) return <div style={card()}>Loading news pulse…</div>;
  if (!pulse) return null;

  const color = pulse.label === 'positive' ? '#4ade80' : pulse.label === 'negative' ? '#f87171' : '#9ca3af';
  const visible = pulse.headlines.slice(0, 5);

  return (
    <div style={card()}>
      {sectionTitle('📰 NEWS PULSE', <span style={{ color, fontWeight: 700, fontSize: 13, textTransform: 'capitalize' }}>{pulse.label} ({pulse.score}/100)</span>)}
      {pulse.themes.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
          {pulse.themes.map(t => (
            <span key={t} style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 10, color: '#d1d5db', background: 'rgba(148,163,184,0.12)', border: '1px solid rgba(148,163,184,0.3)' }}>
              {t}
            </span>
          ))}
        </div>
      )}
      <div style={{ display: 'grid', gap: 8 }}>
        {visible.map((item: NewsItem, i) => <NewsCard key={item.url || i} item={item} />)}
      </div>
    </div>
  );
}

// ── Active squeeze/gamma alerts (admin-only — the only "recent alerts" feed that exists) ─────

function ActiveAlertsCard() {
  const { data, isLoading } = useSWR('pulse-active-alerts', () => api.getSqueezeAlertPerformance({ days_back: 7 }), { refreshInterval: 300_000 });
  if (isLoading) return <div style={card()}>Loading active alerts…</div>;
  if (!data) return null;

  const recent = [...data.recent_alerts]
    .sort((a, b) => b.fired_date.localeCompare(a.fired_date))
    .slice(0, 8);

  return (
    <div style={card()}>
      {sectionTitle('🚨 RECENT SQUEEZE/GAMMA ALERTS', <span style={{ fontSize: 11, color: '#6b7280' }}>last 7 days · admin only</span>)}
      {recent.length === 0 && <div style={{ color: '#4b5563', fontSize: 12 }}>No squeeze-family alerts fired recently.</div>}
      {recent.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {recent.map((a: SqueezeAlertOutcomeRow, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12 }}>
              <span style={{ color: '#6b7280', minWidth: 76 }}>{a.fired_date}</span>
              <span style={{
                fontSize: 10, fontWeight: 700, color: '#f87171', textTransform: 'uppercase',
                padding: '1px 7px', borderRadius: 4, background: 'rgba(248,113,113,0.15)',
              }}>
                {a.alert_type.replace(/_/g, ' ')}
              </span>
              <span style={{ color: '#e5e7eb', fontWeight: 700 }}>{a.symbol}</span>
              <span style={{ color: '#6b7280' }}>@ ${a.alert_price.toFixed(2)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function MarketPulseDashboardPage() {
  const router = useRouter();
  const session = getSession();
  const [market, setMarket] = useState<'US' | 'HK'>('US');

  if (!session) {
    if (typeof window !== 'undefined') router.replace('/login');
    return null;
  }

  return (
    <div style={{ minHeight: '100vh', background: '#0a0a0a', color: '#f9fafb', fontFamily: 'system-ui, sans-serif' }}>
      <div style={{ background: '#111827', borderBottom: '1px solid #1f2937', padding: '0 24px' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto', display: 'flex', alignItems: 'center', gap: 24, height: 56 }}>
          <button onClick={() => router.push('/')} style={{ background: 'none', border: 'none', color: '#6b7280', cursor: 'pointer', fontSize: 13 }}>
            ← Back
          </button>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: '#f9fafb' }}>
            Market Pulse Dashboard
          </h1>
          <span style={{ color: '#6b7280', fontSize: 13 }}>What&apos;s happening right now</span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
            {(['US', 'HK'] as const).map(m => (
              <button
                key={m}
                onClick={() => setMarket(m)}
                style={{
                  padding: '5px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                  background: market === m ? '#2563eb' : 'transparent',
                  color: market === m ? '#fff' : '#9ca3af',
                  border: `1px solid ${market === m ? '#2563eb' : '#1f2937'}`,
                }}
              >
                {m}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div style={{ maxWidth: 1400, margin: '0 auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>
        <RegimeBanner market={market} />
        {market === 'US' && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }} className="market-pulse-two-col">
              <MacroEventsCard />
              <NewsPulseCard />
            </div>
            <MoversAndSectors market={market} />
            {session.role === 'admin' && <ActiveAlertsCard />}
          </>
        )}
        {market === 'HK' && (
          <div style={card()}>
            <div style={{ color: '#4b5569', fontSize: 12 }}>
              Macro events, news pulse, sector heat map, and active alerts are currently US-only
              data sources — HK shows regime status only.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
