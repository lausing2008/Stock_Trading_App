import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import { getSession } from '@/lib/auth';

// ── Static content — kept in sync by hand with services/market-data/src/services/scheduler.py.
// If you add/change a scheduled job there, update the matching row here too. ─────────────────

type Scope = 'price-alert' | 'signal-alert' | 'all-users' | 'per-connection';

const SCOPE_LABEL: Record<Scope, string> = {
  'price-alert': 'Price-Alert subscribers',
  'signal-alert': 'Signal-Alert subscribers',
  'all-users': 'All users (email set)',
  'per-connection': 'Broker connection owner',
};

const SCOPE_COLOR: Record<Scope, string> = {
  'price-alert': '#38bdf8',
  'signal-alert': '#a78bfa',
  'all-users': '#f59e0b',
  'per-connection': '#f87171',
};

type AlertRow = {
  job: string;
  schedule: string;
  scope: Scope;
  cooldown: string;
  note: string;
};

const USER_ALERTS: AlertRow[] = [
  {
    job: 'check_price_alerts',
    schedule: 'Every 1 min',
    scope: 'price-alert',
    cooldown: 'One-shot (fires once, marks triggered)',
    note: 'Polls live yfinance quotes for every untriggered price alert and fires the moment price crosses your threshold — above or below, optionally combined with an RSI/volume/signal condition. Also separately watches every open paper position: any position down 5%+ from entry emails the portfolio owner (24h cooldown per symbol).',
  },
  {
    job: 'check_technical_alerts',
    schedule: 'Every ~5 min (runs inside the market-refresh cycle)',
    scope: 'price-alert',
    cooldown: 'Once/day if recurring is enabled',
    note: 'Checks pattern-based alerts against daily bar history: EMA/golden/death crosses, new 52-week highs/lows, MACD bullish cross, RSI oversold bounce, double bottom, breakout, volume spike, or price sitting a set % below its 52-week high.',
  },
  {
    job: 'check_signal_alerts',
    schedule: 'Every ~5 min (market-refresh cycle) + once at startup',
    scope: 'signal-alert',
    cooldown: '2h same-direction cooldown; full BUY↔SELL reversals bypass it',
    note: 'The "conviction BUY" alert: only fires a BUY when 5 things line up at once — AI signal flips to BUY, confidence ≥60%, bullish analyst consensus, and K-Score/Technical/Momentum confluence ≥75. Exit/bearish transitions (BUY→HOLD/WAIT/SELL) always fire, no gate. Also folds in earnings-proximity reminders (1/2/3/5 days out) as one consolidated table, not a separate email per stock.',
  },
  {
    job: 'earnings_reaction_check',
    schedule: 'Every 1 min',
    scope: 'price-alert',
    cooldown: '7 days per (user, symbol, report date)',
    note: 'Fires once a watched stock’s actual EPS has posted (within the last 2 days) — beat/missed/met, with the real surprise %. This is the "what just happened" half; the pre-market brief below covers the "what’s coming" half.',
  },
  {
    job: 'macro_reaction_alert_check',
    schedule: 'Every 1 min',
    scope: 'price-alert',
    cooldown: 'One-shot per event (reaction_sent_at stamp)',
    note: 'A macro release (CPI/PPI/GDP/NFP/FOMC) doesn’t target one symbol, so this goes to every subscriber, not just watchers of a specific stock. A separate background service detects the real released number and has Claude write a short reaction paragraph; this job only delivers it once, the moment it’s ready.',
  },
  {
    job: 'volume_anomaly_check',
    schedule: 'Every 1 min',
    scope: 'price-alert',
    cooldown: '20h per (user, symbol, RVOL bucket); 10/day cap per user',
    note: 'Scans the whole universe for abnormal volume using a threshold that scales with how much of the trading day has elapsed (so 10am never looks artificially "quiet" or "hot" versus 3pm). Reports the measured volume ratio plus the nearest support/resistance level price is testing — never a breakout prediction, just the facts needed to judge it yourself.',
  },
  {
    job: 'value_area_breakdown_check',
    schedule: 'Every 1 min',
    scope: 'price-alert',
    cooldown: '26h per (user, symbol, breakout/breakdown, profiled day)',
    note: 'Fires when a stock closes back below its Value Area Low (bearish breakdown) or above its Value Area High (breakout/reversal) — the same POC/VAH/VAL levels shown on the Volume Profile chart tool, computed daily and read from a small pre-computed table rather than recalculated live.',
  },
  {
    job: 'top3_conviction_check',
    schedule: 'Every 1 min',
    scope: 'price-alert',
    cooldown: '6h; re-fires only when the top-3 line-up actually changes',
    note: 'The "give me your best picks" alert, built to be genuinely honest: it gates on a stock’s tracked, historical win rate at that exact confidence level (≥70%, at least 30 real past outcomes) — not the model’s raw confidence score. Most days this correctly sends nothing; an empty inbox means the bar is working, not that the feature is broken.',
  },
  {
    job: 'premarket_brief_us / premarket_brief_hk',
    schedule: 'Daily 8:00am local (US/HK), weekdays',
    scope: 'price-alert',
    cooldown: '20h per (user, market, day)',
    note: 'One digest sent before the open: today’s high-importance macro releases, which of your watched stocks report earnings today, recent macro reactions from the last 18h, and overnight futures direction (ES/NQ/YM/RTY) framed as "here’s the market’s own current expectation," never a prediction of what actually happens at the open.',
  },
  {
    job: 'morning_digest_us / morning_digest_hk',
    schedule: 'Daily 8:50am local (US/HK), weekdays',
    scope: 'all-users',
    cooldown: 'Sends every scheduled fire (no dedup — it’s a daily summary)',
    note: 'Broader audience than the alerts above — goes to every user with an email set, not just alert subscribers. Covers the market regime for that market, top 5 SWING and top 5 GROWTH opportunities, your open paper positions with yesterday’s move, and any pattern alerts that fired since yesterday.',
  },
  {
    job: 'post_open_digest (5 US + 5 HK time slots)',
    schedule: '30/90/150/210/270 min after each market’s open',
    scope: 'all-users',
    cooldown: 'Skips sending entirely if nothing meaningful changed since the last snapshot',
    note: 'A running "what’s changed since I last checked" digest through the trading day — regime/VIX moves, your open positions’ price/signal changes, new BUY/SELL flips, top gainers/losers, and volume surges/dry-ups. Deliberately silent on a quiet snapshot rather than sending a repetitive, empty-feeling email.',
  },
  {
    job: 'paper_portfolio_digest',
    schedule: 'Daily 5:00pm ET, weekdays (1h after US close)',
    scope: 'all-users',
    cooldown: 'Sends every scheduled fire',
    note: 'End-of-day recap per active paper portfolio: total return %, today’s closed trades, current open positions, and a rolling Sharpe ratio.',
  },
  {
    job: 'data_quality_checks',
    schedule: 'Every 2 hours',
    scope: 'all-users',
    cooldown: '6h per failing-check type',
    note: 'Not a trading alert — a battery of freshness checks across key data tables (holiday/weekend-aware), so a silent ingestion failure surfaces as an email instead of stale numbers nobody notices. A separate 6h-deduped alert distinguishes "the data is stale" from "the check itself is erroring" (e.g. a database outage).',
  },
  {
    job: 'broker_auth_check',
    schedule: 'Daily 8:30am ET, weekdays (1h before NYSE open)',
    scope: 'per-connection',
    cooldown: 'Fires only on a genuine auth failure',
    note: 'E*Trade’s OAuth tokens hard-expire nightly — this confirms every connected broker is still authorized before the market opens, and emails a fresh reconnect link the moment one isn’t.',
  },
  {
    job: 'broker_token_renewal (5 fixed times, 9:45am–3:45pm ET)',
    schedule: '~90 min apart, weekdays, market hours',
    scope: 'per-connection',
    cooldown: 'Fires only on a genuine renewal failure',
    note: 'Proactively keeps broker sessions alive through the trading day (a token idle for 2h+ intraday goes dead) instead of waiting for tomorrow morning’s check to notice — same reconnect-link email on failure.',
  },
];

type BgRow = { job: string; schedule: string; note: string };

const BACKGROUND_JOBS: BgRow[] = [
  { job: 'us_open_burst / hk_open_burst', schedule: 'Every 5 min, market open window', note: 'Ingests fresh daily bars, refreshes rankings + signals, then runs the alert checks above inline.' },
  { job: 'us_intra / hk_intra', schedule: 'Every 5 min, regular session', note: 'Same ingest→rank→signal→alert cycle, mid-day.' },
  { job: 'us_close_burst / hk_close_burst', schedule: 'Every 5 min, close window', note: 'Same cycle, into the close.' },
  { job: 'us_post_close / hk_post_close', schedule: 'Once at each market’s close', note: 'Final bar of the day, retrains the ML model, evaluates signal outcomes, and re-triggers a full re-tune if the model has gone stale.' },
  { job: 'us_5m_intraday / hk_5m_intraday', schedule: 'Every 5 min, market hours', note: 'Intraday bars only, plus a paper-trading monitor pass (stops, trailing stops, exits).' },
  { job: 'live_price_cache_refresh', schedule: 'Every 1 min, market hours', note: 'One bulk price fetch → the shared live-price cache that volume/value-area alerts and live UI prices all read from.' },
  { job: 'avg_volume_cache_refresh (+ startup check)', schedule: 'Every 4h, plus once at boot', note: 'Maintains the 20-day average-volume baseline the RVOL calculation in the volume-anomaly alert needs.' },
  { job: 'value_area_levels_daily', schedule: 'Daily 6:00pm ET', note: 'Computes each watched stock’s POC/VAH/VAL for that day — feeds the value-area breakdown alert directly.' },
  { job: 'weekly_full_refresh', schedule: 'Sunday 2:00pm PT', note: 'The big weekly self-improvement run: full re-ingest, full rankings/signals refresh, and every self-tuning mechanism (threshold calibration, ML weight tuning, style-profile tuning, entry-weight calibration, RL training) that keeps the alerts above accurate over time.' },
  { job: 'signal_watchdog_daily', schedule: 'Daily 6:10am ET, weekdays', note: 'Watches each style’s rolling 14-day win rate and auto-tightens (or relaxes) the BUY threshold if it drifts too far from target — a faster daily correction between the weekly full re-tunes.' },
  { job: 'sector_rotation_weekly', schedule: 'Sunday 4:00pm ET', note: 'Computes which sectors are gaining/losing momentum — feeds a sector-momentum signal into BUY/SELL scoring.' },
  { job: 'fundamentals_snapshot_weekly', schedule: 'Sunday 4:30pm ET', note: 'Weekly fundamentals snapshot, used for earnings-revision-momentum features in the ML model.' },
  { job: 'watchlist_auto_rotation_weekly', schedule: 'Sunday 5:00pm ET', note: 'Drops chronic underperformers and adds strong new candidates to each watchlist automatically — see the dedicated explainer page for the full rules.' },
  { job: 'edgar_8k_ingest_daily / hk_connect_flows_daily', schedule: 'Daily, after each market’s close', note: 'Pulls SEC 8-K filings (US) and HKEX Stock Connect money flow (HK) — both enrich signal quality, neither alerts directly.' },
  { job: 'db_purge_weekly', schedule: 'Sunday 3:00pm PT', note: 'Deletes old intraday bars and resolved signal-outcome rows past their retention window — pure housekeeping.' },
  { job: 'meta_model_monthly_retrain / backfill_realized_ev_monthly', schedule: 'First Sunday of the month', note: 'A slower monthly retrain of the cross-symbol ML model, plus a check on whether last month’s tuning changes actually helped in real subsequent outcomes.' },
];

// ── Reusable presentational bits — matches watchlist-rotation-explainer.tsx's design system ──

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '32px' }}>
      <h2 style={{ fontSize: '15px', fontWeight: 800, color: '#e2e8f0', marginBottom: '10px' }}>{title}</h2>
      <div style={{ fontSize: '13px', lineHeight: 1.7, color: '#94a3b8' }}>{children}</div>
    </div>
  );
}

function Callout({ tone, title, children }: { tone: 'info' | 'warn' | 'good'; title: string; children: React.ReactNode }) {
  const colors = {
    info: { bg: 'rgba(56,189,248,0.08)', border: 'rgba(56,189,248,0.3)', text: '#38bdf8' },
    warn: { bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.3)', text: '#f87171' },
    good: { bg: 'rgba(34,197,94,0.08)', border: 'rgba(34,197,94,0.3)', text: '#22c55e' },
  }[tone];
  return (
    <div style={{ padding: '12px 16px', borderRadius: '10px', background: colors.bg, border: `1px solid ${colors.border}`, marginBottom: '16px' }}>
      <div style={{ fontSize: '11px', fontWeight: 800, color: colors.text, textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '4px' }}>
        {title}
      </div>
      <div style={{ fontSize: '12.5px', color: '#cbd5e1', lineHeight: 1.6 }}>{children}</div>
    </div>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code style={{ background: '#0d1424', border: '1px solid #1e293b', borderRadius: '4px', padding: '1px 6px', fontSize: '12px', color: '#f59e0b', fontFamily: 'monospace' }}>
      {children}
    </code>
  );
}

function ScopePill({ scope }: { scope: Scope }) {
  const color = SCOPE_COLOR[scope];
  return (
    <span style={{
      display: 'inline-block', fontSize: '10.5px', fontWeight: 700, padding: '2px 8px', borderRadius: '999px',
      background: `${color}1a`, color, whiteSpace: 'nowrap',
    }}>
      {SCOPE_LABEL[scope]}
    </span>
  );
}

// ── Workflow diagram — pure CSS/SVG boxes+arrows, no external dependency ──────────────────────

function DiagramBox({ label, sub, color, wide }: { label: string; sub?: string; color: string; wide?: boolean }) {
  return (
    <div style={{
      padding: '10px 14px', borderRadius: '10px', background: '#0d1424', border: `1px solid ${color}55`,
      minWidth: wide ? '220px' : '150px', textAlign: 'center', flex: wide ? '1 1 auto' : undefined,
    }}>
      <div style={{ fontSize: '12px', fontWeight: 700, color: '#e2e8f0' }}>{label}</div>
      {sub && <div style={{ fontSize: '10.5px', color: '#64748b', marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function DownArrow() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '4px 0' }}>
      <svg width="16" height="20" viewBox="0 0 16 20">
        <line x1="8" y1="0" x2="8" y2="14" stroke="#334155" strokeWidth="2" />
        <polygon points="8,20 3,12 13,12" fill="#334155" />
      </svg>
    </div>
  );
}

function DiagramRow({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', justifyContent: 'center' }}>
      {children}
    </div>
  );
}

function WorkflowDiagram() {
  return (
    <div style={{ padding: '20px', borderRadius: '12px', border: '1px solid #1e293b', background: 'rgba(148,163,184,0.02)' }}>
      <div style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 14, textAlign: 'center' }}>
        1. Ingestion
      </div>
      <DiagramRow>
        <DiagramBox label="yfinance bulk fetch" sub="5×/day per market + every 1 min live-price cache" color="#38bdf8" />
        <DiagramBox label="event-intelligence" sub="macro releases, earnings, EDGAR, HK Connect flow" color="#38bdf8" />
      </DiagramRow>
      <DownArrow />
      <div style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 14, textAlign: 'center' }}>
        2. Computation
      </div>
      <DiagramRow>
        <DiagramBox label="Rankings + Signals" sub="ranking-engine, signal-engine" color="#a78bfa" />
        <DiagramBox label="Redis caches" sub="live prices, avg volume, value-area levels" color="#a78bfa" />
        <DiagramBox label="Self-tuning" sub="weekly calibration + daily watchdog" color="#a78bfa" />
      </DiagramRow>
      <DownArrow />
      <div style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 14, textAlign: 'center' }}>
        3. Alert checks (mostly every 1 min, own lock + cooldown each)
      </div>
      <DiagramRow>
        <DiagramBox label="Price / Technical" color="#38bdf8" />
        <DiagramBox label="Signal (conviction BUY)" color="#a78bfa" />
        <DiagramBox label="Volume / Value-Area" color="#38bdf8" />
        <DiagramBox label="Top-3 Conviction" color="#a78bfa" />
        <DiagramBox label="Earnings / Macro Reaction" color="#38bdf8" />
      </DiagramRow>
      <DownArrow />
      <div style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 14, textAlign: 'center' }}>
        4. Delivery
      </div>
      <DiagramRow>
        <DiagramBox label="Email" sub="every alert" color="#f59e0b" wide />
        <DiagramBox label="Webhook + Web Push" sub="price alerts only" color="#f59e0b" wide />
      </DiagramRow>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AlertsGuidePage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    setAuthed(true);
  }, [router]);

  if (!authed) return null;

  return (
    <div style={{ maxWidth: '1400px', margin: '0 auto', padding: '24px 24px 60px' }}>
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '6px' }}>
          Alerts &amp; Notifications Guide
        </h1>
        <p style={{ fontSize: '13px', color: '#64748b' }}>
          Every scheduled alert and background job in this app, when it runs, who receives it, and how
          it all connects. Manage your own subscriptions on the{' '}
          <a href="/alerts" style={{ color: '#38bdf8', textDecoration: 'none' }}>Alerts page</a>.
        </p>
      </div>

      <Callout tone="info" title="Two different recipient scopes — worth knowing before you read the table">
        Most alerts only reach users who have actually set up a <strong style={{ color: '#e2e8f0' }}>Price Alert</strong> or{' '}
        <strong style={{ color: '#e2e8f0' }}>Signal Alert</strong> on that symbol — a narrower, opt-in audience. A few
        (the morning digest, post-open digests, paper-portfolio digest, and the data-quality check) go to{' '}
        <strong style={{ color: '#e2e8f0' }}>every user with an email set</strong>, since they’re daily
        summaries rather than symbol-specific triggers. The color-coded pill in each row tells you which.
      </Callout>

      <Section title="How it all fits together">
        <p style={{ marginBottom: 16 }}>
          Everything below runs on the same backbone: fresh data comes in, gets turned into rankings/
          signals/cached prices, a battery of alert-checking jobs reads that computed state (never the
          raw data source directly, to avoid hammering yfinance), and the ones that find something
          worth telling you about send an email. The weekly self-tuning pass at the bottom of the
          background-jobs table is what keeps step 2&apos;s output actually accurate over time — it doesn&apos;t
          send anything itself, but every alert above depends on it.
        </p>
        <WorkflowDiagram />
      </Section>

      <Section title="User-facing alerts">
        <p style={{ marginBottom: 14 }}>
          Anything that can land in your inbox. Sorted roughly by how often it can fire.
        </p>
        <div style={{ borderRadius: '10px', border: '1px solid #1e293b', overflowX: 'auto', marginBottom: 8 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px', tableLayout: 'fixed' }}>
            <colgroup>
              <col style={{ width: '15%' }} />
              <col style={{ width: '13%' }} />
              <col style={{ width: '11%' }} />
              <col style={{ width: '16%' }} />
              <col style={{ width: '45%' }} />
            </colgroup>
            <thead>
              <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                {['Alert', 'Schedule', 'Sent to', 'Cooldown / dedup', 'What it does & why'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 12px', color: '#475569', fontWeight: 700, fontSize: '10.5px', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {USER_ALERTS.map(row => (
                <tr key={row.job} style={{ borderBottom: '1px solid #1e293b', verticalAlign: 'top' }}>
                  <td style={{ padding: '10px 12px', color: '#e2e8f0', fontWeight: 600, wordBreak: 'break-word' }}><Code>{row.job}</Code></td>
                  <td style={{ padding: '10px 12px', color: '#94a3b8' }}>{row.schedule}</td>
                  <td style={{ padding: '10px 12px' }}><ScopePill scope={row.scope} /></td>
                  <td style={{ padding: '10px 12px', color: '#64748b' }}>{row.cooldown}</td>
                  <td style={{ padding: '10px 12px', color: '#cbd5e1', lineHeight: 1.6 }}>{row.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Background &amp; self-tuning jobs">
        <p style={{ marginBottom: 14 }}>
          These never email you directly, but feed the data or model quality the alerts above depend on.
        </p>
        <div style={{ borderRadius: '10px', border: '1px solid #1e293b', overflowX: 'auto', marginBottom: 8 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px', tableLayout: 'fixed' }}>
            <colgroup>
              <col style={{ width: '22%' }} />
              <col style={{ width: '20%' }} />
              <col style={{ width: '58%' }} />
            </colgroup>
            <thead>
              <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                {['Job', 'Schedule', 'What it maintains'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 12px', color: '#475569', fontWeight: 700, fontSize: '10.5px', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {BACKGROUND_JOBS.map(row => (
                <tr key={row.job} style={{ borderBottom: '1px solid #1e293b', verticalAlign: 'top' }}>
                  <td style={{ padding: '10px 12px', color: '#e2e8f0', fontWeight: 600, wordBreak: 'break-word' }}><Code>{row.job}</Code></td>
                  <td style={{ padding: '10px 12px', color: '#94a3b8' }}>{row.schedule}</td>
                  <td style={{ padding: '10px 12px', color: '#cbd5e1', lineHeight: 1.6 }}>{row.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Callout tone="good" title="How this all helps in one sentence">
        Instead of you checking the app all day, this pipeline watches prices, volume, signals, macro
        releases, and earnings continuously, self-corrects its own thresholds weekly (and daily via the
        watchdog), and only interrupts you when something genuinely crosses a bar worth knowing about
        — with every gate, cooldown, and threshold on this page chosen to keep that bar honest rather
        than noisy.
      </Callout>

      <Callout tone="warn" title="If an alert looks wrong or isn’t firing">
        Check the job’s own cooldown/dedup column first — most "why didn’t I get this" questions are
        actually a dedup key still active, not a broken job. Beyond that: <Code>check_price_alerts</Code> and{' '}
        <Code>check_signal_alerts</Code> require you to have actually created a Price Alert / Signal
        Alert on that symbol on the{' '}
        <a href="/alerts" style={{ color: '#38bdf8', textDecoration: 'none' }}>Alerts page</a> — nothing
        fires for a symbol you haven’t subscribed to.
      </Callout>
    </div>
  );
}
