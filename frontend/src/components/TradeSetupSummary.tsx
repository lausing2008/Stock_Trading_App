import React from 'react';
import type { TradeSetup } from '@/lib/copilot';

export default function TradeSetupSummary({ setups, currency, compact = false, onLevelSelect }: {
  setups: TradeSetup[]; currency: string | null; compact?: boolean;
  onLevelSelect?: (level: { price: number; label: string; role: string }) => void;
}) {
  function cash(value: number | null) {
    if (value === null) return 'Not measurable';
    if (currency && /^[A-Z]{3}$/.test(currency)) {
      return new Intl.NumberFormat('en-US', { style: 'currency', currency, currencyDisplay: 'code', maximumFractionDigits: 4 }).format(value);
    }
    return `${value.toLocaleString('en-US', { maximumFractionDigits: 4 })} (currency unknown)`;
  }
  function price(value: number | null, label: string, role: string, setup: TradeSetup) {
    const clickable = value !== null && Number.isFinite(value) && value > 0 && ['conditional', 'exit_triggered'].includes(setup.state) && onLevelSelect;
    return clickable ? <button type="button" className="trade-level-button" aria-label={`Highlight ${label} on chart`} onClick={() => onLevelSelect!({ price: value!, label, role })}>{cash(value)}</button> : cash(value);
  }
  return <div>
    {setups.map(s => <article key={s.id} aria-label={s.title} className={compact ? 'trade-setup-compact' : undefined} style={{ padding: 10, margin: '10px 0', border: '1px solid #475569', borderRadius: 7 }}>
      <h5 style={{ margin: compact ? '0 12px 6px 0' : '0 0 6px', fontSize: 13 }}>{s.title}</h5>
      <p><strong>{s.state.replaceAll('_', ' ')}</strong> · Daily-close confirmation</p>
      {s.reasons.map(x => <p key={x} style={{ color: '#fbbf24' }}>{x}</p>)}
      <dl className="trade-setup-levels">
        <div className="trade-setup-level"><dt>{s.shares_basis === 'held' ? 'Original entry cost' : 'Entry reference'}</dt><dd>{price(s.entry, 'Entry', 'support', s)}</dd></div>
        <div className="trade-setup-level"><dt>Stop loss to assess</dt><dd>{price(s.stop, 'Stop', 'stop', s)}</dd></div>
        {s.targets.map((t, i) => <React.Fragment key={i}>
          <div className="trade-setup-level"><dt>Take profit {i + 1}</dt><dd>{price(t.price, `TP${i + 1}`, 'target', s)} · {t.label}{t.reward_risk !== null ? ` · ${t.reward_risk.toFixed(2)}R` : ' · R unknown'}</dd></div>
        </React.Fragment>)}
      </dl>
      <p><strong>{s.shares_basis === 'held' ? 'Shares held' : 'Risk-sized whole shares'}:</strong> <span>{s.shares ?? (s.shares_basis === 'held' ? 'Enter shares held' : 'Enter a risk budget')}</span></p>
      {compact && <><p className="trade-next-step"><strong>Next step:</strong> {s.trigger}</p><p className="trade-earnings-caution">{s.earnings}</p></>}
      {!compact && <ol className="trade-setup-steps" aria-label={`${s.title} guidance`}>
        <li>{s.trigger}</li><li>{s.invalidation}</li><li>{s.earnings}</li>
      </ol>}
      {!!s.playbook?.length && <section aria-label={`${s.title} playbook`} className="trade-playbook">
        <p style={{ margin: '8px 0 4px' }}><strong>Playbook</strong> <span style={{ color: '#94a3b8' }}>· what to do in each case; rules, not a forecast</span></p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 6 }}>
          {s.playbook.map(b => <div key={b.case} style={{ padding: 8, border: '1px solid #334155', borderRadius: 6 }}>
            <strong style={{ color: b.case === 'rises' ? '#4ade80' : b.case === 'falls' ? '#f87171' : b.case === 'sideways' ? '#fbbf24' : '#a5b4fc' }}>{b.title}</strong>
            <p style={{ margin: '4px 0 0' }}>{b.text}</p>
          </div>)}
        </div>
      </section>}
      <details><summary style={{ cursor: 'pointer', color: '#a5b4fc' }}>{compact ? 'Steps, sizing & risks' : 'Sizing, risk & supporting levels'}</summary>
      {compact && <ol className="trade-setup-steps" aria-label={`${s.title} guidance`}><li>{s.trigger}</li><li>{s.invalidation}</li><li>{s.earnings}</li></ol>}
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
