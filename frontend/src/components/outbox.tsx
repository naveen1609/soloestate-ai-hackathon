'use client';
import {FormEvent,useCallback,useEffect,useState} from 'react';
import CrmShell from './crm-shell';
import {crmApi,message,useMutation} from '../lib/crm';
import {OUTBOX_ACTIONS,OUTBOX_STATUSES,OutboxListSchema,OutboxResultSchema} from '../lib/outbox';
import {date,text} from '../lib/types';

const ACTION_HELP:Record<string,string>={mark_resolved_sent:'Confirm the provider actually delivered it. Evidence is required.',authorize_resend:'Queue another attempt. The customer may receive this twice.',abandon:'Terminally drop this message and release its cap reservation.'};

function Detail({id,onDone}:{id:string;onDone:()=>void}){
 const [data,setData]=useState<ReturnType<typeof OutboxListSchema.parse>|null>(null);
 const [error,setError]=useState('');const [action,setAction]=useState<string>('authorize_resend');
 const [reason,setReason]=useState('');const [evidence,setEvidence]=useState('');
 const resolve=useMutation('outbox:resolve:'+id,OutboxResultSchema);const requeue=useMutation('outbox:requeue:'+id,OutboxResultSchema);
 const [notice,setNotice]=useState('');
 const load=useCallback(async()=>OutboxListSchema.parse(await crmApi('outbox?page_size=50')),[]);
 const reload=useCallback(async()=>{try{setData(await load());setError('');}catch(err){setError(message(err));}},[load]);
 useEffect(()=>{let active=true;void load().then(v=>{if(active){setData(v);setError('');}}).catch(err=>{if(active)setError(message(err));});return()=>{active=false;};},[load]);
 if(error&&!data)return <p role="alert" className="notice notice-error">{error}</p>;
 if(!data)return <p role="status">Loading message…</p>;
 const row=data.items.find(item=>item.id===id);
 if(!row)return <p role="status">Message left the queue.</p>;
 const status=text(row.status);const busy=resolve.busy||requeue.busy;
 async function submitResolve(e:FormEvent){
  e.preventDefault();setNotice('');
  const updated=await resolve.execute('outbox/'+id+'/resolve',{action,reason,...(evidence.trim()?{evidence:evidence.trim()}:{})});
  if(updated){
   if(action==='authorize_resend')setNotice('Resend queued. The original attempt may have submitted: the customer may receive this twice.');
   setReason('');setEvidence('');await reload();onDone();}
 }
 async function submitRequeue(){
  const updated=await requeue.execute('outbox/'+id+'/requeue',{reason:reason||'Owner requeued from the outbox.'});
  if(updated){setReason('');await reload();onDone();}
 }
 const payload=(row.payload&&typeof row.payload==='object'?row.payload: {}) as Record<string,unknown>;
 return <section className="panel crm-section"><p className="eyebrow">OUTBOX · {status.toUpperCase()}</p><h2>Queued message</h2>
 <dl className="crm-details"><div><dt>Destination</dt><dd>{text(row.destination)}</dd></div>
 <div><dt>Category</dt><dd>{text(row.category)}</dd></div>
 <div><dt>Attempts</dt><dd>{text(row.attempts)}{Number(row.resend_count)>0?` · ${text(row.resend_count)} owner resend(s)`:''}</dd></div>
 <div><dt>Skip reason</dt><dd>{text(row.skip_reason)||'—'}</dd></div>
 <div><dt>Provider id</dt><dd>{text(row.provider_msg_id)||'—'}</dd></div><div><dt>Queued</dt><dd>{date(row.created_at)}</dd></div></dl>
 <p>{text(payload.body)}</p>
 {(resolve.error||requeue.error)&&<p role="alert" className="notice notice-error">{resolve.error||requeue.error}</p>}
 {notice&&<p role="status" className="notice">{notice}</p>}
 {status==='unknown'&&<form className="crm-form" onSubmit={e=>void submitResolve(e)}><fieldset disabled={resolve.busy||resolve.pending}><div className="crm-columns"><label>Resolution<select aria-label="Resolution" value={action} onChange={e=>setAction(e.target.value)}>{OUTBOX_ACTIONS.map(a=><option key={a} value={a}>{a}</option>)}</select></label><label>Reason<input required maxLength={1000} value={reason} onChange={e=>setReason(e.target.value)}/></label></div>
 <p className="subtle">{ACTION_HELP[action]}</p>
 {action==='mark_resolved_sent'&&<label>Delivery evidence<input aria-label="Delivery evidence" required maxLength={2000} value={evidence} onChange={e=>setEvidence(e.target.value)}/> </label>}</fieldset>
 <button className="button button-primary" disabled={resolve.busy||!reason.trim()||(action==='mark_resolved_sent'&&!evidence.trim())}>{resolve.busy?'Recording…':resolve.pending?'Retry saved resolution':'Record resolution'}</button></form>}
 {(status==='dead'||status==='failed'||status==='skipped')&&<div className="crm-actions"><button className="button button-primary" disabled={busy} onClick={()=>void submitRequeue()}>{requeue.busy?'Requeueing…':requeue.pending?'Retry saved requeue':'Requeue message'}</button></div>}</section>;
}

export default function Outbox(){
 const [status,setStatus]=useState('');const [selected,setSelected]=useState('');const [version,setVersion]=useState(0);
 const [data,setData]=useState<ReturnType<typeof OutboxListSchema.parse>|null>(null);const [error,setError]=useState('');
 useEffect(()=>{let active=true;void (async()=>{try{const list=OutboxListSchema.parse(await crmApi('outbox?page_size=50'+(status?'&status='+status:'')));if(active){setData(list);setError('');}}catch(err){if(active)setError(message(err));}})();return()=>{active=false;};},[status,version]);
 return <CrmShell><div className="crm-heading"><div><p className="eyebrow">DELIVERY QUEUE</p><h1>Message outbox</h1><p>Durable WhatsApp delivery. Uncertain sends wait for an owner decision.</p></div></div>
 <section className="panel crm-section"><label>Status filter<select aria-label="Status filter" value={status} onChange={e=>{setStatus(e.target.value);setSelected('');}}><option value="">All statuses</option>{OUTBOX_STATUSES.map(s=><option key={s} value={s}>{s}</option>)}</select></label>
 {error&&<p role="alert" className="notice notice-error">{error}</p>}
 {!data?<p role="status">Loading outbox…</p>:data.items.length?data.items.map(r=><button key={r.id} className="crm-row" onClick={()=>setSelected(String(r.id))}>{text(r.status)} · {text(r.destination)} · {text(r.id).slice(0,8)}</button>):<p className="crm-empty">No matching messages.</p>}</section>
 {selected&&<Detail key={selected} id={selected} onDone={()=>setVersion(v=>v+1)}/>}</CrmShell>;
}
