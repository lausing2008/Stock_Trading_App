/**
 * Signal Tuning Status page (/signal-tuning)
 *
 * Operational visibility into the self-tuning signal system (Tiers 85-88).
 *
 * Shows per style (SHORT/SWING/LONG/GROWTH):
 *   - 14-day rolling win rate with threshold warnings
 *   - Watchdog circuit-breaker state (nominal / tightened / manual review)
 *   - Effective vs hardcoded-default parameters: buy_threshold, ml_weight_cap, adx_min, breadth_compression
 *   - 7-day BUY signal count and 14-day evaluated outcome count
 *
 * Data source: GET /signals/tune_status (read-only, no side effects)
 * Auth: admin required
 */
import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api } from '@/lib/api';
import { getSession } from '@/lib/auth';

// ── Types ────────────────────────────────────────────────────────────────────

type StyleStatus = {
  defaults: { buy_threshold_bull: number; ml_weight_cap: number; adx_min: number | null; breadth_compression: number | null };
  redis_overrides: { watchdog_threshold: number | null; calibrated_threshold: number | null; ml_weight_cap: number | null; adx_min: number | null; breadth_compression: number | null };
  effective: { buy_threshold_bull: number; ml_weight_cap: number; adx_min: number | null; breadth_compression: number | null };
  performance: { win_rate_14d: number | null; n_outcomes_14d: number; signals_7d: number };
  watchdog: { status: string; tighten_count: number; current_threshold: number | null };
};

type TuneStatusResponse = {
  as_of: string;
  config_loaded_at: string | null;
  styles: Record<string, StyleStatus>;
};

// ── Helpers ─────────────────────────────────────────────────────────────────

const STYLE_COLORS: Record<string, string> = {
  SHORT: '#818cf8',
  SWING: '#4f46e5',
  LONG:  '#34d399',
  GROWTH: '#f59e0b',
};

function winRateColor(wr: number | null): string {
  if (wr === null) return '#94a3b8';
  if (wr >= 0.50) return '#4ade80';
  if (wr >= 0.38) return '#fbbf24';
  return '#f87171';
}

function winRateLabel(wr: number | null): string {
  if (wr === null) return '— no data';
  return `${(wr * 100).toFixed(1)}%`;
}

function watchdogColor(status: string): string {
  if (status === 'nominal') return '#4ade80';
  if (status === 'max_tighten_review') return '#f87171';
  return '#fbbf24';
}

function watchdogLabel(status: string): string {
  if (status === 'nominal') return 'Nominal';
  if (status === 'max_tighten_review') return 'Max Tighten — Manual Review';
  const m = status.match(/tightened_(\d+)x/);
  if (m) return `Tightened ×${m[1]}`;
  return status;
}

function fmtVal(v: number | null | undefined): string {
  if (v == null) return '—';
  return v.toString();
}

function sourceLabel(override: number | null, kind: string): string {
  if (override !== null) return kind;
  return 'default';
}

function sourceColor(override: number | null): string {
  return override !== null ? '#fbbf24' : '#475569';
}

function Delta({ def, eff }: { def: number | null; eff: number | null }) {
  if (def == null || eff == null || def === eff) return null;
  const d = eff - def;
  const sign = d > 0 ? '+' : '';
  return (
    <span style={{ color: d > 0 ? '#f87171' : '#4ade80', marginLeft: 4, fontSize: 10 }}>
      ({sign}{d.toFixed(3)})
    </span>
  );
}

// ── Parameter Row ─────────────────────────────────────────────────────────────

function ParamRow({ label, def, eff, override, overrideKind }: {
  label: string;
  def: number | null;
  eff: number | null;
  override: number | null;
  overrideKind: string;
}) {
  const changed = override !== null;
  return (
    <tr style={{ borderTop: '1px solid #1e293b' }}>
      <td style={{ padding: '5px 8px', color: '#94a3b8', fontSize: 12 }}>{label}</td>
      <td style={{ padding: '5px 8px', color: '#64748b', fontSize: 12, textAlign: 'right' }}>{fmtVal(def)}</td>
      <td style={{ padding: '5px 8px', textAlign: 'right' }}>
        <span style={{ color: changed ? '#fbbf24' : '#cbd5e1', fontWeight: changed ? 700 : 400, fontSize: 12 }}>
          {fmtVal(eff)}
          {changed && def != null && eff != null && <Delta def={def} eff={eff} />}
        </span>
      </td>
      <td style={{ padding: '5px 8px', textAlign: 'right' }}>
        <span style={{ color: sourceColor(override), fontSize: 10 }}>
          {sourceLabel(override, overrideKind)}
        </span>
      </td>
    </tr>
  );
}

// ── Style Card ─────────────────────────────────────────────────────────────────

function StyleCard({ style, data }: { style: string; data: StyleStatus }) {
  const { defaults, redis_overrides, effective, performance, watchdog } = data;
  const color = STYLE_COLORS[style] || '#64748b';
  const wr = performance.win_rate_14d;

  // Determine which threshold source applies
  let thresholdKind = 'calibrated';
  if (redis_overrides.watchdog_threshold !== null) thresholdKind = 'watchdog';

  return (
    <div style={{
      background: '#0f172a',
      border: `1px solid ${color}33`,
      borderRadius: 8,
      overflow: 'hidden',
      flex: '1 1 220px',
      minWidth: 220,
    }}>
      {/* Header */}
      <div style={{ background: `${color}22`, borderBottom: `1px solid ${color}44`, padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ color, fontWeight: 700, fontSize: 14, letterSpacing: 1 }}>{style}</span>
        <span style={{
          marginLeft: 'auto',
          background: `${winRateColor(wr)}22`,
          color: winRateColor(wr),
          borderRadius: 4,
          padding: '2px 8px',
          fontSize: 12,
          fontWeight: 700,
        }}>
          {winRateLabel(wr)}
          {wr !== null && <span style={{ fontWeight: 400, fontSize: 10, marginLeft: 4 }}>win rate</span>}
        </span>
      </div>

      {/* Watchdog status */}
      <div style={{ padding: '8px 14px', background: '#0a0f1e', borderBottom: '1px solid #1e293b', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: 0.5 }}>Watchdog</span>
        <span style={{
          marginLeft: 'auto',
          color: watchdogColor(watchdog.status),
          fontSize: 11,
          fontWeight: 600,
        }}>
          {watchdogLabel(watchdog.status)}
        </span>
      </div>

      {/* Parameter table */}
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid #1e293b' }}>
            <th style={{ padding: '5px 8px', color: '#475569', fontSize: 10, fontWeight: 500, textAlign: 'left', textTransform: 'uppercase' }}>Param</th>
            <th style={{ padding: '5px 8px', color: '#475569', fontSize: 10, fontWeight: 500, textAlign: 'right', textTransform: 'uppercase' }}>Default</th>
            <th style={{ padding: '5px 8px', color: '#94a3b8', fontSize: 10, fontWeight: 600, textAlign: 'right', textTransform: 'uppercase' }}>Effective</th>
            <th style={{ padding: '5px 8px', color: '#475569', fontSize: 10, fontWeight: 500, textAlign: 'right', textTransform: 'uppercase' }}>Source</th>
          </tr>
        </thead>
        <tbody>
          <ParamRow
            label="Buy threshold"
            def={defaults.buy_threshold_bull}
            eff={effective.buy_threshold_bull}
            override={redis_overrides.watchdog_threshold ?? redis_overrides.calibrated_threshold}
            overrideKind={thresholdKind}
          />
          <ParamRow
            label="ML weight cap"
            def={defaults.ml_weight_cap}
            eff={effective.ml_weight_cap}
            override={redis_overrides.ml_weight_cap}
            overrideKind="auto-tuner"
          />
          <ParamRow
            label="ADX min"
            def={defaults.adx_min}
            eff={effective.adx_min}
            override={redis_overrides.adx_min}
            overrideKind="auto-tuner"
          />
          <ParamRow
            label="Breadth compress"
            def={defaults.breadth_compression}
            eff={effective.breadth_compression}
            override={redis_overrides.breadth_compression}
            overrideKind="auto-tuner"
          />
        </tbody>
      </table>

      {/* Metrics footer */}
      <div style={{ padding: '8px 14px', borderTop: '1px solid #1e293b', display: 'flex', gap: 16 }}>
        <div style={{ flex: 1, textAlign: 'center' }}>
          <div style={{ color: '#cbd5e1', fontWeight: 600, fontSize: 14 }}>{performance.signals_7d}</div>
          <div style={{ color: '#475569', fontSize: 10 }}>signals 7d</div>
        </div>
        <div style={{ flex: 1, textAlign: 'center' }}>
          <div style={{ color: '#cbd5e1', fontWeight: 600, fontSize: 14 }}>{performance.n_outcomes_14d}</div>
          <div style={{ color: '#475569', fontSize: 10 }}>outcomes 14d</div>
        </div>
        {watchdog.tighten_count > 0 && (
          <div style={{ flex: 1, textAlign: 'center' }}>
            <div style={{ color: '#fbbf24', fontWeight: 600, fontSize: 14 }}>{watchdog.tighten_count}/3</div>
            <div style={{ color: '#475569', fontSize: 10 }}>tightenings</div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function SignalTuningPage() {
  const router = useRouter();
  const [username, setUsername] = useState<string | null>(null);

  useEffect(() => {
    const s = getSession();
    if (!s) { router.replace('/login'); return; }
    if (!s.is_admin) { router.replace('/'); return; }
    setUsername(s.username);
  }, [router]);

  const { data, error, isLoading, mutate } = useSWR(
    username ? 'signal-tune-status' : null,
    () => api.signalTuneStatus(),
    { revalidateOnFocus: false },
  );

  if (!username) return null;

  const STYLES = ['SHORT', 'SWING', 'LONG', 'GROWTH'];

  // Check if any style has active overrides
  const anyOverrides = data && STYLES.some(s => {
    const d = data.styles[s];
    return d && (d.redis_overrides.watchdog_threshold !== null ||
                 d.redis_overrides.calibrated_threshold !== null ||
                 d.redis_overrides.ml_weight_cap !== null);
  });

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: '24px 16px' }}>
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
          <h1 style={{ margin: 0, color: '#e2e8f0', fontSize: 22, fontWeight: 700 }}>
            Signal Self-Tuning Status
          </h1>
          <button
            onClick={() => mutate()}
            style={{ marginLeft: 'auto', background: '#1e293b', border: '1px solid #334155', borderRadius: 6, color: '#94a3b8', cursor: 'pointer', padding: '4px 12px', fontSize: 12 }}
          >
            Refresh
          </button>
        </div>
        {data && (
          <p style={{ margin: 0, color: '#64748b', fontSize: 12 }}>
            As of {data.as_of}
            {data.config_loaded_at && ` · Config loaded ${new Date(data.config_loaded_at).toLocaleString()}`}
          </p>
        )}
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', gap: 20, marginBottom: 20, flexWrap: 'wrap' }}>
        {[
          { color: '#4ade80', label: '≥50% win rate — healthy' },
          { color: '#fbbf24', label: '38–50% win rate — watchdog watching' },
          { color: '#f87171', label: '<38% win rate — watchdog may tighten' },
        ].map(({ color, label }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ width: 10, height: 10, borderRadius: 2, background: color }} />
            <span style={{ color: '#64748b', fontSize: 11 }}>{label}</span>
          </div>
        ))}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: '#fbbf24', fontSize: 11, fontWeight: 700 }}>yellow value</span>
          <span style={{ color: '#64748b', fontSize: 11 }}>= Redis override active (auto-tuner or watchdog)</span>
        </div>
      </div>

      {anyOverrides && (
        <div style={{ background: '#1c1410', border: '1px solid #d97706', borderRadius: 6, padding: '8px 14px', marginBottom: 20, color: '#fbbf24', fontSize: 12 }}>
          One or more styles have active Redis parameter overrides. These override hardcoded defaults until the Redis TTL expires.
        </div>
      )}

      {/* Loading / error */}
      {isLoading && (
        <div style={{ color: '#64748b', textAlign: 'center', padding: 40 }}>Loading tune status…</div>
      )}
      {error && (
        <div style={{ color: '#f87171', textAlign: 'center', padding: 40 }}>
          Failed to load: {error?.message || 'Unknown error'}
        </div>
      )}

      {/* Style cards */}
      {data && (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          {STYLES.map(style => (
            data.styles[style] ? (
              <StyleCard key={style} style={style} data={data.styles[style]} />
            ) : null
          ))}
        </div>
      )}

      {/* Self-tuning system info */}
      <div style={{ marginTop: 32, borderTop: '1px solid #1e293b', paddingTop: 20 }}>
        <h2 style={{ color: '#94a3b8', fontSize: 14, fontWeight: 600, marginBottom: 12 }}>How the self-tuning system works</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
          {[
            {
              title: 'Tier 85 — Style Auto-Tuner',
              color: '#a78bfa',
              desc: 'Weekly: sweeps ml_weight_cap, adx_min, breadth_compression per style from 90-day signal_outcomes. Finds the combination that maximises win rate × signal count. Writes to Redis (30d TTL).',
              schedule: 'Weekly (Sunday), after calibrate_signal_thresholds',
            },
            {
              title: 'Tier 86 — Self-Healing Watchdog',
              color: '#f43f5e',
              desc: 'Daily: if 14d win rate < 38% with ≥5 outcomes, tightens buy threshold +0.03 (max 3× before flagging manual review). If 0 signals for 7 days, relaxes −0.02. Writes to Redis (7d TTL).',
              schedule: 'Daily 06:10 ET Mon–Fri',
            },
            {
              title: 'Tier 79 — Outcomes Calibration',
              color: '#06b6d4',
              desc: 'Weekly: computes optimal buy thresholds from 90-day outcomes vs implied probability. Writes to Redis (30d TTL). Watchdog threshold takes priority when active.',
              schedule: 'Weekly (Sunday), during full refresh',
            },
            {
              title: 'Tier 87 — Outcome-Informed ML',
              color: '#06b6d4',
              desc: 'ML models trained with closed signal_outcomes rows appended as 2× weighted training examples (per-symbol, ≥20 outcomes required). Live trading labels replace synthetic price-history labels over time.',
              schedule: 'On-demand via POST /ml/tune_all',
            },
          ].map(({ title, color, desc, schedule }) => (
            <div key={title} style={{ background: '#0f172a', border: `1px solid ${color}33`, borderRadius: 6, padding: 14 }}>
              <div style={{ color, fontWeight: 600, fontSize: 12, marginBottom: 6 }}>{title}</div>
              <p style={{ color: '#94a3b8', fontSize: 11, margin: '0 0 8px' }}>{desc}</p>
              <div style={{ color: '#475569', fontSize: 10 }}>Schedule: {schedule}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
