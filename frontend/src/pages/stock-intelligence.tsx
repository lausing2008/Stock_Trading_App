/* A direct link to one issuer's research conclusion.
 *
 * THIS IS NOT THE PRODUCT SURFACE. The agreed home for this is inside Quality & Value, keyed to
 * the symbol being evaluated there; this route exists only so a single symbol can be opened
 * directly. It renders the SAME component and calls the SAME API — there is no second
 * implementation, and nothing here may diverge from what Quality & Value shows.
 */
import { useState } from 'react';
import Head from 'next/head';
import { useRouter } from 'next/router';
import StockIntelligencePanel from '@/components/StockIntelligencePanel';

export default function StockIntelligencePage() {
  const router = useRouter();
  const qs = typeof router.query.symbol === 'string' ? router.query.symbol : '';
  const [symbol, setSymbol] = useState('MU');
  const active = (qs || symbol).toUpperCase();
  return (
    <>
      <Head><title>Stock Intelligence</title></Head>
      <div style={{ maxWidth: 1180, margin: '0 auto', padding: '24px 16px 72px' }}>
        <h1 style={{ margin: '0 0 4px', fontSize: 24, color: '#f1f5f9' }}>
          Stock Intelligence — {active}
        </h1>
        <p style={{ margin: '0 0 18px', color: '#94a3b8', fontSize: 13, maxWidth: 820 }}>
          The same panel shown inside Quality &amp; Value, for one symbol. A direction here is a
          reading of stored evidence — not a forecast, and nothing on this page establishes
          predictive skill.
        </p>
        <form onSubmit={e => { e.preventDefault();
                               router.push(`/stock-intelligence?symbol=${symbol.toUpperCase()}`); }}
              style={{ display: 'flex', gap: 8, marginBottom: 18 }}>
          <input value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="Symbol"
                 style={{ background: 'rgba(15,23,42,0.8)', border: '1px solid rgba(148,163,184,0.3)',
                          borderRadius: 8, padding: '8px 12px', color: '#e2e8f0', width: 160 }} />
          <button type="submit"
                  style={{ background: 'rgba(56,189,248,0.15)', border: '1px solid rgba(56,189,248,0.4)',
                           borderRadius: 8, padding: '8px 16px', color: '#7dd3fc', cursor: 'pointer' }}>
            Load
          </button>
        </form>
        <StockIntelligencePanel symbol={active} />
      </div>
    </>
  );
}
