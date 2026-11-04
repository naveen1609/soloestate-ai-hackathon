// @vitest-environment jsdom
import {beforeEach,expect,it,vi} from 'vitest';
import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Outbox from './outbox';
const OID='80000000-0000-4000-8000-000000000011';
function row(overrides={}){
 return {id:OID,channel:'whatsapp',purpose:'reply',source_id:OID,lead_id:OID,conversation_id:OID,identity_link_id:OID,destination:'+15550001111',verified_destination:null,destination_basis:'in_thread',category:'welcome',content_ref:'reply:test',policy_version:1,consent:{},payload:{body:'Fictional body'},catalog_version:'PHRASEBOOK_V1',status:'unknown',skip_reason:null,provider_msg_id:null,reserved_day:'2026-11-02',reserved:true,resend_count:0,attempts:1,created_at:'2026-11-02T10:00:00+00:00',updated_at:'2026-11-02T10:00:00+00:00',...overrides};
}
beforeEach(()=>{sessionStorage.clear();vi.unstubAllGlobals();});
function stub(handler:(url:string,init?:RequestInit)=>unknown,items=[row()]){
 return vi.fn(async(input:unknown,init?:RequestInit)=>{const url=String(input);if(url==='/api/session')return Response.json({authenticated:true});if(url.includes('/api/owner/crm/outbox?'))return Response.json({items,total:items.length,page:1,page_size:50});return Response.json(handler(url,init));});
}

it('lists queued messages and shows the unknown detail',async()=>{
 const user=userEvent.setup();
 vi.stubGlobal('fetch',stub(()=>({})));
 render(<Outbox/>);
 await user.click(await screen.findByRole('button',{name:/unknown/}));
 expect(await screen.findByText('OUTBOX · UNKNOWN')).toBeVisible();
 expect(screen.getByText('Fictional body')).toBeVisible();
 expect(screen.getByText(/may receive this twice/)).toBeVisible();
});

it('authorizes a resend with reason and shows the duplicate notice',async()=>{
 const user=userEvent.setup();
 const fetch=stub((url,init)=>{
  if(url.includes('/resolve')&&init?.method==='POST')return {record:row({status:'staged',resend_count:1}),duplicate_risk:true};
  return {};});
 vi.stubGlobal('fetch',fetch);
 render(<Outbox/>);
 await user.click(await screen.findByRole('button',{name:/unknown/}));
 await user.type(await screen.findByLabelText('Reason'),'Fictional owner resend');
 await user.click(screen.getByRole('button',{name:'Record resolution'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).includes('/resolve')&&init?.method==='POST')).toBe(true));
 const post=fetch.mock.calls.find(([url,init])=>String(url).includes('/resolve')&&init?.method==='POST')!;
 expect(JSON.parse(post[1]!.body as string)).toMatchObject({action:'authorize_resend',reason:'Fictional owner resend'});
 expect(await screen.findByText('Resend queued. The original attempt may have submitted: the customer may receive this twice.')).toBeVisible();
});

it('requires evidence to mark resolved as sent',async()=>{
 const user=userEvent.setup();
 vi.stubGlobal('fetch',stub(()=>({})));
 render(<Outbox/>);
 await user.click(await screen.findByRole('button',{name:/unknown/}));
 await user.selectOptions(await screen.findByLabelText('Resolution'),'mark_resolved_sent');
 expect(screen.getByRole('button',{name:'Record resolution'})).toBeDisabled();
 await user.type(screen.getByLabelText('Reason'),'Fictional confirm');
 await user.type(screen.getByLabelText('Delivery evidence'),'Provider dashboard shows delivery');
 expect(screen.getByRole('button',{name:'Record resolution'})).toBeEnabled();
});

it('requeues dead rows',async()=>{
 const user=userEvent.setup();
 const dead=row({status:'dead'});
 const fetch=stub((url,init)=>{
  if(url.includes('/requeue')&&init?.method==='POST')return {record:{...dead,status:'staged'}};
  return {};},[dead]);
 vi.stubGlobal('fetch',fetch);
 render(<Outbox/>);
 await user.click(await screen.findByRole('button',{name:/dead/}));
 await user.click(await screen.findByRole('button',{name:'Requeue message'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).includes('/requeue')&&init?.method==='POST')).toBe(true));
});

it('refreshes the queue after a resolution',async()=>{
 const user=userEvent.setup();
 let lists=0;
 vi.stubGlobal('fetch',vi.fn(async(input:unknown,init?:RequestInit)=>{
  const url=String(input);
  if(url==='/api/session')return Response.json({authenticated:true});
  if(url.includes('/api/owner/crm/outbox?')){lists+=1;const items=lists<=2?[row()]:[];return Response.json({items,total:items.length,page:1,page_size:50});}
  if(url.includes('/resolve')&&init?.method==='POST')return Response.json({record:row({status:'sent'})});
  return Response.json({});}));
 render(<Outbox/>);
 await user.click(await screen.findByRole('button',{name:/unknown/}));
 await user.selectOptions(await screen.findByLabelText('Resolution'),'mark_resolved_sent');
 await user.type(screen.getByLabelText('Reason'),'Fictional confirm');
 await user.type(screen.getByLabelText('Delivery evidence'),'Provider dashboard shows delivery');
 await user.click(screen.getByRole('button',{name:'Record resolution'}));
 await waitFor(()=>expect(screen.queryByRole('button',{name:/unknown/})).not.toBeInTheDocument());
});

it('keeps failed resolutions visible without claiming success',async()=>{
 const user=userEvent.setup();
 vi.stubGlobal('fetch',vi.fn(async(input:unknown,init?:RequestInit)=>{
  const url=String(input);
  if(url==='/api/session')return Response.json({authenticated:true});
  if(url.includes('/api/owner/crm/outbox?'))return Response.json({items:[row()],total:1,page:1,page_size:50});
  if(url.includes('/outbox/')&&init?.method==='POST')return Response.json({message:'Only unknown rows accept owner resolution.'},{status:409});
  return Response.json({});}));
 render(<Outbox/>);
 await user.click(await screen.findByRole('button',{name:/unknown/}));
 await user.type(await screen.findByLabelText('Reason'),'Fictional attempt');
 await user.click(screen.getByRole('button',{name:'Record resolution'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Only unknown rows');
});
