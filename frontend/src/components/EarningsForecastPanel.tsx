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

  // T373: the POST-earnings AI direction for this symbol, if the report has already landed.
  // The user asked why the modal shows no directional prediction. For an UPCOMING report it
  // deliberately never will — T370 made the pre-earnings prompt return three SCENARIOS rather
  // than a direction, because before the print a direction is prophecy. After the print it is
  // interpretation of known numbers, and THAT read existed but was only surfaced on /forecast
  // and in the impact email. It belongs here too, on the symbol the user is actually looking at.
  const { data: pastEvents } = useSWR(
    `earnings-past-${symbol}`,
    () => api.eventsEarningsSymbol(symbol),
    { revalidateOnFocus: false },
  );
  const lastWithDirection = (pastEvents ?? [])
    .filter(e => !e.is_upcoming && !!e.impact_direction)
    .sort((a, b) => b.earnings_date.localeCompare(a.earnings_date))[0] ?? null;

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

      {/* T373-FORECAST-REASON: say the REAL reason, from the backend.
          This block used to assert "this feature is admin-gated and off by default, or the
          underlying analyst consensus data is too thin" — a guess, and for TSM both halves were
          FALSE (the flag was on, the key was set, TSM had 9 analysts and a full current-quarter
          consensus). The actual cause was a first-request LLM generation that had not finished.
          Same shape as AUD-CONVICTION-RSIDIV-NOWRITER: a confidently stated explanation for
          something never actually checked. The backend knows which guard fired; show that. */}
      {!isLoading && !error && forecast === null && (
        <div style={{ fontSize: 11.5, color: '#64748b', padding: '10px 12px', borderRadius: 8, background: 'rgba(148,163,184,0.04)', border: '1px dashed #1e293b' }}>
          {data?.unavailable_detail
            ? <>No AI forecast yet — {data.unavailable_detail}</>
            : <>No AI forecast available for this report yet.</>}
          {data?.unavailable_reason === 'llm_failed' && (
            <div style={{ marginTop: 6, color: '#94a3b8' }}>
              The first request for a symbol has to generate one, which can take up to a minute.
              Reopening this usually shows it.
            </div>
          )}
        </div>
      )}

      {/* T373: the POST-earnings directional read for the LAST report, when one exists.
          Answers the user's "why doesn't it show me the prediction direction and reason?" —
          for an UPCOMING report it deliberately never does (see the note at the fetch above),
          but the read on the report that already happened belongs on this modal, not only on
          /forecast and in the impact email.

          Always carries "not a measured edge": until the accuracy endpoint reports an adequate
          sample this is an unvalidated LLM opinion, and this platform has twice shipped a
          confident figure nobody could check (AUD-RANK-RSPLACEHOLDER's fabricated 50.0 that the
          weight optimizer LEARNED from; AUD-CONVICTION-RSIDIV-NOWRITER's "None detected"). */}
      {lastWithDirection && (
        <div style={{
          padding: '10px 12px', borderRadius: 8,
          background: lastWithDirection.impact_direction === 'bullish' ? 'rgba(34,197,94,0.07)'
            : lastWithDirection.impact_direction === 'bearish' ? 'rgba(239,68,68,0.07)'
            : 'rgba(148,163,184,0.05)',
          border: `1px solid ${lastWithDirection.impact_direction === 'bullish' ? 'rgba(34,197,94,0.28)'
            : lastWithDirection.impact_direction === 'bearish' ? 'rgba(239,68,68,0.28)'
            : 'rgba(148,163,184,0.22)'}`,
        }}>
          <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            AI read on the {lastWithDirection.earnings_date} report
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
            <span style={{
              fontSize: 13, fontWeight: 800,
              color: lastWithDirection.impact_direction === 'bullish' ? '#22c55e'
                : lastWithDirection.impact_direction === 'bearish' ? '#f87171' : '#94a3b8',
            }}>
              {(lastWithDirection.impact_direction ?? '').charAt(0).toUpperCase()
                + (lastWithDirection.impact_direction ?? '').slice(1)}
            </span>
            {lastWithDirection.impact_direction_confidence != null && (
              <span style={{ fontSize: 11, color: '#64748b' }}>
                {lastWithDirection.impact_direction_confidence.toFixed(0)}% confidence
              </span>
            )}
            {/* The measured outcome, shown WITH the call — a prediction displayed without its
                result is how an unfalsifiable number survives. */}
            {lastWithDirection.post_earnings_return_1d != null && (
              <span style={{ fontSize: 11, color: '#64748b' }}>
                · actual 1d{' '}
                <span style={{ color: lastWithDirection.post_earnings_return_1d >= 0 ? '#22c55e' : '#f87171' }}>
                  {(lastWithDirection.post_earnings_return_1d * 100).toFixed(2)}%
                </span>
              </span>
            )}
          </div>
          {lastWithDirection.impact_text && (
            <div style={{ fontSize: 12, color: '#cbd5e1', lineHeight: 1.55, marginTop: 6 }}>
              {lastWithDirection.impact_text}
            </div>
          )}
          <div style={{ fontSize: 10.5, color: '#475569', marginTop: 6 }}>
            AI interpretation of the reported numbers — not a measured edge.
          </div>
        </div>
      )}

      {forecast && (
        <>
          <div style={{ fontSize: 13, color: '#cbd5e1', lineHeight: 1.55 }}>{forecast.watching_for}</div>

          {forecast.past_reactions.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#475569' }}>
                Real Past Reactions
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
                  <thead>
                    <tr style={{ color: '#475569', textAlign: 'left' }}>
                      <th style={{ padding: '3px 8px 3px 0', fontWeight: 600 }}>Report Date</th>
                      <th style={{ padding: '3px 8px', fontWeight: 600 }}>Surprise</th>
                      <th style={{ padding: '3px 8px', fontWeight: 600 }}>1-Day Move</th>
                      <th style={{ padding: '3px 0', fontWeight: 600 }}>5-Day Move</th>
                    </tr>
                  </thead>
                  <tbody>
                    {forecast.past_reactions.map(pr => (
                      <tr key={pr.report_date} style={{ borderTop: '1px solid #1e293b' }}>
                        <td style={{ padding: '4px 8px 4px 0', color: '#94a3b8' }}>{pr.report_date}</td>
                        <td style={{ padding: '4px 8px', color: (pr.surprise_pct ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>
                          {pr.surprise_pct != null ? `${pr.surprise_pct >= 0 ? '+' : ''}${pr.surprise_pct.toFixed(1)}%` : '—'}
                        </td>
                        <td style={{ padding: '4px 8px', color: (pr.return_1d ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>
                          {fmtPct(pr.return_1d)}
                        </td>
                        <td style={{ padding: '4px 0', color: (pr.return_5d ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>
                          {fmtPct(pr.return_5d)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ fontSize: 10, color: '#334155' }}>
                Real, measured reactions — used above to ground the scenario read where relevant.
              </div>
            </div>
          )}

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
