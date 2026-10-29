// @vitest-environment jsdom
import {beforeEach,it,expect,vi} from 'vitest';
import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConversationPanel from './conversation-panel';
const LEAD='80000000-0000-4000-8000-000000000001';
const CID='80000000-0000-4000-8000-000000000002';
const FID='80000000-0000-4000-8000-000000000003';
const convo={id:CID,lead_id:LEAD,property_id:null,status:'open',handoff_state:'none',version:1};
const customer={id:'80000000-0000-4000-8000-000000000004',conversation_id:CID,role:'customer',body:'Is there a buyer fee?',grounding:'ungrounded',unsupported_reason:null,citations:[],confidence:null,model_name:null,response_id:null};
const grounded={id:'80000000-0000-4000-8000-000000000005',conversation_id:CID,role:'assistant',body:'No buyer fee applies.',grounding:'grounded',unsupported_reason:null,citations:[{faq_id:FID,version:1,quote:'No buyer fee applies.'}],confidence:'high',model_name:'test-meta',response_id:'resp-1',citations_superseded:false};
beforeEach(()=>{sessionStorage.clear();vi.unstubAllGlobals();});
function stub(handler:(url:string,init?:RequestInit)=>unknown){
 return vi.fn(async(input:unknown,init?:RequestInit)=>Response.json(handler(String(input),init)));
}
const emptyList={items:[],total:0,page:1,page_size:20};

it('opens a conversation with an explicit null property context',async()=>{
 const user=userEvent.setup();
 let opened=false;
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/conversations?lead_id='))return opened?{items:[convo],total:1,page:1,page_size:20}:emptyList;
  if(url.includes('/api/owner/crm/properties'))return emptyList;
  if(url.includes('/api/owner/crm/conversations')&&init?.method==='POST'){opened=true;return {record:convo};}
  return {record:convo,messages:[],truncated:false,drafting_blocked:null};});
 vi.stubGlobal('fetch',fetch);
 render(<ConversationPanel leadId={LEAD}/>);
 await user.click(await screen.findByRole('button',{name:'Open conversation'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='POST')).toBe(true));
 const post=fetch.mock.calls.find(([,init])=>init?.method==='POST')!;
 expect(JSON.parse(post[1]!.body as string)).toMatchObject({lead_id:LEAD,property_id:null});
 expect(await screen.findByText('Status: open · Handoff: none')).toBeVisible();
});

it('sends a simulated message then drafts a cited grounded answer',async()=>{
 const user=userEvent.setup();
 let sent=false, drafted=false;
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/conversations?lead_id='))return {items:[convo],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/properties'))return emptyList;
  if(url.endsWith('/messages')&&init?.method==='POST'){sent=true;return {record:customer};}
  if(url.endsWith('/draft')&&init?.method==='POST'){drafted=true;return {record:grounded,conversation:convo};}
  return {record:convo,messages:drafted?[customer,grounded]:sent?[customer]:[],truncated:false,drafting_blocked:null};});
 vi.stubGlobal('fetch',fetch);
 render(<ConversationPanel leadId={LEAD}/>);
 await user.click(await screen.findByRole('button',{name:/open · none/}));
 await user.type(await screen.findByLabelText('Simulated customer message'),'Is there a buyer fee?');
 await user.click(screen.getByRole('button',{name:'Send simulated message'}));
 await waitFor(()=>expect(screen.getByText('Is there a buyer fee?')).toBeVisible());
 const post=fetch.mock.calls.find(([u,init])=>String(u).endsWith('/messages')&&init?.method==='POST')!;
 expect(JSON.parse(post[1]!.body as string)).toMatchObject({expected_version:1,role:'customer'});
 await user.click(screen.getByRole('button',{name:'Draft answer'}));
 expect(await screen.findByText('assistant · Grounded')).toBeVisible();
 expect(screen.getByText('No buyer fee applies.')).toBeVisible();
 const link=screen.getByRole('link',{name:'FAQ v1'});
 expect(link).toHaveAttribute('href','/faqs/'+FID);
});

it('shows unsupported answers with reason and blocks drafting while pending',async()=>{
 const user=userEvent.setup();
 const unsupported={...grounded,body:'No verified answer is available. Owner review is required.',grounding:'unsupported',unsupported_reason:'missing_citations',citations:[],confidence:null};
 const pending={...convo,status:'pending_owner'};
 vi.stubGlobal('fetch',stub((url)=>{
  if(url.includes('/api/owner/crm/conversations?lead_id='))return {items:[pending],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/properties'))return emptyList;
  return {record:pending,messages:[customer,unsupported],truncated:false,drafting_blocked:'pending_owner'};}));
 render(<ConversationPanel leadId={LEAD}/>);
 await user.click(await screen.findByRole('button',{name:/pending_owner/}));
 expect(await screen.findByText('Unsupported answer: missing citations. Owner review required.')).toBeVisible();
 expect(screen.getByText('Drafting blocked: pending owner.')).toBeVisible();
 expect(screen.getByRole('button',{name:'Draft answer'})).toBeDisabled();
});

it('requests and resolves handoff with an owner reason',async()=>{
 const user=userEvent.setup();
 const calls:string[]=[];
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/conversations?lead_id='))return {items:[convo],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/properties'))return emptyList;
  if(url.endsWith('/handoff')&&init?.method==='POST'){calls.push(String(init.body));return {record:{...convo,handoff_state:'requested',version:2}};}
  return {record:convo,messages:[],truncated:false,drafting_blocked:null};});
 vi.stubGlobal('fetch',fetch);
 render(<ConversationPanel leadId={LEAD}/>);
 await user.click(await screen.findByRole('button',{name:/open · none/}));
 await user.type(await screen.findByLabelText('Handoff reason'),'Fictional handoff');
 await user.click(screen.getByRole('button',{name:'Request handoff'}));
 await waitFor(()=>expect(calls.length).toBe(1));
 expect(JSON.parse(calls[0])).toMatchObject({expected_version:1,action:'request',reason:'Fictional handoff'});
});

it('acknowledges pending review and resolves the conversation',async()=>{
 const user=userEvent.setup();
 const calls:string[]=[];
 const fetch=stub((url,init)=>{
  if(url.includes('/api/owner/crm/conversations?lead_id='))return {items:[{...convo,status:'pending_owner'}],total:1,page:1,page_size:20};
  if(url.includes('/api/owner/crm/properties'))return emptyList;
  if(url.endsWith('/review')&&init?.method==='POST'){calls.push(String(init.body));return {record:convo};}
  return {record:{...convo,status:'pending_owner'},messages:[],truncated:false,drafting_blocked:'pending_owner'};});
 vi.stubGlobal('fetch',fetch);
 render(<ConversationPanel leadId={LEAD}/>);
 await user.click(await screen.findByRole('button',{name:/pending_owner/}));
 await user.type(await screen.findByLabelText('Handoff reason'),'Fictional review');
 await user.click(screen.getByRole('button',{name:'Acknowledge review'}));
 await waitFor(()=>expect(calls.length).toBe(1));
 expect(JSON.parse(calls[0])).toMatchObject({action:'acknowledge'});
});
