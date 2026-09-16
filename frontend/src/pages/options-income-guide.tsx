/**
 * Options Income Guide — /options-income-guide. Advanced-tier/admin Learning page.
 *
 * Explains the two strategies the Options Income Engine (/options-income, T398) trades —
 * covered calls and cash-secured puts — from first principles, then walks through using the
 * actual page step by step. Reuses the Section/SubSection/Callout/Code/DataTable/StepList
 * conventions already established by qqq-leaps-playbook.tsx / learn.tsx rather than inventing
 * new layout patterns.
 */
import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import { getSession, hasAdvancedAccess } from '@/lib/auth';

// ── Shared components (matches qqq-leaps-playbook.tsx conventions) ──────────

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
            <tr key={i} style={{ borderBottom: i < rows.length - 1 ? '1px solid #131c2e' : 'none' }}>
              {row.map((cell, j) => (
                <td key={j} style={{
                  padding: '8px 12px', color: j === highlightCol ? '#e2e8f0' : '#94a3b8',
                  fontWeight: j === highlightCol ? 700 : 400, whiteSpace: 'nowrap',
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
  { id: 'covered-call', label: '1. What is a covered call?' },
  { id: 'csp', label: '2. What is a cash-secured put?' },
  { id: 'engine', label: '3. How this engine picks trades' },
  { id: 'usage', label: '4. Step-by-step: using the page' },
  { id: 'reading', label: '5. Reading positions & P&L' },
  { id: 'fidelity', label: '6. Placing these trades in Fidelity' },
  { id: 'risks', label: '7. Risks & things to know' },
  { id: 'checklist', label: '8. Checklist before creating a portfolio' },
];

export default function OptionsIncomeGuidePage() {
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
          Options Income Guide
        </h1>
        <p style={{ fontSize: 13, color: '#64748b', maxWidth: 700 }}>
          What covered calls and cash-secured puts actually are, how the Options Income Engine
          picks and manages them, and a step-by-step walkthrough of the{' '}
          <Link href="/options-income" style={{ color: '#818cf8' }}>Options Income</Link> page itself.
        </p>
      </div>

      <Callout tone="warn" title="Read this before anything else">
        This is an <strong style={{ color: '#e2e8f0' }}>educational explanation of a paper-trading
        feature</strong>, not financial advice. Every position this engine opens is simulated —
        no real money or real brokerage order is ever involved, matching every other portfolio on
        this platform. The mechanics (premium, assignment, collateral, yield) are real options
        math; the trades themselves are not.
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

      {/* ── 1. Covered call ─────────────────────────────────────────────────── */}
      <Section id="covered-call" title="1. What is a covered call?">
        <p style={{ marginBottom: 14 }}>
          A covered call means: you own 100 shares of a stock, and you sell someone else the
          <em> right</em> (not the obligation) to buy those shares from you at a fixed price (the
          <b style={{ color: '#e2e8f0' }}> strike</b>) any time before a fixed date (the{' '}
          <b style={{ color: '#e2e8f0' }}>expiry</b>). In exchange for selling that right, you
          collect cash up front — the <b style={{ color: '#e2e8f0' }}>premium</b> — which is yours
          to keep no matter what happens next.
        </p>
        <p style={{ marginBottom: 14 }}>
          This engine specifically writes covered calls as a <b style={{ color: '#e2e8f0' }}>synthetic
          buy-write</b>: it &quot;buys&quot; 100 shares and sells the call at the same moment, purely
          in the portfolio&apos;s own cash ledger — there is no separate stock position sitting
          around waiting to be managed.
        </p>

        <SubSection title="The two ways it can end">
          <DataTable
            headers={['Outcome', 'What happens', 'Your result']}
            rows={[
              ['Stock stays below the strike (OTM)', 'The call expires worthless — the buyer had no reason to exercise it', 'You keep the shares (marked to the market close) AND the premium'],
              ['Stock rises above the strike (assigned)', 'The shares are called away — sold to the option buyer at the strike', 'You keep the premium, but your gain on the shares is capped at the strike, even if the stock kept rising past it'],
            ]}
          />
        </SubSection>

        <Callout tone="example" title="Worked example">
          You write a covered call on a $100 stock, strike $105, collecting $2.00/share ($200 for
          one 100-share contract).
          <br /><br />
          <b>If the stock closes at $103 at expiry</b> (below strike): shares mark to $103, so your
          result is $200 premium + ($103 − $100) × 100 = <span style={{ color: '#4ade80' }}>+$500</span>.
          <br />
          <b>If the stock closes at $130</b> (well above strike): you&apos;re assigned at $105, so
          your result is $200 premium + ($105 − $100) × 100 = <span style={{ color: '#4ade80' }}>+$700</span> —
          a real gain, but far less than the $3,200 you&apos;d have made just holding the shares. That
          gap is the actual cost of selling a covered call: it caps your upside in exchange for
          guaranteed income today.
        </Callout>
      </Section>

      {/* ── 2. Cash-secured put ─────────────────────────────────────────────── */}
      <Section id="csp" title="2. What is a cash-secured put?">
        <p style={{ marginBottom: 14 }}>
          A cash-secured put is the mirror image, and doesn&apos;t require owning any shares to
          start. You sell someone the right to make <em>you</em> buy 100 shares at a fixed strike
          before expiry — and you set aside strike × 100 in cash (the{' '}
          <b style={{ color: '#e2e8f0' }}>collateral</b>) as proof you can actually afford to if
          asked. You collect the premium immediately, same as a covered call.
        </p>

        <SubSection title="The two ways it can end">
          <DataTable
            headers={['Outcome', 'What happens', 'Your result']}
            rows={[
              ['Stock stays above the strike (OTM)', 'The put expires worthless — nobody sells you shares below the current price', 'Your full collateral is released, and you keep the premium as pure profit'],
              ['Stock falls below the strike (assigned)', 'You’re obligated to buy 100 shares at the strike, even though the market price is now lower', 'You keep the premium, but you’re buying above the current market price — a real, immediate paper loss on the shares, partly offset by the premium'],
            ]}
          />
        </SubSection>

        <Callout tone="example" title="Worked example">
          You sell a cash-secured put on a $100 stock, strike $95, collecting $1.50/share ($150),
          reserving $9,500 in collateral.
          <br /><br />
          <b>If the stock closes at $97</b> (above strike): OTM, collateral released in full, result
          is <span style={{ color: '#4ade80' }}>+$150</span> (pure premium).
          <br />
          <b>If the stock closes at $85</b> (below strike): assigned — shares are marked to market
          immediately rather than held (see §3), so your result is $150 premium + ($85 − $95) × 100 = {' '}
          <span style={{ color: '#f87171' }}>−$850</span>. The premium softened the loss, but didn&apos;t
          erase it — a cash-secured put is a real promise to buy at the strike, not a one-sided bet.
        </Callout>

        <Callout tone="info" title="Why sell these instead of just buying/holding?">
          Both strategies trade a capped or delayed outcome for cash collected today, regardless of
          which way the stock moves in between. A covered call is attractive when you think a stock
          is unlikely to blow past your strike soon; a cash-secured put is attractive when you&apos;d
          be happy to own the stock anyway at a lower price than today&apos;s, and want to get paid
          to wait for that price.
        </Callout>
      </Section>

      {/* ── 3. How the engine picks trades ──────────────────────────────────── */}
      <Section id="engine" title="3. How this engine picks trades">
        <SubSection title="The universe">
          The engine only trades a fixed set of liquid, mega-cap/index symbols that this platform
          already archives a full daily option chain for: <Code>SPY QQQ META TSLA AMD NVDA MSFT
          AAPL AMZN PLTR QQQM QLD TQQQ</Code>. It never fetches a live quote per candidate — it
          reads each symbol&apos;s own most recently archived end-of-day chain, the same
          data this platform&apos;s LEAPS tools use.
        </SubSection>

        <SubSection title="The filters every candidate must clear">
          <DataTable
            headers={['Filter', 'Value', 'Why']}
            rows={[
              ['Days to expiry', '14 – 45 days', 'Long enough to collect meaningful premium, short enough that a position doesn’t sit open indefinitely'],
              ['Delta (absolute value)', '0.15 – 0.35', 'Far enough out-of-the-money that assignment is the minority outcome, close enough that the premium is worth collecting'],
              ['Open interest', '≥ 50 contracts', 'A basic liquidity floor — a contract nobody trades has an unreliable quoted price'],
              ['Bid price', '> $0 (real, quoted)', 'The engine only prices a trade off a genuine, currently-quoted bid — never a stale or synthetic price'],
              ['Chain age', '≤ 5 days old', 'If the daily chain-capture job ever silently failed for a while, this stops the engine from pricing trades off stale data against today’s live price'],
            ]}
          />
        </SubSection>

        <SubSection title="How a candidate is ranked">
          Within those filters, every qualifying contract for a symbol gets an{' '}
          <b style={{ color: '#e2e8f0' }}>annualized yield</b>: the premium collected, as a
          percentage of the stock price, scaled up to a one-year rate —{' '}
          <Code>premium ÷ price × (365 ÷ days-to-expiry) × 100</Code>. The single best-yielding
          contract per symbol per strategy is kept; the rest are discarded. Premium is always
          priced at the <b style={{ color: '#e2e8f0' }}>bid</b> — the real, conservative price you&apos;d
          actually receive selling into the market, never the ask or an optimistic mid-price.
        </SubSection>

        <SubSection title="What happens at expiry — always closes, never rolls">
          Every position this engine opens is closed the moment it expires — assigned (per §1/§2&apos;s
          mechanics) or liquidated at that day&apos;s market close if it expired worthless. It never
          rolls a position into a new expiry, and never holds assigned shares indefinitely. This
          keeps every portfolio&apos;s exposure bounded and fully explained by its own currently open
          positions, rather than accumulating an unbounded stock inventory across months of expiries.
        </SubSection>

        <SubSection title="The concentration cap">
          <Callout tone="warn" title="Measured live, on the very first portfolio created">
            A single covered call or cash-secured put on a $500 stock needs $50,000 of collateral
            for just one contract. On a $50,000 portfolio, that trade alone would use the entire
            account — no other position could ever be opened alongside it. To prevent that, no
            single position may use more than <b style={{ color: '#e2e8f0' }}>25% of a portfolio&apos;s
            initial capital</b> as collateral, regardless of how attractive its yield looks.
          </Callout>
          This is exactly why portfolio sizing matters at creation time — see §7&apos;s checklist.
        </SubSection>
      </Section>

      {/* ── 4. Step-by-step usage ───────────────────────────────────────────── */}
      <Section id="usage" title="4. Step-by-step: using the page">
        <StepList steps={[
          <>Open <Link href="/options-income" style={{ color: '#818cf8' }}>Options Income</Link> — found under Admin in the nav.</>,
          <>If no portfolio exists yet, an admin clicks <b style={{ color: '#e2e8f0' }}>+ New Portfolio</b>, gives it a name, and sets its starting capital. See §7 before picking a number — too small a portfolio (relative to the concentration cap) may only ever be able to hold one position.</>,
          <>Check the <b style={{ color: '#e2e8f0' }}>Candidates</b> tab any time — it shows the exact same ranked list the engine itself trades from, filterable to just covered calls or just cash-secured puts, with strike, delta, days-to-expiry, premium, annualized yield, and required collateral for every qualifying contract.</>,
          <>The engine runs automatically once a day, at 19:00 ET (shortly after that day&apos;s option chain finishes archiving) — settling anything that expired, then opening new positions from the ranked candidates, respecting the portfolio&apos;s own cash, concentration cap, and daily-entry limit. No action is needed for this to happen.</>,
          <>An admin can click <b style={{ color: '#e2e8f0' }}>Run Step Now</b> to trigger that same cycle immediately instead of waiting for the daily schedule — useful right after creating a new portfolio, or when testing.</>,
          <>Use <b style={{ color: '#e2e8f0' }}>Open Positions</b> to see everything currently held, and <b style={{ color: '#e2e8f0' }}>Closed Positions</b> to see the full history of what was assigned vs. expired worthless, with realized P&amp;L on each — see §5.</>,
        ]} />
      </Section>

      {/* ── 5. Reading positions & P&L ───────────────────────────────────────── */}
      <Section id="reading" title="5. Reading positions & P&L">
        <SubSection title="The summary cards">
          <DataTable
            headers={['Card', 'What it means']}
            rows={[
              ['Current Equity', 'Cash plus the current value of everything still open'],
              ['Total Return', 'Current equity vs. the portfolio’s starting capital'],
              ['Win Rate', 'Share of CLOSED positions with a positive P&L'],
              ['Assignment Rate', 'Share of CLOSED positions that ended in assignment rather than expiring worthless'],
              ['Cash Available', 'Liquid cash not currently reserved as collateral on an open position'],
              ['Premium Collected', 'Total premium received across every position, open and closed — the actual income this engine has generated'],
            ]}
          />
        </SubSection>
        <SubSection title="On the Closed Positions table">
          <b style={{ color: '#e2e8f0' }}>Assigned</b> shows whether that specific position ended in
          assignment (per §1/§2). <b style={{ color: '#e2e8f0' }}>P&amp;L</b> is the total dollar
          result including the premium collected — not just the premium alone. <b style={{ color: '#e2e8f0' }}>Return</b>{' '}
          is that P&amp;L as a percentage of the collateral the position actually tied up, which is
          the fair way to compare a small cash-secured put against a much larger covered call.
        </SubSection>
      </Section>

      {/* ── 6. Placing these trades in Fidelity ─────────────────────────────── */}
      <Section id="fidelity" title="6. Placing these trades in Fidelity">
        <Callout tone="info" title="This platform doesn't place real trades">
          The Options Income Engine is entirely simulated — it never touches a real brokerage
          account. If you find a candidate on the <Link href="/options-income" style={{ color: '#818cf8' }}>Candidates</Link>{' '}
          tab you want to actually put on, you place it yourself. The steps below cover
          Fidelity&apos;s standard order-entry flow (fidelity.com and Active Trader Pro) as of how
          it&apos;s worked for years — Fidelity can and does move buttons around, so treat this as
          the shape of the process, not a pixel-exact walkthrough, and check Fidelity&apos;s own
          current help pages if anything on screen looks different.
        </Callout>

        <SubSection title="Before you start — account requirements">
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li style={{ marginBottom: 8 }}><b style={{ color: '#e2e8f0' }}>Options approval.</b> Both strategies here are covered calls and cash-secured puts — Fidelity&apos;s most basic options approval tier (Level 1) covers both. You apply for this once, from Accounts &amp; Trade → Trade → Options, if you haven&apos;t already; approval is usually near-instant for this tier.</li>
            <li style={{ marginBottom: 8 }}><b style={{ color: '#e2e8f0' }}>A covered call needs the shares first</b> (or a combined order that buys them in the same trade — see below), 100 shares per contract you plan to sell.</li>
            <li><b style={{ color: '#e2e8f0' }}>A cash-secured put needs the full cash</b> — strike × 100 × contracts — sitting available as settled cash, not margin. Fidelity checks and reserves this automatically when you submit the order; if you don&apos;t have enough, it will reject the order rather than let you accidentally use margin.</li>
          </ul>
        </SubSection>

        <SubSection title="Order-type vocabulary — this part is universal, not Fidelity-specific">
          <Callout tone="warn" title="If your chain only shows Bid/Ask columns">
            You will not necessarily see the words &quot;Sell to Open&quot; or &quot;Buy Write&quot;
            printed anywhere on the chain itself — on most brokers, including Fidelity, those are
            just the two directions any order ends up as, decided by <em>which price you click</em>{' '}
            and what a follow-up ticket asks you to confirm, not a menu you pick from inside the
            chain. The chain&apos;s job is only to show you the current bid and ask for every
            strike; the buy/sell decision happens in the order ticket that opens once you click one
            of those numbers.
          </Callout>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li style={{ marginBottom: 8 }}><b style={{ color: '#e2e8f0' }}>Open vs. Close</b> — &quot;Open&quot; means you don&apos;t currently hold this exact contract and are starting a new position; &quot;Close&quot; means you already hold it and are exiting. Every trade in this guide is an Open, since the engine (and you, following it) is always starting from nothing on that specific contract.</li>
            <li><b style={{ color: '#e2e8f0' }}>Buy vs. Sell</b> — a covered call and a cash-secured put are both <b style={{ color: '#e2e8f0' }}>sells</b>: you&apos;re the one collecting the premium, not paying it (see the Option Trading Guide&apos;s <Link href="/option-trading-guide#bid-vs-ask" style={{ color: '#818cf8' }}>bid/ask section</Link> for the full buy-pays-the-ask / sell-receives-the-bid rule).</li>
          </ul>
        </SubSection>

        <SubSection title="Reading the option chain for delta">
          Somewhere on Fidelity&apos;s option chain view there is usually a way to add a{' '}
          <b style={{ color: '#e2e8f0' }}>Delta</b> column (often a gear/settings icon, or a
          &quot;Greeks&quot; toggle above the chain) — that&apos;s the same number the engine
          filters on (§3). Look for calls/puts with delta between roughly 0.15 and 0.35 in absolute
          value, and an expiration 2-6 weeks out, to match what the engine itself would consider.
          If you can&apos;t find a delta column at all, Fidelity&apos;s own chain help/tooltip
          (often a &quot;?&quot; icon near the column headers) will show what&apos;s available in
          your current view.
        </SubSection>

        <SubSection title="Step-by-step: covered call (already own 100+ shares)">
          <StepList steps={[
            'Find the stock’s option chain (from its quote/research page, or a dedicated “Trade Options” / “Options” area of the site) and locate the call strike you want.',
            'Click directly on that call’s BID price — clicking the bid is what starts a SELL order (this is the mechanic behind “Sell to Open”; you don’t need to find that exact wording anywhere).',
            'A trade ticket opens. Confirm it shows: this stock, this call, this strike/expiry, and that you’re opening a new sell (some tickets ask you to confirm “Open” explicitly if you also happen to hold the same contract already — unlikely the first time).',
            'Set Quantity to the number of contracts (1 contract per 100 shares you own).',
            'Set the order to a Limit at or near that same bid price you clicked (see the pricing note below) rather than a Market order.',
            'Set Time in Force (Day is fine to start; GTC lets it sit open across sessions).',
            'Review the order preview — it will show the premium you’ll collect and confirm you have enough shares to cover it — then submit.',
          ]} />
        </SubSection>

        <SubSection title="If you don't already own the shares">
          <p style={{ marginBottom: 12 }}>
            A &quot;Buy Write&quot; is just a name for buying the shares and selling the call in one
            combined order instead of two separate ones — some brokers expose this as its own order
            type (often found by searching &quot;buy write&quot; in the trade/order-type menu, or a
            &quot;strategy&quot; selector near the ticket), others don&apos;t offer it at all. If you
            don&apos;t see one, the two-step equivalent works exactly the same:
          </p>
          <StepList steps={[
            'Place a normal stock order: Buy 100 shares (or 100 × however many contracts you plan to sell).',
            'Once that fills, go back to the option chain and follow the covered-call steps above — click the call’s bid to sell it.',
          ]} />
        </SubSection>

        <SubSection title="Step-by-step: cash-secured put">
          <StepList steps={[
            'Confirm you have the full strike × 100 × contracts in settled cash available (check your account balance first if unsure).',
            'Find the stock’s option chain and locate the put strike you want.',
            'Click directly on that put’s BID price — clicking the bid starts a SELL order, the same mechanic as the covered call above.',
            'A trade ticket opens. Confirm it shows this stock, this put, this strike/expiry, and that you’re opening a new sell.',
            'Set Quantity to the number of contracts (1 contract = a commitment to buy 100 shares if assigned).',
            'Set the order to a Limit at or near the bid price you clicked.',
            'Set Time in Force (Day or GTC).',
            'Review the order preview — it will show the cash that will be reserved as collateral — then submit.',
          ]} />
        </SubSection>

        <Callout tone="good" title="Pricing tip — match the engine's own convention">
          The engine always prices a candidate at the <b style={{ color: '#e2e8f0' }}>bid</b> — the
          real, conservative price a seller actually receives — never the ask or the mid. Setting
          your own limit order at or very near the displayed bid mirrors that and all but guarantees
          a fill; pricing further toward the mid might collect a little more premium, at the cost of
          the order possibly sitting unfilled.
        </Callout>

        <SubSection title="What happens after you submit">
          Nothing else to do until expiry. If the option finishes out-of-the-money, it simply
          expires and your shares (covered call) or cash (cash-secured put) are untouched beyond
          the premium you already collected. If it finishes in-the-money, Fidelity handles
          assignment automatically overnight — no action needed on your part, matching how this
          engine itself always closes at expiry rather than rolling (§3).
        </SubSection>
      </Section>

      {/* ── 7. Risks ─────────────────────────────────────────────────────────── */}
      <Section id="risks" title="7. Risks & things to know">
        <ul style={{ margin: 0, paddingLeft: 20 }}>
          <li style={{ marginBottom: 10 }}><b style={{ color: '#e2e8f0' }}>A covered call caps your upside.</b> If the stock rallies hard past the strike, you only ever get the strike price for the shares — never the real market price, no matter how high it goes.</li>
          <li style={{ marginBottom: 10 }}><b style={{ color: '#e2e8f0' }}>A cash-secured put is a real obligation.</b> If the stock drops well below the strike, you&apos;re still buying at the strike — the premium collected softens that loss, it doesn&apos;t prevent it.</li>
          <li style={{ marginBottom: 10 }}><b style={{ color: '#e2e8f0' }}>The concentration cap limits sizing, not risk itself.</b> A position within the cap can still lose money — it just can&apos;t be the only thing the portfolio holds.</li>
          <li style={{ marginBottom: 10 }}><b style={{ color: '#e2e8f0' }}>Nothing here is rolled.</b> A position that would benefit from being rolled to a later expiry instead simply closes — by design, to keep the engine&apos;s exposure bounded (§3) — so this is not the same as an actively-managed real options desk.</li>
          <li><b style={{ color: '#e2e8f0' }}>This is paper money.</b> Every number on this page reflects a simulated position, not a real brokerage account, matching every other portfolio on this platform.</li>
        </ul>
      </Section>

      {/* ── 7. Checklist ─────────────────────────────────────────────────────── */}
      <Section id="checklist" title="8. Checklist before creating a portfolio">
        <StepList steps={[
          <>Decide how much starting capital to use — since no single position can exceed 25% of it, a portfolio meant to hold several positions at once needs several times the size of the largest single collateral requirement you expect (check the Candidates tab first for a realistic sense of that).</>,
          'Understand that a covered call caps upside and a cash-secured put is a real obligation to buy — re-read §1/§2 if either is unclear.',
          'Understand that positions close at expiry, win or lose — there is no rolling to manage.',
          'Check the Candidates tab so the first automatic run isn’t a surprise.',
          'Remember every trade is simulated — this is a mechanics teaching tool and a research signal, not a live brokerage feed.',
        ]} />
      </Section>
    </div>
  );
}
