// @vitest-environment jsdom
import {beforeEach,it,expect,vi} from 'vitest';
import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LeadJourney from './lead-journey';
const record={id:'80000000-0000-4000-8000-000000000001',version:1,journey_stage:'new',contact_preference:'unspecified',contact_permission:'unknown',handoff_state:'none'};
beforeEach(()=>sessionStorage.clear());
it('loads real API activity and submits a versioned owner state change',async()=>{
 const user=userEvent.setup();const onSaved=vi.fn(async()=>{});
 const fetch=vi.fn(async(_input:unknown,init?:RequestInit)=>Response.json(init?.method?{record:{...record,version:2}}:{items:[],total:0,page:1,page_size:20}));vi.stubGlobal('fetch',fetch);
 render(<LeadJourney record={record} onSaved={onSaved}/>);
 expect(await screen.findByText(/0 recorded changes/)).toBeVisible();
 await user.selectOptions(screen.getByLabelText('journey stage'),'qualifying');
 await user.selectOptions(screen.getByLabelText('contact permission'),'opted_out');
 await user.type(screen.getByLabelText('Reason / customer permission evidence'),'Fictional opt-out request');
 await user.click(screen.getByRole('button',{name:'Save journey controls'}));
 await waitFor(()=>expect(onSaved).toHaveBeenCalledOnce());
 const call=fetch.mock.calls.find(([,init])=>init?.method==='POST')!;
 expect(JSON.parse(String(call[1]?.body))).toMatchObject({expected_version:1,journey_stage:'qualifying',contact_permission:'opted_out',reason:'Fictional opt-out request'});
});
it('shows backend transition errors without claiming a save',async()=>{
 const user=userEvent.setup();const onSaved=vi.fn(async()=>{});
 vi.stubGlobal('fetch',vi.fn(async(_input:unknown,init?:RequestInit)=>init?.method?Response.json({detail:'Invalid journey stage transition.'},{status:409}):Response.json({items:[],total:0,page:1,page_size:20})));
 render(<LeadJourney record={record} onSaved={onSaved}/>);
 await user.selectOptions(screen.getByLabelText('journey stage'),'qualified');await user.type(screen.getByLabelText('Reason / customer permission evidence'),'Fictional');await user.click(screen.getByRole('button',{name:'Save journey controls'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Invalid journey stage transition.');expect(onSaved).not.toHaveBeenCalled();
});
