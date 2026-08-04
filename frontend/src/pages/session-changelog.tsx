/**
 * Session Changelog — /session-changelog. A plain-language walkthrough of everything built,
 * fixed, and investigated in this working session, for the user (not a future Claude session)
 * to read. Follows learn.tsx's Section/SubSection/Callout/Code component conventions and
 * reports.tsx's tab-array structure, rather than inventing new layout patterns.
 *
 * Organized chronologically within topic groups (alerts, signal-quality bugs, decision-engine
 * safety gates, observability, position-scaling) since that's how the work actually happened —
 * each entry names what was found, why it mattered, and what changed.
 */
import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import { getSession } from '@/lib/auth';
import Link from 'next/link';

type Tab = 'overview' | 'alerts' | 'signalbugs' | 'decisionengine' | 'observability' | 'positionscaling';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview',        label: 'Overview' },
  { key: 'alerts',           label: 'New Alerts & Screener' },
  { key: 'signalbugs',       label: 'Signal Quality Fixes' },
  { key: 'decisionengine',   label: 'Decision Engine Safety' },
  { key: 'observability',    label: 'Silent-Failure Audit' },
  { key: 'positionscaling',  label: 'Position Scaling' },
];

function tabFromQuery(q: unknown): Tab {
  const valid: Tab[] = ['overview', 'alerts', 'signalbugs', 'decisionengine', 'observability', 'positionscaling'];
  return valid.includes(q as Tab) ? (q as Tab) : 'overview';
}

// ── Shared components (matches learn.tsx's conventions exactly) ────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 32 }}>
      <h2 style={{ fontSize: 16, fontWeight: 800, color: '#e2e8f0', marginBottom: 10 }}>{title}</h2>
      <div style={{ fontSize: 13.5, lineHeight: 1.75, color: '#94a3b8' }}>{children}</div>
    </div>
  );
}

function SubSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 22 }}>
      <h3 style={{ fontSize: 13.5, fontWeight: 700, color: '#cbd5e1', marginBottom: 8 }}>{title}</h3>
      <div style={{ fontSize: 13.5, lineHeight: 1.75, color: '#94a3b8' }}>{children}</div>
    </div>
  );
}

function Callout({ tone, title, children }: { tone: 'info' | 'warn' | 'good' | 'example'; title: string; children: React.ReactNode }) {
  const colors = {
    info: { bg: 'rgba(56,189,248,0.08)', border: 'rgba(56,189,248,0.3)', text: '#38bdf8' },
    warn: { bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.3)', text: '#f87171' },
    good: { bg: 'rgba(34,197,94,0.08)', border: 'rgba(34,197,94,0.3)', text: '#22c55e' },
    example: { bg: 'rgba(168,85,247,0.08)', border: 'rgba(168,85,247,0.3)', text: '#a78bfa' },
  }[tone];
  return (
    <div style={{ padding: '14px 16px', borderRadius: 10, background: colors.bg, border: `1px solid ${colors.border}`, marginBottom: 16 }}>
      <div style={{ fontSize: 11, fontWeight: 800, color: colors.text, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>
        {title}
      </div>
      <div style={{ fontSize: 12.5, color: '#cbd5e1', lineHeight: 1.7 }}>{children}</div>
    </div>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code style={{ background: '#0d1424', border: '1px solid #1e293b', borderRadius: 4, padding: '1px 6px', fontSize: 12, color: '#f59e0b', fontFamily: 'monospace' }}>
      {children}
    </code>
  );
}

function EntryCard({ title, tag, children }: { title: string; tag?: string; children: React.ReactNode }) {
  return (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: '16px 18px', marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>{title}</span>
        {tag && (
          <span style={{ fontSize: 10, fontWeight: 700, color: '#818cf8', background: 'rgba(99,102,241,0.12)', padding: '2px 7px', borderRadius: 4, textTransform: 'uppercase', letterSpacing: 0.4 }}>
            {tag}
          </span>
        )}
      </div>
      <div style={{ fontSize: 13, lineHeight: 1.7, color: '#94a3b8' }}>{children}</div>
    </div>
  );
}

// ── Tab: Overview ────────────────────────────────────────────────────────────────────────

function OverviewTab() {
  return (
    <>
      <Section title="What this session covered">
        <p style={{ marginBottom: 14 }}>
          This session ran across several distinct threads of work — some started from a direct
          question you asked ({'"'}why didn&apos;t I get an alert{'"'}, {'"'}why is this signal
          wrong{'"'}), others from proactively surveying the improvements tracker for the next
          worthwhile fix. The common theme: several times, investigating one reported symptom
          uncovered a real, previously-invisible bug that was worth fixing on its own — not just
          answering the original question.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 8 }}>
          <div style={{ padding: '14px 16px', borderRadius: 10, background: '#0d1424', border: '1px solid #1e293b' }}>
            <div style={{ fontSize: 12, fontWeight: 800, color: '#22c55e', marginBottom: 8 }}>Built from scratch</div>
            <ul style={{ margin: 0, paddingLeft: 18, color: '#cbd5e1' }}>
              <li style={{ marginBottom: 6 }}>Short-squeeze alert (fast, 1-minute)</li>
              <li style={{ marginBottom: 6 }}>Options-expiry gamma-unwind alert</li>
              <li style={{ marginBottom: 6 }}>Market-wide new-stock screener</li>
              <li>Put-activity spike callout on stock pages</li>
            </ul>
          </div>
          <div style={{ padding: '14px 16px', borderRadius: 10, background: '#0d1424', border: '1px solid #1e293b' }}>
            <div style={{ fontSize: 12, fontWeight: 800, color: '#f87171', marginBottom: 8 }}>Real bugs found &amp; fixed</div>
            <ul style={{ margin: 0, paddingLeft: 18, color: '#cbd5e1' }}>
              <li style={{ marginBottom: 6 }}>Earnings alerts stuck a day behind</li>
              <li style={{ marginBottom: 6 }}>BUY alerts firing at the top of a peak</li>
              <li style={{ marginBottom: 6 }}>2 real broker/data-loss silent failures</li>
              <li>Shadow mode built but never actually turned on</li>
            </ul>
          </div>
        </div>
      </Section>

      <Section title="What actually changed for you, day to day">
        <ul style={{ margin: 0, paddingLeft: 20 }}>
          <li style={{ marginBottom: 10 }}>
            You&apos;ll now get pushed alerts for two new setups automatically — see the{' '}
            <Link href="/short-squeeze" style={{ color: '#38bdf8', textDecoration: 'none' }}>Short Squeeze Scanner</Link>{' '}
            (now with a built-in {'"'}how to read this{'"'} guide) and the new{' '}
            <Link href="/market-screener" style={{ color: '#38bdf8', textDecoration: 'none' }}>Market-Wide Screener</Link>.
          </li>
          <li style={{ marginBottom: 10 }}>
            Earnings alerts and reactions arrive same-day now instead of a day late — a real
            sync-timing gap was found and fixed.
          </li>
          <li style={{ marginBottom: 10 }}>
            The AI Signal&apos;s conviction-gate alert got two new guards specifically to stop it
            from recommending a BUY right at the top of a rally, on the exact real case that
            prompted the investigation.
          </li>
          <li>
            Your paper portfolios&apos; new {'"'}position scaling{'"'} feature (add on a pullback,
            only if the original thesis still holds) is now actually running in Shadow Mode on
            all 5 real portfolios — it was fully built but had never once been switched on.
          </li>
        </ul>
      </Section>

      <Callout tone="info" title="How to read the rest of this page">
        Each tab below covers one thread of work in the order it actually happened, with the
        real numbers/evidence behind each fix — not just {'"'}fixed a bug{'"'}. If you only
        care about what changed for you day-to-day, the summary above is the short version.
      </Callout>
    </>
  );
}

// ── Tab: New Alerts & Screener ───────────────────────────────────────────────────────────

function AlertsTab() {
  return (
    <>
      <Section title="Two new alerts and a new screener">
        <p>
          You asked how to catch something like a fast-moving stock (DFNS was the specific
          example) before it&apos;s already moved — and separately asked about short-selling
          data and whether a big options-expiry unwind could push a stock hard in either
          direction. Both turned into real, shipped features.
        </p>
      </Section>

      <EntryCard title="Short Squeeze Alert" tag="new">
        Fires every minute, the instant a stock with ≥15% of its float sold short is ALSO up
        ≥3% intraday, right now — the moment shorts start getting forced to cover into a rising
        price. Deliberately BUY-direction only, since there&apos;s no reliable data source for a
        symmetric {'"'}crowded longs unwinding{'"'} signal the way short-interest data exists for
        shorts. See it live on the{' '}
        <Link href="/short-squeeze" style={{ color: '#38bdf8', textDecoration: 'none' }}>Short Squeeze Scanner</Link>,
        which also got a new built-in {'"'}how to read this page{'"'} guide explaining Days to
        Cover, Short %, and how to combine them to find a real candidate vs. a merely-loaded one.
      </EntryCard>

      <EntryCard title="Gamma-Unwind (Options-Expiry) Alert" tag="new">
        Runs every 4 hours. Fires when a stock has a large block of options open interest
        concentrated near the current price (calls OR puts, ≥55% one-sided), close to expiry
        (0-3 days out) — the setup where market makers unwinding their hedge near expiry can
        move the stock sharply. This one is explicitly a <em>watch</em>, never a BUY/SELL call —
        it genuinely can&apos;t tell you which direction the unwind pushes price from this data
        alone, and the Alerts Guide documents exactly why (and what a real GEX upgrade would
        need) rather than guessing.
      </EntryCard>

      <EntryCard title="Market-Wide Screener" tag="new">
        Every other screener in this app is limited to your ~150 already-tracked stocks. This
        one scans the whole US market (top gainers, most-active, aggressive small caps via
        Yahoo Finance&apos;s free screener) to surface something fast-moving before it&apos;s on
        your radar at all — with a one-click {'"'}Add{'"'} button to start tracking anything
        interesting you find. See it on the{' '}
        <Link href="/market-screener" style={{ color: '#38bdf8', textDecoration: 'none' }}>Market Screener</Link>{' '}
        page.
      </EntryCard>

      <EntryCard title="Put-Activity Spike Callout" tag="new">
        A small red callout now appears on the stock detail page&apos;s Options Flow section
        whenever there&apos;s unusual put-contract volume — showing the volume/OI ratio, total
        premium, and whale-trade count. Pure client-side, computed from data already being
        fetched, so no new backend call was needed.
      </EntryCard>
    </>
  );
}

// ── Tab: Signal Quality Fixes ─────────────────────────────────────────────────────────────

function SignalBugsTab() {
  return (
    <>
      <Section title="Two real bugs found by investigating your direct reports">
        <p>
          Both of these started as {'"'}why did this happen{'"'} questions and ended with a real,
          previously-invisible bug fixed at the root cause.
        </p>
      </Section>

      <EntryCard title="Earnings Alerts Stuck a Day Behind (PLTR)" tag="fixed">
        <p style={{ marginBottom: 10 }}>
          You asked why you never got an alert on PLTR&apos;s earnings day, despite it reporting
          real, strong Q2 results with a big beat. The root cause: the job that syncs actual
          earnings numbers only ran once a day, before the market even opened — a company
          reporting during or after market hours (the normal case) never had its real numbers
          picked up until the <em>next</em> morning.
        </p>
        <p>
          Fixed with a new job that re-checks any still-unresolved earnings report every 15
          minutes throughout the trading day and after-hours. Also found (and documented as a
          real, still-open limitation) that the underlying data provider itself can lag the real
          announcement by several hours — a faster fix using this app&apos;s own real-time news
          feed is designed but not yet built, since it needs careful handling of correction/
          re-issue headlines from the same wire.
        </p>
      </EntryCard>

      <EntryCard title="BUY Alert Firing at the Top of a Peak (0939.HK)" tag="fixed">
        <p style={{ marginBottom: 10 }}>
          You asked why you got a BUY alert on a stock that was actually going down. Tracing it
          precisely: the stock had genuinely been overbought for a full week (correctly blocked
          by the alert&apos;s existing safety check the whole time) — then one noisy 5-minute
          tick nudged the indicator just barely below its cutoff, and the alert fired at
          essentially the same overbought level it had been blocking all week.
        </p>
        <p>
          Two new checks close this gap: one requires the indicator to have genuinely cooled
          for real, not just flickered across the line for one tick; the other is a completely
          independent check — is the price still within 3% of its own recent high with momentum
          still hot? — that catches the same problem even without relying on the first
          indicator at all. A real, sustained pullback still clears both checks fine; only the
          {' '}{'"'}still basically at the top, one noisy tick from the cutoff{'"'} case is now
          blocked.
        </p>
      </EntryCard>

      <Callout tone="good" title="Both fixes are live-verified, not just tested">
        After deploying, both fixes were checked directly against the real stock that triggered
        the investigation — not just passing an automated test suite — confirming the actual
        behavior changed as intended.
      </Callout>
    </>
  );
}

// ── Tab: Decision Engine Safety ───────────────────────────────────────────────────────────

function DecisionEngineTab() {
  return (
    <>
      <Section title="Closing a gap between two scoring systems">
        <p>
          This app has two independent systems that can decide whether to enter a trade: the
          original, battle-tested one (used automatically whenever the newer one is unreachable)
          and a separate, newer decision engine that&apos;s the default live path. Because they
          were built at different times, the newer one was missing a few real safety checks the
          older one already had — this work closed two of the most important ones.
        </p>
      </Section>

      <EntryCard title="Risk-Off Regime Hard Block" tag="fixed">
        The older system flatly refuses to open ANY new position while the overall market is in
        a {'"'}risk-off{'"'} regime — a rule that exists because a real check of past trades
        found that every single one entered during risk-off had lost money. The newer decision
        engine only had a softer version of this rule (demanding a better risk/reward ratio, not
        an outright block) — meaning it could still approve an entry the older system would have
        categorically refused. Now both systems agree.
      </EntryCard>

      <EntryCard title="Equity-Floor Circuit Breaker" tag="fixed">
        The older system also has a {'"'}stop digging{'"'} rule: once a portfolio&apos;s value
        drops below 80% of what it started with, all new entries are suspended until it
        recovers — the same discipline a careful human trader applies to a badly damaged
        account. The newer decision engine had no equivalent at all. Fixed the same way.
      </EntryCard>

      <Callout tone="info" title="Why this matters even though it's still 'paper' trading">
        Both of these gates exist specifically to prevent compounding a bad situation — entering
        more risk exactly when the market or the account is already in trouble. Closing this gap
        means the newer scoring system (the one actually driving live decisions by default)
        can&apos;t silently skip a safety rule the older, more battle-tested system already
        enforces.
      </Callout>
    </>
  );
}

// ── Tab: Silent-Failure Audit ─────────────────────────────────────────────────────────────

function ObservabilityTab() {
  return (
    <>
      <Section title="Finding failures that were happening with zero trace">
        <p>
          A piece of code that {'"'}fails silently{'"'} — catches an error and just moves on
          with no log, no warning, nothing — is dangerous specifically because you have no way
          of knowing it&apos;s happening. This pass went through every one of these in the core
          paper-trading file and checked each one individually: is this genuinely safe to stay
          silent, or is something real getting lost?
        </p>
      </Section>

      <EntryCard title="Broker Fill Reconciliation" tag="fixed — real risk">
        When a real broker order to exit a position fills, this app updates its own records
        (exit price, profit/loss, cash) to match what actually happened at the broker. That
        reconciliation step could previously fail completely silently — meaning the broker
        genuinely filled the trade correctly, but this app&apos;s own records could quietly go
        stale/wrong with zero indication anything went wrong. Now logged clearly and flagged on
        the trade itself.
      </EntryCard>

      <EntryCard title="Position-Scaling Shadow Data Loss" tag="fixed — real bug">
        This was a genuine data-loss bug, not just a missing log. If a temporary hiccup
        prevented the system from saving a shadow-mode verdict, the code would still mark it
        {' '}{'"'}done{'"'} and remove it from the pending queue anyway — permanently losing that
        verdict while also quietly inflating the reported accuracy number with an outcome that
        was never actually recorded. Fixed to correctly retry it instead.
      </EntryCard>

      <Callout tone="good" title="Most of what was found was genuinely fine">
        Out of 18 silent failure points checked in detail, only these 2 were real risks worth
        fixing outright (plus 2 more that got a visibility-only warning log added). The other 14
        were confirmed to be correctly-designed {'"'}best effort{'"'} behavior — things like a
        cache refresh that just tries again next cycle if it misses once, where adding logging
        wouldn&apos;t reduce any real risk, only add noise.
      </Callout>
    </>
  );
}

// ── Tab: Position Scaling ────────────────────────────────────────────────────────────────

function PositionScalingTab() {
  return (
    <>
      <Section title="A fully-built feature that had never actually run">
        <p>
          While researching what to work on next, a deep dive into the {'"'}position
          scaling{'"'} system (an earlier project: intelligently add to a losing position, but
          only if independent evidence says the original reason you bought it is still true —
          not just because the price dropped) turned up something important.
        </p>
      </Section>

      <EntryCard title="Shadow Mode Was Never Turned On" tag="fixed">
        <p style={{ marginBottom: 10 }}>
          This entire system had been built and tested against historical data (1,213 real
          historical scenarios, an 89% accuracy rate) — but the {'"'}shadow mode{'"'} step,
          designed specifically to watch it run against REAL, live trades before ever trusting
          it with real money, had literally never been switched on for a single real portfolio.
          There was no way to even turn it on through the app — the setting existed in the code
          but was never wired into the actual settings screen.
        </p>
        <p>
          Fixed: added a real toggle to each portfolio&apos;s Config panel (Position Scaling
          section), with proper validation so a typo or unsupported value can&apos;t silently
          save and do nothing. Then turned Shadow Mode ON for all 5 of your real portfolios.
        </p>
      </EntryCard>

      <Callout tone="info" title="What this means going forward">
        Nothing about how your portfolios actually trade has changed yet — shadow mode never
        places a real order or touches cash, it only watches and records what it WOULD have
        done every time a real position pulls back. You can now see this data building up on
        the Paper Portfolio page&apos;s Position Scaling tab. Once enough real verdicts
        accumulate and the real-world hit rate confirms the historical numbers, actually letting
        this system control real position sizing becomes a genuinely data-backed decision —
        deliberately not built yet, on purpose.
      </Callout>
    </>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SessionChangelogPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [tab, setTab] = useState<Tab>(() => tabFromQuery(router.query.tab));

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    setAuthed(true);
  }, [router]);

  useEffect(() => {
    if (router.isReady) setTab(tabFromQuery(router.query.tab));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router.isReady, router.query.tab]);

  if (!authed) return null;

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '24px 0 60px' }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: '#e2e8f0', marginBottom: 6 }}>
          Session Changelog
        </h1>
        <p style={{ fontSize: 13, color: '#64748b', maxWidth: 680 }}>
          What was built, fixed, and investigated in this working session — in plain language,
          with the real evidence behind each fix.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid #1f2937', marginBottom: 28, flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => { setTab(t.key); router.replace({ pathname: '/session-changelog', query: { tab: t.key } }, undefined, { shallow: true }); }}
            style={{
              background: 'none', border: 'none', cursor: 'pointer', padding: '10px 16px', fontSize: 13, fontWeight: 500,
              color: tab === t.key ? '#f9fafb' : '#6b7280',
              borderBottom: tab === t.key ? '2px solid #6d28d9' : '2px solid transparent',
              whiteSpace: 'nowrap',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'overview' && <OverviewTab />}
      {tab === 'alerts' && <AlertsTab />}
      {tab === 'signalbugs' && <SignalBugsTab />}
      {tab === 'decisionengine' && <DecisionEngineTab />}
      {tab === 'observability' && <ObservabilityTab />}
      {tab === 'positionscaling' && <PositionScalingTab />}
    </div>
  );
}
