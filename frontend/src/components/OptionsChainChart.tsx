// T270-STOCKDETAIL-CALLPUT-CHART: a real call/put OI-by-strike bar chart. Neither existing
// options UI on the stock detail page is an actual chart — Options Flow is a stacked bar +
// top-10 text table, Options Chain (below, on the same page) is a broker-style strike-matrix
// table. GET /options-chain already returns calls[]/puts[] with strike/oi per row; this
// component just visualizes that already-fetched data, no new fetch of its own.
//
// Deliberately hand-rolled SVG rather than lightweight-charts (the library the tracker item's
// own research note suggested, since it's already used extensively elsewhere on this page):
// lightweight-charts is fundamentally a TIME-series library — every series' x-axis expects a
// real time value, and there is no first-class support for an arbitrary categorical axis like
// "strike price". Forcing strikes through it would mean either faking timestamps (fragile,
// and confusing for anyone hovering/zooming and seeing a fake date) or writing a full custom
// rendering primitive (the mechanism this page's own VolumeProfilePrimitive uses for its
// by-price-level histogram) — real overkill for a single mirrored bar chart. This page already
// has an established, simpler precedent for exactly this shape: the Volume section a few
// hundred lines below (dailyBars histogram, hand-rolled SVG with gridlines/labels) — this
// component follows that same convention instead.
import { useMemo } from 'react';
import type { OptionsChainRow } from '@/lib/api';
import { aggregateOiByStrike, maxOiAcrossStrikes, fmtOi, labelStepFor } from '@/lib/optionsChainChart';

interface OptionsChainChartProps {
  calls: OptionsChainRow[];
  puts: OptionsChainRow[];
  height?: number;
}

const W = 900;
const PAD_L = 46;
const PAD_R = 12;
const PAD_TOP = 8;
const PAD_BOTTOM = 22;

export default function OptionsChainChart({ calls, puts, height = 220 }: OptionsChainChartProps) {
  const H = height;
  const chartH = H - PAD_TOP - PAD_BOTTOM;
  const midY = PAD_TOP + chartH / 2;

  const points = useMemo(() => aggregateOiByStrike(calls, puts), [calls, puts]);
  const maxOi = useMemo(() => maxOiAcrossStrikes(points), [points]);
  const labelStep = labelStepFor(points.length);

  if (points.length === 0) {
    return <div style={{ fontSize: 12, color: '#475569' }}>No open interest to chart for this expiry.</div>;
  }

  const chartW = W - PAD_L - PAD_R;
  const barSlot = chartW / points.length;
  const barW = Math.max(2, Math.min(18, barSlot * 0.6));
  const yTicks = [1, 0.5]; // fraction of maxOi, mirrored above and below the zero line (0 omitted — the zero line itself already marks it)

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      {/* Zero line (calls above, puts below) */}
      <line x1={PAD_L} y1={midY} x2={W - PAD_R} y2={midY} stroke="#334155" strokeWidth={1} />
      {/* Y-axis OI gridlines/labels, mirrored above and below zero */}
      {yTicks.map(frac => (
        <g key={`up-${frac}`}>
          <line x1={PAD_L} y1={midY - frac * (chartH / 2)} x2={W - PAD_R} y2={midY - frac * (chartH / 2)} stroke="#1e293b" strokeWidth={1} />
          <text x={PAD_L - 4} y={midY - frac * (chartH / 2) + 3} fill="#475569" fontSize={9} textAnchor="end">{fmtOi(frac * maxOi)}</text>
        </g>
      ))}
      {yTicks.map(frac => (
        <g key={`down-${frac}`}>
          <line x1={PAD_L} y1={midY + frac * (chartH / 2)} x2={W - PAD_R} y2={midY + frac * (chartH / 2)} stroke="#1e293b" strokeWidth={1} />
          <text x={PAD_L - 4} y={midY + frac * (chartH / 2) + 3} fill="#475569" fontSize={9} textAnchor="end">{fmtOi(frac * maxOi)}</text>
        </g>
      ))}
      {/* Bars */}
      {points.map((p, i) => {
        const bx = PAD_L + barSlot * i + barSlot / 2;
        const callH = (p.callOi / maxOi) * (chartH / 2);
        const putH = (p.putOi / maxOi) * (chartH / 2);
        return (
          <g key={p.strike}>
            {p.callOi > 0 && (
              <rect x={bx - barW / 2} y={midY - callH} width={barW} height={callH} fill="#4ade80" opacity={0.85}>
                <title>{`Strike $${p.strike} — ${p.callOi.toLocaleString()} call OI`}</title>
              </rect>
            )}
            {p.putOi > 0 && (
              <rect x={bx - barW / 2} y={midY} width={barW} height={putH} fill="#f87171" opacity={0.85}>
                <title>{`Strike $${p.strike} — ${p.putOi.toLocaleString()} put OI`}</title>
              </rect>
            )}
          </g>
        );
      })}
      {/* X-axis strike labels */}
      {points.map((p, i) => {
        if (i % labelStep !== 0) return null;
        const bx = PAD_L + barSlot * i + barSlot / 2;
        return (
          <text key={p.strike} x={bx} y={H - 6} fill="#475569" fontSize={9} textAnchor="middle">
            ${p.strike}
          </text>
        );
      })}
    </svg>
  );
}
