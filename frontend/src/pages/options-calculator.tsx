/**
 * T402-OPTIONS-CALCULATOR — price your own structure, leg by leg.
 *
 * The Options Game Plan prices contracts the engine picked. This is the other half: build any
 * combination yourself and see the payoff, breakevens, max loss/profit and capital required
 * before committing. Entirely client-side arithmetic — no fetch, no live quotes — because the
 * question it answers ("if I buy THIS, what happens?") does not need them, and pretending to
 * quote live prices here would be the dishonest part.
 *
 * DELIBERATE SCOPE LIMIT, stated on the page as well as here: this is an EXPIRY payoff
 * calculator, not an option pricing model. It does not compute theoretical value before
 * expiry, and it has no Greeks — that needs Black-Scholes plus an IV surface, which this app
 * does not have (see the Option Trading Guide's own known-limitations note). Showing a
 * confident mid-life P&L from a model that does not exist would be worse than showing none.
 */
import { useMemo, useState } from 'react';
import { useRouter } from 'next/router';
import Head from 'next/head';

type Leg = {
  id: number;
  action: 'buy' | 'sell';
  right: 'call' | 'put';
  strike: number;
  premium: number;
  contracts: number;
};

const CONTRACT = 100;

const CARD: React.CSSProperties = {
  background: '#0b1220', border: '1px solid #1e293b', borderRadius: 8, padding: 16,
};
const LABEL: React.CSSProperties = {
  fontSize: 10, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: 4,
};
const INPUT: React.CSSProperties = {
  background: '#0f172a', border: '1px solid #334155', borderRadius: 6, color: '#e2e8f0',
  padding: '6px 8px', fontSize: 12, width: '100%',
};

const PRESETS: Record<string, (spot: number) => Omit<Leg, 'id'>[]> = {
  'Long Call': s => [{ action: 'buy', right: 'call', strike: r(s), premium: 3, contracts: 1 }],
  'Cash-Secured Put': s => [{ action: 'sell', right: 'put', strike: r(s * 0.95), premium: 2, contracts: 1 }],
  'Protective Put': s => [{ action: 'buy', right: 'put', strike: r(s * 0.95), premium: 2, contracts: 1 }],
  'Covered Call': s => [{ action: 'sell', right: 'call', strike: r(s * 1.05), premium: 2, contracts: 1 }],
  'Bull Call Spread': s => [
    { action: 'buy', right: 'call', strike: r(s), premium: 4, contracts: 1 },
    { action: 'sell', right: 'call', strike: r(s * 1.08), premium: 1.5, contracts: 1 },
  ],
  'Collar': s => [
    { action: 'buy', right: 'put', strike: r(s * 0.95), premium: 2, contracts: 1 },
    { action: 'sell', right: 'call', strike: r(s * 1.05), premium: 1.8, contracts: 1 },
  ],
  'Straddle': s => [
    { action: 'buy', right: 'call', strike: r(s), premium: 3.5, contracts: 1 },
    { action: 'buy', right: 'put', strike: r(s), premium: 3.2, contracts: 1 },
  ],
  'Iron Condor': s => [
    { action: 'sell', right: 'put', strike: r(s * 0.95), premium: 1.8, contracts: 1 },
    { action: 'buy', right: 'put', strike: r(s * 0.90), premium: 0.9, contracts: 1 },
    { action: 'sell', right: 'call', strike: r(s * 1.05), premium: 1.7, contracts: 1 },
    { action: 'buy', right: 'call', strike: r(s * 1.10), premium: 0.8, contracts: 1 },
  ],
};

function r(n: number) { return Math.round(n * 2) / 2; }
const fmt = (n: number) => `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** P&L at expiry. Intrinsic value only — that IS the whole payoff once expired. */
function payoff(legs: Leg[], spotAtExpiry: number, shares: number, entrySpot: number): number {
  let pnl = shares * (spotAtExpiry - entrySpot);
  for (const l of legs) {
    const intrinsic = l.right === 'call'
      ? Math.max(0, spotAtExpiry - l.strike)
      : Math.max(0, l.strike - spotAtExpiry);
    const sign = l.action === 'buy' ? 1 : -1;
    pnl += sign * (intrinsic - l.premium) * CONTRACT * l.contracts;
  }
  return pnl;
}

export default function OptionsCalculator() {
  const router = useRouter();
  const qSpot = Number(router.query.spot);
  const symbol = typeof router.query.symbol === 'string' ? router.query.symbol : '';

  const [spot, setSpot] = useState<number>(Number.isFinite(qSpot) && qSpot > 0 ? qSpot : 100);
  const [shares, setShares] = useState<number>(0);
  const [nextId, setNextId] = useState(2);
  const [legs, setLegs] = useState<Leg[]>([
    { id: 1, action: 'buy', right: 'call', strike: 100, premium: 3, contracts: 1 },
  ]);

  const applyPreset = (name: string) => {
    const built = PRESETS[name](spot);
    setLegs(built.map((l, i) => ({ ...l, id: i + 1 })));
    setNextId(built.length + 1);
    if (name === 'Protective Put' || name === 'Covered Call' || name === 'Collar') {
      setShares(100 * built.filter(l => l.action === 'sell' || l.right === 'put').length || 100);
    } else setShares(0);
  };

  const update = (id: number, patch: Partial<Leg>) =>
    setLegs(ls => ls.map(l => (l.id === id ? { ...l, ...patch } : l)));

  const stats = useMemo(() => {
    const lo = Math.max(0.01, spot * 0.5), hi = spot * 1.5;
    const steps = 400;
    const pts = Array.from({ length: steps + 1 }, (_, i) => {
      const px = lo + ((hi - lo) * i) / steps;
      return { px, pnl: payoff(legs, px, shares, spot) };
    });
    const netCash = legs.reduce(
      (acc, l) => acc + (l.action === 'buy' ? -1 : 1) * l.premium * CONTRACT * l.contracts, 0);

    // Breakevens: sign changes along the sampled curve, refined by linear interpolation.
    const bes: number[] = [];
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i];
      if ((a.pnl <= 0 && b.pnl > 0) || (a.pnl >= 0 && b.pnl < 0)) {
        const t = Math.abs(a.pnl) / (Math.abs(a.pnl) + Math.abs(b.pnl));
        bes.push(a.px + (b.px - a.px) * t);
      }
    }
    const minP = Math.min(...pts.map(p => p.pnl));
    const maxP = Math.max(...pts.map(p => p.pnl));
    // An edge sample that is still the extreme means the curve keeps going that way — report
    // "unbounded" rather than the arbitrary value at the edge of the sampled window.
    const lossUnbounded = pts[pts.length - 1].pnl === minP || pts[0].pnl === minP;
    const profitUnbounded = pts[pts.length - 1].pnl === maxP;

    const collateral = legs.reduce((acc, l) => {
      if (l.action === 'buy') return acc + l.premium * CONTRACT * l.contracts;
      if (l.right === 'put') return acc + l.strike * CONTRACT * l.contracts;   // cash-secured
      return acc;                                                             // covered by shares
    }, 0) + shares * spot;

    return { pts, netCash, bes, minP, maxP, lossUnbounded, profitUnbounded, collateral };
  }, [legs, shares, spot]);

  const w = 720, h = 260, pad = 30;
  const lo = Math.max(0.01, spot * 0.5), hi = spot * 1.5;
  const maxAbs = Math.max(Math.abs(stats.minP), Math.abs(stats.maxP), 1);
  const X = (px: number) => pad + ((px - lo) / (hi - lo)) * (w - pad * 2);
  const Y = (p: number) => h / 2 - (p / maxAbs) * (h / 2 - pad);
  const path = stats.pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${X(p.px).toFixed(1)},${Y(p.pnl).toFixed(1)}`).join(' ');

  return (
    <>
      <Head><title>Options Calculator — StockAI</title></Head>
      <div style={{ padding: '20px 24px', maxWidth: 1200, margin: '0 auto' }}>
        <h1 style={{ fontSize: 20, color: '#f1f5f9', marginBottom: 4 }}>
          Options Calculator {symbol && <span style={{ color: '#64748b', fontSize: 14 }}>· {symbol}</span>}
        </h1>
        <p style={{ fontSize: 12, color: '#64748b', marginBottom: 18, maxWidth: 760, lineHeight: 1.6 }}>
          Build any structure and see what it does at expiry. Payoff, breakevens, max loss and
          capital required are exact arithmetic — no model, no live quotes. Enter the premium
          you would actually pay or receive.
        </p>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
          {Object.keys(PRESETS).map(name => (
            <button key={name} onClick={() => applyPreset(name)} style={{
              padding: '6px 12px', borderRadius: 6, fontSize: 11, cursor: 'pointer',
              border: '1px solid #334155', background: 'transparent', color: '#cbd5e1',
            }}>{name}</button>
          ))}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 16 }}>
          <div style={CARD}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 14 }}>
              <div><label style={LABEL}>Underlying price</label>
                <input style={INPUT} type="number" step="0.01" value={spot}
                  onChange={e => setSpot(Math.max(0.01, Number(e.target.value) || 0.01))} /></div>
              <div><label style={LABEL}>Shares held</label>
                <input style={INPUT} type="number" step="100" value={shares}
                  onChange={e => setShares(Number(e.target.value) || 0)} /></div>
            </div>

            {legs.map(l => (
              <div key={l.id} style={{ borderTop: '1px solid #1e293b', paddingTop: 10, marginTop: 10 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                  <div><label style={LABEL}>Action</label>
                    <select style={INPUT} value={l.action} onChange={e => update(l.id, { action: e.target.value as 'buy' | 'sell' })}>
                      <option value="buy">Buy</option><option value="sell">Sell</option>
                    </select></div>
                  <div><label style={LABEL}>Type</label>
                    <select style={INPUT} value={l.right} onChange={e => update(l.id, { right: e.target.value as 'call' | 'put' })}>
                      <option value="call">Call</option><option value="put">Put</option>
                    </select></div>
                  <div><label style={LABEL}>Contracts</label>
                    <input style={INPUT} type="number" min={1} value={l.contracts}
                      onChange={e => update(l.id, { contracts: Math.max(1, Number(e.target.value) || 1) })} /></div>
                  <div><label style={LABEL}>Strike</label>
                    <input style={INPUT} type="number" step="0.5" value={l.strike}
                      onChange={e => update(l.id, { strike: Number(e.target.value) || 0 })} /></div>
                  <div><label style={LABEL}>Premium /sh</label>
                    <input style={INPUT} type="number" step="0.01" value={l.premium}
                      onChange={e => update(l.id, { premium: Number(e.target.value) || 0 })} /></div>
                  <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                    <button onClick={() => setLegs(ls => ls.filter(x => x.id !== l.id))}
                      disabled={legs.length === 1}
                      style={{ ...INPUT, cursor: legs.length === 1 ? 'not-allowed' : 'pointer', color: '#ef4444', opacity: legs.length === 1 ? 0.4 : 1 }}>
                      Remove
                    </button></div>
                </div>
              </div>
            ))}

            <button onClick={() => { setLegs(ls => [...ls, { id: nextId, action: 'buy', right: 'call', strike: r(spot), premium: 1, contracts: 1 }]); setNextId(n => n + 1); }}
              style={{ marginTop: 12, padding: '6px 12px', borderRadius: 6, fontSize: 11, cursor: 'pointer', border: '1px solid #334155', background: 'transparent', color: '#38bdf8' }}>
              + Add leg
            </button>
          </div>

          <div style={CARD}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(120px,1fr))', gap: 12 }}>
              <div><span style={LABEL}>Net {stats.netCash >= 0 ? 'credit' : 'debit'}</span>
                <div style={{ fontSize: 15, fontWeight: 700, color: stats.netCash >= 0 ? '#22c55e' : '#f59e0b' }}>
                  {fmt(Math.abs(stats.netCash))}</div></div>
              <div><span style={LABEL}>Max loss</span>
                <div style={{ fontSize: 15, fontWeight: 700, color: '#ef4444' }}>
                  {stats.lossUnbounded ? 'Unbounded' : fmt(Math.abs(stats.minP))}</div></div>
              <div><span style={LABEL}>Max profit</span>
                <div style={{ fontSize: 15, fontWeight: 700, color: '#22c55e' }}>
                  {stats.profitUnbounded ? 'Unbounded' : fmt(stats.maxP)}</div></div>
              <div><span style={LABEL}>Capital required</span>
                <div style={{ fontSize: 15, fontWeight: 700, color: '#cbd5e1' }}>{fmt(stats.collateral)}</div></div>
            </div>
            <div style={{ marginTop: 12 }}>
              <span style={LABEL}>Breakeven{stats.bes.length === 1 ? '' : 's'} at expiry</span>
              <div style={{ fontSize: 13, color: '#e2e8f0' }}>
                {stats.bes.length ? stats.bes.map(b => fmt(b)).join('  ·  ') : 'None in the ±50% range shown'}
              </div>
            </div>

            <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 260, marginTop: 14 }}>
              <line x1={pad} y1={Y(0)} x2={w - pad} y2={Y(0)} stroke="#334155" />
              <line x1={X(spot)} y1={10} x2={X(spot)} y2={h - 10} stroke="#475569" strokeDasharray="3,3" />
              <text x={X(spot) + 4} y={18} fill="#64748b" fontSize={10}>now {fmt(spot)}</text>
              {stats.bes.map((b, i) => (
                <line key={i} x1={X(b)} y1={10} x2={X(b)} y2={h - 10} stroke="#22c55e" strokeDasharray="2,3" opacity={0.5} />
              ))}
              <path d={path} fill="none" stroke="#38bdf8" strokeWidth={2} />
              <text x={pad} y={h - 6} fill="#475569" fontSize={10}>{fmt(lo)}</text>
              <text x={w - pad - 40} y={h - 6} fill="#475569" fontSize={10}>{fmt(hi)}</text>
            </svg>
            <div style={{ fontSize: 10, color: '#475569', lineHeight: 1.6 }}>
              Payoff at EXPIRY, per the legs above, including your shares if any. Commissions,
              assignment timing and dividends are not modelled. This is not a pricing model —
              there is no theoretical value before expiry and no Greeks here, because that needs
              Black-Scholes and an IV surface this app does not compute. Early assignment on a
              short leg can change the outcome before expiry.
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
