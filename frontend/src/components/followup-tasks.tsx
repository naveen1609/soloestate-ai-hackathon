'use client';
import {useEffect,useState} from 'react';
import {crmApi,TaskDetailSchema,ListSchema,Row,message,useMutation} from '../lib/crm';

const NEXT:Record<string,string[]>={open:['in_progress','cancelled'],in_progress:['open','in_review','cancelled'],in_review:['in_progress','done','cancelled'],done:[],cancelled:['open']};

export default function FollowupTasks({leadId}:{leadId:string}){
 const [items,setItems]=useState<Row[]|null>(null);const [selected,setSelected]=useState('');const [error,setError]=useState('');
 const [title,setTitle]=useState('');const [due,setDue]=useState('');const [propertyId,setPropertyId]=useState('');const [properties,setProperties]=useState<Row[]>([]);
 const action=useMutation('followup-task:create:'+leadId);
 const [reload,setReload]=useState(0);
 useEffect(()=>{let active=true;void crmApi('followup-tasks?lead_id='+leadId+'&page_size=20').then(value=>{if(active){setItems(ListSchema.parse(value).items);setError('');}}).catch(err=>{if(active)setError(message(err));});return()=>{active=false;};},[leadId,reload]);
 useEffect(()=>{let active=true;void crmApi('properties?page_size=100').then(value=>{if(active)setProperties(ListSchema.parse(value).items);}).catch(()=>{if(active)setProperties([]);});return()=>{active=false;};},[]);
 async function open(){const record=await action.execute('followup-tasks',{lead_id:leadId,property_id:propertyId||null,title,due_at:due?due+'T00:00:00Z':null});
  if(record){setTitle('');setDue('');setPropertyId('');setReload(n=>n+1);setSelected(record.id);}}
 return <section className="panel crm-section"><h2>Follow-up tasks</h2>
 <p className="subtle">Internal owner work only. Completing a task never delivers anything; due dates are reminders as data.</p>
 {error&&<p role="alert" className="notice notice-error">{error}</p>}
 <div className="crm-filters"><label>Task title<input aria-label="Task title" value={title} maxLength={200} onChange={e=>setTitle(e.target.value)} disabled={action.busy||action.pending}/></label>
 <label>Due date (UTC)<input aria-label="Due date (UTC)" type="date" value={due} onChange={e=>setDue(e.target.value)} disabled={action.busy||action.pending}/></label>
 <label>Task property context<select aria-label="Task property context" value={propertyId} onChange={e=>setPropertyId(e.target.value)} disabled={action.busy||action.pending}><option value="">No property</option>{properties.map(row=><option key={row.id} value={row.id}>{String(row.title)}</option>)}</select></label>
 <button className="button button-light" disabled={action.busy||!title.trim()} onClick={()=>void open()}>{action.busy?'Creating…':action.pending?'Retry saved task':'Create task'}</button></div>
 {action.error&&<p role="alert" className="notice notice-error">{action.error}</p>}
 {items&&<div className="crm-actions">{items.map(row=><button key={row.id} className="button button-light" disabled={selected===row.id} onClick={()=>setSelected(row.id)}>{String(row.status)} · {row.overdue?'overdue':row.due_at?'due '+String(row.due_at).slice(0,10):'no due date'} · {String(row.title).slice(0,24)}</button>)}{!items.length&&<p className="subtle">No tasks yet.</p>}</div>}
 {selected&&<TaskDetail key={selected} id={selected} onChanged={()=>setReload(n=>n+1)}/>}</section>;
}

function TaskDetail({id,onChanged}:{id:string;onChanged:()=>void}){
 const [data,setData]=useState<ReturnType<typeof TaskDetailSchema.parse>|null>(null);const [error,setError]=useState('');
 const [reload,setReload]=useState(0);
 useEffect(()=>{let active=true;void crmApi('followup-tasks/'+id).then(value=>{if(active){setData(TaskDetailSchema.parse(value));setError('');}}).catch(err=>{if(active)setError(message(err));});return()=>{active=false;};},[id,reload]);
 if(!data)return <>{error&&<p role="alert" className="notice notice-error">{error}</p>}<p role="status">Loading task…</p></>;
 return <TaskEdit key={data.record.version} id={id} record={data.record} history={data.history} onChanged={()=>{setReload(n=>n+1);onChanged();}}/>;
}

function taskSnapshot(state:unknown){const task=(state as {task?:unknown}|null|undefined)?.task;return task&&typeof task==='object'?task as Record<string,unknown>:null;}
function fmtSnapshot(value:unknown){return value===null||value===undefined?'none':String(value).slice(0,80);}
function changeSummary(row:Row){
 const before=taskSnapshot(row.before_state),after=taskSnapshot(row.after_state);
 if(!after)return '';
 if(!before)return 'Opened as '+String(after.status??'open');
 const parts:string[]=[];
 for(const field of ['title','notes','due_at','assignee','status','outcome']){const x=before[field]??null,y=after[field]??null;
  if(String(x)!==String(y))parts.push(field.replace('_',' ')+': '+fmtSnapshot(x)+' → '+fmtSnapshot(y));}
 return parts.join('; ');}

function TaskEdit({id,record,history,onChanged}:{id:string;record:Row;history:Row[];onChanged:()=>void}){
 const [title,setTitle]=useState(String(record.title));const [notes,setNotes]=useState(String(record.notes??''));
 const [due,setDue]=useState(record.due_at?String(record.due_at).slice(0,10):'');const [assignee,setAssignee]=useState(String(record.assignee));
 const [outcome,setOutcome]=useState('');const [reason,setReason]=useState('');
 const action=useMutation('followup-task:'+id+':update');
 async function save(payload:Record<string,unknown>){
  const updated=await action.execute('followup-tasks/'+id+'/update',{expected_version:record.version,reason,...payload});
  if(updated){setOutcome('');setReason('');onChanged();}}
 async function saveEdits(){const recordDate=record.due_at?String(record.due_at).slice(0,10):'';
  await save({...(title!==record.title?{title}:{}),...(notes!==(record.notes??'')?{notes}:{}),
   ...(due!==recordDate?{due_at:due?due+'T00:00:00Z':null}:{}),...(assignee!==record.assignee?{assignee}:{})});}
 const current=String(record.status);
 return <div style={{overflowWrap:'anywhere'}}>
 <p role="status">Status: {current}{record.overdue?' · overdue':''}{record.outcome?' · Outcome: '+String(record.outcome):''}</p>
 {action.error&&<p role="alert" className="notice notice-error">{action.error}</p>}
 <div className="crm-filters"><label>Task title<input aria-label="Edit task title" value={title} maxLength={200} onChange={e=>setTitle(e.target.value)} disabled={action.busy}/></label>
 <label>Task notes<textarea aria-label="Edit task notes" value={notes} maxLength={5000} onChange={e=>setNotes(e.target.value)} disabled={action.busy}/></label>
 <label>Due date (UTC)<input aria-label="Edit due date (UTC)" type="date" value={due} onChange={e=>setDue(e.target.value)} disabled={action.busy}/></label>
 <label>Assignee<input aria-label="Edit assignee" value={assignee} maxLength={120} onChange={e=>setAssignee(e.target.value)} disabled={action.busy}/></label>
 <button className="button button-light" disabled={action.busy||!reason.trim()} onClick={()=>void saveEdits()}>Save changes</button></div>
 <div className="crm-filters"><label>Completion outcome<input aria-label="Completion outcome" value={outcome} maxLength={2000} onChange={e=>setOutcome(e.target.value)} disabled={action.busy}/></label>
 <label>Change reason<input aria-label="Change reason" value={reason} maxLength={1000} onChange={e=>setReason(e.target.value)} disabled={action.busy}/></label>
 {(NEXT[current]??[]).map(next=><button key={next} className="button button-light" disabled={action.busy||!reason.trim()||(next==='done'&&!outcome.trim())} onClick={()=>void save({status:next,...(next==='done'?{outcome}:{})})}>Move to {next.replaceAll('_',' ')}</button>)}</div>
 {history.map(row=>{const summary=changeSummary(row);return <div className="crm-row" key={row.id}><p>{String(row.reason)}</p>{summary&&<p className="subtle">{summary}</p>}<p className="subtle">{String(row.created_at)}</p></div>;})}</div>;
}
