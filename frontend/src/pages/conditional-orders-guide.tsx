import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import { getSession } from '@/lib/auth';

// ── Shared small components — same visual language as alerts-guide.tsx ────────────────────────

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

function ActionCard({ name, label, color, desc, requires }: { name: string; label: string; color: string; desc: string; requires?: string }) {
  return (
    <div style={{ padding: '14px 16px', borderRadius: 10, background: '#0d1424', border: `1px solid ${color}33`, marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
        <code style={{ color, fontWeight: 700, fontSize: '12.5px', fontFamily: 'monospace' }}>{name}</code>
        <span style={{ fontSize: '12px', color: '#e2e8f0', fontWeight: 600 }}>{label}</span>
      </div>
      <div style={{ fontSize: '12.5px', color: '#94a3b8', lineHeight: 1.6 }}>{desc}</div>
      {requires && (
        <div style={{ fontSize: '11.5px', color: '#64748b', marginTop: 6 }}>
          Requires: <span style={{ color: '#cbd5e1' }}>{requires}</span>
        </div>
      )}
    </div>
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

function WorkflowDiagram() {
  const stepLabel = { fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase' as const, letterSpacing: '0.05em', marginBottom: 14, textAlign: 'center' as const };
  return (
    <div style={{ padding: '20px', borderRadius: '12px', border: '1px solid #1e293b', background: 'rgba(148,163,184,0.02)' }}>
      <div style={stepLabel}>1. You create an order</div>
      <DiagramRow>
        <DiagramBox label="Trigger conditions" sub="price / RSI / volume / signal / P&L / time" color="#38bdf8" />
        <DiagramBox label="One action" sub="buy / sell / tighten stop / close / alert" color="#38bdf8" />
      </DiagramRow>
      <DownArrow />
      <div style={stepLabel}>2. Evaluated every 1 minute</div>
      <DiagramRow>
        <DiagramBox label="check_conditional_orders" sub="reads stockai:live_prices — no per-order yfinance calls" color="#a78bfa" />
      </DiagramRow>
      <DownArrow />
      <div style={stepLabel}>3. When the trigger fires</div>
      <DiagramRow>
        <DiagramBox label="buy" sub="same real entry gate as any organic trade" color="#f59e0b" />
        <DiagramBox label="sell / stop / close" sub="acts on your existing open position" color="#f59e0b" />
        <DiagramBox label="alert only" sub="no position touched" color="#f59e0b" />
      </DiagramRow>
      <DownArrow />
      <div style={stepLabel}>4. Order is done</div>
      <DiagramRow>
        <DiagramBox label="Email sent" sub="fired or failed — you always hear back" color="#22c55e" />
        <DiagramBox label="Status set" sub="triggered / failed / expired / cancelled" color="#22c55e" />
      </DiagramRow>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ConditionalOrdersGuidePage() {
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
          Conditional Orders — Guide
        </h1>
        <p style={{ fontSize: '13px', color: '#64748b' }}>
          &ldquo;If X, then do Y&rdquo; automation for a paper portfolio&apos;s own position in one symbol.
          Manage your own orders on the{' '}
          <a href="/conditional-orders" style={{ color: '#38bdf8', textDecoration: 'none' }}>Conditional Orders page</a>.
        </p>
      </div>

      <Callout tone="info" title="Deliberately NOT a 'chain' — one trigger, one action">
        This feature was originally requested as &ldquo;conditional order <em>chains</em>&rdquo; — e.g.
        &ldquo;if AAPL breaks $140, buy Y, then if that goes up 10%, buy Z.&rdquo; After weighing the
        real-money-adjacent risk, it was deliberately scoped down to <strong style={{ color: '#e2e8f0' }}>
        single-hop, same-symbol orders</strong> instead: one trigger, one action, on the symbol you
        already picked. If you want a real multi-step plan, create several separate orders — each one
        is independently simple to reason about, debug, and cancel. There is no hidden chain state
        linking them together.
      </Callout>

      <Section title="How it works">
        <p style={{ marginBottom: 16 }}>
          Every conditional order belongs to ONE paper portfolio and ONE symbol. Once a minute, a
          background job checks whether your trigger condition is true right now — if it is, it
          immediately runs your chosen action and the order is done. It never fires twice.
        </p>
        <WorkflowDiagram />
      </Section>

      <Section title="Trigger conditions — what can make an order fire">
        <p style={{ marginBottom: 12 }}>
          Every condition is a simple <Code>metric</Code> / <Code>op</Code> (&ge;, &le;, or =) /{' '}
          <Code>value</Code> comparison. You can combine several — choose whether{' '}
          <strong style={{ color: '#e2e8f0' }}>ALL</strong> must be true (AND) or{' '}
          <strong style={{ color: '#e2e8f0' }}>ANY ONE</strong> is enough (OR) when you create the
          order.
        </p>
        <MetricCard
          name="price"
          desc="The live price of the symbol right now."
          example='{"metric": "price", "op": "gte", "value": 140} → "if NVDA breaks above $140"'
        />
        <MetricCard
          name="rsi"
          desc="The stored daily RSI reading — the same value shown on the stock detail page and used by price-alert compound conditions, not a fresh recompute every minute."
          example='{"metric": "rsi", "op": "lte", "value": 30} → "if RSI drops below 30"'
        />
        <MetricCard
          name="volume_ratio"
          desc="Today's relative volume (RVOL) vs. the 20-day average — same computation the Volume Anomaly alert uses."
          example='{"metric": "volume_ratio", "op": "gte", "value": 3} → "if volume spikes 3x normal"'
        />
        <MetricCard
          name="signal"
          desc="The stored AI signal (BUY/HOLD/WAIT/SELL) for this symbol's SWING horizon."
          example='{"metric": "signal", "op": "eq", "value": "SELL"} → "if the signal flips to SELL"'
        />
        <MetricCard
          name="position_pnl_pct"
          desc="Your CURRENT open position's unrealized P&L %, on this portfolio, in this symbol. Only meaningful if you already hold a position — otherwise this condition can never be true."
          example='{"metric": "position_pnl_pct", "op": "gte", "value": 10} → "if my position is up 10%"'
        />
        <MetricCard
          name="time"
          desc="A specific clock time (UTC, HH:MM) has been reached."
          example='{"metric": "time", "op": "gte", "value": "14:30"} → "at/after 14:30 UTC"'
        />
        <Callout tone="warn" title="Fails closed, always">
          If a metric can&apos;t be measured right now (e.g. no RSI available, no open position for a
          P&amp;L check), that condition is treated as <strong style={{ color: '#e2e8f0' }}>not met</strong> —
          never assumed true. An order can only ever fire on real, complete information.
        </Callout>
      </Section>

      <Section title="Actions — what happens when the trigger fires">
        <ActionCard
          name="buy"
          label="Enter a new position"
          color="#22c55e"
          desc="Only ever fires on top of a REAL, already-existing BUY-eligible signal for the symbol — it never fabricates one. Routes through the exact same entry gate every organic trade goes through (Decision Engine, or the fallback gate if DE is unreachable) with the exact same position-sizing math. A conditional buy only ever decides WHEN to enter, never whether the setup itself is valid."
          requires="A real BUY signal must already exist for the symbol. No open position already in this symbol on this portfolio. Room under max_positions."
        />
        <ActionCard
          name="sell_partial"
          label="Sell a fraction of your position"
          color="#f59e0b"
          desc="Sells the given fraction (0–1) of your current shares — e.g. 0.5 sells half. The rest of the position stays open, unchanged."
          requires="An open position in this symbol on this portfolio."
        />
        <ActionCard
          name="sell_all / close_position"
          label="Close the whole position"
          color="#f87171"
          desc="Fully closes your open position at the live price, exactly like a normal exit — cash is credited, P&L is recorded, and (if this portfolio is linked to a real broker) a real sell order is routed through it too."
          requires="An open position in this symbol on this portfolio."
        />
        <ActionCard
          name="tighten_stop"
          label="Move your stop-loss up"
          color="#38bdf8"
          desc="Sets a new, tighter stop price. Can only ever move the stop UP (never loosen it) — the same monotonic rule every other stop-tightening mechanism in this app already follows."
          requires="An open position in this symbol. The new stop must be above the current stop."
        />
        <ActionCard
          name="alert_only"
          label="Just tell me"
          color="#a78bfa"
          desc="Sends you the notification email — never touches any position. Use this when you want to be notified the moment your condition is true, but want to decide the actual trade yourself."
        />
      </Section>

      <Section title="What you'll see happen">
        <SubSection title="Every order gets exactly one email">
          Whether the trigger fires successfully or the action fails for some reason (e.g. the entry
          gate rejected a buy, or there wasn&apos;t enough cash), you get an email either way — a
          conditional order is meant to act on your behalf while you&apos;re not watching, so a
          silent failure would defeat the whole point.
        </SubSection>
        <SubSection title="Status lifecycle">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {[
              ['pending', '#64748b', 'Waiting for its trigger to become true'],
              ['triggered', '#22c55e', 'Fired, and the action succeeded'],
              ['failed', '#f87171', 'Fired, but the action itself was rejected/failed'],
              ['expired', '#f59e0b', 'Reached its own expiration time before triggering'],
              ['cancelled', '#94a3b8', 'You cancelled it, or its portfolio no longer exists'],
            ].map(([label, color, desc]) => (
              <div key={label} style={{ padding: '8px 12px', borderRadius: 8, background: '#0d1424', border: `1px solid ${color}33`, minWidth: 180 }}>
                <code style={{ color, fontWeight: 700, fontSize: '12px' }}>{label}</code>
                <div style={{ fontSize: '11.5px', color: '#94a3b8', marginTop: 3 }}>{desc}</div>
              </div>
            ))}
          </div>
        </SubSection>
      </Section>

      <Callout tone="example" title="Worked example — the exact use case this feature was built for">
        You watch NVDA and believe a break above $140 confirms a real breakout, but you don&apos;t want
        to wait for the normal signal-refresh cycle to catch up. You create a{' '}
        <Code>buy</Code> order: trigger <Code>price &ge; 140</Code>, on your GROWTH portfolio. The
        moment NVDA crosses $140, the order checks whether a real BUY signal already exists for NVDA —
        if it does, it enters through the exact same gate and sizing math a normal trade would use, and
        you get an email confirming the fill (or explaining why it was rejected, e.g. the entry score
        was too low). If no real BUY signal exists yet, the order fails with that exact reason — it
        never buys on price alone.
      </Callout>

      <Callout tone="warn" title="What this does NOT do">
        <ul style={{ paddingLeft: 18, lineHeight: 1.7, margin: 0 }}>
          <li>No cross-symbol triggers — you cannot say &ldquo;if SPY breaks down, sell my NVDA.&rdquo; Every order watches and acts on exactly one symbol.</li>
          <li>No multi-step chains — an order does not trigger another order. Create separate orders if you want a multi-step plan.</li>
          <li>Paper trading only — actions operate on your paper portfolio&apos;s simulated positions. If that portfolio happens to be linked to a real broker connection, the same broker-routing your normal paper trades already use also applies here — this feature doesn&apos;t add a separate real-money path of its own.</li>
          <li>No editing — cancel a pending order and create a new one if you want to change its trigger or action.</li>
        </ul>
      </Callout>
    </div>
  );
}
