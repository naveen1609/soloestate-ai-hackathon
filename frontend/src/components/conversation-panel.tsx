'use client';
import Link from 'next/link';
import {useEffect,useState} from 'react';
import {crmApi,ConversationDetailSchema,ListSchema,Row,message,useMutation} from '../lib/crm';

export default function ConversationPanel({leadId}:{leadId:string}){
 const [items,setItems]=useState<Row[]|null>(null);const [selected,setSelected]=useState('');const [error,setError]=useState('');
 const [propertyId,setPropertyId]=useState('');const [properties,setProperties]=useState<Row[]>([]);
 const action=useMutation('conversation:open:'+leadId);
 const [reload,setReload]=useState(0);
 useEffect(()=>{let active=true;void crmApi('conversations?lead_id='+leadId+'&page_size=20').then(value=>{if(active){setItems(ListSchema.parse(value).items);setError('');}}).catch(err=>{if(active)setError(message(err));});return()=>{active=false;};},[leadId,reload]);
 useEffect(()=>{let active=true;void crmApi('properties?page_size=100').then(value=>{if(active)setProperties(ListSchema.parse(value).items);}).catch(()=>{if(active)setProperties([]);});return()=>{active=false;};},[]);
 async function open(){const record=await action.execute('conversations',{lead_id:leadId,property_id:propertyId||null});
  if(record){setPropertyId('');setReload(n=>n+1);setSelected(record.id);}}
 return <section className="panel crm-section"><h2>Simulated conversations</h2>
 <p className="subtle">Owner-driven simulation on fictional data. Nothing is sent; contact permission is never inferred here.</p>
 {error&&<p role="alert" className="notice notice-error">{error}</p>}
 <div className="crm-filters"><label>Conversation property context<select aria-label="Conversation property context" value={propertyId} onChange={e=>setPropertyId(e.target.value)} disabled={action.busy||action.pending}><option value="">Business FAQs only</option>{properties.map(row=><option key={row.id} value={row.id}>{String(row.title)}</option>)}</select></label>
 <button className="button button-light" disabled={action.busy} onClick={()=>void open()}>{action.busy?'Opening…':action.pending?'Retry saved conversation':'Open conversation'}</button></div>
 {action.error&&<p role="alert" className="notice notice-error">{action.error}</p>}
 {items&&<div className="crm-actions">{items.map(row=><button key={row.id} className="button button-light" disabled={selected===row.id} onClick={()=>setSelected(row.id)}>{String(row.status)} · {String(row.handoff_state)} · {String(row.id).slice(0,8)}</button>)}{!items.length&&<p className="subtle">No conversations yet.</p>}</div>}
 {selected&&<ConversationDetail key={selected} id={selected} onChanged={()=>setReload(n=>n+1)}/>}</section>;
}

function ConversationDetail({id,onChanged}:{id:string;onChanged:()=>void}){
 const [data,setData]=useState<ReturnType<typeof ConversationDetailSchema.parse>|null>(null);const [error,setError]=useState('');
 const [customerBody,setCustomerBody]=useState('');const [ownerBody,setOwnerBody]=useState('');const [supersedes,setSupersedes]=useState('');
 const [reason,setReason]=useState('');
 const msgAction=useMutation('conversation:'+id+':message');const draftAction=useMutation('conversation:'+id+':draft');
 const stateAction=useMutation('conversation:'+id+':state');
 const [reload,setReload]=useState(0);
 useEffect(()=>{let active=true;void crmApi('conversations/'+id).then(value=>{if(active){setData(ConversationDetailSchema.parse(value));setError('');}}).catch(err=>{if(active)setError(message(err));});return()=>{active=false;};},[id,reload]);
 if(!data)return <>{error&&<p role="alert" className="notice notice-error">{error}</p>}<p role="status">Loading conversation…</p></>;
 const latestCustomer=[...data.messages].reverse().find(row=>row.role==='customer');
 async function send(role:'customer'|'owner',body:string,extra:Record<string,unknown>={}){
  const record=await msgAction.execute('conversations/'+id+'/messages',{expected_version:data!.record.version,role,body,...extra});
  if(record){if(role==='customer')setCustomerBody('');else{setOwnerBody('');setSupersedes('');}setReload(n=>n+1);onChanged();}}
 async function draft(){if(!latestCustomer)return;
  const response=await draftAction.execute('conversations/'+id+'/draft',{expected_version:data!.record.version,message_id:latestCustomer.id});
  if(response){setReload(n=>n+1);onChanged();}}
 async function state(path:'handoff'|'review',payload:Record<string,unknown>){
  const record=await stateAction.execute('conversations/'+id+'/'+path,{expected_version:data!.record.version,...payload});
  if(record){setReason('');setReload(n=>n+1);onChanged();}}
 const blocked=data.drafting_blocked;
 const busy=msgAction.busy||draftAction.busy||stateAction.busy;
 return <div style={{overflowWrap:'anywhere'}}>
 <p role="status">Status: {data.record.status} · Handoff: {data.record.handoff_state}</p>
 {blocked&&<p role="alert">Drafting blocked: {blocked.replaceAll('_',' ')}.</p>}
 {data.truncated&&<p className="subtle">Showing the latest 100 messages.</p>}
 {data.messages.map(row=><article className="crm-row" key={row.id}><p>{row.role}{row.role==='assistant'&&(row.grounding==='grounded'?' · Grounded':row.grounding==='unsupported'?' · Unsupported answer':'')}</p>
  <p>{row.body}</p>
  {row.role==='assistant'&&row.grounding==='grounded'&&<><p className="subtle">Model-reported confidence: {String(row.confidence)}</p>
   <ul>{row.citations.map(item=><li key={item.faq_id+'v'+item.version}><Link href={'/faqs/'+item.faq_id}>FAQ v{item.version}</Link>: {item.quote}</li>)}</ul>
   {row.citations_superseded&&<p role="alert">Cites a superseded FAQ version.</p>}</>}
  {row.role==='assistant'&&row.grounding==='unsupported'&&<p role="alert">Unsupported answer: {String(row.unsupported_reason).replaceAll('_',' ')}. Owner review required.</p>}
 </article>)}
 {msgAction.error&&<p role="alert" className="notice notice-error">{msgAction.error}</p>}
 {draftAction.error&&<p role="alert" className="notice notice-error">{draftAction.error}</p>}
 {stateAction.error&&<p role="alert" className="notice notice-error">{stateAction.error}</p>}
 <div className="crm-filters"><label>Simulated customer message<input aria-label="Simulated customer message" value={customerBody} maxLength={5000} onChange={e=>setCustomerBody(e.target.value)} disabled={busy||data.record.status==='resolved'}/></label>
 <button className="button button-light" disabled={busy||!customerBody.trim()||data.record.status==='resolved'} onClick={()=>void send('customer',customerBody)}>Send simulated message</button>
 <button className="button button-primary" disabled={busy||!latestCustomer||blocked!==null} onClick={()=>void draft()}>{draftAction.busy?'Drafting answer…':'Draft answer'}</button></div>
 <div className="crm-filters"><label>Owner message<input aria-label="Owner message" value={ownerBody} maxLength={5000} onChange={e=>setOwnerBody(e.target.value)} disabled={busy||data.record.status==='resolved'}/></label>
 <label>Link correction to<select aria-label="Link correction to" value={supersedes} onChange={e=>setSupersedes(e.target.value)} disabled={busy||data.record.status==='resolved'}><option value="">No linked message</option>{data.messages.filter(row=>row.role==='assistant').map(row=><option key={row.id} value={row.id}>{row.id.slice(0,8)} · {row.grounding}</option>)}</select></label>
 <button className="button button-light" disabled={busy||!ownerBody.trim()||data.record.status==='resolved'} onClick={()=>void send('owner',ownerBody,supersedes?{supersedes_message_id:supersedes}:{})}>Send owner message</button></div>
 <div className="crm-filters"><label>Handoff reason<input aria-label="Handoff reason" value={reason} maxLength={1000} onChange={e=>setReason(e.target.value)} disabled={busy}/></label>
 {data.record.handoff_state==='requested'
  ?<button className="button button-light" disabled={busy||!reason.trim()} onClick={()=>void state('handoff',{action:'resolve',reason})}>Resolve handoff</button>
  :<button className="button button-light" disabled={busy||!reason.trim()} onClick={()=>void state('handoff',{action:'request',reason})}>Request handoff</button>}
 {data.record.status==='pending_owner'&&<button className="button button-light" disabled={busy||!reason.trim()} onClick={()=>void state('review',{action:'acknowledge',reason})}>Acknowledge review</button>}
 {data.record.status!=='resolved'
  ?<button className="button button-light" disabled={busy||!reason.trim()} onClick={()=>void state('review',{action:'resolve',reason})}>Resolve conversation</button>
  :<button className="button button-light" disabled={busy||!reason.trim()} onClick={()=>void state('review',{action:'reopen',reason})}>Reopen conversation</button>}</div>
 </div>;
}
