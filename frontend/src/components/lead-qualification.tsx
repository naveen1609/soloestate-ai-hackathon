'use client';
import Link from 'next/link';
import {FormEvent,useState} from 'react';
import {crmApi,MatchesSchema,Qualification,Row,message,useMutation} from '../lib/crm';
import {date,money} from '../lib/types';

export default function LeadQualification({record,qualification,onSaved}:{record:Row;qualification?:Qualification;onSaved:(record:Row)=>Promise<void>}){
 const action=useMutation('qualify:'+record.id);
 const [reason,setReason]=useState('');const [refreshing,setRefreshing]=useState(false);
 const [matches,setMatches]=useState<ReturnType<typeof MatchesSchema.parse>|null>(null);const [loading,setLoading]=useState(false);const [error,setError]=useState('');
 const current=matches?.lead_id===record.id&&matches.lead_version===record.version?matches:null;
 async function confirm(event:FormEvent){event.preventDefault();setRefreshing(true);try{const row=await action.execute(`leads/${record.id}/qualify`,{expected_version:record.version,reason});if(row){await onSaved(row);setReason('');}}finally{setRefreshing(false);}}
 async function check(){setLoading(true);setError('');try{const result=MatchesSchema.parse(await crmApi(`leads/${record.id}/matches?limit=20`));if(result.lead_id!==record.id)throw new Error('Match response belongs to a different lead.');setMatches(result);}catch(err){setMatches(null);setError(message(err));}finally{setLoading(false);}}
 const blocked=action.busy||refreshing||Boolean(record.archived_at);
 return <section className="panel crm-section" style={{overflowWrap:'anywhere'}}><h2>Qualification and property matching</h2>
 <p>Qualification reviews business requirements. Contact permission, human handoff, lead status and booking approval remain separate.</p>
 {!qualification?<p role="status">Qualification status unavailable. Reload the lead to refresh it.</p>:<>
 <p role="status">{qualification.qualification_current?'Qualification is current.':qualification.needs_review?'Qualification needs owner review.':qualification.qualification_ready?'Requirements complete; owner confirmation required.':'Qualification is incomplete.'}</p>
 {qualification.missing_fields.length>0&&<p>Missing answers: {qualification.missing_fields.map(s=>s.replaceAll('_',' ')).join(', ')}.</p>}
 {qualification.invalid_fields.length>0&&<p>Review these answers: {qualification.invalid_fields.map(s=>s.replaceAll('_',' ')).join(', ')}. A positive budget ceiling is required.</p>}
 </>}
 <p>Use Edit record to enter or clear requirements. Move the journey to qualifying before the first confirmation.</p>
 <form className="crm-form" onSubmit={e=>void confirm(e)}><label>Qualification review reason<input required maxLength={1000} value={reason} disabled={blocked||action.pending} onChange={e=>setReason(e.target.value)}/></label>
 <button className="button button-primary" disabled={blocked||(!action.pending&&(!qualification?.qualification_ready||!['qualifying','qualified'].includes(String(record.journey_stage))))}>{action.pending?'Retry saved qualification':refreshing?'Confirming qualification…':'Confirm qualification'}</button>
 {action.error&&<p role="alert">{action.error}</p>}</form>
 <h3>Live property matches</h3><p>Active inventory only, within the stated budget and locality, with at least the requested bedrooms. Exact bedrooms rank first, then price and ID. Scores are ranking rules, not AI confidence. Timeframe does not filter inventory because possession dates are not recorded.</p>
 <button className="button button-light" disabled={loading||Boolean(record.archived_at)} onClick={()=>void check()}>{loading?'Checking properties…':'Check property matches'}</button>
 {error&&<p role="alert">{error}</p>}
 {matches&&!current&&<p>Requirements changed. Check property matches again.</p>}
 {current&&<><p className="subtle">Checked {date(current.checked_at)} · lead version {current.lead_version}. Matching is read-only; booking needs approval and a fresh availability check.</p>
 {!current.matching_ready?<p>Matching is incomplete. Review: {[...current.missing_fields,...current.invalid_fields].map(s=>s.replaceAll('_',' ')).join(', ')}.</p>:current.matches.length===0?<p>No eligible properties meet the stated constraints.</p>:current.matches.map(row=><article className="crm-row" key={row.id}><h4><Link href={'/properties/'+row.id}>{row.title}</Link></h4><p>{money(row.price)} · {row.bedrooms} bedrooms · ranking score {row.score}</p><ul>{row.checks.map(check=><li key={check.criterion}>{check.criterion.replaceAll('_',' ')}: requested {JSON.stringify(check.requested)}; property {JSON.stringify(check.actual)} — meets constraint</li>)}</ul></article>)}
 {current.truncated&&<p>Showing the first 20 matches; additional eligible properties exist.</p>}</>}
 </section>;
}
