import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import { getSession } from '@/lib/auth';

// ── Shared small components — same visual language as alerts-guide.tsx/conditional-orders-guide.tsx ──

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '32px' }}>
      <h2 style={{ fontSize: '15px', fontWeight: 800, color: '#e2e8f0', marginBottom: '10px' }}>{title}</h2>
      <div style={{ fontSize: '13px', lineHeight: 1.7, color: '#94a3b8' }}>{children}</div>
    </div>
  );
}

function SubSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '20px' }}>
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

function MetricCard({ name, desc, example }: { name: string; desc: string; example: string }) {
  return (
    <div style={{ padding: '12px 16px', borderRadius: 10, background: '#0d1424', border: '1px solid #1e293b', marginBottom: 10 }}>
      <code style={{ color: '#a78bfa', fontWeight: 700, fontSize: '12.5px', fontFamily: 'monospace' }}>{name}</code>
      <div style={{ fontSize: '12.5px', color: '#94a3b8', lineHeight: 1.6, marginTop: 4 }}>{desc}</div>
      <div style={{ fontSize: '11.5px', color: '#64748b', marginTop: 6, fontFamily: 'monospace' }}>{example}</div>
    </div>
  );
}

function StrategyCard({ name, color, when, mechanics, risk }: { name: string; color: string; when: string; mechanics: string; risk: string }) {
  return (
    <div style={{ padding: '14px 16px', borderRadius: 10, background: '#0d1424', border: `1px solid ${color}33`, marginBottom: 12 }}>
      <div style={{ fontSize: '13px', fontWeight: 700, color, marginBottom: 6 }}>{name}</div>
      <div style={{ fontSize: '12.5px', color: '#94a3b8', lineHeight: 1.6, marginBottom: 6 }}>
        <b style={{ color: '#cbd5e1' }}>When: </b>{when}
      </div>
      <div style={{ fontSize: '12.5px', color: '#94a3b8', lineHeight: 1.6, marginBottom: 6 }}>
        <b style={{ color: '#cbd5e1' }}>How: </b>{mechanics}
      </div>
      <div style={{ fontSize: '12.5px', color: '#94a3b8', lineHeight: 1.6 }}>
        <b style={{ color: '#cbd5e1' }}>Risk: </b>{risk}
      </div>
    </div>
  );
}

function DiagramBox({ label, sub, color }: { label: string; sub?: string; color: string }) {
  return (
    <div style={{
      padding: '10px 14px', borderRadius: '10px', background: '#0d1424', border: `1px solid ${color}55`,
      minWidth: '160px', textAlign: 'center',
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
  return <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', justifyContent: 'center' }}>{children}</div>;
}

function GamePlanDiagram() {
  const stepLabel = { fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase' as const, letterSpacing: '0.05em', marginBottom: 14, textAlign: 'center' as const };
  return (
    <div style={{ padding: '20px', borderRadius: '12px', border: '1px solid #1e293b', background: 'rgba(148,163,184,0.02)' }}>
      <div style={stepLabel}>1. AI Signal already computed these</div>
      <DiagramRow>
        <DiagramBox label="Stop-loss" sub="nearest support / ATR floor" color="#f87171" />
        <DiagramBox label="Take-profit" sub="analyst target price" color="#4ade80" />
      </DiagramRow>
      <DownArrow />
      <div style={stepLabel}>2. Options Game Plan looks up real contracts</div>
      <DiagramRow>
        <DiagramBox label="Put near your stop" sub="25-60 days out" color="#f87171" />
        <DiagramBox label="Call near your target" sub="14-45 days out" color="#4ade80" />
      </DiagramRow>
      <DownArrow />
      <div style={stepLabel}>3. You see real, current prices</div>
      <DiagramRow>
        <DiagramBox label="Protective Put" sub="cost to insure your downside" color="#f59e0b" />
        <DiagramBox label="Covered Call" sub="income for capping your upside" color="#f59e0b" />
      </DiagramRow>
      <DownArrow />
      <div style={stepLabel}>4. You decide</div>
      <DiagramRow>
        <DiagramBox label="Nothing is placed automatically" sub="this shows numbers, you execute the trade yourself" color="#22c55e" />
      </DiagramRow>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function OptionTradingGuidePage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    setAuthed(true);
  }, [router]);

  if (!authed) return null;

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px 24px 60px' }}>
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '6px' }}>
          Option Trading — Guide
        </h1>
        <p style={{ fontSize: '13px', color: '#64748b' }}>
          How to read an options chain, the basic strategies this app can price for you, and how to
          combine them with AI Signal to protect a position from a sharp move against you.
        </p>
      </div>

      <Callout tone="info" title="Advanced-tier feature">
        The computed Options Game Plan card (shown on a stock&apos;s detail page) requires an{' '}
        <b style={{ color: '#e2e8f0' }}>Advanced-tier</b> account. Everything else on this page —
        reading an options chain, understanding the strategies — applies regardless of your tier. Ask
        an admin to upgrade your account in Settings if you don&apos;t see the computed card yet.
      </Callout>

      <Section title="Reading an options chain in this app">
        <p style={{ marginBottom: 12 }}>
          Every stock detail page has an Options Chain panel showing every listed strike for one
          expiration date, both calls and puts side by side. Here&apos;s what each column means:
        </p>
        <MetricCard
          name="strike"
          desc="The price at which the option can be exercised — buy the stock (call) or sell it (put) at this price, regardless of where the stock is actually trading."
          example="A $145 put lets you sell at $145 even if the stock has fallen to $130."
        />
        <MetricCard
          name="bid / ask"
          desc="What buyers are currently offering (bid) and what sellers are currently asking (ask). The real price you'd pay to buy sits closer to the ask; the real price you'd receive selling sits closer to the bid. This app's own Options Game Plan uses the midpoint of the two as a realistic estimate."
          example="bid $2.90, ask $3.10 -> a realistic fill is close to $3.00, not the ask."
        />
        <MetricCard
          name="oi (open interest)"
          desc="How many contracts at this strike are currently open (not yet closed out). Higher open interest generally means an easier fill at a fair price; very low OI means a wide bid/ask spread and a real risk you'd move the price just by trading it."
          example="500 OI is comfortably liquid; 5 OI means you're likely the only one looking at that strike today."
        />
        <MetricCard
          name="iv (implied volatility)"
          desc="The market's own estimate of how much the stock will move before this option expires, expressed as an annualized percentage. Higher IV means a more expensive option (more time-value priced in) — this is why the SAME stock's options can look cheap or expensive purely based on how jumpy the market currently expects it to be, independent of which direction it moves."
          example="IV 30% is fairly normal for a large-cap; IV 80%+ usually means an upcoming earnings report or other real catalyst is priced in."
        />
        <MetricCard
          name="itm (in the money)"
          desc="Whether the option already has real intrinsic value at the CURRENT stock price — a call is ITM when the stock is above the strike; a put is ITM when the stock is below the strike. An option that is NOT in the money ('out of the money') is pure time-value/bet on a future move."
          example="Stock at $150, a $145 put is OTM (out of the money); a $155 put is ITM."
        />
        <p style={{ marginTop: 12 }}>
          The chain panel also shows <Code>max_pain</Code> — the strike where option writers as a
          whole would owe the least at expiry, computed purely from open interest (no prediction of
          where price will actually go). See the Volume Profile / Fair Value Gap docs elsewhere in
          this app for how this compares to gamma-exposure (GEX) levels when a real Unusual Whales
          subscription is configured.
        </p>
      </Section>

      <Section title="Three ways to use options — which ones this app helps with">
        <StrategyCard
          name="🛡️ Protective Put — insure a position you already hold"
          color="#f87171"
          when="You own (or plan to buy) shares and want a hard floor under a sharp, unexpected drop — earnings risk, a broad market selloff, anything your stop-loss might not survive a fast gap through."
          mechanics="Buy a put option below your entry price. If the stock crashes, the put's own value rises to offset the loss — you're never worse off than (strike price - what you paid for the put)."
          risk="The premium you pay is a real, guaranteed cost — if the stock doesn't fall, you simply lose what you paid for the insurance, the same way home insurance costs you money in a year nothing burns down."
        />
        <StrategyCard
          name="💰 Covered Call — collect income against an upside target you already believe in"
          color="#4ade80"
          when="You already hold shares, you have a real price target in mind (e.g. an analyst target, or your own take-profit level), and you're comfortable capping your gain there in exchange for real, upfront income."
          mechanics="Sell a call option at or above your target price. You collect the premium immediately. If the stock stays below your strike, you keep the premium AND the shares. If it rises past your strike, your shares get 'called away' at that price — you still profit up to the strike, plus you keep the premium."
          risk="You give up any gain ABOVE the strike price — if the stock rallies hard past your target, a covered call caps what would otherwise have been a bigger win."
        />
        <StrategyCard
          name="🔗 Collar (both together)"
          color="#a78bfa"
          when="You want the protective put's floor AND you're willing to use the covered call's premium to help pay for it — a genuinely defensive, capped-both-ways position."
          mechanics="Buy the protective put and sell the covered call at the same time. The call's premium partially (sometimes fully) offsets the put's cost, at the price of capping your upside too."
          risk="You're now capped on BOTH sides — you can't lose more than the put's floor, but you also can't gain more than the call's ceiling. This app's Options Game Plan computes each leg independently; combining them into a collar is a manual decision you make from the two numbers it gives you."
        />
      </Section>

      <Section title="How the Options Game Plan card works">
        <p style={{ marginBottom: 16 }}>
          On a stock&apos;s detail page (Advanced tier only), a card automatically prices a real
          protective put against your stop-loss level and a real covered call against your
          take-profit level — the SAME numbers Position Sizer already shows you, not a second,
          independently-guessed set of levels.
        </p>
        <GamePlanDiagram />
        <SubSection title="What it picks, and why">
          <ul style={{ paddingLeft: 18, lineHeight: 1.8, margin: 0 }}>
            <li>
              <b style={{ color: '#e2e8f0' }}>Protective put strike</b>: the real, currently-listed
              strike closest to your stop-loss price — not necessarily an exact match, since listed
              strikes are spaced apart (e.g. every $2.50 or $5).
            </li>
            <li>
              <b style={{ color: '#e2e8f0' }}>Protective put expiry</b>: picked from real listed
              expirations, aiming for 25-60 days out (long enough that the hedge isn&apos;t eaten by
              fast time-decay, short enough that you&apos;re not overpaying for unneeded duration).
            </li>
            <li>
              <b style={{ color: '#e2e8f0' }}>Covered call strike/expiry</b>: same idea, anchored to
              your take-profit level instead, aiming for a shorter 14-45 day window — income
              collection doesn&apos;t need the same runway insurance does.
            </li>
            <li>
              <b style={{ color: '#e2e8f0' }}>Cost / credit</b>: the real bid/ask midpoint of that
              contract right now, shown both per-share and as a % of your position.
            </li>
          </ul>
        </SubSection>
        <SubSection title="Implied Volatility and IV Rank (screener + BUY-signal email, Advanced tier)">
          <p style={{ marginBottom: 10 }}>
            On the screener&apos;s expandable row detail and in the BUY-signal email, you may also
            see an <b style={{ color: '#818cf8' }}>Implied Volatility</b> line showing an{' '}
            <b style={{ color: '#e2e8f0' }}>expected move</b> and an{' '}
            <b style={{ color: '#e2e8f0' }}>IV Rank</b> — both sourced from Unusual Whales, computed
            once daily alongside the rest of that symbol&apos;s Options Game Plan snapshot.
          </p>
          <ul style={{ paddingLeft: 18, lineHeight: 1.8, margin: '0 0 10px' }}>
            <li>
              <b style={{ color: '#e2e8f0' }}>Expected move</b> is the market&apos;s own forecast of
              how far this stock is likely to move, backed out of real options prices (implied
              volatility) rather than guessed — a stock showing &quot;±6.2% (30d)&quot; means the
              options market is currently pricing in roughly that much movement, either direction,
              over the next month. This is what replaces a fixed, one-size-fits-all take-profit
              percentage on a symbol with real Unusual Whales data — see the worked example below.
            </li>
            <li>
              <b style={{ color: '#e2e8f0' }}>IV Rank</b> is a different question: is that implied
              volatility reading high or low <i>for this specific stock</i>, relative to its own
              trailing 1-year range? It&apos;s a 0-100 percentile — 0 means today&apos;s IV is the
              lowest it&apos;s been all year, 100 means it&apos;s the highest. The same 30% IV
              reading could be an IV Rank of 85 for a normally sleepy utility stock (unusually
              volatile right now) or an IV Rank of 15 for a stock that&apos;s always volatile
              (calm by its own standards) — the raw IV number alone can&apos;t tell you which.
            </li>
          </ul>
          <p style={{ margin: 0 }}>
            The practical use: a <b style={{ color: '#e2e8f0' }}>high IV Rank</b> (roughly 70+)
            means options on this stock are relatively expensive right now — a real, if imperfect,
            signal that premium-selling strategies (like the covered call above) collect richer
            income than usual, while premium-buying strategies (like the protective put above) cost
            more than usual for the same protection. A <b style={{ color: '#e2e8f0' }}>low IV Rank</b>{' '}
            (roughly 30 or under) is the mirror image — options are relatively cheap, favoring
            buying premium over selling it. Neither reading is a buy/sell signal on the STOCK
            itself — it&apos;s only about whether the OPTIONS on it are currently priced rich or
            cheap relative to their own recent history.
          </p>
        </SubSection>
      </Section>

      <Callout tone="example" title="Worked example — protecting a real AI Signal BUY">
        AI Signal shows a BUY on a stock with a stop-loss at $142 and a take-profit at $168. The
        Options Game Plan card finds a real $140 put expiring in ~45 days costing $3.00/share
        (2% of your position) — buying it caps your worst case near $137 even if the stock gaps
        down overnight past your stop-loss order. It also finds a real $168 call expiring in ~30
        days paying $1.85/share in premium — selling it collects income while you wait for your
        target, at the cost of capping your gain right around where you already planned to take
        profit anyway.
      </Callout>

      <Callout tone="warn" title="What this does NOT do">
        <ul style={{ paddingLeft: 18, lineHeight: 1.7, margin: 0 }}>
          <li>It does not place any options trade for you — this shows real, current prices; you execute the trade yourself with your own broker.</li>
          <li>It is not a prediction of where the stock will go — the reported numbers are simply what insuring or collecting income against your OWN plan currently costs, computed from a real, live options chain.</li>
          <li>It requires you to already hold (or plan to buy) shares of the underlying stock — a protective put or covered call only make sense as a hedge/income overlay on a real stock position, not as a standalone bet.</li>
          <li>This interactive, per-symbol card itself doesn&apos;t show delta/theta/vega — it&apos;s deliberately limited to strike/expiry/price/floor-or-cap math. Real Greeks for the exact same strike/expiry ARE shown elsewhere — see the next subsection.</li>
        </ul>
      </Callout>

      <SubSection title="Real per-contract Greeks (screener + BUY-signal email, Advanced tier)">
        <p style={{ marginBottom: 10 }}>
          On the screener&apos;s expandable row detail and in the BUY-signal email, the protective
          put/covered call legs show a compact <b style={{ color: '#e2e8f0' }}>Δ Γ Θ V</b> line —
          real delta, gamma, theta, and vega for that EXACT strike/expiry, sourced from Unusual
          Whales and computed daily alongside the rest of the Options Game Plan snapshot.
        </p>
        <p style={{ marginBottom: 10 }}>
          The Greeks turn &quot;this option costs $3.00&quot; into &quot;here&apos;s exactly what
          that $3.00 is exposed to, and by how much.&quot; Two contracts can cost the same and
          look equally reasonable on strike/expiry alone, yet be very differently exposed once
          you look at their Greeks — the worked examples below show exactly that.
        </p>
        <ul style={{ paddingLeft: 18, lineHeight: 1.8, margin: '0 0 10px' }}>
          <li><b style={{ color: '#e2e8f0' }}>Δ (delta)</b> — roughly how much the option&apos;s own price moves per $1 the stock moves. A put around -0.45 means the option gains about $0.45 for every $1 the stock falls (it&apos;s negative because a put gains value as the stock drops). Delta also doubles as a rough odds-of-finishing-in-the-money estimate (-0.45 ≈ 45% odds) and as your effective share exposure (one contract at delta -0.45 hedges roughly 45 shares).</li>
          <li><b style={{ color: '#e2e8f0' }}>Γ (gamma)</b> — how fast delta itself changes as the stock moves; the &quot;delta of delta.&quot; Higher gamma means your hedge&apos;s effectiveness can shift quickly as the stock actually starts moving, typically largest for at-the-money contracts close to expiry. For a protective put, high gamma is usually a GOOD thing — it means your protection strengthens fast exactly when the stock starts falling toward your strike, instead of staying weak until it&apos;s nearly too late.</li>
          <li><b style={{ color: '#e2e8f0' }}>Θ (theta)</b> — how much value the option loses per day, all else equal, just from time passing. A theta of -0.04 means the contract is worth about $0.04 less tomorrow than today if the stock doesn&apos;t move — the real, ongoing cost of holding a hedge, or the income a covered call collects for you (theta works AGAINST an option buyer and FOR an option seller — a protective put buyer pays it, a covered call writer is paid it).</li>
          <li><b style={{ color: '#e2e8f0' }}>V (vega)</b> — how much the option&apos;s price changes if implied volatility itself moves by 1 point, independent of the stock price. Relevant alongside the IV Rank reading above — a high-vega position is more exposed to IV itself calming down or spiking, separate from where the stock goes.</li>
        </ul>
        <p style={{ margin: 0 }}>
          This is shown only when Unusual Whales has real Greeks data for that specific contract
          — no fabricated numbers if the data isn&apos;t available, matching every other UW-sourced
          field in this app.
        </p>
      </SubSection>

      <Callout tone="example" title="Worked example — same put price, very different hedge">
        Two protective puts on the same stock, both quoted around $3.00: <b style={{ color: '#e2e8f0' }}>Put A</b>{' '}
        (closer to the money, 30 days out) shows <Code>Δ -0.48</Code>, <Code>Γ 0.04</Code>,{' '}
        <Code>Θ -0.06</Code>. <b style={{ color: '#e2e8f0' }}>Put B</b> (further out-of-the-money,
        60 days out) shows <Code>Δ -0.22</Code>, <Code>Γ 0.015</Code>, <Code>Θ -0.02</Code>. Same
        $3.00 cost, very different hedge: Put A moves almost twice as much per $1 the stock falls
        (delta -0.48 vs -0.22) and its protection strengthens faster as a real decline develops
        (higher gamma) — but it also bleeds value more than 3x faster every day you hold it
        (theta -0.06 vs -0.02). Put B is the cheaper-to-hold, slower-reacting hedge; Put A is the
        expensive-to-hold, fast-reacting one. Neither is &quot;wrong&quot; — Put A suits someone
        expecting a move soon and wanting strong protection fast; Put B suits someone hedging a
        position they plan to hold for weeks and wants to minimize the daily bleed. Reading only
        the price ($3.00 both) would have hidden this entire tradeoff.
      </Callout>

      <Callout tone="example" title="Worked example — a covered call's theta as real, quantified income">
        AI Signal shows a BUY with a take-profit at $168; the covered call card finds a $168 call,
        30 days out, priced at $1.85 with <Code>Δ 0.35</Code>, <Code>Θ -0.03</Code>. Selling it
        collects that $1.85 premium up front — theta says roughly $0.03/day of that premium is
        &quot;earned&quot; by time decay alone if the stock sits still, meaning it should be worth
        about $0.90 less in 30 days purely from time passing (30 × $0.03), separate from
        whatever the stock itself does. Delta 0.35 also tells you the real odds framing: roughly
        a 35% chance this call finishes in-the-money and caps your gain at $168 — a genuinely
        different, more concrete way to think about &quot;how likely am I to actually get called
        away&quot; than guessing from the stock chart alone.
      </Callout>

      <Section title="Full case study — 100 shares of AAPL, three ways it could go">
        <p style={{ marginBottom: 16 }}>
          Everything above explained the pieces separately. Here they are together, on one real
          position, with every number carried through to a final dollar P&amp;L — using AAPL&apos;s
          real quoted price and real listed option premiums from a live chain, not invented
          numbers.
        </p>

        <div style={{ padding: '14px 16px', borderRadius: '10px', border: '1px solid #1e293b', background: '#0b1420', marginBottom: 16 }}>
          <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#38bdf8', marginBottom: 8 }}>The setup</div>
          <ul style={{ paddingLeft: 18, lineHeight: 1.9, margin: 0, color: '#94a3b8' }}>
            <li>AI Signal fires <b style={{ color: '#4ade80' }}>BUY</b> on <b style={{ color: '#e2e8f0' }}>AAPL</b> at <Code>$328.21</Code> — you buy <Code>100 shares</Code> for <Code>$32,821</Code>.</li>
            <li>ATR(14) is <Code>$6.98</Code>. The SWING-style game plan&apos;s stop is <Code>max(price − 2×ATR, price × 0.945)</Code> = <Code>max($314.25, $310.16)</Code> = <b style={{ color: '#e2e8f0' }}>$314.25</b>.</li>
            <li>A realistic take-profit target is <b style={{ color: '#e2e8f0' }}>$345</b> (roughly a 5% move — the kind of target a real expected-move reading, not a generic fixed percentage, would suggest for a 30-45 day window).</li>
            <li>Real listed options, ~36 days out: a <Code>$315 put</Code> trading at <Code>$4.65</Code>, and a <Code>$345 call</Code> trading at <Code>$4.06</Code> — both close to the stop and target above.</li>
          </ul>
        </div>

        <SubSection title="Scenario A — the stock gaps down hard (buy a protective put)">
          <p style={{ marginBottom: 10 }}>
            Say AAPL gaps overnight on bad news to <Code>$290</Code> — well past your $314.25 stop,
            the exact scenario a protective put exists for (a stop-loss ORDER can&apos;t protect you
            from a gap; it only protects you from a decline you can actually sell into).
          </p>
          <div style={{ padding: '12px 14px', borderRadius: '8px', background: '#0a1120', fontFamily: 'monospace', fontSize: '13px', lineHeight: 1.8, color: '#94a3b8', marginBottom: 10 }}>
            Without a put: ($290.00 − $328.21) × 100 = <b style={{ color: '#f87171' }}>−$3,821.00</b><br/>
            Put cost: $4.65 × 100 = <b style={{ color: '#e2e8f0' }}>$465.00</b><br/>
            Put payout at expiry: max($315.00 − $290.00, 0) × 100 = <b style={{ color: '#4ade80' }}>$2,500.00</b><br/>
            Net P&amp;L: −$3,821.00 + $2,500.00 − $465.00 = <b style={{ color: '#f87171' }}>−$1,786.00</b> (−5.4% of cost basis)
          </div>
          <p style={{ margin: 0 }}>
            The put didn&apos;t prevent a loss — it capped it. Without it you&apos;re down 11.6%;
            with it, you&apos;re down 5.4%, for a known, paid-up-front cost of $465. That $465 is
            the real, quantified price of insurance against exactly this scenario — the same
            reasoning as the Short Squeeze / Gamma Unwind worked examples earlier in this guide,
            now with the actual dollars attached.
          </p>
        </SubSection>

        <SubSection title="Scenario B — the stock goes nowhere (sell a covered call)">
          <p style={{ marginBottom: 10 }}>
            Say AAPL chops sideways around <Code>$328</Code> for the next month — no real move
            either direction. A long stock position alone earns nothing extra here; a covered call
            turns that dead time into real, collected income.
          </p>
          <div style={{ padding: '12px 14px', borderRadius: '8px', background: '#0a1120', fontFamily: 'monospace', fontSize: '13px', lineHeight: 1.8, color: '#94a3b8', marginBottom: 10 }}>
            Stock P&amp;L: ($328.00 − $328.21) × 100 = <b style={{ color: '#e2e8f0' }}>−$21.00</b> (essentially flat)<br/>
            Call premium collected: $4.06 × 100 = <b style={{ color: '#4ade80' }}>$406.00</b><br/>
            Call expires worthless (stock never reached $345) — you keep the full premium<br/>
            Net P&amp;L: −$21.00 + $406.00 = <b style={{ color: '#4ade80' }}>+$385.00</b>
          </div>
          <p style={{ margin: 0 }}>
            A flat month that would otherwise be a wash turns into a real +$385 (about +1.2% of
            cost basis) purely from collecting the call&apos;s premium and having it expire
            unexercised. This is theta working FOR you, exactly as the earlier Greeks example
            described — you sold time decay, and time simply passed.
          </p>
        </SubSection>

        <SubSection title="Scenario C — the stock rallies past your target (the covered call's real tradeoff)">
          <p style={{ marginBottom: 10 }}>
            Say AAPL actually rallies to <Code>$355</Code> — past both your $345 target and your
            covered call&apos;s strike. This is the scenario a covered call writer has to accept:
            real upside gets capped in exchange for the premium collected in every other scenario.
          </p>
          <div style={{ padding: '12px 14px', borderRadius: '8px', background: '#0a1120', fontFamily: 'monospace', fontSize: '13px', lineHeight: 1.8, color: '#94a3b8', marginBottom: 10 }}>
            Uncapped stock gain (no call sold): ($355.00 − $328.21) × 100 = <b style={{ color: '#4ade80' }}>$2,679.00</b><br/>
            With the covered call, shares get called away at $345:<br/>
            &nbsp;&nbsp;Capped stock gain: ($345.00 − $328.21) × 100 = $1,679.00<br/>
            &nbsp;&nbsp;Plus premium collected: $406.00<br/>
            &nbsp;&nbsp;Total capped P&amp;L: <b style={{ color: '#4ade80' }}>$2,085.00</b><br/>
            Opportunity cost vs. holding uncapped: $2,679.00 − $2,085.00 = <b style={{ color: '#fbbf24' }}>$594.00</b>
          </div>
          <p style={{ margin: 0 }}>
            You still made $2,085 — a genuinely good outcome — but gave up $594 of further upside
            to get there. This is the real, honest cost of selling a covered call: it converts an
            uncertain, unlimited upside into a smaller, certain-once-triggered one. Whether that
            trade is worth it depends on how much you actually expected the stock to run past your
            own target versus how much you valued the guaranteed premium income in scenario B.
          </p>
        </SubSection>

        <Callout tone="good" title="What this case study is actually showing">
          The same $32,821 position, the same two option legs ($465 put cost, $406 call premium
          collected), produce three completely different outcomes depending on what the stock
          actually does — and every one of those outcomes was knowable in advance from the
          contract&apos;s own strike and premium, before the stock made its move. That is the real
          value of doing the math: not predicting which scenario happens, but knowing exactly what
          each one costs or pays before you&apos;re in it.
        </Callout>
      </Section>

      <Section title="The Market Pressure panel — Max Pain, OI walls, and NOPE">
        <p style={{ marginBottom: 16 }}>
          A stock&apos;s own page has a Market Pressure panel (Advanced tier, real Unusual Whales
          data) with the GEX dealer-hedging levels covered above, plus three more real readings —
          each a genuinely different lens on the same options market, not duplicates of each other.
        </p>

        <SubSection title="Max Pain — a different magnet theory than GEX's own walls">
          <p style={{ marginBottom: 10 }}>
            GEX&apos;s call wall/put wall/gamma flip describe where DEALERS are hedging — real
            positions market makers hold and actively manage. Max Pain is a completely different
            calculation: for each expiry, it&apos;s the single strike price where, if the stock
            closed there, option WRITERS in aggregate would owe the least money to option holders.
            Some traders watch it because there&apos;s a real (though contested) theory that price
            can drift toward this level into expiry — not because dealers are defending it the way
            they defend a gamma wall, but because option writers as a group have some incentive
            structure pulling toward it. Shown as a distinct purple dotted line on the daily chart.
          </p>
          <p style={{ margin: 0 }}>
            Treat it the same way this guide treats every other structural level: a real, honest
            number worth knowing, not a guaranteed price target. A stock can and does close far
            from its own max pain strike routinely.
          </p>
        </SubSection>

        <SubSection title="OI walls — the raw open-interest count GEX only implies">
          <p style={{ marginBottom: 10 }}>
            GEX&apos;s call_wall/put_wall are GAMMA-weighted — they tell you where dealer hedging
            pressure concentrates, which factors in each contract&apos;s own sensitivity to price
            moves, not just how many contracts exist. The Market Pressure panel&apos;s OI table
            shows the actual, unweighted number of open call/put contracts at each strike across
            all expiries — the raw material an &quot;OI wall&quot; reading is built from. A strike
            with heavy put OI sitting just below the current price is sometimes read as a level
            where put sellers have a real incentive to defend price from falling further (they lose
            money if it does) — again a real, watchable pattern, not a guarantee.
          </p>
        </SubSection>

        <SubSection title="NOPE — a live, delta-weighted read on which way options flow is pushing">
          <p style={{ marginBottom: 10 }}>
            Unlike every other Unusual Whales field in this app (all computed once daily), NOPE
            updates roughly every minute — it&apos;s meant to answer &quot;is there real
            directional pressure building in this stock&apos;s options RIGHT NOW,&quot; a
            same-day question a daily snapshot can&apos;t answer. It&apos;s built differently from
            this app&apos;s own homegrown Pressure score too: where that score uses raw call/put
            premium and volume/OI ratios, NOPE weights by each option&apos;s actual DELTA — a more
            theoretically-grounded measure of real directional exposure building up in the chain.
          </p>
          <p style={{ margin: 0 }}>
            A positive reading leans bullish (net call-side delta pressure), negative leans
            bearish. UW publishes two variants — the standard <Code>nope</Code> (volume-weighted)
            and <Code>nope_fill</Code> (fill-weighted, using actual executed trade sizes instead of
            posted volume) — both shown side by side since neither is documented as strictly
            better; when they broadly agree, that&apos;s a small extra piece of corroboration.
          </p>
        </SubSection>

        <SubSection title="Real historical expected move — was the options market's fear justified?">
          <p style={{ marginBottom: 10 }}>
            The Earnings Calendar page shows an &quot;Expected move&quot; figure next to each
            upcoming report — the options market&apos;s own real, live implied move for THAT
            specific report, not a generic estimate. Underneath it, a &quot;Past moves&quot; strip
            shows up to 4 of this stock&apos;s own prior quarters: what the market expected each
            time (left of the arrow) versus what the stock actually did the next day (right of
            the arrow, colored by direction).
          </p>
          <p style={{ margin: 0 }}>
            This answers a genuinely different question than the existing EPS beat-rate history
            next to it: beat rate is about whether the COMPANY tends to beat analyst estimates;
            this is about whether the MARKET&apos;s own pre-report fear (or complacency) tends to
            be accurate for this specific stock. A stock that consistently moves LESS than its own
            expected move suggests the market has historically over-priced its earnings risk —
            useful context before deciding whether buying a pre-earnings straddle or protective
            put at the currently-quoted premium is likely worth what it costs.
          </p>
        </SubSection>

        <SubSection title="Management tone — a qualitative read from the actual earnings call">
          <p style={{ marginBottom: 10 }}>
            When a stock you&apos;re subscribed to reports earnings, the post-report impact email
            (opt-in, Advanced tier) can include a line reading &quot;Management tone: ...&quot; —
            a genuinely qualitative read grounded in real excerpts from that company&apos;s own
            earnings call, when Unusual Whales has one available for that specific report. This is
            a real, different kind of signal from the numeric beat/miss the rest of the email
            already covers: whether management sounded confident about guidance, defensive about a
            miss, or notably vague on a topic an analyst pressed on — the sort of read that only
            comes from the actual words used on the call, not from EPS and revenue numbers alone.
          </p>
          <p style={{ margin: 0 }}>
            This line only appears when a real transcript excerpt was actually available and
            supported a clear read — no line at all (not a placeholder) otherwise, matching every
            other UW-sourced field in this app&apos;s own honest-when-unavailable convention.
          </p>
        </SubSection>
      </Section>

      <Section title="Reading this app's own alerts into an actual entry">
        <p style={{ marginBottom: 16 }}>
          The AI Signal BUY badge on a stock page is a starting point, not the whole picture — this
          app's other real-time alerts each tell you something different about what's happening in
          the options/short market right now. Here's how to read each one, worked from a real
          example, and where the Options Game Plan card fits once you've decided to act.
        </p>

        <SubSection title="Short Squeeze alert -> entry">
          <p style={{ marginBottom: 10 }}>
            You get an email: a stock is <Code>18.5%</Code> short of float, up <Code>+4.2%</Code>{' '}
            intraday, on <Code>3.1x</Code> its normal volume (the RVOL confirmation this alert
            requires — see the{' '}
            <a href="/alerts-guide" style={{ color: '#38bdf8', textDecoration: 'none' }}>Alerts Guide</a>{' '}
            for exactly how that threshold is computed). This is a real, already-confirmed BUY-direction
            thesis: shorts are being forced to cover into a move that's already happening on real volume,
            not a prediction of a future move.
          </p>
          <ol style={{ paddingLeft: 18, lineHeight: 1.8, margin: '0 0 10px' }}>
            <li><b style={{ color: '#e2e8f0' }}>Check the chart context first.</b> A squeeze alert fires on the move itself — it doesn&apos;t know whether the stock is breaking out from a base or already extended several days into a rally. A move that&apos;s already run hard before the alert fires has less room left and a real risk of reverting fast (this app&apos;s own audit history has found exactly this pattern in past squeeze alerts — see the alerts guide&apos;s own known-limitations notes).</li>
            <li><b style={{ color: '#e2e8f0' }}>Cross-check the AI Signal for the same stock.</b> A squeeze alert with an independent BUY signal already active is a real confluence — two different mechanisms agreeing, not just one.</li>
            <li><b style={{ color: '#e2e8f0' }}>If you decide to enter, size the risk with a protective put.</b> Since the whole thesis is a fast, forced move, a hard gap against you is a real risk a normal stop-loss order might not survive overnight — this is exactly the scenario a protective put (above) is built for: buy shares, then buy a put near your stop-loss level so a gap-down is capped at a known cost, not an open-ended loss.</li>
          </ol>
        </SubSection>

        <SubSection title="Gamma Unwind alert -> entry">
          <p style={{ marginBottom: 10 }}>
            You get an email: a stock has a large options-open-interest block concentrated near its
            current price, <Code>2 days</Code> from expiry, calls-dominant at <Code>88%</Code>. This
            is a <b style={{ color: '#e2e8f0' }}>directional WATCH, not a BUY/SELL call</b> (see the{' '}
            <a href="/alerts-guide" style={{ color: '#38bdf8', textDecoration: 'none' }}>Alerts Guide</a>{' '}
            for the full calls/puts-dominant thresholds) — market makers hedging that block as it
            expires can push price sharply either way, and this data alone can&apos;t tell you which.
          </p>
          <Callout tone="info" title="What is gamma?">
            Gamma measures how fast an option&apos;s <b style={{ color: '#e2e8f0' }}>delta</b>{' '}
            (its price sensitivity to the stock moving $1) changes as the stock price moves. A
            deep-in-the-money option barely changes its delta as price moves further — low gamma.
            An option sitting right <b style={{ color: '#e2e8f0' }}>at-the-money</b>, close to
            expiry, has the opposite problem: its delta swings hard with even a small stock move —
            high gamma. Market makers who sold that option don&apos;t want directional risk, so they
            hedge by buying/selling shares as delta shifts — and near expiry, at-the-money, that
            hedging has to happen fast and in size. That forced hedging flow is the actual mechanism
            behind a Gamma Unwind alert: a large block of options expiring soon, near the current
            price, means a lot of that fast re-hedging is about to happen at once, and it can push
            the underlying stock sharply in either direction depending on which way dealers are
            positioned. This is also exactly what <Code>gamma_flip</Code> (mentioned below) is
            locating — the price level where the market&apos;s aggregate dealer hedging flips from
            stabilizing price (dampening moves) to destabilizing it (amplifying them).
          </Callout>
          <ol style={{ paddingLeft: 18, lineHeight: 1.8, margin: '0 0 10px' }}>
            <li><b style={{ color: '#e2e8f0' }}>Read it as a volatility warning, not a direction.</b> The honest, defensible use of this alert is knowing a sharp move is more likely soon — not betting on which way.</li>
            <li><b style={{ color: '#e2e8f0' }}>If a Real GEX subscription is active</b>, check the stock&apos;s own <Code>gamma_flip</Code> level (shown on the Options Chain / Gamma Exposure panel) — price sitting close to that level is where dealer hedging tends to be most reactive, the same real signal this alert&apos;s calls/puts-dominant proxy is approximating for free-tier users.</li>
            <li><b style={{ color: '#e2e8f0' }}>If you already hold a position going into the expiry window</b>, this is a real, concrete reason to consider a collar (both legs together, above) — you don&apos;t know which way the unwind will push the stock, so capping both sides can be the more honest response than picking a direction you can&apos;t actually predict from this data.</li>
          </ol>
        </SubSection>

        <SubSection title="Dark Pool print -> entry">
          <p style={{ marginBottom: 10 }}>
            You see a large block trade on the Dark Pool tab. See the{' '}
            <a href="/dark-pool-guide" style={{ color: '#38bdf8', textDecoration: 'none' }}>Dark Pool Guide</a>{' '}
            for the full explanation of what this data is and, importantly, what it is{' '}
            <b style={{ color: '#e2e8f0' }}>not</b> — a large print by itself is not a bullish or
            bearish signal, and treating it as one is a common, real misreading this app&apos;s own
            guide warns against directly.
          </p>
          <p style={{ margin: 0 }}>
            The honest use here is as one more piece of context alongside an AI Signal or squeeze
            alert you&apos;re already looking at — a large recent print on a stock you&apos;re
            already considering is worth noting, but it should never be the reason to enter on its
            own.
          </p>
        </SubSection>

        <Callout tone="example" title="Worked example — combining three real signals into one entry">
          A stock shows an AI Signal <b style={{ color: '#4ade80' }}>BUY</b> (confidence 68), fired a{' '}
          <b style={{ color: '#f87171' }}>Short Squeeze alert</b> the same morning (17% short of
          float, +3.8% on 2.4x volume), and has a recent large Dark Pool print noted from the day
          before. None of these alone would be a strong enough reason to act — together, they&apos;re
          a real confluence: an independent AI model, a live forced-covering thesis on real volume,
          and recent large-size interest, all pointing the same direction at the same time. Entering
          here, then immediately checking the Options Game Plan card for a protective put near the
          signal&apos;s own stop-loss level, is the concrete workflow this section is describing —
          confluence to decide whether to enter, the game plan to decide how much you&apos;re risking
          if you&apos;re wrong.
        </Callout>
      </Section>
    </div>
  );
}
