/**
 * Options Flow — T324-OPTIONSFLOW-TAB. A new nav tab consolidating Unusual Whales' own
 * multi-page Options Flow menu (Flow Alerts, Options Screener, 0DTE/Multi-leg/Interval Flow,
 * Net Flow) into 5 tabs on one page, matching reports.tsx's own established tab-array +
 * per-tab-component + ?tab= deep-link structure rather than inventing a new layout.
 *
 * Two of the 5 tabs (Flow Alerts, Dark Pool) read from EXISTING backend caches populated by
 * check_options_flow_alerts()/check_dark_pool_alerts() — scoped to whatever that job's own
 * bounded symbol universe (PriceAlert-subscribed + top-K by K-Score) has scanned, NOT a
 * free-text "any ticker" search, a deliberate choice to avoid per-view Unusual Whales API cost
 * (see each tab's own `scope` disclosure banner). The other 3 (Screener, Flow Scanner, Net
 * Flow) call Unusual Whales fresh on open/refresh since no cache exists for them — real
 * universe-wide/live data with no bounded-scope caveat, at the cost of a live API call per view.
 *
 * Contract Look-Up / OI Explorer were deliberately NOT built here — no dedicated Unusual
 * Whales endpoint exists for either (confirmed against UW's own published API reference); both
 * would just re-show the SAME options-chain/OI data the existing stock-detail Options Chain
 * page (yfinance-backed) already covers. Alert Rules was also skipped — no user-defined
 * alert-rule CRUD exists anywhere in this app (thresholds are hardcoded module constants), a
 * genuinely separate, smaller feature scoped for its own future session if wanted.
 */
import { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api } from '@/lib/api';
import type {
  OptionsFlowAlertRecent, DarkPoolAlertRecent, OptionsScreenerRow, OptionTradeRow, MarketTideRow,
} from '@/lib/api';
import { getSession } from '@/lib/auth';

type Tab = 'flow-alerts' | 'dark-pool' | 'screener' | 'scanner' | 'net-flow';

const TABS: { key: Tab; label: string }[] = [
  { key: 'flow-alerts', label: 'Flow Alerts' },
  { key: 'dark-pool',   label: 'Dark Pool' },
  { key: 'screener',    label: 'Options Screener' },
  { key: 'scanner',     label: 'Flow Scanner' },
  { key: 'net-flow',    label: 'Net Flow' },
];
const VALID_TABS: Tab[] = ['flow-alerts', 'dark-pool', 'screener', 'scanner', 'net-flow'];

function tabFromQuery(q: string | string[] | undefined): Tab {
  const v = Array.isArray(q) ? q[0] : q;
  return (VALID_TABS as string[]).includes(v ?? '') ? (v as Tab) : 'flow-alerts';
}

function fmtMoney(n: number | null | undefined): string {
  if (n == null) return '—';
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function ScopeBanner() {
  return (
    <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(56,189,248,0.06)', border: '1px solid rgba(56,189,248,0.25)', fontSize: '11.5px', color: '#7dd3fc', marginBottom: 16 }}>
      Scoped to symbols you have a price alert on, plus the current top-ranked stocks by K-Score
      — not a free-text search of every ticker. This reads from the same scheduled scan your
      email alerts come from, at no extra Unusual Whales API cost.
    </div>
  );
}

function NotConfiguredNotice({ what }: { what: string }) {
  return (
    <div style={{ padding: '16px 20px', borderRadius: 10, background: 'rgba(148,163,184,0.05)', border: '1px solid #1e293b', fontSize: '13px', color: '#94a3b8' }}>
      {what} requires an Unusual Whales subscription configured and enabled in{' '}
      <a href="/settings" style={{ color: '#38bdf8' }}>Settings → Market Pressure Data</a>.
    </div>
  );
}

function LoadingRow() {
  return <div style={{ textAlign: 'center', padding: '40px', color: '#475569', fontSize: '13px' }}>Loading…</div>;
}

function ErrorRow({ what }: { what: string }) {
  return (
    <div style={{ padding: '16px 20px', borderRadius: 10, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', fontSize: '13px', color: '#f87171' }}>
      Failed to load {what}.
    </div>
  );
}

const thStyle: React.CSSProperties = { padding: '8px 10px', color: '#475569', fontWeight: 700, fontSize: '10.5px', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' };
const tdStyle: React.CSSProperties = { padding: '8px 10px' };

// ── Flow Alerts tab (cached) ─────────────────────────────────────────────────

function FlowAlertsTab() {
  const { data, isLoading, error, mutate } = useSWR(
    'options-flow-alerts-recent',
    () => api.getOptionsFlowAlertsRecent({ limit: 100 }),
    { revalidateOnFocus: false, refreshInterval: 60_000 },
  );

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
        <p style={{ fontSize: '12px', color: '#64748b', maxWidth: 700, margin: 0 }}>
          Real Unusual Whales flow-alerts — rule-based sweep/repeated-hits detection over the
          options tape, direction derived from the real ask-side/bid-side premium split.
        </p>
        <button onClick={() => mutate()} style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#94a3b8' }}>↺ Refresh</button>
      </div>
      <ScopeBanner />
      {isLoading && <LoadingRow />}
      {error && <ErrorRow what="flow alerts" />}
      {data && (
        <div style={{ borderRadius: 10, border: '1px solid #1e293b', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
              <thead>
                <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                  {['Date', 'Symbol', 'Dir', 'Type', 'Strike', 'Expiry', 'Premium', 'Vol/OI', 'Side', 'Sweep'].map(h => (
                    <th key={h} style={{ ...thStyle, textAlign: ['Date', 'Symbol'].includes(h) ? 'left' : 'right' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.alerts.map((row: OptionsFlowAlertRecent, i) => (
                  <tr key={`${row.option_chain}-${i}`} style={{ borderBottom: '1px solid #1e293b' }}>
                    <td style={{ ...tdStyle, color: '#64748b' }}>{row.fired_date}</td>
                    <td style={{ ...tdStyle, fontWeight: 700, color: '#e2e8f0' }}>{row.symbol}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 700, color: row.direction === 'bullish' ? '#22c55e' : '#ef4444' }}>{row.direction === 'bullish' ? '▲' : '▼'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#94a3b8', textTransform: 'uppercase' }}>{row.option_type}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.strike != null ? `$${row.strike.toFixed(2)}` : '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.expiry ?? '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 700, color: '#e2e8f0' }}>{fmtMoney(row.total_premium)}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.volume_oi_ratio != null ? `${row.volume_oi_ratio.toFixed(1)}x` : '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', fontSize: 11, color: row.ask_side_dominant ? '#22c55e' : '#f59e0b' }}>{row.ask_side_dominant ? 'Ask (buy)' : 'Bid (sell)'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>{row.has_sweep ? '⚡' : ''}</td>
                  </tr>
                ))}
                {data.alerts.length === 0 && (
                  <tr><td colSpan={10} style={{ padding: 20, textAlign: 'center', color: '#475569' }}>No flow alerts recorded yet — either nothing has qualified, or Unusual Whales isn&apos;t configured.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Dark Pool tab (cached) ───────────────────────────────────────────────────

function DarkPoolTab() {
  const { data, isLoading, error, mutate } = useSWR(
    'dark-pool-alerts-recent',
    () => api.getDarkPoolAlertsRecent({ limit: 100 }),
    { revalidateOnFocus: false, refreshInterval: 60_000 },
  );

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
        <p style={{ fontSize: '12px', color: '#64748b', maxWidth: 700, margin: 0 }}>
          Real large off-exchange block prints ($1M+). A measured fact — real size, price, and
          venue — never a directional signal. See the <a href="/dark-pool-guide" style={{ color: '#38bdf8' }}>Dark Pool Guide</a>.
        </p>
        <button onClick={() => mutate()} style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#94a3b8' }}>↺ Refresh</button>
      </div>
      <ScopeBanner />
      {isLoading && <LoadingRow />}
      {error && <ErrorRow what="dark pool alerts" />}
      {data && (
        <div style={{ borderRadius: 10, border: '1px solid #1e293b', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
              <thead>
                <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                  {['Date', 'Symbol', 'Price', 'Premium'].map(h => (
                    <th key={h} style={{ ...thStyle, textAlign: ['Date', 'Symbol'].includes(h) ? 'left' : 'right' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.alerts.map((row: DarkPoolAlertRecent, i) => (
                  <tr key={`${row.symbol}-${row.fired_date}-${i}`} style={{ borderBottom: '1px solid #1e293b' }}>
                    <td style={{ ...tdStyle, color: '#64748b' }}>{row.fired_date}</td>
                    <td style={{ ...tdStyle, fontWeight: 700, color: '#e2e8f0' }}>{row.symbol}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#94a3b8' }}>${row.alert_price.toFixed(2)}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 700, color: '#38bdf8' }}>{fmtMoney(row.premium)}</td>
                  </tr>
                ))}
                {data.alerts.length === 0 && (
                  <tr><td colSpan={4} style={{ padding: 20, textAlign: 'center', color: '#475569' }}>No dark pool alerts recorded yet — either nothing has qualified, or Unusual Whales isn&apos;t configured.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Options Screener tab (live) ──────────────────────────────────────────────

function ScreenerTab() {
  const [optionType, setOptionType] = useState<'Calls' | 'Puts' | ''>('');
  const [minPremium, setMinPremium] = useState(250_000);
  const [maxDte, setMaxDte] = useState(45);

  const { data, isLoading, error, mutate } = useSWR(
    ['options-screener', optionType, minPremium, maxDte],
    () => api.getOptionsScreener({ option_type: optionType || undefined, min_premium: minPremium, max_dte: maxDte, limit: 150 }),
    { revalidateOnFocus: false },
  );

  return (
    <div>
      <p style={{ fontSize: '12px', color: '#64748b', maxWidth: 700, marginBottom: 12 }}>
        Live, universe-wide scan for unusual options activity by volume/OI/premium/DTE — real,
        current data fetched fresh from Unusual Whales on every filter change.
      </p>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16, alignItems: 'center' }}>
        <select value={optionType} onChange={e => setOptionType(e.target.value as 'Calls' | 'Puts' | '')} style={{ padding: '6px 10px', borderRadius: 6, fontSize: 12, background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}>
          <option value="">Calls + Puts</option>
          <option value="Calls">Calls only</option>
          <option value="Puts">Puts only</option>
        </select>
        <select value={minPremium} onChange={e => setMinPremium(Number(e.target.value))} style={{ padding: '6px 10px', borderRadius: 6, fontSize: 12, background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}>
          <option value={100_000}>$100K+ premium</option>
          <option value={250_000}>$250K+ premium</option>
          <option value={500_000}>$500K+ premium</option>
          <option value={1_000_000}>$1M+ premium</option>
        </select>
        <select value={maxDte} onChange={e => setMaxDte(Number(e.target.value))} style={{ padding: '6px 10px', borderRadius: 6, fontSize: 12, background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}>
          <option value={7}>≤7 DTE</option>
          <option value={45}>≤45 DTE</option>
          <option value={90}>≤90 DTE</option>
          <option value={183}>≤183 DTE</option>
        </select>
        <button onClick={() => mutate()} style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#94a3b8' }}>↺ Refresh</button>
      </div>
      {isLoading && <LoadingRow />}
      {error && <ErrorRow what="the options screener" />}
      {data && !data.available && <NotConfiguredNotice what="The options screener" />}
      {data && data.available && (
        <div style={{ borderRadius: 10, border: '1px solid #1e293b', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
              <thead>
                <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                  {['Symbol', 'Type', 'Strike', 'Expiry', 'Volume', 'OI', 'Premium', 'IV'].map(h => (
                    <th key={h} style={{ ...thStyle, textAlign: h === 'Symbol' ? 'left' : 'right' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row: OptionsScreenerRow, i) => (
                  <tr key={`${row.ticker}-${row.option_symbol}-${i}`} style={{ borderBottom: '1px solid #1e293b' }}>
                    <td style={{ ...tdStyle, fontWeight: 700, color: '#e2e8f0' }}>{row.ticker}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#94a3b8', textTransform: 'uppercase' }}>{row.option_type ?? '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.strike != null ? `$${row.strike.toFixed(2)}` : '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.expiry ?? '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.volume != null ? row.volume.toLocaleString() : '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.open_interest != null ? row.open_interest.toLocaleString() : '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 700, color: '#e2e8f0' }}>{fmtMoney(row.premium)}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.implied_volatility != null ? `${(row.implied_volatility * 100).toFixed(0)}%` : '—'}</td>
                  </tr>
                ))}
                {data.rows.length === 0 && (
                  <tr><td colSpan={8} style={{ padding: 20, textAlign: 'center', color: '#475569' }}>No contracts match these filters.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Flow Scanner tab (live) — 0DTE / Multi-leg / Interval, one shared endpoint ──

type ScannerPreset = 'interval' | '0dte' | 'multileg';

function ScannerTab() {
  const [preset, setPreset] = useState<ScannerPreset>('interval');
  const [minPremium, setMinPremium] = useState(50_000);

  const filterArgs = useMemo(() => {
    if (preset === '0dte') return { max_dte: 0, min_premium: minPremium, limit: 150 };
    if (preset === 'multileg') return { is_multi_leg: true, min_premium: minPremium, limit: 150 };
    return { min_premium: minPremium, limit: 150 };
  }, [preset, minPremium]);

  const { data, isLoading, error, mutate } = useSWR(
    ['option-trades', preset, minPremium],
    () => api.getOptionTrades(filterArgs),
    { revalidateOnFocus: false },
  );

  return (
    <div>
      <p style={{ fontSize: '12px', color: '#64748b', maxWidth: 700, marginBottom: 12 }}>
        Live raw options-tape prints — one shared feed covering three views (Unusual Whales
        itself has no separate endpoint for 0DTE or multi-leg activity; both are filters on the
        same tape).
      </p>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16, alignItems: 'center' }}>
        {(['interval', '0dte', 'multileg'] as ScannerPreset[]).map(p => (
          <button
            key={p}
            onClick={() => setPreset(p)}
            style={{
              padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
              border: preset === p ? '1px solid #6d28d9' : '1px solid #1e293b',
              background: preset === p ? 'rgba(109,40,217,0.2)' : 'transparent',
              color: preset === p ? '#a78bfa' : '#6b7280',
            }}
          >
            {p === 'interval' ? 'Interval Flow' : p === '0dte' ? '0DTE Flow' : 'Multi-leg Flow'}
          </button>
        ))}
        <select value={minPremium} onChange={e => setMinPremium(Number(e.target.value))} style={{ padding: '6px 10px', borderRadius: 6, fontSize: 12, background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}>
          <option value={10_000}>$10K+ premium</option>
          <option value={50_000}>$50K+ premium</option>
          <option value={250_000}>$250K+ premium</option>
        </select>
        <button onClick={() => mutate()} style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#94a3b8' }}>↺ Refresh</button>
      </div>
      {isLoading && <LoadingRow />}
      {error && <ErrorRow what="the flow scanner" />}
      {data && !data.available && <NotConfiguredNotice what="The flow scanner" />}
      {data && data.available && (
        <div style={{ borderRadius: 10, border: '1px solid #1e293b', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
              <thead>
                <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                  {['Symbol', 'Type', 'Strike', 'Expiry', 'Price', 'Size', 'Premium', 'Multi-leg'].map(h => (
                    <th key={h} style={{ ...thStyle, textAlign: h === 'Symbol' ? 'left' : 'right' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row: OptionTradeRow, i) => (
                  <tr key={`${row.ticker}-${row.option_symbol}-${i}`} style={{ borderBottom: '1px solid #1e293b' }}>
                    <td style={{ ...tdStyle, fontWeight: 700, color: '#e2e8f0' }}>{row.ticker}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#94a3b8', textTransform: 'uppercase' }}>{row.option_type ?? '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.strike != null ? `$${row.strike.toFixed(2)}` : '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.expiry ?? '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.price != null ? `$${row.price.toFixed(2)}` : '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#64748b' }}>{row.size != null ? row.size.toLocaleString() : '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 700, color: '#e2e8f0' }}>{fmtMoney(row.premium)}</td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>{row.is_multi_leg ? '✓' : ''}</td>
                  </tr>
                ))}
                {data.rows.length === 0 && (
                  <tr><td colSpan={8} style={{ padding: 20, textAlign: 'center', color: '#475569' }}>No trades match these filters.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Net Flow tab (live) ───────────────────────────────────────────────────────

function NetFlowTab() {
  const { data, isLoading, error, mutate } = useSWR(
    'market-tide',
    () => api.getMarketTide(),
    { revalidateOnFocus: false },
  );

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
        <p style={{ fontSize: '12px', color: '#64748b', maxWidth: 700, margin: 0 }}>
          Market-wide net call vs. put options premium over time — Unusual Whales&apos; own
          real aggregate sentiment measure, not a per-symbol figure.
        </p>
        <button onClick={() => mutate()} style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#94a3b8' }}>↺ Refresh</button>
      </div>
      {isLoading && <LoadingRow />}
      {error && <ErrorRow what="net flow" />}
      {data && !data.available && <NotConfiguredNotice what="Net flow" />}
      {data && data.available && (
        <div style={{ borderRadius: 10, border: '1px solid #1e293b', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
              <thead>
                <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                  {['Time', 'Net Call Premium', 'Net Put Premium'].map(h => (
                    <th key={h} style={{ ...thStyle, textAlign: h === 'Time' ? 'left' : 'right' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row: MarketTideRow, i) => (
                  <tr key={`${row.timestamp}-${i}`} style={{ borderBottom: '1px solid #1e293b' }}>
                    <td style={{ ...tdStyle, color: '#64748b' }}>{row.timestamp ?? '—'}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 700, color: '#22c55e' }}>{fmtMoney(row.net_call_premium)}</td>
                    <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 700, color: '#ef4444' }}>{fmtMoney(row.net_put_premium)}</td>
                  </tr>
                ))}
                {data.rows.length === 0 && (
                  <tr><td colSpan={3} style={{ padding: 20, textAlign: 'center', color: '#475569' }}>No market-tide data returned.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function OptionsFlowPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    setAuthed(true);
  }, [router]);

  const [tab, setTab] = useState<Tab>(() => tabFromQuery(router.query.tab));
  useEffect(() => {
    if (router.isReady) setTab(tabFromQuery(router.query.tab));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router.isReady, router.query.tab]);

  if (!authed) return null;

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px 0' }}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: '#e2e8f0', marginBottom: 4 }}>Options Flow</h1>
        <p style={{ fontSize: 12, color: '#475569' }}>
          Flow Alerts · Dark Pool · Options Screener · Flow Scanner · Net Flow — real Unusual Whales data.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid #1e293b', marginBottom: 20 }}>
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => { setTab(t.key); router.replace({ pathname: '/options-flow', query: { tab: t.key } }, undefined, { shallow: true }); }}
            style={{
              background: 'none', border: 'none', cursor: 'pointer', padding: '12px 16px', fontSize: 13, fontWeight: 500,
              color: tab === t.key ? '#f9fafb' : '#6b7280',
              borderBottom: tab === t.key ? '2px solid #6d28d9' : '2px solid transparent',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'flow-alerts' && <FlowAlertsTab />}
      {tab === 'dark-pool'   && <DarkPoolTab />}
      {tab === 'screener'    && <ScreenerTab />}
      {tab === 'scanner'     && <ScannerTab />}
      {tab === 'net-flow'    && <NetFlowTab />}
    </div>
  );
}
