/**
 * Learning — /learn. Platform guide teaching how to use StockAI's features together, with
 * worked examples. Follows reports.tsx's tab-array + per-tab-component structure and
 * watchlist-rotation-explainer.tsx's Section/Callout/Code component conventions, rather than
 * inventing new layout patterns.
 *
 * Content is adapted from — not duplicated from scratch — the authoritative design references
 * already written in .claude/CLAUDE.md for each feature (Volume Profile, Fair Value Gaps,
 * Swing Pivots, the AI Signal confidence/confluence/conviction-gate distinction, etc.). This
 * page exists because that documentation lives in a file the user (not just future Claude
 * sessions) should be able to read inside the app itself, with the chart screenshots' worth of
 * detail turned into prose + worked examples a trader can act on.
 */
import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import { getSession } from '@/lib/auth';

type Tab = 'start' | 'charttools' | 'signal' | 'reports' | 'selftuning';

const TABS: { key: Tab; label: string }[] = [
  { key: 'start',      label: 'Getting Started' },
  { key: 'charttools', label: 'Chart Tools' },
  { key: 'signal',     label: 'AI Signal & Confluence' },
  { key: 'reports',    label: 'Reports & Market Intel' },
  { key: 'selftuning', label: 'Self-Tuning System' },
];

function tabFromQuery(q: unknown): Tab {
  const valid: Tab[] = ['start', 'charttools', 'signal', 'reports', 'selftuning'];
  return valid.includes(q as Tab) ? (q as Tab) : 'start';
}

// ── Shared components (matches watchlist-rotation-explainer.tsx's conventions) ──────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 32 }}>
      <h2 style={{ fontSize: 16, fontWeight: 800, color: '#e2e8f0', marginBottom: 10 }}>{title}</h2>
      <div style={{ fontSize: 13.5, lineHeight: 1.75, color: '#94a3b8' }}>{children}</div>
    </div>
  );
}

function SubSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 22 }}>
      <h3 style={{ fontSize: 13.5, fontWeight: 700, color: '#cbd5e1', marginBottom: 8 }}>{title}</h3>
      <div style={{ fontSize: 13.5, lineHeight: 1.75, color: '#94a3b8' }}>{children}</div>
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
    <div style={{ padding: '14px 16px', borderRadius: 10, background: colors.bg, border: `1px solid ${colors.border}`, marginBottom: 16 }}>
      <div style={{ fontSize: 11, fontWeight: 800, color: colors.text, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>
        {title}
      </div>
      <div style={{ fontSize: 12.5, color: '#cbd5e1', lineHeight: 1.7 }}>{children}</div>
    </div>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code style={{ background: '#0d1424', border: '1px solid #1e293b', borderRadius: 4, padding: '1px 6px', fontSize: 12, color: '#f59e0b', fontFamily: 'monospace' }}>
      {children}
    </code>
  );
}

function StepList({ steps }: { steps: string[] }) {
  return (
    <ol style={{ margin: '8px 0 0', paddingLeft: 20 }}>
      {steps.map((s, i) => (
        <li key={i} style={{ marginBottom: 8 }} dangerouslySetInnerHTML={{ __html: s }} />
      ))}
    </ol>
  );
}

// ── Tab: Getting Started ─────────────────────────────────────────────────────────────────────

function GettingStartedTab() {
  return (
    <div style={{ maxWidth: 780 }}>
      <Section title="What this platform actually does">
        <p>
          StockAI ingests price/fundamentals/news data across US and HK markets, computes a K-Score
          (fundamental + technical ranking) and an AI Signal (BUY/SELL/HOLD, per trading style) for
          every stock, and layers chart-analysis tools, research reports, and a live paper-trading
          engine on top. Nothing here places real trades automatically — paper trading simulates
          entries/exits so the system's own decisions can be measured against real outcomes over
          time, which is also what powers the self-tuning mechanisms (see the Self-Tuning tab).
        </p>
      </Section>

      <Section title="The four trading styles">
        <p>
          Every signal, ranking, and watchlist is scoped to one of four trading styles — they are
          NOT the same strategy at different risk levels, they use genuinely different thresholds,
          hold periods, and even different technical weightings:
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10, marginTop: 10 }}>
          {[
            { name: 'SHORT', hold: '~10 days', note: 'tight RSI band, fast reactive' },
            { name: 'SWING', hold: '~20 days', note: 'strictest confidence gate, most-used' },
            { name: 'LONG', hold: '~90 days', note: 'looser thresholds, fundamentals-weighted' },
            { name: 'GROWTH', hold: '~60 days', note: 'relaxed thresholds, catches high-momentum names' },
          ].map(s => (
            <div key={s.name} style={{ padding: 12, borderRadius: 8, background: '#111827', border: '1px solid #1f2937' }}>
              <div style={{ fontSize: 13, fontWeight: 800, color: '#e2e8f0' }}>{s.name}</div>
              <div style={{ fontSize: 11.5, color: '#64748b', marginTop: 2 }}>hold: {s.hold}</div>
              <div style={{ fontSize: 11.5, color: '#94a3b8', marginTop: 4 }}>{s.note}</div>
            </div>
          ))}
        </div>
        <p style={{ marginTop: 12 }}>
          Pick the style that matches how long you actually intend to hold a position — a SHORT
          signal firing on a stock you plan to hold for months is answering a different question
          than you're asking.
        </p>
      </Section>

      <Section title="A recommended first workflow">
        <StepList
          steps={[
            'Open <b>Rankings</b> or <b>Reports → Top Stocks</b> to see the current K-Score leaderboard for your market.',
            'Click into a stock’s detail page. Check the <b>AI Signal</b> badge — but don’t stop there (see the AI Signal & Confluence tab for why the headline label alone isn’t enough).',
            'Look at the <b>Confluence Score</b> and <b>Conviction Gate</b> panels on the same page — these are deliberately stricter, independent checks.',
            'Turn on <b>Swing Pivots</b> in the chart’s Indicators dropdown, then use <b>Fixed Range VP</b> to profile the most recent real swing move (see the Chart Tools tab for the full worked example).',
            'If a <b>Fair Value Gap</b> exists between price and a recent swing point, check its Trade Plan card — entry/stop/target computed automatically from the gap’s own geometry.',
            'Before sizing a real position, run it through <b>Position Sizer</b> and compare against the FVG Trade Plan’s own numbers — they’re independent systems that can (and are meant to) disagree.',
          ]}
        />
      </Section>

      <Callout tone="warn" title="Standing disclaimer">
        Every signal, score, and report in this app is a measured, historical read — not a
        prediction of what a stock will do next. Confidence percentages measure distance from a
        50/50 coin-flip, not the odds of the trade working. Nothing here is financial advice, and
        every conviction/gate check exists specifically because the headline label alone is not
        sufficient reason to enter a real position.
      </Callout>
    </div>
  );
}

// ── Tab: Chart Tools (Swing Pivots + Fixed Range VP + FVG) ───────────────────────────────────

function ChartToolsTab() {
  return (
    <div style={{ maxWidth: 780 }}>
      <Section title="Three tools, one combined workflow">
        <p>
          The chart has three structural-analysis tools that are designed to be used <i>together</i>,
          not in isolation. Each answers a different question — combining them turns &ldquo;eyeball
          the chart and guess where support is&rdquo; into a precise, structure-anchored read.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10, marginTop: 12 }}>
          <div style={{ padding: 12, borderRadius: 8, background: '#111827', border: '1px solid #1f2937' }}>
            <div style={{ fontSize: 12.5, fontWeight: 800, color: '#38bdf8' }}>Swing Pivots</div>
            <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>Where are the real swing highs/lows?</div>
          </div>
          <div style={{ padding: 12, borderRadius: 8, background: '#111827', border: '1px solid #1f2937' }}>
            <div style={{ fontSize: 12.5, fontWeight: 800, color: '#a78bfa' }}>Fixed Range VP</div>
            <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>How did volume actually distribute across that move?</div>
          </div>
          <div style={{ padding: 12, borderRadius: 8, background: '#111827', border: '1px solid #1f2937' }}>
            <div style={{ fontSize: 12.5, fontWeight: 800, color: '#facc15' }}>Fair Value Gaps</div>
            <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>Where did price move too fast to trade fairly?</div>
          </div>
        </div>
      </Section>

      <SubSection title="1. Swing Pivots — finding the real anchor points">
        <p>
          A swing high is a bar whose high is the highest point within a window of nearby bars on
          both sides (default: 5 bars each side) — a real local top, not just &ldquo;a candle that
          went up.&rdquo; A swing low is the mirror. These are exactly the points a discretionary
          trader means when they say &ldquo;draw your trendline from swing low to swing low.&rdquo;
        </p>
        <p>
          Toggle <Code>Swing Pivots</Code> in the chart&rsquo;s Indicators dropdown to see small dot
          markers at every detected pivot. Works on both daily and intraday timeframes.
        </p>
      </SubSection>

      <SubSection title="2. Fixed Range VP — profiling a specific move">
        <p>
          Volume Profile answers &ldquo;where did volume concentrate?&rdquo; — but <b>Session VP</b>
          and <b>Range VP</b> profile a fixed calendar window, which tells you very little unless
          that window happens to line up with a real move. <b>Fixed Range VP</b> is anchored to
          structure instead: click a start point, click an end point, and it computes the volume
          profile using ONLY the bars between those two points.
        </p>
        <p>
          Reads it out as:
        </p>
        <ul style={{ paddingLeft: 20, margin: '4px 0 12px' }}>
          <li><b>POC (Point of Control)</b> — the single price level with the most volume traded in that range. Acts like a magnet — price often gets pulled back toward it.</li>
          <li><b>VAH / VAL (Value Area High/Low)</b> — bracket the price range containing 70% of total volume. Price outside this band sat in comparatively thin, under-traded territory.</li>
          <li><b>HVN (High Volume Nodes)</b> — other local volume peaks besides POC. Tend to act as support/resistance on a revisit.</li>
        </ul>
        <Callout tone="info" title="The snap-to-pivot shortcut">
          Since Fixed Range VP&rsquo;s whole value depends on picking a <i>meaningful</i> start/end
          pair, turn on Swing Pivots first, then click near (not exactly on) each dot when picking
          your Fixed Range VP range — every click automatically snaps to the nearest real pivot
          within 3 bars. You don&rsquo;t need pixel-perfect clicks anymore.
        </Callout>
      </SubSection>

      <SubSection title="3. Fair Value Gaps — untraded price zones">
        <p>
          A Fair Value Gap is a standard 3-candle pattern: if bar 1&rsquo;s high is below bar 3&rsquo;s
          low (or the mirror, for a bearish gap), there&rsquo;s a real price zone between them that
          NO candle actually traded through. The market moved through it too fast for genuine
          two-sided trading — considered &ldquo;unfair,&rdquo; and price frequently comes back to
          &ldquo;rebalance&rdquo; into that zone before continuing.
        </p>
        <p>
          Toggle <Code>Fair Value Gaps</Code> in the Indicators dropdown (off by default). Unfilled
          gaps show as bold colored lines with a direction arrow; once a later candle fully trades
          through a gap, it dims to a thin dotted line — it already did its job as support/resistance
          and is no longer an actionable target for a NEW entry.
        </p>
        <p>
          The <b>Fair Value Gap Trade Plan</b> card (below Position Sizer on the stock detail page)
          automatically picks the single most relevant unfilled gap right now — the nearest one
          positioned so price still has room to retrace into it — and computes:
        </p>
        <ul style={{ paddingLeft: 20, margin: '4px 0 0' }}>
          <li><b>Entry</b> = the gap&rsquo;s midpoint (not its exact edge — more realistic fill assumption)</li>
          <li><b>Stop</b> = just past the gap&rsquo;s far edge (if price fully closes the gap and keeps going, the thesis has failed)</li>
          <li><b>Target</b> = a 1.5:1 reward:risk floor by default, scaled off the gap&rsquo;s own real size</li>
        </ul>
      </SubSection>

      <Section title="Worked example: combining all three">
        <Callout tone="example" title="Scenario">
          A stock ran from a swing low at $80 to a swing high at $110, then pulled back to $95.
        </Callout>
        <StepList
          steps={[
            'Turn on <b>Swing Pivots</b>. Confirm the $80 low and $110 high are both real detected pivots (dots), not just visually-eyeballed points.',
            'Use <b>Fixed Range VP</b>, snap-clicking near each of those two dots, to profile the entire $80→$110 rally specifically.',
            'Read where <b>POC/HVN</b> landed. If they cluster near $95–98 — almost exactly where the pullback has landed — that is a materially stronger signal than "price is near a round number." It means the market spent the MOST volume agreeing that price was fair right where the pullback now sits, during the exact move you care about.',
            'If instead the pullback has landed in a thin, low-volume gap of that same profile (an LVN region, or clearly below VAL), that is a weaker-conviction level — the market didn’t spend much time there last time, so it is more likely to be sliced through than held.',
            'Now check for a <b>Fair Value Gap</b> in the same $80–$110 move. If one exists near $95–98 too — i.e. the FVG Trade Plan card’s entry/stop roughly agree with where POC/HVN sit — two independent structural reads are confirming the same level, which is a stronger setup than either alone.',
          ]}
        />
        <Callout tone="good" title="What this means in practice">
          A pullback that lands on real POC/HVN volume AND inside a Fair Value Gap is a
          higher-quality long setup than a random pullback: it says the market previously agreed
          this price was &ldquo;fair,&rdquo; AND there&rsquo;s a specific untraded zone still pulling
          price back toward it. A pullback landing in thin/LVN territory with no FVG nearby is a
          weaker setup — more likely to fully retrace or break down, even if the chart &ldquo;looks&rdquo;
          similar at a glance.
        </Callout>
      </Section>

      <Section title="How to actually trade a breakout/breakdown vs. POC/HVN/LVN">
        <ul style={{ paddingLeft: 20, margin: 0 }}>
          <li style={{ marginBottom: 8 }}><b>Breakout above VAH</b> — price left the &ldquo;accepted&rdquo; range into thin territory above it. Read as bullish continuation, especially if price holds above VAH on a retest (old resistance flipping to new support is the confirming signal, not the initial break).</li>
          <li style={{ marginBottom: 8 }}><b>Breakdown below VAL</b> — the bearish mirror. Commonly used as an exit/reduce-position trigger.</li>
          <li style={{ marginBottom: 8 }}><b>Failed breakout (poke-and-reject)</b> — if price pokes above VAH/below VAL and then closes back inside, that&rsquo;s often a false breakout — treat it as the OPPOSITE signal from a genuine breakout, not a weaker version of the same one.</li>
          <li style={{ marginBottom: 8 }}><b>HVN vs LVN as a roadmap</b> — HVNs (thick bars) act like speed bumps: price tends to slow, consolidate, or reverse there. LVNs (thin bars/gaps) are zones the market moved through fast the first time — expect a quick move back through them too on a revisit.</li>
        </ul>
      </Section>

      <Section title="Volume Pattern Read: Accumulation/Distribution + Breakout Quality">
        <p>
          The &ldquo;poke-and-reject = false breakout&rdquo; read above used to be a manual chart
          judgment call. The <b>Volume Pattern Read</b> card (below the Fair Value Gap Trade Plan
          on the stock detail page) now computes two related reads directly from price/volume
          data, so you don&rsquo;t have to eyeball it:
        </p>
        <ul style={{ paddingLeft: 20, margin: '4px 0 12px' }}>
          <li style={{ marginBottom: 8 }}><b>Accumulation / Distribution</b> — combines OBV trend (net buying vs. selling pressure, cumulative) with the ratio of volume on up-days vs. down-days over the last 20 bars. Both signals must agree for a real call; if they disagree, it reads &ldquo;neutral&rdquo; rather than guessing.</li>
          <li style={{ marginBottom: 8 }}><b>Breakout Quality</b> — finds the actual bar that broke a support/resistance level and classifies it: <b>real</b> (the next bar held beyond the level AND the break itself had above-average volume), <b>failed</b> (the very next bar reversed back across the level — the poke-and-reject case), or <b>unconfirmed</b> (the break just happened, so there&rsquo;s no next bar yet to confirm — or it held but without volume backing it).</li>
        </ul>
        <Callout tone="warn" title="A pattern read, not confirmed institutional flow">
          No block-trade or dark-pool data source exists anywhere in this app — both reads are
          derived purely from ordinary daily-bar price and volume, the same data everything else
          on this page uses. Treat &ldquo;accumulation&rdquo; as &ldquo;the volume pattern looks
          like buying pressure,&rdquo; not as proof a specific institution is actually buying.
        </Callout>
      </Section>

      <Section title="Anchored VWAP and Auto-Detected Trendlines">
        <p>
          <b>Anchored VWAP</b> — click any point on the chart (snaps to the nearest swing pivot) and
          VWAP recalculates forward from that exact bar. The standard use: anchor to the day you
          would have entered, then check whether price is still holding above it — a trend-continuation
          confirmation.
        </p>
        <p>
          <b>Trendlines</b> — draw manually (2 clicks). Persist per-symbol across sessions and
          correctly re-anchor by timestamp if you switch timeframes, so a trendline drawn on the
          daily chart won&rsquo;t silently jump to the wrong bar if you switch to 1h and back.
        </p>
      </Section>
    </div>
  );
}

// ── Tab: AI Signal & Confluence ──────────────────────────────────────────────────────────────

function SignalTab() {
  return (
    <div style={{ maxWidth: 780 }}>
      <Section title="Why a BUY signal can show low confidence">
        <p>
          Confidence and the BUY/SELL/HOLD decision are two <b>entirely independent</b> calculations
          — this is the single most common source of confusion on the stock detail page.
        </p>
        <p>
          <b>Confidence</b> = <Code>abs(fused_probability - 0.5) * 200</Code> — purely &ldquo;how far
          from a 50/50 coin-flip is the model&rsquo;s probability.&rdquo; A fused probability of 56%
          bullish is barely above a toss-up, so confidence is mechanically forced to just 12% no
          matter what else is true about the stock. Confidence measures conviction in the
          probability estimate itself — not trade quality.
        </p>
        <p>
          <b>BUY/SELL/HOLD</b> is decided separately by whether that same probability clears a
          per-style, per-regime <b>threshold</b> that can itself be self-tuned over time. A BUY
          signal with low confidence means the probability barely cleared the bar to be called BUY
          at all — a marginal, low-conviction call, not a strong one.
        </p>
      </Section>

      <Section title="What confidence level is actually &ldquo;good&rdquo;?">
        <p>
          There is no single hard cutoff — confidence is a continuous read of how far the
          probability sits from a coin-flip, not a pass/fail score. As a rough guide for reading
          the number itself (independent of the panels below, which matter more):
        </p>
        <ul style={{ paddingLeft: 20, margin: 0 }}>
          <li style={{ marginBottom: 8 }}><b>Below ~40%</b> — marginal. The probability barely cleared the BUY/SELL threshold; treat the label as a weak lean, not a real call.</li>
          <li style={{ marginBottom: 8 }}><b>~40–60%</b> — moderate. A reasonably confident directional read, but still worth corroborating before sizing a position.</li>
          <li style={{ marginBottom: 8 }}><b>Above ~60%</b> — high conviction. The fused probability is well clear of a toss-up in either direction.</li>
        </ul>
        <Callout tone="warn" title="Confidence alone is not enough">
          A high-confidence BUY can still fail the Conviction Gate (see below), and a
          low-confidence BUY that clears every other check can still be a reasonable, if smaller,
          position. Confidence tells you how sure the MODEL is about its own probability estimate
          — it does not by itself tell you whether the trade is a good idea. Always read it
          alongside Confluence Score and Conviction Gate, never on its own.
        </Callout>
        <p>
          A more honest read than the raw confidence number is the <b>measured historical win
          rate</b> shown alongside it on stock pages (e.g. &ldquo;Historical win rate 72%,
          n=41&rdquo;). This is tracked separately, per horizon/direction/market and confidence
          band, from real past signal outcomes — not a model&rsquo;s self-reported confidence, but
          what actually happened the last N times a signal in that same band fired. A confidence
          number tells you how sure the model is; a measured win rate tells you how often that
          exact kind of call has actually been right.
        </p>
      </Section>

      <Section title="The panels that matter more than the headline label">
        <p>
          This is exactly what the other panels on the stock detail page are for — they&rsquo;re
          deliberately more reliable signals of &ldquo;should I actually enter&rdquo; than the
          top-line BUY/SELL label alone:
        </p>
        <ul style={{ paddingLeft: 20, margin: 0 }}>
          <li style={{ marginBottom: 8 }}><b>Confluence Score</b> — a weighted blend of AI signal + K-Score + technical + momentum. A low/&ldquo;Weak&rdquo; score with &ldquo;signals conflict&rdquo; is a stronger real-world signal to heed than the BUY label.</li>
          <li style={{ marginBottom: 8 }}><b>Conviction Gate</b> — a 7-layer check (K-Score, Uptrend, RSI, MACD, OBV, ADX, ML). &ldquo;✗ Gate not met&rdquo; with multiple failed layers means the paper trading engine itself would NOT have entered this position even though the top-line label says BUY.</li>
        </ul>
        <Callout tone="warn" title="Design invariant">
          Never treat the top-line AI Signal label as sufficient justification to enter a real
          position on its own — always cross-check Confluence Score and Conviction Gate, which are
          deliberately independent, stricter checks that can (and are meant to) disagree with the
          headline label.
        </Callout>
      </Section>

      <Section title="Why a signal-change email can arrive outside market hours">
        <p>
          Signal alerts are checked five times a day, all inside real US market hours (roughly
          9:25am–4:30pm ET, plus the equivalent HK window) — the check never runs on a fixed
          overnight schedule. If you get an email well outside those hours, the most common
          explanation is that the underlying signal server was <b>restarted</b> (a deploy,
          maintenance, or a crash-and-recover) — the very first thing it does after coming back up
          is a one-time catch-up check, specifically so a real signal change that happened earlier
          in the day doesn&rsquo;t go unreported just because the container was down when it would
          normally have been caught.
        </p>
        <Callout tone="info" title="The signal itself is still real">
          A late-arriving alert reflects a signal change that genuinely happened during real
          trading hours — only the TIMING of the email notification is delayed by the restart, not
          the underlying signal computation. It is not a sign the system is checking prices while
          markets are closed.
        </Callout>
      </Section>

      <Section title="The ↑/↓ percentage arrows on the daily chart">
        <p>
          Small green ↑ and red ↓ arrows above/below certain candles mark <b>AI Signal transition
          points</b> — daily timeframe only. Every day the signal just held its existing direction
          is skipped; only the day it flipped gets a marker. The percentage label is that stored
          signal&rsquo;s own confidence <i>at the moment it flipped</i> — a frozen historical value,
          NOT today&rsquo;s live confidence (shown separately in the sidebar).
        </p>
        <p>
          A cluster of low-confidence flip markers close together often reflects a choppy period
          where the signal was oscillating near its decision threshold, not a series of strong
          directional calls.
        </p>
      </Section>

      <Section title="Position Sizer vs. FVG Trade Plan — two independent reads">
        <p>
          Position Sizer computes entry/stop/target from ATR, nearest support, and analyst target
          price. The Fair Value Gap Trade Plan (see Chart Tools tab) computes its own entry/stop/target
          purely from the gap&rsquo;s geometry. These are deliberately NOT merged into one system —
          compare both before sizing a real position rather than trusting either alone.
        </p>
      </Section>
    </div>
  );
}

// ── Tab: Reports & Market Intel ───────────────────────────────────────────────────────────────

function ReportsTab() {
  return (
    <div style={{ maxWidth: 780 }}>
      <Section title="Reports — one page, seven angles on the market">
        <p>
          <a href="/reports" style={{ color: '#38bdf8', textDecoration: 'none' }}>Reports</a> aggregates
          per-market (US/HK) context across seven tabs: Market Trend (regime + fear/greed + breadth),
          Key Assets (major indices/ETFs), Top Stocks (K-Score leaderboard), Money Flow (sector
          rotation + insider/congress activity), News &amp; Macro (macro calendar + reactions),
          CAPE / Bubble Warning (long-run valuation context), and Self-Tuning (a live view into
          every calibration mechanism — see the Self-Tuning tab for what these numbers mean).
        </p>
      </Section>

      <Section title="CAPE / Bubble Warning — a slow signal, not a trade trigger">
        <p>
          CAPE (Shiller cyclically-adjusted P/E) is a macro valuation indicator for the S&amp;P 500.
          Historically elevated readings have preceded major corrections — but CAPE can stay
          &ldquo;elevated&rdquo; or &ldquo;extreme&rdquo; for years before any correction actually
          happens. Treat this as macro context for position sizing/risk appetite, never as a signal
          to time an individual trade.
        </p>
      </Section>

      <Section title="Event Intelligence — insider, congress, and macro reactions">
        <p>
          <a href="/intelligence" style={{ color: '#38bdf8', textDecoration: 'none' }}>Event Intelligence</a>{' '}
          tracks insider trading, congressional stock trades, and the market&rsquo;s post-release
          reaction to CPI/PPI/GDP/NFP/FOMC events. &ldquo;Top Buys&rdquo; leaderboards are already
          filtered to genuine net buyers — a stock net-selling can never appear there.
        </p>
      </Section>
    </div>
  );
}

// ── Tab: Self-Tuning System ───────────────────────────────────────────────────────────────────

function SelfTuningTab() {
  return (
    <div style={{ maxWidth: 780 }}>
      <Section title="The system tunes itself, continuously — but conservatively">
        <p>
          Several independent mechanisms watch real trading outcomes and adjust the app&rsquo;s own
          parameters over time — thresholds, scoring weights, position-sizing gates. Every one of
          them follows the same core discipline: a chronological (never random) train/validation
          split, a minimum sample floor before acting at all, and an unconditional rejection of any
          change that doesn&rsquo;t measurably beat the CURRENT live baseline on real held-out data.
        </p>
      </Section>

      <Section title="The daily watchdog — a fast, reactive nudge">
        <p>
          <Code>signal_watchdog</Code> runs daily and watches each trading style&rsquo;s rolling
          14-day win rate. If it drops below 38%, the watchdog tightens that style&rsquo;s buy
          threshold by +0.03 — same-day correction, not a slow search. If a style goes quiet
          (zero signals for 7+ days), it relaxes the threshold by &minus;0.02 instead. Capped at 3
          tightenings before flagging for manual review, so the system can&rsquo;t silence itself
          completely.
        </p>
        <Callout tone="info" title="Who checks the watchdog itself?">
          A separate, read-only diagnostic (<Code>/signals/watchdog_self_tuning_report</Code>)
          reports whether the watchdog&rsquo;s own past tighten/relax actions actually helped, once
          enough real trading weeks have passed to measure it — closing the loop from &ldquo;we
          predicted this would help&rdquo; to &ldquo;did it actually help,&rdquo; without
          auto-tuning the tuner itself. See <Code>SELFIMPROVE-WATCHDOG-SELF-TUNING</Code> in the
          Improvements tracker (Admin) for the full design writeup.
        </Callout>
      </Section>

      <Section title="Weekly calibration">
        <p>
          Every Sunday, a larger batch of mechanisms re-tune: TA indicator weights, ML ensemble
          weight, conviction-gate weights, style-profile parameters (ADX floors, compression
          filters), and the entry-score gate paper trading uses. Each one only promotes a change if
          it clears its own minimum-sample floor AND beats the current live baseline&rsquo;s
          validation-slice performance — a losing candidate is always rejected outright, regardless
          of how large the proposed change looked.
        </p>
      </Section>

      <Section title="Where to watch it happen">
        <p>
          <a href="/signal-tuning" style={{ color: '#38bdf8', textDecoration: 'none' }}>Signal Tuning</a>{' '}
          (Admin) shows live vs. hardcoded values per style side by side.{' '}
          <a href="/reports?tab=tuning" style={{ color: '#38bdf8', textDecoration: 'none' }}>Reports → Self-Tuning</a>{' '}
          surfaces the same data from the Reports page. Every promoted or rejected tuning attempt is
          recorded — visible via the Improvements tracker&rsquo;s own linked admin pages — so
          &ldquo;we tried X and it didn&rsquo;t help&rdquo; is always auditable, not silently lost.
        </p>
      </Section>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function LearnPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [tab, setTab] = useState<Tab>(() => tabFromQuery(router.query.tab));

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    setAuthed(true);
  }, [router]);

  useEffect(() => {
    if (router.isReady) setTab(tabFromQuery(router.query.tab));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router.isReady, router.query.tab]);

  if (!authed) return null;

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '24px 0 60px' }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: '#e2e8f0', marginBottom: 6 }}>
          Platform Guide
        </h1>
        <p style={{ fontSize: 13, color: '#64748b', maxWidth: 680 }}>
          How to use StockAI&rsquo;s features together, with worked examples — not a feature list,
          a guide to actually reading and combining what the app already shows you.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid #1f2937', marginBottom: 28, flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => { setTab(t.key); router.replace({ pathname: '/learn', query: { tab: t.key } }, undefined, { shallow: true }); }}
            style={{
              background: 'none', border: 'none', cursor: 'pointer', padding: '10px 16px', fontSize: 13, fontWeight: 500,
              color: tab === t.key ? '#f9fafb' : '#6b7280',
              borderBottom: tab === t.key ? '2px solid #6d28d9' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'start' && <GettingStartedTab />}
      {tab === 'charttools' && <ChartToolsTab />}
      {tab === 'signal' && <SignalTab />}
      {tab === 'reports' && <ReportsTab />}
      {tab === 'selftuning' && <SelfTuningTab />}
    </div>
  );
}
