'use client';
import {FormEvent,useCallback,useEffect,useRef,useState} from 'react';
import CrmShell from './crm-shell';
import {crmApi,ListSchema,Row,message,useMutation} from '../lib/crm';
import {DAY_NAMES,LINK_STATUSES,SingletonSchema,SuppressionListSchema,SuppressionResultSchema} from '../lib/policy';
import {text} from '../lib/types';

function useLoad<T>(load:()=>Promise<T>){
 const [data,setData]=useState<T|null>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(true);
 const gen=useRef(0);
 const reload=useCallback(async()=>{const g=++gen.current;setLoading(true);try{const v=await load();if(g===gen.current){setData(v);setError('');}}catch(err){if(g===gen.current)setError(message(err));}finally{setLoading(false);}},[load]);
 useEffect(()=>{const g=++gen.current;let active=true;void load().then(v=>{if(active&&g===gen.current){setData(v);setError('');}}).catch(err=>{if(active&&g===gen.current)setError(message(err));}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[load]);
 return {data,error,loading,reload,setError};
}

type PolicyRecord={id:string;[k:string]:unknown};
function PolicyFields({data,reload}:{data:PolicyRecord;reload:()=>Promise<void>}){
 const action=useMutation('policy:update',SingletonSchema);
 const [v,setV]=useState<Record<string,string>>({timezone:text(data.timezone),weekly_holiday:String(data.weekly_holiday??2),hours_start:text(data.hours_start).slice(0,5),hours_end:text(data.hours_end).slice(0,5),holiday_dates:(Array.isArray(data.holiday_dates)?data.holiday_dates:[]).join(', '),notice_hours:String(data.notice_hours??16),default_duration_min:String(data.default_duration_min??30),min_duration_min:String(data.min_duration_min??15),max_duration_min:String(data.max_duration_min??120),buffer_minutes:String(data.buffer_minutes??0),reason:''});
 const [days,setDays]=useState<number[]>(Array.isArray(data.working_days)?data.working_days.map(Number):[]);
 function field(key:string){return {value:v[key]??'',onChange:(e:React.ChangeEvent<HTMLInputElement|HTMLSelectElement>)=>setV(s=>({...s,[key]:e.target.value}))};}
 async function submit(e:FormEvent){e.preventDefault();const record=await action.execute('business-policy',{expected_version:data.version,reason:v.reason,timezone:v.timezone,working_days:days,weekly_holiday:Number(v.weekly_holiday),hours_start:v.hours_start,hours_end:v.hours_end,holiday_dates:v.holiday_dates.split(',').map(s=>s.trim()).filter(Boolean),notice_hours:Number(v.notice_hours),default_duration_min:Number(v.default_duration_min),min_duration_min:Number(v.min_duration_min),max_duration_min:Number(v.max_duration_min),buffer_minutes:Number(v.buffer_minutes)},'PATCH');if(record){setV(s=>({...s,reason:''}));await reload();}}
 return <form className="panel crm-section crm-form" onSubmit={e=>void submit(e)}><h2>Business policy</h2><p className="subtle">Policy v{text(data.version)} · notice {text(data.notice_hours)}h · default visit {text(data.default_duration_min)} min · buffer {text(data.buffer_minutes)} min</p>
 <fieldset disabled={action.busy||action.pending}><div className="crm-columns"><label>Timezone<input required maxLength={64} {...field('timezone')}/></label><label>Weekly holiday<select {...field('weekly_holiday')}>{DAY_NAMES.map((d,i)=><option key={i} value={i}>{d}</option>)}</select></label></div>
 <fieldset><legend>Working days</legend><div className="crm-filters">{DAY_NAMES.map((d,i)=><label key={i} className="crm-check"><input type="checkbox" checked={days.includes(i)} onChange={e=>setDays(s=>e.target.checked?[...s,i]:s.filter(x=>x!==i))}/>{d}</label>)}</div></fieldset>
 <div className="crm-columns"><label>Opens at<input required type="time" {...field('hours_start')}/></label><label>Closes at<input required type="time" {...field('hours_end')}/></label></div>
 <label>Holiday dates (YYYY-MM-DD, comma separated)<input {...field('holiday_dates')} placeholder="2026-12-25"/></label>
 <div className="crm-columns"><label>Advance notice (hours)<input required type="number" min="1" {...field('notice_hours')}/></label><label>Default visit (min)<input required type="number" min="1" {...field('default_duration_min')}/></label></div>
 <div className="crm-columns"><label>Shortest visit (min)<input required type="number" min="1" {...field('min_duration_min')}/></label><label>Longest visit (min)<input required type="number" min="1" {...field('max_duration_min')}/></label></div>
 <label>Buffer between visits (min)<input required type="number" min="0" {...field('buffer_minutes')}/></label>
 <label>Change reason<input required maxLength={1000} {...field('reason')}/></label></fieldset>
 {action.error&&<p role="alert" className="notice notice-error">{action.error}</p>}<button className="button button-primary" disabled={action.busy}>{action.busy?'Saving…':action.pending?'Retry saved policy':'Save policy'}</button></form>;
}

function PolicyForm(){
 const load=useCallback(async()=>SingletonSchema.parse(await crmApi('business-policy')).record,[]);
 const {data,error,loading,reload}=useLoad(load);
 if(loading)return <p role="status">Loading business policy…</p>;
 if(error||!data)return <p role="alert" className="notice notice-error">{error||'Policy unavailable.'}</p>;
 return <PolicyFields key={String(data.version)} data={data} reload={reload}/>;
}

function Resources(){
 const load=useCallback(async()=>ListSchema.parse(await crmApi('resources')).items,[]);
 const {data,error,loading,reload}=useLoad(load);
 const create=useMutation('resource:create');const [name,setName]=useState('');
 const [reason,setReason]=useState('');const [target,setTarget]=useState<Row|null>(null);
 const deactivate=useMutation('resource:deactivate:'+(target?.id??'none'));
 async function submit(e:FormEvent){e.preventDefault();const row=await create.execute('resources',{name});if(row){setName('');await reload();}}
 async function submitDeactivate(e:FormEvent){e.preventDefault();if(!target)return;const row=await deactivate.execute('resources/'+target.id+'/deactivate',{expected_version:target.version,reason});if(row){setTarget(null);setReason('');await reload();}}
 return <section className="panel crm-section"><h2>Visit resources</h2><p className="subtle">Overlapping visits are allowed on different resources, never on the same one. Deactivation requires zero future bookings.</p>
 {error&&<p role="alert" className="notice notice-error">{error}</p>}{loading?<p role="status">Loading resources…</p>:<ul className="crm-list">{(data??[]).map(r=><li key={r.id}>{text(r.name)} · {text(r.kind)} · {r.active===true?'active':'inactive'}{r.active===true&&<button className="button button-light" onClick={()=>setTarget(r)}>Deactivate</button>}</li>)}</ul>}
 <form className="crm-form" onSubmit={e=>void submit(e)}><div className="crm-filters"><label>New resource name<input required maxLength={200} value={name} disabled={create.busy||create.pending} onChange={e=>setName(e.target.value)}/></label><button className="button button-primary" disabled={create.busy}>{create.busy?'Creating…':create.pending?'Retry saved resource':'Create resource'}</button></div>{create.error&&<p role="alert" className="notice notice-error">{create.error}</p>}</form>
 {target!==null&&<form className="crm-form" onSubmit={e=>void submitDeactivate(e)}><div className="crm-filters"><label>Deactivation reason for {text(target.name)}<input required maxLength={1000} value={reason} disabled={deactivate.busy||deactivate.pending} onChange={e=>setReason(e.target.value)}/></label><button className="button button-danger" disabled={deactivate.busy}>{deactivate.busy?'Deactivating…':'Confirm deactivation'}</button><button type="button" className="button button-light" onClick={()=>setTarget(null)}>Cancel</button></div>{deactivate.error&&<p role="alert" className="notice notice-error">{deactivate.error}</p>}</form>}</section>;
}

function SlotGeneration(){
 const [resources,setResources]=useState<Row[]>([]);
 const [resource,setResource]=useState('');const [start,setStart]=useState('');const [end,setEnd]=useState('');const [duration,setDuration]=useState('');
 const [result,setResult]=useState('');const [error,setError]=useState('');
 const action=useMutation('slots:generate');
 useEffect(()=>{let active=true;void crmApi('resources').then(v=>{if(active)setResources(ListSchema.parse(v).items);}).catch(err=>{if(active)setError(message(err));});return()=>{active=false;};},[]);
 async function submit(e:FormEvent){e.preventDefault();setResult('');const row=await action.execute('visit-slots/generate',{...(resource?{resource_id:resource}:{}),start_date:start,end_date:end,...(duration?{duration_min:Number(duration)}:{})});if(row)setResult(`Created ${text(row.created)} slots, skipped ${text(row.skipped_overlapping)} overlapping.`);}
 return <section className="panel crm-section"><h2>Generate visit slots</h2><p className="subtle">Builds the availability grid from the business policy. Existing and overlapping windows are skipped, never duplicated.</p>
 <form className="crm-form" onSubmit={e=>void submit(e)}><fieldset disabled={action.busy||action.pending}><div className="crm-columns"><label>Resource<select value={resource} onChange={e=>setResource(e.target.value)}><option value="">Owner (default)</option>{resources.map(r=><option key={r.id} value={r.id}>{text(r.name)}</option>)}</select></label><label>Visit length (min, blank for policy default)<input type="number" min="1" value={duration} onChange={e=>setDuration(e.target.value)}/></label></div><div className="crm-columns"><label>First day<input required type="date" value={start} onChange={e=>setStart(e.target.value)}/></label><label>Last day<input required type="date" value={end} onChange={e=>setEnd(e.target.value)}/></label></div></fieldset>
 {(error||action.error)&&<p role="alert" className="notice notice-error">{error||action.error}</p>}{result&&<p role="status">{result}</p>}<button className="button button-primary" disabled={action.busy}>{action.busy?'Generating…':action.pending?'Retry saved generation':'Generate slots'}</button></form></section>;
}

function AutomationControls(){
 const load=useCallback(async()=>SingletonSchema.parse(await crmApi('automation-controls')).record,[]);
 const {data,error,loading,reload}=useLoad(load);
 const action=useMutation('automation:stop',SingletonSchema);const [reason,setReason]=useState('');
 async function submit(stopped:boolean){if(!data)return;const row=await action.execute('automation-controls/stop',{stopped,reason});if(row){setReason('');await reload();}}
 if(loading)return <p role="status">Loading automation controls…</p>;
 return <section className="panel crm-section"><h2>Emergency stop</h2>{(error||action.error)&&<p role="alert" className="notice notice-error">{error||action.error}</p>}
 {data&&<p>Autonomous commits are currently <strong>{data.emergency_stop?'STOPPED':'allowed'}</strong>. Direct owner booking always stays available.</p>}
 <div className="crm-filters"><label>Stop reason<input required maxLength={1000} value={reason} disabled={action.busy||action.pending} onChange={e=>setReason(e.target.value)}/></label>
 <button className="button button-danger" disabled={action.busy||!reason.trim()} onClick={()=>void submit(true)}>{action.busy?'Confirming…':'Stop autonomous commits'}</button>
 <button className="button button-light" disabled={action.busy||!reason.trim()} onClick={()=>void submit(false)}>Lift the stop</button></div></section>;
}

function AutomationPolicies(){
 const load=useCallback(async()=>ListSchema.parse(await crmApi('automation-policies')).items,[]);
 const {data,error,loading,reload}=useLoad(load);
 const create=useMutation('automation-policy:create');const toggle=useMutation('automation-policy:toggle');
 const [v,setV]=useState({kind:'booking_reminder',hours_before:'24',days_after:'',after_event:'',frequency:'once',categories:'reminder',lead_statuses:'new',max_attempts:'1',cooldown:'24',wording:'template'});
 function field(key:string){return {value:(v as Record<string,string>)[key]??'',onChange:(e:React.ChangeEvent<HTMLInputElement|HTMLSelectElement>)=>setV(s=>({...s,[key]:e.target.value}))};}
 async function submit(e:FormEvent){e.preventDefault();const row=await create.execute('automation-policies',{kind:v.kind,...(v.hours_before?{hours_before:Number(v.hours_before)}:{}),...(v.days_after?{days_after:Number(v.days_after)}:{}),...(v.after_event.trim()?{after_event:v.after_event.trim()}:{}),frequency:v.frequency,categories:v.categories.split(',').map(s=>s.trim()).filter(Boolean),lead_statuses:v.lead_statuses.split(',').map(s=>s.trim()).filter(Boolean),max_attempts:Number(v.max_attempts),per_lead_cooldown_hours:Number(v.cooldown),wording:v.wording});if(row)await reload();}
 async function flip(row:Row){const updated=await toggle.execute('automation-policies/'+row.id,{expected_version:row.version,enabled:!row.enabled},'PATCH');if(updated)await reload();}
 return <section className="panel crm-section"><h2>Follow-up and reminder policies</h2><p className="subtle">Configuration only. Scheduled execution is not implemented in this increment.</p>
 {error&&<p role="alert" className="notice notice-error">{error}</p>}{loading?<p role="status">Loading policies…</p>:<ul className="crm-list">{(data??[]).map(r=><li key={r.id}>{text(r.kind)} · {text(r.frequency)} · {r.enabled?'enabled':'disabled'} <button className="button button-light" disabled={toggle.busy} onClick={()=>void flip(r)}>{r.enabled?'Disable':'Enable'}</button></li>)}</ul>}
 {toggle.error&&<p role="alert" className="notice notice-error">{toggle.error}</p>}
 <form className="crm-form" onSubmit={e=>void submit(e)}><fieldset disabled={create.busy||create.pending}><div className="crm-columns"><label>Policy kind<select {...field('kind')}><option value="booking_reminder">Booking reminder</option><option value="followup_nudge">Follow-up nudge</option></select></label><label>Frequency<select {...field('frequency')}><option value="once">Once</option><option value="daily">Daily</option><option value="weekly">Weekly</option></select></label></div>
 <div className="crm-columns"><label>Hours before visit<input type="number" min="1" {...field('hours_before')}/></label><label>Days after event<input type="number" min="0" {...field('days_after')}/></label></div>
 <label>After event (for nudges)<input maxLength={120} {...field('after_event')} placeholder="no_visit_booked"/></label>
 <div className="crm-columns"><label>Message categories (comma separated)<input {...field('categories')}/></label><label>Lead statuses (comma separated)<input {...field('lead_statuses')}/></label></div>
 <div className="crm-columns"><label>Max attempts<input required type="number" min="1" {...field('max_attempts')}/></label><label>Cooldown per lead (hours)<input required type="number" min="0" {...field('cooldown')}/></label></div>
 <label>Wording<select {...field('wording')}><option value="template">Fixed template</option><option value="assisted">Owner-assisted draft</option></select></label></fieldset>
 {create.error&&<p role="alert" className="notice notice-error">{create.error}</p>}<button className="button button-primary" disabled={create.busy}>{create.busy?'Creating…':create.pending?'Retry saved policy':'Create policy'}</button></form></section>;
}

function SuppressionList(){
 const load=useCallback(async()=>SuppressionListSchema.parse(await crmApi('suppression-list')).items,[]);
 const {data,error,loading,reload}=useLoad(load);
 const action=useMutation('suppression:create',SuppressionResultSchema);
 const [channel,setChannel]=useState('whatsapp');const [destination,setDestination]=useState('');const [evidence,setEvidence]=useState('');
 async function submit(e:FormEvent){e.preventDefault();const row=await action.execute('suppression-list',{channel,destination,...(evidence.trim()?{evidence_ref:evidence.trim()}:{})});if(row){setDestination('');setEvidence('');await reload();}}
 return <section className="panel crm-section"><h2>Suppression list</h2><p className="subtle">Blocked destinations are never messaged by automation. Entries are permanent in this increment.</p>
 {error&&<p role="alert" className="notice notice-error">{error}</p>}{loading?<p role="status">Loading suppressions…</p>:<ul className="crm-list">{(data??[]).map((r,i)=><li key={i}>{text(r.channel)} · {text(r.destination)} · {text(r.source)}</li>)}</ul>}
 <form className="crm-form" onSubmit={e=>void submit(e)}><div className="crm-filters"><label>Channel<select value={channel} disabled={action.busy||action.pending} onChange={e=>setChannel(e.target.value)}><option value="whatsapp">WhatsApp</option><option value="website">Website</option></select></label><label>Destination<input required maxLength={254} value={destination} disabled={action.busy||action.pending} onChange={e=>setDestination(e.target.value)} placeholder="+15550001111 or email"/></label><label>Evidence note<input maxLength={1000} value={evidence} disabled={action.busy||action.pending} onChange={e=>setEvidence(e.target.value)}/></label><button className="button button-primary" disabled={action.busy}>{action.busy?'Adding…':action.pending?'Retry saved entry':'Add suppression'}</button></div>{action.error&&<p role="alert" className="notice notice-error">{action.error}</p>}</form></section>;
}

function IdentityLinks(){
 const [status,setStatus]=useState('');
 const load=useCallback(async()=>ListSchema.parse(await crmApi('identity-links?page_size=100'+(status?'&status='+status:''))).items,[status]);
 const {data,error,loading,reload}=useLoad(load);
 const link=useMutation('identity:link');
 const [v,setV]=useState({link_id:'',lead_id:'',verified_destination:'',reason:''});
 const [revokeReason,setRevokeReason]=useState('');
 const revoke=useMutation('identity:revoke');
 function field(key:string){return {value:(v as Record<string,string>)[key]??'',onChange:(e:React.ChangeEvent<HTMLInputElement>)=>setV(s=>({...s,[key]:e.target.value}))};}
 async function submit(e:FormEvent){e.preventDefault();const row=await link.execute('identity-links/link',{link_id:v.link_id,lead_id:v.lead_id,verified_destination:v.verified_destination,reason:v.reason});if(row){setV({link_id:'',lead_id:'',verified_destination:'',reason:''});await reload();}}
 async function revokeLink(row:Row){const updated=await revoke.execute('identity-links/'+row.id+'/revoke',{expected_version:row.version,reason:revokeReason||'Owner revoked the link.'});if(updated){setRevokeReason('');await reload();}}
 return <section className="panel crm-section"><h2>Channel identities</h2><p className="subtle">New channel identities stay isolated. Only an explicit owner link attaches one to a CRM lead, after out-of-band verification.</p>
 <label>Status filter<select value={status} onChange={e=>setStatus(e.target.value)}><option value="">All statuses</option>{LINK_STATUSES.map(s=><option key={s} value={s}>{s}</option>)}</select></label>
 {error&&<p role="alert" className="notice notice-error">{error}</p>}{loading?<p role="status">Loading identities…</p>:<ul className="crm-list">{(data??[]).map(r=><li key={r.id}>{text(r.channel)} · {text(r.provider_ref)} · {text(r.status)}{r.status==='active'&&<button className="button button-light" disabled={revoke.busy} onClick={()=>void revokeLink(r)}>Revoke</button>}</li>)}</ul>}
 {revoke.error&&<p role="alert" className="notice notice-error">{revoke.error}</p>}
 <form className="crm-form" onSubmit={e=>void submit(e)}><fieldset disabled={link.busy||link.pending}><div className="crm-columns"><label>Observed link id<input required {...field('link_id')}/></label><label>Canonical lead id<input required {...field('lead_id')}/></label></div><label>Verified destination<input required maxLength={254} {...field('verified_destination')} placeholder="+15550001111 or confirmed email"/></label><label>Verification note<input required maxLength={1000} {...field('reason')} placeholder="How the owner verified this identity"/></label></fieldset>
 {link.error&&<p role="alert" className="notice notice-error">{link.error}</p>}<button className="button button-primary" disabled={link.busy}>{link.busy?'Linking…':link.pending?'Retry saved link':'Link identity to lead'}</button></form></section>;
}

export default function Policy(){
 return <CrmShell><div className="crm-heading"><div><p className="eyebrow">OWNER BUSINESS RULES</p><h1>Policy and scheduling</h1><p>Working hours, visit resources, automation limits and channel identities.</p></div></div>
 <PolicyForm/><Resources/><SlotGeneration/><AutomationControls/><AutomationPolicies/><SuppressionList/><IdentityLinks/></CrmShell>;
}
