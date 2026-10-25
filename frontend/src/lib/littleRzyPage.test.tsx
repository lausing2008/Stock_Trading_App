// @vitest-environment jsdom
import React from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SWRConfig } from 'swr';
const m = vi.hoisted(() => ({ replace: vi.fn(), role: 'admin' as string | null,
  api: { rzyAdminOverview: vi.fn(), rzyDigest: vi.fn(), rzyPaper: vi.fn() } }));
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
beforeEach(() => { m.role = 'admin'; m.replace.mockReset(); Object.values(m.api).forEach(f => f.mockReset()); m.api.rzyAdminOverview.mockResolvedValue(overview); m.api.rzyDigest.mockResolvedValue(digest); m.api.rzyPaper.mockResolvedValue(paper); });
afterEach(cleanup);
const view = () => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}><Page /></SWRConfig>);
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
