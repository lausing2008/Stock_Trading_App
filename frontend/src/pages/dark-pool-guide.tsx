import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import { getSession } from '@/lib/auth';

// ── Shared small components — same visual language as option-trading-guide.tsx/alerts-guide.tsx ──

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
      <code style={{ color: '#38bdf8', fontWeight: 700, fontSize: '12.5px', fontFamily: 'monospace' }}>{name}</code>
      <div style={{ fontSize: '12.5px', color: '#94a3b8', lineHeight: 1.6, marginTop: 4 }}>{desc}</div>
      <div style={{ fontSize: '11.5px', color: '#64748b', marginTop: 6, fontFamily: 'monospace' }}>{example}</div>
    </div>
  );
}

function ReasonCard({ name, color, children }: { name: string; color: string; children: React.ReactNode }) {
  return (
    <div style={{ padding: '14px 16px', borderRadius: 10, background: '#0d1424', border: `1px solid ${color}33`, marginBottom: 12 }}>
      <div style={{ fontSize: '13px', fontWeight: 700, color, marginBottom: 6 }}>{name}</div>
      <div style={{ fontSize: '12.5px', color: '#94a3b8', lineHeight: 1.6 }}>{children}</div>
    </div>
  );
}

function FlowDiagram() {
  const box = (label: string, sub: string, color: string) => (
    <div style={{
      padding: '10px 14px', borderRadius: '10px', background: '#0d1424', border: `1px solid ${color}55`,
      minWidth: '140px', textAlign: 'center' as const,
    }}>
      <div style={{ fontSize: '12px', fontWeight: 700, color }}>{label}</div>
      {sub && <div style={{ fontSize: '10.5px', color: '#64748b', marginTop: 2 }}>{sub}</div>}
    </div>
  );
  return (
    <div style={{ padding: '20px', borderRadius: '12px', border: '1px solid #1e293b', background: 'rgba(148,163,184,0.02)' }}>
      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', justifyContent: 'center', alignItems: 'center' }}>
        {box('Institutional order', 'a fund needs to buy/sell a large block', '#a78bfa')}
        <span style={{ color: '#334155', fontSize: '18px' }}>→</span>
        {box('Dark pool venue', 'crosses off the public exchange tape', '#38bdf8')}
        <span style={{ color: '#334155', fontSize: '18px' }}>→</span>
        {box('FINRA trade report', 'published after the fact — this is what you see', '#4ade80')}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DarkPoolGuidePage() {
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
          Dark Pool Trading — Guide
        </h1>
        <p style={{ fontSize: '13px', color: '#64748b' }}>
          What a dark pool print actually is, why it's public information despite the name, and how
          to read the Dark Pool card on a stock's Market Pressure panel without over-reading it.
        </p>
      </div>

      <Section title="What a dark pool actually is">
        <p style={{ marginBottom: 12 }}>
          A dark pool is a private trading venue where large institutional orders — a pension fund
          rebalancing, a mutual fund building a position, a bank hedging a derivatives book — execute
          away from a public exchange like the NYSE or Nasdaq. The trade still has to be reported
          afterward under FINRA's own trade-reporting rules, so it isn't secret or illegal; it's just
          not visible on a normal Level 2 quote screen the moment it happens, the way a trade on a lit
          exchange is.
        </p>
        <p style={{ marginBottom: 12 }}>
          The name "dark pool" refers to that reporting delay and lack of a visible order book — not
          to anything hidden from regulators. Every print this app shows you comes from Unusual
          Whales' real feed of these FINRA-reported trades, the same underlying data any market
          participant can eventually see.
        </p>
        <FlowDiagram />
      </Section>

      <Section title="Why institutions use dark pools">
        <p style={{ marginBottom: 12 }}>
          The core reason is <b style={{ color: '#cbd5e1' }}>market impact</b>. If a fund needs to buy
          500,000 shares of a mid-cap stock, placing that whole order on the lit exchange would move
          the price against them before the order even finishes filling — every other trader would see
          the huge buy order and front-run it. Dark pools let large blocks cross privately, often at
          the midpoint of the public bid/ask, without tipping off the rest of the market in real time.
        </p>
        <SubSection title="The most common real reasons a block prints dark">
          <ReasonCard name="Portfolio rebalancing" color="#38bdf8">
            An index fund or ETF adjusting its holdings to match a benchmark change — routine,
            scheduled, and has nothing to do with a view on the stock's future price.
          </ReasonCard>
          <ReasonCard name="Position building or unwinding" color="#a78bfa">
            An institution accumulating or exiting a large stake over time, deliberately split into
            pieces to avoid moving the price against themselves.
          </ReasonCard>
          <ReasonCard name="Hedging" color="#f59e0b">
            A bank or market maker offsetting risk from an options or derivatives position elsewhere
            — the trade is about managing exposure, not a directional bet on the stock.
          </ReasonCard>
        </SubSection>
      </Section>

      <Section title="How to actually use it — and the one mistake to avoid">
        <Callout tone="warn" title="The most important thing to understand">
          A large dark pool print is <b>not</b> a bullish or bearish signal by itself. Unlike an
          options-flow sweep (where UW itself computes a real aggressive-buy-vs-aggressive-sell
          split), a dark pool print has no such direction attached — you're seeing that size moved,
          not why, and not which side initiated it. Treating a big print as "smart money is bullish"
          is a common but real misreading of what this data actually shows.
        </Callout>
        <p style={{ marginBottom: 12 }}>
          What a dark pool print IS genuinely useful for:
        </p>
        <SubSection title="1. Confirming real institutional interest exists">
          If you're already watching a stock for another reason — an AI Signal BUY, a squeeze
          setup, an earnings catalyst — a wave of large dark pool prints tells you real
          institutional-size capital is actively transacting in the name right now, which is
          corroborating context, not a standalone reason to trade.
        </SubSection>
        <SubSection title="2. Sizing up genuine liquidity">
          Consistent large block activity means the stock can actually absorb size without your own
          order moving the price much — useful context before sizing a position, especially in a
          less-liquid mid-cap name.
        </SubSection>
        <SubSection title="3. A premium-based sense of scale">
          The <Code>premium</Code> field (price × size) tells you the real dollar size of the
          print. This app's own Dark Pool alert only fires above $1M in a single print — genuinely
          institutional-scale activity, not routine flow.
        </SubSection>
      </Section>

      <Section title="Reading the Dark Pool card">
        <p style={{ marginBottom: 12 }}>
          On a stock's detail page, under Market Pressure, the Dark Pool card (when Unusual Whales
          is configured and enabled in Settings) shows the most recent qualifying prints:
        </p>
        <MetricCard
          name="size"
          desc="Number of shares in this single print."
          example='e.g. 250,000 shares'
        />
        <MetricCard
          name="price"
          desc="The execution price of the print — often close to the midpoint of the public bid/ask at the time, not necessarily the last trade price on the lit tape."
          example="e.g. $142.18"
        />
        <MetricCard
          name="premium"
          desc="Total dollar value of the print (price × size) — the field this app's alert and card both rank by."
          example="e.g. $35,545,000"
        />
        <MetricCard
          name="venue"
          desc="UW's own market-center code for the reporting venue (a FINRA ADF dark venue) — shown verbatim, not translated into this app's own taxonomy."
          example='e.g. "L"'
        />
      </Section>

      <Section title="The Dark Pool alert">
        <p style={{ marginBottom: 12 }}>
          If you have a price alert set on a symbol, a real $1M+ dark pool print on that symbol can
          trigger an email — the same honest, measured-fact framing as every other alert this app
          sends: it tells you a large block just printed off-exchange, with the real size, price,
          and venue. It does not claim the stock will move as a result, and it's deliberately not
          framed as bullish or bearish, for exactly the reason explained above.
        </p>
        <Callout tone="info" title="Forward-return tracking">
          Every Dark Pool alert this app sends is recorded and its forward return is measured 1, 2,
          3, 5, 10, and 20 days later — but scored as "did an outsized move happen in either
          direction," not "did the price go up as predicted," since this alert makes no directional
          claim to test in the first place.
        </Callout>
      </Section>

      <Section title="Data source">
        <p>
          Dark pool data comes from Unusual Whales' real{' '}
          <Code>/api/darkpool/{'{'}ticker{'}'}</Code> endpoint — genuinely new capability for this
          app, not a replacement for anything that existed before. It requires a configured and
          enabled Unusual Whales subscription (Settings → Market Pressure Data) — with it off or
          unconfigured, the Dark Pool card and alert simply don't appear, the same graceful-
          degradation behavior as every other Unusual Whales feature in this app.
        </p>
      </Section>
    </div>
  );
}
