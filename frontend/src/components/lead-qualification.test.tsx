// @vitest-environment jsdom
import {beforeEach,it,expect,vi} from 'vitest';
import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LeadQualification from './lead-qualification';
const ID='80000000-0000-4000-8000-000000000001';
const record={id:ID,version:3,journey_stage:'qualifying',archived_at:null};
const ready={matching_ready:true,qualification_ready:true,matching_missing_fields:[],missing_fields:[],invalid_fields:[],qualification_current:false,needs_review:false};
const result={lead_id:ID,lead_version:3,checked_at:'2026-10-29T12:00:00Z',matching_ready:true,missing_fields:[],invalid_fields:[],criteria_snapshot:{budget_max:'200.00'},truncated:false,criteria:'Fixed constraints',matches:[]};
beforeEach(()=>{sessionStorage.clear();vi.unstubAllGlobals();});

it('shows incomplete answers without inventing preferences or allowing confirmation',()=>{
 render(<LeadQualification record={record} qualification={{...ready,qualification_ready:false,missing_fields:['purchase_timeframe']}} onSaved={async()=>{}}/>);
 expect(screen.getByText('Qualification is incomplete.')).toBeVisible();
 expect(screen.getByText('Missing answers: purchase timeframe.')).toBeVisible();
 expect(screen.getByRole('button',{name:'Confirm qualification'})).toBeDisabled();
});

it('submits an explicit versioned owner confirmation and applies only the confirmed record',async()=>{
 const user=userEvent.setup();const onSaved=vi.fn(async()=>{});
 const fetch=vi.fn<(input:unknown,init?:RequestInit)=>Promise<Response>>(async()=>Response.json({record:{...record,version:4,journey_stage:'qualified'}}));vi.stubGlobal('fetch',fetch);
 render(<LeadQualification record={record} qualification={ready} onSaved={onSaved}/>);
 await user.type(screen.getByLabelText('Qualification review reason'),'Fictional requirements reviewed');
 await user.click(screen.getByRole('button',{name:'Confirm qualification'}));
 await waitFor(()=>expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({version:4,journey_stage:'qualified'})));
 expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toMatchObject({expected_version:3,reason:'Fictional requirements reviewed'});
});

it('keeps failed confirmation visible and does not claim qualification',async()=>{
 const user=userEvent.setup();const onSaved=vi.fn(async()=>{});
 vi.stubGlobal('fetch',vi.fn(async()=>Response.json({message:'Record changed. Reload before qualifying.'},{status:409})));
 render(<LeadQualification record={record} qualification={ready} onSaved={onSaved}/>);
 await user.type(screen.getByLabelText('Qualification review reason'),'Fictional review');await user.click(screen.getByRole('button',{name:'Confirm qualification'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Record changed.');expect(onSaved).not.toHaveBeenCalled();
});

it('distinguishes incomplete matching from complete criteria with no inventory',async()=>{
 const user=userEvent.setup();const fetch=vi.fn().mockResolvedValueOnce(Response.json({...result,matching_ready:false,missing_fields:['bedrooms']})).mockResolvedValueOnce(Response.json(result));vi.stubGlobal('fetch',fetch);
 render(<LeadQualification record={record} qualification={ready} onSaved={async()=>{}}/>);
 await user.click(screen.getByRole('button',{name:'Check property matches'}));expect(await screen.findByText('Matching is incomplete. Review: bedrooms.')).toBeVisible();
 expect(screen.queryByText('No eligible properties meet the stated constraints.')).not.toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'Check property matches'}));expect(await screen.findByText('No eligible properties meet the stated constraints.')).toBeVisible();
});

it('renders explained matches and hides them after requirements change',async()=>{
 const user=userEvent.setup();vi.stubGlobal('fetch',vi.fn(async()=>Response.json({...result,matches:[{id:ID,title:'Fictional apartment',price:'150',bedrooms:2,score:100,version:1,checks:[{criterion:'minimum_bedrooms',requested:2,actual:2,passed:true}]}]})));
 const view=render(<LeadQualification record={record} qualification={ready} onSaved={async()=>{}}/>);
 await user.click(screen.getByRole('button',{name:'Check property matches'}));expect(await screen.findByRole('link',{name:'Fictional apartment'})).toHaveAttribute('href','/properties/'+ID);
 expect(screen.getByText(/minimum bedrooms: requested 2; property 2/)).toBeVisible();
 view.rerender(<LeadQualification record={{...record,version:4}} qualification={ready} onSaved={async()=>{}}/>);
 expect(screen.getByText('Requirements changed. Check property matches again.')).toBeVisible();expect(screen.queryByRole('link',{name:'Fictional apartment'})).not.toBeInTheDocument();
});

it('disables qualification and matches on archived leads',()=>{
 render(<LeadQualification record={{...record,archived_at:'2026-10-29T12:00:00Z'}} qualification={ready} onSaved={async()=>{}}/>);
 expect(screen.getByRole('button',{name:'Confirm qualification'})).toBeDisabled();expect(screen.getByRole('button',{name:'Check property matches'})).toBeDisabled();
});


it('does not show another lead matches at the same record version',async()=>{
 const user=userEvent.setup();vi.stubGlobal('fetch',vi.fn(async()=>Response.json({...result,matches:[{id:ID,title:'Fictional original match',price:'150',bedrooms:2,score:100,version:1,checks:[]}]})));
 const view=render(<LeadQualification record={record} qualification={ready} onSaved={async()=>{}}/>);
 await user.click(screen.getByRole('button',{name:'Check property matches'}));expect(await screen.findByRole('link',{name:'Fictional original match'})).toBeVisible();
 view.rerender(<LeadQualification record={{...record,id:'80000000-0000-4000-8000-000000000002'}} qualification={ready} onSaved={async()=>{}}/>);
 expect(screen.queryByRole('link',{name:'Fictional original match'})).not.toBeInTheDocument();
});
