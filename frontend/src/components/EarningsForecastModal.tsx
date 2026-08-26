import Link from 'next/link';
import EarningsForecastPanel from './EarningsForecastPanel';
import type { CalendarEvent } from '@/lib/api';

// AUD-EARNINGSFORECAST: modal shell around EarningsForecastPanel, opened by clicking an
// upcoming earnings event on the calendar page — matches the established fixed-overlay modal
// pattern from positions.tsx (backdrop click-to-close, a top accent bar, an explicit ✕ button).

export default function EarningsForecastModal({ ev, onClose }: { ev: CalendarEvent; onClose: () => void }) {
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(6,8,20,0.85)', backdropFilter: 'blur(4px)' }} />
      <div style={{ position: 'relative', zIndex: 10, width: '100%', maxWidth: '560px', maxHeight: '85vh', overflowY: 'auto', borderRadius: '14px', background: 'linear-gradient(160deg,#0d1424,#090e1a)', border: '1px solid rgba(99,102,241,0.3)', boxShadow: '0 24px 48px rgba(0,0,0,0.5)' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#4f46e5,#818cf8,#4f46e5)', position: 'sticky', top: 0 }} />
        <div style={{ padding: '20px 22px 22px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <h3 style={{ margin: 0, fontSize: '17px', fontWeight: 800, color: '#f1f5f9' }}>
                <Link href={`/stock/${ev.symbol}`} style={{ color: '#818cf8', textDecoration: 'none' }}>{ev.symbol}</Link>
                {' '}Earnings Forecast
              </h3>
              <div style={{ fontSize: 11.5, color: '#64748b', marginTop: 3 }}>
                {ev.name ?? ev.symbol} · reports {ev.date} ({ev.days_to_event === 0 ? 'today' : ev.days_to_event === 1 ? 'tomorrow' : `in ${ev.days_to_event}d`})
              </div>
            </div>
            <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '18px', lineHeight: 1, padding: 0 }}>✕</button>
          </div>

          <EarningsForecastPanel
            symbol={ev.symbol ?? ''}
            sector={ev.sector}
            daysToEvent={ev.days_to_event}
            eventsData={ev}
          />
        </div>
      </div>
    </div>
  );
}
