import React from 'react';
import { RZY_STREAM_STATUS } from '@/lib/rzyReasons';

// A plain-language guide for someone new to Little RZY and to intraday charts. Facts come from
// docs/Little RZY Integration.md (spec), docs/2026-10-19/little-rzy-{implementation,parameters}.md,
// docs/2026-10-20/little-rzy-{email-guide,evaluation-rerun}.md and docs/2026-10-19/
// little-rzy-evaluation-results.md. When a rule or a result changes there, change it here too.
//
// globals.css has no Tailwind build and defines only a fixed set of utility classes (no list,
// spacing-y-1..3, amber/sky/rose text, max-width...). Everything beyond those is inline.

const C = {
  text: '#cbd5e1', soft: '#94a3b8', muted: '#64748b', head: '#f1f5f9', edge: '#334155', panel: '#0f172a',
  teal: '#2dd4bf', tealSoft: 'rgba(45,212,191,0.10)', tealEdge: 'rgba(45,212,191,0.40)',
  amber: '#fcd34d', amberSoft: 'rgba(245,158,11,0.10)', amberEdge: 'rgba(245,158,11,0.45)',
  sky: '#7dd3fc', skySoft: 'rgba(14,165,233,0.10)', skyEdge: 'rgba(14,165,233,0.40)',
  rose: '#fda4af', green: '#6ee7b7', bar: '#334155',
};
const MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace';

const s = {
  h3: { fontSize: 15, fontWeight: 600, color: C.head, margin: '20px 0 6px' } as React.CSSProperties,
  h4: { fontSize: 13, fontWeight: 600, color: C.head, margin: '14px 0 4px' } as React.CSSProperties,
  p: { margin: '6px 0', lineHeight: 1.65, maxWidth: 880 } as React.CSSProperties,
  ul: { listStyle: 'disc', paddingLeft: 22, margin: '6px 0', maxWidth: 880 } as React.CSSProperties,
  ol: { listStyle: 'decimal', paddingLeft: 22, margin: '6px 0', maxWidth: 880 } as React.CSSProperties,
  li: { margin: '5px 0', lineHeight: 1.6 } as React.CSSProperties,
  b: { color: C.head, fontWeight: 600 } as React.CSSProperties,
  code: { fontFamily: MONO, fontSize: 12, color: C.head, background: '#1e293b', borderRadius: 4, padding: '1px 5px' } as React.CSSProperties,
  th: { padding: '6px 8px', textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.04em', color: C.soft, borderBottom: `1px solid ${C.edge}` } as React.CSSProperties,
  td: { padding: '6px 8px', verticalAlign: 'top', borderBottom: '1px solid #1e293b', lineHeight: 1.5 } as React.CSSProperties,
  section: { border: `1px solid ${C.edge}`, borderRadius: 10, background: C.panel, padding: '10px 14px', marginTop: 10 } as React.CSSProperties,
  summary: { cursor: 'pointer', fontWeight: 600, color: C.teal, fontSize: 14, padding: '2px 0' } as React.CSSProperties,
  svg: { display: 'block', width: '100%', minWidth: 520, maxWidth: 720, height: 'auto' } as React.CSSProperties,
};

const B = ({ children }: { children: React.ReactNode }) => <b style={s.b}>{children}</b>;
const Code = ({ children }: { children: React.ReactNode }) => <code style={s.code}>{children}</code>;
const P = ({ children }: { children: React.ReactNode }) => <p style={s.p}>{children}</p>;
const UL = ({ children }: { children: React.ReactNode }) => <ul style={s.ul}>{children}</ul>;
const OL = ({ children }: { children: React.ReactNode }) => <ol style={s.ol}>{children}</ol>;
const LI = ({ children }: { children: React.ReactNode }) => <li style={s.li}>{children}</li>;

function Callout({ tone, title, children }: { tone: 'teal' | 'amber' | 'sky'; title: string; children: React.ReactNode }) {
  const t = { teal: [C.tealSoft, C.tealEdge, C.teal], amber: [C.amberSoft, C.amberEdge, C.amber], sky: [C.skySoft, C.skyEdge, C.sky] }[tone];
  return <div style={{ background: t[0], border: `1px solid ${t[1]}`, borderRadius: 8, padding: '10px 12px', margin: '10px 0', maxWidth: 880 }}>
    <div style={{ color: t[2], fontWeight: 600, marginBottom: 4 }}>{title}</div>
    <div style={{ color: C.text, lineHeight: 1.6 }}>{children}</div>
  </div>;
}

function Part({ id, title, open, children }: { id: string; title: string; open?: boolean; children: React.ReactNode }) {
  return <details id={id} open={open} style={s.section}>
    <summary style={s.summary}>{title}</summary>
    <div style={{ marginTop: 8, color: C.text, fontSize: 14 }}>{children}</div>
  </details>;
}

function Table({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return <div style={{ overflowX: 'auto', margin: '8px 0' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
    <thead><tr>{head.map(h => <th key={h} style={s.th}>{h}</th>)}</tr></thead>
    <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, k) => <td key={k} style={{ ...s.td, color: k === 0 ? C.head : C.text }}>{c}</td>)}</tr>)}</tbody>
  </table></div>;
}

// One rising and one falling candle with open/high/low/close labelled.
function CandleDiagram() {
  const candle = (x: number, up: boolean) => {
    const [o, c] = up ? [130, 70] : [70, 130];
    const col = up ? C.teal : '#64748b';
    const lbl = (y: number, t: string) => <text x={x + 34} y={y + 4} style={{ fontSize: 11, fill: C.text }}>{t}</text>;
    return <g>
      <line x1={x} x2={x} y1={40} y2={160} stroke={col} strokeWidth={2} />
      <rect x={x - 14} y={Math.min(o, c)} width={28} height={Math.abs(c - o)} fill={col} rx={2} />
      {[[40, 'high'], [up ? 70 : 130, 'close'], [up ? 130 : 70, 'open'], [160, 'low']].map(([y, t]) => <g key={String(t)}>
        <line x1={x + 16} x2={x + 30} y1={Number(y)} y2={Number(y)} stroke={C.muted} strokeDasharray="2 2" />{lbl(Number(y), String(t))}</g>)}
      <text x={x} y={186} textAnchor="middle" style={{ fontSize: 12, fill: up ? C.teal : C.soft, fontWeight: 600 }}>{up ? 'Up bar: close above open' : 'Down bar: close below open'}</text>
    </g>;
  };
  return <svg role="img" aria-label="Two candles: an up bar whose close is above its open and a down bar whose close is below its open, each with its high and low marked" viewBox="0 0 560 200" style={s.svg}>
    {candle(110, true)}{candle(380, false)}
  </svg>;
}

// Streams: each symbol is scanned separately on each timeframe.
function StreamGrid() {
  const tfs = ['5m', '15m', '1h', '1d'];
  const rows: [string, string[]][] = [['AAPL', ['ok', 'ok', 'ok', 'ok']], ['MSFT', ['paused', 'ok', 'ok', 'ok']], ['AMD', ['ok', 'late', 'ok', 'ok']]];
  const fill = (v: string) => (v === 'ok' ? 'rgba(45,212,191,0.55)' : v === 'paused' ? 'rgba(245,158,11,0.55)' : 'rgba(251,113,133,0.5)');
  return <svg role="img" aria-label="A grid of three symbols by four timeframes; each cell is a separate stream with its own status" viewBox="0 0 560 190" style={s.svg}>
    {tfs.map((tf, k) => <text key={tf} x={150 + k * 100 + 40} y={22} textAnchor="middle" style={{ fontSize: 12, fill: C.text, fontFamily: MONO }}>{tf}</text>)}
    {rows.map(([sym, st], r) => <g key={sym}>
      <text x={130} y={58 + r * 44} textAnchor="end" style={{ fontSize: 12, fill: C.head, fontFamily: MONO }}>{sym}</text>
      {st.map((v, k) => <g key={k}><rect x={150 + k * 100} y={36 + r * 44} width={80} height={32} rx={6} fill={fill(v)} />
        <text x={190 + k * 100} y={57 + r * 44} textAnchor="middle" style={{ fontSize: 11, fill: '#0f172a', fontWeight: 600 }}>{sym} {tfs[k]}</text></g>)}
    </g>)}
    <text x={150} y={182} style={{ fontSize: 11, fill: C.soft }}>Each box is one stream. Green: usable. Amber: paused after a data correction. Red: data late.</text>
  </svg>;
}

const TOC: [string, string][] = [
  ['rzy-what', '1. What Little RZY is, and what it is not'],
  ['rzy-bars', '2. Bars (candles): the building block'],
  ['rzy-timeframes', '3. Why 5m, 15m, 1h and daily bars? What each one represents'],
  ['rzy-streams', '4. What a “stream” is'],
  ['rzy-pattern', '5. The pattern it looks for, step by step'],
  ['rzy-life', '6. The life of a setup, and what the emails mean'],
  ['rzy-page', '7. How to read each part of this page'],
  ['rzy-routine', '8. A practical routine for using it'],
  ['rzy-evidence', '9. How well does it work? (honest status)'],
  ['rzy-glossary', '10. Glossary'],
];

export default function LittleRzyBeginnerGuide() {
  return <details open className="rounded-lg border border-slate-700" style={{ background: 'rgba(15,23,42,0.6)', padding: 16 }}>
    <summary style={{ ...s.summary, fontSize: 16 }}>Start here: a beginner’s guide to Little RZY</summary>
    <div style={{ marginTop: 10, color: C.text, fontSize: 14 }}>
      <P>This guide assumes no trading background. It explains what the strategy does, what the 5m, 15m, 1h and daily bars mean, what a stream is, and how to read every part of this page. Open any section below; the more technical timeframe reference follows after this guide.</P>
      <nav aria-label="Guide contents" style={{ margin: '8px 0 4px' }}>
        <OL>{TOC.map(([id, t]) => <LI key={id}><a href={`#${id}`} style={{ color: C.teal }}>{t.replace(/^\d+\.\s*/, '')}</a></LI>)}</OL>
      </nav>

      <Part id="rzy-what" title={TOC[0][1]} open>
        <P><B>Little RZY is a rule-based pattern scanner.</B> Every minute during US market hours it checks the stocks on your watchlist for one specific chart pattern: a pullback inside an established trend, followed by a candle that “bounces” off the trend line. When it finds one, it records it, can draw it on the stock chart, and can email you.</P>
        <P><B>Where the name comes from.</B> It is named after the trader whose discretionary method inspired it: a measured-move trend approach (from Chart Fanatics’ “measured-move trend strategy”) combined with John Bollinger’s rules for Bollinger Bands. Our version, called <Code>LITTLE_RZY_INSPIRED_V1</Code>, turns that human judgement into exact, repeatable rules. It is an interpretation, not a copy of anyone’s actual trading, and not an endorsement.</P>
        <P><B>What it covers.</B> US stocks only, during the regular session (9:30 AM to 4:00 PM Eastern), on four bar sizes: 5-minute, 15-minute, one-hour and daily. Pre-market and after-hours bars are not used. A 4-hour timeframe is deliberately unavailable.</P>
        <P><B>How it relates to the rest of the app.</B> It is completely separate from the AI Signals (BUY/SELL/HOLD), the rankings and your paper portfolios. It never places orders, never touches your real or paper portfolio balances, and keeps its own simulated “research paper ledger”.</P>
        <Callout tone="amber" title="Important: this is a research tool, not a trading system">
          Testing so far has found <B>no evidence that these signals beat random entry timing</B> (see section 9). Every simulated paper trade is currently cancelled on purpose, because the stored price data does not yet have verified timing. Treat a Little RZY alert as “a pattern worth looking at”, never as an instruction to buy or sell.
        </Callout>
        <P><B>Where else you will see it.</B></P>
        <UL>
          <LI><B>Stock chart:</B> on a stock’s page, open the chart’s Indicators menu and turn on “Little RZY-inspired” to draw pivots, trend lines and setups. SMA 5 and SMA 10 are listed beside it; they are simple 5-bar and 10-bar moving averages and are separate from the RZY rules.</LI>
          <LI><B>Settings → Short-term trading & Little RZY-inspired:</B> opt in to the morning digest email and the pattern emails, and choose which timeframes you want emails for. Everything is off until you turn it on.</LI>
          <LI><B>This page (admin only):</B> the operations and research view: what the scanner is doing, data quality, emails sent, and the simulated ledger.</LI>
        </UL>
      </Part>

      <Part id="rzy-bars" title={TOC[1][1]}>
        <P>Charts summarise price over fixed slices of time. Each slice becomes one <B>bar</B>, usually drawn as a <B>candle</B>. A 5-minute bar summarises five minutes of trading; a daily bar summarises a whole trading day. Every bar has five numbers, often written <Code>OHLCV</Code>:</P>
        <Table head={['Value', 'Meaning']} rows={[
          ['Open', 'The first traded price in the slice.'],
          ['High', 'The highest price reached in the slice.'],
          ['Low', 'The lowest price reached in the slice.'],
          ['Close', 'The last traded price in the slice. Most rules use the close because it is final once the slice ends.'],
          ['Volume', 'How many shares traded in the slice. Little RZY requires the trigger candle to have some volume.'],
        ]} />
        <figure style={{ margin: '8px 0', overflowX: 'auto' }}><CandleDiagram />
          <figcaption style={{ fontSize: 12, color: C.muted }}>The thick body spans open to close; the thin lines (wicks) reach the high and the low.</figcaption></figure>
        <h4 style={s.h4}>Completed versus forming bars</h4>
        <P>While a slice is still in progress, its bar keeps changing: the close moves, and the high or low can extend. That is a <B>forming</B> bar. Once the slice ends the bar is <B>completed</B> and should not change. Little RZY only ever decides on completed bars. This is why an alert always arrives a little after a bar closes, never in the middle of one, and why you should not act on a forming candle that merely looks like a trigger.</P>
        <P>Sometimes the data provider <B>corrects</B> a bar it already delivered (called a <B>restatement</B>). When that happens Little RZY pauses that stream and rebuilds its history from the corrected bars. You will see this in the Data health section as “restated bars” and “rebuilds”.</P>
      </Part>

      <Part id="rzy-timeframes" title={TOC[2][1]}>
        <P>A <B>timeframe</B> is the length of time each bar covers. The same price action looks very different depending on which one you use, much like zooming a map in and out. Little RZY supports four. (There is no 10-minute timeframe; the “10” you may see on the chart is the SMA 10 moving average.)</P>
        <Table head={['Timeframe', 'Each bar covers', 'Bars per trading day', 'What it represents', 'Typical use']} rows={[
          ['5m', '5 minutes', '78', 'The finest detail: individual pushes and pauses within the day. Very noisy.', 'Timing: pinpointing the exact candle to act on.'],
          ['15m', '15 minutes', '26', 'The shape of the trading day: intraday trends, pullbacks and the A–B–C structure.', 'Setup: finding the pattern worth watching.'],
          ['1h', '1 hour', '7 (the last one is 30 minutes)', 'Swings that play out over one to several days.', 'Context: is the multi-day swing up or down?'],
          ['1d', 'one full session', '1', 'The broad trend over weeks and months.', 'Backdrop: the trend you trade with, not against.'],
        ]} />
        <h4 style={s.h4}>Why have several timeframes at all?</h4>
        <UL>
          <LI><B>Shorter bars react sooner but are noisier.</B> On 5m, a pattern forms and is confirmed within an hour or two, but many of those patterns are just random wiggles. Stops are tight, so trading costs eat a large share of each trade.</LI>
          <LI><B>Longer bars are slower but carry more meaning.</B> A daily uptrend reflects weeks of buying. It produces few setups, and each takes days or weeks to develop, but costs barely matter and the signal is less random.</LI>
          <LI><B>Every rule is counted in bars, not minutes.</B> “Wait 7 bars to confirm a turning point” means 35 minutes on 5m, 1 hour 45 minutes on 15m, about one session on 1h, and 7 trading days on daily. Same rule, very different clock time.</LI>
          <LI><B>Traders read them together, top-down.</B> Daily and 1h tell you the direction; 15m shows the setup; 5m times the entry. If they disagree, the usual advice is to skip. Little RZY itself does not combine timeframes; each one is scanned on its own (see “streams”). Combining them is a reading habit you apply yourself.</LI>
        </UL>
        <h4 style={s.h4}>Where the bars come from</h4>
        <P>5-minute and daily bars are stored from the market data providers. 15-minute and one-hour bars are <B>built from completed 5-minute bars</B>, lined up with the 9:30 open: three 5m bars make one 15m bar, twelve make one 1h bar. If even one of the 5m pieces is missing, that 15m or 1h bar is <B>withheld</B> rather than guessed. So a problem in 5m data shows up in 15m and 1h too. On early-close days the last bar ends at the actual 1:00 PM close.</P>
        <Callout tone="sky" title="Rule of thumb for beginners">
          Start by reading the daily chart to see the trend, then look at 15m setups that go the same way. Use 5m only to watch how price behaves when it reaches the 15m line. Ignore setups that fight the daily trend.
        </Callout>
      </Part>

      <Part id="rzy-streams" title={TOC[3][1]}>
        <P>A <B>stream</B> is one stock on one timeframe, for example <Code>MSFT 15m</Code>. If your watchlist has 50 US stocks and all four timeframes are scanned, that is 200 streams. Each stream is completely independent:</P>
        <UL>
          <LI>It has its own memory: the turning points found so far, the current trend, any setup in progress, and any simulated position.</LI>
          <LI>It is scanned separately. <Code>MSFT 5m</Code> can show a bullish trigger while <Code>MSFT 1d</Code> is in a downtrend. The scanner never compares them for you.</LI>
          <LI>It has its own data health status. One stream can be paused while the others for the same stock keep working.</LI>
          <LI>Its identity also includes the rule settings (the “config” hash shown at the top of the page). Changing a setting starts fresh streams instead of mixing old and new results.</LI>
        </UL>
        <figure style={{ margin: '8px 0', overflowX: 'auto' }}><StreamGrid />
          <figcaption style={{ fontSize: 12, color: C.muted }}>Illustration only. Three stocks times four timeframes gives twelve streams, each with its own status.</figcaption></figure>
        <h4 style={s.h4}>Stream statuses you will see</h4>
        <Table head={['Status', 'What it means']} rows={Object.entries(RZY_STREAM_STATUS).map(([k, v]) => [<Code key={k}>{k}</Code>, v])} />
        <P><B>Generation.</B> Each time a stream is rebuilt after a data correction, its generation number goes up by one. Events from older generations are kept for the record but marked superseded, and emails queued from an old generation are not sent.</P>
        <P><B>Snapshot.</B> After each scan, the result for each stream is saved as a snapshot. The digest and this page read those snapshots; opening the page never re-runs the scan. A snapshot older than the latest session is “expired” and is not used for current candidates.</P>
      </Part>

      <Part id="rzy-pattern" title={TOC[4][1]}>
        <P>Little RZY looks for a <B>continuation</B> setup: in an established trend, price pulls back, then resumes. The description below is for a long (bullish) setup; a short (bearish) setup is the exact mirror image. The diagram in the technical reference below shows the same shape.</P>
        <OL>
          <LI><B>Find turning points (pivots).</B> A pivot high is a bar whose high is above the 7 bars before it and the 7 bars after it; a pivot low is the reverse. Because it needs 7 bars after it, a pivot is only <i>confirmed</i> 7 bars later. The chart can draw it at its original spot, but the strategy only uses it once confirmed, so it never “repaints” history.</LI>
          <LI><B>Decide the trend.</B> Uptrend: the latest two confirmed highs are rising <i>and</i> the latest two confirmed lows are rising. Downtrend: both falling. Anything else is neutral, and no new setups are taken.</LI>
          <LI><B>Find the A–B–C structure.</B> A is a pivot low, B the highest high after it, C a higher pivot low after B. The move from A to B must be strong (at least 1.5 times the ATR, the typical bar range), and the pullback from B to C must give back between 20% and 80% of that move.</LI>
          <LI><B>Check the pullback is healthy.</B> At C, price should have pulled back to around the middle Bollinger band (the 20-bar average), within a quarter of an ATR. This is the “Bollinger context” filter.</LI>
          <LI><B>Draw the line and measure the target.</B> A straight line through A and C is the trend line. D is how far B stood above that line. The target is the highest high plus D again: the “measured move”. When C is confirmed, the setup becomes <B>armed</B> and these levels are frozen.</LI>
          <LI><B>Wait for the trigger.</B> Within 20 bars, a later <i>completed</i> candle must touch the line (within a tenth of an ATR), close above the line, and close above the previous bar’s high. It must not close beyond the upper Bollinger band (the “no chase” rule).</LI>
          <LI><B>Set the stop and check reward/risk.</B> The stop goes below the lowest of C, the trigger candle’s low and the line, minus another quarter ATR. Planned reward/risk must be at least 1.5. In simulation the entry is at the next bar’s open, and reward/risk is checked again there.</LI>
          <LI><B>Manage it.</B> The trade ends at the target, at the stop, or when a candle closes back below the line (then it exits at the next open).</LI>
        </OL>
        <h4 style={s.h4}>A worked example with made-up round numbers</h4>
        <P>A = $100, B = $106, C = $102. The A–C line, at the bar where B occurred, is at $101, so D = $106 − $101 = $5, and the target is $106 + $5 = <B>$111</B>. A later 15m candle dips to the line at $102.40 and closes at <B>$103.50</B>, above the line and above the prior bar’s high. Suppose the stop works out to <B>$101.50</B>. Then risk is $2.00 per share and reward is $7.50, a planned reward/risk of 3.75. That passes the 1.5 minimum, so it would be a valid trigger. These numbers are invented to show the arithmetic; they are not from a real stock.</P>
        <h4 style={s.h4}>Sequence, exhaustion and reversal watches</h4>
        <UL>
          <LI><B>Sequence</B> counts setups inside one trend: the first is 1, the next 2, and so on. Setups 1 and 2 are preferred. A trend already on its 3rd or later setup is “late”. If the data starts in the middle of a trend, the sequence is shown as unknown rather than guessed.</LI>
          <LI><B>Exhaustion warning:</B> a late trend (sequence 3 or more) that recently closed beyond the outer band, came back inside, and whose latest measured move is smaller than the previous one. It is a caution flag only.</LI>
          <LI><B>Reversal watch:</B> after a trend finished or showed exhaustion and its setup failed, price crosses the middle band the other way within 20 bars. It is a <i>watch</i> alert. It never opens a trade in the opposite direction.</LI>
        </UL>
      </Part>

      <Part id="rzy-life" title={TOC[5][1]}>
        <P>Each setup moves through a fixed set of states. Knowing them makes the emails and the ledger easy to read.</P>
        <Table head={['Setup state', 'Meaning']} rows={[
          ['DETECTED', 'An A–B–C shape was found.'],
          ['ARMED', 'C is confirmed, the line and target are frozen, and the scanner is waiting for a trigger candle (for up to 20 bars).'],
          ['TRIGGERED', 'A completed candle met all the trigger rules.'],
          ['COMPLETED', 'The target was reached.'],
          ['INVALIDATED', 'Price closed through the line, so the idea is wrong.'],
          ['EXPIRED', 'No trigger within 20 bars, or the trend changed.'],
          ['REJECTED', 'The shape existed but failed a filter (for example reward/risk below 1.5).'],
        ]} />
        <Table head={['Simulated position state', 'Meaning']} rows={[
          ['PENDING_ENTRY', 'Waiting for the next bar to open so the entry can be simulated and rechecked.'],
          ['OPEN', 'The simulated entry happened (currently never, because fills are disabled).'],
          ['CLOSED', 'Finished. “CLOSED (cancelled)” means it never opened: no profit or loss, just the reasons it was blocked.'],
        ]} />
        <h4 style={s.h4}>Email types</h4>
        <Table head={['Email label', 'What it tells you']} rows={[
          ['Morning digest', 'Sent about 20 minutes before the open (normally 9:10 AM Eastern) on trading days: armed setups on your watchlist with planned reward/risk of at least 1.5. Entry and stop are provisional; wait for a trigger. “No qualifying candidates” is a normal, valid result.'],
          ['Bullish setup triggered (OPEN_LONG)', 'A long trigger fired. The entry is a hypothetical reference price, not an order. Stop below, target above.'],
          ['Bearish setup triggered (OPEN_SHORT)', 'A short trigger fired: a bet on falling prices. Stop above, target below.'],
          ['Watch (WATCH_ONLY)', 'An exhaustion or reversal watch. Nothing opens.'],
          ['PAPER_FILL / EXIT_PENDING / PAPER_EXIT', 'Simulated fills and exits. Not broker fills. Because fills are currently disabled, you should not expect these yet.'],
        ]} />
        <P>Alerts are based on a completed candle of the stated timeframe. A “5m” alert does not mean the idea lasts five minutes; it means the trigger was a 5-minute candle. Email is not a stop-loss: it can be late or never arrive.</P>
      </Part>

      <Part id="rzy-page" title={TOC[6][1]}>
        <P>The rest of this page, from top to bottom:</P>
        <Table head={['Part of the page', 'What it shows', 'What to look for']} rows={[
          ['Header line', 'Strategy version and config hash.', 'If the config hash changes, results before and after are not comparable.'],
          ['Timeframe technical reference', 'Exact clock times of each rule per timeframe, the setup diagram and a top-down checklist.', 'Use it once the basics here make sense.'],
          ['Streams usable now', 'How many streams are in the usable (research_only) state right now, out of all streams.', 'A low number means many streams are paused, late or missing data, so you will see fewer setups.'],
          ['Paused or rebuilding', 'Streams waiting to rebuild after the provider corrected bars.', 'Normally small and temporary.'],
          ['Scan time (median / p95)', 'How long one full scan takes. p95 means 95% of scans were faster than this.', 'Long scans delay alerts; if they grow, alerts become late.'],
          ['Signals too late to email', 'Triggers found after their email deadline.', 'A rising number means alerts are arriving too slowly to be useful.'],
          ['Alert timing and readiness', 'Delay from bar close to detection and email, per timeframe, plus blockers that keep execution off.', 'Median and p95 delays. Seconds matter on 5m, much less on daily.'],
          ['Morning digest preview', 'What the next digest would contain right now, and why each excluded stream was left out.', 'Candidates (symbol, direction, timeframe, entry, stop, target, reward/risk) and the exclusion reasons.'],
          ['Research alerts', 'Recent RZY emails and whether the email provider accepted them.', '“expired” or “suppressed” means not sent, with the reason beside it.'],
          ['Data health', 'Stream counts by timeframe and status, rebuilds, and which provider rewrote completed bars.', 'Many cross_provider revisions point to data providers disagreeing.'],
          ['Scanner runs', 'Each recent scan: time, duration, streams scanned, events found, and statuses.', 'Scans run from 50 minutes before the open until 5 minutes after the close.'],
          ['Research paper ledger', 'The simulated one-share ledger, its performance figures and why each entry was cancelled.', 'Today every row is cancelled by design; the reasons explain why.'],
        ]} />
      </Part>

      <Part id="rzy-routine" title={TOC[7][1]}>
        <OL>
          <LI><B>Before the open:</B> read the morning digest (or the preview here). Note which stocks have armed setups, their direction and timeframe.</LI>
          <LI><B>Check the bigger picture:</B> open each candidate’s chart on the daily timeframe. Is the daily trend going the same way as the setup? If not, set it aside.</LI>
          <LI><B>Check the data:</B> make sure the stream is listed as usable in Data health. 15m and 1h depend on complete 5m data.</LI>
          <LI><B>Wait for the trigger:</B> do nothing until a completed candle triggers. Do not jump in on a forming candle, and do not chase a candle that has already run far.</LI>
          <LI><B>Recheck the numbers at the next open:</B> if price gapped past the stop or target, or reward/risk is now below 1.5, skip it.</LI>
          <LI><B>Decide your own risk:</B> the stop marks where the idea is proven wrong. Size any real position so that hitting the stop is a loss you accept. The tool does not size positions for you.</LI>
          <LI><B>Review afterwards:</B> look at what happened next on the chart. Over many setups, this tells you more than any single outcome.</LI>
        </OL>
        <Callout tone="amber" title="Remember">
          No timeframe has shown an edge so far. The safest use today is learning to read trend structure and pullbacks, and comparing outcomes, not placing real trades on the alerts.
        </Callout>
      </Part>

      <Part id="rzy-evidence" title={TOC[8][1]}>
        <P>The rules were tested on real stored data (5m, 15m and 1h from July 21 to October 20, 2026; daily from December 2023). Each result was compared with <B>random entry timing</B> on the same stocks: if the rules are useful, their trades should do better than entries at random moments.</P>
        <Table head={['Test', 'Result']} rows={[
          ['First real-data evaluation', '693 simulated trades, average −0.09R after costs. No edge versus random timing.'],
          ['Corrected re-run', 'Edge versus random timing +0.001R; the 95% range (−0.066R to +0.073R) includes zero. No edge.'],
          ['By timeframe', '5m and 15m: no edge in either direction. 1h and daily: too few trades to judge.'],
          ['Most promising slice', '15m looked slightly positive early (+0.028R) but lost money in the later holdout period (−0.145R).'],
          ['How trades end', 'Planned targets average about 3R, but most trades exit within a few bars when a candle closes back through the line.'],
        ]} />
        <P>“R” is the planned risk of a trade (see the glossary). +0.1R per trade would mean earning a tenth of your risk on average; 0 means breaking even. Costs alone took about 0.12R per trade on 5m, because 5m stops are so tight.</P>
        <P><B>Why paper fills are disabled.</B> The stored prices do not carry a verified record of when the provider delivered them or how delayed they were, and there is no data on whether a stock can be borrowed for shorting. Without that, a simulated fill could be unrealistically good, so every simulated entry is cancelled with the reason <Code>data_basis_or_freshness_unverified</Code> (and shorts also <Code>shortability_or_borrow_cost_unverified</Code>). This is deliberate, not a bug.</P>
      </Part>

      <Part id="rzy-glossary" title={TOC[9][1]}>
        <Table head={['Term', 'Meaning']} rows={[
          ['ATR (Average True Range)', 'The typical size of one bar’s range over the last 14 bars. Used as a ruler so rules scale with how volatile a stock is.'],
          ['Bollinger Bands', 'A middle line (the 20-bar average close) with an upper and lower band 2 standard deviations away. Price near the middle band in a trend is a normal pullback; a close beyond an outer band is a stretched move.'],
          ['Middle band', 'The 20-bar average close: the centre Bollinger line.'],
          ['Pivot', 'A turning point: a bar whose high (or low) is the extreme of the 7 bars on each side. Confirmed only after the 7 bars that follow it.'],
          ['Trend / trend epoch', 'Up when the last two highs and last two lows both rise; down when both fall. An epoch is one continuous stretch of the same trend.'],
          ['A, B, C', 'The three pivots of a setup: the start, the extreme of the push, and the pullback.'],
          ['D / measured move', 'How far B stood from the A–C line, projected again from the extreme to set the target.'],
          ['Armed', 'A confirmed setup waiting for its trigger candle (up to 20 bars).'],
          ['Trigger', 'The completed candle that touches the line and closes back in the trend direction.'],
          ['Entry reference', 'The hypothetical entry price. Not an order or a fill.'],
          ['Stop', 'The price that proves the setup wrong. Exiting there limits the loss.'],
          ['Target', 'The planned exit price at the measured move.'],
          ['R', 'One unit of risk: the distance from entry to stop. A trade that reaches a target 3 times that distance away earns 3R.'],
          ['Reward/risk (R/R)', 'Distance to target divided by distance to stop. Little RZY requires at least 1.5. It is planned geometry, not a probability of winning.'],
          ['Long / short', 'Long profits if the price rises. Short profits if it falls (you sell borrowed shares and buy them back later).'],
          ['Continuation', 'A trade in the same direction as the existing trend.'],
          ['No-chase rule', 'Do not enter on a candle that has already closed beyond the outer band in the trade direction.'],
          ['Sequence', 'The count of setups within one trend. 1 and 2 are preferred; 3 or more means the trend is late.'],
          ['Exhaustion warning', 'A caution flag that a late trend may be tiring. Not a trade.'],
          ['Reversal watch', 'A heads-up that the trend may be turning. Never opens a trade.'],
          ['Stream', 'One stock on one timeframe, scanned on its own.'],
          ['Snapshot', 'The saved result of the latest scan of a stream.'],
          ['Restatement', 'The data provider changing a bar it already delivered.'],
          ['Rebuild / generation', 'Recomputing a stream from corrected bars. Each rebuild is a new generation.'],
          ['Cross-provider revision', 'A different data provider rewrote a completed bar.'],
          ['Config hash', 'A fingerprint of all rule settings. A different hash means different rules.'],
          ['Expectancy', 'The average result per trade, in R, after costs.'],
          ['Win rate', 'The share of trades that made money. A high win rate can still lose overall if losses are bigger than wins.'],
          ['Drawdown', 'The largest drop from a peak in cumulative results.'],
          ['bps (basis points)', 'Hundredths of a percent. 2 bps = 0.02%. The simulation charges 2 bps spread, 2 bps slippage and $0.005 per share commission.'],
          ['p95', 'The 95th percentile: 95% of observations were at or below this value.'],
        ]} />
      </Part>
    </div>
  </details>;
}
