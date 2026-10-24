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
import React, { useState } from 'react';
import { COMMON_OPTION_KEYS, COMMON_OPTION_NAMES, recommendCommonOption, type OptionPlanContext, type OptionGoal, type CommonOptionKey } from '@/lib/commonOptionPlans';
import type { OptionStrategy, OptionStrategyMatrix } from '@/lib/api';

const CARD: React.CSSProperties = {
  background: '#0b1220', border: '1px solid #1e293b', borderRadius: 8, padding: 14,
};
const LABEL: React.CSSProperties = {
  fontSize: 10, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em',
};

const fmtUSD = (n: number | null | undefined) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const measurement = (n: number | null | undefined) =>
  n == null || !Number.isFinite(n) ? 'unavailable' : n.toFixed(4);
const candidateReason = (reason: string) => ({
  unsupported_contract: 'unsupported contract terms', invalid_or_expired_contract: 'invalid or expired contract',
  two_sided_quote_required: 'missing a usable two-sided quote', crossed_quote: 'crossed bid/ask',
  spread_exceeds_20_percent: 'spread above the 20% research ceiling', non_positive_max_profit: 'non-positive maximum payoff',
  no_chain_rows: 'no call-chain rows', invalid_payoff: 'inconsistent payoff inputs',
}[reason] ?? 'unavailable contract evidence');

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

function StrategyCard({ s, spot, highlight, review }: { s: OptionStrategy; spot: number; highlight: boolean;
  review?: ReturnType<typeof recommendCommonOption>['plans'][CommonOptionKey] }) {
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
      <p style={{ fontSize: 12, color: '#cbd5e1' }}>{s.legs.map((leg, i) => <span key={i}>
        {i > 0 && ' + '}{leg.action.toUpperCase()} {fmtUSD(leg.strike)} {leg.right} · expires {leg.expiry ?? 'unknown'}
        {leg.days_to_expiry != null && ` (${leg.days_to_expiry} days)`}
      </span>)}</p>
      <p style={{ fontSize: 11, color: '#94a3b8' }}>{s.legs.map((leg, i) => <span key={i}>
        {i > 0 && ' · '}OI {leg.oi == null ? 'unavailable' : leg.oi.toLocaleString()}
        {' · '}Delta {measurement(leg.delta)} · Gamma {measurement(leg.gamma)}
        {' · '}Theta {measurement(leg.theta)} · Vega {measurement(leg.vega)}
        {' · '}Spread {leg.spread_pct == null ? 'unavailable' : `${leg.spread_pct}%`}
      </span>)}</p>
      <p style={{ fontSize: 10, color: '#94a3b8' }}>Greeks describe the option contract, not the combined strategy exposure. Snapshot estimates; units are provider conventions. They do not establish a probability of profit.</p>
      <p style={{ fontSize: 10, color: '#94a3b8' }}>Expiry economics per standard contract, before fees. Stock legs use the reference spot.</p>

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

      {review && <div className="option-strategy-playbook">
        <p><strong>Entry:</strong> {review.entry}</p>
        <p><strong>Take profit / objective:</strong> {review.target}</p>
        <p><strong>Stop / invalidation:</strong> {review.stop}</p>
        {!!review.blockers.length && <p className="option-strategy-blockers"><strong>Not eligible for preference:</strong> {review.blockers.join(' ')}</p>}
        <details><summary>Playbook — before entry, while open & exit</summary><ol>{review.steps.map(step => <li key={step}>{step}</li>)}</ol></details>
      </div>}
      <details><summary>Expiry payoff illustration</summary><PayoffChart s={s} spot={spot} /></details>

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
              {/* SF-04: WHERE the number came from. A last-trade price has no spread, so the
                  warning above cannot fire for it — absence of a warning was reading as
                  reassurance on a price that may be hours old and may not be obtainable. */}
              {l.price_source === 'last_trade' && (
                <span style={{ color: '#f59e0b' }} title="No two-sided quote was available for this contract, so the figure shown is the last price it actually traded at — at an unknown time. There is no spread to report, which is why no spread warning appears.">
                  {' '}· last traded price, not a live quote — no spread available
                </span>
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

export default function OptionStrategyMatrixPanel({ matrix, spot, stopLoss, takeProfit, chainAsOf }: {
  matrix: OptionStrategyMatrix; spot: number; stopLoss?: number; takeProfit?: number; chainAsOf?: string | null;
}) {
  const [goal, setGoal] = useState<OptionGoal>('compare');
  const [shares, setShares] = useState('');
  const [cash, setCash] = useState('');
  const [premiumBudget, setPremiumBudget] = useState('');
  const [coveredChoice, setCoveredChoice] = useState<{ source: OptionStrategyMatrix; index: number } | null>(null);
  const [assignmentAcceptance, setAssignmentAcceptance] = useState<string | null>(null);
  const candidates = matrix.candidate_review?.covered_call;
  const alternative = coveredChoice?.source === matrix ? candidates?.alternatives[coveredChoice.index] : undefined;
  const comparedMatrix = alternative ? { ...matrix, candidate_review: undefined, singles: { ...matrix.singles, covered_call: alternative } } : matrix;
  const assignmentKey = JSON.stringify([comparedMatrix.singles.covered_call?.legs, comparedMatrix.singles.cash_secured_put?.legs, chainAsOf]);
  const acceptAssignment = assignmentAcceptance === assignmentKey;
  const amount = (text: string) => text.trim() && Number.isFinite(Number(text)) && Number(text) >= 0 ? Number(text) : null;
  const context: OptionPlanContext = { goal, shares: amount(shares), cash: amount(cash), premiumBudget: amount(premiumBudget), acceptAssignment, stop: stopLoss, target: takeProfit };
  const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const review = recommendCommonOption(comparedMatrix, context, today, chainAsOf);
  const comboKeys = Object.keys(matrix.combos);
  return <section aria-label="Four common option strategies" style={{ marginTop: 16 }}>
    <h3>Four common strategies & conditional preference</h3>
    <p>Compare one standard 100-share contract. These are research illustrations using settled provider prices
      {chainAsOf ? ` observed ${chainAsOf}` : ' with an unknown observation date'}, not live executable quotes.</p>
    <p>Original contract selection uses the nearest strike to spot, the stock stop or target, and preset expiry windows. Covered-call alternatives screen the supplied rows of the selected call expiry; choosing one changes this comparison only. Delta, gamma, open interest and breakeven are not jointly optimized. This comparison does not establish the best return.</p>
    <div className="option-strategy-inputs">
      <label>Objective<select aria-label="Objective" value={goal} onChange={e => setGoal(e.target.value as OptionGoal)}>
        <option value="compare">Compare only — choose an objective</option><option value="upside">Upside / bullish exposure</option>
        <option value="income">Income / willing to own stock</option><option value="protect">Protect existing shares</option><option value="bearish">Bearish / reduce stock risk</option>
      </select></label>
      <label>Unencumbered shares<input type="number" min="0" value={shares} onChange={e => setShares(e.target.value)} /></label>
      <label>Cash reserved for assignment ($)<input type="number" min="0" value={cash} onChange={e => setCash(e.target.value)} /></label>
      <label>Option premium-loss budget ($)<input type="number" min="0" value={premiumBudget} onChange={e => setPremiumBudget(e.target.value)} /></label>
    </div>
    <label><input type="checkbox" checked={acceptAssignment} onChange={e => setAssignmentAcceptance(e.target.checked ? assignmentKey : null)} /> I accept buying or selling 100 shares at the listed strike if assigned.</label>
    <div role="status" style={{ ...CARD, borderColor: review.primary ? '#38bdf8' : '#475569', margin: '14px 0' }}>
      <strong>{review.primary ? `Preferred strategy to research: ${COMMON_OPTION_NAMES[review.primary]}` : 'Wait / no strategy preference yet'}</strong>
      <p>{review.reason}</p><p>{review.ivNote}</p>
      <p>Entry status: WAIT for fresh quotes and broker checks. Holdings and budgets above are your declarations;
        account permissions, deliverables, event coverage and portfolio risk are unverified. No quantity or order is recommended.</p>
      <p>Premium targets and exits below are illustrative policies, not calibrated forecasts or guaranteed fills.
        For hedges and covered calls, the premium budget does not cap stock losses.</p>
    </div>
    {candidates && <section aria-label="Covered call contract selection" style={{ ...CARD, marginBottom: 14 }}>
      <h4>Covered call — contract selection</h4>
      <p>Original anchor {fmtUSD(candidates.anchor)} · expiry {candidates.expiry ?? 'unknown'} · selected strike {fmtUSD(candidates.original.strike)}.</p>
      <p>{candidates.original.reason ? `Original candidate: ${candidateReason(candidates.original.reason)}.` : 'Original candidate passed the basic quote screen; holdings, freshness and broker checks remain separate.'}</p>
      <p>{candidates.screened_count} of {candidates.evaluated_count} rows passed the research quote/payoff screen. Counts cover supplied rows after upstream contract exclusions. Scope: one selected expiration; other expirations are not searched.</p>
      {!!Object.keys(candidates.rejected_counts).length && <p>Excluded: {Object.entries(candidates.rejected_counts).map(([reason, count]) => `${count} ${candidateReason(reason)}`).join('; ')}.</p>}
      <p>{candidates.ranking} The 20% spread ceiling is a research policy; quote age, size and actual deliverables are unverified.</p>
      {candidates.alternatives.length ? <label>Covered-call contract to compare<select aria-label="Covered-call contract to compare"
        value={alternative && coveredChoice ? String(coveredChoice.index) : 'original'} onChange={e => {
          setCoveredChoice(e.target.value === 'original' ? null : { source: matrix, index: Number(e.target.value) });
          setAssignmentAcceptance(null);
        }}>
        <option value="original">Original target-strike selection</option>
        {candidates.alternatives.map((s, i) => <option key={s.legs[0].option_symbol ?? i} value={String(i)}>
          Compare {fmtUSD(s.legs[0].strike)} call · {s.legs[0].expiry} · {fmtUSD(s.net_per_contract)} credit before fees
        </option>)}
      </select></label> : <p>No other contract passed this screen. This does not establish absence in other expirations.</p>}
      {alternative && <p role="status">Comparing an explicitly selected alternative at {fmtUSD(alternative.legs[0].strike)}.
        {' '}Stock target remains {fmtUSD(takeProfit)}; selling this call caps stock upside at its strike.
        {' '}Confirm assignment acceptance again. Combinations still use their original legs. This is not an expected-return ranking.</p>}
    </section>}
    <div className="common-option-strategy-grid">
      {COMMON_OPTION_KEYS.map(key => comparedMatrix.singles[key]
        ? <StrategyCard key={key} s={comparedMatrix.singles[key]} spot={spot} highlight={review.primary === key} review={review.plans[key]} />
        : <article key={key} style={CARD}><h4>{COMMON_OPTION_NAMES[key]}</h4>
          <p>No usable priced contract at the selected strike and expiry. {candidates && key === 'covered_call' ? 'Select a screened alternative above if available.' : 'Nearby strikes have not been exhaustively searched.'} Entry, premium targets and stops are unavailable; refresh the chain.</p>
          <p>{review.plans[key].blockers.join(' ')}</p><details><summary>Playbook — before entry, while open & exit</summary><ol>{review.plans[key].steps.map(step => <li key={step}>{step}</li>)}</ol></details>
        </article>)}
    </div>
    {!!comboKeys.length && <details style={{ marginTop: 16 }}><summary>More strategies — combinations</summary><div className="common-option-strategy-grid">
      {comboKeys.map(key => <StrategyCard key={key} s={matrix.combos[key]} spot={spot} highlight={false} />)}
    </div></details>}
    {!!Object.keys(matrix.unavailable ?? {}).length && <details><summary>Unavailable combinations and reasons</summary>
      {Object.entries(matrix.unavailable ?? {}).map(([key, item]) => <p key={key}><strong>{item.name}:</strong> {item.reason}</p>)}
    </details>}
    <p>Strategy mechanics: <a href="https://www.optionseducation.org/strategies/all-strategies/long-call">OIC long call</a>, <a href="https://www.optionseducation.org/strategies/all-strategies/protective-put">protective put</a>, <a href="https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write">covered call</a>, <a href="https://www.optionseducation.org/strategies/all-strategies/cash-secured-put">cash-secured put</a>.</p>
  </section>;
}
