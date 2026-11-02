'use client';
import {FormEvent,useCallback,useEffect,useState} from 'react';
import CrmShell from './crm-shell';
import {crmApi,ListSchema,Row,message,useMutation} from '../lib/crm';
import {REQUEST_STATUSES,RequestDetailSchema,TERMINAL_REQUEST} from '../lib/policy';
import {date,text} from '../lib/types';

function localInput(value:string){const d=new Date(value);return Number.isNaN(d.getTime())?'':new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}

function Detail({id,onDone}:{id:string;onDone:()=>void}){
 const load=useCallback(async()=>RequestDetailSchema.parse(await crmApi('booking-requests/'+id)),[id]);
 const [data,setData]=useState<ReturnType<typeof RequestDetailSchema.parse>|null>(null);
 const [error,setError]=useState('');const [resources,setResources]=useState<Row[]>([]);
 const confirm=useMutation('request:confirm:'+id);const decide=useMutation('request:decide:'+id);
 const commit=useMutation('request:commit:'+id);const cancel=useMutation('request:cancel:'+id);
 const [decision,setDecision]=useState('approved');const [reason,setReason]=useState('');
 const [subStart,setSubStart]=useState('');const [subEnd,setSubEnd]=useState('');const [subResource,setSubResource]=useState('');
 const reload=useCallback(async()=>{try{setData(await load());setError('');}catch(err){setError(message(err));}},[load]);
 useEffect(()=>{let active=true;void load().then(v=>{if(active){setData(v);const r=v.record;setSubStart(r.owner_approved_start||r.customer_confirmed_start?localInput(text(r.owner_approved_start||r.customer_confirmed_start)):localInput(text(r.requested_start)));setSubEnd(r.owner_approved_end||r.customer_confirmed_end?localInput(text(r.owner_approved_end||r.customer_confirmed_end)):localInput(text(r.requested_end)));}}).catch(err=>{if(active)setError(message(err));});void crmApi('resources').then(v=>{if(active)setResources(ListSchema.parse(v).items);}).catch(()=>{});return()=>{active=false;};},[load]);
 if(error&&!data)return <p role="alert" className="notice notice-error">{error}</p>;
 if(!data)return <p role="status">Loading request…</p>;
 const r=data.record;const status=text(r.status);const busy=confirm.busy||decide.busy||commit.busy||cancel.busy;
 const reasons=Array.isArray(r.exception_reasons)?r.exception_reasons:[];
 async function run(action:ReturnType<typeof useMutation>,path:string,body:Record<string,unknown>){const updated=await action.execute(path,body);await reload();if(updated)setReason('');onDone();}
 async function submitDecide(e:FormEvent){e.preventDefault();await run(decide,'booking-requests/'+id+'/decide',{expected_version:r.version,expected_request_hash:r.request_hash,decision,reason,...(subStart&&subEnd?{approved_start:new Date(subStart).toISOString(),approved_end:new Date(subEnd).toISOString()}:{}),...(subResource?{approved_resource_id:subResource}:{})});}
 return <section className="panel crm-section"><p className="eyebrow">BOOKING REQUEST · {status.toUpperCase()}</p><h2>Requested visit</h2>
 <dl className="crm-details"><div><dt>Requested window</dt><dd>{date(r.requested_start)} – {date(r.requested_end)}</dd></div>
 <div><dt>Customer-confirmed</dt><dd>{r.customer_confirmed_start?`${date(r.customer_confirmed_start)} – ${date(r.customer_confirmed_end)}`:'—'}</dd></div>
 <div><dt>Owner-approved</dt><dd>{r.owner_approved_start?`${date(r.owner_approved_start)} – ${date(r.owner_approved_end)}`:'—'}</dd></div>
 <div><dt>Policy exceptions</dt><dd>{reasons.length?reasons.join(', '):'none'}</dd></div>
 <div><dt>Policy version</dt><dd>{text(r.policy_version)}</dd></div><div><dt>Expires</dt><dd>{date(r.expires_at)}</dd></div></dl>
 {(confirm.error||decide.error||commit.error||cancel.error)&&<p role="alert" className="notice notice-error">{confirm.error||decide.error||commit.error||cancel.error}</p>}
 {data.alternatives.length>0&&<><h3>Suggested alternatives</h3><ul className="crm-list">{data.alternatives.map(a=><li key={a.id}>{date(a.starts_at)} – {date(a.ends_at)}</li>)}</ul></>}
 <div className="crm-actions">
 {status==='proposed'&&<button className="button button-primary" disabled={busy} onClick={()=>void run(confirm,'booking-requests/'+id+'/confirm',{expected_version:r.version})}>{confirm.busy?'Confirming…':confirm.pending?'Retry saved confirmation':'Confirm proposed window'}</button>}
 {status==='approved'&&<button className="button button-primary" disabled={busy} onClick={()=>void run(commit,'booking-requests/'+id+'/commit',{expected_request_hash:r.request_hash})}>{commit.busy?'Committing…':commit.pending?'Retry saved commit':'Commit booking'}</button>}
 {!TERMINAL_REQUEST.includes(status)&&<button className="button button-danger" disabled={busy} onClick={()=>void run(cancel,'booking-requests/'+id+'/cancel',{expected_version:r.version,reason:reason||'Owner cancelled the request.'})}>{cancel.busy?'Cancelling…':'Cancel request'}</button>}
 </div>
 {status==='awaiting_owner'&&<form className="crm-form" onSubmit={e=>void submitDecide(e)}><fieldset disabled={decide.busy||decide.pending}><div className="crm-columns"><label>Decision<select aria-label="Decision" value={decision} onChange={e=>setDecision(e.target.value)}><option value="approved">Approve</option><option value="denied">Deny</option></select></label><label>Decision reason<input required maxLength={1000} value={reason} onChange={e=>setReason(e.target.value)}/></label></div>
 <div className="crm-columns"><label>Approved start (blank keeps request)<input type="datetime-local" value={subStart} onChange={e=>setSubStart(e.target.value)}/></label><label>Approved end<input type="datetime-local" value={subEnd} onChange={e=>setSubEnd(e.target.value)}/></label></div>
 <label>Approved resource<select value={subResource} onChange={e=>setSubResource(e.target.value)}><option value="">Keep requested resource</option>{resources.map(x=><option key={x.id} value={x.id}>{text(x.name)}</option>)}</select></label></fieldset>
 <button className="button button-primary" disabled={decide.busy||!reason.trim()}>{decide.busy?'Recording…':decide.pending?'Retry saved decision':'Record decision'}</button></form>}</section>;
}

export default function BookingRequests(){
 const [status,setStatus]=useState('');const [selected,setSelected]=useState('');
 const [data,setData]=useState<ReturnType<typeof ListSchema.parse>|null>(null);const [error,setError]=useState('');
 const reload=useCallback(async()=>{try{setData(ListSchema.parse(await crmApi('booking-requests?page_size=50'+(status?'&status='+status:''))));setError('');}catch(err){setError(message(err));}},[status]);
 useEffect(()=>{let active=true;void (async()=>{try{const list=ListSchema.parse(await crmApi('booking-requests?page_size=50'+(status?'&status='+status:'')));if(active){setData(list);setError('');}}catch(err){if(active)setError(message(err));}})();return()=>{active=false;};},[status]);
 return <CrmShell><div className="crm-heading"><div><p className="eyebrow">OWNER APPROVAL QUEUE</p><h1>Booking requests</h1><p>Policy-checked visit requests. Nothing books without a confirmed commit.</p></div></div>
 <section className="panel crm-section"><label>Status filter<select aria-label="Status filter" value={status} onChange={e=>{setStatus(e.target.value);setSelected('');}}><option value="">All statuses</option>{REQUEST_STATUSES.map(s=><option key={s} value={s}>{s}</option>)}</select></label>
 {error&&<p role="alert" className="notice notice-error">{error}</p>}
 {!data?<p role="status">Loading requests…</p>:data.items.length?data.items.map(r=><button key={r.id} className="crm-row" onClick={()=>setSelected(String(r.id))}>{text(r.status)} · {date(r.requested_start)} · {text(r.id).slice(0,8)}</button>):<p className="crm-empty">No matching requests.</p>}</section>
 {selected&&<Detail key={selected} id={selected} onDone={()=>void reload()}/>}</CrmShell>;
}
