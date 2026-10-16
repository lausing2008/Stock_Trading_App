// @vitest-environment jsdom
import React from 'react';
import { cleanup,fireEvent,render,screen } from '@testing-library/react';
import { afterEach,expect,it,vi } from 'vitest';
const call=vi.hoisted(()=>vi.fn());
vi.mock('@/lib/api',()=>({api:{calibrationCandidates:call}}));
import CalibrationCandidateCard from './CalibrationCandidateCard';
afterEach(()=>{cleanup();call.mockReset();});
it('keeps unavailable evidence explicit and never suggests a winner',async()=>{
 call.mockResolvedValue({status:'insufficient_evidence',reason:'Need more eligible dates',mode:'verified_only',production_changed:false,candidates:[],evidence_counts:{verified:0,provisional:571}});
 render(<CalibrationCandidateCard/>);expect(call).not.toHaveBeenCalled();fireEvent.click(screen.getByText('Run comparison'));
 await screen.findByText('Verified observations only.');expect(call).toHaveBeenCalledWith(false);expect(screen.getByText(/verified: 0/)).toBeTruthy();
});
it('labels provisional comparisons and does not turn null errors into zero',async()=>{
 call.mockResolvedValue({status:'insufficient_chronological_training',reason:'No eligible paired rows',mode:'provisional_research',production_changed:false,candidates:[{candidate:'long_pooled',scope:'all_markets',forecast_count:0,paired_count:0,paired_brier:null}]});
 render(<CalibrationCandidateCard/>);fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(screen.getByText('Run comparison'));
 await screen.findByText('Not measurable');expect(call).toHaveBeenCalledWith(true);expect(screen.getByText(/results are not verified/)).toBeTruthy();
});
