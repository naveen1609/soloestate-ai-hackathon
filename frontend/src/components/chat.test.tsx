// @vitest-environment jsdom
import {beforeEach,expect,it,vi} from 'vitest';
import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Chat from './chat';

const CID='90000000-0000-4000-8000-000000000001';
const LID='90000000-0000-4000-8000-000000000002';
const FAQ='90000000-0000-4000-8000-000000000003';
const MID='90000000-0000-4000-8000-000000000004';
const RID='90000000-0000-4000-8000-000000000005';
const readiness={matching_ready:false,qualification_ready:false,matching_missing_fields:['preferred_localities','budget_min'],missing_fields:[],invalid_fields:[],qualification_current:true,needs_review:false};
const lead={id:LID,version:1,name:'Fictional Visitor',preferred_localities:null,budget_min:null,budget_max:null,bedrooms:null,purchase_timeframe:null,readiness};
const convo={id:CID,lead_id:LID,status:'open',handoff_state:'none',property_id:null,version:1,created_at:'2034-01-01T00:00:00Z'};
const customer={id:MID,role:'customer',body:'Is there a buyer fee?',grounding:'ungrounded',unsupported_reason:null,citations:[],citations_superseded:false,confidence:null,supersedes_message_id:null,created_at:'2034-01-01T00:01:00Z'};
const reply={id:RID,role:'assistant',body:'A verified reply.',grounding:'grounded',unsupported_reason:null,citations:[{faq_id:FAQ,version:1,quote:'No buyer fee applies.'}],citations_superseded:false,confidence:'high',supersedes_message_id:null,created_at:'2034-01-01T00:02:00Z'};
beforeEach(()=>{sessionStorage.clear();localStorage.clear();});
function backend(handler:(path:string,init?:RequestInit)=>Response|Promise<Response>){
 const fetch=vi.fn(async(input:string|URL|Request,init?:RequestInit)=>handler(String(input),init));
 vi.stubGlobal('fetch',fetch);return fetch;
}

it('starts a chat from capture and resumes the stored thread',async()=>{
 const user=userEvent.setup();
 const fetch=backend((path,init)=>init?.method==='POST'?Response.json({lead,conversation:convo,messages:[]}):Response.json({lead,conversation:convo,messages:[customer,reply],truncated:false,drafting_blocked:null}));
 render(<Chat/>);
 await user.type(screen.getByLabelText('Name'),'Fictional Visitor');
 await user.type(screen.getByLabelText('Email'),'visitor@example.invalid');
 await user.click(screen.getByRole('button',{name:'Start chat'}));
 await waitFor(()=>expect(screen.getByText(/No buyer fee applies/)).toBeVisible());
 expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toMatchObject({name:'Fictional Visitor',email:'visitor@example.invalid'});
 expect(window.localStorage.getItem('chat:conversation')).toBe(CID);
 expect(screen.getByText(/Status:/)).toBeVisible();
});

it('sends a message with the thread version and shows grounded citations',async()=>{
 const user=userEvent.setup();window.localStorage.setItem('chat:conversation',CID);
 const fetch=backend((path,init)=>init?.method==='POST'?Response.json({message:customer,reply,conversation:convo}):Response.json({lead,conversation:convo,messages:[customer,reply],truncated:false,drafting_blocked:null}));
 render(<Chat/>);
 expect(await screen.findByText(/No buyer fee applies/)).toBeVisible();
 await user.type(screen.getByLabelText('Message'),'Is there a buyer fee?');
 await user.click(screen.getByRole('button',{name:'Send'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='POST'&&String(init?.body).includes('"expected_version":1'))).toBe(true));
 expect(screen.getByText(/confidence/)).toBeVisible();
});

it('shows pending review state and disables sending',async()=>{
 window.localStorage.setItem('chat:conversation',CID);
 backend(()=>Response.json({lead,conversation:{...convo,status:'pending_owner',version:2},messages:[customer],truncated:false,drafting_blocked:'Owner review is pending.'}));
 render(<Chat/>);
 expect(await screen.findByText(/Owner review is pending/)).toBeVisible();
 expect(screen.getByRole('button',{name:'Send'})).toBeDisabled();
 expect(screen.getByLabelText('Message')).toBeDisabled();
});

it('saves requirements and shows matching properties',async()=>{
 const user=userEvent.setup();window.localStorage.setItem('chat:conversation',CID);
 const matches={lead_id:LID,lead_version:2,checked_at:'2034-01-01T00:00:00Z',matching_ready:true,missing_fields:[],invalid_fields:[],criteria_snapshot:{},truncated:false,criteria:'demo',matches:[{id:FAQ,title:'Demo home',price:'150',bedrooms:2,score:1,version:1,checks:[]}]};
 const fetch=backend((path,init)=>{
  if(String(path).includes('/matches'))return Response.json(matches);
  if(init?.method==='POST')return Response.json({lead:{...lead,version:2,preferred_localities:['Bellandur']}});
  return Response.json({lead,conversation:convo,messages:[],truncated:false,drafting_blocked:null});
 });
 render(<Chat/>);
 expect(await screen.findByText('What are you looking for?')).toBeVisible();
 await user.type(screen.getByLabelText('Localities'),'Bellandur');
 await user.type(screen.getByLabelText('Minimum budget'),'100');
 await user.type(screen.getByLabelText('Maximum budget'),'200');
 await user.type(screen.getByLabelText('Bedrooms'),'2');
 await user.click(screen.getByRole('button',{name:'Save requirements'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='POST'&&String(init?.body).includes('Bellandur'))).toBe(true));
 await user.click(screen.getByRole('button',{name:'Show matching properties'}));
 expect(await screen.findByText(/Demo home/)).toBeVisible();
});

it('requests owner review from an open chat',async()=>{
 const user=userEvent.setup();window.localStorage.setItem('chat:conversation',CID);
 let requested=false;
 const fetch=backend((path,init)=>{
  if(init?.method==='POST'){requested=true;return Response.json({conversation:{...convo,handoff_state:'requested'}});}
  return Response.json({lead,conversation:{...convo,handoff_state:requested?'requested':'none'},messages:[],truncated:false,drafting_blocked:requested?'Resolve the human handoff first.':null});
 });
 render(<Chat/>);
 expect(await screen.findByText('Talk to the owner')).toBeVisible();
 await user.type(screen.getByLabelText('Handoff reason'),'Fictional handoff');
 await user.click(screen.getByRole('button',{name:'Request owner review'}));
 await waitFor(()=>expect(screen.queryByText('Talk to the owner')).not.toBeInTheDocument());
 expect(fetch.mock.calls.some(([,init])=>String(init?.body).includes('Fictional handoff'))).toBe(true);
});

it('refetches the thread after a failed send so saved messages stay visible',async()=>{
 const user=userEvent.setup();window.localStorage.setItem('chat:conversation',CID);
 const fetch=backend((path,init)=>init?.method==='POST'?Response.json({message:'Blocked. Your message was saved; reload the chat to see its current state.'},{status:409}):Response.json({lead,conversation:convo,messages:[customer],truncated:false,drafting_blocked:null}));
 render(<Chat/>);
 expect(await screen.findByText('Is there a buyer fee?')).toBeVisible();
 await user.type(screen.getByLabelText('Message'),'Another question?');
 await user.click(screen.getByRole('button',{name:'Send'}));
 await waitFor(()=>expect(screen.getByText(/Your message was saved/)).toBeVisible());
 expect(screen.getByText('Is there a buyer fee?')).toBeVisible();
 expect(fetch.mock.calls.filter(([,init])=>init?.method!=='POST').length).toBeGreaterThan(1);
});

it('selects property focus from the matches list',async()=>{
 const user=userEvent.setup();window.localStorage.setItem('chat:conversation',CID);
 const PID='90000000-0000-4000-8000-000000000009';
 const matches={lead_id:LID,lead_version:1,checked_at:'2034-01-01T00:00:00Z',matching_ready:true,missing_fields:[],invalid_fields:[],criteria_snapshot:{},truncated:false,criteria:'demo',matches:[{id:PID,title:'Focus home',price:'150',bedrooms:2,score:1,version:1,checks:[]}]};
 let focused=false;
 const fetch=backend((path,init)=>{
  if(String(path).includes('/matches'))return Response.json(matches);
  if(init?.method==='POST'){focused=true;return Response.json({conversation:{...convo,property_id:PID,version:2}});}
  return Response.json({lead,conversation:{...convo,property_id:focused?PID:null,version:focused?2:1},messages:[],truncated:false,drafting_blocked:null});
 });
 render(<Chat/>);
 expect(await screen.findByText('What are you looking for?')).toBeVisible();
 await user.click(screen.getByRole('button',{name:'Show matching properties'}));
 expect(await screen.findByText(/Focus home/)).toBeVisible();
 await user.click(screen.getByRole('button',{name:'Ask about this property'}));
 await waitFor(()=>expect(screen.getByText(/· focus/)).toBeVisible());
 expect(fetch.mock.calls.some(([,init])=>init?.method==='POST'&&String(init?.body).includes(PID))).toBe(true);
});

it('returns to capture when the chat token is rejected',async()=>{
 window.localStorage.setItem('chat:conversation',CID);
 backend(()=>Response.json({message:'Start or resume the chat first.'},{status:401}));
 render(<Chat/>);
 expect(await screen.findByRole('button',{name:'Start chat'})).toBeVisible();
 expect(window.localStorage.getItem('chat:conversation')).toBeNull();
});
