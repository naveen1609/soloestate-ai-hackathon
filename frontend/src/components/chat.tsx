'use client';
import {useEffect,useState} from 'react';
import {chatApi,ChatConversationResultSchema,ChatLeadResultSchema,ChatSendSchema,ChatStartSchema,ChatThread,ChatThreadSchema,message,useChatMutation} from '../lib/chat';
import {MatchesSchema,SlotsSchema} from '../lib/crm';

function stamp(value:string){const time=new Date(value);return Number.isNaN(time.getTime())?value:time.toLocaleString();}
export default function Chat(){
 const [id,setId]=useState<string|null>(()=>typeof window==='undefined'?null:window.localStorage.getItem('chat:conversation'));
 const [thread,setThread]=useState<ChatThread|null>(null);
 const [error,setError]=useState('');
 const startAction=useChatMutation('start');const sendAction=useChatMutation('send');const reqAction=useChatMutation('requirements');const handAction=useChatMutation('handoff');
 const [name,setName]=useState('');const [email,setEmail]=useState('');
 const [body,setBody]=useState('');const [localities,setLocalities]=useState('');const [budgetMin,setBudgetMin]=useState('');const [budgetMax,setBudgetMax]=useState('');const [bedrooms,setBedrooms]=useState('');const [timeframe,setTimeframe]=useState('');const [reason,setReason]=useState('');
 const [matches,setMatches]=useState<unknown>(null);const [slots,setSlots]=useState<unknown>(null);const [contextBusy,setContextBusy]=useState(false);const [contextError,setContextError]=useState('');
 const [since,setSince]=useState('');const [until,setUntil]=useState('');
 const [reload,setReload]=useState(0);
 useEffect(()=>{if(!id)return;let live=true;
  chatApi('conversations/'+id).then(value=>{if(!live)return;setThread(ChatThreadSchema.parse(value));setError('');}).catch(err=>{if(!live)return;const status=(err as {status?:number}).status;if(status===401||status===404){window.localStorage.removeItem('chat:conversation');setId(null);setThread(null);}else setError(message(err));});
  return ()=>{live=false;};},[id,reload]);
 async function start(){const result=await startAction.execute('start',{name:name.trim(),email:email.trim()},ChatStartSchema);if(result){window.localStorage.setItem('chat:conversation',result.conversation.id);setId(result.conversation.id);setThread({...result,truncated:false,drafting_blocked:null});setMatches(null);setSlots(null);}}
 async function send(){if(!thread)return;const result=await sendAction.execute('conversations/'+id+'/send',{expected_version:thread.conversation.version,body:body.trim()},ChatSendSchema);if(result)setBody('');setReload(n=>n+1);}
 async function saveRequirements(){if(!thread)return;const payload:Record<string,unknown>={expected_version:thread.lead.version};if(localities.trim())payload.preferred_localities=localities.split(',').map(item=>item.trim()).filter(Boolean);if(budgetMin.trim())payload.budget_min=budgetMin.trim();if(budgetMax.trim())payload.budget_max=budgetMax.trim();if(bedrooms.trim())payload.bedrooms=Number(bedrooms);if(timeframe)payload.purchase_timeframe=timeframe;const result=await reqAction.execute('conversations/'+id+'/requirements',payload,ChatLeadResultSchema);if(result){setLocalities('');setBudgetMin('');setBudgetMax('');setBedrooms('');setTimeframe('');}setReload(n=>n+1);}
 async function loadMatches(){if(!id)return;try{setMatches(MatchesSchema.parse(await chatApi('conversations/'+id+'/matches')));}catch(err){setError(message(err));}}
 async function loadSlots(){if(!id)return;try{const params=new URLSearchParams({since:new Date(since).toISOString(),until:new Date(until).toISOString()});setSlots(SlotsSchema.parse(await chatApi('conversations/'+id+'/slots?'+params)));}catch(err){setError(message(err));}}
 async function handoff(){if(!thread)return;const result=await handAction.execute('conversations/'+id+'/handoff',{expected_version:thread.conversation.version,reason:reason.trim()},ChatConversationResultSchema);if(result)setReason('');setReload(n=>n+1);}
 async function setContext(propertyId:string){if(!thread||!id)return;setContextBusy(true);setContextError('');try{ChatConversationResultSchema.parse(await chatApi('conversations/'+id+'/context',{method:'POST',body:JSON.stringify({request_key:crypto.randomUUID(),expected_version:thread.conversation.version,property_id:propertyId})}));}catch(err){setContextError(message(err));}finally{setContextBusy(false);setReload(n=>n+1);}}
 function quit(){window.localStorage.removeItem('chat:conversation');setId(null);setThread(null);setError('');setMatches(null);setSlots(null);}
 const blocked=thread?[thread.conversation.status!=='open'?`This chat is ${thread.conversation.status}.`:null,thread.drafting_blocked].filter(Boolean).join(' '):'';
 return <div className="crm-shell"><header className="crm-header"><span className="crm-brand">SoloEstate <span>AI chat</span></span>{id&&<button className="button button-light" onClick={quit}>New chat</button>}</header>
 <main className="crm-main">
 {error&&<p role="alert" className="notice notice-error">{error}</p>}
 {!id&&<section className="panel"><h1>Website chat</h1><p className="subtle">Ask about properties. Starting a chat creates a website visitor record the owner can follow up.</p>
  {startAction.error&&<p role="alert" className="notice notice-error">{startAction.error}</p>}
  <div className="crm-filters"><label>Name<input aria-label="Name" value={name} maxLength={200} onChange={e=>setName(e.target.value)} disabled={startAction.busy}/></label>
  <label>Email<input aria-label="Email" value={email} maxLength={254} onChange={e=>setEmail(e.target.value)} disabled={startAction.busy}/></label>
  <button className="button button-primary" disabled={startAction.busy||!name.trim()||!email.trim()} onClick={()=>void start()}>{startAction.busy?'Starting…':startAction.pending?'Retry saved start':'Start chat'}</button></div></section>}
 {thread&&<section className="panel"><h1>Website chat</h1>
  <p className="subtle">Status: {thread.conversation.status} · Follow-up: {thread.conversation.handoff_state}{thread.conversation.property_id?` · Focus: ${thread.conversation.property_id.slice(0,8)}`:''}{blocked?` · ${blocked}`:''}</p>
  {sendAction.error&&<p role="alert" className="notice notice-error">{sendAction.error}</p>}
  <ol className="chat-thread">{thread.messages.map(item=><li key={item.id} className={'chat-'+item.role}><p><strong>{item.role}:</strong> {item.body}</p>
   {item.role==='assistant'&&item.grounding==='grounded'&&<p className="subtle">Grounded · confidence {item.confidence}{item.citations.map(cite=><span key={cite.faq_id+cite.version}> · FAQ v{cite.version}: “{cite.quote}”</span>)}{item.citations_superseded&&<span> · Citations changed since this answer.</span>}</p>}
   {item.grounding==='unsupported'&&<p role="note" className="notice">No verified answer is available ({item.unsupported_reason}). The owner has been asked to review.</p>}</li>)}</ol>
  {thread.truncated&&<p className="subtle">Showing the latest messages.</p>}
  <div className="crm-filters"><label>Message<input aria-label="Message" value={body} maxLength={5000} onChange={e=>setBody(e.target.value)} disabled={sendAction.busy||!!blocked}/></label>
  <button className="button button-primary" disabled={sendAction.busy||!!blocked||!body.trim()} onClick={()=>void send()}>{sendAction.busy?'Sending…':sendAction.pending?'Retry saved message':'Send'}</button>
  <button className="button button-light" onClick={()=>setReload(n=>n+1)}>Refresh</button></div></section>}
 {thread&&<section className="panel crm-section"><h2>What are you looking for?</h2>
  <p className="subtle">Current: {[thread.lead.preferred_localities?.join(', ')||'no localities',thread.lead.budget_min??'no minimum',thread.lead.budget_max??'no maximum',thread.lead.bedrooms??'no bedrooms',thread.lead.purchase_timeframe??'no timeframe'].join(' · ')}{thread.lead.readiness.matching_missing_fields.length?` · Still needed: ${thread.lead.readiness.matching_missing_fields.join(', ')}`:''}</p>
  {reqAction.error&&<p role="alert" className="notice notice-error">{reqAction.error}</p>}
  <div className="crm-filters"><label>Localities<input aria-label="Localities" value={localities} onChange={e=>setLocalities(e.target.value)} disabled={reqAction.busy} placeholder="Bellandur, Whitefield"/></label>
  <label>Minimum budget<input aria-label="Minimum budget" value={budgetMin} onChange={e=>setBudgetMin(e.target.value)} disabled={reqAction.busy}/></label>
  <label>Maximum budget<input aria-label="Maximum budget" value={budgetMax} onChange={e=>setBudgetMax(e.target.value)} disabled={reqAction.busy}/></label>
  <label>Bedrooms<input aria-label="Bedrooms" value={bedrooms} onChange={e=>setBedrooms(e.target.value)} disabled={reqAction.busy}/></label>
  <label>Timeframe<select aria-label="Timeframe" value={timeframe} onChange={e=>setTimeframe(e.target.value)} disabled={reqAction.busy}><option value="">No change</option><option value="within_3_months">Within 3 months</option><option value="three_to_six_months">3–6 months</option><option value="later">Later</option><option value="no_fixed_deadline">No fixed deadline</option></select></label>
  <button className="button button-light" disabled={reqAction.busy} onClick={()=>void saveRequirements()}>{reqAction.busy?'Saving…':reqAction.pending?'Retry saved requirements':'Save requirements'}</button></div>
  <div className="crm-filters"><button className="button button-light" onClick={()=>void loadMatches()}>Show matching properties</button></div>
  {contextError&&<p role="alert" className="notice notice-error">{contextError}</p>}
  {matches!=null&&<ul>{(matches as {matches:{id:string;title:string;price:string|number;bedrooms:number;score:number}[];missing_fields:string[]}).matches.map(item=><li key={item.id}>{item.title} · {String(item.price)} · {item.bedrooms} bedrooms · score {item.score}{item.id===thread.conversation.property_id?' · focus':''} <button className="button button-light" disabled={contextBusy||thread.conversation.status==='resolved'} onClick={()=>void setContext(item.id)}>Ask about this property</button></li>)}{(matches as {missing_fields:string[]}).missing_fields.length>0&&<li>Still needed: {(matches as {missing_fields:string[]}).missing_fields.join(', ')}</li>}</ul>}</section>}
 {thread&&<section className="panel crm-section"><h2>Visit availability</h2><p className="subtle">Provisional only — the owner confirms every visit.</p>
  <div className="crm-filters"><label>From<input aria-label="From" type="datetime-local" value={since} onChange={e=>setSince(e.target.value)}/></label>
  <label>Until<input aria-label="Until" type="datetime-local" value={until} onChange={e=>setUntil(e.target.value)}/></label>
  <button className="button button-light" disabled={!since||!until} onClick={()=>void loadSlots()}>Show availability</button></div>
  {slots!=null&&<ul>{(slots as {items:{id:string;starts_at:string;ends_at:string}[]}).items.map(item=><li key={item.id}>{stamp(item.starts_at)} – {stamp(item.ends_at)}</li>)}</ul>}</section>}
 {thread&&thread.conversation.handoff_state==='none'&&<section className="panel crm-section"><h2>Talk to the owner</h2>
  {handAction.error&&<p role="alert" className="notice notice-error">{handAction.error}</p>}
  <div className="crm-filters"><label>Reason<input aria-label="Handoff reason" value={reason} maxLength={1000} onChange={e=>setReason(e.target.value)} disabled={handAction.busy}/></label>
  <button className="button button-light" disabled={handAction.busy||!reason.trim()} onClick={()=>void handoff()}>{handAction.busy?'Requesting…':handAction.pending?'Retry saved request':'Request owner review'}</button></div></section>}
 </main></div>;
}
