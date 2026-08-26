import useSWR from 'swr';
import { api, type EarningsForecast } from '@/lib/api';

// AUD-EARNINGSFORECAST: renders the on-demand PRE-report LLM forecast (watching_for + a fixed
// 3-row scenario table + an optional bellwether note) alongside real, already-available
// consensus/beat-rate context — this content is shared between the earnings-calendar modal
// (EarningsForecastModal below) and a dedicated section on the stock detail page, so a fix to
// one place's rendering can't silently drift from the other.
//
// generate_earnings_forecast() always returns a real response with `forecast: null` when the
// admin feature flag is off or the LLM call itself fails — this component renders whatever
// consensus context it DOES have regardless, and simply omits the LLM section when null. This
// matches the backend's own explicit design choice (see routes.py's get_earnings_forecast()
// docstring): never let an optional, cost-gated LLM feature block a real data display.

function fmtMoney(v: number | null | undefined) {
  if (v == null) return '—';
  return `$${v.toFixed(2)}`;
}
function fmtPct(v: number | null | undefined) {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
}
// eps_avg_surprise_pct is stored already-scaled to a percent, matching earnings.tsx's own
// fmtSurprise() convention exactly (see that file's comment for why fmtPct() would 100x it).
function fmtSurprise(v: number | null | undefined) {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
}

const SCENARIO_COLOR: Record<string, { border: string; bg: string; dot: string }> = {
  'Beat + Raise': { border: 'rgba(74,222,128,0.3)', bg: 'rgba(74,222,128,0.06)', dot: '#4ade80' },
  'In-Line': { border: 'rgba(148,163,184,0.25)', bg: 'rgba(148,163,184,0.05)', dot: '#94a3b8' },
  'Miss or Cut': { border: 'rgba(248,113,113,0.3)', bg: 'rgba(248,113,113,0.06)', dot: '#f87171' },
};

export default function EarningsForecastPanel({
  symbol, sector, daysToEvent,
  eventsData,
}: {
  symbol: string;
  sector?: string | null;
  daysToEvent: number;
  // Optional: an already-fetched calendar row's own consensus fields (from eventsCalendar()),
  // shown even before/without the LLM forecast — avoids a second network round-trip for data
  // the caller may already have on hand (e.g. the calendar page's own EventCard).
  eventsData?: {
    analyst_price_target_mean?: number | null;
    analyst_price_target_weighted?: number | null;
    analyst_n_firms?: number | null;
    eps_beat_rate?: number | null;
    eps_avg_surprise_pct?: number | null;
    eps_estimate?: number | null;
  } | null;
}) {
  const { data, error, isLoading } = useSWR(
    `earnings-forecast-${symbol}`,
    () => api.eventsEarningsForecast(symbol, sector ?? null, daysToEvent),
    { revalidateOnFocus: false },
  );
  const forecast: EarningsForecast | null = data?.forecast ?? null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Real, already-known context — always shown regardless of LLM availability */}
      {eventsData && (eventsData.analyst_price_target_mean != null || eventsData.eps_beat_rate != null) && (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', padding: '10px 12px', borderRadius: 8, background: 'rgba(148,163,184,0.05)', border: '1px solid #1e293b' }}>
          {eventsData.analyst_price_target_mean != null && (
            <div>
              <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Analyst Price Target</div>
              <div style={{ fontSize: 15, fontWeight: 800, color: '#e2e8f0' }}>
                {fmtMoney(eventsData.analyst_price_target_weighted ?? eventsData.analyst_price_target_mean)}
                {eventsData.analyst_n_firms != null && <span style={{ fontSize: 11, fontWeight: 400, color: '#475569' }}> ({eventsData.analyst_n_firms} firm{eventsData.analyst_n_firms === 1 ? '' : 's'})</span>}
              </div>
            </div>
          )}
          {eventsData.eps_estimate != null && (
            <div>
              <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em' }}>EPS Estimate</div>
              <div style={{ fontSize: 15, fontWeight: 800, color: '#e2e8f0' }}>${eventsData.eps_estimate.toFixed(2)}</div>
            </div>
          )}
          {eventsData.eps_beat_rate != null && (
            <div>
              <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Beat History</div>
              <div style={{ fontSize: 15, fontWeight: 800, color: eventsData.eps_beat_rate >= 0.5 ? '#4ade80' : '#f87171' }}>
                {Math.round(eventsData.eps_beat_rate * 100)}%
                {eventsData.eps_avg_surprise_pct != null && (
                  <span style={{ fontSize: 11, fontWeight: 400, color: eventsData.eps_avg_surprise_pct >= 0 ? '#4ade80' : '#f87171' }}> (avg {fmtSurprise(eventsData.eps_avg_surprise_pct)})</span>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {isLoading && (
        <div style={{ fontSize: 12, color: '#475569', padding: '12px 0', textAlign: 'center' }}>
          Generating forecast…
        </div>
      )}
      {error && (
        <div style={{ fontSize: 12, color: '#64748b', padding: '8px 0' }}>
          Could not load a forecast right now — real consensus data above is still current.
        </div>
      )}

      {!isLoading && !error && forecast === null && (
        <div style={{ fontSize: 11.5, color: '#64748b', padding: '10px 12px', borderRadius: 8, background: 'rgba(148,163,184,0.04)', border: '1px dashed #1e293b' }}>
          No AI forecast available for this report yet — this feature is admin-gated and off by
          default, or the underlying analyst consensus data is too thin to forecast from.
        </div>
      )}

      {forecast && (
        <>
          <div style={{ fontSize: 13, color: '#cbd5e1', lineHeight: 1.55 }}>{forecast.watching_for}</div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#475569' }}>
              How to Interpret the Market Impact
            </div>
            {forecast.scenarios.map(row => {
              const c = SCENARIO_COLOR[row.scenario] ?? SCENARIO_COLOR['In-Line'];
              return (
                <div key={row.scenario} style={{ padding: '9px 12px', borderRadius: 8, background: c.bg, border: `1px solid ${c.border}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                    <div style={{ width: 7, height: 7, borderRadius: '50%', background: c.dot, flexShrink: 0 }} />
                    <span style={{ fontSize: 12.5, fontWeight: 800, color: '#e2e8f0' }}>{row.scenario}</span>
                    <span style={{ fontSize: 11, color: '#64748b' }}>— {row.interpretation}</span>
                  </div>
                  <div style={{ fontSize: 11.5, color: '#94a3b8', paddingLeft: 13 }}>{row.typical_reaction}</div>
                </div>
              );
            })}
          </div>

          {forecast.bellwether_note && (
            <div style={{ padding: '9px 12px', borderRadius: 8, background: 'rgba(129,140,248,0.06)', border: '1px solid rgba(129,140,248,0.25)' }}>
              <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#818cf8', marginBottom: 3 }}>
                Broad Macro Impact — Bellwether Read
              </div>
              <div style={{ fontSize: 11.5, color: '#94a3b8', lineHeight: 1.5 }}>{forecast.bellwether_note}</div>
            </div>
          )}

          <div style={{ fontSize: 10, color: '#334155', fontStyle: 'italic' }}>
            General market-pattern education, not a prediction — no scenario here is more or
            less likely than another. Not financial advice.
          </div>
        </>
      )}
    </div>
  );
}
