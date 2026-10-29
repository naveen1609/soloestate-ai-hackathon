// @vitest-environment jsdom
import {beforeEach,it,expect,vi} from 'vitest';
import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import FollowupTasks from './followup-tasks';
const LEAD='80000000-0000-4000-8000-000000000001';
const TID='80000000-0000-4000-8000-000000000002';
const task={id:TID,lead_id:LEAD,property_id:null,title:'Call buyer about Whitefield',notes:'',due_at:null,assignee:'owner',status:'open',outcome:null,version:1,overdue:false};
beforeEach(()=>{sessionStorage.clear();vi.unstubAllGlobals();});
function stub(handler:(url:string,init?:RequestInit)=>unknown){
 return vi.fn(async(input:unknown,init?:RequestInit)=>Response.json(handler(String(input),init)));
}
const emptyList={items:[],total:0,page:1,page_size:20};

it('creates a task with explicit null property and due date',async()=>{
 const user=userEvent.setup();
 let created=false;
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/followup-tasks?lead_id='))return created?{items:[task],total:1,page:1,page_size:20}:emptyList;
  if(url.includes('/api/owner/crm/properties'))return emptyList;
  if(url.includes('/api/owner/crm/followup-tasks')&&init?.method==='POST'){created=true;return {record:task};}
  return {record:task,history:[]};});
 vi.stubGlobal('fetch',fetch);
 render(<FollowupTasks leadId={LEAD}/>);
 await user.type(await screen.findByLabelText('Task title'),'Call buyer about Whitefield');
 await user.click(screen.getByRole('button',{name:'Create task'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='POST')).toBe(true));
 const post=fetch.mock.calls.find(([,init])=>init?.method==='POST')!;
 expect(JSON.parse(post[1]!.body as string)).toMatchObject({lead_id:LEAD,property_id:null,title:'Call buyer about Whitefield',due_at:null});
 expect(await screen.findByText('Status: open')).toBeVisible();
});

it('lists tasks with due order and overdue flags',async()=>{
 const past={...task,id:'80000000-0000-4000-8000-000000000003',title:'Past due call',due_at:'2026-01-01T00:00:00+00:00',overdue:true};
 const future={...task,id:'80000000-0000-4000-8000-000000000004',title:'Future visit prep',due_at:'2034-01-01T00:00:00+00:00',overdue:false};
 vi.stubGlobal('fetch',stub((url)=>{
  if(url.includes('/api/owner/crm/followup-tasks?lead_id='))return {items:[past,future],total:2,page:1,page_size:20};
  if(url.includes('/api/owner/crm/properties'))return emptyList;
  return {record:past,history:[]};}));
 render(<FollowupTasks leadId={LEAD}/>);
 const buttons=await screen.findAllByRole('button',{name:/open ·/});
 expect(buttons.map(button=>button.textContent)).toEqual(['open · overdue · Past due call','open · due 2034-01-01 · Future visit prep']);
});

it('completes a reviewed task with an outcome',async()=>{
 const user=userEvent.setup();
 const review={...task,status:'in_review',version:3};
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/followup-tasks?lead_id='))return {items:[review],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/properties'))return emptyList;
  if(url.endsWith('/update')&&init?.method==='POST')return {record:{...review,status:'done',version:4,outcome:'Buyer confirmed a visit.'}};
  return {record:review,history:[]};});
 vi.stubGlobal('fetch',fetch);
 render(<FollowupTasks leadId={LEAD}/>);
 await user.click(await screen.findByRole('button',{name:/in_review ·/}));
 await user.type(await screen.findByLabelText('Completion outcome'),'Buyer confirmed a visit.');
 await user.type(screen.getByLabelText('Change reason'),'Fictional completion');
 await user.click(screen.getByRole('button',{name:'Move to done'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='POST')).toBe(true));
 const post=fetch.mock.calls.find(([,init])=>init?.method==='POST')!;
 expect(JSON.parse(post[1]!.body as string)).toMatchObject({expected_version:3,status:'done',outcome:'Buyer confirmed a visit.'});
});

it('sends content edits with a reason',async()=>{
 const user=userEvent.setup();
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/followup-tasks?lead_id='))return {items:[task],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/properties'))return emptyList;
  if(url.endsWith('/update')&&init?.method==='POST')return {record:{...task,title:'Call buyer about rates',version:2}};
  return {record:task,history:[]};});
 vi.stubGlobal('fetch',fetch);
 render(<FollowupTasks leadId={LEAD}/>);
 await user.click(await screen.findByRole('button',{name:/open ·/}));
 const title=await screen.findByLabelText('Edit task title');
 await user.clear(title);
 await user.type(title,'Call buyer about rates');
 await user.type(screen.getByLabelText('Change reason'),'Fictional correction');
 await user.click(screen.getByRole('button',{name:'Save changes'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='POST')).toBe(true));
 const post=fetch.mock.calls.find(([,init])=>init?.method==='POST')!;
 expect(JSON.parse(post[1]!.body as string)).toMatchObject({expected_version:1,title:'Call buyer about rates',reason:'Fictional correction'});
 expect(JSON.parse(post[1]!.body as string)).not.toHaveProperty('due_at');
});

it('shows meaningful revision history from snapshots',async()=>{
 const user=userEvent.setup();
 const open={id:TID,lead_id:LEAD,property_id:null,title:'Call buyer',notes:'',due_at:null,assignee:'owner',status:'open',outcome:null,version:1};
 const edited={...open,title:'Call buyer about rates',version:2};
 const history=[{id:'80000000-0000-4000-8000-000000000005',reason:'Fictional correction',before_state:{task:open},after_state:{task:edited},created_at:'2026-10-29T11:00:00+00:00'},
  {id:'80000000-0000-4000-8000-000000000006',reason:'Owner opened task',before_state:null,after_state:{task:open},created_at:'2026-10-29T10:00:00+00:00'}];
 vi.stubGlobal('fetch',stub((url)=>{
  if(url.includes('/api/owner/crm/followup-tasks?lead_id='))return {items:[{...task,title:'Call buyer about rates',version:2}],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/properties'))return emptyList;
  return {record:{...task,title:'Call buyer about rates',version:2},history};}));
 render(<FollowupTasks leadId={LEAD}/>);
 await user.click(await screen.findByRole('button',{name:/open ·/}));
 expect(await screen.findByText('title: Call buyer → Call buyer about rates')).toBeVisible();
 expect(screen.getByText('Opened as open')).toBeVisible();
});

it('renders legacy history snapshots without new fields',async()=>{
 const user=userEvent.setup();
 const history=[{id:'80000000-0000-4000-8000-000000000007',reason:'Fictional review',before_state:{task:{id:TID,status:'in_review',outcome:null}},after_state:{task:{id:TID,status:'in_progress',outcome:null}},created_at:'2026-10-29T11:00:00+00:00'}];
 vi.stubGlobal('fetch',stub((url)=>{
  if(url.includes('/api/owner/crm/followup-tasks?lead_id='))return {items:[{...task,status:'in_progress',version:2}],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/properties'))return emptyList;
  return {record:{...task,status:'in_progress',version:2},history};}));
 render(<FollowupTasks leadId={LEAD}/>);
 await user.click(await screen.findByRole('button',{name:/in_progress ·/}));
 expect(await screen.findByText('Fictional review')).toBeVisible();
 expect(screen.getByText('status: in_review → in_progress')).toBeVisible();
});

it('keeps failed saves visible without claiming success',async()=>{
 const user=userEvent.setup();
 vi.stubGlobal('fetch',vi.fn(async(input:unknown,init?:RequestInit)=>{
  const url=String(input);
  if(url.includes('/api/owner/crm/followup-tasks?lead_id='))return Response.json({items:[task],total:1,page:1,page_size:20});
  if(url.includes('/api/owner/crm/properties'))return Response.json(emptyList);
  if(init?.method==='POST')return Response.json({message:'Record changed. Reload before editing.'},{status:409});
  return Response.json({record:task,history:[]});}));
 render(<FollowupTasks leadId={LEAD}/>);
 await user.click(await screen.findByRole('button',{name:/open ·/}));
 await user.type(await screen.findByLabelText('Change reason'),'Fictional stale');
 await user.click(screen.getByRole('button',{name:'Move to cancelled'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Reload before editing');
});
