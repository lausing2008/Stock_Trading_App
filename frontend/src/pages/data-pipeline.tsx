import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import { getSession } from '@/lib/auth';

/**
 * Data Pipeline & Ingestion — end-to-end operational reference.
 *
 * Built 2026-09-09 after AUD-ING-POLYGONDELAYED, where four US symbols sat frozen for four days
 * because a DELAYED-plan provider sat first in the adapter priority and answered out-of-range
 * windows with an empty HTTP 200. Nothing on the platform showed the shape of the pipeline in
 * one place, so the stall was only found by eye.
 *
 * Every number, cadence and file path here was read from source or measured in production on
 * 2026-09-09 — nothing is illustrative. Where a figure is a projection rather than a
 * measurement it says so explicitly.
 */

// ── Shared components — same visual language as dark-pool-guide/alerts-guide ──

function Section({ id, title, children }: { id?: string; title: string; children: React.ReactNode }) {
  return (
    <div id={id} style={{ marginBottom: '38px', scrollMarginTop: '80px' }}>
      <h2 style={{ fontSize: '15px', fontWeight: 800, color: '#e2e8f0', marginBottom: '12px' }}>{title}</h2>
      <div style={{ fontSize: '13px', lineHeight: 1.7, color: '#94a3b8' }}>{children}</div>
    </div>
  );
}

function SubSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '22px' }}>
      <h3 style={{ fontSize: '13px', fontWeight: 700, color: '#cbd5e1', marginBottom: '8px' }}>{title}</h3>
      <div style={{ fontSize: '13px', lineHeight: 1.7, color: '#94a3b8' }}>{children}</div>
    </div>
  );
}

function Callout({ tone, title, children }: { tone: 'info' | 'warn' | 'good' | 'example'; title: string; children: React.ReactNode }) {
  const colors = {
    info: { bg: 'rgba(56,189,248,0.08)', border: 'rgba(56,189,248,0.3)', text: '#38bdf8' },
    warn: { bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.3)', text: '#f87171' },
    good: { bg: 'rgba(34,197,94,0.08)', border: 'rgba(34,197,94,0.3)', text: '#22c55e' },
    example: { bg: 'rgba(168,85,247,0.08)', border: 'rgba(168,85,247,0.3)', text: '#a78bfa' },
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

function Pre({ children }: { children: string }) {
  return (
    <div style={{ overflowX: 'auto', background: '#0d1424', border: '1px solid #1e293b', borderRadius: 10, padding: '14px 16px', marginBottom: 16 }}>
      <pre style={{ margin: 0, fontSize: '11.5px', lineHeight: 1.55, color: '#cbd5e1', fontFamily: 'monospace', whiteSpace: 'pre' }}>{children}</pre>
    </div>
  );
}

function Table({ head, rows, widths }: { head: string[]; rows: React.ReactNode[][]; widths?: string[] }) {
  return (
    <div style={{ overflowX: 'auto', marginBottom: 16 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', minWidth: 520 }}>
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} style={{
                textAlign: 'left', padding: '7px 10px', color: '#64748b', fontWeight: 700,
                fontSize: '10.5px', textTransform: 'uppercase', letterSpacing: '0.04em',
                borderBottom: '1px solid #1e293b', width: widths?.[i],
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j} style={{
                  padding: '7px 10px', color: '#94a3b8', borderBottom: '1px solid #131c2e',
                  verticalAlign: 'top', lineHeight: 1.55,
                }}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const M = ({ children }: { children: React.ReactNode }) => (
  <span style={{ fontFamily: 'monospace', fontSize: '11.5px', color: '#7dd3fc' }}>{children}</span>
);

const Hi = ({ children }: { children: React.ReactNode }) => (
  <strong style={{ color: '#e2e8f0', fontWeight: 700 }}>{children}</strong>
);

// ── Page ──────────────────────────────────────────────────────────────────────

const TOC = [
  ['flow', '1. End-to-end flow'],
  ['sources', '2. Data sources'],
  ['adapters', '3. Adapter selection'],
  ['write', '4. The write path'],
  ['schedules', '5. Every schedule (70 jobs)'],
  ['caches', '6. Redis caches'],
  ['downstream', '7. Downstream consumers'],
  ['health', '8. Health & DQ checks'],
  ['failures', '9. How this has failed before'],
];

export default function DataPipelinePage() {
  const router = useRouter();
  const [ok, setOk] = useState(false);

  useEffect(() => {
    const s = getSession();
    if (!s) { router.replace('/login'); return; }
    if (s.role !== 'admin') { router.replace('/'); return; }
    setOk(true);
  }, [router]);

  if (!ok) return null;

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto', padding: '24px 20px 80px' }}>
      <div style={{ marginBottom: 8 }}>
        <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#f1f5f9', margin: 0 }}>
          Data Pipeline &amp; Ingestion
        </h1>
        <div style={{ fontSize: '12.5px', color: '#64748b', marginTop: 6, lineHeight: 1.6 }}>
          End-to-end reference: where every number on this platform comes from, how it gets in,
          on what schedule, and what reads it afterwards.
          {' '}Verified against source and production on <Hi>2026-09-09</Hi>.
        </div>
      </div>

      <Callout tone="info" title="Why this page exists">
        On 2026-09-09, four US symbols (UBER, CWEN, ARMK, BIP) were found frozen at the previous
        Friday&rsquo;s close &mdash; <Hi>four days stale, with zero errors anywhere</Hi>. A
        DELAYED-plan data provider sat first in the adapter priority and answered out-of-range
        windows with an empty <Code>HTTP 200</Code>, while the ingest logged a clean success.
        No single view showed the shape of the pipeline, so it was only caught by eye. This page
        is that view.
      </Callout>

      {/* TOC */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 8, padding: '12px 14px', marginBottom: 30,
        background: '#0d1424', border: '1px solid #1e293b', borderRadius: 10,
      }}>
        {TOC.map(([id, label]) => (
          <a key={id} href={`#${id}`} style={{
            fontSize: '11.5px', color: '#7dd3fc', textDecoration: 'none',
            padding: '4px 9px', borderRadius: 6, background: 'rgba(56,189,248,0.08)',
            border: '1px solid rgba(56,189,248,0.2)',
          }}>{label}</a>
        ))}
      </div>

      {/* ── 1. FLOW ── */}
      <Section id="flow" title="1. End-to-end flow">
        <p style={{ marginTop: 0 }}>
          Every price bar follows the same path. The <Hi>refresh cycle</Hi> below runs 5&times;
          per trading day per market, plus a 5-minute intraday loop during the session.
        </p>

        <Pre>{`┌─────────────────────────────────────────────────────────────────────────┐
│  EXTERNAL SOURCES                                                        │
│                                                                          │
│   Unusual Whales      yfinance          Polygon         FMP / EDGAR      │
│   (US price + flow)   (HK + US fallbk)  (DELAYED, last) (fundamentals)   │
└───────────┬──────────────┬──────────────────┬───────────────┬───────────┘
            │              │                  │               │
            └──────────────┴────────┬─────────┘               │
                                    ▼                          │
                   ┌────────────────────────────────┐          │
                   │  ingest_symbol()               │          │
                   │  adapters/ chain by _PRIORITY  │          │
                   │  first NON-EMPTY frame wins    │          │
                   └────────────────┬───────────────┘          │
                                    ▼                          │
                   ┌────────────────────────────────┐          │
                   │  validate_ohlcv()              │          │
                   │  high>=low, high>=open|close   │          │
                   │  low<=open|close, all > 0      │          │
                   │  volume > 0  (HK daily: >= 0)  │          │
                   └────────────────┬───────────────┘          │
                                    ▼                          │
                   ┌────────────────────────────────┐          │
                   │  dedup on (stock_id,ts,tf)     │          │
                   │  keeps LAST occurrence         │          │
                   └────────────────┬───────────────┘          │
                                    ▼                          ▼
                   ┌──────────────────────────────────────────────────┐
                   │  pg_insert(Price) ON CONFLICT DO UPDATE           │
                   │  + parquet write   + Redis cache bust             │
                   └────────────────┬─────────────────────────────────┘
                                    ▼
        ┌───────────────────────────┴────────────────────────────┐
        ▼                  ▼                  ▼                  ▼
  ranking-engine     signal-engine      ml-prediction      paper trading
  K-Score            4 horizons         train + infer      entry/exit gates
        │                  │                  │                  │
        └──────────────────┴────────┬─────────┴──────────────────┘
                                    ▼
                        alerts · emails · dashboards`}</Pre>

        <SubSection title="The refresh cycle, stage by stage">
          <p style={{ marginTop: 0 }}>
            <Code>_refresh_market(market)</Code> &mdash; <M>scheduler.py:632</M>. Stages are
            deliberately isolated so a data-source blip cannot kill alerts or paper trading.
          </p>
          <Table
            head={['#', 'Stage', 'What runs', 'Isolated?']}
            widths={['5%', '22%', '53%', '20%']}
            rows={[
              ['1', <Hi key="a">Ingest</Hi>, <span key="b"><Code>ingest_universe(symbols, &quot;1d&quot;)</Code> &rarr; sector ETFs &rarr; 2800.HK (the DB-backed <M>^HSI</M> stand-in)</span>, 'yes — try/except'],
              ['2', <Hi key="c">Rankings + signals</Hi>, <span key="d"><Code>POST /rankings/refresh</Code> then <Code>POST /signals/refresh</Code></span>, 'runs even if ingest partly failed'],
              ['2.5', <Hi key="e">Auto-research</Hi>, 'background research for top BUY signals lacking a fresh report', 'yes'],
              ['3', <Hi key="f">Alerts</Hi>, <span key="g"><Code>check_signal_alerts()</Code> — <Hi>5&times;/day, not per-minute</Hi></span>, 'always runs'],
              ['4', <Hi key="h">Paper trading</Hi>, <span key="i"><Code>_run_paper_trading_step(market=market)</Code> — market-scoped</span>, 'always runs'],
            ]}
          />
          <Callout tone="warn" title="Stage 2 uses the last good bar">
            Rankings and signals run <Hi>even when ingest failed</Hi>. That is deliberate &mdash;
            a transient source outage should not blank the platform &mdash; but it means a silent
            ingest stall produces <Hi>confident output computed on stale prices</Hi>, with no
            error. That is exactly what happened in AUD-ING-POLYGONDELAYED.
          </Callout>
        </SubSection>
      </Section>

      {/* ── 2. SOURCES ── */}
      <Section id="sources" title="2. Data sources">
        <SubSection title="Price data (OHLCV)">
          <Table
            head={['Source', 'Markets', 'Role', 'Status']}
            widths={['20%', '14%', '38%', '28%']}
            rows={[
              [<Hi key="a">Unusual Whales</Hi>, 'US only', <span key="b">First choice for US since 2026-09-09. <Code>/api/stock/&#123;ticker&#125;/ohlc/&#123;candle&#125;</Code></span>, <span key="c" style={{ color: '#22c55e' }}>live · authenticated · 120k/day</span>],
              [<Hi key="d">yfinance</Hi>, 'US + HK', <span key="e"><Hi>Sole HK source.</Hi> US fallback.</span>, <span key="f" style={{ color: '#fbbf24' }}>live · unauthenticated scrape · rate-limits</span>],
              [<Hi key="g">Polygon</Hi>, 'US only', 'Last resort. Kept as a second opinion for backfills inside its coverage window.', <span key="h" style={{ color: '#f87171' }}>DELAYED plan · ~2 trading days behind</span>],
              [<Hi key="i">Alpha Vantage</Hi>, 'US only', 'Registered, third in priority.', <span key="j" style={{ color: '#64748b' }}>no key configured</span>],
            ]}
          />
          <Callout tone="warn" title="Polygon is stale, not dead — and that is worse">
            A 2026-09-04 audit recorded Polygon as &ldquo;confirmed dead in production (blank
            keys)&rdquo;. That was <Hi>wrong</Hi>: it has a live key returning HTTP 200 and was
            the <Hi>preferred</Hi> US provider. A genuinely blank key would have failed over
            cleanly and caused no incident. Verify a source by <Hi>issuing a request and judging
            the freshness of what comes back</Hi> &mdash; never by reading its config.
          </Callout>
        </SubSection>

        <SubSection title="Non-price data">
          <Table
            head={['Data', 'Source', 'Fetched by', 'Cadence']}
            widths={['24%', '20%', '32%', '24%']}
            rows={[
              ['Options flow alerts', 'Unusual Whales', <Code key="a">options_flow_alert_check</Code>, 'every 1 min (cached)'],
              ['Dark pool prints', 'Unusual Whales', <Code key="b">dark_pool_alert_check</Code>, 'every 1 min (15-min cache)'],
              ['GEX / gamma levels', 'Unusual Whales', <Code key="c">gex_eod</Code>, '17:15 ET Mon–Fri'],
              ['Short interest / float', 'Unusual Whales', 'squeeze checks', 'every 1 min (cached)'],
              ['ETF fund flows', 'Unusual Whales', <Code key="d">etf_fund_flows_daily</Code>, '17:30 ET Mon–Fri'],
              ['Options chains', 'Unusual Whales', <Code key="e">options_game_plan_eod</Code>, '17:30 ET Mon–Fri'],
              ['Fundamentals', 'FMP / yfinance', <Code key="f">fundamentals_snapshot_weekly</Code>, 'Sun 16:30 ET'],
              ['SEC 8-K filings', 'EDGAR', <Code key="g">edgar_8k_ingest_daily</Code>, '17:30 ET Mon–Fri'],
              ['Congress trades', 'Unusual Whales', 'congress ingest', 'daily'],
              ['HK Connect flows', 'HKEX', <Code key="h">hk_connect_flows_daily</Code>, '17:00 HKT Mon–Fri'],
              ['News headlines', 'news-intelligence', 'service ingest', 'continuous'],
            ]}
          />
        </SubSection>
      </Section>

      {/* ── 3. ADAPTERS ── */}
      <Section id="adapters" title="3. Adapter selection">
        <p style={{ marginTop: 0 }}>
          <Code>_PRIORITY</Code> in <M>adapters/registry.py</M> defines preference order. An
          adapter only enters the candidate list if its <Code>supports(market, timeframe)</Code>
          {' '}returns true &mdash; which for Polygon and Alpha Vantage also requires a
          configured key.
        </p>

        <Pre>{`_PRIORITY = ["unusual_whales", "yfinance", "alpha_vantage", "polygon"]

resolved candidate lists (verified live 2026-09-09):
    US 1d  ->  ['unusual_whales', 'yfinance', 'alpha_vantage', 'polygon']
    US 5m  ->  ['unusual_whales', 'yfinance', 'polygon']
    HK 1d  ->  ['yfinance']                     <-- UW has NO HK coverage`}</Pre>

        <SubSection title="The selection chain in ingest_symbol()">
          <Pre>{`if provider:                          -> use exactly that adapter
elif symbol.endswith(".HK") or market == "HK":
                                      -> yfinance   (Polygon has no HK data)
elif force or head is None:           -> yfinance   (bulk/backfill; preserve paid quota)
elif not _polygon_budget_available(): -> yfinance   (free-tier minute budget spent)
else:                                 -> get_adapters(market, timeframe)`}</Pre>
          <p>
            The loop then tries each adapter in order and takes the{' '}
            <Hi>first non-empty validated frame</Hi>. An empty frame does not{' '}
            <Code>break</Code> &mdash; it falls through to the next adapter.
          </p>
          <Callout tone="good" title="FIXED 2026-09-09 — the budget gate was skipping the primary">
            <Code>_polygon_budget_available()</Code> was written when Polygon was <Hi>first</Hi>
            in priority, so exhausting its free-tier 5-req/min budget meant &ldquo;go straight to
            yfinance rather than send a request we know will 429&rdquo;. After the reorder put
            Polygon <Hi>last</Hi>, that same line hardcoded yfinance and{' '}
            <Hi>skipped Unusual Whales entirely</Hi>.
            <div style={{ marginTop: 8 }}>
              The blast radius was near-total: the counter increments on <Hi>every</Hi> US
              incremental ingest whether or not Polygon is reached, so with a budget of 5 and 131
              US symbols, only the <Hi>first 5 symbols per minute</Hi> saw UW — the other{' '}
              <Hi>~126 were forced onto yfinance-only</Hi>, the rate-limiting source the reorder
              existed to stop depending on. The previous day&rsquo;s fix was inert for 96% of the
              universe, and nothing failed: yfinance answered and <Code>ingest.done</Code> logged
              success.
            </div>
            <div style={{ marginTop: 8 }}>
              Fixed by making the gate <Hi>exclude Polygon</Hi> rather than select a replacement.
              The lesson generalises: <Hi>a guard written as &ldquo;fall back to X&rdquo; encodes
              the priority order current when it was written</Hi> — reordering the list does not
              update the guard. Prefer &ldquo;exclude Y&rdquo; over &ldquo;use X&rdquo;.
            </div>
          </Callout>

          <Callout tone="good" title="Why an empty response must never be authoritative">
            An empty frame is indistinguishable from &ldquo;no bars traded&rdquo;. Polygon now{' '}
            <Hi>raises</Hi> when its DELAYED plan cannot see the requested window, so the failure
            is visible in logs rather than an ambiguous shrug.
          </Callout>
        </SubSection>
      </Section>

      {/* ── 4. WRITE PATH ── */}
      <Section id="write" title="4. The write path">
        <SubSection title="validate_ohlcv() — the only gate before a write">
          <p style={{ marginTop: 0 }}>
            <M>ingestion.py:120</M>. <Code>pg_insert(Price)</Code> is the <Hi>only writer
            anywhere</Hi>, so every bar in the database passed these rules.
          </p>
          <Table
            head={['Rule', 'Applies to']}
            widths={['58%', '42%']}
            rows={[
              [<Code key="a">high &gt;= low, high &gt;= open, high &gt;= close</Code>, 'every market, every timeframe'],
              [<Code key="b">low &lt;= open, low &lt;= close</Code>, 'every market, every timeframe'],
              [<Code key="c">open, high, low, close all &gt; 0</Code>, 'every market, every timeframe'],
              [<Code key="d">volume &gt; 0</Code>, <span key="e">US daily/weekly <Hi>only</Hi></span>],
              [<Code key="f">volume &gt;= 0</Code>, <span key="g">HK (any tf) + US intraday &mdash; see below</span>],
            ]}
          />
          <Callout tone="warn" title="The volume rule is market-aware for a measured reason">
            <Code>volume &gt; 0</Code> is a <Hi>US-liquid-equity assumption</Hi>. For thinly
            traded HK small caps a zero-volume day is a legitimate no-trade session. It silently
            deleted <Hi>62% of 1671.HK</Hi> and <Hi>56% of 0117.HK</Hi> history before being made
            market-aware. Note it <Hi>lowers the floor to &gt;= 0</Hi> rather than skipping the
            check &mdash; skipping would let a <Hi>negative</Hi> volume through.
          </Callout>
        </SubSection>

        <SubSection title="Dedup, upsert, and side effects">
          <Table
            head={['Step', 'What it does', 'Why']}
            widths={['22%', '44%', '34%']}
            rows={[
              [<Hi key="a">Dedup</Hi>, <span key="b">collapse duplicate <Code>(stock_id, ts, timeframe)</Code>, keeping the last</span>, <span key="c">Postgres raises <M>CardinalityViolation</M> if one statement maps two rows to the same conflict target</span>],
              [<Hi key="d">Upsert</Hi>, <Code key="e">ON CONFLICT DO UPDATE</Code>, 'makes re-ingest idempotent; late corrections overwrite'],
              [<Hi key="f">Parquet</Hi>, 'partitioned by symbol + timeframe', 'analytics / backtest reads'],
              [<Hi key="g">Cache bust</Hi>, <Code key="h">stockai:live_prices</Code>, 'so the next reader sees the new bar'],
            ]}
          />
          <Callout tone="warn" title="`inserted` does NOT mean new bars">
            <Code>result.rowcount</Code> on an upsert counts rows <Hi>sent</Hi>, not rows{' '}
            <Hi>changed</Hi>. A cycle that re-writes 5 identical existing bars reports{' '}
            <Code>inserted=5</Code> and logs a clean success. That is precisely how a four-day
            outage stayed invisible. <Code>ingest.done</Code> now also logs{' '}
            <Code>advanced</Code>, <Code>head_before</Code>, <Code>head_after</Code> and{' '}
            <Code>adapter</Code> &mdash; <Hi>read <Code>advanced</Code></Hi>, not{' '}
            <Code>inserted</Code>.
          </Callout>
        </SubSection>
      </Section>

      {/* ── 5. SCHEDULES ── */}
      <Section id="schedules" title="5. Every schedule (70 jobs)">
        <p style={{ marginTop: 0 }}>
          Enumerated from <M>scheduler.py</M> by parsing whole <Code>add_job(...)</Code> blocks.
          All times are in the timezone shown.
        </p>

        <SubSection title="Price ingestion — the core loop">
          <Table
            head={['Job', 'Schedule', 'TZ']}
            widths={['34%', '46%', '20%']}
            rows={[
              [<Code key="a">us_premarket_5m_early</Code>, '04:00–08:55, every 5 min, Mon–Fri', 'ET'],
              [<Code key="b">us_premarket_5m_9am</Code>, '09:00–09:25, every 5 min, Mon–Fri', 'ET'],
              [<Code key="c">us_open_burst</Code>, '09:25–09:45, every 5 min, Mon–Fri', 'ET'],
              [<Code key="d">us_5m_intraday</Code>, '09:30–15:55, every 5 min, Mon–Fri', 'ET'],
              [<Code key="e">us_intra</Code>, '10:00–14:55, every 5 min, Mon–Fri', 'ET'],
              [<Code key="f">us_close_burst</Code>, '15:30–15:55, every 5 min, Mon–Fri', 'ET'],
              [<Code key="g">us_post_close</Code>, <span key="h"><Hi>16:30 Mon–Fri</Hi> — the settled daily bar</span>, 'ET'],
              [<Code key="i">hk_open_burst</Code>, '09:25–09:45, every 5 min, Mon–Fri', 'HKT'],
              [<Code key="j">hk_5m_intraday</Code>, '09:30–15:55 (skips 12:00 lunch), every 5 min', 'HKT'],
              [<Code key="k">hk_intra</Code>, '10:00–14:55, every 5 min, Mon–Fri', 'HKT'],
              [<Code key="l">hk_close_burst</Code>, '15:30–15:55, every 5 min, Mon–Fri', 'HKT'],
              [<Code key="m">hk_post_close</Code>, <span key="n"><Hi>16:30 Mon–Fri</Hi></span>, 'HKT'],
              [<Code key="o">weekly_full_refresh</Code>, <span key="p">Sun 14:00 — <Hi>force=True</Hi>, full re-fetch</span>, 'PT'],
            ]}
          />
          <Callout tone="warn" title="HK's lunch break is a real scheduling hazard">
            HKEX closes 12:00–13:00 HKT. <Code>hk_5m_intraday</Code> skips the 12:00 hour. A
            backtest harness that built entry timestamps at 12:00 HKT produced{' '}
            <Hi>zero HK entries for months</Hi> because that instant never trades.
          </Callout>
        </SubSection>

        <SubSection title="Every-minute alert detectors (16 alert jobs + the cache that feeds them)">
          <p style={{ marginTop: 0 }}>
            All read shared Redis caches rather than fetching per-symbol, so the cadence is cheap.
          </p>
          <Table
            head={['Job', 'Detects']}
            widths={['42%', '58%']}
            rows={[
              [<Code key="a">price_alert_check</Code>, 'user price alerts (78 untriggered)'],
              [<Code key="b">conditional_order_check</Code>, <span key="c">if/then orders — <Hi>fails closed</Hi> (real-money-adjacent)</span>],
              [<Code key="d">short_squeeze_alert_check</Code>, 'short-squeeze setups'],
              [<Code key="e">squeeze_ignition_alert_check</Code>, 'squeeze ignition'],
              [<Code key="f">squeeze_watch_revert_check</Code>, 'squeeze watch reverts'],
              [<Code key="g">options_flow_alert_check</Code>, 'unusual options activity'],
              [<Code key="h">dark_pool_alert_check</Code>, 'large dark-pool prints'],
              [<Code key="i">volume_anomaly_check</Code>, 'abnormal volume, universe-wide'],
              [<Code key="l">sr_watch_check</Code>, 'support/resistance reverts'],
              [<Code key="m">value_area_breakdown_check</Code>, 'value-area breakdowns'],
              [<Code key="n">portfolio_drawdown_alert_check</Code>, 'portfolio drawdown'],
              [<Code key="o">top3_conviction_check</Code>, 'top-3 conviction changes'],
              [<Code key="p">earnings_reaction_check</Code>, 'post-earnings reactions'],
              [<Code key="q">earnings_impact_alert_check</Code>, 'earnings impact'],
              [<Code key="r">early_earnings_news_alert_check</Code>, 'early earnings news'],
              [<Code key="s">macro_reaction_alert_check</Code>, 'macro event reactions'],
              [<Code key="t">live_price_cache_refresh</Code>, <span key="u"><Hi>feeds all of the above</Hi> — 172 symbols/min</span>],
            ]}
          />
          <Callout tone="good" title="Cache TTLs govern API spend, not the job interval">
            <Code>get_dark_pool_prints()</Code> caches <Hi>15 minutes per symbol</Hi>, so a
            per-minute loop over ~20 symbols serves 14 of every 15 cycles from cache &mdash; which
            is why dark pool shows <Hi>4,326 calls/day</Hi> rather than the ~28,800 an uncached
            loop would make. Changing a job from 1 min to 5 min would barely move quota; changing
            a cache TTL would move it a lot.
          </Callout>
        </SubSection>

        <SubSection title="Daily — post-close chain (ET)">
          <Table
            head={['Time', 'Job', 'What']}
            widths={['14%', '40%', '46%']}
            rows={[
              ['06:10', <Code key="a">signal_watchdog_daily</Code>, 'self-healing threshold watchdog'],
              ['08:00', <Code key="b">premarket_brief_us</Code>, 'pre-market brief email'],
              ['08:30', <Code key="c">broker_auth_check</Code>, 'E*Trade token liveness'],
              ['08:50', <Code key="d">morning_digest_us</Code>, <span key="e">digest — <Hi>market-holiday aware</Hi></span>],
              ['17:00', <Code key="f">options_flow_eod</Code>, 'end-of-day options flow'],
              ['17:00', <Code key="i">paper_portfolio_digest</Code>, 'paper portfolio email'],
              ['17:15', <Code key="j">gex_eod</Code>, 'gamma exposure levels'],
              ['17:30', <Code key="k">options_game_plan_eod</Code>, 'options game plans'],
              ['17:30', <Code key="l">etf_fund_flows_daily</Code>, 'ETF fund flows'],
              ['17:30', <Code key="m">edgar_8k_ingest_daily</Code>, 'SEC 8-K filings'],
              ['18:00', <Code key="n">value_area_levels_daily</Code>, 'volume-profile value areas'],
              ['18:05', <Code key="o">fix_effectiveness_recheck_daily</Code>, 'did past fixes hold?'],
              ['18:15', <Code key="p">squeeze_alert_outcome_eval_daily</Code>, 'score squeeze alerts'],
              ['18:20', <Code key="q">prebreakout_alert_outcome_eval_daily</Code>, 'score prebreakout alerts'],
              ['18:25', <Code key="r">options_flow_alert_outcome_eval_daily</Code>, 'score flow alerts'],
              ['18:30', <Code key="s">dark_pool_alert_outcome_eval_daily</Code>, 'score dark-pool alerts'],
            ]}
          />
        </SubSection>

        <SubSection title="Weekly (Sunday)">
          <Table
            head={['Time', 'Job', 'What']}
            widths={['16%', '40%', '44%']}
            rows={[
              ['03:00 UTC', <Code key="a">meta_model_monthly_retrain</Code>, '1st Sunday only — meta-model'],
              ['04:00 UTC', <Code key="b">position_scaling_gate_weekly_retrain</Code>, 'scaling gate retrain'],
              ['04:00 UTC', <Code key="c">backfill_realized_ev_monthly</Code>, '1st Sunday only'],
              ['04:30 UTC', <Code key="d">position_scaling_gate_weekly_drift_check</Code>, 'drift check'],
              ['14:00 PT', <Code key="e">weekly_full_refresh</Code>, <span key="f"><Hi>force=True</Hi> full re-fetch</span>],
              ['15:00 PT', <Code key="g">db_purge_weekly</Code>, 'purge old rows (§6)'],
              ['16:00 ET', <Code key="h">sector_rotation_weekly</Code>, 'sector rotation'],
              ['16:30 ET', <Code key="i">fundamentals_snapshot_weekly</Code>, 'fundamentals snapshot'],
              ['17:00 ET', <Code key="j">watchlist_auto_rotation_weekly</Code>, 'watchlist rotation'],
              ['17:30 ET', <Code key="k">theme_forecast_weekly</Code>, 'theme forecast email'],
              ['17:45 ET', <Code key="l">trade_coach_weekly</Code>, 'trade-pattern coach email'],
            ]}
          />
        </SubSection>

        <SubSection title="Other intervals">
          <Table
            head={['Job', 'Schedule']}
            widths={['46%', '54%']}
            rows={[
              [<Code key="a">llm_usage_spike_check</Code>, 'every 15 min'],
              [<Code key="b">gamma_unwind_alert_check</Code>, 'every 4 hours'],
              [<Code key="b2">prebreakout_alert_check</Code>, <span key="b3">every 4 hours — <Hi>not</Hi> every minute</span>],
              [<Code key="b4">data_quality_checks</Code>, 'every 2 hours (46 gauges)'],
              [<Code key="c">avg_volume_cache_refresh</Code>, 'every 4 hours'],
              [<Code key="d">analyst_target_outcomes_daily</Code>, '06:45 UTC'],
              [<Code key="e">position_scaling_shadow_daily_resolve</Code>, '05:00 UTC'],
              [<Code key="f">hk_connect_flows_daily</Code>, '17:00 HKT Mon–Fri'],
            ]}
          />
        </SubSection>
      </Section>

      {/* ── 6. CACHES ── */}
      <Section id="caches" title="6. Redis caches & retention">
        <Table
          head={['Key pattern', 'TTL', 'Refreshed by', 'Read by']}
          widths={['30%', '12%', '28%', '30%']}
          rows={[
            [<Code key="a">stockai:live_prices</Code>, '1 min', <Code key="b">live_price_cache_refresh</Code>, 'all 17 minute-jobs'],
            [<Code key="c">stockai:avg_volume</Code>, '4 h', <Code key="d">avg_volume_cache_refresh</Code>, 'volume anomaly, squeeze'],
            [<Code key="e">stockai:uw:darkpool:&#123;sym&#125;</Code>, '15 min', <Code key="f">dark_pool_alert_check</Code>, 'dark-pool alerts'],
            [<Code key="g">stockai:fundamentals:v2:&#123;sym&#125;</Code>, 'weekly', <Code key="h">fundamentals_snapshot_weekly</Code>, 'squeeze, K-Score, ML'],
            [<Code key="i">stockai:metric:uw_calls:&#123;ep&#125;:&#123;day&#125;</Code>, 'per-day', 'every UW call', 'quota monitoring'],
            [<Code key="j">stockai:admin:provider_key:*</Code>, 'none', 'Settings page', 'adapter key lookup'],
            [<Code key="k">auth:blacklist:&#123;jti&#125;</Code>, 'token life', 'logout', 'JWT verification'],
          ]}
        />
        <Callout tone="info" title="Provider keys live in Redis, not .env">
          <Code>get_runtime_key(&quot;polygon&quot;)</Code> reads Redis first, then falls back to
          env. Verified 2026-09-09: the Polygon key is <Hi>Redis-only</Hi> (no env fallback), so
          clearing it on the Settings page fully disables that adapter &mdash;{' '}
          <Code>supports()</Code> gates on the key, so it drops out of the candidate list
          entirely. Had an env value also been set, clearing Settings would have <Hi>looked</Hi>
          {' '}like it worked while the adapter silently kept running.
        </Callout>
        <SubSection title="Database retention (db_purge_weekly)">
          <Table
            head={['Table', 'Retention']}
            widths={['40%', '60%']}
            rows={[
              [<Code key="a">prices</Code> , 'D1 kept indefinitely'],
              [<Code key="b">prices_5m</Code>, '90 days'],
              [<Code key="c">signals</Code>, '365 days (~4 rows/stock/day)'],
              [<Code key="d">signal_outcomes</Code>, '400 days'],
              [<Code key="e">scheduler_jobs</Code>, <span key="f" style={{ color: '#fbbf24' }}>unpurged</span>],
            ]}
          />
        </SubSection>
      </Section>

      {/* ── 7. DOWNSTREAM ── */}
      <Section id="downstream" title="7. Downstream consumers">
        <p style={{ marginTop: 0 }}>Once a bar lands, these read it:</p>
        <Table
          head={['Consumer', 'Port', 'Reads', 'When']}
          widths={['26%', '10%', '38%', '26%']}
          rows={[
            [<Hi key="a">ranking-engine</Hi>, '8004', 'D1 bars → K-Score, relative strength, sector percentile', 'stage 2 of each refresh'],
            [<Hi key="b">signal-engine</Hi>, '8005', 'D1 + intraday → 4 horizons (GROWTH/SWING/LONG/SHORT)', 'stage 2 of each refresh'],
            [<Hi key="c">ml-prediction</Hi>, '8003', 'D1 → 22 features, train + live inference', 'nightly train; inference per signal'],
            [<Hi key="d">technical-analysis</Hi>, '8002', 'D1 + intraday → indicators, levels, patterns', 'on demand'],
            [<Hi key="e">paper trading</Hi>, '—', 'live price → entry gates, stops, exits', 'stage 4 + every 5 min'],
            [<Hi key="f">decision-engine</Hi>, '8009', 'signals + game plans → authoritative entry gate', 'per candidate'],
            [<Hi key="g">strategy / portfolio</Hi>, '8006/8007', 'D1 → backtests, optimisation', 'weekly + on demand'],
          ]}
        />
        <Callout tone="info" title="decision-engine is the authoritative gate">
          <Code>decision_engine_mode</Code> defaults to <Code>&quot;primary&quot;</Code>, so
          decision-engine decides. <Code>paper_trading_engine._should_enter()</Code> is the
          <Hi> shadow-logged fallback</Hi>. A fix applied only to the fallback produces plausible
          shadow logs while <Hi>changing no real decision</Hi> &mdash; this has happened three
          times. After changing any gate, grep the symbol under{' '}
          <Code>services/decision-engine/</Code> and confirm it appears.
        </Callout>
      </Section>

      {/* ── 8. HEALTH ── */}
      <Section id="health" title="8. Health & data-quality checks">
        <p style={{ marginTop: 0 }}>
          <Code>data_quality_checks</Code> runs on an <Hi>interval of every 2 hours</Hi>
          (<Code>IntervalTrigger(hours=2)</Code>, not a daily cron) and evaluates{' '}
          <Hi>46 gauges</Hi>. Results surface on{' '}
          <a href="/admin-health" style={{ color: '#7dd3fc' }}>System Health</a>.
        </p>
        <Table
          head={['Gauge', 'Watches']}
          widths={['40%', '60%']}
          rows={[
            [<Code key="a">prices_us_d1</Code> , 'newest US daily bar overall'],
            [<Code key="b">prices_hk_d1</Code>, 'newest HK daily bar overall'],
            [<Code key="c">stale_symbols_d1</Code>, <span key="d"><Hi>per-symbol</Hi> staleness (&gt;7 days)</span>],
            [<Code key="e">uw_rate_limit_events_48h</Code>, 'UW 429s in 48h — currently 0'],
            [<Code key="f">rankings_us / rankings_hk</Code>, 'ranking freshness'],
            [<Code key="g">signals_us / signals_hk</Code>, 'signal freshness'],
            [<Code key="h">signal_outcomes</Code>, <span key="i">evaluation staleness — counted in <Hi>trading days</Hi></span>],
            [<span key="j"><Code>check_*</Code> (17)</span>, 'per-job liveness for every minute-job'],
            [<span key="k"><Code>squeeze_*_48h</Code> (6)</span>, 'squeeze reject-reason breakdowns'],
          ]}
        />
        <SubSection title="Gating gaps — found and fixed 2026-09-09">
          <p style={{ marginTop: 0 }}>
            Four were found in one pass while writing this page. They were fixed{' '}
            <Hi>four different ways on purpose</Hi>, because what a job actually{' '}
            <Hi>costs</Hi> on a non-trading day differs — and that decides where to gate it.
          </p>
          <Table
            head={['Job', 'Was', 'Fix + why']}
            widths={['24%', '34%', '42%']}
            rows={[
              [<Code key="a">live_price_cache_refresh</Code>,
               <span key="b">raw <Code>weekday() &gt;= 5</Code> — ran every market <Hi>holiday</Hi></span>,
               <span key="c"><Hi>Gated in-function, per market.</Hi> A yfinance bulk download of ~173 stocks ≈ <Hi>480 wasted downloads/holiday</Hi>, caching stale quotes for 16 scanners. Per-market because a US holiday is often a normal HKEX session.</span>],
              [<Code key="d">edgar_8k_ingest_daily</Code>,
               <span key="e"><Code>mon-fri</Code>, no holiday check</span>,
               <span key="f"><Hi>Gated in-function.</Hi> The sweep is rate-limited 0.15s/CIK for SEC fair-use, so a no-op pass still spends real budget. Skip path still records a job status — a silent skip looks like a dead job.</span>],
              [<span key="g">the six <Code>18:0x</Code> evaluators</span>,
               <span key="h">no <Code>day_of_week</Code> — fired weekends</span>,
               <span key="i"><Hi>Fixed at the trigger.</Hi> They query real <Code>Price</Code> rows, so a weekend run is <Hi>waste, not corruption</Hi>. An internal date guard would also have blocked a legitimate Monday catch-up.</span>],
              [<Code key="j">avg_volume_cache_refresh</Code>,
               <span key="k">no <Code>misfire_grace_time</Code></span>,
               <span key="l"><Hi>Set to 300s.</Hi> A missed fire is <Hi>dropped</Hi>, and with <Code>max_instances=1</Code> that can retire the schedule — the AUD-MISFIREGRACE-OPTIONSFLOW shape. A repo-wide test now requires one on every interval job.</span>],
            ]}
          />
          <Callout tone="example" title="The judgement worth reusing">
            Before choosing <Hi>where</Hi> to gate a job, ask what it actually costs on a
            non-trading day. A job that burns a rate-limited API or writes stale data into a
            shared cache deserves an <Hi>in-function</Hi> guard. A job that queries real bars and
            finds nothing only needs a <Hi>cadence</Hi> change — and an over-eager internal guard
            there would block legitimate catch-up runs.
          </Callout>
        </SubSection>

        <Callout tone="warn" title="Aggregate gauges are blind to single-symbol death">
          <Code>prices_us_d1</Code> is a <Code>MAX()</Code> over all symbols &mdash; one fresh
          symbol makes it green. <Code>stale_symbols_d1</Code> exists precisely to catch
          per-symbol death, and it{' '}
          <Hi>still missed the 4-day, 4-symbol stall</Hi> that prompted this page, because those
          symbols were 4 days stale against a 7-day threshold.
        </Callout>
      </Section>

      {/* ── 9. FAILURES ── */}
      <Section id="failures" title="9. How this pipeline has actually failed">
        <p style={{ marginTop: 0 }}>
          Real incidents, each with the generalisable check. <Hi>Not one was a crash</Hi> &mdash;
          every one was a silent wrong answer, which is why &ldquo;no errors in the logs&rdquo; is
          not evidence anything works.
        </p>
        <Table
          head={['Incident', 'What happened', 'The check it teaches']}
          widths={['22%', '43%', '35%']}
          rows={[
            [<Hi key="a">POLYGONDELAYED</Hi>, '4 symbols frozen 4 days; DELAYED plan answered empty HTTP 200 from first place in priority', 'An empty response must never be authoritative. Verify a source by issuing a request.'],
            [<Hi key="b">HKZEROVOLUME</Hi>, <span key="c">a <Code>volume &gt; 0</Code> rule deleted 62% of an illiquid HK symbol&rsquo;s history</span>, 'Ask whether a validity rule encodes a market assumption.'],
            [<Hi key="d">DELISTNEVERFIRES</Hi>, 'delisting detector unreachable — an explicit date window returns a dead ticker&rsquo;s last stale bar and succeeds', 'Never infer liveness from the absence of an exception; judge bar freshness.'],
            [<Hi key="e">DIGEST-HOLIDAYBLIND</Hi>, <span key="f">13 emails sent on Labor Day showing Friday&rsquo;s prices as live</span>, <span key="g">A <Code>mon-fri</Code> cron is not a market-open check.</span>],
            [<Hi key="h">CROSSMARKETSWEEP</Hi>, 'HK&rsquo;s open burst force-closed US positions at the previous day&rsquo;s close (SNOW −19% at a price that never traded)', 'A job must act only on the market whose clock invoked it.'],
            [<Hi key="i">RANK-RSPLACEHOLDER</Hi>, 'a fabricated neutral 50.0 for a missing benchmark, which the weight optimiser then learned from', 'Fail closed. "Not measured" must never become "measured as zero".'],
            [<Hi key="j">CARDINALITYVIOLATION</Hi>, 'duplicate bars in one batch aborted the whole insert', 'Dedup before the conflict target matters.'],
          ]}
        />
        <Callout tone="example" title="The recurring shape">
          A component returns something <Hi>plausible</Hi> instead of failing: an empty list, a
          neutral 50.0, a stale bar, a default that is valid but wrong. Everything downstream
          keeps working and produces confident output. The fix is almost always the same &mdash;{' '}
          <Hi>make the ambiguous case loud</Hi>: raise instead of returning empty, return{' '}
          <Code>None</Code> instead of a placeholder, log whether the head actually moved.
        </Callout>
      </Section>

      <div style={{ marginTop: 40, paddingTop: 16, borderTop: '1px solid #1e293b', fontSize: '11.5px', color: '#475569', lineHeight: 1.7 }}>
        Sources: <M>services/market-data/src/services/scheduler.py</M>,{' '}
        <M>services/market-data/src/services/ingestion.py</M>,{' '}
        <M>services/market-data/src/adapters/</M>. Incident detail in{' '}
        <M>docs/incidents/</M>. Quota baseline measured 2026-09-08; the UW-adapter projection is
        re-checked 2026-09-10 (see <M>docs/2026-09-09/UW_QUOTA_CHECKPOINT.md</M>).
      </div>
    </div>
  );
}
