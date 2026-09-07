/**
 * QQQ LEAPS Playbook — /qqq-leaps-playbook. Advanced-tier/admin-only Learning page.
 *
 * Content adapted from docs/recomm_or_audit/QQQ_LEAPS_PLAYBOOK.md (written 2026-09-04 from a
 * real live QQQ option chain) — this page reformats that analysis into the Learning section's
 * own Section/SubSection/Callout/Code conventions (matching learn.tsx and
 * watchlist-rotation-explainer.tsx) rather than inventing new layout patterns, and adds a new
 * $10k/$20k position-sizing case study (the source doc's own smallest bracket was $50k — this
 * page fills that gap since a $10-20k account is a materially different situation: at $214/contract
 * one LEAP alone is 100%+ of a $10-20k account, which the source doc's own "≤10-15% of portfolio"
 * rule already rules out entirely at that size).
 *
 * All dollar figures/greeks below are a SNAPSHOT from a single point in time, explicitly flagged
 * as stale-by-design throughout — this is a structural-mechanics teaching tool (delta, decay,
 * leverage, breakeven math), not a live quote source. The platform's own Options Game Plan /
 * live option chain should be used for current numbers before acting.
 */
import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import { getSession, hasAdvancedAccess } from '@/lib/auth';

// ── Shared components (matches learn.tsx / watchlist-rotation-explainer.tsx conventions) ────

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

function DataTable({ headers, rows, highlightCol }: { headers: string[]; rows: (string | number)[][]; highlightCol?: number }) {
  return (
    <div style={{ borderRadius: 10, border: '1px solid #1e293b', overflow: 'hidden', marginBottom: 16, overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
        <thead>
          <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
            {headers.map(h => (
              <th key={h} style={{ textAlign: 'left', padding: '8px 12px', color: '#475569', fontWeight: 700, fontSize: 10.5, textTransform: 'uppercase', letterSpacing: 0.4, borderBottom: '1px solid #1e293b', whiteSpace: 'nowrap' }}>
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
                  padding: '8px 12px', whiteSpace: 'nowrap',
                  color: j === highlightCol ? '#e2e8f0' : (j === 0 ? '#e2e8f0' : '#94a3b8'),
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
  { id: 'instruments', label: '1. QQQ vs TQQQ vs QQQM' },
  { id: 'mechanics', label: '2. Why 0.80 delta' },
  { id: 'case-study', label: '3. Live case study & math' },
  { id: 'small-account', label: '4. $10k / $20k account' },
  { id: 'execution', label: '5. Execution playbook' },
  { id: 'income', label: '6. Income overlay' },
  { id: 'risks', label: '7. Risks' },
  { id: 'decision', label: '8. Decision framework' },
  { id: 'checklist', label: '9. Pre-execution checklist' },
];

// ── Main page ─────────────────────────────────────────────────────────────────

export default function QqqLeapsPlaybookPage() {
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
          QQQ LEAPS Playbook
        </h1>
        <p style={{ fontSize: 13, color: '#64748b', maxWidth: 700 }}>
          A structural (not predictive) framework for using long-dated deep-in-the-money QQQ calls
          as a capital-efficient substitute for owning shares — with real math, a live case study,
          and a dedicated worked example for a $10,000–$20,000 account.
        </p>
      </div>

      <Callout tone="warn" title="Read this before anything else">
        This is an <strong style={{ color: '#e2e8f0' }}>educational framework</strong>, not financial
        advice — not from a licensed advisor. LEAPS involve real capital and multi-year risk, and the
        right choice depends on your capital base, tax situation, income needs, and risk tolerance,
        none of which this page can assess for you.
        <br /><br />
        Every price/greek/premium number below is a <strong style={{ color: '#e2e8f0' }}>snapshot from
        2026-09-04</strong> and will be stale by the time you read this — re-pull the live chain
        (this platform&apos;s Options Game Plan) before any real decision. The <em>math and mechanics</em>{' '}
        are what to take away, not the specific dollar figures.
        <br /><br />
        This platform&apos;s own AI BUY signals currently measure <strong style={{ color: '#f87171' }}>negative
        edge</strong> — do not use platform signals to time entry into this strategy. Everything below
        rests on structural mechanics (delta, decay, leverage), never on predicting direction.
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

      {/* ── 1. Instruments ─────────────────────────────────────────────────── */}
      <Section id="instruments" title="1. Which instrument? QQQ vs TQQQ vs QQQM">
        <p style={{ marginBottom: 14 }}>
          <strong style={{ color: '#e2e8f0' }}>Direct answer: for a LEAPS strategy, QQQ is the only
          viable choice of the three</strong> — but the reason is <em>options liquidity</em>, not
          the usual &quot;leveraged ETFs decay away&quot; warning. Measured data (below) partially
          contradicts that warning.
        </p>

        <SubSection title="Measured comparison — all three, same 3-year window (2023-09-06 → 2026-09-04, ~752 trading days)">
          <DataTable
            headers={['Metric', 'QQQ', 'QQQM', 'TQQQ']}
            highlightCol={1}
            rows={[
              ['Last close', '$718.96', '$295.51', '$72.03'],
              ['Total return (3y)', '+95.2%', '+95.2%', '+261.1%'],
              ['CAGR', '25.0%', '25.0%', '53.4%'],
              ['Annualized volatility', '20.5%', '20.3%', '60.8%'],
              ['Worst single day', '−6.21%', '−6.11%', '−18.31%'],
              ['Max drawdown', '−22.77%', '−22.70%', '−58.04%'],
              ['Expense ratio', '0.20%', '0.15%', '0.88%'],
              ['Options market', 'Deepest in world', 'Thin / unusable', 'Liquid but short-dated'],
              ['LEAPS to Dec-2028', 'Yes', 'No', 'Available, inadvisable'],
            ]}
          />
        </SubSection>

        <SubSection title="TQQQ did not decay away as much as the textbook warning implies">
          <p style={{ marginBottom: 10 }}>
            TQQQ&apos;s actual 3-year return was <Code>+261.1%</Code> vs. a naive &quot;3× QQQ&apos;s
            total return&quot; of <Code>+285.6%</Code> — a ratio of <strong style={{ color: '#e2e8f0' }}>0.94</strong>.
            Daily returns tracked almost perfectly (2.96× QQQ&apos;s daily move, correlation 0.9998).
            Volatility decay cost roughly <strong style={{ color: '#e2e8f0' }}>6% over three years</strong> —
            real, but not catastrophic, over this specific window.
          </p>
          <Callout tone="warn" title="Why the warning still has teeth">
            This 3-year window was an almost uninterrupted bull market. Leveraged-ETF decay is a{' '}
            <strong>choppy/sideways-market</strong> phenomenon, and this window contains almost no
            such period. The 2000–2013 Nasdaq stretch is the scenario that destroys leveraged ETFs,
            and it is entirely absent from this measurement. The data cannot tell you what TQQQ does
            across a sideways decade, because it hasn&apos;t seen one recently.
          </Callout>
        </SubSection>

        <SubSection title="So why is QQQ still the answer for LEAPS specifically?">
          <p style={{ marginBottom: 10 }}>The case against TQQQ LEAPS doesn&apos;t rest on decay. It rests on three things:</p>
          <StepList steps={[
            <><strong style={{ color: '#e2e8f0' }}>Leverage-on-leverage.</strong> A 0.80-delta LEAP already provides ~2.7× leverage. On TQQQ that compounds to <strong style={{ color: '#f87171' }}>~8× effective Nasdaq-100 exposure</strong>. The measured −58% TQQQ drawdown becomes a near-total loss on a leveraged claim against it — and unlike shares, an option can expire worthless before recovery arrives.</>,
            <><strong style={{ color: '#e2e8f0' }}>Cost of the option.</strong> TQQQ&apos;s 60.8% realized volatility means far higher implied volatility, therefore far more time-value to pay for and decay away.</>,
            <><strong style={{ color: '#e2e8f0' }}>Path dependency multiplies.</strong> Two path-dependent instruments stacked (daily reset × option expiry) makes outcomes extremely sensitive to sequence, not just direction. You can be right about the Nasdaq&apos;s destination and still lose everything on timing.</>,
          ]} />
          <Callout tone="good" title="The refined rule">
            <strong>TQQQ shares</strong> are a defensible, aggressive way to express a bull view — the
            data supports that, if sized for a real −60% drawdown and never on margin.{' '}
            <strong style={{ color: '#f87171' }}>TQQQ LEAPS are not</strong> — the option&apos;s expiry
            removes the one thing that let TQQQ shares recover from −56.8% peak-to-trough: unlimited
            time.
          </Callout>
        </SubSection>

        <SubSection title="QQQM — same index, cheaper, but not for options">
          <p>
            QQQM tracked identically to QQQ (+95.2%, matching to within rounding) at a lower 0.15%
            expense ratio — genuinely the better <em>share</em> vehicle. But its options market is far
            too thin for LEAPS (wide spreads, negligible open interest).{' '}
            <strong style={{ color: '#e2e8f0' }}>You cannot execute this strategy in QQQM.</strong>{' '}
            Practical split: <strong style={{ color: '#e2e8f0' }}>QQQM for shares, QQQ for options</strong> — not
            competitors, different jobs.
          </p>
        </SubSection>

        <SubSection title="Decision matrix">
          <DataTable
            headers={['Goal', 'Best instrument', 'Why']}
            rows={[
              ['Cheap long-term core holding', 'QQQM', 'Identical performance, lowest fee'],
              ['LEAPS / any options strategy', 'QQQ', 'Only one with real options liquidity'],
              ['Covered-call income overlay', 'QQQ', 'Needs the liquid chain'],
              ['Aggressive bull, shares only', 'TQQQ (caution)', 'Data supports it — hard drawdown tolerance, no expiry pressure'],
              ['Aggressive bull via options', 'QQQ LEAP', '~2.7× leverage with a defined max loss'],
              ['Lower-volatility alternative', 'SPY', 'Broader (500 holdings), less tech-concentrated'],
            ]}
          />
        </SubSection>
      </Section>

      {/* ── 2. Mechanics ───────────────────────────────────────────────────── */}
      <Section id="mechanics" title="2. Why 0.80 delta — the mechanics">
        <p style={{ marginBottom: 14 }}>At 0.80 delta you&apos;re buying a <strong style={{ color: '#e2e8f0' }}>stock substitute</strong>, not a lottery ticket:</p>
        <ul style={{ margin: '0 0 14px', paddingLeft: 20 }}>
          <li style={{ marginBottom: 6 }}>Moves like stock: +$1 in QQQ ≈ +$0.80 in the option</li>
          <li style={{ marginBottom: 6 }}>Mostly intrinsic value — little premium at risk from time decay</li>
          <li style={{ marginBottom: 6 }}>High probability of finishing in-the-money (delta ≈ a rough probability of expiring ITM)</li>
          <li style={{ marginBottom: 6 }}>Defined maximum loss — unlike shares on margin, you cannot lose more than the premium paid</li>
          <li style={{ marginBottom: 6 }}>No margin calls, no forced liquidation</li>
        </ul>
        <p style={{ marginBottom: 14 }}>The tradeoffs, stated plainly: no dividends forgone (~0.55%/yr on QQQ), real time decay (quantified in §3), total loss possible if QQQ finishes below the strike, and the extrinsic value you pay <em>is</em> the price of the leverage — not free.</p>
        <SubSection title="Why not other deltas">
          <DataTable
            headers={['Delta', 'Behavior', 'Verdict']}
            rows={[
              ['0.50 (at-the-money)', '~50% of premium is extrinsic; heavy theta', 'Speculation, not substitution'],
              ['0.70', 'More leverage, more decay, lower P(finish ITM)', 'Acceptable if you accept more risk'],
              ['0.80', 'Balanced — leverage + high P(finish ITM)', 'The standard choice'],
              ['0.90', 'Very stock-like, but ties up much more capital', 'Diminishing benefit'],
            ]}
          />
        </SubSection>
      </Section>

      {/* ── 3. Case study ──────────────────────────────────────────────────── */}
      <Section id="case-study" title="3. Live case study — real numbers (snapshot 2026-09-04)">
        <SubSection title="The chain, around 0.80 delta — Jan-2028 expiry (1.38 years out)">
          <p style={{ marginBottom: 10 }}>QQQ spot at the time: <strong style={{ color: '#e2e8f0' }}>$718.96</strong></p>
          <DataTable
            headers={['Strike', 'Mid', 'IV', 'Delta', 'Extrinsic', 'Open interest']}
            highlightCol={0}
            rows={[
              ['$520', '$238.91', '42.4%', '0.834', '~$40', '223'],
              ['$550 ✅', '$214.00', '40.6%', '0.811', '$45.04', '3,212'],
              ['$575', '$193.97', '38.8%', '0.790', '$50.00', '697'],
              ['$600', '$174.69', '37.1%', '0.766', '$55.73', '5,081'],
            ]}
          />
          <p style={{ fontSize: 12, color: '#64748b' }}>The 0.80-delta strike is ~$550 for Jan-2028 (delta 0.811, open interest 3,212 — good liquidity).</p>
        </SubSection>

        <SubSection title="The economics of that specific contract">
          <Mono>{`QQQ Jan-2028 $550 Call @ $214.00 mid

Cost per contract           $21,400
vs. 100 shares               $71,896
Capital freed                $50,496   (70% less capital)

Intrinsic value              $168.96
Extrinsic (time value)        $45.04    (6.3% of spot)
Effective leverage             2.72×
Breakeven at expiry           $764.00   (+6.3% from spot)

Annualized decay cost         $32.64/yr  =  4.54% of notional/yr`}</Mono>
          <p style={{ marginBottom: 10 }}>
            <strong style={{ color: '#e2e8f0' }}>How the leverage number is computed:</strong>{' '}
            <Code>effective leverage = (delta × spot) / premium paid</Code> — here{' '}
            <Code>(0.811 × $718.96) / $214.00 = 2.72×</Code>. A $1 move in QQQ moves this contract by
            ~$0.81, and $0.81 is 2.72× larger as a fraction of the $214 you paid than a $1 move is as
            a fraction of the $718.96 a full share costs.
          </p>
          <Callout tone="info" title="Read the decay number carefully">
            <strong>4.54% per year of the notional you control</strong> is the true cost of this
            leverage. Compare: a margin loan typically runs 6–13%/yr <em>plus</em> margin-call risk;
            this LEAP runs ~4.5%/yr with a defined max loss and no margin calls. That comparison is
            genuinely favorable — but it means <strong>QQQ must appreciate more than ~4.5%/yr just to
            match holding shares outright</strong>, and over 1.38 years you need +6.3% just to break
            even at expiry.
          </Callout>
        </SubSection>

        <SubSection title="Three price-path outcomes at expiry (Jan-2028) — $21,400 invested either way">
          <DataTable
            headers={['QQQ path over 1.38y', 'QQQ price at expiry', 'Shares ($21,400 invested, 29.8 shares)', 'LEAP ($550 strike, 1 contract, $21,400 cost)']}
            rows={[
              ['Flat (0%)', '$718.96', '$21,400 (no change)', '$16,896 intrinsic → −21.0% on premium'],
              ['+25%', '$898.70', '$26,750 (+25%)', '$34,870 intrinsic → +62.9% on premium'],
              ['−25%', '$539.22', '$16,050 (−25%)', 'Below $550 strike → expires worthless → −100% on premium'],
            ]}
          />
          <p style={{ fontSize: 12, color: '#64748b', marginBottom: 10 }}>
            LEAP intrinsic value at expiry = <Code>max(QQQ price − $550, 0) × 100 shares/contract</Code>.
            Flat row: <Code>($718.96 − $550) × 100 = $16,896</Code>, a loss of{' '}
            <Code>$21,400 − $16,896 = $4,504</Code> = <strong style={{ color: '#f87171' }}>−21.0% on
            premium</strong>. +25% row: <Code>($898.70 − $550) × 100 = $34,870</Code>, a gain of{' '}
            <Code>$34,870 − $21,400 = $13,470</Code> = <strong style={{ color: '#22c55e' }}>+62.9% on
            premium</strong>.
          </p>
          <Callout tone="example" title="The single most important number on this page">
            The <strong>Flat</strong> row is the one to sit with: QQQ went nowhere over 1.38 years and
            the LEAP still lost <strong style={{ color: '#f87171' }}>21% of its premium</strong> — that
            is the 4.54%/yr decay cost from the previous section, compounding. Shares have a roughly
            1:1, symmetric payoff with zero decay. The LEAP has a leveraged, asymmetric one: bigger
            gains on a real rally, a guaranteed decay cost if QQQ is flat, and a total loss if QQQ
            finishes below $550 — the true breakeven is <strong style={{ color: '#e2e8f0' }}>$764.00</strong>{' '}
            (spot + the $45.04 extrinsic paid), not the $718.96 spot price itself.
          </Callout>
        </SubSection>
      </Section>

      {/* ── 4. Small account case study ────────────────────────────────────── */}
      <Section id="small-account" title="4. Case study: a $10,000 or $20,000 account">
        <Callout tone="warn" title="The core problem at this size">
          One $550-strike LEAP above costs <strong style={{ color: '#e2e8f0' }}>$21,400</strong> — that
          alone is <strong style={{ color: '#f87171' }}>107–214% of a $10k–$20k account</strong>. The
          source playbook&apos;s own position-sizing rule (never put more than ~10–15% of a portfolio
          into LEAPS premium) rules this exact contract out entirely at this account size. The honest
          answer to &quot;do I just buy QQQ, or TQQQ, or a LEAP&quot; at $10-20k is:{' '}
          <strong style={{ color: '#e2e8f0' }}>a full-size 0.80-delta LEAP is the wrong tool for this
          account size</strong> — not because the math is different, but because one contract is
          undiversifiable lottery-ticket sizing at $10-20k, no matter how conservative the delta.
        </Callout>

        <SubSection title="Position-sizing table, extended down to $10k/$20k">
          <DataTable
            headers={['Account size', 'Max LEAPS premium (10–15% rule)', 'Contracts @ $21,400', 'Realistic action']}
            highlightCol={0}
            rows={[
              ['$10,000', '$1,000–1,500', '0 — cannot buy even 1/10th of a contract at this notional', 'Shares/QQQM only, or a much cheaper lower-delta LEAP (see below)'],
              ['$20,000', '$2,000–3,000', '0 (still under 15% of one contract)', 'Shares/QQQM only, or a much cheaper lower-delta LEAP (see below)'],
              ['$50,000', '$5,000–7,500', 'Under 1 — use shares/QQQM instead', 'Same conclusion as the source playbook’s own smallest bracket'],
              ['$100,000', '$10,000–15,000', '0–1 (tight)', 'Marginal — only with strong conviction and a written exit plan'],
              ['$250,000', '$25,000–37,500', '1', 'The smallest account size this exact $550-strike contract fits comfortably'],
            ]}
          />
        </SubSection>

        <SubSection title="What actually works at $10k–$20k — three real options, with math">
          <p style={{ marginBottom: 14 }}>
            Three genuinely different ways to deploy $10,000 (the same math scales linearly for
            $20,000 — just double every dollar figure and share/contract count below).
          </p>

          <SubSection title="Option A — QQQ or QQQM shares outright (no leverage)">
            <Mono>{`$10,000 / $718.96 per QQQ share  =  13.9 shares (13 whole shares, $9,346 deployed)
$10,000 / $295.51 per QQQM share =  33.8 shares (33 whole shares, $9,752 deployed)

Leverage:                1.0×  (none)
Max loss:                 100% of capital, only if QQQ → $0 (never happened, ever)
Dividend:                  ~0.55%/yr, paid
Time decay:                none
Margin calls:              none (assuming no margin used)`}</Mono>
            <p>
              <strong style={{ color: '#e2e8f0' }}>Verdict: the correct default at this account size.</strong>{' '}
              No expiry, no decay, dividends included, and the entire position moves 1:1 with the
              index. QQQM is strictly better here (same exposure, lower fee) unless you specifically
              want QQQ&apos;s slightly deeper liquidity for some other reason.
            </p>
          </SubSection>

          <SubSection title="Option B — a cheaper, lower-delta LEAP sized to actually fit">
            <p style={{ marginBottom: 10 }}>
              A 0.80-delta LEAP is the wrong <em>delta</em> for this account size, but a LEAP is not
              automatically wrong — a further-out-of-the-money, lower-delta contract costs far less
              per contract. Illustrative math using the same Jan-2028 chain (deltas from the same
              live snapshot, so treat the exact strike as illustrative and re-pull the real chain):
            </p>
            <DataTable
              headers={['Strike', 'Approx. mid', 'Approx. delta', 'Effective leverage', '% of $10k account (1 contract)']}
              rows={[
                ['$650 (further OTM)', '~$110', '~0.55', '(0.55 × $718.96)/$110 ≈ 3.6×', '110%'],
                ['$700 (near-the-money)', '~$85', '~0.45', '(0.45 × $718.96)/$85 ≈ 3.8×', '85%'],
              ]}
            />
            <Callout tone="warn" title="Even the cheapest realistic contract is still ~85-110% of a $10k account">
              This is the honest conclusion, not a workaround: <strong style={{ color: '#e2e8f0' }}>at
              $10k, there is no strike that both fits the 10–15% sizing rule AND behaves like the
              stock-substitute strategy described in this page.</strong> A lower-delta contract that
              does fit the dollar budget (e.g. a $850+ strike costing $2,000 or less) is a genuinely
              different, far more speculative trade — high leverage, low P(finish in the money), heavy
              extrinsic value, closer to the &quot;0.50 delta = speculation&quot; row in §2&apos;s
              table than to this page&apos;s core strategy. Don&apos;t relabel that trade as
              &quot;LEAPS&quot; and size it like the disciplined version above.
            </Callout>
            <p>
              At <strong style={{ color: '#e2e8f0' }}>$20,000</strong>, the $650-strike contract
              (~$11,000) fits inside the 10-15% band only if you round up to ~55% max, which still
              breaks the rule — the conclusion is the same at $20k, just slightly less extreme.
            </p>
          </SubSection>

          <SubSection title="Option C — TQQQ shares as the leverage lever instead of options">
            <p style={{ marginBottom: 10 }}>
              If the actual goal is &quot;more upside than plain QQQ shares, sized to fit $10-20k,&quot;{' '}
              <strong style={{ color: '#e2e8f0' }}>TQQQ shares</strong> — not LEAPS on anything — are
              the instrument built for this. No minimum lot size, no strike/delta selection, no
              expiry.
            </p>
            <Mono>{`$10,000 / $72.03 per TQQQ share  =  138.8 shares (138 whole shares, $9,940 deployed)

Effective leverage:        ~3× QQQ's DAILY move (measured 2.96×, r=0.9998 — very close to advertised)
Max loss:                   100% of capital (measured max drawdown so far: −58.04%, not 100%,
                              but a share position can go to zero in theory — an option cannot
                              go BELOW zero either, so this is not actually an asymmetry vs. Option B)
Dividend:                   effectively none (fund distributes negligibly)
Time decay:                 none in the options sense — cost shows up as daily-reset volatility
                              drag instead, measured at ~6% over the 3-year window in §1
Margin calls:                only if bought ON MARGIN — the hard rule below is never do this`}</Mono>
            <Callout tone="good" title="Why this is the most honest 'more leverage' answer at this account size">
              Unlike Option B, there is no forced choice between &quot;wrong delta&quot; and
              &quot;wrong dollar size&quot; — you buy exactly as many TQQQ shares as your budget
              allows, at any account size, with no expiry ever forcing a decision. The tradeoff is the
              one already measured in §1: real, if usually mild, volatility decay, and a genuinely
              larger measured drawdown (−58% vs QQQ&apos;s −23%) that a share position, unlike an
              option, can always outlast given enough time and no margin.
            </Callout>
          </SubSection>
        </SubSection>

        <SubSection title="Suggested $10,000 allocation (illustrative, not a recommendation)">
          <Mono>{`CORE (70%)   $7,000 in QQQM shares  ≈ 23 shares      — permanent, dividend-paying, zero decay
TQQQ (15%)   $1,500 in TQQQ shares ≈ 20 shares      — the leverage lever, shares only, never on margin
CASH (15%)   $1,500 held back                       — dry powder for the drawdown that will eventually come`}</Mono>
          <p style={{ marginBottom: 10 }}>
            For <strong style={{ color: '#e2e8f0' }}>$20,000</strong>, double every figure:
            $14,000 QQQM / $3,000 TQQQ / $3,000 cash.
          </p>
          <p>
            <strong style={{ color: '#e2e8f0' }}>Where the LEAP re-enters:</strong> once the account
            crosses roughly <Code>$100,000–150,000</Code> (the point where one $21.4k-class contract
            is back inside the 10-15% sizing band), revisit §3&apos;s disciplined 0.80-delta structure
            instead of the TQQQ-shares substitute above.
          </p>
        </SubSection>
      </Section>

      {/* ── 5. Execution playbook ──────────────────────────────────────────── */}
      <Section id="execution" title="5. Execution playbook (for accounts large enough for a real LEAP — see §4 first if you're under ~$100k)">
        <SubSection title="Step 1 — Position sizing (do this first, every time)">
          <p><strong style={{ color: '#e2e8f0' }}>Rule: never allocate more to LEAPS premium than you can afford to lose entirely.</strong> If one contract exceeds ~15% of your portfolio, this strategy is too large for you right now — use shares/QQQM instead (see §4). This is the most common way people get hurt with this strategy.</p>
        </SubSection>
        <SubSection title="Step 2 — Expiry selection">
          <p>Target <strong style={{ color: '#e2e8f0' }}>12–24 months to expiry.</strong> Under 12 months, theta accelerates meaningfully. Over 24 months, spreads widen and liquidity thins. Prefer January expiries — they carry the deepest open interest by market convention.</p>
        </SubSection>
        <SubSection title="Step 3 — Strike selection">
          <StepList steps={[
            'Pull the live chain — never reuse the numbers in this page, they are a stale snapshot',
            'Find the strike with delta 0.78–0.82',
            'Sanity check: strike should sit roughly 20–25% below spot for a ~1.4-year LEAP',
            <>Require open interest &gt; 500 (the $550/$600 examples in §3 both qualified)</>,
            <>Check the bid/ask spread is under ~3% of the mid price</>,
          ]} />
        </SubSection>
        <SubSection title="Step 4 — Order execution">
          <StepList steps={[
            'Always use limit orders — never market-order a LEAP',
            'Start at the mid price, walk up in $0.05–0.10 increments',
            <>Avoid the first/last 30 minutes of the trading day — spreads are widest then</>,
            'Consider legging in across 2–3 tranches rather than all at once',
          ]} />
        </SubSection>
        <SubSection title="Step 5 — Ongoing management">
          <DataTable
            headers={['Trigger', 'Action']}
            rows={[
              ['Delta drifts above 0.90', 'Consider rolling up — take profit, reset leverage'],
              ['Delta drifts below 0.60', 'Position has gone against you — reassess the thesis, don’t average down reflexively'],
              ['Under 9 months to expiry', 'Roll out to a further expiry — theta accelerates from here'],
              ['Down 40–50% on premium', 'Pre-committed exit point — decide this BEFORE entering, not under stress'],
              ['Up 80–100%', 'Consider taking partial profit or rolling up'],
            ]}
          />
          <Callout tone="info" title="Set these rules before you enter, in writing">
            Deciding under stress is how positions turn into disasters. Write the exit plan down
            before the trade exists, not after it starts moving against you.
          </Callout>
        </SubSection>
      </Section>

      {/* ── 6. Income overlay ──────────────────────────────────────────────── */}
      <Section id="income" title="6. Income overlay — the poor man's covered call">
        <p style={{ marginBottom: 14 }}>Once you own a LEAP large enough to fit your account (§5), sell short-dated out-of-the-money calls against it — a diagonal spread commonly called a Poor Man&apos;s Covered Call:</p>
        <Mono>{`LONG   QQQ Jan-2028 $550 Call   (delta ~0.81)         ← the LEAP position
SHORT  QQQ ~30-45 DTE OTM Call  (delta 0.20-0.30)      ← income leg, sold repeatedly`}</Mono>
        <SubSection title="Why this matters, especially if income is a goal">
          <ul style={{ margin: '0 0 14px', paddingLeft: 20 }}>
            <li style={{ marginBottom: 6 }}>Harvests the volatility risk premium — a structurally positive, well-documented edge</li>
            <li style={{ marginBottom: 6 }}>Requires no directional prediction (unlike this platform&apos;s BUY signals, which currently measure negative edge)</li>
            <li style={{ marginBottom: 6 }}>Can meaningfully offset — potentially fully — the LEAP&apos;s ~4.5%/yr decay cost from §3</li>
            <li style={{ marginBottom: 6 }}>This platform already surfaces the strikes/expiries/premiums/IV rank needed for the short leg, via the Options Game Plan</li>
          </ul>
        </SubSection>
        <SubSection title="Critical rules">
          <StepList steps={[
            <>Short strike must exceed <Code>long strike + net debit paid</Code>, or you can lock in a structural loss</>,
            'Sell 30–45 days to expiry (best theta-to-risk ratio)',
            'Target 0.20–0.30 delta on the short leg',
            <>Sell when IV rank is high — the platform tracks <Code>iv_rank_1y</Code> for exactly this</>,
            'Roll the short strike up/out if QQQ rallies hard through it',
          ]} />
          <Callout tone="good" title="Realistic expectation">
            0.5–1.5% per month on the short leg in normal conditions — plausibly enough to cover the
            LEAP&apos;s entire decay cost, which is precisely the point of running this overlay.
          </Callout>
        </SubSection>
      </Section>

      {/* ── 7. Risks ────────────────────────────────────────────────────────── */}
      <Section id="risks" title="7. Risks — stated plainly">
        <DataTable
          headers={['Risk', 'Reality']}
          rows={[
            ['Total loss', 'If QQQ finishes below the strike at expiry, the contract expires worthless. A repeat of the 2025-04 drawdown (−22.77%) would put a $550 strike near-zero from a $718.96 spot.'],
            ['Leverage cuts both ways', '~2.7× up AND down'],
            ['Time decay', '~4.5%/yr of notional, accelerating in the final year before expiry'],
            ['No dividends', '~0.55%/yr forgone vs. holding shares'],
            ['Concentration', 'QQQ is ~100 stocks, heavily weighted to correlated mega-cap tech'],
            ['IV crush', 'If implied volatility falls, your LEAP loses value even if QQQ is completely flat'],
            ['Liquidity', 'Fine at common round strikes; thin at unusual ones'],
          ]}
        />
        <Callout tone="warn" title="The IV point deserves emphasis">
          At the 2026-09-04 snapshot, implied volatility on the $550 Jan-2028 was 40.6% — roughly
          double QQQ&apos;s realized volatility (20.4%) over the same 3 years. Some premium over
          realized vol is normal (the variance risk premium), but a gap this wide means it was a
          relatively expensive time to <em>buy</em> long-dated options — and correspondingly a more
          attractive time to <em>sell</em> short-dated ones (§6). Check current IV rank before buying;
          if it&apos;s elevated, the income-overlay side of this strategy may fit better than the
          buying side right now.
        </Callout>
      </Section>

      {/* ── 8. Decision framework ───────────────────────────────────────────── */}
      <Section id="decision" title="8. Decision framework">
        <SubSection title="Choose LEAPS if:">
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li style={{ marginBottom: 6 }}>You&apos;re bullish on the Nasdaq-100 over 1–2+ years</li>
            <li style={{ marginBottom: 6 }}>You want capital efficiency (~70% less capital for ~2.7× exposure)</li>
            <li style={{ marginBottom: 6 }}>You can lose 100% of the premium without it affecting your life</li>
            <li style={{ marginBottom: 6 }}>You&apos;ll actively manage rolls and (ideally) the income overlay</li>
            <li style={{ marginBottom: 6 }}>One contract is ≤10–15% of your portfolio (see §4 if it isn&apos;t)</li>
          </ul>
        </SubSection>
        <SubSection title="Choose QQQM shares instead if:">
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li style={{ marginBottom: 6 }}>You want simple, permanent, low-cost exposure</li>
            <li style={{ marginBottom: 6 }}>You want the dividend</li>
            <li style={{ marginBottom: 6 }}>You don&apos;t want to manage expiries and rolls</li>
            <li style={{ marginBottom: 6 }}>One LEAP contract would exceed ~15% of your portfolio — this is the case for any account under ~$100-150k</li>
          </ul>
        </SubSection>
        <SubSection title="Choose neither if:">
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li style={{ marginBottom: 6 }}>You&apos;d need this money within 2 years</li>
            <li style={{ marginBottom: 6 }}>A −60% drawdown on the position would force you to sell</li>
            <li style={{ marginBottom: 6 }}>You can&apos;t monitor the position at least monthly</li>
          </ul>
        </SubSection>
        <SubSection title="On TQQQ specifically">
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li style={{ marginBottom: 6 }}><strong style={{ color: '#f87171' }}>Not for LEAPS</strong> — ~8× effective exposure, and the option&apos;s expiry removes the unlimited recovery time that saved TQQQ shares through the measured −58.04% drawdown (see §1)</li>
            <li style={{ marginBottom: 6 }}><strong style={{ color: '#22c55e' }}>Shares are defensible</strong> if you genuinely tolerate −60%, never use margin, and understand its +53.4% CAGR was earned in an uninterrupted bull market</li>
          </ul>
        </SubSection>
      </Section>

      {/* ── 9. Checklist ────────────────────────────────────────────────────── */}
      <Section id="checklist" title="9. Pre-execution checklist">
        <StepList steps={[
          'Re-pull the live chain — every price above is stale',
          'Confirm delta is 0.78–0.82 at your chosen strike — recompute, don’t assume any strike in this page still applies',
          'Confirm open interest > 500 and spread < 3% of mid',
          'Confirm one contract’s premium is ≤ 10–15% of your total portfolio — if not, see §4’s smaller-account alternatives',
          'Write down an exit plan: profit target, stop level, and roll date (< 9 months to expiry)',
          'Understand and accept that you can lose 100% of the premium',
          'Check current IV rank — is now actually a good time to buy premium, or to sell it instead (§6)?',
          'Not using this platform’s AI BUY signals to time entry — measured negative edge',
        ]} />

        <Callout tone="info" title="Where this connects to the rest of the platform">
          Use the Options Game Plan for real strikes/expiries/premiums/IV rank (especially for the §6
          income leg), and <Code>iv_rank_1y</Code> for timing when to sell premium. Do not use AI BUY
          signals or confidence scores to time LEAP entry or size the position — both are documented
          elsewhere in this platform as currently unreliable for that purpose.
        </Callout>
      </Section>
    </div>
  );
}
