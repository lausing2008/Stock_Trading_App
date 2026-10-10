import React, { useState } from 'react';
import type { OptionsFlowStockSummary } from '@/lib/api';

export function StockFlowOverview({ rows, onSelect }: {
  rows: OptionsFlowStockSummary[]; onSelect: (symbol: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? rows : rows.slice(0, 6);
  return <section aria-label="Stock activity overview" style={{ marginBottom: 20 }}>
    <h2>Start with a stock</h2>
    <p>Matching historical activity across all pages, up to 50 symbols ordered by latest alert.
      Filters apply here. Multiple contracts can reflect one event; counts are not independent votes.
      Select a stock to inspect its alerts, then review a specific contract.</p>
    <p>{rows.length ? `${visible.length} of ${rows.length} stock groups shown · latest activity first, not a quality ranking` : 'No stocks match these filters.'}</p>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
      {visible.map(row => <article key={row.symbol} style={{ padding: 14, border: '1px solid #334155', borderRadius: 8 }}>
        <strong>{row.symbol}</strong> · {row.bullish > 0 && row.bearish > 0 ? 'Mixed activity' : row.bullish > 0 ? 'Bullish activity inference' : 'Bearish activity inference'}
        <p>{row.bullish} bullish · {row.bearish} bearish<br />
          {row.alert_count} alerts · {row.contracts} contracts · {row.dates} dates</p>
        <p>Latest alert: {row.latest_at.replace('T', ' ')}{/[zZ]|[+-]\d\d:\d\d$/.test(row.latest_at) ? '' : ' UTC'}</p>
        <p>Trade readiness: not assessed</p>
        <button onClick={() => onSelect(row.symbol)}>Inspect {row.symbol} alerts</button>
        {' · '}<a href={`/quality-value?symbols=${encodeURIComponent(row.symbol)}`}>Stock research</a>
      </article>)}
    </div>
    {rows.length > 6 && <button onClick={() => setExpanded(value => !value)} style={{ marginTop: 12 }}>
      {expanded ? 'Show fewer stocks' : `Show all ${rows.length} stock groups`}
    </button>}
  </section>;
}
