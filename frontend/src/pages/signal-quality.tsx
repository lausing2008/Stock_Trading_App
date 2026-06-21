/**
 * Signal Quality / Calibration page (/signal-quality)
 *
 * Shows how well-calibrated the AI signal confidence scores are.
 * A well-calibrated model: when it says 70% confidence, it should be right ~70% of the time.
 *
 * Data source: GET /signals/outcomes/calibration?days=N
 *
 * Reading the reliability diagram
 * ────────────────────────────────
 * X axis = expected win rate (midpoint of confidence band, e.g. 65–70% band → 67.5%)
 * Y axis = actual win rate observed for signals in that band
 * Diagonal = perfect calibration
 * Points above diagonal = model is UNDER-confident (actual beats expectation) — good
 * Points below diagonal = model is OVER-confident (actual lags expectation) — recalibrate
 *
 * Confidence bands
 * ─────────────────
 * Signals are grouped into 5-point confidence buckets (50–55%, 55–60%, …, 95–100%).
 * Each row shows: how many signals fell in that band, actual win rate, avg return,
 * and the calibration gap (actual − expected). A negative gap means overconfident.
 */
import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type CalibrationData, type CalibrationHorizon, type CalibrationBand } from '@/lib/api';
import { getSession } from '@/lib/auth';

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtPct(n: number | null | undefined, digits = 1): string {
  if (n == null) return '—';
  return `${n.toFixed(digits)}%`;
}

function fmtReturn(n: number | null | undefined): string {
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
}

function gapColor(gap: number): string {
  if (gap >= 5) return '#4ade80';
  if (gap >= 0) return '#86efac';
  if (gap >= -5) return '#fbbf24';
  return '#f87171';
}

function gapLabel(gap: number): string {
  if (gap >= 10) return 'Under-confident';
  if (gap >= 3) return 'Slightly under';
  if (gap >= -3) return 'Well calibrated';
  if (gap >= -8) return 'Slightly over';
  return 'Over-confident';
}

function horizonColor(h: string): string {
  switch (h) {
    case 'SHORT': return '#818cf8';
    case 'SWING': return '#4f46e5';
    case 'LONG': return '#4ade80';
    case 'GROWTH': return '#facc15';
    default: return '#94a3b8';
  }
}

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{
      background: '#1e293b',
      border: '1px solid #334155',
      borderRadius: 10,
      padding: '16px 20px',
      flex: '1 1 140px',
      minWidth: 120,
    }}>
      <div style={{ fontSize: 11, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color: color ?? '#f1f5f9', lineHeight: 1 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: '#475569', marginTop: 5 }}>{sub}</div>}
    </div>
  );
}

// ── Reliability Diagram ────────────────────────────────────────────────────────

function ReliabilityDiagram({ bands }: { bands: CalibrationBand[] }) {
  // Only bands with at least some data
  const pts = bands.filter(b => b.count > 0);
  if (pts.length === 0) {
    return (
      <div style={{ color: '#475569', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
        No bands with enough data to plot
      </div>
    );
  }

  const W = 200;
  const H = 200;
  const pad = { top: 10, right: 10, bottom: 32, left: 36 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  // X: expected win rate 50–100 (midpoint of each band)
  // Y: actual win rate 0–100
  function xPos(midpoint: number): number {
    return pad.left + ((midpoint - 50) / 50) * plotW;
  }
  function yPos(winRate: number): number {
    return pad.top + plotH - (winRate / 100) * plotH;
  }

  // Grid lines
  const gridX = [50, 60, 70, 80, 90, 100];
  const gridY = [0, 25, 50, 75, 100];

  return (
    <svg
      width={W}
      height={H}
      style={{ display: 'block', maxWidth: '100%' }}
      aria-label="Reliability diagram: expected vs actual win rate"
    >
      {/* Grid lines Y */}
      {gridY.map(v => (
        <line
          key={`gy-${v}`}
          x1={pad.left} y1={yPos(v)}
          x2={pad.left + plotW} y2={yPos(v)}
          stroke="#1e293b" strokeWidth="1"
        />
      ))}
      {/* Grid lines X */}
      {gridX.map(v => (
        <line
          key={`gx-${v}`}
          x1={xPos(v)} y1={pad.top}
          x2={xPos(v)} y2={pad.top + plotH}
          stroke="#1e293b" strokeWidth="1"
        />
      ))}

      {/* Axis borders */}
      <line x1={pad.left} y1={pad.top} x2={pad.left} y2={pad.top + plotH} stroke="#334155" strokeWidth="1" />
      <line x1={pad.left} y1={pad.top + plotH} x2={pad.left + plotW} y2={pad.top + plotH} stroke="#334155" strokeWidth="1" />

      {/* Perfect calibration diagonal */}
      <line
        x1={xPos(50)} y1={yPos(50)}
        x2={xPos(100)} y2={yPos(100)}
        stroke="#475569" strokeWidth="1.5" strokeDasharray="5,4"
      />

      {/* Points */}
      {pts.map((b, i) => {
        const cx = xPos(b.midpoint);
        const cy = yPos(b.win_rate_pct);
        const color = gapColor(b.calibration_gap);
        // Radius proportional to count, capped
        const r = Math.max(4, Math.min(10, 4 + Math.sqrt(b.count) * 0.7));
        return (
          <g key={i}>
            <circle cx={cx} cy={cy} r={r} fill={color} fillOpacity={0.85} stroke="#0f172a" strokeWidth="1" />
          </g>
        );
      })}

      {/* Y axis labels */}
      {gridY.map(v => (
        <text
          key={`yl-${v}`}
          x={pad.left - 4} y={yPos(v) + 4}
          textAnchor="end" fill="#475569"
          fontSize="9" fontFamily="system-ui,sans-serif"
        >
          {v}%
        </text>
      ))}

      {/* X axis labels */}
      {[50, 70, 90].map(v => (
        <text
          key={`xl-${v}`}
          x={xPos(v)} y={H - pad.bottom + 14}
          textAnchor="middle" fill="#475569"
          fontSize="9" fontFamily="system-ui,sans-serif"
        >
          {v}%
        </text>
      ))}

      {/* Axis titles */}
      <text x={pad.left + plotW / 2} y={H - 2} textAnchor="middle" fill="#64748b" fontSize="9" fontFamily="system-ui,sans-serif">
        Expected (confidence midpoint)
      </text>
      <text
        x={9} y={pad.top + plotH / 2}
        textAnchor="middle" fill="#64748b" fontSize="9" fontFamily="system-ui,sans-serif"
        transform={`rotate(-90, 9, ${pad.top + plotH / 2})`}
      >
        Actual win rate
      </text>
    </svg>
  );
}

// ── Band Table ─────────────────────────────────────────────────────────────────

function BandTable({ bands }: { bands: CalibrationBand[] }) {
  if (!bands.length) {
    return <div style={{ color: '#475569', fontSize: 12 }}>No band data available.</div>;
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid #334155' }}>
            {['Confidence Band', 'Signals', 'Win Rate', 'Avg Return', 'Gap', 'Assessment'].map(h => (
              <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: '#64748b', fontWeight: 600, whiteSpace: 'nowrap' }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {bands.map((b, i) => {
            const color = gapColor(b.calibration_gap);
            return (
              <tr key={i} style={{ borderBottom: '1px solid #1e293b' }}>
                <td style={{ padding: '7px 10px', color: '#e2e8f0', fontWeight: 600, fontFamily: 'monospace' }}>
                  {b.band}
                </td>
                <td style={{ padding: '7px 10px', color: b.count >= 20 ? '#94a3b8' : '#64748b' }}>
                  {b.count}
                  {b.count < 10 && <span style={{ color: '#475569', fontSize: 10, marginLeft: 4 }}>(low)</span>}
                </td>
                <td style={{ padding: '7px 10px', color: b.win_rate_pct >= 60 ? '#4ade80' : b.win_rate_pct >= 50 ? '#facc15' : '#f87171', fontWeight: 600 }}>
                  {fmtPct(b.win_rate_pct)}
                </td>
                <td style={{ padding: '7px 10px', color: b.avg_return_pct == null ? '#475569' : b.avg_return_pct >= 0 ? '#4ade80' : '#f87171' }}>
                  {fmtReturn(b.avg_return_pct)}
                </td>
                <td style={{ padding: '7px 10px' }}>
                  <span style={{
                    display: 'inline-block',
                    padding: '2px 7px',
                    borderRadius: 4,
                    background: `${color}22`,
                    color,
                    fontWeight: 700,
                    fontSize: 11,
                  }}>
                    {b.calibration_gap >= 0 ? '+' : ''}{b.calibration_gap.toFixed(1)}pp
                  </span>
                </td>
                <td style={{ padding: '7px 10px', color: '#94a3b8', fontSize: 11 }}>
                  {gapLabel(b.calibration_gap)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Horizon Section ────────────────────────────────────────────────────────────

function HorizonSection({ h }: { h: CalibrationHorizon }) {
  const acColor = horizonColor(h.horizon);
  const wrColor = h.win_rate_pct >= 60 ? '#4ade80' : h.win_rate_pct >= 50 ? '#facc15' : '#f87171';

  return (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 12, padding: '20px 24px' }}>
      {/* Horizon header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <span style={{
          display: 'inline-block',
          padding: '3px 10px',
          borderRadius: 5,
          background: `${acColor}22`,
          color: acColor,
          fontWeight: 700,
          fontSize: 12,
          letterSpacing: '0.07em',
        }}>
          {h.horizon}
        </span>
        <span style={{ color: '#475569', fontSize: 12 }}>{h.total} evaluated signals</span>
      </div>

      {/* Stats row */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
        <StatCard label="Win Rate" value={fmtPct(h.win_rate_pct)} color={wrColor} />
        <StatCard label="Avg Return" value={fmtReturn(h.avg_return_pct)} color={h.avg_return_pct == null ? undefined : h.avg_return_pct >= 0 ? '#4ade80' : '#f87171'} />
        <StatCard
          label="Suggested Min Confidence"
          value={h.suggested_min_confidence != null ? `${h.suggested_min_confidence}%` : '—'}
          sub={h.suggested_min_confidence != null ? 'Filter below this to improve precision' : 'Not enough data'}
          color="#facc15"
        />
      </div>

      {/* Diagram + table */}
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {/* Reliability diagram */}
        <div style={{ flexShrink: 0 }}>
          <div style={{ fontSize: 11, color: '#64748b', marginBottom: 8, fontWeight: 600 }}>
            Reliability Diagram
          </div>
          <div style={{ background: '#0a1120', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 8px' }}>
            <ReliabilityDiagram bands={h.bands} />
          </div>
          <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: '#475569' }}>
              <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: '#4ade80' }} />
              Above diagonal = under-confident (good)
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: '#475569' }}>
              <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: '#f87171' }} />
              Below diagonal = over-confident
            </div>
            <div style={{ fontSize: 10, color: '#475569', marginTop: 2 }}>
              Point size = relative sample count
            </div>
          </div>
        </div>

        {/* Band table */}
        <div style={{ flex: 1, minWidth: 280 }}>
          <div style={{ fontSize: 11, color: '#64748b', marginBottom: 8, fontWeight: 600 }}>
            Confidence Bands
          </div>
          <BandTable bands={h.bands} />
        </div>
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────

const DAYS_OPTIONS = [90, 180, 365] as const;
type DaysOption = typeof DAYS_OPTIONS[number];

const HORIZONS = ['SHORT', 'SWING', 'LONG', 'GROWTH'] as const;
type HorizonTab = typeof HORIZONS[number] | 'ALL';

export default function SignalQualityPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const s = getSession();
    if (!s) { router.replace('/login'); return; }
    setAuthed(true);
  }, [router]);

  const [days, setDays] = useState<DaysOption>(180);
  const [activeHorizon, setActiveHorizon] = useState<HorizonTab>('SWING');

  const { data, isLoading, error } = useSWR<CalibrationData>(
    authed ? ['signal-quality-calibration', days] : null,
    () => api.outcomesCalibration(days),
    { revalidateOnFocus: false },
  );

  if (!authed) return null;

  const noData = !isLoading && !error && data != null && data.total === 0;
  const hasData = data != null && data.total > 0;

  const shownHorizons: CalibrationHorizon[] = hasData
    ? (activeHorizon === 'ALL' ? data.horizons : data.horizons.filter(h => h.horizon === activeHorizon))
    : [];

  return (
    <div style={{
      minHeight: '100vh',
      background: '#0f172a',
      color: '#f1f5f9',
      fontFamily: 'system-ui, -apple-system, sans-serif',
      padding: '24px 20px 60px',
    }}>
      <div style={{ maxWidth: 960, margin: '0 auto' }}>

        {/* ── Page header ─────────────────────────────────────── */}
        <div style={{ marginBottom: 28 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <Link href="/signal-accuracy" style={{ color: '#475569', textDecoration: 'none', fontSize: 13 }}>
              Signal Accuracy
            </Link>
            <span style={{ color: '#334155' }}>›</span>
            <span style={{ color: '#94a3b8', fontSize: 13 }}>Signal Quality</span>
          </div>
          <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, color: '#f1f5f9' }}>
            Signal Calibration
          </h1>
          <p style={{ color: '#64748b', fontSize: 14, margin: '8px 0 0', maxWidth: 640, lineHeight: 1.6 }}>
            A well-calibrated model means the confidence score matches actual outcomes: when the AI assigns
            70% confidence, it should win roughly 70% of the time. Points{' '}
            <span style={{ color: '#4ade80' }}>above the diagonal</span> indicate the model is more accurate
            than it claims (under-confident — good). Points{' '}
            <span style={{ color: '#f87171' }}>below the diagonal</span> mean the model is claiming more
            certainty than it delivers (over-confident — needs recalibration).
          </p>
        </div>

        {/* ── Controls ─────────────────────────────────────────── */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginBottom: 24 }}>
          <div style={{ fontSize: 12, color: '#64748b', fontWeight: 600 }}>Lookback:</div>
          {DAYS_OPTIONS.map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              style={{
                background: days === d ? '#4f46e5' : '#1e293b',
                color: days === d ? '#fff' : '#94a3b8',
                border: `1px solid ${days === d ? '#4f46e5' : '#334155'}`,
                borderRadius: 6,
                padding: '5px 14px',
                fontSize: 13,
                cursor: 'pointer',
                fontWeight: days === d ? 600 : 400,
              }}
            >
              {d}d
            </button>
          ))}
        </div>

        {/* ── Loading state ─────────────────────────────────────── */}
        {isLoading && (
          <div style={{ textAlign: 'center', padding: '80px 20px', color: '#475569', fontSize: 15 }}>
            Loading calibration data...
          </div>
        )}

        {/* ── Error state ──────────────────────────────────────── */}
        {error && (
          <div style={{
            background: '#1e293b',
            border: '1px solid #dc2626',
            borderRadius: 10,
            padding: '20px 24px',
            color: '#fca5a5',
            fontSize: 14,
          }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>Failed to load calibration data</div>
            <div style={{ color: '#94a3b8', fontSize: 12 }}>
              {error?.message ?? 'Unknown error'}. Check that the signal engine is running and outcomes have been evaluated.
            </div>
          </div>
        )}

        {/* ── No data state ─────────────────────────────────────── */}
        {noData && (
          <div style={{
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: 12,
            padding: '40px 32px',
            textAlign: 'center',
            color: '#64748b',
          }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>📊</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: '#94a3b8', marginBottom: 8 }}>
              Not enough evaluated signals yet
            </div>
            <div style={{ fontSize: 14, maxWidth: 400, margin: '0 auto', lineHeight: 1.6 }}>
              Check back after more signals have been evaluated. Outcomes are evaluated daily —
              signals need at least 5–20 trading days to settle before they can be assessed.
            </div>
          </div>
        )}

        {/* ── Data ─────────────────────────────────────────────── */}
        {hasData && (
          <>
            {/* Overall stats */}
            <div style={{
              background: '#1e293b',
              border: '1px solid #334155',
              borderRadius: 12,
              padding: '18px 22px',
              marginBottom: 24,
            }}>
              <div style={{ fontSize: 11, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 12 }}>
                Overall — {data.days}d lookback
              </div>
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <StatCard
                  label="Total Evaluated"
                  value={data.total.toLocaleString()}
                  sub="BUY signals assessed"
                />
                <StatCard
                  label="Global Win Rate"
                  value={fmtPct(data.overall.win_rate_pct)}
                  color={data.overall.win_rate_pct >= 60 ? '#4ade80' : data.overall.win_rate_pct >= 50 ? '#facc15' : '#f87171'}
                  sub={data.overall.win_rate_pct >= 60 ? 'Above random baseline' : data.overall.win_rate_pct >= 50 ? 'Near baseline' : 'Below baseline'}
                />
                <StatCard
                  label="Avg Return"
                  value={fmtReturn(data.overall.avg_return_pct)}
                  color={data.overall.avg_return_pct == null ? undefined : data.overall.avg_return_pct >= 0 ? '#4ade80' : '#f87171'}
                />
              </div>
            </div>

            {/* Horizon tabs */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 20 }}>
              {(['ALL', ...HORIZONS] as HorizonTab[]).map(h => {
                const active = activeHorizon === h;
                const col = h === 'ALL' ? '#94a3b8' : horizonColor(h);
                return (
                  <button
                    key={h}
                    onClick={() => setActiveHorizon(h)}
                    style={{
                      background: active ? `${col}22` : '#1e293b',
                      color: active ? col : '#64748b',
                      border: `1px solid ${active ? col : '#334155'}`,
                      borderRadius: 7,
                      padding: '7px 16px',
                      fontSize: 12,
                      fontWeight: active ? 700 : 400,
                      cursor: 'pointer',
                      letterSpacing: '0.05em',
                    }}
                  >
                    {h}
                    {h !== 'ALL' && (() => {
                      const hd = data.horizons.find(x => x.horizon === h);
                      return hd ? (
                        <span style={{ marginLeft: 6, opacity: 0.7, fontSize: 10 }}>
                          {hd.total}
                        </span>
                      ) : null;
                    })()}
                  </button>
                );
              })}
            </div>

            {/* Horizon sections */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              {shownHorizons.length === 0 && (
                <div style={{ color: '#475569', fontSize: 13, padding: '24px 0', textAlign: 'center' }}>
                  No data for this horizon in the selected window.
                </div>
              )}
              {shownHorizons.map(h => (
                <HorizonSection key={h.horizon} h={h} />
              ))}
            </div>

            {/* Calibration guide */}
            <div style={{
              background: '#1e293b',
              border: '1px solid #334155',
              borderRadius: 12,
              padding: '18px 22px',
              marginTop: 28,
            }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 12 }}>
                How to use calibration data
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {[
                  ['Suggested min confidence', 'The lowest confidence band where win rate > 50% with at least 10 signals. Use this as a minimum filter in Signal Filter to cut low-quality entries.'],
                  ['Calibration gap', 'Actual win rate minus expected win rate (midpoint of band). Positive = model is cautious and delivers better than it claims. Negative = model overpromises.'],
                  ['Point size on diagram', 'Larger circles = more signals in that band. Small circles may not be statistically significant — treat them with caution.'],
                  ['When to act', 'If multiple horizons show consistent negative gaps (below the diagonal), consider raising thresholds or re-running ML tuning (POST /ml/tune_all).'],
                ].map(([term, desc]) => (
                  <div key={term} style={{ display: 'flex', gap: 12 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', minWidth: 180, flexShrink: 0 }}>{term}</div>
                    <div style={{ fontSize: 12, color: '#64748b', lineHeight: 1.5 }}>{desc}</div>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
