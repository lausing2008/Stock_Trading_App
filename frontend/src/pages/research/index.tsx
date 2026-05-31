import { useRouter } from 'next/router';
import { useState } from 'react';

export default function ResearchIndex() {
  const router = useRouter();
  const [symbol, setSymbol] = useState('');

  function go(e: React.FormEvent) {
    e.preventDefault();
    if (symbol.trim()) router.push(`/research/${symbol.trim().toUpperCase()}`);
  }

  return (
    <div style={{ maxWidth: '560px', margin: '80px auto', textAlign: 'center' }}>
      <div style={{ fontSize: '36px', marginBottom: '12px' }}>Research Engine</div>
      <p style={{ color: '#64748b', marginBottom: '32px', lineHeight: 1.6 }}>
        Enter a stock symbol to generate a comprehensive AI-powered research report — technical, fundamental, company, industry, and economic analysis in one dashboard.
      </p>
      <form onSubmit={go} style={{ display: 'flex', gap: '10px', justifyContent: 'center' }}>
        <input
          autoFocus
          value={symbol}
          onChange={e => setSymbol(e.target.value.toUpperCase())}
          placeholder="e.g. AAPL"
          style={{ padding: '12px 18px', borderRadius: '10px', border: '1px solid #1e293b', background: 'rgba(255,255,255,0.04)', color: '#f1f5f9', fontSize: '16px', outline: 'none', width: '200px', fontFamily: 'ui-monospace, monospace', letterSpacing: '0.06em' }}
        />
        <button
          type="submit"
          disabled={!symbol.trim()}
          style={{ padding: '12px 28px', borderRadius: '10px', border: 'none', background: symbol ? 'linear-gradient(135deg,#4ade80,#22c55e)' : '#1e293b', color: symbol ? '#000' : '#475569', fontSize: '15px', fontWeight: 700, cursor: symbol ? 'pointer' : 'default' }}
        >
          Analyze
        </button>
      </form>
    </div>
  );
}
