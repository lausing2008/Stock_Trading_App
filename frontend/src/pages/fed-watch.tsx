/**
 * T405-FEDWATCH — market-implied odds of a Fed move at each upcoming FOMC meeting.
 *
 * FOMC previously appeared in this app only as a BLACKOUT GATE ("don't open near a meeting").
 * That says a meeting is risky without saying what the market thinks will happen at it.
 *
 * Everything here is derived from CBOT 30-Day Fed Funds futures (ZQ) — the same instrument
 * CME's own FedWatch uses. These are market EXPECTATIONS, not a forecast by this app.
 */
import useSWR from 'swr';
import Head from 'next/head';
import { api } from '@/lib/api';
import type { FedWatchMeeting } from '@/lib/api';

const CARD: React.CSSProperties = {
  background: '#0b1220', border: '1px solid #1e293b', borderRadius: 8, padding: 16,
};
const LABEL: React.CSSProperties = {
  fontSize: 10, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em',
};

const fmtDate = (iso: string) =>
  new Date(iso + 'T12:00:00Z').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });

function Bar({ pct, cut }: { pct: number; cut: boolean }) {
  return (
    <div style={{ background: '#0f172a', borderRadius: 4, height: 8, overflow: 'hidden', flex: 1 }}>
      <div style={{ width: `${Math.max(0, Math.min(100, pct))}%`, height: '100%',
                    background: cut ? '#38bdf8' : pct > 0 ? '#f59e0b' : '#334155' }} />
    </div>
  );
}

function MeetingCard({ m }: { m: FedWatchMeeting }) {
  if (!m.available) {
    return (
      <div style={{ ...CARD, opacity: 0.75 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#cbd5e1' }}>{fmtDate(m.meeting_date)}</div>
        <div style={{ fontSize: 11, color: '#f59e0b', marginTop: 8 }}>
          {m.reason === 'no_futures_quote'
            ? `No quote for ${m.contract} — this meeting is shown so the gap is visible rather than the calendar just looking shorter.`
            : 'The meeting falls on the final day of its contract month, so the month average carries no information about the post-meeting rate.'}
        </div>
      </div>
    );
  }
  const move = m.probability_of_any_move_pct ?? 0;
  const cutting = (m.expected_change_bp ?? 0) < 0;
  return (
    <div style={CARD}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0' }}>{fmtDate(m.meeting_date)}</div>
        <div style={{ fontSize: 11, color: move >= 50 ? (cutting ? '#38bdf8' : '#f59e0b') : '#94a3b8' }}>
          {move.toFixed(0)}% chance of a move
        </div>
      </div>

      <div style={{ fontSize: 17, fontWeight: 700, marginTop: 8, color: cutting ? '#38bdf8' : (m.direction === 'hike' ? '#f59e0b' : '#cbd5e1') }}>
        {m.most_likely}
      </div>

      <div style={{ marginTop: 12 }}>
        {(m.outcomes ?? []).map(o => (
          <div key={o.label} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <div style={{ fontSize: 11, color: '#cbd5e1', width: 92 }}>{o.label}</div>
            <Bar pct={o.probability_pct} cut={o.move_bp < 0} />
            <div style={{ fontSize: 11, color: '#94a3b8', width: 44, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
              {o.probability_pct.toFixed(1)}%
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 12,
                    borderTop: '1px solid #1e293b', paddingTop: 10 }}>
        <div><span style={LABEL}>Rate before</span>
          <div style={{ fontSize: 12, color: '#cbd5e1' }}>{m.rate_before_pct.toFixed(3)}%</div></div>
        <div><span style={LABEL}>Implied after</span>
          <div style={{ fontSize: 12, color: '#cbd5e1' }}>{m.rate_after_pct?.toFixed(3)}%</div></div>
      </div>

      <div style={{ fontSize: 10, color: '#475569', marginTop: 8 }}>
        {m.contract} @ {m.contract_price} · month avg {m.implied_month_avg_pct?.toFixed(3)}%
      </div>

      {m.low_precision && (
        <div style={{ fontSize: 10.5, color: '#f59e0b', marginTop: 8, lineHeight: 1.5 }}>
          ⚠ Low precision — only {m.days_after_meeting} day(s) of this contract month fall after
          the meeting, so a small quote error moves the implied rate a lot.
        </div>
      )}
    </div>
  );
}

export default function FedWatchPage() {
  const { data, error } = useSWR('fed-watch', () => api.getFedWatch(), { refreshInterval: 300_000 });

  return (
    <>
      <Head><title>Fed Watch — StockAI</title></Head>
      <div style={{ padding: '20px 24px', maxWidth: 1200, margin: '0 auto' }}>
        <h1 style={{ fontSize: 20, color: '#f1f5f9', marginBottom: 4 }}>Fed Watch</h1>
        <p style={{ fontSize: 12, color: '#64748b', marginBottom: 18, maxWidth: 780, lineHeight: 1.6 }}>
          What the market currently prices for each upcoming FOMC meeting, derived from CBOT
          30-Day Fed Funds futures — the same instrument CME&apos;s FedWatch uses. These are
          market expectations, not a forecast by this app.
        </p>

        {error && <div style={{ ...CARD, color: '#ef4444', fontSize: 12 }}>Could not load Fed Watch.</div>}
        {!data && !error && <div style={{ fontSize: 12, color: '#475569' }}>Loading…</div>}

        {data && !data.available && (
          <div style={{ ...CARD, borderColor: 'rgba(245,158,11,0.35)' }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#f59e0b' }}>Fed Watch unavailable</div>
            <div style={{ fontSize: 11.5, color: '#cbd5e1', marginTop: 6 }}>
              {data.reason === 'no_fomc_calendar'
                ? 'No upcoming FOMC meetings are present in the economic calendar.'
                : 'Fed Funds futures quotes are unavailable right now, so no probabilities can be derived.'}
            </div>
          </div>
        )}

        {data?.available && (
          <>
            <div style={{ ...CARD, marginBottom: 16 }}>
              <span style={LABEL}>Current market-implied effective rate</span>
              <div style={{ fontSize: 24, fontWeight: 700, color: '#e2e8f0', marginTop: 4 }}>
                {data.current_implied_rate_pct?.toFixed(3)}%
              </div>
              <div style={{ fontSize: 10.5, color: '#64748b', marginTop: 6, lineHeight: 1.5 }}>
                This is the EFFECTIVE fed funds rate the futures imply — not the target range.
                The effective rate trades a few basis points inside the band.
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 14 }}>
              {(data.meetings ?? []).map(m => <MeetingCard key={m.meeting_date} m={m} />)}
            </div>

            <div style={{ ...CARD, marginTop: 20 }}>
              <div style={{ ...LABEL, marginBottom: 8 }}>How to read this — and what it is not</div>
              <ul style={{ margin: 0, paddingLeft: 18, color: '#94a3b8', fontSize: 11.5, lineHeight: 1.7 }}>
                <li>
                  The Fed moves in 25bp steps, so &quot;a 12bp cut&quot; is not a thing. An expected
                  change of −12bp means the market prices roughly a 50% chance of a 25bp cut and
                  50% of no move. The bars above express exactly that.
                </li>
                {(data.limitations ?? []).map((l, i) => <li key={i}>{l}</li>)}
                <li>
                  A 70% chance of a cut means cuts do <b>not</b> happen 30% of the time — and the
                  market is often wrong by considerably more than that.
                </li>
              </ul>
              <div style={{ fontSize: 10, color: '#475569', marginTop: 10 }}>
                Source: {data.source} · as of {data.as_of ? new Date(data.as_of).toLocaleString() : '—'}
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}
