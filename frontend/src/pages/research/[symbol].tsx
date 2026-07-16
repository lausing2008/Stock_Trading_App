import { useState, useCallback, useEffect, useRef } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import { api, type ResearchReport, type ChecklistItem } from '@/lib/api';
import { loadSettings } from '@/lib/settings';

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmt$(n: number | null | undefined, digits = 2) {
  if (n == null) return '—';
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function fmtCap(n: number | null | undefined) {
  if (n == null) return '—';
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toLocaleString()}`;
}

function fmtPct(n: number | null | undefined) {
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;
}

function scoreColor(s: number) {
  if (s >= 80) return '#4ade80';
  if (s >= 65) return '#facc15';
  if (s >= 50) return '#fb923c';
  return '#f87171';
}

function recColor(r: string) {
  if (r === 'STRONG BUY') return '#4ade80';
  if (r === 'BUY') return '#86efac';
  if (r === 'WATCH') return '#facc15';
  if (r === 'AVOID') return '#fb923c';
  return '#f87171';
}

function recBg(r: string) {
  if (r === 'STRONG BUY') return 'rgba(74,222,128,0.15)';
  if (r === 'BUY') return 'rgba(74,222,128,0.1)';
  if (r === 'WATCH') return 'rgba(250,204,21,0.15)';
  if (r === 'AVOID') return 'rgba(251,146,60,0.15)';
  return 'rgba(248,113,113,0.15)';
}

function ScoreBar({ label, score, weight }: { label: string; score: number; weight: number }) {
  const c = scoreColor(score);
  return (
    <div style={{ marginBottom: '10px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
        <span style={{ fontSize: '12px', color: '#94a3b8' }}>{label} <span style={{ color: '#475569', fontSize: '10px' }}>({weight}%)</span></span>
        <span style={{ fontSize: '12px', fontWeight: 700, color: c }}>{score}</span>
      </div>
      <div style={{ height: '5px', borderRadius: '3px', background: '#1e293b', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${score}%`, background: c, borderRadius: '3px', transition: 'width 0.6s ease' }} />
      </div>
    </div>
  );
}

function Badge({ status }: { status: 'pass' | 'warning' | 'fail' }) {
  const cfg = {
    pass:    { label: 'PASS',    color: '#4ade80', bg: 'rgba(74,222,128,0.12)',  border: 'rgba(74,222,128,0.3)'  },
    warning: { label: 'WARN',    color: '#facc15', bg: 'rgba(250,204,21,0.12)', border: 'rgba(250,204,21,0.3)'  },
    fail:    { label: 'FAIL',    color: '#f87171', bg: 'rgba(248,113,113,0.12)',border: 'rgba(248,113,113,0.3)' },
  }[status];
  return (
    <span style={{ fontSize: '9px', fontWeight: 800, padding: '2px 6px', borderRadius: '4px', letterSpacing: '0.05em', color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.border}`, flexShrink: 0 }}>
      {cfg.label}
    </span>
  );
}

function ChecklistRow({ item }: { item: ChecklistItem }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
      <Badge status={item.status} />
      <span style={{ fontSize: '12px', color: '#94a3b8', flex: 1 }}>{item.item}</span>
      {item.note && <span style={{ fontSize: '10px', color: '#475569', fontFamily: 'ui-monospace,monospace' }}>{item.note}</span>}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: '#0d1829', border: '1px solid #1e293b', borderRadius: '12px', padding: '20px', marginBottom: '16px' }}>
      <div style={{ fontSize: '13px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '16px' }}>{title}</div>
      {children}
    </div>
  );
}

function DataRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
      <span style={{ fontSize: '12px', color: '#475569' }}>{label}</span>
      <span style={{ fontSize: '12px', fontWeight: 600, color: color ?? '#e2e8f0' }}>{value}</span>
    </div>
  );
}

function CrossBadge({ status }: { status: string }) {
  if (status === 'golden_cross') return <span style={{ color: '#4ade80', fontWeight: 700, fontSize: '11px' }}>Golden Cross ✓</span>;
  if (status === 'death_cross') return <span style={{ color: '#f87171', fontWeight: 700, fontSize: '11px' }}>Death Cross ✗</span>;
  return <span style={{ color: '#64748b', fontSize: '11px' }}>No Cross</span>;
}

function MoatBadge({ rating }: { rating: string }) {
  const c = { 'Very Strong': '#4ade80', Strong: '#86efac', Moderate: '#facc15', Weak: '#fb923c', None: '#f87171' }[rating] ?? '#94a3b8';
  return <span style={{ color: c, fontWeight: 700 }}>{rating}</span>;
}

function RRBadge({ a }: { a: string }) {
  const c = { Excellent: '#4ade80', Good: '#86efac', Average: '#facc15', Poor: '#f87171' }[a] ?? '#94a3b8';
  return <span style={{ color: c, fontWeight: 700 }}>{a}</span>;
}

// ── Tab definitions ────────────────────────────────────────────────────────────

const TABS = ['Summary', 'Technical', 'Fundamental', 'Company', 'Industry', 'Economic', 'Checklist', 'Trading Plan', 'AI Verdict'] as const;
type Tab = typeof TABS[number];

// ── Main page ──────────────────────────────────────────────────────────────────

export default function ResearchPage() {
  const router = useRouter();
  const symbol = (router.query.symbol as string | undefined)?.toUpperCase() ?? '';

  const [settings, setSettings] = useState<ReturnType<typeof loadSettings> | null>(null);
  useEffect(() => { setSettings(loadSettings()); }, []);
  const provider = settings?.aiProvider === 'deepseek' ? 'deepseek' : 'claude';
  const apiKey = provider === 'deepseek' ? (settings?.deepseekApiKey ?? '') : (settings?.claudeApiKey ?? '');
  const model = provider === 'deepseek' ? (settings?.deepseekModel ?? 'deepseek-chat') : (settings?.claudeModel ?? 'claude-sonnet-4-6');

  const [tab, setTab] = useState<Tab>('Summary');
  const [report, setReport] = useState<ResearchReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [portfolioSize, setPortfolioSize] = useState(100000);
  const [maxRisk, setMaxRisk] = useState(2.0);
  const [showConfig, setShowConfig] = useState(false);
  const [customApiKey, setCustomApiKey] = useState('');
  const [chatMessages, setChatMessages] = useState<{role: string; content: string}[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const [printMode, setPrintMode] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const effectiveKey = customApiKey || apiKey;

  useEffect(() => {
    if (!printMode) return;
    const prevTitle = document.title;
    document.title = `${symbol} — Research Report`;
    window.print();
    document.title = prevTitle;
    setPrintMode(false);
  }, [printMode, symbol]);

  useEffect(() => {
    if (!symbol) return;
    api.getResearch(symbol).then(r => { setReport(r); setTab('Summary'); }).catch(() => {});
  }, [symbol]);

  const generate = useCallback(async () => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.generateResearch(symbol, {
        provider,
        model,
        api_key: effectiveKey,
        portfolio_size: portfolioSize,
        max_risk_pct: maxRisk,
      });
      setReport(r);
      setTab('Summary');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [symbol, effectiveKey, provider, model, portfolioSize, maxRisk]);

  if (!symbol) return null;

  return (
    <div style={{ maxWidth: '1000px', margin: '0 auto', paddingBottom: '60px' }}>

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <Link href="/research" style={{ color: '#475569', fontSize: '12px', textDecoration: 'none' }}>← Research</Link>
            <h1 style={{ fontSize: '26px', fontWeight: 800, color: '#818cf8', fontFamily: 'ui-monospace,monospace', margin: 0 }}>{symbol}</h1>
            {report && <span style={{ fontSize: '13px', color: '#475569' }}>{report.company_name}</span>}
          </div>
          {report && (() => {
            const genDate = report.generated_at ? new Date(report.generated_at) : null;
            const validDate = genDate && !isNaN(genDate.getTime());
            const ageMs = validDate ? Date.now() - genDate!.getTime() : 0;
            const ageDays = Math.floor(ageMs / 86400000);
            const stale = ageDays >= 14;
            const aging = !stale && ageDays >= 7;
            return (
              <div style={{ fontSize: '11px', color: '#334155', marginTop: '4px', display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                <span>Generated {validDate ? genDate!.toLocaleString() : 'just now'}</span>
                {aging && (
                  <span style={{ fontSize: '10px', fontWeight: 700, padding: '1px 6px', borderRadius: '4px', color: '#f59e0b', background: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.3)' }}>
                    {ageDays}d old — consider regenerating
                  </span>
                )}
                {stale && (
                  <span style={{ fontSize: '10px', fontWeight: 700, padding: '1px 6px', borderRadius: '4px', color: '#f87171', background: 'rgba(248,113,113,0.12)', border: '1px solid rgba(248,113,113,0.3)' }}>
                    STALE · {ageDays}d — data may not reflect current conditions
                  </span>
                )}
                <span className="no-print">
                  <button onClick={() => { setReport(null); api.clearResearch(symbol).catch(() => {}); }} style={{ fontSize: '10px', color: '#475569', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline', padding: 0 }}>Regenerate</button>
                </span>
              </div>
            );
          })()}
        </div>

        {report && (
          <div className="no-print">
            <button
              onClick={() => setPrintMode(true)}
              style={{ padding: '8px 18px', borderRadius: '8px', border: '1px solid rgba(129,140,248,0.35)', background: 'rgba(129,140,248,0.08)', color: '#818cf8', fontSize: '12px', fontWeight: 600, cursor: 'pointer' }}
            >
              ↓ Export PDF
            </button>
          </div>
        )}

        {!report && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', alignItems: 'flex-end' }}>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <button onClick={() => setShowConfig(s => !s)} style={{ fontSize: '11px', color: '#475569', background: 'none', border: '1px solid #1e293b', borderRadius: '6px', padding: '4px 10px', cursor: 'pointer' }}>⚙ Config</button>
              <button
                onClick={generate}
                disabled={loading}
                style={{ padding: '10px 24px', borderRadius: '10px', border: 'none', background: loading ? '#1e293b' : 'linear-gradient(135deg,#4ade80,#22c55e)', color: loading ? '#475569' : '#000', fontSize: '14px', fontWeight: 700, cursor: loading ? 'default' : 'pointer' }}
              >
                {loading ? 'Analyzing…' : 'Generate Report'}
              </button>
            </div>
            {showConfig && (
              <div style={{ padding: '14px', borderRadius: '10px', border: '1px solid #1e293b', background: '#0d1829', display: 'flex', flexDirection: 'column', gap: '10px', minWidth: '280px' }}>
                <div style={{ fontSize: '11px', color: '#64748b', fontWeight: 700, textTransform: 'uppercase' }}>Analysis Config</div>
                <label style={{ fontSize: '12px', color: '#94a3b8' }}>
                  API Key (overrides settings)
                  <input value={customApiKey} onChange={e => setCustomApiKey(e.target.value)} type="password" placeholder="sk-ant-… or leave blank" style={{ display: 'block', width: '100%', marginTop: '4px', padding: '6px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#080f1e', color: '#f1f5f9', fontSize: '12px', boxSizing: 'border-box' }} />
                </label>
                <label style={{ fontSize: '12px', color: '#94a3b8' }}>
                  Portfolio Size ($)
                  <input type="number" value={portfolioSize} onChange={e => setPortfolioSize(+e.target.value)} style={{ display: 'block', width: '100%', marginTop: '4px', padding: '6px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#080f1e', color: '#f1f5f9', fontSize: '12px', boxSizing: 'border-box' }} />
                </label>
                <label style={{ fontSize: '12px', color: '#94a3b8' }}>
                  Max Risk Per Trade (%)
                  <input type="number" value={maxRisk} onChange={e => setMaxRisk(+e.target.value)} min={0.5} max={10} step={0.5} style={{ display: 'block', width: '100%', marginTop: '4px', padding: '6px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#080f1e', color: '#f1f5f9', fontSize: '12px', boxSizing: 'border-box' }} />
                </label>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Quality warning banner ──────────────────────────────────────────── */}
      {report?.report_quality === 'fallback' && (
        <div style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: '10px', padding: '10px 16px', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '16px' }}>⚠</span>
          <div>
            <span style={{ fontSize: '13px', fontWeight: 700, color: '#f87171' }}>AI analysis unavailable — </span>
            <span style={{ fontSize: '12px', color: '#94a3b8' }}>Claude timed out or returned an error. Qualitative scores (company / industry / economic) are placeholder defaults, not real analysis. Click Regenerate when the AI provider is available.</span>
          </div>
        </div>
      )}
      {report?.report_quality === 'partial' && (
        <div style={{ background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.3)', borderRadius: '10px', padding: '10px 16px', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '16px' }}>⚠</span>
          <div>
            <span style={{ fontSize: '13px', fontWeight: 700, color: '#fbbf24' }}>Partial data — </span>
            <span style={{ fontSize: '12px', color: '#94a3b8' }}>Some upstream services were unavailable when this report was generated. One or more score components may be estimated. Regenerate for a complete report.</span>
          </div>
        </div>
      )}

      {error && (
        <div style={{ padding: '12px 16px', borderRadius: '8px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#f87171', fontSize: '13px', marginBottom: '16px', lineHeight: 1.5 }}>
          {error}
          {!effectiveKey && (
            <div style={{ marginTop: '8px' }}>
              <input value={customApiKey} onChange={e => setCustomApiKey(e.target.value)} type="password" placeholder="Paste your API key here…" style={{ width: '100%', padding: '8px 12px', borderRadius: '6px', border: '1px solid #334155', background: '#080f1e', color: '#f1f5f9', fontSize: '13px', boxSizing: 'border-box', outline: 'none' }} />
            </div>
          )}
        </div>
      )}

      {loading && (
        <div style={{ textAlign: 'center', padding: '60px 0' }}>
          <div style={{ fontSize: '32px', marginBottom: '16px', animation: 'spin 2s linear infinite', display: 'inline-block' }}>⚙</div>
          <div style={{ color: '#64748b', fontSize: '14px' }}>Analyzing {symbol} — gathering data and running AI analysis… (this can take 1–2 minutes)</div>
          <div style={{ color: '#334155', fontSize: '12px', marginTop: '8px' }}>This may take 20–40 seconds</div>
        </div>
      )}

      {!report && !loading && !error && (
        <div style={{ textAlign: 'center', padding: '80px 0', color: '#334155' }}>
          <div style={{ fontSize: '48px', marginBottom: '16px' }}>🔬</div>
          <div style={{ fontSize: '16px', color: '#475569', marginBottom: '8px' }}>Planning Stage Research Engine</div>
          <div style={{ fontSize: '13px', color: '#334155', maxWidth: '480px', margin: '0 auto', lineHeight: 1.6 }}>
            Click <strong style={{ color: '#4ade80' }}>Generate Report</strong> to run a comprehensive AI-powered analysis covering technical, fundamental, company, industry, and economic dimensions.
          </div>
          <div style={{ marginTop: '20px', padding: '14px', borderRadius: '10px', border: '1px solid rgba(250,204,21,0.3)', background: 'rgba(250,204,21,0.05)', maxWidth: '400px', margin: '20px auto 0' }}>
            <div style={{ fontSize: '12px', color: '#facc15', marginBottom: '4px' }}>
              {effectiveKey ? 'API key configured — full AI analysis will run.' : 'No API key — computed scores will work, AI narrative will be skipped. Optionally add a key:'}
            </div>
            {!effectiveKey && (
              <input value={customApiKey} onChange={e => setCustomApiKey(e.target.value)} type="password" placeholder="sk-ant-…" style={{ width: '100%', marginTop: '8px', padding: '8px 12px', borderRadius: '6px', border: '1px solid #334155', background: '#080f1e', color: '#f1f5f9', fontSize: '13px', boxSizing: 'border-box' }} />
            )}
          </div>
        </div>
      )}

      {report && (
        <>
          {/* ── Executive banner ─────────────────────────────────────────────── */}
          <div style={{ background: '#0d1829', border: `1px solid ${recColor(report.recommendation)}40`, borderRadius: '14px', padding: '20px 24px', marginBottom: '20px' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'flex-start' }}>

              {/* Left: company info */}
              <div style={{ flex: 1, minWidth: '200px' }}>
                <div style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>{report.company_name}</div>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
                  {report.sector && <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '4px', background: 'rgba(99,102,241,0.12)', color: '#818cf8' }}>{report.sector}</span>}
                  {report.beta != null && <span style={{ fontSize: '11px', color: '#475569' }}>β {report.beta.toFixed(2)}</span>}
                  {report.next_earnings && <span style={{ fontSize: '11px', color: '#fb923c' }}>Earnings: {report.next_earnings}{report.days_to_earnings != null ? ` (${report.days_to_earnings}d)` : ''}</span>}
                </div>
                <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
                  <div><div style={{ fontSize: '10px', color: '#475569' }}>Price</div><div style={{ fontSize: '20px', fontWeight: 800, fontFamily: 'ui-monospace,monospace', color: '#f1f5f9' }}>{fmt$(report.current_price)}</div></div>
                  <div><div style={{ fontSize: '10px', color: '#475569' }}>Market Cap</div><div style={{ fontSize: '14px', fontWeight: 600, color: '#94a3b8' }}>{fmtCap(report.market_cap)}</div></div>
                  {report.week_52_high != null && <div><div style={{ fontSize: '10px', color: '#475569' }}>52W High/Low</div><div style={{ fontSize: '12px', color: '#94a3b8' }}>{fmt$(report.week_52_high)} / {fmt$(report.week_52_low)}</div></div>}
                  {report.analyst?.target_price != null && <div><div style={{ fontSize: '10px', color: '#475569' }}>Analyst Target</div><div style={{ fontSize: '12px', color: '#4ade80' }}>{fmt$(report.analyst.target_price)}</div></div>}
                </div>
              </div>

              {/* Center: overall score */}
              <div style={{ textAlign: 'center', padding: '0 20px' }}>
                <div style={{ fontSize: '48px', fontWeight: 800, color: scoreColor(report.overall_score), lineHeight: 1 }}>{report.overall_score}</div>
                <div style={{ fontSize: '11px', color: '#475569', marginTop: '2px' }}>Overall Score</div>
                <div style={{ marginTop: '10px', padding: '6px 16px', borderRadius: '8px', background: recBg(report.recommendation), border: `1px solid ${recColor(report.recommendation)}55`, color: recColor(report.recommendation), fontSize: '14px', fontWeight: 800, letterSpacing: '0.05em' }}>
                  {report.recommendation}
                </div>
                <div style={{ fontSize: '10px', color: '#475569', marginTop: '6px' }}>Confidence: {report.confidence}%</div>
              </div>

              {/* Right: sub-scores */}
              <div style={{ flex: 1, minWidth: '200px' }}>
                <ScoreBar label="Technical" score={report.scores.technical} weight={25} />
                <ScoreBar label="Fundamental" score={report.scores.fundamental} weight={30} />
                <ScoreBar label="Company" score={report.scores.company} weight={15} />
                <ScoreBar label="Industry" score={report.scores.industry} weight={15} />
                <ScoreBar label="Economic" score={report.scores.economic} weight={15} />
              </div>
            </div>

            {/* Signal + ranking row */}
            <div style={{ display: 'flex', gap: '12px', marginTop: '16px', paddingTop: '14px', borderTop: '1px solid rgba(255,255,255,0.05)', flexWrap: 'wrap' }}>
              {report.signal?.signal && (
                <div style={{ padding: '6px 14px', borderRadius: '8px', background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.25)' }}>
                  <span style={{ fontSize: '10px', color: '#475569' }}>AI Signal </span>
                  <span style={{ fontSize: '13px', fontWeight: 700, color: '#818cf8' }}>{report.signal.signal}</span>
                  {report.signal.confidence != null && <span style={{ fontSize: '10px', color: '#475569' }}> · {report.signal.confidence.toFixed(0)}%</span>}
                </div>
              )}
              {report.ranking?.score != null && (
                <div style={{ padding: '6px 14px', borderRadius: '8px', background: 'rgba(250,204,21,0.06)', border: '1px solid rgba(250,204,21,0.2)' }}>
                  <span style={{ fontSize: '10px', color: '#475569' }}>K-Score </span>
                  <span style={{ fontSize: '13px', fontWeight: 700, color: '#facc15' }}>{report.ranking.score.toFixed(1)}</span>
                </div>
              )}
              {report.short_float_pct != null && (
                <div style={{ padding: '6px 14px', borderRadius: '8px', background: 'rgba(248,113,113,0.06)', border: '1px solid rgba(248,113,113,0.15)' }}>
                  <span style={{ fontSize: '10px', color: '#475569' }}>Short Float </span>
                  <span style={{ fontSize: '13px', fontWeight: 700, color: '#f87171' }}>{report.short_float_pct.toFixed(1)}%</span>
                </div>
              )}
              {report.analyst?.recommendation && (
                <div style={{ padding: '6px 14px', borderRadius: '8px', background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.15)' }}>
                  <span style={{ fontSize: '10px', color: '#475569' }}>Analysts ({report.analyst.num_analysts}) </span>
                  <span style={{ fontSize: '13px', fontWeight: 700, color: '#94a3b8' }}>{report.analyst.recommendation}</span>
                </div>
              )}
              {(report as unknown as Record<string, unknown>).dcf != null && (() => {
                const dcf = (report as unknown as Record<string, unknown>).dcf as {
                  dcf_fair_value: number;
                  margin_of_safety_pct: number;
                  assessment: string;
                  high_conviction: boolean;
                };
                const mos = dcf.margin_of_safety_pct;
                const mosColor = mos > 15 ? '#4ade80' : mos > -15 ? '#facc15' : '#f87171';
                return (
                  <div style={{ padding: '6px 14px', borderRadius: '8px', background: 'rgba(74,222,128,0.06)', border: `1px solid ${mosColor}33` }}>
                    <span style={{ fontSize: '10px', color: '#475569' }}>DCF Fair Value </span>
                    <span style={{ fontSize: '13px', fontWeight: 700, color: mosColor }}>${dcf.dcf_fair_value.toFixed(2)}</span>
                    <span style={{ fontSize: '10px', color: mosColor, marginLeft: '4px' }}>({mos >= 0 ? '+' : ''}{mos.toFixed(1)}%)</span>
                    {dcf.high_conviction && (
                      <span style={{ marginLeft: '6px', fontSize: '9px', fontWeight: 700, color: '#4ade80', background: 'rgba(74,222,128,0.15)', padding: '1px 5px', borderRadius: '3px' }}>
                        HIGH CONVICTION
                      </span>
                    )}
                  </div>
                );
              })()}
            </div>
          </div>

          {/* ── Tabs ─────────────────────────────────────────────────────────── */}
          <div className="no-print" style={{ display: 'flex', gap: '2px', overflowX: 'auto', marginBottom: '16px', borderBottom: '1px solid #1e293b', paddingBottom: '1px' }}>
            {TABS.map(t => (
              <button key={t} onClick={() => setTab(t)} style={{ padding: '8px 14px', borderRadius: '6px 6px 0 0', border: 'none', background: tab === t ? 'rgba(129,140,248,0.15)' : 'transparent', color: tab === t ? '#818cf8' : '#475569', fontSize: '12px', fontWeight: tab === t ? 700 : 400, cursor: 'pointer', whiteSpace: 'nowrap', borderBottom: tab === t ? '2px solid #818cf8' : '2px solid transparent' }}>
                {t}
              </button>
            ))}
          </div>

          {/* ── Tab: Summary ─────────────────────────────────────────────────── */}
          {(tab === 'Summary' || printMode) && (
            <div className="research-tab-panel" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <Section title="Bullish Factors">
                {report.executive_summary.bullish_factors.map((f, i) => (
                  <div key={i} style={{ display: 'flex', gap: '8px', padding: '7px 0', borderBottom: '1px solid rgba(255,255,255,0.03)', fontSize: '13px', color: '#94a3b8', lineHeight: 1.4 }}>
                    <span style={{ color: '#4ade80', flexShrink: 0 }}>↑</span>{f}
                  </div>
                ))}
              </Section>
              <Section title="Bearish Factors">
                {report.executive_summary.bearish_factors.map((f, i) => (
                  <div key={i} style={{ display: 'flex', gap: '8px', padding: '7px 0', borderBottom: '1px solid rgba(255,255,255,0.03)', fontSize: '13px', color: '#94a3b8', lineHeight: 1.4 }}>
                    <span style={{ color: '#f87171', flexShrink: 0 }}>↓</span>{f}
                  </div>
                ))}
              </Section>
              <Section title="Key Risks">
                {report.executive_summary.key_risks.map((r, i) => (
                  <div key={i} style={{ display: 'flex', gap: '8px', padding: '7px 0', borderBottom: '1px solid rgba(255,255,255,0.03)', fontSize: '13px', color: '#94a3b8', lineHeight: 1.4 }}>
                    <span style={{ color: '#fb923c', flexShrink: 0 }}>⚠</span>{r}
                  </div>
                ))}
              </Section>
              <Section title="Key Opportunities">
                {report.executive_summary.key_opportunities.map((o, i) => (
                  <div key={i} style={{ display: 'flex', gap: '8px', padding: '7px 0', borderBottom: '1px solid rgba(255,255,255,0.03)', fontSize: '13px', color: '#94a3b8', lineHeight: 1.4 }}>
                    <span style={{ color: '#818cf8', flexShrink: 0 }}>→</span>{o}
                  </div>
                ))}
              </Section>
            </div>
          )}

          {/* ── Tab: Technical ───────────────────────────────────────────────── */}
          {(tab === 'Technical' || printMode) && (() => {
            const t = report.technical;
            return (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                  <div style={{ fontSize: '28px', fontWeight: 800, color: scoreColor(t.score) }}>{t.score}</div>
                  <div>
                    <div style={{ fontSize: '16px', fontWeight: 700, color: '#e2e8f0' }}>{t.trend_verdict}</div>
                    <CrossBadge status={t.cross_status} />
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                  <Section title="Trend Analysis">
                    <DataRow label="Price vs 50-day SMA" value={t.price_vs_50_ema.ema ? `${fmt$(t.price_vs_50_ema.ema)} (${fmtPct(t.price_vs_50_ema.pct_diff)})` : '—'} color={t.price_vs_50_ema.value === 'above' ? '#4ade80' : '#f87171'} />
                    <DataRow label="Price vs 200-day SMA" value={t.price_vs_200_ema.ema ? `${fmt$(t.price_vs_200_ema.ema)} (${fmtPct(t.price_vs_200_ema.pct_diff)})` : '—'} color={t.price_vs_200_ema.value === 'above' ? '#4ade80' : '#f87171'} />
                    <div style={{ marginTop: '10px', padding: '10px', borderRadius: '8px', background: 'rgba(255,255,255,0.02)', fontSize: '12px', color: '#64748b', lineHeight: 1.5 }}>
                      {t.price_vs_200_ema.interpretation}
                    </div>
                  </Section>

                  <Section title="RSI &amp; MACD">
                    <DataRow label="RSI (14)" value={t.rsi.value != null ? `${t.rsi.value} — ${t.rsi.status}` : '—'}
                      color={t.rsi.status === 'Strong' || t.rsi.status === 'Healthy' ? '#4ade80' : t.rsi.status === 'Overbought' || t.rsi.status === 'Weak' ? '#f87171' : '#facc15'} />
                    <DataRow label="MACD Line" value={t.macd.line != null ? t.macd.line.toString() : '—'} />
                    <DataRow label="Signal Line" value={t.macd.signal != null ? t.macd.signal.toString() : '—'} />
                    <DataRow label="MACD Crossover" value={t.macd.crossover.replace('_', ' ')}
                      color={t.macd.crossover === 'bullish' ? '#4ade80' : t.macd.crossover === 'bearish' ? '#f87171' : '#64748b'} />
                    <div style={{ marginTop: '10px', padding: '10px', borderRadius: '8px', background: 'rgba(255,255,255,0.02)', fontSize: '12px', color: '#64748b', lineHeight: 1.5 }}>
                      {t.rsi.interpretation}
                    </div>
                  </Section>

                  <Section title="Volume Analysis">
                    <DataRow label="Current Volume" value={t.volume.current.toLocaleString()} />
                    <DataRow label="20-Day Avg Volume" value={t.volume.avg_20d.toLocaleString()} />
                    <DataRow label="Relative Volume (RVOL)" value={`${t.volume.rvol}x — ${t.volume.status}`}
                      color={t.volume.rvol >= 1.5 ? '#4ade80' : t.volume.rvol >= 1.0 ? '#facc15' : '#f87171'} />
                    <div style={{ marginTop: '10px', padding: '10px', borderRadius: '8px', background: 'rgba(255,255,255,0.02)', fontSize: '12px', color: '#64748b' }}>
                      {t.volume.interpretation}
                    </div>
                  </Section>

                  <Section title="Support &amp; Resistance">
                    <DataRow label="Nearest Support" value={fmt$(t.support_resistance.nearest_support)} color="#4ade80" />
                    <DataRow label="Major Support" value={fmt$(t.support_resistance.major_support)} color="#22c55e" />
                    <DataRow label="Nearest Resistance" value={fmt$(t.support_resistance.nearest_resistance)} color="#f87171" />
                    <DataRow label="Major Resistance" value={fmt$(t.support_resistance.major_resistance)} color="#dc2626" />
                  </Section>

                  <Section title="ATR &amp; Volatility">
                    <DataRow label="ATR (14)" value={fmt$(t.atr.value)} />
                    <DataRow label="ATR %" value={t.atr.pct != null ? `${t.atr.pct}%` : '—'} />
                    <DataRow label="Volatility Rating" value={t.atr.volatility_rating} color={t.atr.volatility_rating === 'High' ? '#f87171' : t.atr.volatility_rating === 'Moderate' ? '#facc15' : '#4ade80'} />
                  </Section>

                  <Section title="MACD Histogram">
                    <DataRow label="Histogram Value" value={t.histogram_analysis.value != null ? t.histogram_analysis.value.toString() : '—'}
                      color={t.histogram_analysis.value != null && t.histogram_analysis.value > 0 ? '#4ade80' : '#f87171'} />
                    <DataRow label="Status" value={t.histogram_analysis.status.replace(/_/g, ' ')}
                      color={t.histogram_analysis.status.startsWith('green') ? '#4ade80' : '#f87171'} />
                    <div style={{ marginTop: '10px', padding: '10px', borderRadius: '8px', background: 'rgba(255,255,255,0.02)', fontSize: '12px', color: '#64748b' }}>
                      {t.histogram_analysis.interpretation}
                    </div>
                  </Section>
                </div>
              </div>
            );
          })()}

          {/* ── Tab: Fundamental ─────────────────────────────────────────────── */}
          {(tab === 'Fundamental' || printMode) && (() => {
            const f = report.fundamental;
            return (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                  <div style={{ fontSize: '28px', fontWeight: 800, color: scoreColor(f.score) }}>{f.score}</div>
                  <div style={{ fontSize: '14px', color: '#64748b' }}>Fundamental Score</div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>

                  <Section title="Revenue &amp; EPS">
                    <DataRow label="Revenue Growth YoY" value={f.revenue.yoy_growth != null ? `${fmtPct(f.revenue.yoy_growth)}` : '—'}
                      color={f.revenue.yoy_growth != null && f.revenue.yoy_growth >= 0 ? '#4ade80' : '#f87171'} />
                    <DataRow label="Revenue Assessment" value={f.revenue.assessment} />
                    <DataRow label="EPS (Trailing)" value={fmt$(f.eps.trailing_eps)} />
                    <DataRow label="EPS (Forward)" value={fmt$(f.eps.forward_eps)} />
                    <DataRow label="EPS Growth YoY" value={f.eps.yoy_growth != null ? fmtPct(f.eps.yoy_growth) : '—'}
                      color={f.eps.yoy_growth != null && f.eps.yoy_growth >= 0 ? '#4ade80' : '#f87171'} />
                    <DataRow label="EPS Assessment" value={f.eps.assessment} />
                  </Section>

                  <Section title="Margins">
                    <DataRow label="Gross Margin" value={f.margins.gross != null ? `${f.margins.gross}%` : '—'} color="#4ade80" />
                    <DataRow label="Operating Margin" value={f.margins.operating != null ? `${f.margins.operating}%` : '—'} />
                    <DataRow label="Net Margin" value={f.margins.net != null ? `${f.margins.net}%` : '—'} />
                    <div style={{ marginTop: '10px', fontSize: '12px', color: '#475569', padding: '8px', background: 'rgba(255,255,255,0.02)', borderRadius: '6px' }}>{f.margins.comparison}</div>
                  </Section>

                  <Section title="Balance Sheet">
                    <DataRow label="Total Cash" value={fmtCap(f.balance_sheet.cash)} color="#4ade80" />
                    <DataRow label="Total Debt" value={fmtCap(f.balance_sheet.debt)} color="#f87171" />
                    <DataRow label="Debt/Equity Ratio" value={f.balance_sheet.de_ratio != null ? f.balance_sheet.de_ratio.toFixed(2) : '—'}
                      color={f.balance_sheet.de_ratio != null && f.balance_sheet.de_ratio < 1 ? '#4ade80' : f.balance_sheet.de_ratio != null && f.balance_sheet.de_ratio < 2 ? '#facc15' : '#f87171'} />
                    <DataRow label="Assessment" value={f.balance_sheet.assessment} />
                  </Section>

                  <Section title="Cash Flow">
                    <DataRow label="Operating Cash Flow" value={fmtCap(f.cash_flow.operating_cf)} />
                    <DataRow label="Free Cash Flow" value={fmtCap(f.cash_flow.fcf)} color={f.cash_flow.fcf != null && f.cash_flow.fcf > 0 ? '#4ade80' : '#f87171'} />
                    <DataRow label="FCF Margin" value={f.cash_flow.fcf_margin != null ? `${f.cash_flow.fcf_margin}%` : '—'} />
                    <DataRow label="Assessment" value={f.cash_flow.assessment} />
                  </Section>

                  <Section title="Valuation">
                    <DataRow label="P/E (Trailing)" value={f.valuation.pe != null ? f.valuation.pe.toString() : '—'} />
                    <DataRow label="P/E (Forward)" value={f.valuation.forward_pe != null ? f.valuation.forward_pe.toString() : '—'} />
                    <DataRow
                      label={f.valuation.peg_growth_source === 'revenue_growth' ? 'PEG Ratio (rev. proxy)' : 'PEG Ratio'}
                      value={f.valuation.peg != null ? f.valuation.peg.toString() : '—'}
                      color={f.valuation.peg_growth_source === 'revenue_growth' ? '#f59e0b' : undefined}
                    />
                    <DataRow label="Price/Sales (EV/Rev)" value={f.valuation.price_sales != null ? f.valuation.price_sales.toString() : '—'} />
                    <DataRow label="EV/EBITDA" value={f.valuation.ev_ebitda != null ? f.valuation.ev_ebitda.toString() : '—'} />
                    <DataRow label="Valuation" value={f.valuation.assessment} color={f.valuation.assessment === 'Undervalued' ? '#4ade80' : f.valuation.assessment === 'Overvalued' ? '#f87171' : '#facc15'} />
                  </Section>

                  <Section title="Profitability">
                    <DataRow label="Return on Equity (ROE)" value={f.profitability.roe != null ? `${f.profitability.roe}%` : '—'} color={f.profitability.roe != null && f.profitability.roe >= 15 ? '#4ade80' : undefined} />
                    <DataRow label="Return on Assets (ROA)" value={f.profitability.roa != null ? `${f.profitability.roa}%` : '—'} />
                    <DataRow label="Grade" value={f.profitability.grade} />
                  </Section>
                </div>
              </div>
            );
          })()}

          {/* ── Tab: Company ─────────────────────────────────────────────────── */}
          {(tab === 'Company' || printMode) && (() => {
            const c = report.company;
            return (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                  <div style={{ fontSize: '28px', fontWeight: 800, color: scoreColor(report.scores.company) }}>{report.scores.company}</div>
                  <div style={{ fontSize: '14px', color: '#64748b' }}>Company Score</div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>

                  <Section title="Business Model">
                    <p style={{ fontSize: '13px', color: '#94a3b8', lineHeight: 1.7, margin: 0 }}>{c.business_model}</p>
                  </Section>

                  <Section title="Competitive Moat">
                    <div style={{ marginBottom: '10px' }}>
                      <span style={{ fontSize: '12px', color: '#475569' }}>Moat Rating: </span>
                      <MoatBadge rating={c.moat?.rating ?? 'Unknown'} />
                    </div>
                    <p style={{ fontSize: '12px', color: '#64748b', lineHeight: 1.6, margin: 0 }}>{c.moat?.explanation}</p>
                  </Section>

                  <Section title="Competitive Advantages">
                    {Object.entries(c.competitive_advantage || {}).map(([key, val]) => (
                      <DataRow key={key} label={key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())} value={String(val)}
                        color={String(val).toLowerCase().includes('strong') || String(val).toLowerCase() === 'high' || String(val).toLowerCase() === 'excellent' ? '#4ade80' : String(val).toLowerCase().includes('weak') || String(val).toLowerCase() === 'low' || String(val).toLowerCase() === 'none' ? '#f87171' : undefined} />
                    ))}
                  </Section>

                  <Section title="Management Quality">
                    <DataRow label="Rating" value={c.management?.rating ?? '—'} />
                    <p style={{ fontSize: '12px', color: '#64748b', lineHeight: 1.6, marginTop: '10px', marginBottom: 0 }}>{c.management?.explanation}</p>
                  </Section>

                  <Section title="Insider Activity">
                    <DataRow label="Status" value={c.insider_activity?.status ?? '—'}
                      color={c.insider_activity?.status === 'Bullish' ? '#4ade80' : c.insider_activity?.status === 'Bearish' ? '#f87171' : '#facc15'} />
                    <p style={{ fontSize: '12px', color: '#64748b', lineHeight: 1.6, marginTop: '10px', marginBottom: 0 }}>{c.insider_activity?.explanation}</p>
                  </Section>

                  <Section title="Institutional Ownership">
                    <DataRow label="Owned by Institutions" value={c.institutional_ownership?.pct != null ? `${c.institutional_ownership.pct.toFixed(1)}%` : '—'} />
                    <DataRow label="Trend" value={c.institutional_ownership?.trend ?? '—'} />
                    <p style={{ fontSize: '12px', color: '#64748b', lineHeight: 1.6, marginTop: '10px', marginBottom: 0 }}>{c.institutional_ownership?.interpretation}</p>
                  </Section>
                </div>
              </div>
            );
          })()}

          {/* ── Tab: Industry ────────────────────────────────────────────────── */}
          {(tab === 'Industry' || printMode) && (() => {
            const ind = report.industry_analysis;
            return (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '16px', flexWrap: 'wrap' }}>
                  <div style={{ fontSize: '28px', fontWeight: 800, color: scoreColor(report.scores.industry) }}>{report.scores.industry}</div>
                  <div>
                    <div style={{ fontSize: '16px', fontWeight: 700, color: '#e2e8f0' }}>{ind?.verdict ?? '—'}</div>
                    <div style={{ fontSize: '12px', color: '#475569' }}>Industry Status: {ind?.status}</div>
                  </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>

                  <Section title="Industry Overview">
                    <DataRow label="Status" value={ind?.status ?? '—'} color={ind?.status === 'Growing' ? '#4ade80' : ind?.status === 'Declining' ? '#f87171' : '#facc15'} />
                    <p style={{ fontSize: '12px', color: '#64748b', lineHeight: 1.6, marginTop: '10px', marginBottom: 0 }}>{ind?.evidence}</p>
                  </Section>

                  <Section title="Verdict">
                    <DataRow label="Industry Verdict" value={ind?.verdict ?? '—'} color={ind?.verdict?.includes('Tailwind') ? '#4ade80' : ind?.verdict?.includes('Headwind') ? '#f87171' : '#facc15'} />
                    <p style={{ fontSize: '12px', color: '#64748b', lineHeight: 1.6, marginTop: '10px', marginBottom: 0 }}>{ind?.verdict_explanation}</p>
                  </Section>

                  <Section title="TAM &amp; Market">
                    <DataRow label="TAM Size" value={ind?.tam?.size ?? '—'} />
                    <DataRow label="TAM Growth" value={ind?.tam?.growth ?? '—'} />
                    <DataRow label="Expansion Potential" value={ind?.tam?.expansion_potential ?? '—'} />
                    <DataRow label="TAM Rating" value={ind?.tam?.rating ?? '—'} color={ind?.tam?.rating === 'Excellent' || ind?.tam?.rating === 'Good' ? '#4ade80' : '#facc15'} />
                  </Section>

                  <Section title="Market Share">
                    <DataRow label="Position" value={ind?.market_share?.position ?? '—'} />
                    <DataRow label="Trend" value={ind?.market_share?.trend ?? '—'} color={ind?.market_share?.trend === 'Gaining' ? '#4ade80' : ind?.market_share?.trend === 'Losing' ? '#f87171' : '#facc15'} />
                    <DataRow label="Verdict" value={ind?.market_share?.verdict ?? '—'} />
                  </Section>

                  <Section title="Regulatory Risk">
                    <DataRow label="Risk Level" value={ind?.regulatory_risk ?? '—'}
                      color={ind?.regulatory_risk === 'Low' ? '#4ade80' : ind?.regulatory_risk === 'High' ? '#f87171' : '#facc15'} />
                  </Section>

                  <Section title="Key Competitors">
                    {(ind?.competitors || []).length === 0 ? (
                      <div style={{ fontSize: '12px', color: '#334155' }}>No competitor data available</div>
                    ) : (ind?.competitors || []).map((c, i) => (
                      <div key={i} style={{ padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                        <div style={{ fontSize: '12px', fontWeight: 700, color: '#94a3b8' }}>{c.name}</div>
                        <div style={{ fontSize: '11px', color: '#475569', marginTop: '2px' }}>{c.relative_position}</div>
                      </div>
                    ))}
                  </Section>
                </div>
              </div>
            );
          })()}

          {/* ── Tab: Economic ────────────────────────────────────────────────── */}
          {(tab === 'Economic' || printMode) && (() => {
            const eco = report.economic;
            const rc = eco?.recession_risk;
            return (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                  <div style={{ fontSize: '28px', fontWeight: 800, color: scoreColor(report.scores.economic) }}>{report.scores.economic}</div>
                  <div style={{ fontSize: '14px', color: '#64748b' }}>Economic Score</div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>

                  <Section title="Federal Reserve">
                    <DataRow label="Fed Policy" value={eco?.fed?.status ?? '—'} color={eco?.fed?.status === 'Cutting' ? '#4ade80' : eco?.fed?.status === 'Hiking' ? '#f87171' : '#facc15'} />
                    <p style={{ fontSize: '12px', color: '#64748b', lineHeight: 1.6, marginTop: '10px', marginBottom: 0 }}>{eco?.fed?.impact}</p>
                  </Section>

                  <Section title="Inflation &amp; CPI">
                    <DataRow label="CPI Trend" value={eco?.inflation?.cpi_trend ?? '—'} color={eco?.inflation?.cpi_trend === 'Improving' ? '#4ade80' : eco?.inflation?.cpi_trend === 'Worsening' ? '#f87171' : '#facc15'} />
                    <p style={{ fontSize: '12px', color: '#64748b', lineHeight: 1.6, marginTop: '10px', marginBottom: 0 }}>{eco?.inflation?.impact}</p>
                  </Section>

                  <Section title="GDP &amp; Employment">
                    <DataRow label="GDP Status" value={eco?.gdp?.status ?? '—'} color={eco?.gdp?.status === 'Expanding' ? '#4ade80' : eco?.gdp?.status === 'Contracting' ? '#f87171' : '#facc15'} />
                    <DataRow label="Employment" value={eco?.employment?.status ?? '—'} />
                    <p style={{ fontSize: '12px', color: '#64748b', lineHeight: 1.6, marginTop: '10px', marginBottom: 0 }}>{eco?.gdp?.significance}</p>
                  </Section>

                  <Section title="Recession Risk Checklist">
                    {rc && (
                      <>
                        <ChecklistRow item={{ item: 'Yield Curve Inverted?', status: rc.yield_curve_inverted ? 'fail' : 'pass', note: rc.yield_curve_inverted ? 'Yes' : 'No' }} />
                        <ChecklistRow item={{ item: 'GDP Negative 2 Quarters?', status: rc.gdp_negative ? 'fail' : 'pass', note: rc.gdp_negative ? 'Yes' : 'No' }} />
                        <ChecklistRow item={{ item: 'Unemployment Rising?', status: rc.unemployment_rising ? 'warning' : 'pass', note: rc.unemployment_rising ? 'Yes' : 'No' }} />
                        <ChecklistRow item={{ item: 'Consumer Confidence Falling?', status: rc.consumer_confidence_falling ? 'warning' : 'pass', note: rc.consumer_confidence_falling ? 'Yes' : 'No' }} />
                        <DataRow label="Recession Risk Rating" value={rc.rating}
                          color={rc.rating === 'Low' ? '#4ade80' : rc.rating === 'High' ? '#f87171' : '#facc15'} />
                      </>
                    )}
                  </Section>

                  <div style={{ gridColumn: '1/-1' }}>
                    <Section title="Market Environment">
                      <DataRow label="Favored Style" value={eco?.market_environment?.favored_style ?? '—'} color="#818cf8" />
                      <p style={{ fontSize: '12px', color: '#64748b', lineHeight: 1.6, marginTop: '10px', marginBottom: 0 }}>{eco?.market_environment?.explanation}</p>
                    </Section>
                  </div>
                </div>
              </div>
            );
          })()}

          {/* ── Tab: Checklist ───────────────────────────────────────────────── */}
          {(tab === 'Checklist' || printMode) && (() => {
            const cl = report.checklist;
            const layers = [
              { title: 'Layer 1 — Company', items: cl.layer1_company },
              { title: 'Layer 2 — Industry', items: cl.layer2_industry },
              { title: 'Layer 3 — Economy', items: cl.layer3_economy },
              { title: 'Layer 4 — Technical', items: cl.layer4_technical },
            ];
            return (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                {layers.map(({ title, items }) => {
                  const passes = items.filter(i => i.status === 'pass').length;
                  const warns = items.filter(i => i.status === 'warning').length;
                  const fails = items.filter(i => i.status === 'fail').length;
                  return (
                    <Section key={title} title={title}>
                      <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
                        <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '4px', background: 'rgba(74,222,128,0.1)', color: '#4ade80' }}>{passes} Pass</span>
                        <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '4px', background: 'rgba(250,204,21,0.1)', color: '#facc15' }}>{warns} Warn</span>
                        <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '4px', background: 'rgba(248,113,113,0.1)', color: '#f87171' }}>{fails} Fail</span>
                      </div>
                      {items.map((item, i) => <ChecklistRow key={i} item={item} />)}
                    </Section>
                  );
                })}
              </div>
            );
          })()}

          {/* ── Tab: Trading Plan ────────────────────────────────────────────── */}
          {(tab === 'Trading Plan' || printMode) && (() => {
            const ep = report.entry_planning;
            const ps = report.position_sizing;
            return (
              <div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>

                  <Section title="Entry Zones">
                    <div style={{ padding: '12px', borderRadius: '8px', background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.2)', marginBottom: '10px' }}>
                      <div style={{ fontSize: '10px', fontWeight: 700, color: '#818cf8', textTransform: 'uppercase', marginBottom: '4px' }}>Aggressive Entry</div>
                      <div style={{ fontSize: '16px', fontWeight: 700, color: '#e2e8f0', fontFamily: 'ui-monospace,monospace' }}>{ep?.aggressive_entry?.zone ?? '—'}</div>
                      <div style={{ fontSize: '11px', color: '#475569', marginTop: '4px' }}>{ep?.aggressive_entry?.rationale}</div>
                    </div>
                    <div style={{ padding: '12px', borderRadius: '8px', background: 'rgba(74,222,128,0.06)', border: '1px solid rgba(74,222,128,0.2)' }}>
                      <div style={{ fontSize: '10px', fontWeight: 700, color: '#4ade80', textTransform: 'uppercase', marginBottom: '4px' }}>Conservative Entry</div>
                      <div style={{ fontSize: '16px', fontWeight: 700, color: '#e2e8f0', fontFamily: 'ui-monospace,monospace' }}>{ep?.conservative_entry?.zone ?? '—'}</div>
                      <div style={{ fontSize: '11px', color: '#475569', marginTop: '4px' }}>{ep?.conservative_entry?.rationale}</div>
                    </div>
                  </Section>

                  <Section title="Stop Loss">
                    <div style={{ padding: '14px', borderRadius: '8px', background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.25)', textAlign: 'center' }}>
                      <div style={{ fontSize: '10px', fontWeight: 700, color: '#f87171', textTransform: 'uppercase' }}>Stop Loss</div>
                      <div style={{ fontSize: '28px', fontWeight: 800, color: '#f87171', fontFamily: 'ui-monospace,monospace', margin: '6px 0' }}>{fmt$(ep?.stop_loss?.price)}</div>
                      <div style={{ fontSize: '11px', color: '#475569' }}>{ep?.stop_loss?.method}</div>
                    </div>
                    <div style={{ marginTop: '10px', fontSize: '12px', color: '#64748b', lineHeight: 1.5 }}>{ep?.stop_loss?.rationale}</div>
                  </Section>

                  <Section title="Profit Targets">
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      {(ep?.take_profit || []).map(t => (
                        <div key={t.target} style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '10px 14px', borderRadius: '8px', background: 'rgba(74,222,128,0.05)', border: '1px solid rgba(74,222,128,0.15)' }}>
                          <div style={{ fontSize: '11px', fontWeight: 700, color: '#475569', width: '50px' }}>T{t.target}</div>
                          <div style={{ fontFamily: 'ui-monospace,monospace', fontWeight: 700, color: '#4ade80', fontSize: '15px' }}>{fmt$(t.price)}</div>
                          <div style={{ fontSize: '12px', color: '#22c55e', fontWeight: 600 }}>+{t.gain_pct}%</div>
                          <div style={{ fontSize: '11px', color: '#334155', flex: 1 }}>{t.rationale}</div>
                        </div>
                      ))}
                    </div>
                  </Section>

                  <Section title="Risk / Reward">
                    <DataRow label="Expected Reward" value={ep?.risk_reward?.expected_reward != null ? `$${ep.risk_reward.expected_reward.toFixed(2)}` : '—'} color="#4ade80" />
                    <DataRow label="Expected Risk" value={ep?.risk_reward?.expected_risk != null ? `$${ep.risk_reward.expected_risk.toFixed(2)}` : '—'} color="#f87171" />
                    <DataRow label="Risk/Reward Ratio" value={ep?.risk_reward?.ratio != null ? `${ep.risk_reward.ratio}:1` : '—'} color="#facc15" />
                    <div style={{ marginTop: '10px', textAlign: 'center' }}>
                      <RRBadge a={ep?.risk_reward?.assessment ?? '—'} />
                    </div>
                  </Section>

                  <div style={{ gridColumn: '1/-1' }}>
                    <Section title="Position Sizing">
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: '12px' }}>
                        {[
                          { label: 'Portfolio Size', value: ps?.portfolio_size != null ? `$${ps.portfolio_size.toLocaleString()}` : '—' },
                          { label: 'Max Risk %', value: ps?.max_risk_pct != null ? `${ps.max_risk_pct}%` : '—' },
                          { label: 'Dollar Risk', value: ps?.dollar_risk != null ? `$${ps.dollar_risk.toLocaleString()}` : '—', color: '#fb923c' },
                          { label: 'Stop Distance', value: ps?.stop_distance != null ? `$${ps.stop_distance.toFixed(2)}` : '—' },
                          { label: 'Share Quantity', value: ps?.share_quantity != null ? ps.share_quantity.toLocaleString() : '—', color: '#818cf8' },
                          { label: 'Position Size', value: ps?.position_size != null ? `$${ps.position_size.toLocaleString()}` : '—', color: '#4ade80' },
                          { label: '% of Portfolio', value: ps?.pct_of_portfolio != null ? `${ps.pct_of_portfolio}%` : '—' },
                        ].map(({ label, value, color }) => (
                          <div key={label} style={{ padding: '12px', borderRadius: '8px', background: 'rgba(255,255,255,0.02)', border: '1px solid #1e293b', textAlign: 'center' }}>
                            <div style={{ fontSize: '10px', color: '#475569', marginBottom: '4px' }}>{label}</div>
                            <div style={{ fontSize: '16px', fontWeight: 700, color: color ?? '#e2e8f0', fontFamily: 'ui-monospace,monospace' }}>{value}</div>
                          </div>
                        ))}
                      </div>
                    </Section>
                  </div>

                  <div style={{ gridColumn: '1/-1' }}>
                    <Section title="Trade Invalidation Conditions">
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        {report.trade_invalidation.map((cond, i) => (
                          <div key={i} style={{ display: 'flex', gap: '10px', padding: '8px 12px', borderRadius: '8px', background: 'rgba(248,113,113,0.05)', border: '1px solid rgba(248,113,113,0.15)', fontSize: '13px', color: '#94a3b8', lineHeight: 1.4 }}>
                            <span style={{ color: '#f87171', flexShrink: 0 }}>✗</span>{cond}
                          </div>
                        ))}
                      </div>
                    </Section>
                  </div>
                </div>
              </div>
            );
          })()}

          {/* ── Tab: AI Verdict ──────────────────────────────────────────────── */}
          {(tab === 'AI Verdict' || printMode) && (() => {
            const v = report.ai_verdict;
            const isFallback = report.report_quality === 'fallback';
            const buyColor = v?.can_buy_today === 'YES' ? '#4ade80' : v?.can_buy_today === 'NO' ? '#f87171' : '#facc15';
            return (
              <div>
                {/* Hero verdict */}
                <div style={{ textAlign: 'center', padding: '32px', background: '#0d1829', borderRadius: '14px', border: `1px solid ${isFallback ? '#ef4444' : recColor(v?.final_recommendation ?? '')}40`, marginBottom: '16px' }}>
                  {isFallback ? (
                    <>
                      <div style={{ fontSize: '14px', color: '#ef4444', marginBottom: '8px', fontWeight: 600 }}>AI Analysis Unavailable</div>
                      <div style={{ fontSize: '32px', fontWeight: 800, color: '#ef4444', lineHeight: 1 }}>INSUFFICIENT DATA</div>
                      <div style={{ marginTop: '12px', fontSize: '13px', color: '#94a3b8', maxWidth: '500px', margin: '12px auto 0', lineHeight: 1.6 }}>
                        The AI analysis could not be completed. Please retry later or check your API configuration.
                      </div>
                    </>
                  ) : (
                    <>
                      <div style={{ fontSize: '14px', color: '#475569', marginBottom: '8px' }}>Can I Buy This Stock Today?</div>
                      <div style={{ fontSize: '56px', fontWeight: 800, color: buyColor, lineHeight: 1 }}>{v?.can_buy_today ?? '—'}</div>
                      <div style={{ marginTop: '16px', fontSize: '14px', color: '#94a3b8', maxWidth: '600px', margin: '16px auto 0', lineHeight: 1.7 }}>{v?.why}</div>
                      <div style={{ marginTop: '20px', display: 'flex', justifyContent: 'center', gap: '12px', flexWrap: 'wrap' }}>
                        <div style={{ padding: '8px 20px', borderRadius: '10px', background: recBg(v?.final_recommendation ?? ''), border: `1px solid ${recColor(v?.final_recommendation ?? '')}55`, color: recColor(v?.final_recommendation ?? ''), fontSize: '16px', fontWeight: 800, letterSpacing: '0.04em' }}>
                          {v?.final_recommendation}
                        </div>
                        <div style={{ padding: '8px 20px', borderRadius: '10px', background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.25)', color: '#818cf8', fontSize: '14px', fontWeight: 600 }}>
                          Confidence: {v?.confidence_pct ?? 0}%
                        </div>
                      </div>
                    </>
                  )}
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>

                  <Section title="Biggest Risks">
                    {(v?.biggest_risks || []).map((r, i) => (
                      <div key={i} style={{ display: 'flex', gap: '8px', padding: '7px 0', borderBottom: '1px solid rgba(255,255,255,0.03)', fontSize: '13px', color: '#94a3b8', lineHeight: 1.4 }}>
                        <span style={{ color: '#f87171', flexShrink: 0 }}>⚠</span>{r}
                      </div>
                    ))}
                  </Section>

                  <Section title="What Must Improve Before Buying?">
                    {(v?.must_improve || []).map((cond, i) => (
                      <div key={i} style={{ display: 'flex', gap: '8px', padding: '7px 0', borderBottom: '1px solid rgba(255,255,255,0.03)', fontSize: '13px', color: '#94a3b8', lineHeight: 1.4 }}>
                        <span style={{ color: '#fb923c', flexShrink: 0 }}>→</span>{cond}
                      </div>
                    ))}
                  </Section>

                  <div style={{ gridColumn: '1/-1' }}>
                    <Section title="What Would Make This a Strong Buy?">
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                        {(v?.strong_buy_catalysts || []).map((cat, i) => (
                          <div key={i} style={{ padding: '8px 14px', borderRadius: '8px', background: 'rgba(74,222,128,0.08)', border: '1px solid rgba(74,222,128,0.2)', fontSize: '13px', color: '#4ade80' }}>
                            {cat}
                          </div>
                        ))}
                      </div>
                    </Section>
                  </div>
                </div>
              </div>
            );
          })()}

          {/* Regenerate button at bottom */}
          <div className="no-print" style={{ textAlign: 'center', marginTop: '24px' }}>
            <button
              onClick={() => { setReport(null); setChatMessages([]); api.clearResearch(symbol).catch(() => {}); }}
              style={{ fontSize: '12px', color: '#475569', background: 'none', border: '1px solid #1e293b', borderRadius: '8px', padding: '8px 20px', cursor: 'pointer' }}
            >
              Clear &amp; Regenerate Report
            </button>
          </div>

          {/* ── AI Chatbot ────────────────────────────────────────────────── */}
          <div className="no-print" style={{ marginTop: '32px', background: '#0d1829', border: '1px solid #1e293b', borderRadius: '14px', overflow: 'hidden' }}>
            <div style={{ padding: '16px 20px', borderBottom: '1px solid #1e293b', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span style={{ fontSize: '16px' }}>💬</span>
              <span style={{ fontSize: '13px', fontWeight: 700, color: '#94a3b8' }}>Ask the Analyst</span>
              <span style={{ fontSize: '11px', color: '#334155' }}>— ask anything about this {symbol} report</span>
            </div>

            {/* Messages */}
            <div style={{ minHeight: '80px', maxHeight: '420px', overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {chatMessages.length === 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {['What is the entry strategy?', 'Is the valuation attractive?', 'What are the biggest risks?', 'Should I buy today?'].map(q => (
                    <button key={q} onClick={() => setChatInput(q)}
                      style={{ fontSize: '11px', padding: '6px 12px', borderRadius: '6px', border: '1px solid #1e293b', background: 'rgba(129,140,248,0.08)', color: '#818cf8', cursor: 'pointer' }}>
                      {q}
                    </button>
                  ))}
                </div>
              )}
              {chatMessages.map((msg, i) => (
                <div key={i} style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start' }}>
                  <div style={{
                    maxWidth: '78%', padding: '10px 14px', borderRadius: msg.role === 'user' ? '12px 12px 4px 12px' : '12px 12px 12px 4px',
                    background: msg.role === 'user' ? 'rgba(99,102,241,0.2)' : 'rgba(255,255,255,0.04)',
                    border: msg.role === 'user' ? '1px solid rgba(99,102,241,0.3)' : '1px solid rgba(255,255,255,0.06)',
                    fontSize: '13px', color: '#e2e8f0', lineHeight: 1.6, whiteSpace: 'pre-wrap',
                  }}>
                    {msg.content}
                  </div>
                </div>
              ))}
              {chatLoading && (
                <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                  <div style={{ padding: '10px 14px', borderRadius: '12px 12px 12px 4px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)', fontSize: '13px', color: '#475569' }}>
                    Thinking…
                  </div>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            {/* Input */}
            <div style={{ padding: '12px 16px', borderTop: '1px solid #1e293b', display: 'flex', gap: '8px' }}>
              <input
                value={chatInput}
                onChange={e => setChatInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (!chatInput.trim() || chatLoading) return;
                    const userMsg = { role: 'user', content: chatInput.trim() };
                    const newMsgs = [...chatMessages, userMsg];
                    setChatMessages(newMsgs);
                    setChatInput('');
                    setChatLoading(true);
                    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
                    api.chatResearch(symbol, newMsgs, effectiveKey, model, provider)
                      .then(res => { setChatMessages(m => [...m, res]); })
                      .catch(err => { setChatMessages(m => [...m, { role: 'assistant', content: `Error: ${err.message}` }]); })
                      .finally(() => { setChatLoading(false); setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50); });
                  }
                }}
                placeholder={effectiveKey ? `Ask about ${symbol}…` : 'Configure an API key in Settings to chat'}
                disabled={!effectiveKey || chatLoading}
                style={{ flex: 1, padding: '10px 14px', borderRadius: '8px', border: '1px solid #1e293b', background: effectiveKey ? 'rgba(255,255,255,0.04)' : '#080f1e', color: '#f1f5f9', fontSize: '13px', outline: 'none', fontFamily: 'inherit' }}
              />
              <button
                disabled={!chatInput.trim() || !effectiveKey || chatLoading}
                onClick={() => {
                  if (!chatInput.trim() || chatLoading) return;
                  const userMsg = { role: 'user', content: chatInput.trim() };
                  const newMsgs = [...chatMessages, userMsg];
                  setChatMessages(newMsgs);
                  setChatInput('');
                  setChatLoading(true);
                  chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
                  api.chatResearch(symbol, newMsgs, effectiveKey, model, provider)
                    .then(res => { setChatMessages(m => [...m, res]); })
                    .catch(err => { setChatMessages(m => [...m, { role: 'assistant', content: `Error: ${err.message}` }]); })
                    .finally(() => { setChatLoading(false); setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50); });
                }}
                style={{ padding: '10px 20px', borderRadius: '8px', border: 'none', background: chatInput.trim() && effectiveKey && !chatLoading ? 'linear-gradient(135deg,#4ade80,#22c55e)' : '#1e293b', color: chatInput.trim() && effectiveKey && !chatLoading ? '#000' : '#475569', fontSize: '13px', fontWeight: 700, cursor: chatInput.trim() && effectiveKey && !chatLoading ? 'pointer' : 'default' }}
              >
                Send
              </button>
            </div>
          </div>
        </>
      )}

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        @media print {
          .no-print { display: none !important; }
          .research-tab-panel { display: block !important; margin-bottom: 28px; page-break-inside: avoid; }
        }
      `}</style>
    </div>
  );
}
