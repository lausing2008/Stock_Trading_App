/**
 * Reports — T255-REPORTS-TAB Phase 1. Consolidated per-market (US/HK) reports view,
 * composing existing endpoints (see .claude/CLAUDE.md "Research: Reports Tab" for the full
 * endpoint inventory this was built from). Same tab-array + per-tab-component structure as
 * intelligence.tsx, which this page follows deliberately rather than inventing a new layout.
 *
 * Six tabs: Trend, Assets, Top Stocks, Money Flow, News & Macro, Self-Tuning. Market toggle
 * (US/HK) scopes the market-specific tabs; Self-Tuning is market-agnostic (signal/paper-trading
 * calibration is global) and News & Macro is currently US-only at the source (economic
 * calendar / macro reactions are US Fed/BLS data).
 */
import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api, type RankingRow, type SectorGroup } from '@/lib/api';
import { getSession } from '@/lib/auth';

type Market = 'US' | 'HK';
type Tab = 'trend' | 'assets' | 'top' | 'flow' | 'news' | 'tuning';

const TABS: { key: Tab; label: string }[] = [
  { key: 'trend',  label: 'Market Trend' },
  { key: 'assets', label: 'Key Assets' },
  { key: 'top',    label: 'Top Stocks' },
  { key: 'flow',   label: 'Money Flow' },
  { key: 'news',   label: 'News & Macro' },
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
  const { data: breadth } = useSWR('breadth', () => api.marketBreadth());
  const { data: cape } = useSWR('cape', () => api.eventsCape());

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
        <div style={sectionTitle}>Market Breadth (US)</div>
        {breadth ? (
          <>
            <div style={{ fontSize: 22, fontWeight: 700, color: breadth.color }}>{fmtPct(breadth.breadth_pct)}</div>
            <div style={{ fontSize: 13, color: '#9ca3af', marginTop: 8 }}>
              {breadth.above_200ma} above / {breadth.below_200ma} below 200MA ({breadth.total} total) — <span style={{ color: breadth.color }}>{breadth.label}</span>
            </div>
            {market === 'HK' && <div style={{ fontSize: 11, color: '#f59e0b', marginTop: 8 }}>US-only data source — no HK breadth endpoint yet (see T255-REPORTS-TAB tracker item).</div>}
          </>
        ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
      </div>

      <div style={card}>
        <div style={sectionTitle}>CAPE / Bubble Warning</div>
        {cape?.latest ? (
          <>
            <div style={{ fontSize: 22, fontWeight: 700, color: cape.latest.band === 'extreme' ? '#f87171' : cape.latest.band === 'high' ? '#fb923c' : cape.latest.band === 'elevated' ? '#f59e0b' : '#4ade80' }}>
              {cape.latest.cape_value.toFixed(1)} <span style={{ fontSize: 14, fontWeight: 400, color: '#9ca3af', textTransform: 'capitalize' }}>{cape.latest.band}</span>
            </div>
            <div style={{ fontSize: 12, color: '#6b7280', marginTop: 8 }}>Shiller CAPE — a slow-moving macro valuation signal, not a trade trigger.</div>
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

// ── Money Flow ────────────────────────────────────────────────────────────────
function FlowTab({ market }: { market: Market }) {
  const { data: rotation } = useSWR(`sector-rotation-kscore-${market}`, () => api.sectorRotation(market));
  const { data: rankings } = useSWR(`rankings-flow-${market}`, () => api.rankings(market));

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
        <div style={sectionTitle}>Sector Momentum (K-Score-based, {market})</div>
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

// ── News & Macro ──────────────────────────────────────────────────────────────
function NewsTab() {
  const { data } = useSWR('events-overview-reports', () => api.eventsOverview());

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={card}>
        <div style={sectionTitle}>Latest Macro Reaction</div>
        {data?.latest_macro_reaction ? (
          <>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>{data.latest_macro_reaction.title}</div>
            <div style={{ fontSize: 13, color: '#d1d5db', lineHeight: 1.6 }}>{data.latest_macro_reaction.reaction_text}</div>
          </>
        ) : <div style={{ color: '#6b7280', fontSize: 13 }}>No macro reaction generated yet — fires automatically after a CPI/PPI/GDP/NFP release or FOMC statement.</div>}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
        <div style={card}>
          <div style={sectionTitle}>Upcoming Calendar</div>
          {data ? (
            <div style={{ fontSize: 13, color: '#d1d5db', lineHeight: 2 }}>
              <div>Economic events: <span style={{ fontWeight: 700 }}>{data.economic.upcoming_count}</span></div>
              <div>Earnings: <span style={{ fontWeight: 700 }}>{data.earnings.upcoming_count}</span></div>
              {data.economic.fomc_days_away != null && <div>Next FOMC: <span style={{ fontWeight: 700 }}>{data.economic.fomc_days_away}d away</span></div>}
            </div>
          ) : <div style={{ color: '#6b7280' }}>Loading…</div>}
        </div>

        <div style={card}>
          <div style={sectionTitle}>Insider Top Buys</div>
          {data?.insider.top_buys?.length ? (
            <div style={{ fontSize: 13, lineHeight: 1.9 }}>
              {data.insider.top_buys.slice(0, 6).map(b => (
                <div key={b.symbol} style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ fontWeight: 600 }}>{b.symbol}</span>
                  <span style={{ color: '#9ca3af' }}>{b.buy_count} buys / score {b.score.toFixed(0)}</span>
                </div>
              ))}
            </div>
          ) : <div style={{ color: '#6b7280', fontSize: 13 }}>No recent insider buys.</div>}
        </div>

        <div style={card}>
          <div style={sectionTitle}>Congress Top Buys</div>
          {data?.congress.top_buys?.length ? (
            <div style={{ fontSize: 13, lineHeight: 1.9 }}>
              {data.congress.top_buys.slice(0, 6).map(b => (
                <div key={b.symbol} style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ fontWeight: 600 }}>{b.symbol}</span>
                  <span style={{ color: '#9ca3af' }}>{b.company ?? ''}</span>
                </div>
              ))}
            </div>
          ) : <div style={{ color: '#6b7280', fontSize: 13 }}>No recent congress buys.</div>}
        </div>
      </div>

      <div style={{ ...card, color: '#f59e0b', fontSize: 13 }}>
        Market-level news sentiment monitoring (a "Market Pulse" mood score across general
        headlines, distinct from the per-symbol news already on each stock page) is designed
        but not yet built — see T249-MARKETMOVER-P4-MARKET-PULSE-NEWS-CARD in the improvements
        tracker.
      </div>
    </div>
  );
}

// ── Self-Tuning / Backtest Reports ───────────────────────────────────────────
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

const VALID_TABS: Tab[] = ['trend', 'assets', 'top', 'flow', 'news', 'tuning'];

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
          <span style={{ color: '#6b7280', fontSize: 13 }}>Trend · Assets · Top Stocks · Money Flow · News · Self-Tuning</span>
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
        {tab === 'news'   && <NewsTab />}
        {tab === 'tuning' && <TuningTab />}
      </div>
    </div>
  );
}
