'use client';
import Link from 'next/link';
import {useEffect,useState} from 'react';
import {z} from 'zod';
import CrmShell from './crm-shell';
import {crmApi,SummarySchema,message} from '../lib/crm';
import {date,text} from '../lib/types';

function Overview(){
 const [data,setData]=useState<z.infer<typeof SummarySchema>|null>(null);const [error,setError]=useState('');
 useEffect(()=>{let active=true;async function load(){try{const value=SummarySchema.parse(await crmApi('summary'));if(active){setData(value);setError('');}}catch(err){if(active)setError(message(err));}}void load();const timer=setInterval(()=>void load(),15000);return()=>{active=false;clearInterval(timer);};},[]);
 return <><div className="crm-heading"><div><p className="eyebrow">LIVE BUSINESS OVERVIEW</p><h1>Your real estate workspace</h1><p>Current PostgreSQL records · INR · Asia/Kolkata</p></div><Link className="button button-primary" href="/appointments/new">Schedule visit</Link></div>{error&&<p role="alert" className="notice notice-error">{error}</p>}{!data?<p role="status">Loading business records…</p>:<>
 <div className="crm-metrics">{[['Active leads',data.metrics.leads,'/leads'],['New leads',data.metrics.new_leads,'/leads?status=new'],['Active properties',data.metrics.properties,'/properties?active=true'],['Upcoming visits',data.metrics.upcoming_visits,'/appointments?period=upcoming'],['Pending approvals',data.metrics.pending_approvals,'/appointments']].map(([label,value,href])=><Link href={String(href)} className="panel" key={label}><span>{label}</span><strong>{value}</strong></Link>)}</div>
 <div className="crm-columns"><section className="panel crm-section"><h2>Recent leads</h2>{data.recent_leads.length?data.recent_leads.map(row=><Link className="crm-row" href={'/leads/'+row.id} key={row.id}><strong>{text(row.name)}</strong><span>{text(row.status)} · {text(row.email)}</span></Link>):<p>No leads yet. <Link href="/leads/new">Create a lead</Link>.</p>}</section>
 <section className="panel crm-section"><h2>Upcoming appointments</h2>{data.appointments.length?data.appointments.map(row=><Link className="crm-row" href={'/appointments/'+row.id} key={row.id}><strong>{text(row.lead_name)} · {text(row.property_title)}</strong><span>{date(row.starts_at)}</span></Link>):<p>No confirmed upcoming visits.</p>}</section></div>
 <section className="panel crm-section"><h2>Owner appointment requests</h2><p>Review and explicitly confirm before a booking is committed.</p>{data.requests.length?data.requests.map(row=><Link className="crm-row" href={'/appointments/new?request='+row.id} key={row.id}><strong>Review request {row.id.slice(0,8)}</strong><span>{text(row.status)}</span></Link>):<p>No pending owner appointment requests.</p>}</section>
 <section className="panel crm-section"><h2>AI goal execution</h2><p>The Muse Spark plan, tool timeline, run history and agent approval review remain in the execution workspace.</p><Link className="button button-light" href="/agent">Open AI execution</Link></section></> }</>;
}
export default function CrmOverview(){return <CrmShell><Overview/></CrmShell>;}
