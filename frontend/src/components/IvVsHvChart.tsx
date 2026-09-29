// T411-IVHV: implied volatility against realized volatility, on one axis.
//
// Hand-rolled SVG rather than lightweight-charts, following OptionsChainChart.tsx's own
// precedent on this page: this is a two-line overlay with gaps in both series, and the value is
// entirely in where one line sits relative to the other. lightweight-charts would bring pane
// lifecycle management inside a conditionally-rendered tab for no gain here.
//
// The gaps are the reason the geometry lives in lib/ivHvChart.ts and is tested there. Both
// series legitimately have holes — HV emits nothing until a full 20-session window exists, and
// UW can be missing a day — and a line drawn straight through a hole invents a reading.
import { useMemo } from 'react';
import type { IvHvResponse } from '@/lib/api';
import { segments, volBounds, fmtVol, fmtSpread, spreadRead, dateTicks, shortDate } from '@/lib/ivHvChart';

const W = 900;
const H = 240;
const PAD_L = 46;
const PAD_R = 14;
const PAD_TOP = 10;
const PAD_BOTTOM = 26;

const IV_COLOR = '#f59e0b';
const HV_COLOR = '#38bdf8';

const TONE_COLOR: Record<string, string> = {
  rich: '#f59e0b',
  cheap: '#4ade80',
  fair: '#94a3b8',
  unknown: '#64748b',
};

interface Props {
  data: IvHvResponse;
  days: number;
  onDaysChange: (d: number) => void;
}

const RANGES = [
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
];

export default function IvVsHvChart({ data, days, onDaysChange }: Props) {
  const points = useMemo(() => data.points ?? [], [data.points]);
  const bounds = useMemo(() => volBounds(points), [points]);
  const ivRuns = useMemo(() => segments(points, 'iv'), [points]);
  const hvRuns = useMemo(() => segments(points, 'hv'), [points]);
  const ticks = useMemo(() => dateTicks(points, 6), [points]);

  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_TOP - PAD_BOTTOM;

  const x = (i: number) => PAD_L + (points.length <= 1 ? chartW / 2 : (i / (points.length - 1)) * chartW);
  const y = (v: number) => PAD_TOP + chartH - ((v - bounds.min) / (bounds.max - bounds.min)) * chartH;

  const pathOf = (runs: { i: number; value: number }[][]) =>
    runs.map(run => run.map((p, k) => `${k === 0 ? 'M' : 'L'}${x(p.i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ')).join(' ');

  const read = spreadRead(data.iv_minus_hv);
  const gridVals = [0, 0.25, 0.5, 0.75, 1].map(f => bounds.min + f * (bounds.max - bounds.min));

  const rangePicker = (
    <div style={{ display: 'flex', gap: 4 }}>
      {RANGES.map(r => (
        <button
          key={r.label}
          onClick={() => onDaysChange(r.days)}
          style={{
            fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 5, cursor: 'pointer',
            background: days === r.days ? 'rgba(56,189,248,0.15)' : 'transparent',
            border: `1px solid ${days === r.days ? 'rgba(56,189,248,0.4)' : '#1e293b'}`,
            color: days === r.days ? '#38bdf8' : '#64748b',
          }}
        >
          {r.label}
        </button>
      ))}
    </div>
  );

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, color: '#cbd5e1', margin: 0 }}>Implied vs Realized Volatility</h2>
        {data.iv_minus_hv != null && (
          <span
            title="Implied volatility minus realized volatility, at the most recent day where both were measured. A difference of two annualized rates, so the unit is percentage points."
            style={{
              fontSize: 11, fontWeight: 700, cursor: 'help', borderRadius: 5, padding: '2px 8px',
              color: TONE_COLOR[read.tone],
              background: `${TONE_COLOR[read.tone]}1f`,
              border: `1px solid ${TONE_COLOR[read.tone]}4d`,
            }}
          >
            IV − HV {fmtSpread(data.iv_minus_hv)}
          </span>
        )}
        <span style={{ fontSize: 11, color: '#64748b' }}>{read.label}</span>
        <div style={{ marginLeft: 'auto' }}>{rangePicker}</div>
      </div>

      <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 10 }}>
          <Legend color={IV_COLOR} label="Implied (IV)" value={fmtVol(data.latest?.iv)} />
          <Legend color={HV_COLOR} label={`Realized (HV, ${data.hv_window_sessions ?? 20}d)`} value={fmtVol(data.latest?.hv)} />
          {data.latest?.iv_rank_1y != null && (
            <Legend color="#a78bfa" label="IV Rank (1y)" value={`${data.latest.iv_rank_1y.toFixed(0)}/100`} />
          )}
        </div>

        <div style={{ overflowX: 'auto' }}>
          <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" role="img"
               aria-label={`Implied versus realized volatility for ${data.symbol}`}>
            {gridVals.map((v, i) => (
              <g key={i}>
                <line x1={PAD_L} x2={W - PAD_R} y1={y(v)} y2={y(v)} stroke="#1e293b" strokeWidth={1} />
                <text x={PAD_L - 6} y={y(v) + 3} textAnchor="end" fontSize={10} fill="#64748b">{fmtVol(v, 0)}</text>
              </g>
            ))}
            {ticks.map(i => (
              <text key={i} x={x(i)} y={H - 8} textAnchor="middle" fontSize={10} fill="#64748b">
                {shortDate(points[i].date)}
              </text>
            ))}
            <path d={pathOf(hvRuns)} fill="none" stroke={HV_COLOR} strokeWidth={1.8} strokeLinejoin="round" />
            <path d={pathOf(ivRuns)} fill="none" stroke={IV_COLOR} strokeWidth={1.8} strokeLinejoin="round" />
          </svg>
        </div>

        {(data.hv_points ?? 0) === 0 && (
          <p style={{ fontSize: 11, color: '#f59e0b', margin: '10px 0 0', lineHeight: 1.5 }}>
            <strong>Realized volatility unavailable.</strong> This platform has no stored daily
            price history for {data.symbol}, so there is nothing to measure actual movement
            against — only the implied line is shown. The comparison this panel exists for is
            not available for this symbol.
          </p>
        )}
        {(data.iv_points ?? 0) === 0 && (
          <p style={{ fontSize: 11, color: '#f59e0b', margin: '10px 0 0', lineHeight: 1.5 }}>
            <strong>Implied volatility unavailable.</strong> Unusual Whales returned no IV
            history for {data.symbol} — it has no coverage outside US-listed names. Only the
            realized line is shown.
          </p>
        )}
        <p style={{ fontSize: 11, color: '#64748b', margin: '10px 0 0', lineHeight: 1.5 }}>
          <strong style={{ color: '#94a3b8' }}>How to read it.</strong>{' '}
          The amber line is what the options market charged for future movement; the blue line is
          the movement that actually happened, measured over a trailing{' '}
          {data.hv_window_sessions ?? 20}-session window. Amber persistently above blue means
          premium has been expensive relative to what followed — the case for selling it rather
          than buying it. Neither line is a signal on its own, and a gap can persist for months
          without closing. Breaks in a line are days with no reading, not flat days.
        </p>
      </div>
    </div>
  );
}

function Legend({ color, label, value }: { color: string; label: string; value: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <span style={{ width: 14, height: 3, background: color, borderRadius: 2, display: 'inline-block' }} />
      <span style={{ fontSize: 11, color: '#64748b' }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0' }}>{value}</span>
    </div>
  );
}
