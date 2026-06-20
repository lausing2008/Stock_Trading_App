import { useState, useMemo, useRef, useEffect } from 'react';
import { api, type Price } from '@/lib/api';

const COLORS = ['#818cf8', '#34d399', '#fb923c', '#f87171', '#facc15'];
const RANGES = [
  { label: '1M', bars: 22 },
  { label: '3M', bars: 63 },
  { label: '6M', bars: 126 },
  { label: '1Y', bars: 252 },
];

function fmtRet(pct: number): string {
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
}

function retColor(pct: number): string {
  return pct >= 0 ? '#4ade80' : '#f87171';
}

type SeriesData = { symbol: string; dates: string[]; normalized: number[] };

function CompareChart({ series, width = 760, height = 320 }: {
  series: SeriesData[];
  width?: number;
  height?: number;
}) {
  if (!series.length) return null;

  // Align all series to the same date set (inner join)
  const allDates = series.reduce<Set<string>>(
    (acc, s) => {
      const set = new Set(s.dates);
      if (acc.size === 0) return set;
      for (const d of acc) if (!set.has(d)) acc.delete(d);
      return acc;
    },
    new Set<string>()
  );
  const commonDates = Array.from(allDates).sort();
  if (commonDates.length < 2) return null;

  const pad = { top: 16, right: 16, bottom: 28, left: 42 };
  const W = width - pad.left - pad.right;
  const H = height - pad.top - pad.bottom;

  // Compute aligned values per series
  const aligned = series.map(s => {
    const idx = new Map(s.dates.map((d, i) => [d, i]));
    const vals = commonDates.map(d => {
      const i = idx.get(d);
      return i != null ? s.normalized[i] : null;
    }).filter((v): v is number => v != null);
    return { symbol: s.symbol, vals };
  });

  const allVals = aligned.flatMap(a => a.vals);
  const minV = Math.min(...allVals);
  const maxV = Math.max(...allVals);
  const range = maxV - minV || 1;

  const xScale = (i: number) => (i / (commonDates.length - 1)) * W;
  const yScale = (v: number) => H - ((v - minV) / range) * H;

  // Y-axis grid lines
  const nTicks = 5;
  const ticks = Array.from({ length: nTicks }, (_, i) => minV + (range / (nTicks - 1)) * i);

  // X-axis date labels (max 6)
  const xStep = Math.max(1, Math.floor(commonDates.length / 6));
  const xLabels = commonDates
    .map((d, i) => ({ d, i }))
    .filter(({ i }) => i % xStep === 0 || i === commonDates.length - 1);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      <g transform={`translate(${pad.left},${pad.top})`}>
        {/* Grid */}
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={0} y1={yScale(t)} x2={W} y2={yScale(t)} stroke="#1e293b" strokeWidth={1} />
            <text x={-6} y={yScale(t)} textAnchor="end" dominantBaseline="middle"
              fontSize={9} fill="#475569">
              {t.toFixed(1)}
            </text>
          </g>
        ))}
        {/* Baseline at 100 */}
        {minV <= 100 && maxV >= 100 && (
          <line x1={0} y1={yScale(100)} x2={W} y2={yScale(100)}
            stroke="#334155" strokeWidth={1} strokeDasharray="3,3" />
        )}
        {/* X-axis labels */}
        {xLabels.map(({ d, i }) => (
          <text key={i} x={xScale(i)} y={H + 14} textAnchor="middle" fontSize={9} fill="#475569">
            {d.slice(5)} {/* MM-DD */}
          </text>
        ))}
        {/* Series lines */}
        {aligned.map((a, si) => {
          const points = a.vals
            .map((v, i) => `${xScale(i).toFixed(1)},${yScale(v).toFixed(1)}`)
            .join(' ');
          return (
            <polyline key={a.symbol} points={points}
              fill="none" stroke={COLORS[si % COLORS.length]} strokeWidth={2}
              strokeLinejoin="round" strokeLinecap="round" />
          );
        })}
        {/* End dots */}
        {aligned.map((a, si) => {
          const lastVal = a.vals[a.vals.length - 1];
          return (
            <circle key={a.symbol}
              cx={xScale(a.vals.length - 1)} cy={yScale(lastVal)}
              r={4} fill={COLORS[si % COLORS.length]} />
          );
        })}
      </g>
    </svg>
  );
}

export default function ComparePage() {
  const [inputs, setInputs] = useState<string[]>(['', '']);
  const [rangeIdx, setRangeIdx] = useState(3); // 1Y default
  const [series, setSeries] = useState<SeriesData[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const [chartW, setChartW] = useState(760);

  useEffect(() => {
    if (!containerRef.current) return;
    const obs = new ResizeObserver(([e]) => setChartW(e.contentRect.width));
    obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  async function handleCompare() {
    const symbols = inputs.map(s => s.trim().toUpperCase()).filter(Boolean);
    if (symbols.length < 2) { setError('Add at least 2 symbols'); return; }
    setLoading(true); setError(''); setSeries([]);
    try {
      const limit = RANGES[rangeIdx].bars + 5;
      const results = await Promise.all(
        symbols.map(async sym => {
          const prices: Price[] = await api.getPrices(sym, 'D1', limit);
          return { symbol: sym, prices };
        })
      );
      // Build normalized series: base = first close in window
      const built: SeriesData[] = results
        .filter(r => r.prices.length >= 2)
        .map(r => {
          const sorted = [...r.prices].sort((a, b) => a.ts.localeCompare(b.ts)).slice(-RANGES[rangeIdx].bars);
          const base = sorted[0].close;
          return {
            symbol: r.symbol,
            dates: sorted.map(p => p.ts.slice(0, 10)),
            normalized: sorted.map(p => (p.close / base) * 100),
          };
        });
      if (!built.length) { setError('No price data found'); return; }
      setSeries(built);
    } catch {
      setError('Failed to fetch price data');
    } finally {
      setLoading(false);
    }
  }

  function addSymbol() {
    if (inputs.length < 5) setInputs(prev => [...prev, '']);
  }
  function removeSymbol(i: number) {
    setInputs(prev => prev.filter((_, j) => j !== i));
  }
  function updateSymbol(i: number, val: string) {
    setInputs(prev => prev.map((s, j) => j === i ? val.toUpperCase() : s));
  }

  const returns = useMemo(() =>
    series.map(s => ({
      symbol: s.symbol,
      ret: s.normalized.length ? s.normalized[s.normalized.length - 1] - 100 : 0,
    })).sort((a, b) => b.ret - a.ret),
    [series]
  );

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>
          Compare Stocks
        </h1>
        <p style={{ fontSize: '12px', color: '#475569' }}>
          Normalized performance — all series start at 100, showing relative return over the period
        </p>
      </div>

      {/* Input panel */}
      <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', padding: '20px', marginBottom: '24px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', marginBottom: '14px', alignItems: 'center' }}>
          {inputs.map((val, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: COLORS[i % COLORS.length], flexShrink: 0 }} />
              <input
                value={val}
                onChange={e => updateSymbol(i, e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleCompare()}
                placeholder={`Symbol ${i + 1}`}
                style={{
                  width: '90px', padding: '6px 10px', borderRadius: '6px',
                  border: `1px solid ${COLORS[i % COLORS.length]}44`,
                  background: '#080f1e', color: '#e2e8f0', fontSize: '13px',
                  fontWeight: 700, outline: 'none', textTransform: 'uppercase',
                }}
              />
              {inputs.length > 2 && (
                <button onClick={() => removeSymbol(i)}
                  style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '16px', lineHeight: 1, padding: '0 2px' }}>
                  ×
                </button>
              )}
            </div>
          ))}
          {inputs.length < 5 && (
            <button onClick={addSymbol}
              style={{ padding: '6px 12px', borderRadius: '6px', fontSize: '12px', cursor: 'pointer', border: '1px dashed #334155', background: 'transparent', color: '#64748b' }}>
              + Add
            </button>
          )}
        </div>

        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
          {/* Range selector */}
          <div style={{ display: 'flex', gap: '4px' }}>
            {RANGES.map((r, i) => (
              <button key={r.label} onClick={() => setRangeIdx(i)}
                style={{
                  padding: '5px 12px', borderRadius: '5px', fontSize: '12px', cursor: 'pointer',
                  border: '1px solid #1e293b', fontWeight: rangeIdx === i ? 700 : 400,
                  background: rangeIdx === i ? '#4f46e5' : 'transparent',
                  color: rangeIdx === i ? '#fff' : '#64748b',
                }}>
                {r.label}
              </button>
            ))}
          </div>

          <button onClick={handleCompare} disabled={loading}
            style={{
              padding: '6px 18px', borderRadius: '6px', fontSize: '13px', fontWeight: 700,
              cursor: loading ? 'wait' : 'pointer', background: '#4f46e5', color: '#fff',
              border: 'none', opacity: loading ? 0.7 : 1,
            }}>
            {loading ? 'Loading…' : 'Compare'}
          </button>
          {error && <span style={{ fontSize: '12px', color: '#f87171' }}>{error}</span>}
        </div>
      </div>

      {/* Chart */}
      {series.length > 0 && (
        <>
          {/* Legend + returns */}
          <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', marginBottom: '12px' }}>
            {returns.map(({ symbol, ret }, i) => {
              const si = series.findIndex(s => s.symbol === symbol);
              const color = COLORS[si % COLORS.length];
              return (
                <div key={symbol} style={{ display: 'flex', alignItems: 'center', gap: '6px', background: '#0f172a', border: `1px solid ${color}44`, borderRadius: '8px', padding: '6px 12px' }}>
                  <div style={{ width: '12px', height: '3px', borderRadius: '2px', background: color }} />
                  <span style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0' }}>{symbol}</span>
                  <span style={{ fontSize: '13px', fontWeight: 700, color: retColor(ret) }}>{fmtRet(ret)}</span>
                  {i === 0 && <span style={{ fontSize: '9px', color: '#475569', background: '#1e293b', padding: '1px 5px', borderRadius: '3px' }}>best</span>}
                </div>
              );
            })}
          </div>

          <div ref={containerRef} style={{ background: '#080f1e', border: '1px solid #1e293b', borderRadius: '12px', padding: '16px' }}>
            <CompareChart series={series} width={chartW - 32} height={340} />
          </div>

          {/* Summary table */}
          <div style={{ marginTop: '16px', background: '#0f172a', border: '1px solid #1e293b', borderRadius: '10px', overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr style={{ background: '#080f1e' }}>
                  {['Symbol', 'Return', 'Start Price', 'Current', 'Bars'].map(h => (
                    <th key={h} style={{ padding: '8px 14px', textAlign: h === 'Symbol' ? 'left' : 'right', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {returns.map(({ symbol, ret }) => {
                  const s = series.find(x => x.symbol === symbol)!;
                  const si = series.findIndex(x => x.symbol === symbol);
                  const color = COLORS[si % COLORS.length];
                  return (
                    <tr key={symbol} style={{ borderBottom: '1px solid rgba(30,41,59,0.5)' }}>
                      <td style={{ padding: '8px 14px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: color }} />
                          <a href={`/stock/${symbol}`} style={{ color, fontWeight: 700, textDecoration: 'none' }}>{symbol}</a>
                        </div>
                      </td>
                      <td style={{ padding: '8px 14px', textAlign: 'right', fontWeight: 700, color: retColor(ret) }}>{fmtRet(ret)}</td>
                      <td style={{ padding: '8px 14px', textAlign: 'right', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                        {s.normalized.length ? (s.normalized[0] === 100 ? '100.00' : '—') : '—'}
                      </td>
                      <td style={{ padding: '8px 14px', textAlign: 'right', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                        {s.normalized.length ? s.normalized[s.normalized.length - 1].toFixed(2) : '—'}
                      </td>
                      <td style={{ padding: '8px 14px', textAlign: 'right', color: '#475569' }}>{s.normalized.length}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!series.length && !loading && (
        <div style={{ textAlign: 'center', padding: '60px 0', color: '#334155', fontSize: '14px' }}>
          Enter 2–5 symbols and click Compare to see normalized performance
        </div>
      )}
    </div>
  );
}
