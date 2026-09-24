/**
 * Reports — T255-REPORTS-TAB Phase 1. Consolidated per-market (US/HK) reports view,
 * composing existing endpoints (see .claude/CLAUDE.md "Research: Reports Tab" for the full
 * endpoint inventory this was built from). Same tab-array + per-tab-component structure as
 * intelligence.tsx, which this page follows deliberately rather than inventing a new layout.
 *
 * Five tabs: Trend, Assets, Top Stocks, Money Flow, Self-Tuning. Market toggle (US/HK) scopes
 * the market-specific tabs; Self-Tuning is market-agnostic (signal/paper-trading calibration is
 * global). AUD-REPORTSTAB-DEDUP (2026-09-22): the former News & Macro and CAPE / Bubble Warning
 * tabs were removed as near-duplicates of intelligence.tsx's own Overview/Bubble Warning tabs —
 * see CapeTab/NewsTab's own removal note further down for the full reasoning. Their nav items
 * now deep-link straight to /intelligence.
 */
import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api, type InstitutionalFund, type RankingRow, type SectorGroup } from '@/lib/api';
import { getSession } from '@/lib/auth';

type Market = 'US' | 'HK';
type Tab = 'trend' | 'assets' | 'top' | 'flow' | 'smartmoney' | 'funds' | 'tuning';

// AUD-REPORTSTAB-DEDUP (2026-09-22): 'News & Macro' and 'CAPE / Bubble Warning' tabs were
// removed from here — both were near-duplicates of intelligence.tsx's own 'Overview' and
// 'Bubble Warning' tabs (same /events/overview and /events/valuation/cape endpoints), and the
// intelligence.tsx versions were strictly more complete (Market Pulse card, cross-asset card,
// composite leaders, CAPE staleness warning + history table — none of which this page had).
// The two Reports nav items for them now deep-link straight to /intelligence instead — see
// _app.tsx's NAV_GROUPS.
const TABS: { key: Tab; label: string }[] = [
  { key: 'trend',  label: 'Market Trend' },
  { key: 'assets', label: 'Key Assets' },
  { key: 'top',    label: 'Top Stocks' },
  { key: 'flow',   label: 'Money Flow' },
  { key: 'smartmoney', label: 'Who to Follow' },
  { key: 'funds', label: 'Big Funds' },
  { key: 'tuning', label: 'Self-Tuning' },
];

function fmtPct(n: number | null | undefined, digits = 1): string {
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function fmtNum(n: number | null | undefined, digits = 0): string {
  if (n == null) return '—';
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function pctColor(n: number | null | undefined): string {
  if (n == null) return '#6b7280';
  return n >= 0 ? '#4ade80' : '#f87171';
}

const card: React.CSSProperties = { background: '#111827', border: '1px solid #1f2937', borderRadius: 10, padding: 18 };
const sectionTitle: React.CSSProperties = { fontSize: 13, fontWeight: 700, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 12 };

// ── Market Trend ───────────────────────────────────────────────────────────────
function TrendTab({ market }: { market: Market }) {
  const { data: regime } = useSWR(`regime-${market}`, () => api.regime(market));
  const { data: fearGreed } = useSWR('fear-greed', () => api.fearGreed());
  const { data: breadth } = useSWR(`breadth-${market}`, () => api.marketBreadth(market));

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
      <div style={card}>
        <div style={sectionTitle}>Market Regime ({market})</div>
        {regime ? (
          <>
            <div style={{ fontSize: 22, fontWeight: 700, textTransform: 'capitalize', color: regime.state === 'bull' ? '#4ade80' : regime.state === 'bear' || regime.state === 'risk_off' ? '#f87171' : '#f59e0b' }}>
              {regime.state.replace('_', ' ')}
            </div>
            <div style={{ fontSize: 13, color: '#9ca3af', marginTop: 8, lineHeight: 1.8 }}>
              <div>VIX: <span style={{ color: '#e5e7eb' }}>{fmtNum(regime.vix, 1)}</span></div>
              <div>SPY 20d return: <span style={{ color: pctColor(regime.spy_20d_ret != null ? regime.spy_20d_ret * 100 : null) }}>{fmtPct(regime.spy_20d_ret != null ? regime.spy_20d_ret * 100 : null)}</span></div>
              <div>Breadth: <span style={{ color: regime.breadth_weak ? '#f87171' : '#4ade80' }}>{regime.breadth_weak ? 'Weak' : 'Healthy'}</span></div>
            </div>
          </>
        ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
      </div>

      <div style={card}>
        <div style={sectionTitle}>Fear &amp; Greed Index</div>
        {fearGreed ? (
          <>
            <div style={{ fontSize: 22, fontWeight: 700, color: fearGreed.score >= 55 ? '#4ade80' : fearGreed.score <= 45 ? '#f87171' : '#f59e0b' }}>
              {fearGreed.score.toFixed(1)} <span style={{ fontSize: 14, fontWeight: 400, color: '#9ca3af' }}>{fearGreed.rating}</span>
            </div>
            <div style={{ fontSize: 13, color: '#9ca3af', marginTop: 8, lineHeight: 1.8 }}>
              {fearGreed.sp500_regime && <div>S&amp;P 500: <span style={{ textTransform: 'capitalize' }}>{fearGreed.sp500_regime} market</span></div>}
              {fearGreed.sp500_vs_ma200_pct != null && <div>vs. 200MA: <span style={{ color: pctColor(fearGreed.sp500_vs_ma200_pct) }}>{fmtPct(fearGreed.sp500_vs_ma200_pct)}</span></div>}
            </div>
          </>
        ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
      </div>

      <div style={card}>
        <div style={sectionTitle}>Market Breadth ({market})</div>
        {breadth ? (
          <>
            <div style={{ fontSize: 22, fontWeight: 700, color: breadth.color }}>{fmtPct(breadth.breadth_pct)}</div>
            <div style={{ fontSize: 13, color: '#9ca3af', marginTop: 8 }}>
              {breadth.above_200ma} above / {breadth.below_200ma} below 200MA ({breadth.total} total) — <span style={{ color: breadth.color }}>{breadth.label}</span>
            </div>
          </>
        ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
      </div>
    </div>
  );
}

// ── Key Asset Performance ────────────────────────────────────────────────────
function AssetsTab({ market }: { market: Market }) {
  const { data: overview } = useSWR('market-overview', () => api.marketOverview());
  const { data: rotation } = useSWR('sector-rotation-etf', () => api.sectorRotationEtf());

  const relevantIndices = overview?.filter(i => market === 'HK' ? i.market === 'HK' || i.ticker.includes('HSI') : i.market !== 'HK');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={card}>
        <div style={sectionTitle}>Index &amp; Benchmark Performance ({market})</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
          {(relevantIndices ?? []).map(idx => (
            <div key={idx.ticker} style={{ padding: 12, background: '#0b1120', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#9ca3af' }}>{idx.name}</div>
              <div style={{ fontSize: 18, fontWeight: 700 }}>{idx.price != null ? idx.price.toFixed(2) : '—'}</div>
              <div style={{ fontSize: 13, color: pctColor(idx.change_pct) }}>{fmtPct(idx.change_pct)}</div>
            </div>
          ))}
          {!overview && <div style={{ color: '#6b7280' }}>Loading…</div>}
        </div>
      </div>

      {market === 'US' ? (
        <div style={card}>
          <div style={sectionTitle}>US Sector ETF Rotation vs. SPY</div>
          {rotation?.sectors ? (
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ color: '#9ca3af', textAlign: 'left' }}>
                  <th style={{ padding: '6px 8px' }}>Sector</th>
                  <th style={{ padding: '6px 8px' }}>ETF</th>
                  <th style={{ padding: '6px 8px' }}>1W</th>
                  <th style={{ padding: '6px 8px' }}>1M</th>
                  <th style={{ padding: '6px 8px' }}>3M</th>
                  <th style={{ padding: '6px 8px' }}>vs SPY (1M)</th>
                  <th style={{ padding: '6px 8px' }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {rotation.sectors.map(s => (
                  <tr key={s.etf} style={{ borderTop: '1px solid #1f2937' }}>
                    <td style={{ padding: '6px 8px' }}>{s.sector}</td>
                    <td style={{ padding: '6px 8px', color: '#9ca3af' }}>{s.etf}</td>
                    <td style={{ padding: '6px 8px', color: pctColor(s.ret_1w) }}>{fmtPct(s.ret_1w)}</td>
                    <td style={{ padding: '6px 8px', color: pctColor(s.ret_1m) }}>{fmtPct(s.ret_1m)}</td>
                    <td style={{ padding: '6px 8px', color: pctColor(s.ret_3m) }}>{fmtPct(s.ret_3m)}</td>
                    <td style={{ padding: '6px 8px', color: pctColor(s.vs_spy_1m) }}>{fmtPct(s.vs_spy_1m)}</td>
                    <td style={{ padding: '6px 8px', textTransform: 'capitalize', color: s.status === 'leading' ? '#4ade80' : s.status === 'lagging' ? '#f87171' : '#9ca3af' }}>{s.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
        </div>
      ) : (
        <div style={{ ...card, color: '#f59e0b', fontSize: 13 }}>
          No HK sector-ETF rotation endpoint yet — see T255-REPORTS-TAB tracker item (Phase 2, item 5).
        </div>
      )}
    </div>
  );
}

// ── Top Performing Stocks ────────────────────────────────────────────────────
function TopStocksTab({ market }: { market: Market }) {
  const { data } = useSWR(`rankings-${market}`, () => api.rankings(market));
  const { data: sectors } = useSWR('sector-performance', () => api.sectorPerformance());

  const top = (data?.rankings ?? [])
    .filter((r): r is RankingRow & { score: number } => r.score != null)
    .sort((a, b) => b.score - a.score)
    .slice(0, 20);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={card}>
        <div style={sectionTitle}>Top K-Score Stocks ({market})</div>
        {data ? (
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: '#9ca3af', textAlign: 'left' }}>
                <th style={{ padding: '6px 8px' }}>#</th>
                <th style={{ padding: '6px 8px' }}>Symbol</th>
                <th style={{ padding: '6px 8px' }}>Name</th>
                <th style={{ padding: '6px 8px' }}>Sector</th>
                <th style={{ padding: '6px 8px' }}>K-Score</th>
              </tr>
            </thead>
            <tbody>
              {top.map((r, i) => (
                <tr key={r.symbol} style={{ borderTop: '1px solid #1f2937' }}>
                  <td style={{ padding: '6px 8px', color: '#6b7280' }}>{i + 1}</td>
                  <td style={{ padding: '6px 8px', fontWeight: 600 }}>{r.symbol}</td>
                  <td style={{ padding: '6px 8px', color: '#9ca3af' }}>{r.name}</td>
                  <td style={{ padding: '6px 8px', color: '#9ca3af' }}>{r.sector ?? '—'}</td>
                  <td style={{ padding: '6px 8px', color: r.score >= 70 ? '#4ade80' : r.score >= 40 ? '#f59e0b' : '#f87171', fontWeight: 700 }}>{r.score.toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
      </div>

      <div style={card}>
        <div style={sectionTitle}>Sector Performance (Today)</div>
        {sectors ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
            {(sectors as SectorGroup[]).map(s => (
              <div key={s.sector} style={{ padding: 10, background: '#0b1120', borderRadius: 8 }}>
                <div style={{ fontSize: 12, color: '#9ca3af' }}>{s.sector}</div>
                <div style={{ fontSize: 16, fontWeight: 700, color: pctColor(s.avg_change_pct) }}>{fmtPct(s.avg_change_pct)}</div>
                <div style={{ fontSize: 11, color: '#6b7280' }}>{s.stock_count} stocks</div>
              </div>
            ))}
          </div>
        ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
      </div>
    </div>
  );
}

const TRAJECTORY_COLOR: Record<string, string> = {
  'Emerging Leader': '#4ade80',
  'Established Leader': '#86efac',
  'Fading Leader': '#f59e0b',
  'Emerging Laggard': '#93c5fd',
  'Established Laggard': '#9ca3af',
  'Fading Laggard': '#f87171',
};

// ── Money Flow ────────────────────────────────────────────────────────────────
function FlowTab({ market }: { market: Market }) {
  const { data: rotation } = useSWR(`sector-rotation-kscore-${market}`, () => api.sectorRotation(market));
  const { data: rankings } = useSWR(`rankings-flow-${market}`, () => api.rankings(market));
  // T255-REPORTS-TAB Phase 2: HK Stock-Connect southbound top-N money flow — only fetched
  // when the HK tab is active, matching the market-scoping already used above.
  const { data: hkFlow } = useSWR(market === 'HK' ? 'hk-connect-flow-leaderboard' : null, () => api.hkConnectFlowLeaderboard());
  // T220-G/T258: US-only, K-Score-based rotation — distinct source from the RS-based
  // `rotation` fetch above (which itself may be scoped by market).
  const { data: kscoreRotation } = useSWR('sector-rotation-kscore-t258', () => api.sectorRotationKscore());
  const kscoreEntries = kscoreRotation
    ? Object.entries(kscoreRotation).sort((a, b) => (b[1].recent_kscore ?? -Infinity) - (a[1].recent_kscore ?? -Infinity))
    : [];

  // "Best stocks in the leading sector" — client-side join of sector momentum + rankings,
  // scoped to THIS app's existing universe. A whole-market screener (per the user's explicit
  // "not just my system" clarification) is tracked as new backend work, not yet built —
  // see T255-REPORTS-TAB Phase 2 item 4.
  const leadingSector = rotation?.sectors
    ?.filter(s => s.leading_pct != null)
    .sort((a, b) => (b.leading_pct ?? 0) - (a.leading_pct ?? 0))[0];

  const bestInLeadingSector = leadingSector && rankings
    ? rankings.rankings
        .filter(r => r.sector === leadingSector.sector && r.score != null)
        .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
        .slice(0, 8)
    : [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={card}>
        <div style={sectionTitle}>Sector Momentum (Relative Strength, {market})</div>
        {rotation?.sectors ? (
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: '#9ca3af', textAlign: 'left' }}>
                <th style={{ padding: '6px 8px' }}>Sector</th>
                <th style={{ padding: '6px 8px' }}>Avg RS</th>
                <th style={{ padding: '6px 8px' }}>RS Change</th>
                <th style={{ padding: '6px 8px' }}>Leading %</th>
                <th style={{ padding: '6px 8px' }}># Stocks</th>
              </tr>
            </thead>
            <tbody>
              {rotation.sectors.map(s => (
                <tr key={s.sector} style={{ borderTop: '1px solid #1f2937', background: s.sector === leadingSector?.sector ? 'rgba(74,222,128,0.06)' : undefined }}>
                  <td style={{ padding: '6px 8px', fontWeight: s.sector === leadingSector?.sector ? 700 : 400 }}>{s.sector}</td>
                  <td style={{ padding: '6px 8px' }}>{fmtNum(s.avg_rs, 1)}</td>
                  <td style={{ padding: '6px 8px', color: pctColor(s.rs_change) }}>{s.rs_change != null ? fmtNum(s.rs_change, 1) : '—'}</td>
                  <td style={{ padding: '6px 8px' }}>{fmtPct(s.leading_pct)}</td>
                  <td style={{ padding: '6px 8px', color: '#9ca3af' }}>{s.stock_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
      </div>

      <div style={card}>
        <div style={sectionTitle}>Sector K-Score Momentum &amp; Trajectory (US)</div>
        <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 10 }}>
          Rank trajectory compares this week&apos;s sector rank against the snapshot from ~4
          weeks ago — Emerging/Fading track a sector&apos;s rank moving in/out of the top half,
          Established means the rank held steady. Computed weekly (Sunday); US-only for now.
          A sector newly becoming &ldquo;Emerging Leader&rdquo; also proactively emails your
          alert-subscribed address with the top stocks in it — no need to check this table
          yourself every week; see the Alerts Guide for details.
        </div>
        {kscoreRotation ? (
          kscoreEntries.length > 0 ? (
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ color: '#9ca3af', textAlign: 'left' }}>
                  <th style={{ padding: '6px 8px' }}>Sector</th>
                  <th style={{ padding: '6px 8px' }}>K-Score</th>
                  <th style={{ padding: '6px 8px' }}>4wk Δ</th>
                  <th style={{ padding: '6px 8px' }}>Rank</th>
                  <th style={{ padding: '6px 8px' }}>Trajectory</th>
                </tr>
              </thead>
              <tbody>
                {kscoreEntries.map(([sector, e]) => (
                  <tr key={sector} style={{ borderTop: '1px solid #1f2937' }}>
                    <td style={{ padding: '6px 8px', fontWeight: 600 }}>{sector}</td>
                    <td style={{ padding: '6px 8px' }}>{e.recent_kscore != null ? fmtNum(e.recent_kscore, 1) : '—'}</td>
                    <td style={{ padding: '6px 8px', color: pctColor(e.delta) }}>{e.delta != null ? fmtNum(e.delta, 1) : '—'}</td>
                    <td style={{ padding: '6px 8px', color: '#9ca3af' }}>
                      {e.rank != null ? `#${e.rank}${e.prior_rank != null ? ` (was #${e.prior_rank})` : ''}` : '—'}
                    </td>
                    <td style={{ padding: '6px 8px', fontWeight: 600, color: e.trajectory ? TRAJECTORY_COLOR[e.trajectory] ?? '#9ca3af' : '#6b7280' }}>
                      {e.trajectory ?? 'Not enough history yet'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div style={{ color: '#6b7280', fontSize: 13 }}>No sector rotation data yet — computed weekly.</div>
        ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
      </div>

      {market === 'HK' && (
        <div style={card}>
          <div style={sectionTitle}>HK Stock-Connect Southbound Flow — Top Net Buys (5d)</div>
          <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 10 }}>
            Net mainland money flowing into HK stocks via Southbound Stock Connect over the
            last 5 trading days — real, measured positioning, not a prediction. Sourced from a
            daily holdings-change snapshot (net accumulation = net buying); computed nightly.
          </div>
          {hkFlow ? (
            hkFlow.length > 0 ? (
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ color: '#9ca3af', textAlign: 'left' }}>
                    <th style={{ padding: '6px 8px' }}>Symbol</th>
                    <th style={{ padding: '6px 8px' }}>Net Buy (HKD M)</th>
                  </tr>
                </thead>
                <tbody>
                  {hkFlow.map(f => (
                    <tr key={f.symbol} style={{ borderTop: '1px solid #1f2937' }}>
                      <td style={{ padding: '6px 8px', fontWeight: 700 }}>{f.symbol}</td>
                      <td style={{ padding: '6px 8px', color: '#4ade80' }}>{fmtNum(f.net_buy_hkd, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div style={{ color: '#6b7280', fontSize: 13 }}>No net-buy flow data in this window yet.</div>
          ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
        </div>
      )}

      {leadingSector && (
        <div style={card}>
          <div style={sectionTitle}>Best Stocks in Leading Sector — {leadingSector.sector}</div>
          <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 10 }}>
            Ranked from stocks already in your system. A whole-market screener (surfacing candidates
            beyond your existing watchlist, with a one-click &quot;add to my system&quot; action) is
            planned but not yet built.
          </div>
          {bestInLeadingSector.length > 0 ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10 }}>
              {bestInLeadingSector.map(r => (
                <div key={r.symbol} style={{ padding: 10, background: '#0b1120', borderRadius: 8 }}>
                  <div style={{ fontWeight: 700 }}>{r.symbol}</div>
                  <div style={{ fontSize: 11, color: '#9ca3af' }}>{r.name}</div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: (r.score ?? 0) >= 70 ? '#4ade80' : '#f59e0b' }}>K {r.score?.toFixed(0)}</div>
                </div>
              ))}
            </div>
          ) : <div style={{ color: '#6b7280', fontSize: 13 }}>No ranked stocks in this sector yet.</div>}
        </div>
      )}
    </div>
  );
}

// ── Self-Tuning / Backtest Reports ───────────────────────────────────────────
// ── Who to Follow (AUD-SMARTMONEY, 2026-09-22) ─────────────────────────────────
// Congressional disclosures, ranked by PERSON rather than by ticker. Two measured facts decide
// whether this tab is honest or a gimmick, and both are rendered rather than buried:
//
//   1. ENTRY IS THE DISCLOSURE DATE, NEVER THE TRADE DATE. Filings lag the trade by a MEDIAN of
//      33 days (mean 45, worst 323). The same purchases return +3.73% over 21 days from the
//      trade date but +3.15% from disclosure — roughly 0.6pp of the apparent edge is already
//      gone before anyone outside could act. Quoting the trade-date number would advertise a
//      return the reader cannot reach. Exactly the same class of error as quoting post-earnings
//      drift that includes the untradeable overnight gap.
//   2. A MINORITY OF ROWS STILL HAVE NO BUY/SELL DIRECTION, and the count is now served by the
//      API rather than written here, because the previous hardcoded "7,691 of 9,453" survived
//      the repair that made it 776 and went on stating a number that no longer described the
//      data. AUD-UWCONGRESS-FIELDNAMES found those rows were never missing direction at all —
//      the parser was reading key names the feed does not send, discarding 81% of the dataset
//      on arrival. Repairing it took the followable leaderboard from 3 traders to 17 and made
//      Trump rankable for the first time.
//
// The below-floor table is deliberately shown rather than truncated away: seeing that the top of
// an 8-buy-minimum list sits among dozens of 1-3 buy names is what stops a reader treating the
// leaders as a ranking of skill.
function SmartMoneyTab() {
  const { data, error } = useSWR('events-smart-money', () => api.eventsSmartMoney());
  const [showBelowFloor, setShowBelowFloor] = useState(false);

  if (error) return <div style={card}><div style={{ color: '#f87171' }}>Failed to load.</div></div>;
  if (!data) return <div style={card}><div style={{ color: '#6b7280' }}>Loading…</div></div>;

  const followable = data.traders.filter(t => t.sample_is_adequate);
  const belowFloor = data.traders.filter(t => !t.sample_is_adequate);

  const row = (t: typeof data.traders[number], muted: boolean) => (
    <tr key={`${t.name}-${t.chamber}`} style={{ borderTop: '1px solid #1f2937', opacity: muted ? 0.55 : 1 }}>
      <td style={{ padding: '7px 8px', fontWeight: muted ? 400 : 600 }}>
        {t.name}
        {t.party ? <span style={{ color: '#6b7280', fontSize: 11, marginLeft: 6 }}>({t.party})</span> : null}
      </td>
      <td style={{ padding: '7px 8px', color: '#9ca3af' }}>{t.chamber ?? '—'}</td>
      <td style={{ padding: '7px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{t.n_buys}</td>
      <td style={{ padding: '7px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: pctColor(t.avg_21d_pct), fontWeight: muted ? 400 : 700 }}>
        {fmtPct(t.avg_21d_pct, 2)}
      </td>
      <td style={{ padding: '7px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: '#9ca3af' }}>
        {t.pct_up != null ? `${t.pct_up.toFixed(0)}%` : '—'}
      </td>
      <td style={{ padding: '7px 8px', color: '#6b7280', fontSize: 12 }}>{t.latest_disclosure ?? '—'}</td>
    </tr>
  );

  const head = (
    <thead>
      <tr style={{ color: '#9ca3af', textAlign: 'left', fontSize: 12 }}>
        <th style={{ padding: '6px 8px' }}>Name</th>
        <th style={{ padding: '6px 8px' }}>Chamber</th>
        <th style={{ padding: '6px 8px', textAlign: 'right' }}>Buys</th>
        <th style={{ padding: '6px 8px', textAlign: 'right' }}>Avg {data.horizon_days}d</th>
        <th style={{ padding: '6px 8px', textAlign: 'right' }}>% Up</th>
        <th style={{ padding: '6px 8px' }}>Latest Filing</th>
      </tr>
    </thead>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* The lag caveat goes FIRST and unmissable. If a reader takes one thing from this tab it
          must be that these returns are measured from the day the filing became public. */}
      <div style={{ ...card, borderColor: '#78350f', background: 'rgba(120,53,15,0.15)' }}>
        <div style={{ ...sectionTitle, color: '#fbbf24', marginBottom: 8 }}>Read this before the tables</div>
        <div style={{ fontSize: 13, color: '#e5e7eb', lineHeight: 1.65 }}>
          Every return below is measured from the <strong>disclosure date</strong> — the day the filing
          became public and you could actually have acted — not from the trade date. Congressional filings
          lag the trade by a <strong>median of 33 days</strong> (mean 45, worst 323). Measured on this
          platform&apos;s own data, the same purchases return <strong style={{ color: '#4ade80' }}>+3.73%</strong> over
          {' '}{data.horizon_days} days from the trade date but only <strong style={{ color: '#fbbf24' }}>+3.15%</strong>
          {' '}from disclosure. That ~0.6pp gap is edge that had already happened before anyone outside
          could see the filing.
        </div>
      </div>

      <div style={card}>
        <div style={sectionTitle}>Followable — at least {data.min_trades_for_adequacy} disclosed buys</div>
        <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 12 }}>
          {data.n_followable} of {data.traders.length} tracked names clear the sample floor. Buys only —
          a disclosed sale is a different decision and is not averaged in here.
        </div>
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          {head}
          <tbody>{followable.map(t => row(t, false))}</tbody>
        </table>
        {followable.some(t => (t.avg_21d_pct ?? 0) < 0) && (
          <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 12, lineHeight: 1.6 }}>
            The negative row is kept deliberately. A leaderboard that only ever shows winners gives no
            sense of the spread, and the spread here is what tells you whether the top of the list is
            skill or the tail of a small sample.
          </div>
        )}
      </div>

      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <div style={{ ...sectionTitle, marginBottom: 0 }}>Below the sample floor ({belowFloor.length})</div>
          <button
            onClick={() => setShowBelowFloor(v => !v)}
            style={{ marginLeft: 'auto', background: 'none', border: '1px solid #1f2937', borderRadius: 6, color: '#9ca3af', fontSize: 12, padding: '4px 10px', cursor: 'pointer' }}
          >
            {showBelowFloor ? 'Hide' : 'Show'}
          </button>
        </div>
        <div style={{ fontSize: 12, color: '#6b7280', lineHeight: 1.6 }}>
          Fewer than {data.min_trades_for_adequacy} disclosed buys. Shown so the leaders above can be read
          in context — <strong>do not rank on these</strong>. A name with one buy and a +12% print is noise,
          not a track record.
        </div>
        {showBelowFloor && (
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginTop: 12 }}>
            {head}
            <tbody>{belowFloor.map(t => row(t, true))}</tbody>
          </table>
        )}
      </div>

      <div style={card}>
        <div style={sectionTitle}>Tracked, but not followable ({data.direction_unknown.length})</div>
        <div style={{ fontSize: 13, color: '#e5e7eb', lineHeight: 1.65, marginBottom: 12 }}>
          These names have disclosures in the database but the feed they arrive on omits buy/sell
          entirely. <strong>A filing here may be a sale.</strong> There is no way to compute a
          follow-return from them, so they are listed rather than ranked — including the most active
          name on the platform.
        </div>
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ color: '#9ca3af', textAlign: 'left', fontSize: 12 }}>
              <th style={{ padding: '6px 8px' }}>Name</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Filings</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>On Tracked Stocks</th>
              <th style={{ padding: '6px 8px' }}>Latest Trade</th>
            </tr>
          </thead>
          <tbody>
            {data.direction_unknown.map(u => (
              <tr key={u.name} style={{ borderTop: '1px solid #1f2937' }}>
                <td style={{ padding: '7px 8px' }}>{u.name}</td>
                <td style={{ padding: '7px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(u.n_records)}</td>
                <td style={{ padding: '7px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: u.on_tracked_stock ? '#e5e7eb' : '#6b7280' }}>{fmtNum(u.on_tracked_stock)}</td>
                <td style={{ padding: '7px 8px', color: '#6b7280', fontSize: 12 }}>{u.latest_trade ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {/* AUD-UWCONGRESS-NAMEMERGE: the feeds' differing spellings ("Rohit Khanna" / "Ro Khanna")
            are now merged onto the roster's canonical name, so one person no longer ranks twice.
            What remains true is narrower and still worth saying: a member can hold BOTH followable
            trades and older no-direction filings, so the same name legitimately appears in both
            tables and the counts still must not be summed. */}
        <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 12, lineHeight: 1.6 }}>
          A member can appear in both tables at once — followable buys above, older filings of unknown
          direction here. That is not double-counting on our side, but the two counts describe different
          rows and should not be added together. Name spellings across feeds are reconciled against the
          official roster; anything the roster cannot resolve unambiguously is deliberately left alone
          rather than guessed at.
        </div>
      </div>

      <div style={card}>
        <div style={sectionTitle}>What this is and is not</div>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: '#9ca3af', lineHeight: 1.8 }}>
          {data.caveats.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      </div>
    </div>
  );
}

// ── Big Funds (AUD-INSTFOLLOW) ─────────────────────────────────────────────────
// 13F position adds for 16 named managers, entered at the FILING date. Three findings from the
// first live run are baked into how this renders, because each one makes the page look MORE
// authoritative while being wrong:
//
//   1. THE HEADLINE NUMBER IS ALPHA, NOT THE RAW RETURN. Raw, all sixteen managers came out
//      negative (-0.35% to -16.66%), which reads as a damning verdict on professional
//      investors. SPY fell 2.44% over the identical window; Duquesne's "-0.35%" was +2.1pp of
//      alpha. The raw column is still shown, next to the benchmark, so the subtraction is
//      checkable rather than trusted.
//   2. THIS IS ONE QUARTER OVER ONE WINDOW — NOT A TRACK RECORD. Every fund shares essentially
//      the same window, so one market episode drives much of the spread. The page says so above
//      the table, not in a footnote.
//   3. A 13F IS THE LONG BOOK ONLY. For Citadel, Millennium, Two Sigma, AQR and Renaissance the
//      disclosed longs are one leg of a hedged position whose shorts are invisible here, so
//      their large negative alphas may be the hedge working. Those rows are marked inline.
//
// Staleness is per row because coverage varies enormously — UW's Scion data is ~359 days old
// while most managers are current — so a single "as of" header would be a lie for some rows.
const HEDGED_MULTISTRAT = ['Citadel', 'Millennium', 'Two Sigma', 'AQR', 'Renaissance'];

function BigFundsTab() {
  const { data, error } = useSWR('events-institutional-followers', () => api.eventsInstitutionalFollowers());
  if (error) return <div style={card}><div style={{ color: '#f87171' }}>Failed to load.</div></div>;
  if (!data) return <div style={card}><div style={{ color: '#6b7280' }}>Loading…</div></div>;

  const measured = data.funds.filter(f => !f.unavailable && f.alpha_vs_spy_pct != null);
  const unmeasured = data.funds.filter(f => f.unavailable || f.alpha_vs_spy_pct == null);
  const isHedged = (n: string) => HEDGED_MULTISTRAT.some(h => n.startsWith(h));

  const row = (f: InstitutionalFund) => {
    const stale = (f.staleness_days ?? 0) > 200;
    return (
      <tr key={f.name} style={{ borderTop: '1px solid #1f2937', opacity: f.sample_is_adequate ? 1 : 0.6 }}>
        <td style={{ padding: '7px 8px' }}>
          <span style={{ fontWeight: f.sample_is_adequate ? 600 : 400 }}>{f.name}</span>
          {isHedged(f.name) && (
            <span title="13F shows only the long book; this manager's shorts and derivatives are invisible here"
                  style={{ marginLeft: 6, fontSize: 10, color: '#a78bfa', border: '1px solid #4c1d95', borderRadius: 4, padding: '1px 4px' }}>
              hedged
            </span>
          )}
        </td>
        <td style={{ padding: '7px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: pctColor(f.alpha_vs_spy_pct), fontWeight: 700 }}>
          {fmtPct(f.alpha_vs_spy_pct, 2)}
        </td>
        <td style={{ padding: '7px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: '#9ca3af' }}>{fmtPct(f.avg_21d_pct, 2)}</td>
        <td style={{ padding: '7px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: '#6b7280' }}>{fmtPct(f.benchmark_21d_pct, 2)}</td>
        <td style={{ padding: '7px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
          {f.n_measured}<span style={{ color: '#6b7280' }}> / {f.n_buys}</span>
        </td>
        <td style={{ padding: '7px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: '#9ca3af' }}>
          {f.pct_up != null ? `${f.pct_up.toFixed(0)}%` : '—'}
        </td>
        <td style={{ padding: '7px 8px', fontSize: 12, color: stale ? '#fbbf24' : '#6b7280' }}>
          {f.filing_date ?? '—'}{f.staleness_days != null ? ` · ${f.staleness_days}d old` : ''}
        </td>
      </tr>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ ...card, borderColor: '#78350f', background: 'rgba(120,53,15,0.15)' }}>
        <div style={{ ...sectionTitle, color: '#fbbf24', marginBottom: 8 }}>This is positioning, not a signal</div>
        <div style={{ fontSize: 13, color: '#e5e7eb', lineHeight: 1.65 }}>
          A 13F is a <strong>quarter-end snapshot filed up to 45 days later</strong> — not a trade feed. It
          shows no intra-quarter round trips, no short positions and no options unless separately reported.
          The manager may have exited before you ever saw it. Returns below are entered at the
          <strong> filing date</strong>, the first moment the position was public.
          <br /><br />
          And this is <strong>one quarter over one {data.horizon_days}-day window, not a track record</strong>.
          Every fund here shares essentially the same window, so a single market episode drives much of the
          spread between them. Read it as what the latest disclosed adds happened to do — not as skill.
        </div>
      </div>

      <div style={card}>
        <div style={sectionTitle}>Alpha on newly-added positions ({data.n_followable} of {data.funds.length} with enough priced positions)</div>
        <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 12, lineHeight: 1.6 }}>
          Sorted by <strong style={{ color: '#9ca3af' }}>alpha</strong> — the fund&apos;s return minus the market&apos;s own move over the
          identical window. The raw return and the benchmark are both shown so you can check the subtraction.
          Ranking on the raw number would order these managers by beta: on the first run every one of them
          looked negative, purely because the market was.
        </div>
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ color: '#9ca3af', textAlign: 'left', fontSize: 12 }}>
              <th style={{ padding: '6px 8px' }}>Manager</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Alpha</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Raw {data.horizon_days}d</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Market</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>Priced / Adds</th>
              <th style={{ padding: '6px 8px', textAlign: 'right' }}>% Up</th>
              <th style={{ padding: '6px 8px' }}>Filed</th>
            </tr>
          </thead>
          <tbody>{measured.map(row)}</tbody>
        </table>
        <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 12, lineHeight: 1.6 }}>
          <strong>Priced / Adds</strong> — only positions in stocks this platform prices are measured, so the
          first number is usually far below the fund&apos;s reported adds. Rows below the
          {' '}{data.min_positions_for_adequacy}-position floor are dimmed: enough to average, never enough to judge.
          Rows marked <span style={{ color: '#a78bfa' }}>hedged</span> are multi-strategy or quantitative funds
          whose disclosed longs are one leg of a position whose shorts are invisible here — a large negative
          alpha there may be the hedge working exactly as intended.
        </div>
      </div>

      {unmeasured.length > 0 && (
        <div style={card}>
          <div style={sectionTitle}>No measurable positions ({unmeasured.length})</div>
          <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 10, lineHeight: 1.6 }}>
            Listed rather than dropped — a manager missing from the table above would otherwise make it look
            complete when it is not. Either the filing holds nothing this platform prices, or no filing was returned.
          </div>
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <tbody>
              {unmeasured.map(f => (
                <tr key={f.name} style={{ borderTop: '1px solid #1f2937' }}>
                  <td style={{ padding: '7px 8px', color: '#9ca3af' }}>{f.name}</td>
                  <td style={{ padding: '7px 8px', textAlign: 'right', color: '#6b7280', fontSize: 12 }}>
                    {f.unavailable ? 'no filing returned' : `${f.n_buys} adds, none priced`}
                  </td>
                  <td style={{ padding: '7px 8px', color: '#6b7280', fontSize: 12 }}>{f.filing_date ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={card}>
        <div style={sectionTitle}>What this is and is not</div>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: '#9ca3af', lineHeight: 1.8 }}>
          {data.caveats.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      </div>
    </div>
  );
}

function TuningTab() {
  const { data: tuneStatus } = useSWR('signal-tune-status-reports', () => api.signalTuneStatus());
  const { data: outcomes } = useSWR('outcomes-summary-reports', () => api.outcomesSummary(undefined, 90));
  const { data: promotions } = useSWR('promotion-history-reports', () => api.promotionHistory());
  const { data: scheduler } = useSWR('scheduler-status-reports', () => api.schedulerStatus());
  const { data: minRr } = useSWR('min-rr-reports', () => api.minRrCalibration());
  const { data: entryFactors } = useSWR('entry-factors-reports', () => api.entryFactors());

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={card}>
        <div style={sectionTitle}>Live Strategy Parameters by Horizon</div>
        {tuneStatus?.styles ? (
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: '#9ca3af', textAlign: 'left' }}>
                <th style={{ padding: '6px 8px' }}>Horizon</th>
                <th style={{ padding: '6px 8px' }}>Buy Threshold</th>
                <th style={{ padding: '6px 8px' }}>ML Weight Cap</th>
                <th style={{ padding: '6px 8px' }}>14d Win Rate</th>
                <th style={{ padding: '6px 8px' }}>Watchdog</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(tuneStatus.styles).map(([style, s]) => (
                <tr key={style} style={{ borderTop: '1px solid #1f2937' }}>
                  <td style={{ padding: '6px 8px', fontWeight: 700 }}>{style}</td>
                  <td style={{ padding: '6px 8px' }}>{(s.effective.buy_threshold_bull * 100).toFixed(0)}%</td>
                  <td style={{ padding: '6px 8px' }}>{(s.effective.ml_weight_cap * 100).toFixed(0)}%</td>
                  <td style={{ padding: '6px 8px', color: (s.performance.win_rate_14d ?? 0) >= 0.5 ? '#4ade80' : '#f87171' }}>
                    {s.performance.win_rate_14d != null ? `${(s.performance.win_rate_14d * 100).toFixed(0)}% (n=${s.performance.n_outcomes_14d})` : '—'}
                  </td>
                  <td style={{ padding: '6px 8px', color: '#9ca3af' }}>{s.watchdog.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
        <div style={card}>
          <div style={sectionTitle}>Signal Outcomes (90d)</div>
          {outcomes?.overall ? (
            <div style={{ fontSize: 13, color: '#d1d5db', lineHeight: 2 }}>
              <div>Win rate: <span style={{ fontWeight: 700, color: outcomes.overall.win_rate >= 0.5 ? '#4ade80' : '#f87171' }}>{(outcomes.overall.win_rate * 100).toFixed(1)}%</span></div>
              <div>Avg return: <span style={{ color: pctColor(outcomes.overall.avg_return_pct) }}>{fmtPct(outcomes.overall.avg_return_pct, 2)}</span></div>
              <div>Total outcomes: <span style={{ fontWeight: 700 }}>{outcomes.total}</span></div>
            </div>
          ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
        </div>

        <div style={card}>
          <div style={sectionTitle}>Min R:R Calibration</div>
          {minRr ? (
            minRr.status === 'calibrated' ? (
              <div style={{ fontSize: 13, color: '#d1d5db', lineHeight: 2 }}>
                <div>Min R:R: <span style={{ fontWeight: 700 }}>{minRr.min_rr_ratio?.toFixed(2)}:1</span></div>
                <div>Trades used: <span style={{ fontWeight: 700 }}>{minRr.n_trades}</span></div>
              </div>
            ) : <div style={{ color: '#6b7280', fontSize: 13 }}>{minRr.note ?? 'Not calibrated yet.'}</div>
          ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
        </div>

        <div style={card}>
          <div style={sectionTitle}>Entry Factor Calibration</div>
          {entryFactors ? (
            entryFactors.status === 'calibrated' ? (
              <div style={{ fontSize: 13, color: '#d1d5db', lineHeight: 2 }}>
                <div>Win rate: <span style={{ fontWeight: 700 }}>{entryFactors.win_rate != null ? `${(entryFactors.win_rate * 100).toFixed(1)}%` : '—'}</span></div>
                <div>Trades used: <span style={{ fontWeight: 700 }}>{entryFactors.n_trades}</span></div>
              </div>
            ) : <div style={{ color: '#6b7280', fontSize: 13 }}>Not calibrated yet — needs ≥100 closed paper trades.</div>
          ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
        </div>
      </div>

      <div style={card}>
        <div style={sectionTitle}>Recent Scheduler Jobs</div>
        {scheduler?.jobs ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 8 }}>
            {scheduler.jobs.slice(0, 12).map(j => (
              <div key={j.job} style={{ padding: 8, background: '#0b1120', borderRadius: 6, fontSize: 12 }}>
                <div style={{ fontWeight: 600 }}>{j.job}</div>
                <div style={{ color: j.status === 'ok' ? '#4ade80' : j.status === 'error' ? '#f87171' : '#9ca3af' }}>{j.status}</div>
              </div>
            ))}
          </div>
        ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
      </div>

      {promotions && (
        <div style={{ fontSize: 12, color: '#6b7280' }}>
          {promotions.meta_model_history.length} meta-model promotion decisions, {promotions.position_scaling_history.length} position-scaling gate decisions recorded.
          See <a href="/admin-health" style={{ color: '#818cf8' }}>Admin Health</a> for the full audit trail.
        </div>
      )}
    </div>
  );
}

const VALID_TABS: Tab[] = ['trend', 'assets', 'top', 'flow', 'smartmoney', 'funds', 'tuning'];

function tabFromQuery(q: string | string[] | undefined): Tab {
  const v = Array.isArray(q) ? q[0] : q;
  return (VALID_TABS as string[]).includes(v ?? '') ? (v as Tab) : 'trend';
}

export default function ReportsPage() {
  const router = useRouter();
  const session = getSession();

  if (!session) {
    if (typeof window !== 'undefined') router.replace('/login');
    return null;
  }

  // Deep-linked from the Reports nav dropdown (/reports?tab=X) — each nav item lands
  // directly on its tab instead of always opening to Trend. router.query isn't populated
  // until after hydration on first render (Next.js), so this can't be read in useState's
  // initializer the way _app.tsx's auth check reads localStorage synchronously; a one-time
  // effect syncing tab from the query once it's available is the correct pattern here.
  const [tab, setTab] = useState<Tab>(() => tabFromQuery(router.query.tab));
  useEffect(() => {
    if (router.isReady) setTab(tabFromQuery(router.query.tab));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router.isReady, router.query.tab]);
  const [market, setMarket] = useState<Market>('US');

  return (
    <div style={{ minHeight: '100vh', background: '#0a0a0a', color: '#f9fafb', fontFamily: 'system-ui, sans-serif' }}>
      <div style={{ background: '#111827', borderBottom: '1px solid #1f2937', padding: '0 24px' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto', display: 'flex', alignItems: 'center', gap: 24, height: 56 }}>
          <button onClick={() => router.push('/')} style={{ background: 'none', border: 'none', color: '#6b7280', cursor: 'pointer', fontSize: 13 }}>
            ← Back
          </button>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>Reports</h1>
          <span style={{ color: '#6b7280', fontSize: 13 }}>Trend · Assets · Top Stocks · Money Flow · Who to Follow · Big Funds · Self-Tuning</span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
            {(['US', 'HK'] as Market[]).map(m => (
              <button
                key={m}
                onClick={() => setMarket(m)}
                style={{
                  padding: '6px 14px', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer',
                  border: market === m ? '1px solid #6d28d9' : '1px solid #1f2937',
                  background: market === m ? 'rgba(109,40,217,0.2)' : 'transparent',
                  color: market === m ? '#a78bfa' : '#6b7280',
                }}
              >
                {m}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div style={{ background: '#111827', borderBottom: '1px solid #1f2937', padding: '0 24px' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto', display: 'flex', gap: 0 }}>
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => { setTab(t.key); router.replace({ pathname: '/reports', query: { tab: t.key } }, undefined, { shallow: true }); }}
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
      </div>

      <div style={{ maxWidth: 1400, margin: '0 auto', padding: '28px 24px' }}>
        {tab === 'trend'  && <TrendTab market={market} />}
        {tab === 'assets' && <AssetsTab market={market} />}
        {tab === 'top'    && <TopStocksTab market={market} />}
        {tab === 'flow'   && <FlowTab market={market} />}
        {tab === 'smartmoney' && <SmartMoneyTab />}
        {tab === 'funds' && <BigFundsTab />}
        {tab === 'tuning' && <TuningTab />}
      </div>
    </div>
  );
}
