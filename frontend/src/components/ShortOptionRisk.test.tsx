// @vitest-environment jsdom
import React from 'react';
import {afterEach,expect,it} from 'vitest';
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import ShortOptionRisk from './ShortOptionRisk';
import {coveredCall,securedPut} from '@/testFixtures/shortOptionRisk';
afterEach(cleanup);

it('explains stock-zero risk and decomposes a covered-call downside scenario',()=>{
  render(<ShortOptionRisk strategy={coveredCall} referenceStock={418.19}/>);
  const region=screen.getByRole('region',{name:'Covered Call expiry risk'});
  expect(region.textContent).toContain('$41,711.00 loss');
  expect(region.textContent).toContain('does not stop the shares losing value');
  fireEvent.click(within(region).getByText('Why the loss is large — expiry scenarios'));
  fireEvent.change(within(region).getByLabelText('Hypothetical stock price at expiry ($)'),{target:{value:'400'}});
  expect(within(region).getByRole('status').textContent).toContain('total −$1,711.00');
  expect(within(region).getByText(/not your stock purchase cost/)).toBeTruthy();
  expect(within(region).getByRole('region',{name:'Covered Call scenario table'}).getAttribute('tabindex')).toBe('0');
});
it('shows the cash obligation and does not treat cash-secured as loss protection',()=>{
  render(<ShortOptionRisk strategy={securedPut} referenceStock={418.19}/>);
  const region=screen.getByRole('region',{name:'Cash-Secured Put expiry risk'});
  expect(region.textContent).toContain('$39,043.00 loss');
  expect(region.textContent).toContain('funded, not protected');
  fireEvent.click(within(region).getByText('Why the loss is large — expiry scenarios'));
  fireEvent.change(within(region).getByLabelText('Hypothetical stock price at expiry ($)'),{target:{value:'350'}});
  expect(within(region).getByRole('status').textContent).toContain('pay $40,500.00 for 100 shares');
  expect(within(region).getByRole('status').textContent).toContain('total −$4,043.00');
});
it('handles zero, invalid input and the exact strike without promising assignment',()=>{
  render(<ShortOptionRisk strategy={coveredCall} referenceStock={418.19}/>);
  fireEvent.click(screen.getByText('Why the loss is large — expiry scenarios'));
  const input=screen.getByLabelText('Hypothetical stock price at expiry ($)');
  fireEvent.change(input,{target:{value:'0'}});expect(screen.getByRole('status').textContent).toContain('total −$41,711.00');
  fireEvent.change(input,{target:{value:'-1'}});expect(screen.getByRole('alert').textContent).toContain('$0 or more');
  expect(screen.queryByRole('status')).toBeNull();
  fireEvent.change(input,{target:{value:'480'}});expect(screen.getByRole('status').textContent).toContain('assignment is uncertain');
  expect(screen.getByText(/Touching a strike does not automatically/)).toBeTruthy();
});
it('updates scenarios when a different contract is selected while keeping the local what-if price',()=>{
  const view=render(<ShortOptionRisk strategy={coveredCall} referenceStock={418.19}/>);
  fireEvent.click(screen.getByText('Why the loss is large — expiry scenarios'));
  fireEvent.change(screen.getByLabelText('Hypothetical stock price at expiry ($)'),{target:{value:'500'}});
  expect(screen.getByRole('status').textContent).toContain('total +$6,289.00');
  const next={...coveredCall,max_profit_per_contract:8289,legs:[{...coveredCall.legs[0],strike:500}]};
  view.rerender(<ShortOptionRisk strategy={next} referenceStock={418.19}/>);
  expect(screen.getByRole('status').textContent).toContain('total +$8,289.00');
});
it('does not invent an illustration for unsupported or inconsistent payoff records',()=>{
  render(<ShortOptionRisk strategy={{...coveredCall,max_loss_per_contract:100}} referenceStock={418.19}/>);
  expect(screen.getByText(/reported payoff bounds must agree/)).toBeTruthy();
  expect(screen.queryByRole('table')).toBeNull();
});
