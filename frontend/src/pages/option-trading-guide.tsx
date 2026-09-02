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
          <li>No real per-contract Greeks (delta/theta/vega) beyond implied volatility are shown — this app doesn&apos;t compute or source true option Greeks; the game plan card is deliberately limited to strike/expiry/price/floor-or-cap math that doesn&apos;t need them.</li>
        </ul>
      </Callout>
    </div>
  );
}
