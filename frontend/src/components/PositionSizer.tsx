import { useState, useEffect } from 'react';
import { loadSettings } from '@/lib/settings';

type Props = {
  entryPrice?: number;
  stopLoss?: number;
  atrStop?: number | null;
  atr?: number | null;
  takeProfit?: number;
  symbol?: string;
};

export default function PositionSizer({ entryPrice, stopLoss, atrStop, atr, takeProfit, symbol }: Props) {
  const settings = typeof window !== 'undefined' ? loadSettings() : null;
  const [accountSize, setAccountSize] = useState<number>(settings?.accountSize || 10000);
  const [riskPct, setRiskPct] = useState<number>(settings?.riskPctPerTrade || 1);
  // Prefer ATR-based stop, fall back to support level
  const defaultStop = atrStop ?? stopLoss ?? 0;
  const [entry, setEntry] = useState<number>(entryPrice ?? 0);
  const [stop, setStop] = useState<number>(defaultStop);
  const [target, setTarget] = useState<number>(takeProfit ?? 0);

  // Sync when props change (e.g. when signal loads)
  useEffect(() => { if (entryPrice) setEntry(entryPrice); }, [entryPrice]);
  useEffect(() => {
    const s = atrStop ?? stopLoss;
    if (s) setStop(s);
  }, [atrStop, stopLoss]);
  useEffect(() => { if (takeProfit) setTarget(takeProfit); }, [takeProfit]);

  const riskPerShare = entry > 0 && stop > 0 ? Math.abs(entry - stop) : null;
  const dollarRisk   = accountSize * (riskPct / 100);
  const shares       = riskPerShare && riskPerShare > 0 ? Math.floor(dollarRisk / riskPerShare) : null;
  const positionSize = shares != null && entry > 0 ? shares * entry : null;
  const pctOfAccount = positionSize != null ? (positionSize / accountSize) * 100 : null;
  const rewardPerShare = entry > 0 && target > 0 ? Math.abs(target - entry) : null;
  const rr = riskPerShare && rewardPerShare && riskPerShare > 0 ? rewardPerShare / riskPerShare : null;
  const potentialProfit = shares != null && rewardPerShare != null ? shares * rewardPerShare : null;
  const potentialLoss   = shares != null && riskPerShare != null  ? shares * riskPerShare  : null;

  const row = (label: string, value: string, color?: string) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: '1px solid #0f172a' }}>
      <span style={{ fontSize: 12, color: '#64748b' }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 600, color: color ?? '#e2e8f0' }}>{value}</span>
    </div>
  );

  return (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <h3 style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8', margin: 0 }}>
          Position Sizer {symbol ? `— ${symbol}` : ''}
        </h3>
        {atr != null && (
          <span style={{ fontSize: 10, color: '#475569', background: '#0d1424', border: '1px solid #1e293b', borderRadius: 4, padding: '2px 7px' }}>
            ATR(14) = {atr.toFixed(2)} · stop = 2×ATR
          </span>
        )}
      </div>

      {/* Inputs */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 14 }}>
        <div>
          <label style={{ fontSize: 10, color: '#64748b', display: 'block', marginBottom: 3 }}>Account Size ($)</label>
          <input type="number" value={accountSize}
            onChange={e => setAccountSize(Number(e.target.value))}
            style={{ width: '100%', padding: '5px 8px', borderRadius: 5, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 12 }} />
        </div>
        <div>
          <label style={{ fontSize: 10, color: '#64748b', display: 'block', marginBottom: 3 }}>Risk per Trade (%)</label>
          <div style={{ display: 'flex', gap: 4 }}>
            <input type="number" step="0.5" min="0.5" max="5" value={riskPct}
              onChange={e => setRiskPct(Number(e.target.value))}
              style={{ flex: 1, padding: '5px 8px', borderRadius: 5, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 12 }} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {[1, 2].map(v => (
                <button key={v} onClick={() => setRiskPct(v)}
                  style={{ padding: '1px 6px', borderRadius: 3, fontSize: 10, cursor: 'pointer', border: '1px solid',
                    borderColor: riskPct === v ? '#6366f1' : '#1e293b',
                    background: riskPct === v ? 'rgba(99,102,241,0.15)' : 'transparent',
                    color: riskPct === v ? '#818cf8' : '#475569' }}>
                  {v}%
                </button>
              ))}
            </div>
          </div>
        </div>
        <div>
          <label style={{ fontSize: 10, color: '#64748b', display: 'block', marginBottom: 3 }}>Entry Price</label>
          <input type="number" step="0.01" value={entry || ''}
            onChange={e => setEntry(Number(e.target.value))}
            style={{ width: '100%', padding: '5px 8px', borderRadius: 5, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 12 }} />
        </div>
        <div>
          <label style={{ fontSize: 10, color: '#64748b', display: 'block', marginBottom: 3 }}>Stop Loss</label>
          <input type="number" step="0.01" value={stop || ''}
            onChange={e => setStop(Number(e.target.value))}
            style={{ width: '100%', padding: '5px 8px', borderRadius: 5, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 12 }} />
        </div>
        <div>
          <label style={{ fontSize: 10, color: '#64748b', display: 'block', marginBottom: 3 }}>Take Profit <span style={{ color: '#334155' }}>(optional)</span></label>
          <input type="number" step="0.01" value={target || ''}
            onChange={e => setTarget(Number(e.target.value))}
            style={{ width: '100%', padding: '5px 8px', borderRadius: 5, border: '1px solid #1e293b', background: '#020617', color: '#e2e8f0', fontSize: 12 }} />
        </div>
      </div>

      {/* Results */}
      {shares != null && shares > 0 ? (
        <div>
          <div style={{ padding: '10px 12px', borderRadius: 6, background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.2)', marginBottom: 10, textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 800, color: '#818cf8' }}>{shares.toLocaleString()}</div>
            <div style={{ fontSize: 11, color: '#64748b' }}>shares to buy</div>
            {positionSize != null && (
              <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 2 }}>
                ${positionSize.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} position
                {pctOfAccount != null && <span style={{ color: pctOfAccount > 20 ? '#f87171' : '#64748b' }}> ({pctOfAccount.toFixed(1)}% of account)</span>}
              </div>
            )}
          </div>
          {row('Dollar Risk', `$${dollarRisk.toFixed(2)}`, '#f87171')}
          {potentialLoss != null && row('Max Loss (actual)', `-$${potentialLoss.toFixed(2)}`, '#f87171')}
          {rr != null && row('Risk : Reward', `1 : ${rr.toFixed(1)}`, rr >= 2 ? '#4ade80' : rr >= 1.5 ? '#facc15' : '#f87171')}
          {potentialProfit != null && row('Potential Profit', `+$${potentialProfit.toFixed(2)}`, '#4ade80')}
          {riskPerShare != null && row('Risk per Share', `$${riskPerShare.toFixed(2)}`)}
          {pctOfAccount != null && pctOfAccount > 20 && (
            <div style={{ marginTop: 8, padding: '6px 10px', borderRadius: 5, background: 'rgba(251,113,133,0.08)', border: '1px solid rgba(251,113,133,0.2)', fontSize: 11, color: '#f87171' }}>
              ⚠ Position is {pctOfAccount.toFixed(0)}% of account — consider reducing risk % or shares
            </div>
          )}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: '#475569', textAlign: 'center', padding: '10px 0' }}>
          Enter entry price and stop loss to calculate position size
        </div>
      )}
    </div>
  );
}
