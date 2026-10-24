import React from 'react';

type Comparison = {
  n_active: number;
  n_inactive: number;
  edge_pct: number | null;
  verdict: string;
  comparison_status?: 'measured' | 'insufficient_evidence';
  comparison_reason?: string | null;
};

export default function FilterAuditComparison({ row }: { row: Comparison }) {
  // Also guard older API responses that manufactured a numeric edge for an empty cohort.
  const measured = row.n_active > 0 && row.n_inactive > 0
    && row.comparison_status !== 'insufficient_evidence'
    && row.edge_pct != null && Number.isFinite(row.edge_pct);
  const edge = measured ? row.edge_pct! : null;
  const verdict = measured ? row.verdict : 'Insufficient evidence';
  const color = !measured ? '#94a3b8' : verdict === 'predictive' ? '#4ade80' : verdict === 'harmful' ? '#f87171' : '#facc15';
  const edgeColor = edge == null ? '#94a3b8' : edge < -3 ? '#4ade80' : edge > 3 ? '#f87171' : '#94a3b8';
  return <>
    <td style={{ padding: '6px 10px', color: edgeColor, fontWeight: 700 }}>
      {edge == null ? '—' : `${edge > 0 ? '+' : ''}${edge}%`}
    </td>
    <td style={{ padding: '6px 10px' }}>
      <span title={measured ? 'Descriptive cohort comparison; not validated prediction.' : row.comparison_reason ?? 'One or both cohorts have no return observations.'}
        style={{ padding: '2px 8px', borderRadius: 4, background: `${color}22`, color, fontSize: 10, fontWeight: 700, textTransform: 'uppercase' }}>
        {verdict}
      </span>
    </td>
  </>;
}
