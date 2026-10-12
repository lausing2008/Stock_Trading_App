import useSWR from 'swr';
import { api, type OptionsFlowAlertRow } from '@/lib/api';

export function LongOptionPlaybookReview({ row }: { row: OptionsFlowAlertRow }) {
  const { data, error, isLoading } = useSWR(
    ['long-option-playbook', row.option_chain, row.fired_date],
    () => api.longOptionPlaybook(row.option_chain, row.fired_date)
  );
  return <section aria-label="Long option research playbook" style={{ border: '1px solid #334155', borderRadius: 8, padding: 14, marginBottom: 16, color: '#cbd5e1' }}>
    <strong>Long option research playbook</strong>
    {isLoading && <p>Loading provider contract evidence…</p>}
    {error && <p style={{ color: '#f87171' }}>Contract evidence is unavailable.</p>}
    {data && <>
      <p>{data.symbol} {data.strategy.replaceAll('_', ' ')} · {data.status.replaceAll('_', ' ')}</p>
      {data.leg ? <p>Archived contract: {data.leg.contract_id}, strike ${data.leg.strike}, expiry {data.leg.expiry}. Archived bid/ask ${data.leg.archived_bid} / ${data.leg.archived_ask} as of {data.leg.quote_as_of}. Illustrative 100-share premium ${data.leg.premium_per_standard_100_share_contract_before_fees}; expiry breakeven before fees ${data.leg.breakeven_at_expiry_before_fees}.</p> : <p>No contract with a verified archive identity and usable quote met the 14–60 day window.</p>}
      <p>Confirm: {data.confirmation_rule}<br />Invalidate: {data.invalidation_rule}<br />Exit: {data.exit_policy}</p>
      <p><strong>Why no actionable alert:</strong> {data.blockers.map(reason => reason.replaceAll('_', ' ')).join('; ')}.</p>
      <p style={{ color: '#94a3b8' }}>{data.note}</p>
    </>}
  </section>;
}
