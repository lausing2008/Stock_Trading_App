import { useState, useCallback } from 'react';
import Head from 'next/head';
import { api, DecisionResult, ScoreItem, PositionPlan, DecisionFactors, DecisionMultipliers } from '../lib/api';

const VERDICT_COLOR: Record<string, string> = {
  BUY:     '#22c55e',
  SCALE:   '#86efac',
  HOLD:    '#f59e0b',
  SKIP:    '#64748b',
  BLOCKED: '#ef4444',
};

const REGIME_COLOR: Record<string, string> = {
  bull:     '#22c55e',
  neutral:  '#94a3b8',
  choppy:   '#f59e0b',
  risk_off: '#f97316',
  bear:     '#ef4444',
};

function ScoreBar({ score, minScore }: { score: number; minScore: number }) {
  const max = 12;
  if (score < 0) {
    return (
      <div style={{ margin: '16px 0', padding: '8px 12px', background: '#450a0a', borderRadius: 6, fontSize: 13, color: '#fca5a5' }}>
        Hard rejected before scoring — no score computed
      </div>
    );
  }
  const pct = score / max * 100;
  const minPct = (minScore / max) * 100;
  const color = score >= minScore ? '#22c55e' : '#ef4444';
  return (
    <div style={{ margin: '16px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, fontSize: 13, color: '#94a3b8' }}>
        <span>Score: <strong style={{ color, fontSize: 18 }}>{score}</strong> / {max}</span>
        <span>Min required: <strong style={{ color: '#f59e0b' }}>{minScore}</strong></span>
      </div>
      <div style={{ position: 'relative', height: 10, background: '#1e293b', borderRadius: 6 }}>
        <div style={{ position: 'absolute', left: 0, top: 0, height: '100%', width: `${pct}%`, background: color, borderRadius: 6, transition: 'width 0.3s' }} />
        <div style={{ position: 'absolute', top: -3, left: `${minPct}%`, width: 2, height: 16, background: '#f59e0b', borderRadius: 1 }} />
      </div>
    </div>
  );
}

function BreakdownTable({ items }: { items: ScoreItem[] }) {
  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>Score Breakdown</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {items.map((item, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 10px', background: '#0f172a', borderRadius: 6 }}>
            <span style={{
              fontWeight: 700, fontSize: 13, minWidth: 36, textAlign: 'right',
              color: item.pts > 0 ? '#22c55e' : item.pts < 0 ? '#ef4444' : '#64748b',
            }}>
              {item.pts > 0 ? '+' : ''}{item.pts}
            </span>
            <span style={{ fontSize: 12, color: '#64748b', minWidth: 90 }}>{item.layer}</span>
            <span style={{ fontSize: 12, color: '#94a3b8', flex: 1 }}>{item.note}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function FactorsGrid({ factors }: { factors: DecisionFactors }) {
  const rows: [string, string | number | null][] = [
    ['Signal', factors.signal_direction ?? '—'],
    ['Confidence', factors.signal_confidence != null ? `${factors.signal_confidence.toFixed(1)}%` : '—'],
    ['ML Bull Prob', factors.ml_bull_prob != null ? `${(factors.ml_bull_prob * 100).toFixed(1)}%` : '—'],
    ['Research', factors.research_recommendation ?? '—'],
    ['Research Score', factors.research_score != null ? String(factors.research_score) : '—'],
    ['Regime', factors.regime ?? '—'],
    ['Volume Z', factors.volume_z != null ? factors.volume_z.toFixed(2) : '—'],
    ['Days to Earnings', factors.days_to_earnings != null ? String(factors.days_to_earnings) : '—'],
    ['Signal Age (h)', factors.signal_age_h != null ? factors.signal_age_h.toFixed(1) : '—'],
    ['Conf Delta', factors.conf_delta != null ? factors.conf_delta.toFixed(1) : '—'],
    ['Cross-Style Buys', factors.cross_style_buys != null ? String(factors.cross_style_buys) : '—'],
  ];
  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>Factors</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
        {rows.map(([label, val]) => (
          <div key={label} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 10px', background: '#0f172a', borderRadius: 5 }}>
            <span style={{ fontSize: 12, color: '#64748b' }}>{label}</span>
            <span style={{ fontSize: 12, color: '#cbd5e1', fontWeight: 600 }}>{val}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MultipliersRow({ m }: { m: DecisionMultipliers }) {
  const entries: [string, number][] = [
    ['Regime', m.regime], ['Research', m.research], ['Confidence', m.confidence],
    ['Consensus', m.consensus], ['Earnings', m.earnings],
  ];
  const total = entries.reduce((acc, [, v]) => acc * v, 1);
  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>
        Kelly Multipliers <span style={{ color: '#94a3b8', fontWeight: 400 }}>→ net {total.toFixed(3)}×</span>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {entries.map(([label, val]) => (
          <div key={label} style={{ padding: '4px 10px', background: '#0f172a', borderRadius: 5, fontSize: 12 }}>
            <span style={{ color: '#64748b' }}>{label}: </span>
            <span style={{ color: val < 1 ? '#f97316' : val > 1 ? '#22c55e' : '#94a3b8', fontWeight: 700 }}>{val.toFixed(2)}×</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PositionCard({ pos }: { pos: PositionPlan }) {
  return (
    <div style={{ marginTop: 16, padding: 14, background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b' }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 2 }}>Position Plan</div>
      <div style={{ fontSize: 11, color: '#f97316', marginBottom: 10 }}>
        Illustrative preview only — the paper trading engine sizes real entries independently and may size this trade differently or not take it at all.
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
        {[
          ['Shares', pos.shares],
          ['Size %', `${(pos.size_pct * 100).toFixed(1)}%`],
          ['Dollar Risk', `$${pos.dollar_risk.toFixed(0)}`],
          ['R:R', `${pos.rr_ratio.toFixed(1)}:1`],
          ['Entry', `$${pos.entry_price.toFixed(2)}`],
          ['Stop', `$${pos.stop_price.toFixed(2)}`],
          ['Target 1', `$${pos.target_1.toFixed(2)}`],
          ['Target 2', `$${pos.target_2.toFixed(2)}`],
        ].map(([label, val]) => (
          <div key={label} style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 2 }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>{val}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

const STYLES = ['SWING', 'GROWTH', 'SCALP', 'INCOME'];

export default function DecidePage() {
  const [symbol, setSymbol] = useState('');
  const [style, setStyle] = useState('SWING');
  const [result, setResult] = useState<DecisionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await api.decide(sym, style);
      // /explain wraps the result: { symbol, style, explanation, result }
      const r: DecisionResult = (data as any).result ?? data;
      setResult(r);
    } catch (e: any) {
      setError(e?.message ?? 'Request failed');
    } finally {
      setLoading(false);
    }
  }, [symbol, style]);

  const handleKey = (e: React.KeyboardEvent) => { if (e.key === 'Enter') run(); };

  return (
    <>
      <Head><title>Decision Engine — StockAI</title></Head>
      <div style={{ minHeight: '100vh', background: '#020617', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 800, margin: '0 auto' }}>

          <h1 style={{ fontSize: 22, fontWeight: 700, color: '#f1f5f9', marginBottom: 4 }}>Decision Engine</h1>
          <p style={{ fontSize: 13, color: '#475569', marginBottom: 24 }}>
            5-layer scoring — price zone, R:R quality, signal quality, research alignment, market regime
          </p>

          {/* Input row */}
          <div style={{ display: 'flex', gap: 10, marginBottom: 24, flexWrap: 'wrap' }}>
            <input
              value={symbol}
              onChange={e => setSymbol(e.target.value.toUpperCase())}
              onKeyDown={handleKey}
              placeholder="Symbol (e.g. AAPL)"
              style={{
                flex: 1, minWidth: 160, padding: '10px 14px', background: '#0f172a',
                border: '1px solid #334155', borderRadius: 8, color: '#f1f5f9',
                fontSize: 15, outline: 'none',
              }}
            />
            <select
              value={style}
              onChange={e => setStyle(e.target.value)}
              style={{
                padding: '10px 14px', background: '#0f172a', border: '1px solid #334155',
                borderRadius: 8, color: '#94a3b8', fontSize: 14,
              }}
            >
              {STYLES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <button
              onClick={run}
              disabled={loading || !symbol.trim()}
              style={{
                padding: '10px 24px', background: loading ? '#1e293b' : '#6366f1',
                border: 'none', borderRadius: 8, color: '#f1f5f9', fontWeight: 700,
                fontSize: 14, cursor: loading ? 'default' : 'pointer',
              }}
            >
              {loading ? 'Deciding…' : 'Decide'}
            </button>
          </div>

          {error && (
            <div style={{ padding: 14, background: '#450a0a', border: '1px solid #ef4444', borderRadius: 8, color: '#fca5a5', marginBottom: 16, fontSize: 13 }}>
              {error}
            </div>
          )}

          {result && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* Verdict header */}
              <div style={{ padding: '20px 24px', background: '#0f172a', borderRadius: 10, border: `2px solid ${VERDICT_COLOR[result.verdict] ?? '#334155'}` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 12 }}>
                  <div style={{
                    padding: '6px 20px', borderRadius: 20, fontWeight: 800, fontSize: 20,
                    background: `${VERDICT_COLOR[result.verdict]}22`,
                    color: VERDICT_COLOR[result.verdict] ?? '#94a3b8',
                    border: `1px solid ${VERDICT_COLOR[result.verdict] ?? '#334155'}`,
                  }}>
                    {result.verdict}
                  </div>
                  <div style={{ color: '#475569', fontSize: 13 }}>
                    {result.symbol} · {result.style} · {result.latency_ms}ms
                  </div>
                  {result.factors.regime && (
                    <div style={{
                      padding: '4px 12px', borderRadius: 12, fontSize: 12, fontWeight: 600,
                      background: `${REGIME_COLOR[result.factors.regime] ?? '#334155'}22`,
                      color: REGIME_COLOR[result.factors.regime] ?? '#94a3b8',
                      border: `1px solid ${REGIME_COLOR[result.factors.regime] ?? '#334155'}`,
                    }}>
                      {result.factors.regime}
                    </div>
                  )}
                </div>

                {result.blocked_reason && (
                  <div style={{ padding: '8px 12px', background: '#450a0a', borderRadius: 6, color: '#fca5a5', fontSize: 13, marginBottom: 8 }}>
                    Blocked: {result.blocked_reason}
                  </div>
                )}

                <ScoreBar score={result.score} minScore={result.min_score} />
              </div>

              {/* Two-column layout: breakdown + factors */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <div style={{ padding: 16, background: '#0f172a', borderRadius: 10, border: '1px solid #1e293b' }}>
                  <BreakdownTable items={result.score_breakdown} />
                </div>
                <div style={{ padding: 16, background: '#0f172a', borderRadius: 10, border: '1px solid #1e293b' }}>
                  <FactorsGrid factors={result.factors} />
                  <MultipliersRow m={result.multipliers} />
                </div>
              </div>

              {/* Position plan */}
              {result.position && <PositionCard pos={result.position} />}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
