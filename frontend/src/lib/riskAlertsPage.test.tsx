// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ history: vi.fn(), summary: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: { riskAlertHistory: mocks.history, riskAlertSummary: mocks.summary } }));
vi.mock('@/components/RiskDeliveryScheduleCard', () => ({ default: () => <div>Quiet hours</div> }));
import RiskAlertsPage from '@/pages/risk-alerts';
afterEach(() => { cleanup(); vi.clearAllMocks(); });
it('filters benchmark research through the real history contract without implying alert delivery', async () => {
  mocks.history.mockResolvedValue({ events: [{ id: 1, subject: 'SPY', scope: 'benchmark', rule: 'session_decline',
    change_pct: -2.5, step: 1, ladder: [2, 3, 5, 8], observed_to: '2026-10-23', evidence_status: 'provisional',
    delivery_mode: 'shadow', evidence: {}, receipt: null }] });
  mocks.summary.mockResolvedValue({ total: 1, by_delivery_mode: { shadow: 1 }, by_rule: {}, by_evidence_status: {} });
  render(<RiskAlertsPage />);
  await screen.findByText('Benchmark research · shadow only');
  fireEvent.change(screen.getByLabelText('Risk event scope'), { target: { value: 'benchmark' } });
  await waitFor(() => expect(mocks.history).toHaveBeenLastCalledWith({ days: 14, rule: undefined, scope: 'benchmark' }));
  expect(screen.getByText(/Summary counts cover all scopes/)).toBeTruthy();
  expect(screen.getByText(/benchmark observations are research in shadow mode/)).toBeTruthy();
});
