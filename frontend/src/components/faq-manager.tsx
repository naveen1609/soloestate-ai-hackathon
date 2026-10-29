'use client';
import Link from 'next/link';
import {FormEvent,useEffect,useState} from 'react';
import CrmShell from './crm-shell';
import {crmApi,FaqDetailSchema,ListSchema,Row,message,useMutation} from '../lib/crm';

export default function FaqManager({id}:{id?:string}){return <CrmShell>{id?<FaqDetail id={id}/>:<FaqList/>}</CrmShell>;}

function FaqList(){
 const [q,setQ]=useState('');const [scope,setScope]=useState('');const [status,setStatus]=useState('active');
 const [data,setData]=useState<ReturnType<typeof ListSchema.parse>|null>(null);const [error,setError]=useState('');
 const [question,setQuestion]=useState('');const [answer,setAnswer]=useState('');const [createScope,setCreateScope]=useState('business');
 const [propertyId,setPropertyId]=useState('');const [properties,setProperties]=useState<Row[]>([]);
 const action=useMutation('faq:new');
 useEffect(()=>{let active=true;const timer=setTimeout(()=>{const params=new URLSearchParams({q,page:'1',page_size:'20'});
  if(scope)params.set('scope',scope);if(status)params.set('status',status);
  void crmApi('faqs?'+params).then(value=>{if(active){setData(ListSchema.parse(value));setError('');}}).catch(err=>{if(active)setError(message(err));});
 },250);return()=>{active=false;clearTimeout(timer);};},[q,scope,status]);
 useEffect(()=>{let active=true;void crmApi('properties?page_size=100').then(value=>{if(active)setProperties(ListSchema.parse(value).items);}).catch(()=>{if(active)setProperties([]);});return()=>{active=false;};},[]);
 async function submit(event:FormEvent){event.preventDefault();
  const record=await action.execute('faqs',{scope:createScope,property_id:createScope==='property'?propertyId:null,question,answer});
  if(record){setQuestion('');setAnswer('');setPropertyId('');const params=new URLSearchParams({q,page:'1',page_size:'20'});
   if(scope)params.set('scope',scope);if(status)params.set('status',status);setData(ListSchema.parse(await crmApi('faqs?'+params)));}}
 return <><div className="crm-heading"><div><p className="eyebrow">KNOWLEDGE BASE</p><h1>FAQs</h1></div></div>
 <section className="panel crm-section"><div className="crm-filters"><label>Search FAQs<input aria-label="Search FAQs" value={q} maxLength={200} onChange={e=>setQ(e.target.value)}/></label><label>Scope<select aria-label="Scope filter" value={scope} onChange={e=>setScope(e.target.value)}><option value="">All scopes</option><option value="business">Business</option><option value="property">Property</option></select></label><label>Status<select aria-label="Status filter" value={status} onChange={e=>setStatus(e.target.value)}><option value="">All statuses</option><option value="active">Active</option><option value="archived">Archived</option></select></label></div>
 {error&&<p role="alert" className="notice notice-error">{error}</p>}{data&&<><p className="subtle">{data.total} records</p>{data.items.map(row=><Link className="crm-row" href={'/faqs/'+row.id} key={row.id}>{String(row.question)} · {String(row.scope)} · v{String(row.current_version)} · {String(row.status)}</Link>)}{!data.items.length&&<p className="crm-empty">No matching records.</p>}</>}</section>
 <form className="panel crm-section crm-form" onSubmit={e=>void submit(e)}><h2>Create FAQ</h2><p className="subtle">Versions are immutable; later edits never rewrite cited history.</p>
 <label>FAQ scope<select aria-label="FAQ scope" value={createScope} onChange={e=>setCreateScope(e.target.value)} disabled={action.busy||action.pending}><option value="business">Business</option><option value="property">Property</option></select></label>
 {createScope==='property'&&<label>FAQ property<select aria-label="FAQ property" value={propertyId} onChange={e=>setPropertyId(e.target.value)} disabled={action.busy||action.pending}><option value="">Select a property</option>{properties.map(row=><option key={row.id} value={row.id}>{String(row.title)}</option>)}</select></label>}
 <label>FAQ question<input aria-label="FAQ question" value={question} maxLength={500} onChange={e=>setQuestion(e.target.value)} disabled={action.busy||action.pending}/></label>
 <label>FAQ answer<textarea aria-label="FAQ answer" value={answer} maxLength={5000} onChange={e=>setAnswer(e.target.value)} disabled={action.busy||action.pending}/></label>
 {action.error&&<p role="alert" className="notice notice-error">{action.error}</p>}
 <button className="button button-primary" disabled={action.busy||(createScope==='property'&&!propertyId)}>{action.busy?'Confirming save…':action.pending?'Retry saved FAQ':'Save FAQ'}</button></form></>;
}

function FaqDetail({id}:{id:string}){
 const [data,setData]=useState<ReturnType<typeof FaqDetailSchema.parse>|null>(null);const [error,setError]=useState('');
 const [reload,setReload]=useState(0);
 const archiveAction=useMutation('faq:archive:'+id);
 useEffect(()=>{let active=true;void crmApi('faqs/'+id).then(value=>{if(active){setData(FaqDetailSchema.parse(value));setError('');}}).catch(err=>{if(active)setError(message(err));});return()=>{active=false;};},[id,reload]);
 async function flip(){if(!data)return;
  const record=await archiveAction.execute('faqs/'+id+'/archive',{expected_version:data.record.version,archived:data.record.status==='active'});
  if(record)setReload(n=>n+1);}
 return <>{error&&<p className="notice notice-error" role="alert">{error}</p>}{!data?<p role="status">Loading FAQ…</p>:<>
 <div className="crm-heading"><div><Link href="/faqs">← FAQs</Link><h1>{data.record.question}</h1><p>{data.record.scope} · v{data.record.current_version} · {data.record.status}</p></div>
 <div className="crm-actions"><button className="button button-light" disabled={archiveAction.busy} onClick={()=>void flip()}>{data.record.status==='active'?'Archive FAQ':'Restore FAQ'}</button></div></div>
 <section className="panel crm-section"><h2>Current answer</h2><p>{data.record.answer}</p>
 <h3>Version history</h3>{data.versions.map(row=><div className="crm-row" key={row.version}><p>v{row.version}{row.version===data.record.current_version?' · current':''} · {String(row.created_at)}</p><p>{row.question}</p><p className="subtle">{row.answer}</p></div>)}</section>
 {archiveAction.error&&<p role="alert" className="notice notice-error">{archiveAction.error}</p>}
 <FaqVersionForm key={data.record.version} id={id} version={data.record.version} question={data.record.question} answer={data.record.answer} archived={data.record.status!=='active'} onSaved={()=>setReload(n=>n+1)}/></>}</>;
}

function FaqVersionForm({id,version,question:initialQ,answer:initialA,archived,onSaved}:{id:string;version:number;question:string;answer:string;archived:boolean;onSaved:()=>void}){
 const [question,setQuestion]=useState(initialQ);const [answer,setAnswer]=useState(initialA);
 const action=useMutation('faq:version:'+id);
 async function submit(event:FormEvent){event.preventDefault();
  const record=await action.execute('faqs/'+id+'/versions',{expected_version:version,question,answer});
  if(record)onSaved();}
 return <form className="panel crm-section crm-form" onSubmit={e=>void submit(e)}><h2>Save new version</h2><p className="subtle">Creates an immutable version; past citations keep pointing at their pinned versions.</p>
 <label>FAQ question<input aria-label="FAQ question" value={question} maxLength={500} onChange={e=>setQuestion(e.target.value)} disabled={action.busy||action.pending}/></label>
 <label>FAQ answer<textarea aria-label="FAQ answer" value={answer} maxLength={5000} onChange={e=>setAnswer(e.target.value)} disabled={action.busy||action.pending}/></label>
 {action.error&&<p role="alert" className="notice notice-error">{action.error}</p>}
 <button className="button button-primary" disabled={action.busy||archived}>{action.busy?'Confirming save…':action.pending?'Retry saved version':'Save new version'}</button></form>;
}
