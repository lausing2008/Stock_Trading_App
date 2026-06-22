import type { RankingRow, LatestPrice, SignalSummary } from '@/lib/api';
type SymbolWR = Record<string, { wr: number; n: number }>;
import { confluenceScore, confluenceGrade } from '@/lib/confluence';

type PriceMap = Record<string, LatestPrice>;

function fmtVol(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

export default function RankingsTable({
  rows,
  prices,
  signals,
  selectedSymbols,
  onToggleCompare,
  boardSet,
  symbolWR,
}: {
  rows: RankingRow[];
  prices?: PriceMap;
  signals?: Record<string, SignalSummary>;
  selectedSymbols?: Set<string>;
  onToggleCompare?: (symbol: string) => void;
  boardSet?: Set<string>;
  symbolWR?: SymbolWR;
}) {
  return (
    <div className="overflow-x-auto rounded-md border border-slate-800">
      <table className="w-full text-left text-sm text-slate-200">
        <thead className="bg-slate-800/60 text-slate-300">
          <tr>
            {onToggleCompare && <th className="px-2 py-2 w-8" title="Select to compare"></th>}
            <th className="px-3 py-2">#</th>
            <th className="px-3 py-2">Symbol</th>
            <th className="px-3 py-2">Name</th>
            <th className="px-3 py-2">Market</th>
            <th className="px-3 py-2">Sector</th>
            <th className="px-3 py-2 text-right">Price</th>
            <th className="px-3 py-2 text-right">Change</th>
            <th className="px-3 py-2 text-right">Volume</th>
            <th className="px-3 py-2 text-right">vs Avg</th>
            <th className="px-3 py-2 text-right">K-Score</th>
            <th className="px-3 py-2 text-center">Signal</th>
            <th className="px-3 py-2 text-right" title="Relative Strength vs sector ETF (0-100). >60 = leading sector, <40 = lagging">RS</th>
            <th className="px-3 py-2 text-right">Confluence</th>
            <th className="px-3 py-2 text-right">Fair Price</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const lp = prices?.[r.symbol];
            const changeUp = (lp?.change_pct ?? 0) >= 0;
            const pending = r.score == null && r.technical == null;
            const volRatio = lp?.volume != null && lp?.avg_volume != null && lp.avg_volume > 0
              ? lp.volume / lp.avg_volume : null;
            const volColor = volRatio == null ? '#475569'
              : volRatio >= 2 ? '#4ade80'
              : volRatio >= 1.5 ? '#86efac'
              : volRatio < 0.5 ? '#f87171'
              : '#64748b';
            const sig = signals?.[r.symbol];
            const cs = pending ? null : confluenceScore(r, sig);
            const grade = cs != null ? confluenceGrade(cs) : null;
            const signal = sig?.signal ?? null;
            const divergeBg = !pending && r.score != null && signal != null
              ? r.score >= 65 && signal === 'SELL' ? 'rgba(239,68,68,0.07)'
              : r.score >= 65 && signal === 'HOLD' ? 'rgba(251,191,36,0.06)'
              : r.score < 40 && signal === 'BUY' ? 'rgba(251,191,36,0.06)'
              : undefined : undefined;
            return (
              <tr key={r.symbol} className={`border-t border-slate-800 hover:bg-slate-900${pending ? ' opacity-50' : ''}`} style={divergeBg ? { background: divergeBg } : undefined}>
                {onToggleCompare && (
                  <td className="px-2 py-2">
                    <button
                      onClick={() => onToggleCompare(r.symbol)}
                      title={selectedSymbols?.has(r.symbol) ? 'Remove from comparison' : 'Add to comparison'}
                      style={{
                        width: 22, height: 22, borderRadius: 4, fontSize: 11, cursor: 'pointer',
                        border: `1px solid ${selectedSymbols?.has(r.symbol) ? '#6366f1' : '#334155'}`,
                        background: selectedSymbols?.has(r.symbol) ? 'rgba(99,102,241,0.2)' : 'transparent',
                        color: selectedSymbols?.has(r.symbol) ? '#818cf8' : '#475569',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}
                    >
                      {selectedSymbols?.has(r.symbol) ? '✓' : '+'}
                    </button>
                  </td>
                )}
                <td className="px-3 py-2 text-slate-500">{pending ? '—' : i + 1}</td>
                <td className="px-3 py-2 font-medium">
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <a href={`/stock/${r.symbol}`} className="text-indigo-400 hover:underline">{r.symbol}</a>
                    {symbolWR?.[r.symbol] && (() => {
                      const { wr, n } = symbolWR[r.symbol];
                      const wrPct = Math.round(wr * 100);
                      const col = wrPct >= 55 ? '#22c55e' : wrPct >= 45 ? '#f59e0b' : '#ef4444';
                      return (
                        <span style={{ fontSize: '9px', fontWeight: 700, color: col, background: `${col}18`, border: `1px solid ${col}44`, padding: '1px 4px', borderRadius: '3px', whiteSpace: 'nowrap' }}
                              title={`90d win rate: ${wrPct}% (${n} outcomes)`}>
                          {wrPct}%WR
                        </span>
                      );
                    })()}
                    {boardSet?.has(r.symbol) && (
                      <span style={{ fontSize: '9px', fontWeight: 700, padding: '1px 6px', borderRadius: '4px', color: '#34d399', background: 'rgba(52,211,153,0.12)', border: '1px solid rgba(52,211,153,0.3)', whiteSpace: 'nowrap' }}>
                        ✓ On Board
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-3 py-2">
                  <div>{r.name}</div>
                  {r.name_zh && <div className="text-xs text-slate-500 mt-0.5">{r.name_zh}</div>}
                </td>
                <td className="px-3 py-2 text-slate-400">{r.market}</td>
                <td className="px-3 py-2 text-xs text-slate-500" style={{ maxWidth: 120, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={r.sector ?? undefined}>
                  {r.sector ?? '—'}
                </td>
                <td className="px-3 py-2 text-right font-semibold">
                  {lp ? `$${lp.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
                </td>
                <td className="px-3 py-2 text-right text-xs font-semibold" style={{ color: lp?.change_pct != null ? (changeUp ? '#4ade80' : '#f87171') : '#475569' }}>
                  {lp?.change_pct != null ? `${changeUp ? '▲' : '▼'} ${Math.abs(lp.change_pct).toFixed(2)}%` : '—'}
                </td>
                <td className="px-3 py-2 text-right text-xs" style={{ color: '#94a3b8' }}>
                  {fmtVol(lp?.volume)}
                </td>
                <td className="px-3 py-2 text-right text-xs font-semibold" style={{ color: volColor }}>
                  {volRatio != null ? `${volRatio.toFixed(1)}×` : '—'}
                </td>
                <td className="px-3 py-2 text-right font-semibold">
                  {pending ? <span className="text-xs text-slate-600">Pending data</span> : r.score != null ? r.score.toFixed(1) : '—'}
                </td>
                <td className="px-3 py-2 text-center">
                  {signal ? (
                    <span style={{
                      fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 4,
                      color: signal === 'BUY' ? '#4ade80' : signal === 'SELL' ? '#f87171' : '#64748b',
                      background: signal === 'BUY' ? 'rgba(74,222,128,0.12)' : signal === 'SELL' ? 'rgba(248,113,113,0.12)' : 'rgba(100,116,139,0.12)',
                    }}>{signal}</span>
                  ) : <span className="text-slate-700">—</span>}
                </td>
                <td className="px-3 py-2 text-right text-xs font-semibold" style={{
                  color: r.relative_strength == null ? '#475569'
                    : r.relative_strength >= 60 ? '#4ade80'
                    : r.relative_strength >= 45 ? '#94a3b8'
                    : '#f87171',
                }}>
                  {r.relative_strength != null ? r.relative_strength.toFixed(0) : '—'}
                </td>
                <td className="px-3 py-2 text-right">
                  {grade && cs != null ? (
                    <span title={`${grade.description} · max position ${grade.maxPositionPct}`}>
                      <span style={{ fontWeight: 700, color: grade.color, fontSize: '13px' }}>{cs}</span>
                      <span style={{ fontSize: '10px', color: grade.color, opacity: 0.75, marginLeft: '4px' }}>{grade.label}</span>
                    </span>
                  ) : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-3 py-2 text-right text-indigo-400">{r.fair_price?.toFixed(2) ?? '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
