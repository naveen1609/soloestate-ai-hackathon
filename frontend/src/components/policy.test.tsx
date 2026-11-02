// @vitest-environment jsdom
import {beforeEach,expect,it,vi} from 'vitest';
import {act,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Policy from './policy';
const RID='80000000-0000-4000-8000-000000000001';
const policy={id:'default',timezone:'Asia/Kolkata',working_days:[0,1,3,4,5,6],weekly_holiday:2,hours_start:'10:00:00',hours_end:'17:00:00',holiday_dates:[],notice_hours:16,default_duration_min:30,min_duration_min:15,max_duration_min:120,buffer_minutes:0,version:3};
const resource={id:RID,name:'Owner',kind:'person',active:true,version:1};
beforeEach(()=>{sessionStorage.clear();vi.unstubAllGlobals();});
function stub(handler:(url:string,init?:RequestInit)=>unknown){
 return vi.fn(async(input:unknown,init?:RequestInit)=>{const url=String(input);if(url==='/api/session')return Response.json({authenticated:true});return Response.json(handler(url,init));});
}
function base(handler:(url:string,init?:RequestInit)=>unknown){
 return stub((url,init)=>{
  if(url.includes('/api/owner/crm/business-policy'))return {record:policy};
  if(url.includes('/api/owner/crm/resources'))return {items:[resource],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/automation-controls'))return {record:{id:'default',emergency_stop:false}};
  if(url.includes('/api/owner/crm/automation-policies'))return {items:[],total:0,page:1,page_size:20};
  if(url.includes('/api/owner/crm/suppression-list'))return {items:[],total:0,page:1,page_size:20};
  if(url.includes('/api/owner/crm/identity-links'))return {items:[],total:0,page:1,page_size:20};
  return handler(url,init);});
}

it('renders the current policy summary',async()=>{
 vi.stubGlobal('fetch',base(()=>({})));
 render(<Policy/>);
 expect(await screen.findByText(/Policy v3/)).toBeVisible();
 expect(await screen.findByDisplayValue('Asia/Kolkata')).toBeVisible();
});

it('saves policy edits with version and reason',async()=>{
 const user=userEvent.setup();
 const fetch=base(()=>({record:{...policy,version:4}}));vi.stubGlobal('fetch',fetch);
 render(<Policy/>);
 await user.clear(await screen.findByLabelText('Advance notice (hours)'));
 await user.type(screen.getByLabelText('Advance notice (hours)'),'24');
 await user.type(screen.getByLabelText('Change reason'),'Fictional calmer schedule');
 await user.click(screen.getByRole('button',{name:'Save policy'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='PATCH')).toBe(true));
 const patch=fetch.mock.calls.find(([,init])=>init?.method==='PATCH')!;
 expect(patch[0]).toContain('/api/owner/crm/business-policy');
 expect(JSON.parse(patch[1]!.body as string)).toMatchObject({expected_version:3,reason:'Fictional calmer schedule',notice_hours:24,working_days:[0,1,3,4,5,6]});
});

it('creates and deactivates resources',async()=>{
 const user=userEvent.setup();
 const agent={id:'80000000-0000-4000-8000-000000000002',name:'Showing agent',kind:'person',active:true,version:1};
 let created=false;
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/business-policy'))return {record:policy};
  if(url.includes('/api/owner/crm/resources')&&!init?.method)return {items:created?[resource,agent]:[resource],total:created?2:1,page:1,page_size:20};
  if(url.endsWith('/api/owner/crm/resources')&&init?.method==='POST'){created=true;return {record:agent};}
  if(url.includes('/deactivate')&&init?.method==='POST')return {record:{...agent,active:false}};
  if(url.includes('/api/owner/crm/automation-controls'))return {record:{id:'default',emergency_stop:false}};
  if(url.includes('/api/owner/crm/automation-policies')||url.includes('/api/owner/crm/suppression-list')||url.includes('/api/owner/crm/identity-links'))return {items:[],total:0,page:1,page_size:20};
  return {};});
 vi.stubGlobal('fetch',fetch);
 render(<Policy/>);
 await user.type(await screen.findByLabelText('New resource name'),'Showing agent');
 await user.click(screen.getByRole('button',{name:'Create resource'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='POST')).toBe(true));
 const post=fetch.mock.calls.find(([,init])=>init?.method==='POST')!;
 expect(JSON.parse(post[1]!.body as string)).toMatchObject({name:'Showing agent'});
 expect(await screen.findByText(/Showing agent/)).toBeVisible();
 const rows=await screen.findAllByText(/Showing agent/);
 await user.click(within(rows[0].closest('li')!).getByRole('button',{name:'Deactivate'}));
 await user.type(await screen.findByLabelText(/Deactivation reason/),'Fictional departure');
 await user.click(screen.getByRole('button',{name:'Confirm deactivation'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).includes('/deactivate')&&init?.method==='POST')).toBe(true));
 const deact=fetch.mock.calls.find(([url,init])=>String(url).includes('/deactivate')&&init?.method==='POST')!;
 expect(JSON.parse(deact[1]!.body as string)).toMatchObject({expected_version:1,reason:'Fictional departure'});
});

it('generates slots and reports counts',async()=>{
 const user=userEvent.setup();
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/business-policy'))return {record:policy};
  if(url.includes('/api/owner/crm/resources')&&!init?.method)return {items:[resource],total:1,page:1,page_size:20};
  if(url.includes('/visit-slots/generate'))return {record:{id:RID,created:14,skipped_overlapping:2}};
  if(url.includes('/api/owner/crm/automation-controls'))return {record:{id:'default',emergency_stop:false}};
  if(url.includes('/api/owner/crm/automation-policies')||url.includes('/api/owner/crm/suppression-list')||url.includes('/api/owner/crm/identity-links'))return {items:[],total:0,page:1,page_size:20};
  return {};});
 vi.stubGlobal('fetch',fetch);
 render(<Policy/>);
 fireEvent.change(await screen.findByLabelText('First day'),{target:{value:'2026-11-11'}});
 fireEvent.change(screen.getByLabelText('Last day'),{target:{value:'2026-11-12'}});
 await user.click(screen.getByRole('button',{name:'Generate slots'}));
 expect(await screen.findByText('Created 14 slots, skipped 2 overlapping.')).toBeVisible();
});

it('stops and lifts autonomous commits with a reason',async()=>{
 const user=userEvent.setup();
 let stopped=false;
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/business-policy'))return {record:policy};
  if(url.includes('/api/owner/crm/resources'))return {items:[resource],total:1,page:1,page_size:20};
  if(url.includes('/automation-controls/stop')&&init?.method==='POST'){stopped=JSON.parse(init.body as string).stopped;return {record:{id:'default',emergency_stop:stopped}};}
  if(url.includes('/api/owner/crm/automation-controls'))return {record:{id:'default',emergency_stop:stopped}};
  if(url.includes('/api/owner/crm/automation-policies')||url.includes('/api/owner/crm/suppression-list')||url.includes('/api/owner/crm/identity-links'))return {items:[],total:0,page:1,page_size:20};
  return {};});
 vi.stubGlobal('fetch',fetch);
 render(<Policy/>);
 expect(await screen.findByText((_,el)=>el?.tagName==='P'&&(el?.textContent??'').includes('currently allowed.'))).toBeVisible();
 await user.type(screen.getByLabelText('Stop reason'),'Fictional drill');
 await user.click(screen.getByRole('button',{name:'Stop autonomous commits'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url])=>String(url).includes('/automation-controls/stop'))).toBe(true));
 const post=fetch.mock.calls.find(([url])=>String(url).includes('/automation-controls/stop'))!;
 expect(JSON.parse(post[1]!.body as string)).toMatchObject({stopped:true,reason:'Fictional drill'});
 expect(await screen.findByText('STOPPED')).toBeVisible();
});

it('creates an automation policy and adds a suppression',async()=>{
 const user=userEvent.setup();
 let added=false;
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/automation-policies')&&init?.method==='POST')return {record:{id:RID,kind:'followup_nudge',enabled:false}};
  if(url.includes('/api/owner/crm/suppression-list')&&init?.method==='POST'){added=true;return {record:{channel:'whatsapp',destination:'+15550001111'}};}
  if(url.includes('/api/owner/crm/suppression-list')&&added)return {items:[{channel:'whatsapp',destination:'+15550001111',source:'owner_action'}],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/business-policy'))return {record:policy};
  if(url.includes('/api/owner/crm/resources'))return {items:[resource],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/automation-controls'))return {record:{id:'default',emergency_stop:false}};
  if(url.includes('/api/owner/crm/automation-policies'))return {items:[],total:0,page:1,page_size:20};
  if(url.includes('/api/owner/crm/suppression-list'))return {items:[],total:0,page:1,page_size:20};
  if(url.includes('/api/owner/crm/identity-links'))return {items:[],total:0,page:1,page_size:20};
  return {};});
 vi.stubGlobal('fetch',fetch);
 render(<Policy/>);
 await user.selectOptions(await screen.findByLabelText('Policy kind'),'followup_nudge');
 await user.clear(screen.getByLabelText('Hours before visit'));
 await user.type(screen.getByLabelText('Days after event'),'2');
 await user.type(screen.getByLabelText('After event (for nudges)'),'no_visit_booked');
 await user.click(screen.getByRole('button',{name:'Create policy'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).endsWith('/automation-policies')&&init?.method==='POST')).toBe(true));
 const created=fetch.mock.calls.find(([url,init])=>String(url).endsWith('/automation-policies')&&init?.method==='POST')!;
 expect(JSON.parse(created[1]!.body as string)).toMatchObject({kind:'followup_nudge',days_after:2,after_event:'no_visit_booked'});
 await user.type(screen.getByLabelText('Destination'),'+15550001111');
 await user.click(screen.getByRole('button',{name:'Add suppression'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).includes('/suppression-list')&&init?.method==='POST')).toBe(true));
 expect(await screen.findByText(/\+15550001111/)).toBeVisible();
});

it('links an identity and revokes an active link',async()=>{
 const user=userEvent.setup();
 const observed={id:'80000000-0000-4000-8000-000000000003',channel:'whatsapp',provider_ref:'15550001111',lead_id:RID,status:'observed',version:1};
 const active={id:'80000000-0000-4000-8000-000000000004',channel:'whatsapp',provider_ref:'15550001111',lead_id:RID,status:'active',version:1,verified_destination:'+15550001111'};
 let links=[observed,active];
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/business-policy'))return {record:policy};
  if(url.includes('/api/owner/crm/resources'))return {items:[resource],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/identity-links')&&!init?.method)return {items:links,total:links.length,page:1,page_size:20};
  if(url.includes('/identity-links/link')&&init?.method==='POST'){links=[active];return {record:active};}
  if(url.includes('/revoke')&&init?.method==='POST'){links=[];return {record:{...active,status:'revoked'}};}
  if(url.includes('/api/owner/crm/automation-controls'))return {record:{id:'default',emergency_stop:false}};
  if(url.includes('/api/owner/crm/automation-policies')||url.includes('/api/owner/crm/suppression-list'))return {items:[],total:0,page:1,page_size:20};
  return {};});
 vi.stubGlobal('fetch',fetch);
 render(<Policy/>);
 await user.type(await screen.findByLabelText('Observed link id'),observed.id);
 await user.type(screen.getByLabelText('Canonical lead id'),RID);
 await user.type(screen.getByLabelText('Verified destination'),'+15550001111');
 await user.type(screen.getByLabelText('Verification note'),'Fictional front-desk check');
 await user.click(screen.getByRole('button',{name:'Link identity to lead'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).includes('/identity-links/link')&&init?.method==='POST')).toBe(true));
 const link=fetch.mock.calls.find(([url,init])=>String(url).includes('/identity-links/link')&&init?.method==='POST')!;
 expect(JSON.parse(link[1]!.body as string)).toMatchObject({link_id:observed.id,lead_id:RID,verified_destination:'+15550001111'});
 const revokeButtons=await screen.findAllByRole('button',{name:'Revoke'});
 await user.click(revokeButtons[0]);
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).includes('/revoke')&&init?.method==='POST')).toBe(true));
});

it('discards a stale identity reload that resolves after a newer filter load',async()=>{
 const user=userEvent.setup();
 const active={id:'80000000-0000-4000-8000-000000000004',channel:'whatsapp',provider_ref:'15550001111',lead_id:RID,status:'active',version:1};
 const revoked={...active,status:'revoked',version:2};
 const superseded={id:'80000000-0000-4000-8000-000000000003',channel:'whatsapp',provider_ref:'15550001111',lead_id:RID,status:'superseded',version:1};
 const calls:string[]=[];const waiters:((v:unknown)=>void)[]=[];
 vi.stubGlobal('fetch',vi.fn(async(input:unknown,init?:RequestInit)=>{
  const url=String(input);
  if(url==='/api/session')return Response.json({authenticated:true});
  if(url.includes('/api/owner/crm/business-policy'))return Response.json({record:policy});
  if(url.includes('/api/owner/crm/resources'))return Response.json({items:[resource],total:1,page:1,page_size:20});
  if(url.includes('/api/owner/crm/automation-controls'))return Response.json({record:{id:'default',emergency_stop:false}});
  if(url.includes('/api/owner/crm/automation-policies')||url.includes('/api/owner/crm/suppression-list'))return Response.json({items:[],total:0,page:1,page_size:20});
  if(url.includes('/revoke')&&init?.method==='POST')return Response.json({record:revoked});
  if(url.includes('/api/owner/crm/identity-links')&&!init?.method){calls.push(url);return new Promise<Response>(resolve=>waiters.push(v=>resolve(Response.json(v))));}
  return Response.json({});}));
 render(<Policy/>);
 const section=(await screen.findByRole('heading',{name:'Channel identities'})).closest('section')!;
 const items=()=>within(section).queryAllByRole('listitem');
 await waitFor(()=>expect(calls.length).toBeGreaterThanOrEqual(1));
 waiters.shift()!({items:[active],total:1,page:1,page_size:20});
 await waitFor(()=>expect(items().some(li=>li.textContent?.includes('15550001111'))).toBe(true));
 await user.selectOptions(within(section).getByLabelText('Status filter'),'active');
 await waitFor(()=>expect(calls.length).toBe(2));
 expect(calls[1]).toContain('status=active');
 waiters.shift()!({items:[active],total:1,page:1,page_size:20});
 await waitFor(()=>expect(items().length).toBe(1));
 await user.click(within(section).getByRole('button',{name:'Revoke'}));
 await waitFor(()=>expect(calls.length).toBe(3));
 expect(calls[2]).toContain('status=active');
 await user.selectOptions(within(section).getByLabelText('Status filter'),'');
 await waitFor(()=>expect(calls.length).toBe(4));
 expect(calls[3]).not.toContain('status=');
 const stale=waiters.shift()!;const fresh=waiters.shift()!;
 fresh({items:[superseded,revoked],total:2,page:1,page_size:20});
 await waitFor(()=>expect(items().some(li=>li.textContent?.includes('revoked'))).toBe(true));
 stale({items:[],total:0,page:1,page_size:20});
 await act(async()=>{await new Promise(r=>setTimeout(r,0));});
 expect(items().length).toBe(2);
 expect(items().some(li=>li.textContent?.includes('revoked'))).toBe(true);
});

it('keeps failed policy saves visible without claiming success',async()=>{
 const user=userEvent.setup();
 vi.stubGlobal('fetch',vi.fn(async(input:unknown,init?:RequestInit)=>{
  const url=String(input);
  if(url==='/api/session')return Response.json({authenticated:true});
  if(url.includes('/api/owner/crm/business-policy')&&init?.method==='PATCH')return Response.json({message:'Policy changed. Reload before editing.'},{status:409});
  if(url.includes('/api/owner/crm/business-policy'))return Response.json({record:policy});
  if(url.includes('/api/owner/crm/resources'))return Response.json({items:[resource],total:1,page:1,page_size:20});
  if(url.includes('/api/owner/crm/automation-controls'))return Response.json({record:{id:'default',emergency_stop:false}});
  if(url.includes('/api/owner/crm/automation-policies')||url.includes('/api/owner/crm/suppression-list')||url.includes('/api/owner/crm/identity-links'))return Response.json({items:[],total:0,page:1,page_size:20});
  return Response.json({});}));
 render(<Policy/>);
 await user.type(await screen.findByLabelText('Change reason'),'Fictional stale');
 await user.click(screen.getByRole('button',{name:'Save policy'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Reload before editing');
});
