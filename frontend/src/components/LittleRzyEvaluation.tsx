import React from 'react';
import type { RzyEvaluationResponse, RzyEvalHypothesis, RzyEvalStats, RzyEvalSummary } from '@/lib/api';

// No Tailwind build exists in this app (globals.css has a fixed set of utilities), so layout and
// colour here are inline styles, as in LittleRzyBeginnerGuide.
const C = { text: '#cbd5e1', dim: '#94a3b8', faint: '#64748b', line: '#334155', head: '#f1f5f9',
  good: '#5eead4', bad: '#fca5a5', warn: '#fcd34d', panel: 'rgba(15,23,42,0.6)' };
const box: React.CSSProperties = { border: `1px solid ${C.line}`, borderRadius: 8, padding: 12, background: C.panel };
const th: React.CSSProperties = { textAlign: 'left', padding: '4px 8px', fontSize: 11, textTransform: 'uppercase', letterSpacing: '.04em', color: C.dim, borderBottom: `1px solid ${C.line}`, whiteSpace: 'nowrap' };
const td: React.CSSProperties = { padding: '4px 8px', verticalAlign: 'top', borderBottom: `1px solid ${C.line}` };
const num: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' };

const r = (v?: number | null, d = 3) => (v == null || !Number.isFinite(v) ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}`);
const pct = (v?: number | null) => (v == null || !Number.isFinite(v) ? '—' : `${(100 * v).toFixed(1)}%`);
const ci = (c?: [number, number] | null) => (c ? `${r(c[0])} to ${r(c[1])}` : '—');
const day = (v?: string | null) => (v ? new Date(/(Z|[+-]\d\d:\d\d)$/.test(v) ? v : `${v}Z`).toLocaleDateString() : '—');

// Plain-language names for the predeclared labels (rzy_evaluation_context.py).
const LABELS: Record<string, string> = {
  aligned: 'With the trend', opposed: 'Against the trend', neutral: 'No clear trend', unknown: 'Not enough data',
  not_applicable: 'Not applicable', all_aligned: 'All 3 other timeframes agree', majority_aligned: '2 of 3 agree',
  minority_aligned: '0 or 1 of 3 agree', incomplete: 'Some timeframes lack data', spy_above_50d: 'SPY above its 50-day average',
  spy_below_50d: 'SPY below its 50-day average', seen_by_v1: 'Sessions earlier tests saw', 'unseen_after_2026-10-20': 'Unseen sessions (after Oct 20)',
  outside_regular_session: 'Outside regular hours', low: 'Calmest third', mid: 'Middle third', high: 'Most volatile third', long: 'Long (buy)', short: 'Short (sell)',
  '1d': 'Daily', '1h': '1 hour', '15m': '15 minutes', '5m': '5 minutes',
};
const label = (k: string) => LABELS[k] ?? k;

function tone(verdict?: string) {
  const v = (verdict ?? '').toLowerCase();
  if (v.includes('better') || v.includes('above zero') || v.includes('first group better')) return C.good;
  if (v.includes('worse') || v.includes('below zero')) return C.bad;
  if (v.includes('insufficient')) return C.warn;
  return C.dim;
}

// What each verdict means for a beginner; the wording mirrors the backend's verdict strings.
function plain(verdict?: string) {
  const v = (verdict ?? '').toLowerCase();
  if (v.includes('insufficient')) return 'Too few trades to tell.';
  if (v.includes('no measurable')) return 'No better than entering at a random time. The range of likely values includes zero.';
  if (v.includes('first group better')) return 'The first group did measurably better.';
  if (v.includes('first group worse')) return 'The first group did measurably worse.';
  if (v.includes('better') || v.includes('above zero')) return 'Measurably better than random timing.';
  if (v.includes('worse') || v.includes('below zero')) return 'Measurably worse than random timing.';
  return verdict ?? '—';
}

function Verdict({ v }: { v?: string }) {
  return <span style={{ color: tone(v), fontWeight: 600 }}>{v ?? '—'}</span>;
}

function SliceTable({ title, intro, rows }: { title: string; intro?: string; rows?: Record<string, RzyEvalStats> }) {
  const entries = Object.entries(rows ?? {}).filter(([k]) => k !== 'not_applicable');
  if (!entries.length) return null;
  return <div style={{ marginTop: 16 }}>
    <h4 style={{ color: C.head, fontWeight: 600, margin: '0 0 4px' }}>{title}</h4>
    {intro && <p style={{ color: C.dim, fontSize: 13, margin: '0 0 6px', maxWidth: 820 }}>{intro}</p>}
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
        <thead><tr><th style={th}>Group</th><th style={th}>Trades</th><th style={th}>Win rate</th><th style={th}>Avg R</th>
          <th style={th}>Edge vs random</th><th style={th}>Edge 95% range</th><th style={th}>Verdict</th></tr></thead>
        <tbody>{entries.map(([k, s]) => <tr key={k}>
          <td style={td}>{label(k)}</td><td style={num}>{s.trades ?? 0}</td><td style={num}>{pct(s.win_rate)}</td>
          <td style={num}>{r(s.mean_r)}</td><td style={num}>{r(s.edge_vs_control_r)}</td><td style={num}>{ci(s.edge_ci95_by_symbol)}</td>
          <td style={td}><Verdict v={s.verdict} /></td></tr>)}</tbody>
      </table>
    </div>
  </div>;
}

function Headline({ title, verdict, note }: { title: string; verdict?: string; note: string }) {
  return <div style={{ ...box, flex: '1 1 220px' }}>
    <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.04em', color: C.dim }}>{title}</div>
    <div style={{ marginTop: 4, fontSize: 16, fontWeight: 600, color: tone(verdict) }}>{verdict ?? '—'}</div>
    <div style={{ marginTop: 4, fontSize: 12, color: C.dim }}>{plain(verdict)} {note}</div>
  </div>;
}

function Stat({ label: l, value, note }: { label: string; value: string; note: string }) {
  return <div style={{ ...box, flex: '1 1 150px' }}>
    <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.04em', color: C.dim }}>{l}</div>
    <div style={{ marginTop: 4, fontSize: 20, fontWeight: 600, color: C.head, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    <div style={{ marginTop: 2, fontSize: 12, color: C.faint }}>{note}</div>
  </div>;
}

function HypothesisRow({ h }: { h: RzyEvalHypothesis }) {
  const diff = h.kind === 'difference';
  const trades = Array.isArray(h.trades) ? `${h.trades[0]} vs ${h.trades[1]}` : String(h.trades);
  const edges = Array.isArray(h.edge_r) ? `${r(h.edge_r[0])} vs ${r(h.edge_r[1])}` : r(h.edge_r as number | null);
  return <tr>
    <td style={td}><strong style={{ color: C.head }}>{h.id}</strong>{h.role === 'primary' && <div style={{ fontSize: 11, color: C.good }}>primary</div>}</td>
    <td style={td}>{h.question}{diff && h.groups && <div style={{ fontSize: 12, color: C.faint }}>Groups: {h.groups[0]} vs {h.groups[1]}</div>}</td>
    <td style={num}>{trades}</td>
    <td style={num}>{edges}{diff && <div style={{ fontSize: 12, color: C.faint }}>difference {r(h.difference_r)}</div>}</td>
    <td style={num}>{ci(h.ci95)}</td>
    <td style={td}><Verdict v={h.verdict} /><div style={{ fontSize: 12, color: C.faint }}>{plain(h.verdict)}</div></td>
  </tr>;
}

function Results({ s, data }: { s: RzyEvalSummary; data: RzyEvaluationResponse }) {
  const o = s.baseline.overall;
  const slices = s.baseline as unknown as Record<string, Record<string, RzyEvalStats>>;
  const cov = s.snapshot.coverage ?? {};
  const latest = data.latest!;
  return <div style={{ display: 'grid', gap: 16 }}>
    <p style={{ color: C.dim, fontSize: 13, margin: 0, maxWidth: 860 }}>
      Every stored bar was replayed through the live Little RZY rules. Each trade was then compared with 20 trades taken at
      random times on the same stock, in the same direction, with the same stop and target distances and the same costs.
      The question is whether the pattern’s timing beats random timing. Data exported {day(s.snapshot.exported_at)}.
    </p>

    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
      <Headline title="All history" verdict={s.headline.all_data} note="Every trade the rules took." />
      <Headline title="Unseen sessions" verdict={s.headline.unseen} note={`Only signals after ${day(s.unseen_after)}, which no earlier test saw. The strongest evidence, but short.`} />
      <Headline title="Trading with the daily trend" verdict={s.headline.daily_alignment} note="The question of combining streams: do intraday trades that agree with the daily trend do better than ones that do not?" />
    </div>

    <details style={{ ...box }}>
      <summary style={{ cursor: 'pointer', color: C.head, fontWeight: 600 }}>How to read these numbers</summary>
      <ul style={{ margin: '8px 0 0', paddingLeft: 20, color: C.text, fontSize: 13, lineHeight: 1.6 }}>
        <li><strong>R</strong> is profit or loss divided by the amount risked (entry to stop). +1R means you made what you risked; −1R means you lost it.</li>
        <li><strong>Avg R</strong> is the average result per trade after costs. Positive is good, but a rising market can make it positive by itself.</li>
        <li><strong>Edge vs random</strong> is the trade’s R minus the average R of its random-time twins. This is the number that shows whether the pattern adds anything.</li>
        <li><strong>95% range</strong> is where the true edge plausibly lies. Trades of the same stock move together, so it is computed by resampling whole stocks. If the range includes zero, there is no measurable edge.</li>
        <li><strong>Best move (MFE)</strong> and <strong>worst move (MAE)</strong> are how far a trade went in your favour and against you before it closed, in R. A large best move with poor results points at the exits. A small best move points at the entries.</li>
        <li>Groups with fewer than 30 trades are labelled too small. Many groups are tested, so a few will look good by luck alone. The scorecard at the bottom counts how many.</li>
      </ul>
    </details>

    <div>
      <h4 style={{ color: C.head, fontWeight: 600, margin: '0 0 6px' }}>Live rules, all data</h4>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        <Stat label="Trades" value={String(o.trades ?? 0)} note={`${o.symbols ?? 0} stocks`} />
        <Stat label="Win rate" value={pct(o.win_rate)} note="trades closed above 0R" />
        <Stat label="Avg R" value={r(o.mean_r)} note={`after costs (about ${r(o.mean_cost_r)}R each)`} />
        <Stat label="Edge vs random" value={r(o.edge_vs_control_r)} note={`95% range ${ci(o.edge_ci95_by_symbol)}`} />
        <Stat label="Best move (MFE)" value={r(o.mean_mfe_r, 2)} note={`${pct(o.reached_1r_rate)} reached +1R`} />
        <Stat label="Worst move (MAE)" value={r(o.mean_mae_r == null ? null : -o.mean_mae_r, 2)} note={`${pct(o.target_exit_rate)} exited at target`} />
      </div>
      {o.exits && <p style={{ color: C.faint, fontSize: 12, margin: '6px 0 0' }}>
        How trades ended: {Object.entries(o.exits).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(' · ')}. Average holding {o.avg_holding_bars?.toFixed(1) ?? '—'} bars. Largest losing streak in cumulative R: {r(o.max_drawdown_r == null ? null : -o.max_drawdown_r, 2)}.
      </p>}
    </div>

    <div>
      <h4 style={{ color: C.head, fontWeight: 600, margin: '0 0 6px' }}>Predeclared questions</h4>
      <p style={{ color: C.dim, fontSize: 13, margin: '0 0 6px', maxWidth: 860 }}>Written down before the test saw any data, so the answers could not be chosen after the fact. H3 is the main question; the others are supporting.</p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
          <thead><tr><th style={th}>ID</th><th style={th}>Question</th><th style={th}>Trades</th><th style={th}>Edge (R)</th><th style={th}>95% range</th><th style={th}>Verdict</th></tr></thead>
          <tbody>{s.hypotheses.map(h => <HypothesisRow key={h.id} h={h} />)}</tbody>
        </table>
      </div>
    </div>

    <SliceTable title="Seen vs unseen sessions" intro="If the pattern were real, the unseen sessions should look like the rest." rows={slices.by_period} />
    <SliceTable title="By timeframe" rows={slices.by_timeframe} />
    <SliceTable title="Agreement with the daily trend (intraday trades)" intro="The daily trend as it stood at the signal: yesterday’s daily bar for an intraday trade, never today’s unfinished one." rows={slices.by_htf_daily} />
    <SliceTable title="Agreement with the next higher timeframe" intro="5-minute trades against the 15-minute trend, 15-minute against 1-hour, 1-hour against daily." rows={slices.by_htf_next} />
    <SliceTable title="Agreement across all timeframes" intro="How many of the stock’s other three timeframes were trending the same way at the signal." rows={slices.by_confluence} />
    <SliceTable title="Market backdrop" intro="Whether SPY’s last daily close was above or below its 50-day average." rows={slices.by_market_regime} />
    <SliceTable title="Time of day (New York)" rows={slices.by_time_of_day} />
    <SliceTable title="Volatility" intro="Average true range as a share of price at the signal, split into thirds within each timeframe." rows={slices.by_volatility} />
    <SliceTable title="Direction" rows={slices.by_direction} />
    <SliceTable title="Pullback number in the trend" intro="1 and 2 are the rules’ preferred pullbacks; unknown means the trend began before the data did." rows={slices.by_sequence} />
    <details style={{ marginTop: 8 }}>
      <summary style={{ cursor: 'pointer', color: C.head }}>By month and timeframe</summary>
      <SliceTable title="Month" intro="Read results month by month: one good or bad month can dominate a pooled total." rows={slices.by_month} />
    </details>

    <div>
      <h4 style={{ color: C.head, fontWeight: 600, margin: '0 0 6px' }}>Rule variations</h4>
      <p style={{ color: C.dim, fontSize: 13, margin: '0 0 6px', maxWidth: 860 }}>The same test with one rule changed. None was tuned or picked; all are shown.</p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
          <thead><tr><th style={th}>Variant</th><th style={th}>What changes</th><th style={th}>Trades</th><th style={th}>Avg R</th><th style={th}>Edge vs random</th><th style={th}>95% range</th><th style={th}>Verdict</th></tr></thead>
          <tbody>{s.variants.map(v => <tr key={v.name}>
            <td style={td}>{v.name}</td><td style={td}>{v.note}</td><td style={num}>{v.overall.trades ?? 0}</td><td style={num}>{r(v.overall.mean_r)}</td>
            <td style={num}>{r(v.overall.edge_vs_control_r)}</td><td style={num}>{ci(v.overall.edge_ci95_by_symbol)}</td><td style={td}><Verdict v={v.overall.verdict} /></td></tr>)}</tbody>
        </table>
      </div>
    </div>

    <div style={box}>
      <h4 style={{ color: C.head, fontWeight: 600, margin: '0 0 4px' }}>Scorecard: luck check</h4>
      <p style={{ margin: 0, fontSize: 13 }}>
        {s.scorecard.cells_tested} groups with at least 30 trades were tested. {s.scorecard.better} looked better than random timing and {s.scorecard.worse} looked worse.
        By chance alone, about {s.scorecard.expected_by_chance_each_side} of each would be expected. {s.scorecard.reading}
      </p>
      {s.scorecard.better_cells.length > 0 && <p style={{ margin: '6px 0 0', fontSize: 12, color: C.faint }}>
        Better: {s.scorecard.better_cells.map(c => `${c.slice} ${label(c.cell)} (${c.trades})`).join(' · ')}</p>}
    </div>

    <div style={{ fontSize: 12, color: C.faint }}>
      <p style={{ margin: '0 0 4px' }}>Data: {Object.entries(cov).map(([tf, c]) => `${label(tf)} ${c.symbols} stocks, ${day(c.first)}–${day(c.last)}`).join(' · ')}.</p>
      <p style={{ margin: '0 0 4px' }}>Assumed for research: every signal could be filled at the next bar’s open with modelled spread, slippage and commission; shorts pay 0.5% a year to borrow. Delisted stocks are missing. Results describe the past under these assumptions; they are not a forecast.</p>
      <p style={{ margin: '0 0 4px' }}>Run {latest.run_id.slice(0, 12)} · code {latest.git_revision.slice(0, 8)}{latest.git_dirty ? ' (uncommitted changes)' : ''} · {s.analysis_version}{latest.current_config ? '' : ' · ran on an older rule configuration'}.</p>
      <p style={{ margin: 0 }}>{data.forward_journal.note}</p>
    </div>

    {data.runs.length > 1 && <details>
      <summary style={{ cursor: 'pointer', color: C.head }}>Earlier runs</summary>
      <table style={{ borderCollapse: 'collapse', fontSize: 13, marginTop: 6 }}>
        <thead><tr><th style={th}>Data exported</th><th style={th}>Analysis</th><th style={th}>Trades</th><th style={th}>Edge</th><th style={th}>Verdict</th></tr></thead>
        <tbody>{data.runs.map(x => <tr key={x.run_id}><td style={td}>{day(x.snapshot_exported_at)}</td><td style={td}>{x.analysis_version}</td>
          <td style={num}>{x.trades ?? '—'}</td><td style={num}>{r(x.edge_vs_control_r)}</td><td style={td}><Verdict v={x.verdict} /></td></tr>)}</tbody>
      </table>
    </details>}
  </div>;
}

export default function LittleRzyEvaluation({ data, error }: { data?: RzyEvaluationResponse; error?: unknown }) {
  return <details open style={{ border: `1px solid ${C.line}`, borderRadius: 8, padding: 16, color: C.text, fontSize: 14 }}>
    <summary style={{ cursor: 'pointer', fontSize: 18, fontWeight: 600, color: C.head }}>How good is Little RZY? Test results</summary>
    <div style={{ marginTop: 12 }}>
      {error ? <p role="alert" style={{ color: C.bad }}>Test results unavailable: {String((error as Error)?.message ?? error)}</p>
        : !data ? <p style={{ color: C.dim }}>Loading test results…</p>
        : !data.latest ? <p style={{ color: C.dim }}>No evaluation has been published yet. Runs are produced with scripts/evaluate_little_rzy.py (export, evaluate, publish).</p>
        : <Results s={data.latest.summary} data={data} />}
    </div>
  </details>;
}
