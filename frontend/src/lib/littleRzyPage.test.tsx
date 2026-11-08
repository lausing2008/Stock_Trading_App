// @vitest-environment jsdom
import React from 'react';
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SWRConfig } from 'swr';
const m = vi.hoisted(() => ({ replace: vi.fn(), role: 'admin' as string | null,
  api: { rzyAdminOverview: vi.fn(), rzyDigest: vi.fn(), rzyPaper: vi.fn(), rzyEvaluation: vi.fn() } }));
vi.mock('next/router', () => ({ useRouter: () => ({ replace: m.replace }) }));
vi.mock('@/lib/auth', () => ({ getSession: () => (m.role ? { username: 'lausing', role: m.role } : null) }));
vi.mock('@/lib/api', () => ({ api: m.api }));
import Page from '@/pages/little-rzy';
const overview = { strategy_version: 'LITTLE_RZY_INSPIRED_V1', config_hash: 'abc', generated_at: '2026-10-21T15:00:00',
  positioning: 'Research overlay and watch alert. Not a validated trade recommendation; paper execution is disabled.',
  scans: { recent_session_scans: 90, duration_seconds: { median: 41, p95: 160, max: 200 }, too_late_to_mail: 12, queued: 7,
    latest: [{ scan_time: '2026-10-21T15:00:00', mode: 'session', duration_seconds: 41.2, streams: 342, statuses: { research_only: 330, restatement_pending: 12 }, new_emailable_events: 3, too_late_to_mail: 1, queued: 2 }] },
  streams: { total: 342, by_status: { research_only: 330, restatement_pending: 12 }, by_timeframe_status: { '5m': { research_only: 100, restatement_pending: 12 } }, oldest_snapshot: '2026-10-21T14:59:00', newest_snapshot: '2026-10-21T15:00:00' },
  rebuilds_last_20h: { total: 40, by_reason: { stored_completed_bar_changed: 40 } },
  bar_revisions_last_20h: { total: 50, completed: { cross_provider: 30, same_provider: 5, previous_provider_unknown: 2 }, forming: { same_provider: 13 } },
  alerts: { states: { 'little_rzy:accepted': 2, 'little_rzy:expired': 1 }, recent: [{ alert_type: 'little_rzy', state: 'expired', subject: 'RZY research · Bullish setup triggered · MU · 5m', created_at: '2026-10-21T14:00:00', accepted_at: null, expires_at: null, terminal_reason: null }] } };
const digest = { available: true, snapshot_from: '2026-10-21T14:59:00', snapshot_to: '2026-10-21T15:00:00', snapshot_age_minutes: 1, streams_with_snapshot: 342, streams_expected: 342,
  candidates: [{ symbol: 'NVDA', direction: 'long', timeframe: '15m', target: 210, entry_reference: 200, provisional_stop: 195, planned_reward_risk: 2, guidance: 'g' }],
  exclusions: [{ symbol: 'MU', timeframe: '15m', reason: 'stale_15m_aggregate' }], note: 'n', total_candidates: 1 };
const paper = { note: 'n', provenance: 'Current configuration only.', positions: [
  { symbol: 'ZS', timeframe: '5m', state: 'CLOSED', cancelled: true, reason: 'data_basis_or_freshness_unverified,actual_fill_reward_risk_or_gap', generation: 3 },
  { symbol: 'SOXL', timeframe: '5m', state: 'CLOSED', cancelled: true, reason: 'data_basis_or_freshness_unverified,shortability_or_borrow_cost_unverified', generation: 1 }] };
const noRuns = { strategy_version: 'V', config_hash: 'abc', positioning: 'p', latest: null, runs: [], forward_journal: { by_cohort: {}, note: 'Live simulated fills are blocked.' } };
const stats = (trades: number, edge: number, verdict: string) => ({ trades, symbols: 40, win_rate: .41, mean_r: -.09, edge_vs_control_r: edge,
  edge_ci95_by_symbol: [edge - .07, edge + .07] as [number, number], mean_mfe_r: .8, mean_mae_r: .9, reached_1r_rate: .3, target_exit_rate: .08,
  mean_cost_r: .1, exits: { line_invalidation: 600, stop: 50, target: 40 }, avg_holding_bars: 4.2, max_drawdown_r: 70, verdict });
const evaluation = { ...noRuns,
  latest: { run_id: 'abcdef1234567890', created_at: '2026-11-08T16:00:00', snapshot_exported_at: '2026-11-08T15:00:00', analysis_version: 'RZY_EVALUATION_V2',
    git_revision: '0123456789ab', git_dirty: false, config_hash: 'abc', current_config: true, trades: 812, edge_vs_control_r: .004, verdict: 'no measurable edge vs control',
    summary: { analysis_version: 'RZY_EVALUATION_V2', unseen_after: '2026-10-20T14:04:54+00:00', snapshot: { exported_at: '2026-11-08T15:00:00', coverage: { '5m': { symbols: 150, bars: 9, first: '2026-07-21T13:30:00+00:00', last: '2026-11-06T20:55:00+00:00' } } },
      headline: { all_data: 'no measurable edge', unseen: 'insufficient sample', daily_alignment: 'no measurable difference' },
      hypotheses: [
        { id: 'H3', role: 'primary', kind: 'difference', question: 'Do intraday trades aligned with the daily trend have a larger edge?', groups: ['aligned with daily', 'not aligned'], trades: [300, 280], edge_r: [.01, -.02], difference_r: .03, ci95: [-.05, .11], verdict: 'no measurable difference' },
        { id: 'H2', role: 'secondary', kind: 'edge', question: 'Unseen sessions edge?', trades: 22, edge_r: .05, ci95: null, verdict: 'insufficient sample' }],
      scorecard: { cells_tested: 60, better: 2, worse: 1, expected_by_chance_each_side: 1.5, reading: 'Treat an isolated one as a lead.', better_cells: [{ slice: 'time_of_day', cell: '12:00-14:00', trades: 90, edge_r: .2 }], worse_cells: [] },
      baseline: { overall: stats(812, .004, 'no measurable edge vs control'), long_only: stats(400, .01, 'no measurable edge vs control'),
        by_htf_daily: { aligned: stats(300, .01, 'no measurable edge vs control'), opposed: stats(150, -.02, 'no measurable edge vs control'), not_applicable: stats(80, 0, 'x') },
        by_period: { seen_by_v1: stats(790, .003, 'no measurable edge vs control'), 'unseen_after_2026-10-20': stats(22, .05, 'insufficient sample (<30 trades)') } },
      variants: [{ name: 'baseline', note: 'The live rules.', overall: stats(812, .004, 'no measurable edge vs control') }] } },
  runs: [{ run_id: 'abcdef1234567890', created_at: '2026-11-08T16:00:00', snapshot_exported_at: '2026-11-08T15:00:00', analysis_version: 'RZY_EVALUATION_V2', git_revision: '0123', git_dirty: false, config_hash: 'abc', current_config: true, trades: 812, edge_vs_control_r: .004, verdict: 'no measurable edge vs control' },
    { run_id: 'old', created_at: '2026-10-20T16:00:00', snapshot_exported_at: '2026-10-20T14:04:54', analysis_version: 'RZY_EVALUATION_V2', git_revision: '07c8', git_dirty: false, config_hash: 'abc', current_config: true, trades: 692, edge_vs_control_r: .001, verdict: 'no measurable edge vs control' }] };
beforeEach(() => { m.role = 'admin'; m.replace.mockReset(); Object.values(m.api).forEach(f => f.mockReset()); m.api.rzyAdminOverview.mockResolvedValue(overview); m.api.rzyDigest.mockResolvedValue(digest); m.api.rzyPaper.mockResolvedValue(paper); m.api.rzyEvaluation.mockResolvedValue(noRuns); });
afterEach(cleanup);
const view = () => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}><Page /></SWRConfig>);
it('labels write counts and missing observation timing without inventing zero delay', async () => {
  m.api.rzyAdminOverview.mockResolvedValue({ ...overview, monitoring: {
    readiness: 'research_only', execution_ready: false, blockers: [], note: 'Observed writes are not provider finality.', streams: [],
    by_timeframe: { '5m': { trigger_occurrences: 1, invalid_timestamps: 0, too_late_to_queue: 0,
      post_close_write_missing: 1, post_close_write_timestamp_excluded: 0, receipt_outcomes: { not_queued: 1 },
      seconds_from_close: { observed_post_close_write: { samples: 0, median: null, p95: null, max: null } },
      segments_seconds: { observed_write_to_detection: { samples: 0, median: null, p95: null, max: null } } } }
  }, ingest_observations: [{ session_day: '2026-10-23', session_kind: 'REGULAR', market: 'US', timeframe: '5m', provider: 'fixture',
    batch_session_groups: 1, received: 4, duplicate_dropped: 1, off_grid_rejected: 1, authority_rejected: 1, written: 1 }] });
  view();
  await screen.findByText('Received, rejected and written bars (last 20 hours)');
  expect(screen.getByText(/including repeated writes of existing bars/)).toBeTruthy();
  expect(screen.getByText(/Post-close write evidence: 1 missing, 0 excluded/)).toBeTruthy();
  const row = screen.getByText('5m / fixture').closest('tr');
  expect(Array.from(row?.querySelectorAll('td') ?? []).map(c => c.textContent)).toEqual(['2026-10-23', 'US / REGULAR', '5m / fixture', '4', '1', '1', '1', '1']);
  expect(screen.getAllByText('—s').length).toBeGreaterThan(0);
});
it('explains every section: positioning, digest exclusions, alerts, data health, scans and cancelled paper entries', async () => {
  view();
  await screen.findByText('Research overlay and watch alert. Not a validated trade recommendation; paper execution is disabled.');
  expect(await screen.findByText('NVDA')).toBeTruthy();
  expect(screen.getByText(/1-hour|15-minute bar that should be complete is missing/)).toBeTruthy();
  expect(screen.getByText(/no longer fresh by the time it could be delivered/)).toBeTruthy();
  expect(screen.getByText(/cross_provider: a different data provider rewrote the bar/)).toBeTruthy();
  expect(screen.getByText('What “CLOSED (cancelled)” means')).toBeTruthy();
  expect(screen.getByText(/cancelled before it opens/)).toBeTruthy();
  expect(screen.getAllByText(/gapped past the stop or target/).length).toBeGreaterThan(0);
  expect(screen.getByText('330 / 342')).toBeTruthy();
});
it('redirects non-admins away and loads nothing', async () => {
  m.role = 'user'; view();
  await new Promise(r => setTimeout(r, 0));
  expect(m.replace).toHaveBeenCalledWith('/');
  expect(m.api.rzyAdminOverview).not.toHaveBeenCalled(); expect(m.api.rzyPaper).not.toHaveBeenCalled();
});
it('says the preview is not available yet when no snapshot exists', async () => {
  m.api.rzyDigest.mockResolvedValue({ available: false, candidates: [], exclusions: [], note: 'n', message: 'Preview not available yet: no scan snapshot exists for your watchlist.' });
  view(); expect(await screen.findByText(/Preview not available yet/)).toBeTruthy();
});

it('distinguishes processing completion from the input cutoff and excludes unknown cutoffs', async () => {
  m.api.rzyDigest.mockResolvedValue({ ...digest, data_cutoff_from: '2026-10-21T14:50:00', data_cutoff_to: '2026-10-21T14:51:00', streams_unknown_cutoff: 1 });
  view();
  expect(await screen.findByText(/Input data cutoffs:/)).toBeTruthy();
  expect(screen.getByText(/they do not make older inputs current/)).toBeTruthy();
  expect(screen.getByText(/unknown data cutoff and are excluded/)).toBeTruthy();
});

it('shows latency denominators, execution blockers and durable performance without inventing wins', async () => {
  m.api.rzyAdminOverview.mockResolvedValue({ ...overview, monitoring: {
    execution_ready: false, readiness: 'research_only', blockers: ['Provider timing unverified'], note: 'Acceptance is not inbox delivery.',
    by_timeframe: { '5m': { trigger_occurrences: 4, invalid_timestamps: 0, receipt_outcomes: { not_queued: 2 },
      seconds_from_close: { detection: { samples: 4, median: 300, p95: 600, max: 600 }, provider_acceptance: { samples: 2, median: 350, p95: 400, max: 400 } } } },
    streams: [{ symbol: 'TEST', timeframe: '5m', status: 'stale', current: true, checked_at: '2026-10-21T15:00:00',
      data_as_of: '2026-10-21T14:50:00', expected_latest_close: '2026-10-21T14:55:00', latest_completed_bin_present: false, paper_blockers: ['execution_basis_unverified'] }]
  } });
  m.api.rzyPaper.mockResolvedValue({ ...paper, performance: {
    summary: { forward_observations: 2, cancelled: 2, unresolved: 0, closed_trades: 0, scored_trades: 0, unscorable_closed: 0,
      win_rate: null, net_expectancy_r: null, average_win_r: null, average_loss_r: null, realized_drawdown_r: null,
      replay_or_imported: 3, superseded: 1, conclusion: 'No demonstrated edge.' }, groups: {}, journal_total: 1,
    recording_since: '2026-10-25T12:00:00Z', units: 'Net R per one-share simulated trade.', coverage: 'Superseded generations excluded.',
    journal: [{ journal_id: 'j1', symbol: 'TEST', timeframe: '5m', generation: 1, observed_in: 'forward_observation', state: 'CLOSED',
      cancelled: true, superseded: false, direction: 'long', first_seen_at: '2026-10-25T12:00:00Z',
      lifecycle: [{ event_id: 'e1', event_type: 'ENTRY_CANCELLED', signal_time: '2026-10-25T12:00:00Z' }] }]
  } });
  view();
  await screen.findByText('Alert timing and readiness');
  expect(screen.getByText('Research only · execution is not ready.')).toBeTruthy();
  expect(screen.getAllByText('600s')).toHaveLength(2);
  expect(screen.getByText('missing')).toBeTruthy();
  expect(screen.getByText('Forward simulator performance')).toBeTruthy();
  expect(screen.getByText('Forward win rate').nextElementSibling?.textContent).toBe('—');
  expect(screen.getByText('Net expectancy').nextElementSibling?.textContent).toBe('— R');
  expect(screen.getByText(/ENTRY_CANCELLED ·/)).toBeTruthy();
});
it('explains what each timeframe measures and labels multi-timeframe entry use as untested', async () => {
  view();
  const reference = (await screen.findByText(/Reading the timeframes/)).closest('details') as HTMLElement;
  const rows = ['5m', '15m', '1h', '1d'].map(tf => within(reference).getAllByText(tf).find(e => e.closest('tr'))?.closest('tr'));
  expect(rows.every(Boolean)).toBe(true);
  expect(rows[0]?.textContent).toContain('35 min');
  expect(rows[2]?.textContent).toContain('too few');
  expect(screen.getByText(/This is a reading practice, not something the scanner does/)).toBeTruthy();
  expect(screen.getByText(/Keep the stop where the setup is proven wrong/)).toBeTruthy();
  const diagrams = screen.getAllByRole('img').map(e => e.getAttribute('aria-label') ?? '');
  expect(diagrams.some(l => /seven bars shaded/.test(l))).toBe(true);
  expect(diagrams.some(l => /pivots A, B and C/.test(l))).toBe(true);
  expect(diagrams.some(l => /if they disagree, skip/.test(l))).toBe(true);
});
it('opens with a beginner guide that defines bars, timeframes and streams before the technical reference', async () => {
  view();
  const guide = (await screen.findByText(/Start here: a beginner’s guide to Little RZY/)).closest('details') as HTMLElement;
  expect(guide.hasAttribute('open')).toBe(true);
  const reference = screen.getByText(/Reading the timeframes/).closest('details') as HTMLElement;
  expect(guide.compareDocumentPosition(reference) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  for (const id of ['rzy-what', 'rzy-bars', 'rzy-timeframes', 'rzy-streams', 'rzy-pattern', 'rzy-life', 'rzy-page', 'rzy-routine', 'rzy-evidence', 'rzy-glossary'])
    expect(guide.querySelector(`#${id}`)).toBeTruthy();
  // No 10-minute timeframe exists; the guide says so and lists the real four.
  expect(within(guide).getByText(/There is no 10-minute timeframe/)).toBeTruthy();
  const tfTable = within(guide).getByText('Bars per trading day').closest('table') as HTMLElement;
  expect(['5m', '15m', '1h', '1d'].every(tf => within(tfTable).getByText(tf))).toBe(true);
  // Stream statuses come from the shared reason table, so they cannot drift from Data health.
  expect(within(guide).getByText('research_only')).toBeTruthy();
  expect(within(guide).getByText(/no evidence that these signals beat random entry timing/)).toBeTruthy();
});

it('says when no evaluation has been published instead of showing empty results', async () => {
  view();
  expect(await screen.findByText(/No evaluation has been published yet/)).toBeTruthy();
});

it('shows the published evaluation: headlines, the primary question, plain-language slices and the luck check', async () => {
  m.api.rzyEvaluation.mockResolvedValue(evaluation);
  view();
  const section = (await screen.findByText('How good is Little RZY? Test results')).closest('details') as HTMLElement;
  expect(section.hasAttribute('open')).toBe(true);
  const s = within(section);
  expect(await s.findByText('Trading with the daily trend')).toBeTruthy();
  expect(s.getAllByText('no measurable difference').length).toBeGreaterThan(0);
  // The primary hypothesis is marked and its two groups are named.
  const h3 = s.getByText('H3').closest('tr') as HTMLElement;
  expect(within(h3).getByText('primary')).toBeTruthy();
  expect(within(h3).getByText(/aligned with daily vs not aligned/)).toBeTruthy();
  expect(within(h3).getByText('-0.050 to +0.110')).toBeTruthy();
  // Labels are translated; the not-applicable bucket is hidden from the alignment table.
  expect(s.getByText('With the trend')).toBeTruthy();
  expect(s.getByText('Against the trend')).toBeTruthy();
  expect(s.queryByText('Not applicable')).toBeNull();
  expect(s.getByText('Unseen sessions (after Oct 20)')).toBeTruthy();
  expect(s.getAllByText('Too few trades to tell.').length).toBeGreaterThan(0);
  expect(s.getByText(/60 groups with at least 30 trades were tested\. 2 looked better/)).toBeTruthy();
  expect(s.getByText(/about 1\.5 of each would be expected/)).toBeTruthy();
  expect(s.getByText(/Live simulated fills are blocked/)).toBeTruthy();
  expect(s.getByText('Earlier runs')).toBeTruthy();
  expect(s.getAllByText('+0.004').length).toBeGreaterThanOrEqual(3); // headline stat, variant row, run history
});

it('reports an unavailable evaluation instead of hiding it', async () => {
  m.api.rzyEvaluation.mockRejectedValue(new Error('boom'));
  view();
  expect(await screen.findByText(/Test results unavailable: boom/)).toBeTruthy();
});
