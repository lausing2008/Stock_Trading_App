/**
 * T402-OPTIONS-STRATEGY-MATRIX — all four legs, the combinations, and which to use.
 *
 * The Options Game Plan showed two plays: BUY a put (hedge) and SELL a call (income). This
 * renders the full grid — the other two corners are BUY a call (leveraged upside, defined
 * risk) and SELL a put (paid to wait for a lower entry) — plus the structures built by
 * combining them, and the recommendation among them with its reasoning stated so it can be
 * disagreed with.
 *
 * Every number comes from a REAL currently-listed contract. Nothing here predicts direction.
 */
import { useState } from 'react';
import type { OptionStrategy, OptionStrategyMatrix } from '@/lib/api';

const CARD: React.CSSProperties = {
  background: '#0b1220', border: '1px solid #1e293b', borderRadius: 8, padding: 14,
};
const LABEL: React.CSSProperties = {
  fontSize: 10, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em',
};

const fmtUSD = (n: number | null | undefined) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** Unbounded is rendered as a word, never as a number — "$0" would read as "no profit". */
const fmtBound = (n: number | null | undefined, unbounded: string) =>
  n == null ? unbounded : fmtUSD(n);

/** Payoff at expiry for one structure, per contract, ignoring commissions.
 *  Long stock is included for structures that require it, because a covered call's payoff
 *  without the stock underneath it is not the thing the user is holding. */
function payoffAt(s: OptionStrategy, spotAtExpiry: number, entrySpot: number): number {
  let pnl = 0;
  if (s.requires_shares) pnl += (spotAtExpiry - entrySpot) * 100;
  for (const leg of s.legs) {
    const intrinsic = leg.right === 'call'
      ? Math.max(0, spotAtExpiry - leg.strike)
      : Math.max(0, leg.strike - spotAtExpiry);
    const sign = leg.action === 'buy' ? 1 : -1;
    pnl += sign * (intrinsic * 100 - leg.cost_per_contract);
  }
  return pnl;
}

function PayoffChart({ s, spot }: { s: OptionStrategy; spot: number }) {
  const lo = spot * 0.75, hi = spot * 1.25;
  const pts = Array.from({ length: 61 }, (_, i) => {
    const px = lo + ((hi - lo) * i) / 60;
    return { px, pnl: payoffAt(s, px, spot) };
  });
  const maxAbs = Math.max(...pts.map(p => Math.abs(p.pnl)), 1);
  const w = 260, h = 90, pad = 4;
  const x = (px: number) => pad + ((px - lo) / (hi - lo)) * (w - pad * 2);
  const y = (pnl: number) => h / 2 - (pnl / maxAbs) * (h / 2 - pad);
  const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.px).toFixed(1)},${y(p.pnl).toFixed(1)}`).join(' ');

  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 90 }} aria-label="Payoff at expiry">
      {/* zero line — the visual break-even */}
      <line x1={pad} y1={h / 2} x2={w - pad} y2={h / 2} stroke="#334155" strokeWidth={1} />
      {/* today's price */}
      <line x1={x(spot)} y1={0} x2={x(spot)} y2={h} stroke="#475569" strokeWidth={1} strokeDasharray="2,2" />
      <path d={d} fill="none" stroke="#38bdf8" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function StrategyCard({ s, spot, highlight }: { s: OptionStrategy; spot: number; highlight: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ ...CARD, borderColor: highlight ? '#22c55e' : '#1e293b' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: highlight ? '#22c55e' : '#e2e8f0' }}>
          {s.name}{highlight && ' ★'}
        </div>
        <div style={{ fontSize: 11, color: s.net === 'credit' ? '#22c55e' : '#f59e0b' }}>
          {s.net === 'credit' ? 'You receive' : 'You pay'} {fmtUSD(Math.abs(s.net_per_contract))}
        </div>
      </div>

      <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 6, lineHeight: 1.5 }}>{s.what_it_does}</div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(88px,1fr))', gap: 8, marginTop: 10 }}>
        <div><div style={LABEL}>Max loss</div>
          <div style={{ fontSize: 12, color: '#ef4444' }}>{fmtBound(s.max_loss_per_contract, 'Unlimited')}</div></div>
        <div><div style={LABEL}>Max profit</div>
          <div style={{ fontSize: 12, color: '#22c55e' }}>{fmtBound(s.max_profit_per_contract, 'Unlimited')}</div></div>
        <div><div style={LABEL}>Breakeven</div>
          <div style={{ fontSize: 12, color: '#cbd5e1' }}>{fmtUSD(s.breakeven)}
            {s.breakeven_move_pct != null && <span style={{ color: '#64748b' }}> ({s.breakeven_move_pct > 0 ? '+' : ''}{s.breakeven_move_pct}%)</span>}
          </div></div>
        <div><div style={LABEL}>Capital</div>
          <div style={{ fontSize: 12, color: '#cbd5e1' }}>{fmtUSD(s.collateral_per_contract)}</div></div>
      </div>

      <PayoffChart s={s} spot={spot} />

      <button onClick={() => setOpen(o => !o)} style={{
        marginTop: 4, background: 'transparent', border: 'none', color: '#38bdf8',
        fontSize: 11, cursor: 'pointer', padding: 0,
      }}>{open ? '▾ Hide legs' : '▸ Show legs & when to use'}</button>

      {open && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, color: '#cbd5e1', marginBottom: 8, lineHeight: 1.5 }}>
            <strong style={{ color: '#94a3b8' }}>Use when:</strong> {s.use_when}
          </div>
          {s.legs.map((l, i) => (
            <div key={i} style={{ fontSize: 11, color: '#94a3b8', padding: '4px 0', borderTop: '1px solid #0f172a' }}>
              <span style={{ color: l.action === 'buy' ? '#38bdf8' : '#f59e0b', fontWeight: 700 }}>
                {l.action.toUpperCase()}
              </span>{' '}
              1× ${l.strike.toFixed(2)} {l.right} · exp {l.expiry} ({l.days_to_expiry}d) · {fmtUSD(l.price_per_share)}/sh
              {l.spread_pct != null && l.spread_pct > 20 && (
                <span style={{ color: '#f59e0b' }}> · wide spread {l.spread_pct}% — you will not fill at this price</span>
              )}
            </div>
          ))}
          {s.requires_shares && (
            <div style={{ fontSize: 10, color: '#f59e0b', marginTop: 6 }}>
              Requires owning 100 shares per contract. The payoff above includes the stock.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function OptionStrategyMatrixPanel({ matrix, spot }: { matrix: OptionStrategyMatrix; spot: number }) {
  const rec = matrix.recommendation;
  const all = { ...matrix.singles, ...matrix.combos };
  const singleKeys = ['long_call', 'covered_call', 'protective_put', 'cash_secured_put'].filter(k => matrix.singles[k]);
  const comboKeys = Object.keys(matrix.combos);

  return (
    <div style={{ marginTop: 16 }}>
      {rec.primary && (
        <div style={{ ...CARD, borderColor: '#22c55e', marginBottom: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: '#22c55e' }}>
            ★ Best fit right now: {rec.name}
          </div>
          <div style={{ fontSize: 12, color: '#cbd5e1', marginTop: 6, lineHeight: 1.6 }}>{rec.reason}</div>
          {rec.iv_note && (
            <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 8, lineHeight: 1.6 }}>
              <strong style={{ color: '#64748b' }}>Volatility:</strong> {rec.iv_note}
              {matrix.iv_rank != null && <> (IV rank {matrix.iv_rank.toFixed(0)}/100)</>}
            </div>
          )}
          {rec.constraint && (
            <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 6, lineHeight: 1.6 }}>
              <strong style={{ color: '#64748b' }}>Your position:</strong> {rec.constraint}
            </div>
          )}
          {!!rec.alternatives?.length && (
            <div style={{ marginTop: 10, borderTop: '1px solid #1e293b', paddingTop: 8 }}>
              <div style={LABEL}>Also reasonable</div>
              {rec.alternatives.map(a => (
                <div key={a.key} style={{ fontSize: 11, color: '#94a3b8', marginTop: 5, lineHeight: 1.5 }}>
                  <strong style={{ color: '#cbd5e1' }}>{a.name}</strong> — {a.reason}
                </div>
              ))}
            </div>
          )}
          <div style={{ fontSize: 10, color: '#64748b', marginTop: 10, lineHeight: 1.5 }}>
            This ranks structures against your position and the current volatility regime. It is
            not a prediction that the trade wins, and it cannot know your tax or margin situation.
          </div>
        </div>
      )}

      <div style={{ ...LABEL, marginBottom: 8 }}>The four legs</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: 12 }}>
        {singleKeys.map(k => <StrategyCard key={k} s={all[k]} spot={spot} highlight={rec.primary === k} />)}
      </div>

      {!!comboKeys.length && (
        <>
          <div style={{ ...LABEL, margin: '18px 0 8px' }}>Combinations</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: 12 }}>
            {comboKeys.map(k => <StrategyCard key={k} s={all[k]} spot={spot} highlight={rec.primary === k} />)}
          </div>
        </>
      )}
    </div>
  );
}
