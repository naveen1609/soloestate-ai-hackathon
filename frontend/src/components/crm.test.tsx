// @vitest-environment jsdom
import {beforeEach,expect,it,vi} from 'vitest';
import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CrmRecords,{RecordForm} from './crm-records';
import CrmOverview from './crm-overview';
import CrmAppointments from './crm-appointments';
import {useMutation} from '../lib/crm';

const {push}=vi.hoisted(()=>({push:vi.fn()}));
vi.mock('next/navigation',()=>({useRouter:()=>({push})}));
const ID='80000000-0000-4000-8000-000000000001';
const lead={id:ID,name:'Fictional CRM Lead',email:'crm@example.invalid',preferred_localities:['Demo'],budget_min:'100',budget_max:'200',bedrooms:2,status:'new',notes:'',version:1,archived_at:null};
beforeEach(()=>{sessionStorage.clear();window.history.replaceState({},'','/leads');push.mockClear();});
function backend(handler?:(path:string,init?:RequestInit)=>Response|Promise<Response>){
 const fetch=vi.fn(async(input:string|URL|Request,init?:RequestInit)=>{
  const path=String(input);if(path==='/api/session')return Response.json({authenticated:true});
  if(handler)return handler(path,init);
  return Response.json({record:lead});
 });vi.stubGlobal('fetch',fetch);return fetch;
}

it('does not fetch CRM records before owner authentication',async()=>{
 const fetch=vi.fn(async()=>Response.json({authenticated:false}));vi.stubGlobal('fetch',fetch);
 render(<CrmRecords entity="leads"/>);
 expect(await screen.findByText('Owner sign-in required')).toBeVisible();expect(fetch).toHaveBeenCalledTimes(1);
});

it('renders current business metrics from the summary API',async()=>{
 backend(()=>Response.json({metrics:{leads:12,new_leads:4,properties:7,upcoming_visits:3,pending_approvals:2},recent_leads:[lead],appointments:[],requests:[]}));
 render(<CrmOverview/>);
 expect(await screen.findByText('Fictional CRM Lead')).toBeVisible();
 expect(screen.getByText('Active leads').parentElement).toHaveTextContent('12');
 expect(screen.getByText('Upcoming visits').parentElement).toHaveTextContent('3');
});

it('fails closed on malformed business metrics',async()=>{
 backend(()=>Response.json({metrics:{leads:'invented'}}));render(<CrmOverview/>);
 expect(await screen.findByRole('alert')).toBeVisible();expect(screen.queryByText('Active leads')).not.toBeInTheDocument();
});

it('sends search, status and pagination through the CRM list endpoint',async()=>{
 const user=userEvent.setup();const fetch=backend(()=>Response.json({items:[lead],total:25,page:1,page_size:20}));
 render(<CrmRecords entity="leads"/>);expect(await screen.findByText('Fictional CRM Lead')).toBeVisible();
 await user.type(screen.getByLabelText('Search'),'Fictional');await user.selectOptions(screen.getByLabelText('Filter'),'qualified');
 await waitFor(()=>expect(fetch.mock.calls.some(([path])=>String(path).includes('status=qualified')&&String(path).includes('q=Fictional'))).toBe(true));
 await user.click(screen.getByRole('button',{name:'Next'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([path])=>String(path).includes('page=2'))).toBe(true));
});

it('creates a lead only after a confirmed backend response',async()=>{
 const user=userEvent.setup();const fetch=backend();render(<RecordForm entity="leads"/>);
 await user.type(screen.getByLabelText('Name'),'New fictional lead');await user.type(screen.getByLabelText('Email'),'new@example.invalid');
 await user.type(screen.getByLabelText('Preferred localities (comma separated)'),'Demo');await user.type(screen.getByLabelText('Minimum budget (INR)'),'100');await user.type(screen.getByLabelText('Maximum budget (INR)'),'200');await user.type(screen.getByLabelText('Bedrooms'),'2');
 await user.click(screen.getByRole('button',{name:'Save record'}));
 await waitFor(()=>expect(push).toHaveBeenCalledWith('/leads/'+ID));
 const request=fetch.mock.calls.find(([,init])=>init?.method==='POST');expect(JSON.parse(request![1]!.body as string)).toMatchObject({email:'new@example.invalid',bedrooms:2,preferred_localities:['Demo']});
});

it('edits a property using its expected version and deactivates without deletion',async()=>{
 const user=userEvent.setup();const fetch=backend();render(<RecordForm entity="properties" initial={{id:ID,title:'Demo',locality:'Demo',price:'150',bedrooms:2,description:'',active:true,version:3}}/>);
 await user.selectOptions(screen.getByLabelText('Inventory state'),'false');await user.click(screen.getByRole('button',{name:'Save record'}));
 await waitFor(()=>expect(push).toHaveBeenCalled());
 const request=fetch.mock.calls.find(([,init])=>init?.method==='PATCH');expect(JSON.parse(request![1]!.body as string)).toMatchObject({active:false,expected_version:3});
});

it('keeps stale edits visible instead of claiming a save',async()=>{
 const user=userEvent.setup();backend(()=>Response.json({message:'Record changed. Reload before editing.'},{status:409}));render(<RecordForm entity="properties" initial={{id:ID,title:'Demo',locality:'Demo',price:'150',bedrooms:2,active:true,version:1}}/>);
 await user.click(screen.getByRole('button',{name:'Save record'}));expect(await screen.findByText('Record changed. Reload before editing.')).toBeVisible();expect(push).not.toHaveBeenCalled();
});

function Mutation(){const action=useMutation('test-operation');return <><button onClick={()=>void action.execute('leads',{name:'Original'})}>Write</button>{action.error&&<p role="alert">{action.error}</p>}</>;}
it('prevents duplicate in-flight CRM submissions',async()=>{
 let resolve!:(value:Response)=>void;const fetch=vi.fn(()=>new Promise<Response>(r=>{resolve=r;}));vi.stubGlobal('fetch',fetch);render(<Mutation/>);
 fireEvent.click(screen.getByText('Write'));fireEvent.click(screen.getByText('Write'));expect(fetch).toHaveBeenCalledTimes(1);
 await act(async()=>resolve(Response.json({record:lead})));
});

it('retains the same request key after an ambiguous network result',async()=>{
 let writes=0;const bodies:unknown[]=[];
 backend((path,init)=>{if(path.includes('operations/'))return Response.json({message:'Not found'},{status:404});bodies.push(JSON.parse(init!.body as string));writes++;if(writes===1)throw new Error('Network response lost');return Response.json({record:lead});});
 render(<Mutation/>);fireEvent.click(screen.getByText('Write'));expect(await screen.findByRole('alert')).toHaveTextContent('Network response lost');fireEvent.click(screen.getByText('Write'));
 await waitFor(()=>expect(bodies).toHaveLength(2));expect(bodies[0]).toEqual(bodies[1]);
});

it('restores the exact pending action after a component reload',async()=>{
 const key='80000000-0000-4000-8000-000000000099';
 sessionStorage.setItem('crm:test-operation',JSON.stringify({path:'leads',method:'POST',body:{request_key:key,name:'Saved fictional lead'}}));
 const fetch=backend();render(<Mutation/>);fireEvent.click(screen.getByText('Write'));
 await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));
 expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual({request_key:key,name:'Saved fictional lead'});
});

it('allows the saved booking retry after a consumed approval and links its committed result',async()=>{
 window.history.replaceState({},'','/appointments/new?request='+ID);
 sessionStorage.setItem('crm:confirm:'+ID,JSON.stringify({path:'appointment-requests/'+ID+'/commit',method:'POST',body:{request_key:'saved-booking',expected_payload_hash:'a'.repeat(64)}}));
 backend(()=>Response.json({record:{id:ID,approval_status:'consumed',booking_id:ID,payload_hash:'a'.repeat(64),payload:{},replacement:{}}}));
 render(<CrmAppointments/>);
 expect(await screen.findByRole('button',{name:'Retry saved booking'})).toBeEnabled();
 expect(screen.getByRole('link',{name:'Open committed appointment'})).toHaveAttribute('href','/appointments/'+ID);
});

it('requires explicit owner confirmation before committing a prepared appointment',async()=>{
 const user=userEvent.setup();window.history.replaceState({},'','/appointments/new?request='+ID);
 const fetch=backend((path,init)=>Response.json({record:init?.method==='POST'?{id:ID,status:'confirmed'}:{id:ID,approval_id:ID,approval_status:'pending',payload_hash:'a'.repeat(64),payload:{recipient:'demo@example.invalid',property_title:'Demo home',price:'150',starts_at:'2034-01-02T10:00:00Z',ends_at:'2034-01-02T10:30:00Z'},replacement:{}}}));
 render(<CrmAppointments/>);expect(await screen.findByText('Review appointment request')).toBeVisible();
 expect(fetch.mock.calls.some(([,init])=>init?.method==='POST')).toBe(false);
 await user.click(screen.getByRole('button',{name:'Approve and book'}));
 await waitFor(()=>expect(push).toHaveBeenCalledWith('/appointments/'+ID));
 const request=fetch.mock.calls.find(([path])=>String(path).endsWith('/commit'));expect(JSON.parse(request![1]!.body as string).expected_payload_hash).toBe('a'.repeat(64));
});


it('uses the committed journey version for an immediate lead edit',async()=>{
 const user=userEvent.setup();let latest={...lead,journey_stage:'new',contact_preference:'unspecified',contact_permission:'unknown',handoff_state:'none'};
 const fetch=backend((path,init)=>{
  if(path.includes('/activities'))return Response.json({items:[],total:0,page:1,page_size:20});
  if(path.endsWith('/journey')&&init?.method==='POST'){latest={...latest,version:2,journey_stage:'qualifying'};return Response.json({record:latest});}
  return Response.json({record:latest});
 });
 render(<CrmRecords entity="leads" id={ID}/>);
 await user.selectOptions(await screen.findByLabelText('journey stage'),'qualifying');
 await user.type(screen.getByLabelText('Reason / customer permission evidence'),'Fictional qualification');await user.click(screen.getByRole('button',{name:'Save journey controls'}));
 await waitFor(()=>expect(screen.getByLabelText('Reason / customer permission evidence')).toHaveValue(''));
 await user.click(screen.getByRole('button',{name:'Edit record'}));await user.click(screen.getByRole('button',{name:'Save record'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='PATCH'&&JSON.parse(String(init.body)).expected_version===2)).toBe(true));
});


it('records a visit outcome with its expected version and shows the confirmed result',async()=>{
 const user=userEvent.setup();let record:Record<string,string|number|boolean|null>={id:ID,lead_id:ID,status:'confirmed',visit_outcome:null,outcome_note:null,followup_state:'none',followup_note:null,visit_result:'scheduled',has_replacement:false,version:1};
 const fetch=backend((path,init)=>{
  if(init?.method==='POST'&&String(path).includes('/outcome')){record={...record,visit_outcome:'completed',outcome_note:'Toured twice.',version:2,visit_result:'completed'};return Response.json({record});}
  return Response.json({record});
 });
 render(<CrmRecords entity="appointments" id={ID}/>);
 expect(await screen.findByText(/Visit result: scheduled/)).toBeVisible();
 await user.selectOptions(screen.getByLabelText('Visit outcome'),'completed');
 await user.type(screen.getByLabelText('Outcome note'),'Toured twice.');
 await user.type(screen.getByLabelText('Outcome reason'),'Fictional visit');
 await user.click(screen.getByRole('button',{name:'Record outcome'}));
 await waitFor(()=>expect(screen.getByText(/Visit result: completed/)).toBeVisible());
 expect(screen.queryByLabelText('Visit outcome')).not.toBeInTheDocument();
 const request=fetch.mock.calls.find(([url,init])=>init?.method==='POST'&&String(url).includes('/outcome'));
 expect(JSON.parse(String(request![1]?.body))).toMatchObject({expected_version:1,visit_outcome:'completed',outcome_note:'Toured twice.',reason:'Fictional visit'});
});

it('saves visit follow-up transitions with its expected version',async()=>{
 const user=userEvent.setup();let record={id:ID,status:'confirmed',visit_outcome:null,followup_state:'none',followup_note:null,visit_result:'scheduled',version:1};
 const fetch=backend((path,init)=>{
  if(init?.method==='POST'&&String(path).includes('/outcome')){const body=JSON.parse(String(init.body));record={...record,followup_state:body.followup_state??record.followup_state,followup_note:body.followup_note??record.followup_note,version:2};return Response.json({record});}
  return Response.json({record});
 });
 render(<CrmRecords entity="appointments" id={ID}/>);
 await user.selectOptions(await screen.findByLabelText('Follow-up state'),'pending');
 await user.type(screen.getByLabelText('Follow-up note'),'Send sheet.');
 await user.type(screen.getByLabelText('Follow-up reason'),'Fictional next action');
 await user.click(screen.getByRole('button',{name:'Save follow-up'}));
 await waitFor(()=>expect(screen.getByText(/Follow-up: pending/)).toBeVisible());
 const request=fetch.mock.calls.find(([,init])=>init?.method==='POST');
 expect(JSON.parse(String(request![1]?.body))).toMatchObject({expected_version:1,followup_state:'pending',followup_note:'Send sheet.'});
});

it('hides reschedule and cancel after an outcome and offers fresh rebooking after no-show',async()=>{
 backend(()=>Response.json({record:{id:ID,lead_id:ID,status:'confirmed',visit_outcome:'no_show',followup_state:'none',visit_result:'no_show',version:2}}));
 render(<CrmRecords entity="appointments" id={ID}/>);
 await screen.findByText(/Visit result: no_show/);
 expect(screen.queryByRole('link',{name:'Reschedule visit'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Confirm cancellation'})).not.toBeInTheDocument();
 expect(screen.getByRole('link',{name:'Book follow-up visit'})).toHaveAttribute('href','/appointments/new?lead_id='+ID);
 expect(screen.queryByLabelText('Visit outcome')).not.toBeInTheDocument();
 expect(screen.getByLabelText('Follow-up state')).toBeVisible();
});

it('filters the appointment list by recorded outcome and shows derived results',async()=>{
 const user=userEvent.setup();
 const rows=[{id:ID,lead_name:'Fictional CRM Lead',property_title:'Demo home',starts_at:'2034-01-02T10:00:00Z',status:'confirmed',visit_result:'completed'}];
 const fetch=backend(()=>Response.json({items:rows,total:1,page:1,page_size:20}));
 render(<CrmRecords entity="appointments"/>);
 expect(await screen.findByText('completed')).toBeVisible();
 await user.selectOptions(screen.getByLabelText('Filter'),'completed');
 await waitFor(()=>expect(fetch.mock.calls.some(([path])=>String(path).includes('outcome=completed'))).toBe(true));
});

it('saves a partial lead with explicit null requirements rather than zero defaults',async()=>{
 const user=userEvent.setup();const fetch=backend();render(<RecordForm entity="leads"/>);
 await user.type(screen.getByLabelText('Name'),'Fictional incomplete buyer');await user.type(screen.getByLabelText('Email'),'incomplete@example.invalid');
 await user.click(screen.getByRole('button',{name:'Save record'}));
 await waitFor(()=>expect(push).toHaveBeenCalled());
 const call=fetch.mock.calls.find(([,init])=>init?.method==='POST')!;
 expect(JSON.parse(String(call[1]?.body))).toMatchObject({budget_min:null,budget_max:null,preferred_localities:null,bedrooms:null,purchase_timeframe:null});
});
