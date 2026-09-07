/**
 * Short Squeeze vs Gamma Squeeze — /squeeze-playbook. Advanced-tier/admin-only Learning page.
 *
 * Explains the two mechanically-distinct squeeze types, compares them, and gives an execution
 * playbook — grounded in what THIS platform actually measures, not generic options-education
 * content. Mechanics cited from services/market-data/src/services/scheduler.py's own alert
 * functions; measured win rates from docs/audits/2026-09-03-six-part-platform-audit-5-short-
 * squeeze.md and docs/2026-09-05/ (SQUEEZE_IGNITION_REVIEW.md,
 * SHORT_SQUEEZE_ALERT_TUNING_REVIEW.md, GAMMA_SQUEEZE_CAPABILITY_REVIEW.md).
 *
 * Deliberate editorial stance: every one of this platform's four squeeze/gamma alert types
 * currently measures weak-or-negative edge on real resolved outcomes. A "how to make money"
 * page that omitted that would be actively misleading, so the playbook below is built on
 * MECHANICS (what actually causes a squeeze, what makes one fail) plus the platform's own live
 * scanner data — explicitly NOT on "buy the alert email." The measured figures are quoted as a
 * dated snapshot with their real sample-size caveats, and users are pointed at the live
 * performance page rather than these frozen numbers wherever possible.
 */
import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import { getSession, hasAdvancedAccess } from '@/lib/auth';

// ── Shared components (matches learn.tsx / qqq-leaps-playbook.tsx conventions) ───────────────

function Section({ title, id, children }: { title: string; id?: string; children: React.ReactNode }) {
  return (
    <div id={id} style={{ marginBottom: 36, scrollMarginTop: 70 }}>
      <h2 style={{ fontSize: 17, fontWeight: 800, color: '#e2e8f0', marginBottom: 12 }}>{title}</h2>
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

function Mono({ children }: { children: React.ReactNode }) {
  return (
    <pre style={{
      background: '#0d1424', border: '1px solid #1e293b', borderRadius: 10, padding: '14px 16px',
      fontSize: 12.5, color: '#cbd5e1', fontFamily: 'monospace', lineHeight: 1.7, overflowX: 'auto',
      marginBottom: 16, whiteSpace: 'pre',
    }}>
      {children}
    </pre>
  );
}

function DataTable({ headers, rows, highlightCol }: { headers: string[]; rows: React.ReactNode[][]; highlightCol?: number }) {
  return (
    <div style={{ borderRadius: 10, border: '1px solid #1e293b', overflow: 'hidden', marginBottom: 16, overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
        <thead>
          <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
            {headers.map(h => (
              <th key={h} style={{ textAlign: 'left', padding: '8px 12px', color: '#475569', fontWeight: 700, fontSize: 10.5, textTransform: 'uppercase', letterSpacing: 0.4, borderBottom: '1px solid #1e293b' }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} style={{ borderBottom: i < rows.length - 1 ? '1px solid #1e293b' : 'none' }}>
              {row.map((cell, j) => (
                <td key={j} style={{
                  padding: '8px 12px', verticalAlign: 'top',
                  color: j === 0 || j === highlightCol ? '#e2e8f0' : '#94a3b8',
                  fontWeight: j === 0 || j === highlightCol ? 600 : 400,
                }}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StepList({ steps }: { steps: React.ReactNode[] }) {
  return (
    <ol style={{ margin: '8px 0 0', paddingLeft: 20 }}>
      {steps.map((s, i) => (
        <li key={i} style={{ marginBottom: 10 }}>{s}</li>
      ))}
    </ol>
  );
}

const JUMP_LINKS: { id: string; label: string }[] = [
  { id: 'short-squeeze', label: '1. What is a short squeeze' },
  { id: 'gamma-squeeze', label: '2. What is a gamma squeeze' },
  { id: 'comparison', label: '3. Side-by-side comparison' },
  { id: 'reality', label: '4. What this platform actually measures' },
  { id: 'playbook', label: '5. The playbook' },
  { id: 'sizing', label: '6. Sizing & risk' },
  { id: 'mistakes', label: '7. Common ways people lose' },
  { id: 'checklist', label: '8. Pre-trade checklist' },
];

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SqueezePlaybookPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    if (!hasAdvancedAccess(session)) { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  if (!authed) return null;

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '24px 0 60px' }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: '#e2e8f0', marginBottom: 6 }}>
          Short Squeeze vs Gamma Squeeze
        </h1>
        <p style={{ fontSize: 13, color: '#64748b', maxWidth: 720 }}>
          Two different mechanics that both produce violent upside moves — and are constantly
          confused with each other. What each actually is, how they differ, what this platform
          really measures for both, and a playbook grounded in that measurement rather than in
          hype.
        </p>
      </div>

      <Callout tone="warn" title="The honest headline — read this first">
        Every one of this platform&apos;s four squeeze/gamma alert types currently measures{' '}
        <strong style={{ color: '#f87171' }}>weak or negative edge</strong> on real resolved
        outcomes (§4 has the exact numbers and their sample-size caveats). A page telling you
        &quot;buy the squeeze alert email to make money&quot; would be contradicted by this
        platform&apos;s own data.
        <br /><br />
        So this playbook is built on <strong style={{ color: '#e2e8f0' }}>mechanics</strong> — what
        actually causes a squeeze, what makes one fail, and how to use the live{' '}
        <Link href="/short-squeeze" style={{ color: '#38bdf8', textDecoration: 'none' }}>Short Squeeze Scanner</Link>{' '}
        as a research starting point — explicitly not on trading alerts blindly. Educational
        framework, not financial advice.
      </Callout>

      {/* Jump nav */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 32, padding: '12px 14px',
        background: 'rgba(148,163,184,0.04)', border: '1px solid #1e293b', borderRadius: 10,
      }}>
        {JUMP_LINKS.map(l => (
          <a key={l.id} href={`#${l.id}`} style={{
            fontSize: 11.5, color: '#818cf8', textDecoration: 'none', padding: '4px 9px',
            borderRadius: 6, background: 'rgba(129,140,248,0.08)', border: '1px solid rgba(129,140,248,0.2)',
          }}>
            {l.label}
          </a>
        ))}
      </div>

      {/* ── 1. Short squeeze ────────────────────────────────────────────────── */}
      <Section id="short-squeeze" title="1. What is a short squeeze?">
        <p style={{ marginBottom: 14 }}>
          A short seller borrows shares, sells them, and profits if the price falls. They owe those
          shares back. If the price <em>rises</em> instead, their loss is theoretically unlimited —
          and at some point they must buy shares to close the position.
        </p>
        <p style={{ marginBottom: 14 }}>
          A <strong style={{ color: '#e2e8f0' }}>short squeeze</strong> is the feedback loop that
          happens when many shorts are forced to buy at once: their buying pushes the price up,
          which forces more shorts to cover, which pushes the price up further. The fuel is{' '}
          <strong style={{ color: '#e2e8f0' }}>the size of the short position relative to available
          shares</strong>.
        </p>

        <SubSection title="The three ingredients">
          <DataTable
            headers={['Ingredient', 'What it measures', 'Why it matters']}
            rows={[
              ['Short % of float', 'Shorted shares ÷ freely-tradeable shares', 'The raw fuel. 15%+ is elevated; 20-30%+ is heavily shorted.'],
              ['Days to cover', 'Short interest ÷ average daily volume', 'How many days of normal volume shorts need to exit. High = they can’t exit quietly.'],
              ['A catalyst', 'Earnings beat, news, sector move, a big buyer', 'Squeezes need a trigger. High short interest alone can sit dormant for months.'],
            ]}
          />
          <Callout tone="info" title="Borrow fee and shares-available are the underrated signals">
            When a stock gets genuinely hard to borrow, the borrow fee spikes and{' '}
            <Code>short_shares_available</Code> collapses. That&apos;s a real-time stress signal that
            short interest percentage alone won&apos;t show you — this platform surfaces both on the{' '}
            <Link href="/short-squeeze" style={{ color: '#38bdf8', textDecoration: 'none' }}>Short Squeeze Scanner</Link>{' '}
            via real Unusual Whales short-interest data.
          </Callout>
        </SubSection>

        <SubSection title="How this platform's short-squeeze alert actually works">
          <p style={{ marginBottom: 10 }}>
            <Code>check_short_squeeze_alerts()</Code> runs every minute and fires on a{' '}
            <em>state transition</em> (not-qualifying → qualifying) when all of these hold at once:
          </p>
          <Mono>{`short_percent_of_float  >=  15.0%      (weekly-refreshed fundamentals cache)
intraday change_pct     >=  3.0%       (the move is ALREADY under way)
RVOL (vol / 20d avg)    >=  ~2.2       (session-elapsed-scaled; volume confirmation)
short-interest age      <=  30 days    (hard reject if staler)`}</Mono>
          <Callout tone="warn" title="Notice what this design implies">
            The 3% move and 2.2× RVOL are <strong>confirming</strong> conditions — by the time this
            alert fires, the move has already started. This is deliberate (it filters out dormant
            high-short-interest stocks that never go anywhere), but it means the alert is
            structurally <strong style={{ color: '#e2e8f0' }}>late by design</strong>, and this
            platform&apos;s own tuning review concluded it tends to select stocks that already popped
            and then mean-revert. That&apos;s the single most important thing to understand before
            trading it.
          </Callout>
        </SubSection>
      </Section>

      {/* ── 2. Gamma squeeze ────────────────────────────────────────────────── */}
      <Section id="gamma-squeeze" title="2. What is a gamma squeeze?">
        <p style={{ marginBottom: 14 }}>
          A gamma squeeze has <strong style={{ color: '#e2e8f0' }}>nothing to do with short
          sellers of stock</strong>. It comes from options market makers hedging their own risk.
        </p>
        <p style={{ marginBottom: 14 }}>
          When you buy a call, a market maker sells it to you. To stay delta-neutral, they buy some
          shares as a hedge. <strong style={{ color: '#e2e8f0' }}>Gamma</strong> is the rate at which
          that required hedge changes as the stock moves. As the stock rises toward and through a
          strike with heavy open interest, the dealer&apos;s required hedge grows{' '}
          <em>non-linearly</em> — they must buy more shares, which pushes the price up, which
          requires more hedging.
        </p>

        <SubSection title="The mechanical difference in one sentence">
          <Callout tone="example" title="Short squeeze vs gamma squeeze">
            A <strong>short squeeze</strong> is forced buying by people who bet against the stock and
            are losing. A <strong>gamma squeeze</strong> is mechanical, non-discretionary buying by
            market makers who have no view on the stock at all — they&apos;re just hedging.
          </Callout>
        </SubSection>

        <SubSection title="Key concepts">
          <DataTable
            headers={['Term', 'What it means', 'Why you care']}
            rows={[
              [<>Gamma exposure (<Code>GEX</Code>)</>, 'Aggregate dealer gamma across the chain', 'Sign tells you whether dealer hedging dampens or amplifies moves'],
              ['Call wall', 'Strike with the largest call open interest', 'Often acts as resistance — dealers sell into it — until it breaks, then it accelerates'],
              ['Put wall', 'Strike with the largest put open interest', 'Often acts as support'],
              ['Gamma flip', 'Price where aggregate dealer gamma flips sign', 'Above it, hedging usually suppresses volatility; below it, hedging amplifies it'],
              ['0-DTE / expiry proximity', 'Days until the heavy OI expires', 'Gamma effects intensify sharply as expiry approaches, then vanish entirely after it'],
            ]}
          />
        </SubSection>

        <SubSection title="How this platform's gamma alert actually works — and its real limitation">
          <Callout tone="warn" title="This is a proxy, not a real GEX model — stated in the code's own docstring">
            <Code>check_gamma_unwind_alerts()</Code> does <strong>not</strong> compute true gamma
            exposure. A real GEX model needs per-contract Black-Scholes gamma plus a
            dealer-positioning assumption; neither is computed. What it actually detects is{' '}
            <strong style={{ color: '#e2e8f0' }}>near-the-money open-interest concentration close to
            expiry</strong>:
          </Callout>
          <Mono>{`strikes within  ±5%  of spot
expiry within    5   calendar days
notional        >= $5M
lopsided:  calls >= 85%   (bullish-thesis alert: gamma_unwind_calls)
        or  puts >= 55%   (bearish-thesis alert: gamma_unwind_puts)`}</Mono>
          <p style={{ marginBottom: 10 }}>
            It runs a few times a day (not real-time) over a bounded symbol universe, reconstructed
            from yfinance option chains — a data source this codebase documents as its most
            rate-limit-fragile call.
          </p>
          <p>
            Real Unusual Whales GEX data (<Code>call_wall</Code>, <Code>put_wall</Code>,{' '}
            <Code>gamma_flip</Code>) <em>is</em> fetched, but currently only as post-hoc
            corroboration — <strong style={{ color: '#e2e8f0' }}>it never gates which candidates the
            alert picks</strong>. This platform&apos;s own capability review flags that as the main
            fixable gap: the genuinely predictive number (<Code>gamma_flip</Code>) is being fetched
            and not used for the decision.
          </p>
        </SubSection>
      </Section>

      {/* ── 3. Comparison ───────────────────────────────────────────────────── */}
      <Section id="comparison" title="3. Side-by-side comparison">
        <DataTable
          headers={['', 'Short squeeze', 'Gamma squeeze']}
          highlightCol={1}
          rows={[
            ['Who is forced to buy', 'Short sellers closing losing bets', 'Options dealers hedging, with no directional view'],
            ['Fuel source', 'Short interest vs. available float', 'Open-interest concentration near spot'],
            ['Typical duration', 'Days to weeks', 'Hours to days — often dies at expiry'],
            ['Key metric', 'Short % of float, days to cover, borrow fee', 'GEX, call/put wall, gamma flip, DTE'],
            ['What ends it', 'Shorts finish covering, or price collapses back', 'Expiry passes, or price moves away from the strike cluster'],
            ['Can it happen in an index/ETF?', 'Rarely — float is effectively unlimited', 'Yes — very common in SPY/QQQ around big expiries'],
            ['Predictability', 'Fuel is visible in advance; timing is not', 'Structure is visible in advance; catalyst is not'],
            ['This platform’s alert', <>1-min real-time, <Code>15%</Code> float + <Code>3%</Code> move + RVOL</>, <>Few times/day, OI-concentration proxy (not real GEX)</>],
            ['Measured win rate here', '9.1% (n=11 — see §4 caveat)', '24.5% calls / 31.8% puts'],
          ]}
        />
        <Callout tone="info" title="They can and do stack">
          The most violent historical moves (GME 2021 being the canonical example) were{' '}
          <strong style={{ color: '#e2e8f0' }}>both at once</strong> — heavy short interest forcing
          covering, while heavy call buying forced dealer hedging in the same direction. When you see
          high short float <em>and</em> a heavy near-the-money call cluster into expiry, those are two
          independent forced-buying mechanisms pointing the same way. That confluence is rare and is
          the genuinely interesting setup.
        </Callout>
      </Section>

      {/* ── 4. Reality check ────────────────────────────────────────────────── */}
      <Section id="reality" title="4. What this platform actually measures (the part most pages skip)">
        <p style={{ marginBottom: 14 }}>
          Snapshot of real resolved outcomes from <Code>squeeze_alert_outcomes</Code>, as of
          2026-09-03/05. These are actual fired alerts scored on real forward returns, not backtests:
        </p>
        <DataTable
          headers={['Alert type', 'Fired', 'Win rate', 'Avg 5d return', 'Read']}
          rows={[
            ['short_squeeze', '11', '9.1%', '−6.17%', 'Sample far too small to trust either way — see caveat below'],
            ['squeeze_ignition', '0', '—', '—', 'Has never fired, ever — gate conjunction too rare'],
            ['gamma_unwind_calls', '98', '24.5%', '~−0.01%', 'Worse than a coin flip on a real sample size'],
            ['gamma_unwind_puts', '211', '31.8%', '~−0.005%', 'Largest sample, still below breakeven'],
          ]}
        />

        <Callout tone="warn" title="The n=11 caveat matters enormously — don't over-read 9.1%">
          Nine of those eleven <Code>short_squeeze</Code> alerts fired inside a{' '}
          <strong>single correlated 8-day window</strong> (2026-08-17 → 08-24) during a broad
          momentum reversal where SPY itself fell ~2% and the named symbols fell 11-22%. The
          effective <em>independent</em> sample size is closer to 2 than 11. This platform&apos;s own
          audit states it plainly: the 9.1% figure &quot;should not be read as a reliable long-run
          figure either way, positive or negative.&quot;
          <br /><br />
          The <Code>gamma_unwind_*</Code> numbers (n=98 and n=211) are far more meaningful — and
          they&apos;re still below breakeven.
        </Callout>

        <SubSection title="Why the short-squeeze alert underperforms — a real structural finding">
          <p style={{ marginBottom: 10 }}>
            This platform&apos;s tuning review reached a specific, useful conclusion: the alert&apos;s
            own confirming conditions (3%+ intraday move plus high RVOL) systematically select for{' '}
            <strong style={{ color: '#e2e8f0' }}>stocks that have already popped and are about to
            mean-revert</strong> — not for pre-squeeze setups. It explicitly recommends{' '}
            <strong>not</strong> loosening the thresholds, because looser variants measured worse,
            not better.
          </p>
          <p>
            There&apos;s also a real, disclosed slippage cost: the alert&apos;s outcome scoring enters
            at <Code>fired_date + 1 day</Code> (a deliberate no-lookahead choice). One real example —
            NBIS alerted at $277.68 and the next-day entry price was $248.43, a{' '}
            <strong style={{ color: '#f87171' }}>10.5% gap</strong>. That gap isn&apos;t a bug; it is
            what an extreme, already-peaking intraday move looks like by the next real entry
            opportunity.
          </p>
        </SubSection>

        <Callout tone="good" title="Check the live numbers, not this snapshot">
          The table above is frozen at one date. Real current win rates by alert type and by forward
          window (1d/2d/3d/5d/10d/20d) are on the{' '}
          <Link href="/squeeze-alert-performance" style={{ color: '#38bdf8', textDecoration: 'none' }}>Squeeze Alert Performance</Link>{' '}
          page <span style={{ color: '#64748b' }}>(admin-only)</span>, which updates as alerts
          resolve. Always prefer that over these numbers.
        </Callout>
      </Section>

      {/* ── 5. Playbook ─────────────────────────────────────────────────────── */}
      <Section id="playbook" title="5. The playbook">
        <Callout tone="info" title="What this playbook is, given §4">
          Since the alerts themselves don&apos;t currently show positive edge, this playbook treats
          them as a <strong style={{ color: '#e2e8f0' }}>research trigger, not a buy signal</strong> —
          something that tells you where to look, after which you do the work below. If you
          aren&apos;t willing to do that work, the honest answer is to skip these setups entirely and
          hold an index instead.
        </Callout>

        <SubSection title="Step 1 — Start from the fuel, not the move">
          <p style={{ marginBottom: 10 }}>
            Use the <Link href="/short-squeeze" style={{ color: '#38bdf8', textDecoration: 'none' }}>Short Squeeze Scanner</Link>{' '}
            to find stocks where the <em>setup</em> exists before the move does. What you want to see:
          </p>
          <Mono>{`short % of float        >= 20%      (15% is the alert floor; 20%+ is real fuel)
days to cover           >= 3         (shorts can't exit quietly)
borrow fee               rising      (genuine borrow stress, not stale data)
short_shares_available   falling     (the squeeze precondition tightening)
short-interest data      < 2 weeks old`}</Mono>
          <p>
            This is the opposite of waiting for the alert email — you&apos;re building a watchlist of
            loaded setups <em>before</em> a catalyst, so you&apos;re not chasing a 3% move that
            already happened.
          </p>
        </SubSection>

        <SubSection title="Step 2 — Require a real catalyst, and name it out loud">
          <p style={{ marginBottom: 10 }}>
            High short interest alone can sit dormant for months. Write down the specific catalyst
            before entering: earnings date, product/FDA event, sector rotation, index inclusion,
            insider buying, an activist stake. If you can&apos;t name one,{' '}
            <strong style={{ color: '#e2e8f0' }}>you don&apos;t have a trade, you have a hope</strong>.
          </p>
          <p>
            Useful cross-checks on this platform: the <Link href="/earnings" style={{ color: '#38bdf8', textDecoration: 'none' }}>Earnings calendar</Link>{' '}
            for date risk, <Link href="/insider" style={{ color: '#38bdf8', textDecoration: 'none' }}>Insider Trading</Link>{' '}
            and <Link href="/congress" style={{ color: '#38bdf8', textDecoration: 'none' }}>Congress Trades</Link>{' '}
            for accumulation, and <Link href="/news" style={{ color: '#38bdf8', textDecoration: 'none' }}>Real-Time News</Link>{' '}
            for the trigger itself.
          </p>
        </SubSection>

        <SubSection title="Step 3 — For the gamma leg, check the option structure">
          <p style={{ marginBottom: 10 }}>
            If you want the gamma mechanism working <em>with</em> you rather than against you:
          </p>
          <StepList steps={[
            <>Find where the heavy near-the-money call open interest actually sits — this is the call wall, and it&apos;s resistance until it breaks.</>,
            <>Check days to expiry. Gamma effects intensify into expiry and <strong style={{ color: '#e2e8f0' }}>disappear the moment it passes</strong> — a gamma thesis has a hard deadline that a short-squeeze thesis does not.</>,
            <>Prefer setups where price is <em>below</em> a heavy call cluster with a catalyst that could push it through, not ones already extended past it (the hedging flow is already spent there).</>,
            <>Use <Link href="/options-flow" style={{ color: '#38bdf8', textDecoration: 'none' }}>Options Flow</Link> to see whether real premium is actually being paid on the call side, or whether the open interest is stale.</>,
          ]} />
        </SubSection>

        <SubSection title="Step 4 — Define exit BEFORE entry (this is the whole game)">
          <p style={{ marginBottom: 10 }}>
            Squeezes are violent in both directions. The measured −6.17% average on this
            platform&apos;s own short-squeeze alerts is largely a story about <em>exits</em>, not
            entries. Decide all four of these in writing first:
          </p>
          <DataTable
            headers={['Decision', 'A concrete default', 'Why']}
            rows={[
              ['Profit target', 'Scale out 1/3 at +15%, 1/3 at +30%, trail the rest', 'Squeezes spike then collapse — taking some off the table on the spike is most of the edge'],
              ['Hard stop', '−8% to −12% from entry, set at entry', 'Below the noise band but above a full collapse. Never widen it after the fact.'],
              ['Time stop', 'Exit if nothing happens in 5-10 trading days', 'A squeeze that doesn’t ignite is dead money in a high-volatility name'],
              ['Gamma deadline', 'Exit before the expiry your thesis depends on', 'For a gamma thesis, expiry passing removes the mechanism entirely'],
            ]}
          />
          <Callout tone="warn" title="The single most common failure">
            Entering on the alert, watching it go against you, and then deciding your exit. Every
            number above must exist before you click buy.
          </Callout>
        </SubSection>
      </Section>

      {/* ── 6. Sizing ───────────────────────────────────────────────────────── */}
      <Section id="sizing" title="6. Sizing and risk">
        <p style={{ marginBottom: 14 }}>
          These are the highest-volatility setups retail traders touch. Size accordingly — the goal
          is to still be solvent and calm after being wrong three times in a row.
        </p>
        <DataTable
          headers={['Account', 'Max per squeeze position', 'Max total squeeze exposure', 'Notes']}
          highlightCol={0}
          rows={[
            ['$10,000', '$300–500 (3–5%)', '$1,000–1,500 (10–15%)', '2–3 positions max; a −10% stop risks $30-50 per trade'],
            ['$20,000', '$600–1,000 (3–5%)', '$2,000–3,000 (10–15%)', 'Same discipline, more room to diversify across setups'],
            ['$50,000+', '$1,500–2,500 (3–5%)', '$5,000–7,500 (10–15%)', 'Consider expressing gamma legs via defined-risk spreads'],
          ]}
        />
        <Callout tone="warn" title="Rules that are not negotiable">
          <strong>Never on margin.</strong> A squeeze name can gap 20% against you overnight, and
          margin turns a survivable loss into a forced liquidation at the worst possible price.{' '}
          <strong>Never average down</strong> into a failing squeeze — the thesis was
          time-and-catalyst dependent, and adding size to a broken thesis is how small losses become
          account-defining ones. <strong>Never size on conviction</strong> — this platform&apos;s own
          confidence calibration is documented as unreliable for exactly this purpose.
        </Callout>
      </Section>

      {/* ── 7. Mistakes ─────────────────────────────────────────────────────── */}
      <Section id="mistakes" title="7. Common ways people lose money on these">
        <DataTable
          headers={['Mistake', 'What actually happens']}
          rows={[
            ['Buying the alert email at market open', 'You enter after the 3% move that triggered it — this platform measured a real 10.5% gap between one alert price and the next-day entry'],
            ['Treating high short interest as a signal by itself', 'It’s fuel, not ignition. Dormant heavily-shorted stocks can drift down for months'],
            ['Confusing the two squeeze types', 'A gamma thesis dies at expiry; a short-squeeze thesis doesn’t. Using the wrong exit timing kills an otherwise-right call'],
            ['Assuming the gamma alert means real GEX', 'It’s an open-interest-concentration proxy. Real gamma_flip data exists on the platform but does not currently drive the alert'],
            ['Reading small samples as edge', '9.1% on n=11 (9 from one correlated week) is noise, in either direction'],
            ['Holding through expiry hoping for a spike', 'After expiry the dealer hedging flow is simply gone — the mechanism you were betting on no longer exists'],
            ['Using AI BUY signals to time entry', 'Documented negative measured edge on this platform — do not use them for this'],
          ]}
        />
      </Section>

      {/* ── 8. Checklist ────────────────────────────────────────────────────── */}
      <Section id="checklist" title="8. Pre-trade checklist">
        <StepList steps={[
          <>Short % of float ≥ 20%, days to cover ≥ 3, short-interest data less than ~2 weeks old</>,
          <>Borrow fee rising and/or <Code>short_shares_available</Code> falling (real borrow stress)</>,
          <>A specific, named catalyst with a date — written down, not implied</>,
          <>If the thesis is gamma-based: identified call wall, and days-to-expiry confirmed</>,
          <>Position ≤ 3–5% of account; total squeeze exposure ≤ 10–15%</>,
          <>Profit target, hard stop, time stop, and gamma deadline all written down before entry</>,
          <>Not on margin. Not averaging down. Not sized on confidence score.</>,
          <>Checked the live <Link href="/squeeze-alert-performance" style={{ color: '#38bdf8', textDecoration: 'none' }}>Squeeze Alert Performance</Link> page rather than trusting §4&apos;s frozen snapshot</>,
        ]} />

        <Callout tone="example" title="If you take one thing from this page">
          The two mechanics are genuinely different and demand different exits — that&apos;s the
          durable, non-expiring knowledge here. The alert win rates will change as more outcomes
          resolve; the mechanics won&apos;t. And if the honest conclusion for your situation is
          &quot;this is too volatile for my account size,&quot; that&apos;s a legitimate answer, not
          a failure to find a trade.
        </Callout>
      </Section>
    </div>
  );
}
