// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const api = vi.hoisted(() => ({ getRiskDeliverySchedule: vi.fn(), setRiskDeliverySchedule: vi.fn() }));
vi.mock('@/lib/api', () => ({ api }));
import RiskDeliveryScheduleCard from './RiskDeliveryScheduleCard';
beforeEach(() => { Object.values(api).forEach(m => m.mockReset()); api.getRiskDeliverySchedule.mockResolvedValue({ enabled: false, timezone: 'UTC', start: '22:00', end: '07:00' }); });
afterEach(cleanup);
it('saves only the account schedule after confirmation, without enrolling or enabling delivery', async () => {
  api.setRiskDeliverySchedule.mockImplementation(async x => x);
  render(<RiskDeliveryScheduleCard />); await screen.findByLabelText('Quiet-hour time zone');
  expect(api.setRiskDeliverySchedule).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.change(screen.getByLabelText('Quiet-hour time zone'), { target: { value: 'America/Los_Angeles' } });
  fireEvent.click(screen.getByText('Save quiet hours')); await screen.findByRole('status');
  expect(api.setRiskDeliverySchedule).toHaveBeenCalledWith({ enabled: true, timezone: 'America/Los_Angeles', start: '22:00', end: '07:00' });
  expect(screen.getByText(/does not enable delivery or subscribe/)).toBeTruthy();
});
it('shows load failures and exposes no save action with unknown preferences', async () => {
  api.getRiskDeliverySchedule.mockRejectedValue(new Error('offline')); render(<RiskDeliveryScheduleCard />);
  await screen.findByRole('alert'); expect(screen.queryByText('Save quiet hours')).toBeNull();
});
it('shows a failed save and permits retry instead of reporting success', async () => {
  api.setRiskDeliverySchedule.mockRejectedValue(new Error('Could not save'));
  render(<RiskDeliveryScheduleCard />); await screen.findByLabelText('Quiet-hour start');
  fireEvent.click(screen.getByText('Save quiet hours')); await screen.findByRole('alert');
  await waitFor(() => expect((screen.getByText('Save quiet hours') as HTMLButtonElement).disabled).toBe(false));
  expect(screen.queryByRole('status')).toBeNull();
});
