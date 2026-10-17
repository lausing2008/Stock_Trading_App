import React from 'react';
import type { TradeSetup } from '@/lib/copilot';

export default function TradeSetupSummary({ setups, currency }: { setups: TradeSetup[]; currency: string | null }) {
  function cash(value: number | null) {
    if (value === null) return 'Not measurable';
    if (currency && /^[A-Z]{3}$/.test(currency)) {
      return new Intl.NumberFormat('en-US', { style: 'currency', currency, currencyDisplay: 'code', maximumFractionDigits: 4 }).format(value);
    }
    return `${value.toLocaleString('en-US', { maximumFractionDigits: 4 })} (currency unknown)`;
  }
  return <div>
    {setups.map(s => <article key={s.id} aria-label={s.title} style={{ padding: 10, margin: '10px 0', border: '1px solid #475569', borderRadius: 7 }}>
      <h5 style={{ margin: '0 0 6px', fontSize: 13 }}>{s.title}</h5>
      <p><strong>{s.state.replaceAll('_', ' ')}</strong> · Daily-close confirmation</p>
      {s.reasons.map(x => <p key={x} style={{ color: '#fbbf24' }}>{x}</p>)}
      <dl className="trade-setup-levels">
        <div className="trade-setup-level"><dt>{s.shares_basis === 'held' ? 'Original entry cost' : 'Entry reference'}</dt><dd>{cash(s.entry)}</dd></div>
        <div className="trade-setup-level"><dt>Stop loss to assess</dt><dd>{cash(s.stop)}</dd></div>
        {s.targets.map((t, i) => <React.Fragment key={i}>
          <div className="trade-setup-level"><dt>Take profit {i + 1}</dt><dd>{cash(t.price)} · {t.label}{t.reward_risk !== null ? ` · ${t.reward_risk.toFixed(2)}R` : ' · R unknown'}</dd></div>
        </React.Fragment>)}
      </dl>
      <p><strong>{s.shares_basis === 'held' ? 'Shares held' : 'Risk-sized whole shares'}:</strong> <span>{s.shares ?? (s.shares_basis === 'held' ? 'Enter shares held' : 'Enter a risk budget')}</span></p>
      <ol className="trade-setup-steps" aria-label={`${s.title} guidance`}>
        <li>{s.trigger}</li><li>{s.invalidation}</li><li>{s.earnings}</li>
      </ol>
      <details><summary style={{ cursor: 'pointer', color: '#a5b4fc' }}>Sizing, risk & supporting levels</summary>
      <dl style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
        <dt>Nominal risk per share</dt><dd style={{ margin: 0 }}>{cash(s.risk_per_share)}</dd>
        {s.risk_budget !== null && <><dt>Risk budget ({currency ?? 'currency unknown'})</dt><dd style={{ margin: 0 }}>{cash(s.risk_budget)}</dd></>}
        <dt>{s.shares_basis === 'held' ? 'Position cost' : 'Capital before fees'}</dt><dd style={{ margin: 0 }}>{cash(s.capital)}</dd>
        <dt>Nominal P/L at stop</dt><dd style={{ margin: 0 }}>{cash(s.cash_at_stop)}</dd>
      </dl>
      {s.recovery_levels.map(x => <p key={x.price}>{cash(x.price)} · {x.label}</p>)}
      <p style={{ color: '#94a3b8' }}>{s.risk_note}</p>
      </details>
    </article>)}
    {!!setups.length && <p>Reference prices and conditional objectives, not forecasts or live orders. Recalculate risk and size at the actual fill. Risk budgets use the stock&apos;s currency.</p>}
  </div>;
}
