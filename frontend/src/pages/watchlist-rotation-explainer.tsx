import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import { getSession } from '@/lib/auth';

// ── Static content ────────────────────────────────────────────────────────────

const WATCHLIST_MARKET_MIX = [
  { name: 'Growth / Momentum', style: 'GROWTH', markets: 'US + HK' },
  { name: 'Swing Trade', style: 'SWING', markets: 'US + HK' },
  { name: '10 Days Swing Trading', style: 'SWING', markets: 'US + HK' },
  { name: 'Long Term', style: 'LONG', markets: 'US only' },
  { name: 'Short Term', style: 'SHORT', markets: 'US only' },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '32px' }}>
      <h2 style={{ fontSize: '15px', fontWeight: 800, color: '#e2e8f0', marginBottom: '10px' }}>{title}</h2>
      <div style={{ fontSize: '13px', lineHeight: 1.7, color: '#94a3b8' }}>{children}</div>
    </div>
  );
}

function Callout({ tone, title, children }: { tone: 'info' | 'warn' | 'good'; title: string; children: React.ReactNode }) {
  const colors = {
    info: { bg: 'rgba(56,189,248,0.08)', border: 'rgba(56,189,248,0.3)', text: '#38bdf8' },
    warn: { bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.3)', text: '#f87171' },
    good: { bg: 'rgba(34,197,94,0.08)', border: 'rgba(34,197,94,0.3)', text: '#22c55e' },
  }[tone];
  return (
    <div style={{ padding: '12px 16px', borderRadius: '10px', background: colors.bg, border: `1px solid ${colors.border}`, marginBottom: '16px' }}>
      <div style={{ fontSize: '11px', fontWeight: 800, color: colors.text, textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '4px' }}>
        {title}
      </div>
      <div style={{ fontSize: '12.5px', color: '#cbd5e1', lineHeight: 1.6 }}>{children}</div>
    </div>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code style={{ background: '#0d1424', border: '1px solid #1e293b', borderRadius: '4px', padding: '1px 6px', fontSize: '12px', color: '#f59e0b', fontFamily: 'monospace' }}>
      {children}
    </code>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function WatchlistRotationExplainerPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    if (session.role !== 'admin') { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  if (!authed) return null;

  return (
    <div style={{ maxWidth: '860px', margin: '0 auto', padding: '24px 0 60px' }}>
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '6px' }}>
          How Watchlist Auto-Rotation Works
        </h1>
        <p style={{ fontSize: '13px', color: '#64748b' }}>
          A weekly job that automatically drops underperforming stocks and adds newly-strong candidates —
          replacing what used to be 100% manual watchlist curation.{' '}
          <a href="/watchlist-performance" style={{ color: '#38bdf8', textDecoration: 'none' }}>
            View live rotation history →
          </a>
        </p>
      </div>

      <Callout tone="info" title="Why 4 separate lists, not one merged list">
        We considered replacing the 4 style-specific watchlists (SHORT/SWING/LONG/GROWTH) with one
        generic list rotated weekly. We kept the 4 separate lists instead — each style has its own
        independently-trained ML model per symbol and its own threshold profile (LONG in particular
        applies a fundamentals/K-Score boost the other styles don&apos;t). &quot;Good for GROWTH&quot;
        and &quot;good for LONG&quot; are genuinely different judgments about the same stock, not the
        same ranking viewed two ways — merging them would throw that distinction away.
      </Callout>

      <Section title="The schedule">
        Runs every <strong style={{ color: '#e2e8f0' }}>Sunday at 17:00 ET</strong> — deliberately
        placed after that week&apos;s fundamentals snapshot (16:30 ET) and sector rotation (16:00 ET),
        so the K-Score rankings used to pick new candidates are as fresh as that week&apos;s data gets.
      </Section>

      <Section title="Scoped per watchlist, not per style">
        <p style={{ marginBottom: 12 }}>
          The obvious design would run rotation once per style against one merged candidate pool. That&apos;s
          wrong for this app: checking real production data, several style watchlists genuinely mix US
          and HK stocks under the same style tag.
        </p>
        <div style={{ borderRadius: '10px', border: '1px solid #1e293b', overflow: 'hidden', marginBottom: 12 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
            <thead>
              <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                {['Watchlist', 'Style', 'Markets'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 12px', color: '#475569', fontWeight: 700, fontSize: '10.5px', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {WATCHLIST_MARKET_MIX.map(w => (
                <tr key={w.name} style={{ borderBottom: '1px solid #1e293b' }}>
                  <td style={{ padding: '8px 12px', color: '#e2e8f0', fontWeight: 600 }}>{w.name}</td>
                  <td style={{ padding: '8px 12px', color: '#94a3b8' }}>{w.style}</td>
                  <td style={{ padding: '8px 12px', color: w.markets === 'US + HK' ? '#f59e0b' : '#64748b' }}>{w.markets}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          A per-style rotation with one merged candidate pool would risk suggesting HK candidates to a
          US-heavy GROWTH watchlist, or vice versa. The job instead runs independently per{' '}
          <Code>watchlist_id</Code>: each watchlist&apos;s candidate pool is scoped to that watchlist&apos;s
          own <strong style={{ color: '#e2e8f0' }}>dominant market</strong> (whichever market has more
          existing members; ties break toward US).
        </p>
      </Section>

      <Section title="The rules">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 8 }}>
          <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#0d1424', border: '1px solid rgba(239,68,68,0.25)' }}>
            <div style={{ fontSize: '12px', fontWeight: 800, color: '#ef4444', marginBottom: 8 }}>− DROP a stock if BOTH:</div>
            <ul style={{ margin: 0, paddingLeft: 18, color: '#cbd5e1' }}>
              <li style={{ marginBottom: 6 }}>≥ 15 resolved outcomes in the trailing 90 days</li>
              <li>Win rate over those outcomes is below 40%</li>
            </ul>
          </div>
          <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#0d1424', border: '1px solid rgba(34,197,94,0.25)' }}>
            <div style={{ fontSize: '12px', fontWeight: 800, color: '#22c55e', marginBottom: 8 }}>+ ADD up to 3 candidates:</div>
            <ul style={{ margin: 0, paddingLeft: 18, color: '#cbd5e1' }}>
              <li style={{ marginBottom: 6 }}>Highest K-Score (Ranking.score)</li>
              <li>On the watchlist&apos;s dominant market, not already a member</li>
            </ul>
          </div>
        </div>
      </Section>

      <Callout tone="warn" title="Why 15 outcomes, why 40% — not arbitrary numbers">
        The original design risk flagged for this feature was <strong>whipsawing</strong> — dropping a
        stock right before it recovers, if the sample size isn&apos;t big enough to trust. The 15-outcome
        floor directly mirrors the Self-Healing Watchdog&apos;s own floor (see the Signal Tuning page),
        which was raised for the identical reason after it was found acting on as few as 5 samples.
        Reusing an already-battle-tested floor from this codebase, rather than inventing a new number,
        was the deliberate choice.
      </Callout>

      <Section title="Every action is logged — nothing disappears silently">
        <p style={{ marginBottom: 12 }}>
          Every add and every drop writes one row to the same <Code>TuneHistory</Code> audit table every
          other self-tuning mechanism in this app uses (the Watchdog, the ML weight calibrator, the
          promotion gates). A drop records the stock&apos;s win rate, the 40% floor it missed, and the
          sample count; an add records its K-Score. Nothing changes on a watchlist without a
          reconstructable reason sitting right next to it.
        </p>
        <p>
          Browse the full history — and revert any single action — on the{' '}
          <a href="/watchlist-performance" style={{ color: '#38bdf8', textDecoration: 'none' }}>
            Watchlist Performance page
          </a>
          &apos;s <strong style={{ color: '#e2e8f0' }}>Rotation History</strong> section, filtered per style.
        </p>
      </Section>

      <Callout tone="good" title="You can always undo a single action">
        Reverting a &quot;drop&quot; re-adds that stock to the same watchlist it came from. Reverting an
        &quot;add&quot; removes it again. The audit row itself is never deleted — it&apos;s marked
        reverted, so the history keeps an honest record of what happened and that it was later undone,
        rather than erasing the trail. A bad week of rotation decisions is always fully undoable, one
        action at a time.
      </Callout>

      <Section title="What this deliberately does NOT do">
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          <li style={{ marginBottom: 8 }}>
            <strong style={{ color: '#e2e8f0' }}>No visible &quot;auto-added&quot; badge on the watchlist
            itself.</strong> Neither the watchlist nor its items have a field distinguishing a
            manually-added stock from one the job added — adding one would mean altering an existing,
            populated production table, which this app has no safe migration path for today. The Rotation
            History page is the only place to see how a stock arrived.
          </li>
          <li>
            <strong style={{ color: '#e2e8f0' }}>No automatic drift detection on the rotation job
            itself</strong> (unlike some of the app&apos;s ML retraining jobs). This is a new mechanism —
            worth revisiting after a few months of real run history exist to look at.
          </li>
        </ul>
      </Section>
    </div>
  );
}
