'use client';
import Link from 'next/link';
import {FormEvent,useCallback,useEffect,useState} from 'react';
import {useRouter} from 'next/navigation';
import CrmShell from './crm-shell';
import {crmApi,DetailSchema,ListSchema,SlotsSchema,Row,message,useMutation} from '../lib/crm';
import {date,money,object,text} from '../lib/types';

function localTime(date:Date){return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);}

function Review({request,onDone}:{request:Row;onDone:()=>void}){
 const router=useRouter();const commit=useMutation('confirm:'+request.id);const reject=useMutation('reject:'+request.id);
 const payload=object(request.payload),replacement=object(request.replacement);
 const busy=commit.busy||reject.busy;
 async function decide(approve:boolean){const action=approve?commit:reject;const result=await action.execute('appointment-requests/'+request.id+(approve?'/commit':'/reject'),{expected_payload_hash:request.payload_hash});if(result){if(approve)router.push('/appointments/'+result.id);else onDone();}}
 return <section className="panel crm-section crm-review"><p className="eyebrow">EXPLICIT OWNER CONSENT</p><h2>Review appointment request</h2><p>Nothing is booked until you approve and the backend confirms the transaction.</p><dl className="crm-details"><div><dt>Customer</dt><dd>{text(payload.recipient)}</dd></div><div><dt>Property</dt><dd>{text(payload.property_title)} · {money(payload.price)}</dd></div><div><dt>Visit</dt><dd>{date(payload.starts_at)} – {date(payload.ends_at)}</dd></div><div><dt>Request status</dt><dd>{text(request.approval_status)}</dd></div>{Boolean(replacement.replaces_booking_id)&&<div><dt>Replaces appointment</dt><dd><Link href={'/appointments/'+text(replacement.replaces_booking_id)}>{text(replacement.replaces_booking_id)}</Link><p>The original stays confirmed unless the replacement is committed successfully.</p></dd></div>}</dl><p className="subtle">Snapshot {text(request.payload_hash).slice(0,12)}… · No customer message or external calendar invite is sent.</p>
 {typeof request.booking_id==='string'&&<p><Link className="button button-light" href={'/appointments/'+request.booking_id}>Open committed appointment</Link></p>}{(commit.error||reject.error)&&<p role="alert" className="notice notice-error">{commit.error||reject.error}</p>}<div className="crm-actions"><button className="button button-primary" disabled={busy||reject.pending||(!commit.pending&&!['pending','approved'].includes(text(request.approval_status)))} onClick={()=>void decide(true)}>{commit.busy?'Committing booking…':commit.pending?'Retry saved booking':replacement.replaces_booking_id?'Approve and reschedule':'Approve and book'}</button><button className="button button-danger" disabled={busy||commit.pending||(!reject.pending&&request.approval_status!=='pending')} onClick={()=>void decide(false)}>{reject.pending?'Retry saved rejection':'Reject request'}</button></div></section>;
}

function Scheduler(){
 const router=useRouter();
 const [leads,setLeads]=useState<Row[]>([]),[properties,setProperties]=useState<Row[]>([]),[slots,setSlots]=useState<Row[]>([]);
 const [lead,setLead]=useState(''),[property,setProperty]=useState(''),[slot,setSlot]=useState('');
 const [leadSearch,setLeadSearch]=useState(''),[propertySearch,setPropertySearch]=useState('');
 const [old,setOld]=useState<Row|null>(null),[review,setReview]=useState<Row|null>(null);
 const [loading,setLoading]=useState(true),[error,setError]=useState('');const [availabilityLoading,setAvailabilityLoading]=useState(false);
 const [since,setSince]=useState(()=>localTime(new Date(Date.now()+60000))),[until,setUntil]=useState(()=>localTime(new Date(Date.now()+14*86400000)));
 const [start,setStart]=useState(''),[end,setEnd]=useState('');
 const action=useMutation('prepare-appointment');const slotAction=useMutation('create-visit-slot');
 const loadAvailability=useCallback(async()=>{
  setAvailabilityLoading(true);setError('');try{
   const query=new URLSearchParams({since:new Date(since).toISOString(),until:new Date(until).toISOString()});
   const value=SlotsSchema.parse(await crmApi('visit-slots?'+query));setSlots(value.items);setSlot('');
   if(value.truncated)setError('Showing the first 100 slots. Narrow the date window to find more.');
  }catch(err){setSlots([]);setSlot('');setError(message(err));}finally{setAvailabilityLoading(false);}
 },[since,until]);
 useEffect(()=>{let active=true;async function load(){try{
  const params=new URLSearchParams(window.location.search);
  if(params.has('request')){const data=DetailSchema.parse(await crmApi('appointment-requests/'+params.get('request')));if(active)setReview(data.record);return;}
  const [leadData,propertyData]=await Promise.all([crmApi('leads?page_size=100'),crmApi('properties?active=true&page_size=100')]);
  const availableLeads=ListSchema.parse(leadData).items;const availableProperties=ListSchema.parse(propertyData).items;
  if(params.has('replace')){
   const appointment=DetailSchema.parse(await crmApi('appointments/'+params.get('replace'))).record;
   const l=DetailSchema.parse(await crmApi('leads/'+text(appointment.lead_id))).record;
   const p=DetailSchema.parse(await crmApi('properties/'+text(appointment.property_id))).record;
   if(active){setOld(appointment);setLead(l.id);setProperty(p.id);if(!availableLeads.some(r=>r.id===l.id))availableLeads.push(l);if(!availableProperties.some(r=>r.id===p.id))availableProperties.push(p);}
  }else if(params.has('lead_id')){
   const l=DetailSchema.parse(await crmApi('leads/'+params.get('lead_id'))).record;
   if(active){setLead(l.id);if(!availableLeads.some(r=>r.id===l.id))availableLeads.push(l);}
  }
  if(active){setLeads(availableLeads);setProperties(availableProperties);}
 }catch(err){if(active)setError(message(err));}finally{if(active)setLoading(false);}}void load();return()=>{active=false;};},[]);
 async function search(entity:'leads'|'properties'){
  try{const result=ListSchema.parse(await crmApi(entity+'?page_size=100&q='+encodeURIComponent(entity==='leads'?leadSearch:propertySearch)+(entity==='properties'?'&active=true':'')));if(entity==='leads'){setLeads(result.items);setLead('');}else{setProperties(result.items);setProperty('');}setError('');}catch(err){setError(message(err));}
 }
 async function prepare(event:FormEvent){event.preventDefault();const result=await action.execute('appointment-requests',{lead_id:lead,property_id:property,slot_id:slot,...(old?{replaces_booking_id:old.id,expected_version:old.version}:{})});if(result){setReview(result);window.history.replaceState({},'','/appointments/new?request='+result.id);}}
 async function createSlot(event:FormEvent){event.preventDefault();try{const result=await slotAction.execute('visit-slots',slotAction.pending?{}:{starts_at:new Date(start).toISOString(),ends_at:new Date(end).toISOString()});if(result){
  const from=new Date(text(result.starts_at)),to=new Date(new Date(text(result.ends_at)).getTime()+86400000);
  setSince(localTime(from));setUntil(localTime(to));setStart('');setEnd('');
  const refreshed=SlotsSchema.parse(await crmApi('visit-slots?'+new URLSearchParams({since:from.toISOString(),until:to.toISOString()})));
  setSlots(refreshed.items);setSlot(refreshed.items.some(row=>row.id===result.id)?result.id:'');setError('');
 }}catch(err){setError(message(err));}}
 return <><div className="crm-heading"><div><Link href="/appointments">← Appointments</Link><h1>{old?'Reschedule visit':'Schedule a site visit'}</h1><p>Owner operation · explicit consent · fresh availability check</p></div></div>{error&&<p className="notice notice-error" role="alert">{error}</p>}{loading?<p role="status">Loading appointment workspace…</p>:review?<Review request={review} onDone={()=>router.push('/appointments')}/>:<>
 <form className="panel crm-section crm-form" onSubmit={e=>void prepare(e)}><h2>Select customer, property and slot</h2>{old&&<p>Original visit: {date(old.starts_at)} · {text(old.property_title)}. It remains confirmed until replacement succeeds.</p>}
 <fieldset disabled={action.busy||action.pending}><div className="crm-columns"><div><label>Find lead<input value={leadSearch} disabled={!!old} onChange={e=>setLeadSearch(e.target.value)} placeholder="Name or email"/></label><button type="button" className="button button-light" disabled={!!old} onClick={()=>void search('leads')}>Search leads</button><label>Customer<select required value={lead} disabled={!!old} onChange={e=>setLead(e.target.value)}><option value="">Select lead</option>{leads.map(row=><option key={row.id} value={row.id}>{text(row.name)} · {text(row.email)}</option>)}</select></label></div><div><label>Find property<input value={propertySearch} onChange={e=>setPropertySearch(e.target.value)} placeholder="Title or locality"/></label><button type="button" className="button button-light" onClick={()=>void search('properties')}>Search properties</button><label>Property<select required value={property} onChange={e=>setProperty(e.target.value)}><option value="">Select property</option>{properties.map(row=><option key={row.id} value={row.id}>{text(row.title)} · {money(row.price)}</option>)}</select></label></div></div>
 <div className="crm-columns"><label>Availability from (local time)<input required type="datetime-local" value={since} onChange={e=>{setSince(e.target.value);setSlot('');setSlots([]);}}/></label><label>Availability until (local time)<input required type="datetime-local" value={until} onChange={e=>{setUntil(e.target.value);setSlot('');setSlots([]);}}/></label></div><button type="button" className="button button-light" disabled={availabilityLoading} onClick={()=>void loadAvailability()}>{availabilityLoading?'Checking availability…':'Load available slots'}</button><label>Visit slot<select required value={slot} onChange={e=>setSlot(e.target.value)}><option value="">Select an available slot</option>{slots.map(row=><option key={row.id} value={row.id}>{date(row.starts_at)} – {date(row.ends_at)}</option>)}</select></label><p className="subtle">Availability is provisional. Choose a window no longer than 31 days. Search results are limited to 100; narrow the search if needed.</p></fieldset>
 {action.error&&<p className="notice notice-error" role="alert">{action.error}</p>}<button className="button button-primary" disabled={action.busy}>{action.busy?'Preparing review…':action.pending?'Retry saved request':'Prepare owner review'}</button></form>
 <form className="panel crm-section crm-form" onSubmit={e=>void createSlot(e)}><h2>Create a future visit slot</h2><p>Slots are shared across properties for one owner. Overlaps are rejected by PostgreSQL.</p><fieldset disabled={slotAction.busy||slotAction.pending}><label>Slot start (local time)<input type="datetime-local" required value={start} onChange={e=>setStart(e.target.value)}/></label><label>Slot end (local time)<input type="datetime-local" required value={end} onChange={e=>setEnd(e.target.value)}/></label></fieldset>{slotAction.error&&<p role="alert" className="notice notice-error">{slotAction.error}</p>}<button className="button button-light" disabled={slotAction.busy}>{slotAction.busy?'Creating slot…':slotAction.pending?'Retry saved slot':'Create slot'}</button></form></>}</>;
}

export default function CrmAppointments(){return <CrmShell><Scheduler/></CrmShell>;}
