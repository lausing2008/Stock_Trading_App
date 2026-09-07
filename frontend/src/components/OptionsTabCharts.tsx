// OPTIONSTAB-B: three charts for the stock detail page's Options tab, each rendering data the
// app ALREADY fetches and types but currently shows only as a text list (or not at all):
//
//   GexOiByStrikeChart   — GammaExposure.oi_per_strike[], the CROSS-EXPIRY OI distribution.
//                          MarketPressurePanel renders only a top-N text list of it today.
//                          Deliberately distinct from OptionsChainChart, which is
//                          single-expiry off /options-chain — these complement, not duplicate.
//   MaxPainByExpiryChart — GammaExposure.max_pain[], an ARRAY the stock page reads only [0] of.
//   OiTermStructureChart — OptionsExpirationRow[], a text rollup today.
//
// Hand-rolled SVG, NOT lightweight-charts. This is the established convention for options
// charts here and the reasoning is documented at length in OptionsChainChart.tsx:1-18 and
// tracker T270: lightweight-charts is fundamentally a TIME-series library — every series'
// x-axis expects a real time value, with no first-class support for a categorical axis like
// "strike price". Forcing strikes through it means faking timestamps (fragile, and confusing
// on hover/zoom) or writing a full custom rendering primitive. The page's own volume histogram
// is the simpler precedent these follow.
//
// All data-shaping lives in lib/optionsTabCharts.ts so it's unit-testable without a
// component/DOM harness (this repo has none for page-level React) — same split as
// OptionsChainChart/lib/optionsChainChart.
import { useMemo } from 'react';
import type { GammaExposure, OptionsExpirationRow } from '@/lib/api';
import {
  aggregateGexOiByStrike, maxGexOi, hasNoRealGexOi,
  buildMaxPainSeries, maxPainRange,
  buildTermStructure, maxTermStructureOi,
  fmtCompact, fmtExpiryLabel, labelStepFor,
} from '@/lib/optionsTabCharts';

const W = 900;
const PAD_L = 52;
const PAD_R = 14;
const PAD_TOP = 10;
const PAD_BOTTOM = 24;

const CALL_COLOR = '#22c55e';
const PUT_COLOR = '#f87171';
const LINE_COLOR = '#818cf8';

function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 12, color: '#475569', padding: '8px 0' }}>{children}</div>;
}

function ChartTitle({ title, hint }: { title: string; hint?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
      <h3 style={{ fontSize: 13, fontWeight: 700, color: '#cbd5e1', margin: 0 }}>{title}</h3>
      {hint && <span style={{ fontSize: 11, color: '#475569' }}>{hint}</span>}
    </div>
  );
}

// ── 1. Cross-expiry OI distribution by strike (mirrored bars) ────────────────────────────

export function GexOiByStrikeChart({ gex, height = 220 }: { gex: GammaExposure | undefined; height?: number }) {
  const points = useMemo(() => aggregateGexOiByStrike(gex?.oi_per_strike), [gex?.oi_per_strike]);
  const maxOi = useMemo(() => maxGexOi(points), [points]);

  if (hasNoRealGexOi(points)) {
    return (
      <>
        <ChartTitle title="Open Interest by Strike (all expiries)" />
        <Empty>No cross-expiry open-interest data available for this symbol.</Empty>
      </>
    );
  }

  const H = height;
  const chartH = H - PAD_TOP - PAD_BOTTOM;
  const midY = PAD_TOP + chartH / 2;
  const chartW = W - PAD_L - PAD_R;
  const barSlot = chartW / points.length;
  const barW = Math.max(2, Math.min(18, barSlot * 0.6));
  const labelStep = labelStepFor(points.length);
  const yTicks = [1, 0.5];

  // Reference lines for the GEX walls, when present — these are the whole point of looking at
  // an OI distribution, so drawing them on the same axis is the useful part.
  const refs: { value: number; label: string; color: string }[] = [];
  if (gex?.call_wall != null) refs.push({ value: gex.call_wall, label: 'Call wall', color: CALL_COLOR });
  if (gex?.put_wall != null) refs.push({ value: gex.put_wall, label: 'Put wall', color: PUT_COLOR });
  if (gex?.gamma_flip != null) refs.push({ value: gex.gamma_flip, label: 'Gamma flip', color: '#f59e0b' });

  const minStrike = points[0].strike;
  const maxStrike = points[points.length - 1].strike;
  const xForStrike = (s: number) =>
    maxStrike === minStrike ? PAD_L + chartW / 2
      : PAD_L + ((s - minStrike) / (maxStrike - minStrike)) * chartW;

  return (
    <>
      <ChartTitle title="Open Interest by Strike (all expiries)" hint="calls above · puts below" />
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        <line x1={PAD_L} y1={midY} x2={W - PAD_R} y2={midY} stroke="#334155" strokeWidth={1} />
        {yTicks.map(frac => (
          <g key={`t-${frac}`}>
            <line x1={PAD_L} y1={midY - frac * (chartH / 2)} x2={W - PAD_R} y2={midY - frac * (chartH / 2)} stroke="#1e293b" strokeWidth={1} />
            <text x={PAD_L - 5} y={midY - frac * (chartH / 2) + 3} fill="#475569" fontSize={9} textAnchor="end">{fmtCompact(frac * maxOi)}</text>
            <line x1={PAD_L} y1={midY + frac * (chartH / 2)} x2={W - PAD_R} y2={midY + frac * (chartH / 2)} stroke="#1e293b" strokeWidth={1} />
            <text x={PAD_L - 5} y={midY + frac * (chartH / 2) + 3} fill="#475569" fontSize={9} textAnchor="end">{fmtCompact(frac * maxOi)}</text>
          </g>
        ))}
        {refs.map(r => {
          const x = xForStrike(r.value);
          if (x < PAD_L || x > W - PAD_R) return null;  // wall sits outside the plotted strike range
          return (
            <g key={r.label}>
              <line x1={x} y1={PAD_TOP} x2={x} y2={PAD_TOP + chartH} stroke={r.color} strokeWidth={1} strokeDasharray="3 3" opacity={0.75} />
              <text x={x + 3} y={PAD_TOP + 9} fill={r.color} fontSize={9}>{r.label}</text>
            </g>
          );
        })}
        {points.map((p, i) => {
          const bx = PAD_L + barSlot * i + barSlot / 2;
          const callH = (p.callOi / maxOi) * (chartH / 2);
          const putH = (p.putOi / maxOi) * (chartH / 2);
          return (
            <g key={p.strike}>
              <rect x={bx - barW / 2} y={midY - callH} width={barW} height={callH} fill={CALL_COLOR} opacity={0.75}>
                <title>{`$${p.strike} — call OI ${p.callOi.toLocaleString()}`}</title>
              </rect>
              <rect x={bx - barW / 2} y={midY} width={barW} height={putH} fill={PUT_COLOR} opacity={0.75}>
                <title>{`$${p.strike} — put OI ${p.putOi.toLocaleString()}`}</title>
              </rect>
              {i % labelStep === 0 && (
                <text x={bx} y={H - 8} fill="#475569" fontSize={9} textAnchor="middle">{p.strike}</text>
              )}
            </g>
          );
        })}
      </svg>
    </>
  );
}

// ── 2. Max pain across expiries (line) ───────────────────────────────────────────────────

export function MaxPainByExpiryChart({ gex, height = 190 }: { gex: GammaExposure | undefined; height?: number }) {
  const points = useMemo(() => buildMaxPainSeries(gex?.max_pain), [gex?.max_pain]);
  const range = useMemo(() => maxPainRange(points), [points]);

  if (points.length === 0 || !range) {
    return (
      <>
        <ChartTitle title="Max Pain by Expiry" />
        <Empty>No max-pain data available across expiries for this symbol.</Empty>
      </>
    );
  }
  if (points.length === 1) {
    // A single expiry can't draw a meaningful line — show the value plainly rather than a
    // one-point "trend" that implies a shape the data doesn't have.
    return (
      <>
        <ChartTitle title="Max Pain by Expiry" />
        <div style={{ fontSize: 12, color: '#94a3b8' }}>
          Only one expiry reported: <strong style={{ color: '#e2e8f0' }}>${points[0].maxPain}</strong>{' '}
          <span style={{ color: '#475569' }}>({fmtExpiryLabel(points[0].expiry)})</span>
        </div>
      </>
    );
  }

  const H = height;
  const chartH = H - PAD_TOP - PAD_BOTTOM;
  const chartW = W - PAD_L - PAD_R;
  const xAt = (i: number) => PAD_L + (points.length === 1 ? chartW / 2 : (i / (points.length - 1)) * chartW);
  const yAt = (v: number) => PAD_TOP + chartH - ((v - range.min) / (range.max - range.min)) * chartH;
  const labelStep = labelStepFor(points.length);
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xAt(i).toFixed(1)} ${yAt(p.maxPain).toFixed(1)}`).join(' ');

  return (
    <>
      <ChartTitle title="Max Pain by Expiry" hint="where option writers lose least at expiry" />
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        {[0, 0.5, 1].map(frac => {
          const y = PAD_TOP + chartH * frac;
          const val = range.max - (range.max - range.min) * frac;
          return (
            <g key={frac}>
              <line x1={PAD_L} y1={y} x2={W - PAD_R} y2={y} stroke="#1e293b" strokeWidth={1} />
              <text x={PAD_L - 5} y={y + 3} fill="#475569" fontSize={9} textAnchor="end">${val.toFixed(0)}</text>
            </g>
          );
        })}
        <path d={path} fill="none" stroke={LINE_COLOR} strokeWidth={1.75} />
        {points.map((p, i) => (
          <g key={p.expiry}>
            <circle cx={xAt(i)} cy={yAt(p.maxPain)} r={2.75} fill={LINE_COLOR}>
              <title>{`${p.expiry} — max pain $${p.maxPain}`}</title>
            </circle>
            {i % labelStep === 0 && (
              <text x={xAt(i)} y={H - 8} fill="#475569" fontSize={9} textAnchor="middle">{fmtExpiryLabel(p.expiry)}</text>
            )}
          </g>
        ))}
      </svg>
    </>
  );
}

// ── 3. OI term structure by expiry (stacked bars) ────────────────────────────────────────

const LEVEL_COLOR: Record<OptionsExpirationRow['level'], string> = {
  normal: '#475569',
  elevated: '#38bdf8',
  high: '#f59e0b',
  extreme: '#f87171',
};

export function OiTermStructureChart({
  expirations, height = 220, limit = 10,
}: { expirations: OptionsExpirationRow[] | undefined; height?: number; limit?: number }) {
  const points = useMemo(() => buildTermStructure(expirations, limit), [expirations, limit]);
  const maxOi = useMemo(() => maxTermStructureOi(points), [points]);

  if (points.length === 0) {
    return (
      <>
        <ChartTitle title="Open Interest Term Structure" />
        <Empty>No per-expiry open-interest data available for this symbol.</Empty>
      </>
    );
  }

  const H = height;
  const chartH = H - PAD_TOP - PAD_BOTTOM;
  const chartW = W - PAD_L - PAD_R;
  const barSlot = chartW / points.length;
  const barW = Math.max(6, Math.min(46, barSlot * 0.6));

  return (
    <>
      <ChartTitle
        title="Open Interest Term Structure"
        hint={`nearest ${points.length} ${points.length === 1 ? 'expiry' : 'expiries'} · calls + puts stacked`}
      />
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        {[0, 0.5, 1].map(frac => {
          const y = PAD_TOP + chartH * frac;
          return (
            <g key={frac}>
              <line x1={PAD_L} y1={y} x2={W - PAD_R} y2={y} stroke="#1e293b" strokeWidth={1} />
              <text x={PAD_L - 5} y={y + 3} fill="#475569" fontSize={9} textAnchor="end">
                {fmtCompact(maxOi * (1 - frac))}
              </text>
            </g>
          );
        })}
        {points.map((p, i) => {
          const bx = PAD_L + barSlot * i + barSlot / 2;
          const callH = (p.callOi / maxOi) * chartH;
          const putH = (p.putOi / maxOi) * chartH;
          const baseY = PAD_TOP + chartH;
          const ratioTxt = p.putCallOiRatio != null ? ` · P/C ${p.putCallOiRatio.toFixed(2)}` : '';
          return (
            <g key={p.expiry}>
              {/* puts on the bottom of the stack, calls above — matches the calls-above
                  convention the strike chart and Options Flow bar already use */}
              <rect x={bx - barW / 2} y={baseY - putH} width={barW} height={putH} fill={PUT_COLOR} opacity={0.75}>
                <title>{`${p.expiry} — put OI ${p.putOi.toLocaleString()}${ratioTxt}`}</title>
              </rect>
              <rect x={bx - barW / 2} y={baseY - putH - callH} width={barW} height={callH} fill={CALL_COLOR} opacity={0.75}>
                <title>{`${p.expiry} — call OI ${p.callOi.toLocaleString()}${ratioTxt}`}</title>
              </rect>
              {/* concentration level tick above the bar — the field exists and flags where OI
                  is unusually bunched, which is exactly what makes an expiry worth noticing */}
              {p.level !== 'normal' && (
                <circle cx={bx} cy={baseY - putH - callH - 6} r={2.5} fill={LEVEL_COLOR[p.level]}>
                  <title>{`${p.expiry} — ${p.level} concentration (${p.concentrationPct.toFixed(1)}%)`}</title>
                </circle>
              )}
              <text x={bx} y={H - 8} fill="#475569" fontSize={9} textAnchor="middle">{fmtExpiryLabel(p.expiry)}</text>
            </g>
          );
        })}
      </svg>
    </>
  );
}
