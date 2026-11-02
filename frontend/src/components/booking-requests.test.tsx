// @vitest-environment jsdom
import {beforeEach,expect,it,vi} from 'vitest';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import BookingRequests from './booking-requests';
const RID='80000000-0000-4000-8000-000000000001';
const LEAD='80000000-0000-4000-8000-000000000002';
const PROP='80000000-0000-4000-8000-000000000003';
const HASH='a'.repeat(64);
function request(overrides={}){
 return {id:RID,lead_id:LEAD,property_id:PROP,channel:'owner',requested_start:'2026-11-11T05:30:00+00:00',requested_end:'2026-11-11T06:00:00+00:00',requested_resource_id:null,requested_duration_min:30,customer_confirmed_start:null,customer_confirmed_end:null,customer_confirmed_resource_id:null,owner_approved_start:null,owner_approved_end:null,owner_approved_resource_id:null,needs_owner_approval:true,exception_reasons:['weekly_holiday'],status:'awaiting_owner',approval_id:null,booking_id:null,policy_version:1,request_hash:HASH,expires_at:'2026-11-11T06:00:00+00:00',proposal_expires_at:null,version:1,...overrides};
}
const alt={id:'80000000-0000-4000-8000-000000000004',resource_id:RID,starts_at:'2026-11-12T05:30:00+00:00',ends_at:'2026-11-12T06:00:00+00:00'};
beforeEach(()=>{sessionStorage.clear();vi.unstubAllGlobals();});
function stub(handler:(url:string,init?:RequestInit)=>unknown){
 return vi.fn(async(input:unknown,init?:RequestInit)=>{const url=String(input);if(url==='/api/session')return Response.json({authenticated:true});return Response.json(handler(url,init));});
}
function base(row:unknown,alts=[alt],handler:(url:string,init?:RequestInit)=>unknown=()=>({})){
 return stub((url,init)=>{
  if(url.includes('/api/owner/crm/booking-requests?'))return {items:[row],total:1,page:1,page_size:50};
  if(url.includes('/api/owner/crm/booking-requests/')&&!init?.method)return {record:row,alternatives:alts};
  if(url.includes('/api/owner/crm/resources'))return {items:[{id:RID,name:'Owner',kind:'person',active:true,version:1}],total:1,page:1,page_size:20};
  return handler(url,init);});
}

it('lists requests and shows alternatives on detail',async()=>{
 const user=userEvent.setup();
 vi.stubGlobal('fetch',base(request()));
 render(<BookingRequests/>);
 await user.click(await screen.findByRole('button',{name:/awaiting_owner/}));
 expect(await screen.findByText('Suggested alternatives')).toBeVisible();
 expect(screen.getByText('weekly_holiday')).toBeVisible();
});

it('confirms a proposed window',async()=>{
 const user=userEvent.setup();
 const row=request({status:'proposed',needs_owner_approval:false,exception_reasons:[],proposal_expires_at:'2026-11-11T05:00:00+00:00'});
 const fetch=base(row,[],(url,init)=>{
  if(url.includes('/confirm')&&init?.method==='POST')return {record:{...row,status:'approved',customer_confirmed_start:row.requested_start,customer_confirmed_end:row.requested_end,version:2}};
  return {};});
 vi.stubGlobal('fetch',fetch);
 render(<BookingRequests/>);
 await user.click(await screen.findByRole('button',{name:/proposed/}));
 await user.click(await screen.findByRole('button',{name:'Confirm proposed window'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).includes('/confirm')&&init?.method==='POST')).toBe(true));
 const post=fetch.mock.calls.find(([url,init])=>String(url).includes('/confirm')&&init?.method==='POST')!;
 expect(JSON.parse(post[1]!.body as string)).toMatchObject({expected_version:1});
});

it('approves with a substitution window and resource',async()=>{
 const user=userEvent.setup();
 const fetch=base(request(),[],(url,init)=>{
  if(url.includes('/decide')&&init?.method==='POST')return {record:request({status:'approved'})};
  return {};});
 vi.stubGlobal('fetch',fetch);
 render(<BookingRequests/>);
 await user.click(await screen.findByRole('button',{name:/awaiting_owner/}));
 fireEvent.change(await screen.findByLabelText('Approved start (blank keeps request)'),{target:{value:'2026-11-12T11:00'}});
 fireEvent.change(screen.getByLabelText('Approved end'),{target:{value:'2026-11-12T11:30'}});
 await user.type(screen.getByLabelText('Decision reason'),'Fictional owner override');
 await user.click(screen.getByRole('button',{name:'Record decision'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).includes('/decide')&&init?.method==='POST')).toBe(true));
 const post=fetch.mock.calls.find(([url,init])=>String(url).includes('/decide')&&init?.method==='POST')!;
 const body=JSON.parse(post[1]!.body as string);
 expect(body).toMatchObject({expected_version:1,expected_request_hash:HASH,decision:'approved',reason:'Fictional owner override'});
 expect(new Date(body.approved_start).getTime()).toBe(new Date('2026-11-12T11:00').getTime());
 expect(new Date(body.approved_end).getTime()).toBe(new Date('2026-11-12T11:30').getTime());
});

it('commits an approved request and cancels another',async()=>{
 const user=userEvent.setup();
 const approved=request({status:'approved',needs_owner_approval:false,exception_reasons:[],customer_confirmed_start:'2026-11-11T05:30:00+00:00',customer_confirmed_end:'2026-11-11T06:00:00+00:00'});
 const fetch=base(approved,[],(url,init)=>{
  if(url.includes('/commit')&&init?.method==='POST')return {record:{...approved,status:'booked',booking_id:RID},booking_id:RID};
  if(url.includes('/cancel')&&init?.method==='POST')return {record:{...approved,status:'cancelled'}};
  return {};});
 vi.stubGlobal('fetch',fetch);
 render(<BookingRequests/>);
 await user.click(await screen.findByRole('button',{name:/approved/}));
 await user.click(await screen.findByRole('button',{name:'Commit booking'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).includes('/commit')&&init?.method==='POST')).toBe(true));
 const commit=fetch.mock.calls.find(([url,init])=>String(url).includes('/commit')&&init?.method==='POST')!;
 expect(JSON.parse(commit[1]!.body as string)).toMatchObject({expected_request_hash:HASH});
 expect(JSON.parse(commit[1]!.body as string)).not.toHaveProperty('expected_version');
 await user.click(screen.getByRole('button',{name:'Cancel request'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).includes('/cancel')&&init?.method==='POST')).toBe(true));
});

it('keeps failed commits visible without claiming success',async()=>{
 const user=userEvent.setup();
 const approved=request({status:'approved',needs_owner_approval:false,exception_reasons:[]});
 vi.stubGlobal('fetch',vi.fn(async(input:unknown,init?:RequestInit)=>{
  const url=String(input);
  if(url==='/api/session')return Response.json({authenticated:true});
  if(url.includes('/api/owner/crm/booking-requests?'))return Response.json({items:[approved],total:1,page:1,page_size:50});
  if(url.includes('/api/owner/crm/booking-requests/')&&init?.method==='POST')return Response.json({message:'Policy conditions changed since approval.'},{status:409});
  if(url.includes('/api/owner/crm/booking-requests/'))return Response.json({record:approved,alternatives:[]});
  if(url.includes('/api/owner/crm/resources'))return Response.json({items:[],total:0,page:1,page_size:20});
  return Response.json({});}));
 render(<BookingRequests/>);
 await user.click(await screen.findByRole('button',{name:/approved/}));
 await user.click(await screen.findByRole('button',{name:'Commit booking'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Policy conditions changed');
});
