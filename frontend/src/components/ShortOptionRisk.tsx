import React, { useId, useState } from 'react';
import type { OptionStrategy } from '@/lib/api';
import { shortIncomeKind, shortIncomeScenario } from '@/lib/optionExpiryRisk';

const usd = (n: number) => `${n < 0 ? '−' : ''}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const signed = (n: number) => `${n > 0 ? '+' : ''}${usd(n)}`;
const cell: React.CSSProperties = { padding: 7, textAlign: 'left', borderBottom: '1px solid #334155', verticalAlign: 'top' };
const numeric: React.CSSProperties = { ...cell, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' };

export default function ShortOptionRisk({ strategy, referenceStock }: { strategy: OptionStrategy; referenceStock: number }) {
  const [custom, setCustom] = useState('');
  const inputId = useId();
  const kind = shortIncomeKind(strategy);
  if (!kind) return null;
  const label = kind === 'covered_call' ? 'Covered Call' : 'Cash-Secured Put';
  const zero = shortIncomeScenario(strategy, referenceStock, 0);
  if (!zero) return <p>{label} scenarios unavailable: stock/option references, supported terms and reported payoff bounds must agree. Refresh the plan before using these figures.</p>;
  const strike = strategy.legs[0].strike;
  const choices = [...new Set([0, Number((referenceStock * .8).toFixed(2)), referenceStock, strike, Number((Math.max(referenceStock, strike) * 1.2).toFixed(2))])].sort((a,b)=>a-b);
  const value = custom.trim() === '' ? null : Number(custom);
  const valid = value === null || (Number.isFinite(value) && value >= 0);
  const result = value !== null && valid ? shortIncomeScenario(strategy, referenceStock, value) : null;
  const outcome = (price: number) => price === strike ? 'At strike: assignment is uncertain; zero option intrinsic modeled.'
    : kind === 'covered_call'
      ? price > strike ? `Assignment scenario: deliver 100 shares at ${usd(strike)}.` : 'Call has no expiry intrinsic; retain the shares in this model.'
      : price < strike ? `Assignment scenario: pay ${usd(strike * 100)} for 100 shares.` : 'Put has no expiry intrinsic; no shares purchased in this model.';
  return <section aria-label={`${label} expiry risk`} style={{ marginTop: 12, fontSize: 12, color: '#cbd5e1' }}>
    <p><strong>Worst case at stock $0:</strong> {usd(-zero.total)} loss. {kind === 'covered_call'
      ? `This includes 100 shares valued at the ${usd(referenceStock)} reference, less the ${usd(zero.premium)} call premium. A higher strike caps the selling price; it does not stop the shares losing value.`
      : `You may have to buy 100 shares for ${usd(strike * 100)} even when they are worth less. The ${usd(zero.premium)} premium reduces that loss. Cash-secured means funded, not protected against a fall.`}</p>
    <details><summary style={{ color: '#7dd3fc', cursor: 'pointer', padding: '4px 0' }}>Why the loss is large — expiry scenarios</summary>
      <p>One standard 100-share contract, before fees, taxes and dividends. {kind === 'covered_call'
        ? 'Total P/L includes stock plus the short call, measured from reference spot—not your stock purchase cost.'
        : 'Total P/L includes the short put’s purchase obligation. Effective share entry if assigned is strike minus premium per share.'}
        {' '}These are expiration illustrations, not probabilities or current liquidation values.</p>
      <label htmlFor={inputId} style={{ display: 'block', marginTop: 10 }}>Hypothetical stock price at expiry ($)</label>
      <input id={inputId} type="number" min="0" step="0.01" value={custom} onChange={e=>setCustom(e.target.value)} style={{ maxWidth: '100%', width: 180, padding: 6, background: '#0f172a', color: '#e2e8f0', border: '1px solid #475569', borderRadius: 5 }} />
      {!valid && <p role="alert">Enter a finite stock price of $0 or more.</p>}
      {result && <p role="status">At {usd(value!)}: stock/assignment effect {signed(result.stockOrAssignmentEffect)} + premium {usd(result.premium)} = total {signed(result.total)}. {outcome(value!)}</p>}
      <p>Preset expiry scenarios — scroll the table for the full decomposition.</p>
      <div role="region" aria-label={`${label} scenario table`} tabIndex={0} style={{ maxWidth: '100%', overflowX: 'auto' }}>
        <table aria-label={`${label} expiry scenarios`} style={{ width: '100%', minWidth: 580, fontSize: 12 }}>
          <thead><tr>{['Stock at expiry', 'Total P/L', 'Stock / assignment effect', 'Premium received', 'Modeled outcome'].map(h=><th key={h} style={cell}>{h}</th>)}</tr></thead>
          <tbody>{choices.map(price=>{
            const s=shortIncomeScenario(strategy,referenceStock,price)!;
            return <tr key={price}><td style={numeric}>{usd(price)}{price===0?' — worst case':''}</td><td style={{...numeric,color:s.total<0?'#f87171':'#4ade80'}}>{signed(s.total)}</td><td style={numeric}>{signed(s.stockOrAssignmentEffect)}</td><td style={numeric}>{signed(s.premium)}</td><td style={cell}>{outcome(price)}</td></tr>;
          })}</tbody>
        </table>
      </div>
      <p>Touching a strike does not automatically cause assignment. Early assignment can occur; its timing and cash flows are not modeled here. A stock-price target is not a guaranteed exit.</p>
      <a href={kind==='covered_call'?'https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write':'https://www.optionseducation.org/strategies/all-strategies/cash-secured-put'} target="_blank" rel="noreferrer">OIC strategy mechanics</a>
    </details>
  </section>;
}
